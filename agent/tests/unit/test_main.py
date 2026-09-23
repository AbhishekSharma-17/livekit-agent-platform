"""`run_session` end to end offline: dispatch, build, greeting, telemetry, failure path."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import tomllib
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fakes.fake_api import FakeApi, resolved_config
from fakes.fake_llm import FakeLLM
from fakes.fake_stt import FakeSTT
from fakes.fake_tts import FakeTTS
from livekit import rtc
from livekit.agents import AgentServer, AgentSession, inference, llm
from lkap_contracts.api_models import KbHit
from lkap_contracts.dispatch import DispatchMetadata

from lkap_agent.config_client import ConfigUnavailableError, SessionEndedError, SessionNotFoundError
from lkap_agent.main import (
    CONFIG_UNAVAILABLE_LINE,
    DEFAULT_AGENT_NAME,
    FALLBACK_TTS_MODEL,
    START_FAILED_LINE,
    WORKFLOW_FALLBACK_LLM_MODEL,
    Deps,
    NoopBackgroundRunner,
    NoopFrameBuffer,
    NoopUiChannel,
    _workflow_model,
    effective_agent_name,
    only_lkap_jobs,
    require_agent_name,
    run_session,
    server,
)
from lkap_agent.observability import SessionObserver, redact_arguments, transcript_from_history
from lkap_agent.packs.loader import NullPack, PackLoader
from lkap_agent.providers.factory import BuiltProviders, ProviderFactory
from lkap_agent.settings import Settings

SECRET = "sk-SECRET123"


@pytest.fixture(autouse=True)
def _inference_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-W2-6: Inference objects read their credentials from the process env.

    Without these, a realtime session's workflow-LLM fallback would fail to
    construct and the run would silently take the "could not build" path.
    """
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret")
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")


# ------------------------------------------------------------------- doubles


class FakeJob:
    """The `ctx.job` fields `run_session` reads (`room.name` comes from the job proto)."""

    def __init__(self, metadata: str, job_id: str = "job-1", room_name: str = "lkap-room-1") -> None:
        self.metadata = metadata
        self.id = job_id
        self.room = SimpleNamespace(name=room_name)


class FakeProc:
    """The `ctx.proc` fields `run_session` reads."""

    def __init__(self) -> None:
        self.userdata: dict[str, Any] = {}


class FakeJobContext:
    """A `JobContextLike` that records connect/shutdown instead of doing them."""

    def __init__(self, metadata: str, room: rtc.Room | None = None) -> None:
        self.job = FakeJob(metadata)
        self.proc = FakeProc()
        # A genuine, never-connected `rtc.Room`: `AgentSession.start(room=...)`
        # builds a real `RoomIO` around it, which a duck-typed double cannot
        # satisfy. Nothing here ever calls `room.connect()`, so no socket opens.
        self.room = room or rtc.Room()
        self.connected = 0
        self.shutdown_reasons: list[str] = []
        self.shutdown_callbacks: list[Callable[[str], Awaitable[None]]] = []

    async def connect(self) -> None:
        self.connected += 1

    def shutdown(self, reason: str = "user requested") -> None:
        self.shutdown_reasons.append(reason)

    def add_shutdown_callback(self, callback: Any) -> None:
        self.shutdown_callbacks.append(callback)

    async def fire_shutdown(self, reason: str = "user requested") -> None:
        """Drive the registered callbacks the way the worker would."""
        for callback in self.shutdown_callbacks:
            await callback(reason)


class _RecordingFactory(ProviderFactory):
    """Returns fakes instead of importing vendor SDKs."""

    def __init__(self, *, with_tts: bool = True, avatar: Any = None) -> None:
        self.llm = FakeLLM(["Understood."])
        self.stt = FakeSTT()
        self.tts = FakeTTS() if with_tts else None
        self.avatar = avatar

    def build_all(self, resolved: Any, *, optional: Any = None) -> BuiltProviders:
        is_realtime = resolved.config.pipeline.mode == "realtime"
        return BuiltProviders(
            realtime=self.llm if is_realtime else None,
            llm=None if is_realtime else self.llm,
            stt=None if is_realtime else self.stt,
            tts=self.tts,
            avatar=self.avatar,
        )


class _FakeAvatar:
    """Records the `start` / `wait_for_join` ordering the worker must follow."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.room: rtc.Room | None = None

    async def start(self, session: AgentSession[Any], room: rtc.Room) -> None:
        self.events.append("start")
        self.room = room

    async def wait_for_join(self) -> None:
        self.events.append("wait_for_join")


def _metadata(session_id: str = "sess-1") -> str:
    return DispatchMetadata(
        session_id=session_id, agent_id="agent-1", config_version=1, participant_identity="user-guest"
    ).model_dump_json()


def _settings() -> Settings:
    return Settings(
        LIVEKIT_URL="wss://example.livekit.cloud",  # type: ignore[call-arg]
        LIVEKIT_API_KEY="k",  # type: ignore[call-arg]
        LIVEKIT_API_SECRET="s",  # type: ignore[call-arg]
        service_token="svc",
        api_base_url="http://api.test",
    )


class RoomlessStarter:
    """Starts the session with no room, recording what the worker handed over.

    `RoomOptions(participant_identity=...)` makes `RoomIO.start()` read
    `room.local_participant`, and `rtc.Room` raises before it is connected — so
    an offline test starts the session in text mode and asserts on the session
    object rather than on the wire.
    """

    def __init__(self) -> None:
        self.session: AgentSession[Any] | None = None
        self.agent: Any = None
        self.room: Any = None
        self.room_options: Any = None

    async def __call__(self, *, session: AgentSession[Any], agent: Any, room: Any, room_options: Any) -> None:
        self.session, self.agent, self.room, self.room_options = session, agent, room, room_options
        await session.start(agent)

    def assistant_turns(self) -> list[str]:
        """Every assistant message in the started session's history."""
        assert self.session is not None
        return [
            i.text_content or ""
            for i in self.session.history.items
            if isinstance(i, llm.ChatMessage) and i.role == "assistant"
        ]


def _deps(api: FakeApi, *, factory: ProviderFactory | None = None, **overrides: Any) -> Deps:
    """A fully-injected `Deps`: no network, no plugin imports, no turn detector."""
    overrides.setdefault("pack_loader", PackLoader([]))
    overrides.setdefault("session_starter", RoomlessStarter())
    overrides.setdefault("ui_channel_factory", lambda **kw: NoopUiChannel(kw.get("session_id", "")))
    overrides.setdefault("frame_buffer_factory", lambda **kw: NoopFrameBuffer())
    overrides.setdefault("background_runner_factory", lambda **kw: NoopBackgroundRunner())
    overrides.setdefault("turn_detector_factory", lambda _mode: None)
    return Deps(
        settings=_settings(),
        config_client=cast(Any, api),
        provider_factory=factory or _RecordingFactory(),
        **overrides,
    )


# ------------------------------------------------------------------- happy path


async def test_run_session_starts_a_cascaded_session_and_reports_it_started() -> None:
    """The whole path runs: resolve, build, start, connect, `session_started`."""
    api = FakeApi(resolved_config(greeting="Hello there!"))
    ctx = FakeJobContext(_metadata())

    await run_session(ctx, _deps(api))

    assert api.resolve_calls == ["sess-1"]
    assert ctx.connected == 1
    await ctx.fire_shutdown("done")
    assert "session_started" in api.event_types()


async def test_run_session_speaks_the_greeting_via_say_when_a_tts_exists() -> None:
    """With a TTS, `greeting_mode="say"` speaks the exact line and skips the LLM."""
    factory = _RecordingFactory(with_tts=True)
    starter = RoomlessStarter()
    api = FakeApi(resolved_config(greeting="Hello there!", greeting_mode="say"))
    ctx = FakeJobContext(_metadata())

    await run_session(ctx, _deps(api, factory=factory, session_starter=starter))
    await asyncio.sleep(0.1)

    assert "Hello there!" in starter.assistant_turns()
    assert not factory.llm.calls, "say() must not consult the LLM"


async def test_run_session_greets_through_generate_reply_without_a_tts() -> None:
    """ARCHITECTURE §15.9: `say()` would raise, so the model is asked to speak it.

    Verified against livekit-agents 1.8.2 `AgentActivity.say`, which raises
    unless a TTS exists or the realtime model sets `capabilities.supports_say`
    — which neither MVP realtime plugin does.
    """
    factory = _RecordingFactory(with_tts=False)
    api = FakeApi(resolved_config(with_tts=False, greeting="Hello there!", greeting_mode="say"))
    ctx = FakeJobContext(_metadata())

    await run_session(ctx, _deps(api, factory=factory))

    assert factory.llm.calls, "generate_reply should have driven one LLM call"
    assert "Hello there!" in factory.llm.calls[0].prompt


async def test_run_session_connects_before_starting_an_avatar() -> None:
    """`bey`/`tavus` `AvatarSession.start()` reads `room.name`, so connect comes first.

    Ordering verified in livekit-plugins-bey 1.8.2: `start()` mints the avatar's
    token from `room.name` and the job's local participant identity, and
    `wait_for_join()` is a no-op when the room was not connected at `start()`.
    """
    avatar = _FakeAvatar()
    api = FakeApi(resolved_config(with_avatar=True))
    ctx = FakeJobContext(_metadata())

    await run_session(ctx, _deps(api, factory=_RecordingFactory(avatar=avatar)))

    assert avatar.events == ["start", "wait_for_join"]
    assert avatar.room is ctx.room
    assert ctx.connected == 1


async def test_run_session_registers_the_silent_reply_handler_synchronously() -> None:
    """The handler must be a plain `def`: livekit reads `has_tool_reply` right after emit."""
    api = FakeApi(resolved_config(mode="realtime", with_tts=False))
    ctx = FakeJobContext(_metadata())
    factory = _RecordingFactory(with_tts=False)

    await run_session(ctx, _deps(api, factory=factory))
    assert ctx.shutdown_reasons == [], "the realtime session must build (workflow LLM fallback)"

    # The registered callback is PlatformAgent.on_function_tools_executed.
    import inspect

    from lkap_agent.platform_agent import PlatformAgent

    assert not inspect.iscoroutinefunction(PlatformAgent.on_function_tools_executed)


# ------------------------------------------------------------------- failure path


@pytest.mark.parametrize(
    "error", [SessionNotFoundError("gone"), SessionEndedError("over")], ids=["404", "ended"]
)
async def test_run_session_speaks_a_fixed_line_and_shuts_down_when_resolve_fails(
    error: Exception,
) -> None:
    """A user must hear why nothing happens rather than sit in silence.

    No summary: the api never marked an unknown or ended session `active`.
    """
    api = FakeApi(resolve_error=error)
    ctx = FakeJobContext(_metadata())
    spoken: list[str] = []

    async def _speaker(job_ctx: Any, line: str) -> None:
        spoken.append(line)

    await run_session(ctx, _deps(api, fallback_speaker=_speaker))

    assert spoken == [CONFIG_UNAVAILABLE_LINE]
    assert ctx.connected == 1
    assert ctx.shutdown_reasons == ["configuration unavailable"]
    assert api.summaries == []


async def test_run_session_fails_cleanly_when_the_session_cannot_be_built() -> None:
    """A config that resolves but cannot start still posts a failed summary."""
    api = FakeApi(resolved_config())
    ctx = FakeJobContext(_metadata())
    spoken: list[str] = []

    class _BrokenFactory(ProviderFactory):
        def build_all(self, resolved: Any, *, optional: Any = None) -> BuiltProviders:
            return BuiltProviders()  # no llm: SessionBuilder must refuse

    async def _speaker(job_ctx: Any, line: str) -> None:
        spoken.append(line)

    await run_session(ctx, _deps(api, factory=_BrokenFactory(), fallback_speaker=_speaker))

    assert spoken == [CONFIG_UNAVAILABLE_LINE]
    assert ctx.shutdown_reasons == ["configuration invalid"]
    assert [s.status for s in api.summaries] == ["failed"]
    assert api.summaries[0].error is not None


async def test_run_session_rejects_a_job_dispatched_without_metadata() -> None:
    """Only `POST /v1/agents/{id}/connect` dispatches us; a bare job is a platform bug."""
    ctx = FakeJobContext("")

    with pytest.raises(ValueError, match="without metadata"):
        await run_session(ctx, _deps(FakeApi()))


# ---------------------------------------------------------------------- secrets


async def test_run_session_never_logs_a_resolved_credential(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """Decrypted vendor keys must not reach any log sink (definition of done #5).

    Both sinks are checked: `configure_logging()` is a process-startup call the
    worker makes and tests do not, so structlog writes to stdout here — an
    assertion on `caplog` alone would pass vacuously.
    """
    config = resolved_config(mode="realtime", with_tts=False)
    config.resolved["realtime"].kwargs["api_key"] = SECRET
    ctx = FakeJobContext(_metadata())

    with caplog.at_level(logging.DEBUG):
        await run_session(ctx, _deps(FakeApi(config), factory=_RecordingFactory(with_tts=False)))
        await ctx.fire_shutdown("done")

    captured = capsys.readouterr()
    assert SECRET not in caplog.text
    assert SECRET not in captured.out
    assert SECRET not in captured.err


def test_redact_arguments_masks_credential_shaped_keys() -> None:
    """Tool arguments are logged, so anything key-like is masked first."""
    redacted = redact_arguments(f'{{"query": "flood", "api_key": "{SECRET}", "Token": "{SECRET}"}}')

    assert redacted["query"] == "flood"
    assert redacted["api_key"] == "***"
    assert redacted["Token"] == "***"


def test_redact_arguments_does_not_echo_unparseable_input() -> None:
    """Malformed arguments could be anything, so only their length is reported."""
    assert redact_arguments(f"not json {SECRET}") == {"_raw_len": len(f"not json {SECRET}")}


# ---------------------------------------------------------------- observability


async def test_shutdown_posts_the_summary_with_the_transcript() -> None:
    """The console's session detail comes from this one `PUT`."""
    api = FakeApi(resolved_config())
    ctx = FakeJobContext(_metadata())

    await run_session(ctx, _deps(api))
    await ctx.fire_shutdown("client disconnected")

    assert len(api.summaries) == 1
    summary = api.summaries[0]
    assert summary.status == "ended"
    assert summary.final_ui_state is not None
    assert "session_ended" in api.event_types()


async def test_shutdown_is_idempotent() -> None:
    """A double shutdown (job end plus close) must not post two summaries."""
    api = FakeApi(resolved_config())
    ctx = FakeJobContext(_metadata())

    await run_session(ctx, _deps(api))
    await ctx.fire_shutdown("first")
    await ctx.fire_shutdown("second")

    assert len(api.summaries) == 1


async def test_observer_records_tool_lifecycle_events() -> None:
    """Tool timing comes from `tool_execution_updated` (ARCHITECTURE §15.8).

    `ToolCallStarted.function_call.call_id` and `ToolCallEnded.call_id` are the
    correlation key; both exist in livekit-agents 1.8.2.
    """
    from livekit.agents.voice.events import ToolCallEnded, ToolCallStarted, ToolExecutionUpdatedEvent

    api = FakeApi()
    observer = SessionObserver(session_id="sess-1", client=cast(Any, api))
    call = llm.FunctionCall(call_id="call-7", name="lookup_policy", arguments=f'{{"api_key": "{SECRET}"}}')

    observer._on_tool_execution(ToolExecutionUpdatedEvent(update=ToolCallStarted(function_call=call)))
    observer._on_tool_execution(
        ToolExecutionUpdatedEvent(
            update=ToolCallEnded(id="call-7", call_id="call-7", message="Policy active", status="done")
        )
    )
    await observer.flush()

    started = api.events_of("tool_call_started")[0]
    ended = api.events_of("tool_call_ended")[0]
    assert started.payload["call_id"] == "call-7"
    assert started.payload["args_redacted"]["api_key"] == "***"
    assert ended.payload["status"] == "done"
    assert ended.payload["duration_ms"] is not None
    assert ended.payload["tool"] == started.payload["tool"] != "", "the SDK's ToolCallEnded has no name"


async def test_observer_captures_usage_from_session_usage_updated() -> None:
    """`metrics.UsageCollector` is deprecated in 1.8.2; usage comes from the event."""
    from livekit.agents.metrics import AgentSessionUsage
    from livekit.agents.voice.events import SessionUsageUpdatedEvent

    api = FakeApi()
    observer = SessionObserver(session_id="sess-1", client=cast(Any, api))

    observer._on_usage(SessionUsageUpdatedEvent(usage=AgentSessionUsage(model_usage=[])))
    await observer.shutdown(reason="done")

    assert isinstance(api.summaries[0].usage, dict)
    assert api.events_of("metrics")[0].payload["kind"] == "session_usage"


def test_transcript_from_history_keeps_turns_and_drops_images() -> None:
    """Frames must never reach the database; `text_content` already excludes them."""
    history = llm.ChatContext.empty()
    history.add_message(role="system", content="You are helpful.")
    history.add_message(role="user", content=["Look at this."])
    history.add_message(role="assistant", content="I see a flooded basement.")
    user_message = [i for i in history.items if isinstance(i, llm.ChatMessage) and i.role == "user"][0]
    user_message.content.append(llm.ImageContent(image="data:image/jpeg;base64,AAAA"))

    turns = transcript_from_history(history)

    assert [t.role for t in turns] == ["user", "assistant"]
    assert turns[0].text == "Look at this."
    assert all("base64" not in t.text for t in turns)


# ------------------------------------------------------------------- knowledge


async def test_the_session_context_kb_client_is_bound_to_the_agents_knowledge_bases() -> None:
    """`ctx.kb.search(query)` must hit the configured KBs without a tool passing ids."""
    hit = KbHit(chunk_id="c", document_id="d", filename="policy.md", score=1.0, text="Covered.")
    api = FakeApi(resolved_config(kb_ids=["kb-1", "kb-2"]), hits=[hit])
    ctx = FakeJobContext(_metadata())
    seen: list[Any] = []

    class _CapturingPack(NullPack):
        def tools(self, pack_ctx: Any) -> list[Any]:
            seen.append(pack_ctx)
            return []

    class _OnePackLoader(PackLoader):
        def get(self, pack_id: str) -> Any:
            return _CapturingPack()

    await run_session(ctx, _deps(api, pack_loader=_OnePackLoader([])))

    session_ctx = seen[0]
    assert await session_ctx.kb.search("is flood covered") == [hit]
    assert api.kb_queries[0][0] == ["kb-1", "kb-2"]


# --------------------------------------------------------------- workflow llm


def test_workflow_model_prefers_the_dedicated_slot_then_the_conversational_llm() -> None:
    """A realtime model cannot serve JSON extraction, so a fallback LLM is required."""
    dedicated, conversational = object(), object()

    assert _workflow_model(BuiltProviders(workflow_llm=dedicated, llm=conversational)) is dedicated
    assert _workflow_model(BuiltProviders(llm=conversational)) is conversational
    fallback = _workflow_model(BuiltProviders(realtime=object()))
    assert isinstance(fallback, inference.LLM), "a realtime model cannot do JSON extraction"


def test_workflow_model_fallback_reads_inference_credentials_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-W2-6: Inference objects are built with the model only, never explicit creds."""
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def _recorder(*args: Any, **kwargs: Any) -> str:
        calls.append((args, kwargs))
        return "inference-llm"

    monkeypatch.setattr(inference, "LLM", _recorder)

    assert _workflow_model(BuiltProviders(realtime=object())) == "inference-llm"
    assert calls == [((WORKFLOW_FALLBACK_LLM_MODEL,), {})]


def test_fallback_model_ids_come_from_the_provider_registry() -> None:
    """F-12: no hardcoded model ids; the fallbacks track the registry defaults."""
    from lkap_contracts import providers as provider_registry

    assert WORKFLOW_FALLBACK_LLM_MODEL == provider_registry.get("livekit-inference-llm").default_model
    assert FALLBACK_TTS_MODEL == provider_registry.get("livekit-inference-tts").default_model


# ------------------------------------------------------- D-W2-4 / D-W2-7 wiring


class _RecordingFrames(NoopFrameBuffer):
    def __init__(self) -> None:
        self.preferred: list[str | None] = []

    def set_preferred_source(self, source: str | None) -> None:
        self.preferred.append(source)


async def test_assemble_wires_ui_identity_and_the_video_source_preference() -> None:
    """The UI channel targets the api-minted identity; `set_video_source` reaches the buffer."""
    api = FakeApi(resolved_config(camera=True))
    ctx = FakeJobContext(_metadata())
    ui_kwargs: list[dict[str, Any]] = []
    frames = _RecordingFrames()

    def _ui(**kw: Any) -> NoopUiChannel:
        ui_kwargs.append(kw)
        return NoopUiChannel(kw.get("session_id", ""))

    await run_session(ctx, _deps(api, ui_channel_factory=_ui, frame_buffer_factory=lambda **kw: frames))

    assert ui_kwargs[0]["ui_identity"] == "user-guest"
    on_set_video_source = ui_kwargs[0]["on_set_video_source"]
    for source in ("screen", "camera", "none"):
        await on_set_video_source(source)
    assert frames.preferred == ["screen", "camera", None]


async def test_deps_from_env_ui_factory_passes_ui_identity_to_the_channel() -> None:
    from lkap_agent.main import _wire_optional_modules

    deps = _deps(FakeApi())
    _wire_optional_modules(deps)
    channel = deps.ui_channel_factory(room=rtc.Room(), session_id="sess-1", ui_identity="user-guest")

    assert channel._ui_identity == "user-guest"


# ------------------------------------------------------------- typed chat turns


async def test_typed_chat_is_routed_through_on_user_turn_completed() -> None:
    """`lk.chat` input reaches the pack hook and gets a reply, like a spoken turn."""
    from livekit.agents.voice.room_io import TextInputOptions

    from lkap_agent.platform_agent import platform_text_input_cb

    turns: list[str] = []

    class _TurnPack(NullPack):
        async def on_user_turn_completed(self, ctx: Any, turn_ctx: Any, new_message: Any) -> None:
            turns.append(new_message.text_content or "")

    class _Loader(PackLoader):
        def get(self, pack_id: str) -> Any:
            return _TurnPack()

    starter = RoomlessStarter()
    factory = _RecordingFactory()
    await run_session(
        FakeJobContext(_metadata()),
        _deps(FakeApi(resolved_config()), factory=factory, pack_loader=_Loader([]), session_starter=starter),
    )
    text_input = starter.room_options.text_input
    assert isinstance(text_input, TextInputOptions)
    assert text_input.text_input_cb is platform_text_input_cb
    assert starter.session is not None

    await text_input.text_input_cb(starter.session, SimpleNamespace(text="typed question"))
    await asyncio.sleep(0.2)

    assert turns == ["typed question"]
    assert any("typed question" in call.prompt for call in factory.llm.calls)


async def test_a_voice_only_agent_gets_no_typed_chat_callback() -> None:
    """D-W2-9p: `capabilities.chat_input=false` disables `lk.chat` at the worker."""
    starter = RoomlessStarter()
    await run_session(
        FakeJobContext(_metadata()),
        _deps(FakeApi(resolved_config(chat_input=False)), session_starter=starter),
    )

    assert starter.room_options.text_input is False


# ---------------------------------------------------------------- D-W2-9a snapshot


class _OrderPack(NullPack):
    """Records the platform's on_enter sequence against the UI channel."""

    def __init__(self, order: list[str]) -> None:
        super().__init__()
        self._order = order

    def initial_state(self, ctx: Any) -> dict[str, Any]:
        self._order.append("initial_state")
        return {"claim": {"stage": "intake"}}

    async def on_session_start(self, ctx: Any) -> None:
        self._order.append(f"on_session_start custom={ctx.ui.state.custom}")


class _OrderUi(NoopUiChannel):
    def __init__(self, order: list[str], session_id: str) -> None:
        super().__init__(session_id)
        self._order = order

    async def snapshot(self) -> None:
        self._order.append(f"snapshot custom={self.state.custom}")


async def test_on_enter_sends_the_initial_snapshot_after_the_pack_hook() -> None:
    """D-W2-9a: initial_state -> on_session_start -> platform snapshot, in that order."""
    order: list[str] = []
    api = FakeApi(resolved_config())
    ctx = FakeJobContext(_metadata())

    class _Loader(PackLoader):
        def get(self, pack_id: str) -> Any:
            return _OrderPack(order)

    await run_session(
        ctx,
        _deps(
            api,
            pack_loader=_Loader([]),
            ui_channel_factory=lambda **kw: _OrderUi(order, kw.get("session_id", "")),
        ),
    )
    await asyncio.sleep(0.1)

    assert order == [
        "initial_state",
        "on_session_start custom={'claim': {'stage': 'intake'}}",
        "snapshot custom={'claim': {'stage': 'intake'}}",
    ]


async def test_on_enter_still_snapshots_when_the_pack_hook_fails() -> None:
    order: list[str] = []

    class _BrokenPack(_OrderPack):
        async def on_session_start(self, ctx: Any) -> None:
            raise RuntimeError("pack bug")

    class _Loader(PackLoader):
        def get(self, pack_id: str) -> Any:
            return _BrokenPack(order)

    await run_session(
        FakeJobContext(_metadata()),
        _deps(
            FakeApi(resolved_config()),
            pack_loader=_Loader([]),
            ui_channel_factory=lambda **kw: _OrderUi(order, kw.get("session_id", "")),
        ),
    )
    await asyncio.sleep(0.1)

    assert order[-1].startswith("snapshot")


# ------------------------------------------------------------ D-W2-9e job shutdown


async def test_session_close_shuts_the_job_down() -> None:
    """D-W2-9e: without this the job (and the summary) outlives the hangup."""
    starter = RoomlessStarter()
    api = FakeApi(resolved_config())
    ctx = FakeJobContext(_metadata())

    await run_session(ctx, _deps(api, session_starter=starter))
    assert starter.session is not None
    await starter.session.aclose()
    await asyncio.sleep(0.05)

    assert len(ctx.shutdown_reasons) == 1
    assert ctx.shutdown_reasons[0].startswith("session closed: ")


async def test_the_error_handler_records_the_vision_degrade_event() -> None:
    """D-W2-8 R5 wiring: the synchronous `error` handler reports through the observer."""
    from livekit.agents.voice.events import ErrorEvent

    starter = RoomlessStarter()
    api = FakeApi(resolved_config(camera=True))
    ctx = FakeJobContext(_metadata())
    await run_session(ctx, _deps(api, session_starter=starter))
    assert starter.session is not None and starter.agent is not None
    starter.agent._last_turn_had_frame = True
    error = llm.LLMError(
        type="llm_error", timestamp=0.0, label="t", error=RuntimeError("no images"), recoverable=True
    )

    starter.session.emit("error", ErrorEvent(error=error, source=factory_llm(starter)))
    await ctx.fire_shutdown("done")

    messages = [e.payload.get("message", "") for e in api.events if e.type == "error"]
    assert any(m.startswith("vision injection disabled") for m in messages)


def factory_llm(starter: RoomlessStarter) -> Any:
    assert starter.session is not None
    return starter.session.llm


# ------------------------------------------------------- D-W2-11 name by construction


@pytest.mark.parametrize(
    "environ",
    [
        {"LIVEKIT_AGENT_NAME": "other-project-agent"},
        {"LIVEKIT_AGENT_NAME": "lkap-agent", "LIVEKIT_AGENT_NAME_OVERRIDE": "other-project-agent"},
        {"LIVEKIT_AGENT_NAME_OVERRIDE": "other-project-agent"},
    ],
    ids=["wrong", "override", "override-only"],
)
def test_require_agent_name_refuses_a_contradicting_set_source(environ: dict[str, str]) -> None:
    with pytest.raises(RuntimeError):
        require_agent_name(environ, _settings())


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"LIVEKIT_AGENT_NAME": ""},
        {"LIVEKIT_AGENT_NAME": "lkap-agent"},
        {"LIVEKIT_AGENT_NAME_OVERRIDE": "lkap-agent"},
    ],
    ids=["missing", "empty", "set", "override-matches"],
)
def test_require_agent_name_accepts_unset_or_matching_sources(environ: dict[str, str]) -> None:
    assert require_agent_name(environ, _settings()) == "lkap-agent"


def test_require_agent_name_refuses_a_settings_mismatch() -> None:
    settings = _settings()
    settings.livekit_agent_name = "other"
    with pytest.raises(RuntimeError):
        require_agent_name({"LIVEKIT_AGENT_NAME": "lkap-agent"}, settings)


@pytest.mark.parametrize(
    ("environ", "expected"),
    [({}, "lkap-agent"), ({"LIVEKIT_AGENT_NAME_OVERRIDE": "forced"}, "forced")],
)
def test_effective_agent_name_mirrors_the_sdk_precedence(environ: dict[str, str], expected: str) -> None:
    assert effective_agent_name(environ) == expected


class _FakeJobRequest:
    """The slice of `livekit.agents.JobRequest` that `only_lkap_jobs` touches."""

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        self.room = SimpleNamespace(name="room-1")
        self.accepted = False
        self.rejected = False

    async def accept(self) -> None:
        self.accepted = True

    async def reject(self) -> None:
        self.rejected = True


@pytest.mark.parametrize("agent_name", ["", "other-project-agent"])
async def test_only_lkap_jobs_rejects_other_agent_names(agent_name: str) -> None:
    req = _FakeJobRequest(agent_name)
    await only_lkap_jobs(cast(Any, req))
    assert req.rejected and not req.accepted


async def test_only_lkap_jobs_accepts_lkap_agent() -> None:
    req = _FakeJobRequest("lkap-agent")
    await only_lkap_jobs(cast(Any, req))
    assert req.accepted and not req.rejected


def test_worker_registers_as_lkap_agent_with_the_request_filter() -> None:
    # Registration ran at import; `AgentServer` has no public getter (D-W2-11 step 3).
    assert "LIVEKIT_AGENT_NAME_OVERRIDE" not in os.environ
    assert server._agent_name == "lkap-agent"
    assert server._request_fnc is only_lkap_jobs


def test_sdk_rtc_session_precedence_still_matches_our_assumptions() -> None:
    src = inspect.getsource(AgentServer.rtc_session)
    assert "LIVEKIT_AGENT_NAME_OVERRIDE" in src and "elif agent_name:" in src, (
        "livekit-agents changed rtc_session's agent-name precedence; re-read DECISIONS-W2 §D-W2-11"
    )
    assert src.index("LIVEKIT_AGENT_NAME_OVERRIDE") < src.index("elif agent_name:"), (
        "livekit-agents changed rtc_session's agent-name precedence; re-read DECISIONS-W2 §D-W2-11"
    )


def test_livekit_toml_agent_name_equals_required_agent_name() -> None:
    toml_path = Path(__file__).resolve().parents[2] / "livekit.toml"
    data = tomllib.loads(toml_path.read_text())
    assert data["agent"]["name"] == DEFAULT_AGENT_NAME


# ------------------------------------------------------------- noop fallbacks


async def test_noop_collaborators_satisfy_the_pack_protocols() -> None:
    """A session still runs with a dark panel when W1-AGENT-UI is not wired in."""
    ui = NoopUiChannel("sess-1")
    await ui.patch([])
    await ui.set_status("Working", "info")
    await ui.add_note("note")
    assert await ui.push_asset(b"x", "image/png", "sketch")
    assert (await ui.request_ui("toast", {}))["ok"] is False

    frames = NoopFrameBuffer()
    assert frames.latest() is None
    assert await frames.latest_jpeg() is None

    runner = NoopBackgroundRunner()
    results: list[int] = []

    async def _work() -> int:
        return 42

    async def _collect(value: int) -> None:
        results.append(value)

    job_id = runner.submit(name="job", coro=_work(), on_result=_collect)
    await runner._tasks[job_id]

    assert results == [42]
    runner.cancel(job_id)


# --------------------------------------------------------------- production wiring


async def test_deps_from_env_wires_the_real_collaborators(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Deps.from_env` must pick up W1-AGENT-UI / W1-AGENT-TOOLS where they exist.

    The factories are guarded by `try/except ImportError` so the worker still
    runs when a sibling package has not merged; this test is what catches a
    guard that silently swallows a *renamed* symbol instead of a missing module.
    """
    from lkap_agent.settings import get_settings

    for key, value in {
        "LIVEKIT_URL": "wss://example.livekit.cloud",
        "LIVEKIT_API_KEY": "k",
        "LIVEKIT_API_SECRET": "s",
        "LKAP_SERVICE_TOKEN": "svc",
        "LKAP_API_BASE_URL": "http://api.test",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    try:
        deps = Deps.from_env({"vad": "prewarmed"})
    finally:
        get_settings.cache_clear()

    room = rtc.Room()
    ui = deps.ui_channel_factory(room=room, session_id="sess-1")
    frames = deps.frame_buffer_factory(room=room, participant_identity="user-guest")

    assert type(ui).__name__ == "UiChannel"
    assert type(frames).__name__ == "FrameBuffer"
    assert deps.vad == "prewarmed"
    assert deps.fallback_speaker is not None
    runner = deps.background_runner_factory(
        ui=ui, session=object(), labels={"sync_claim_packet": "Claim writer"}
    )
    assert runner._labels == {"sync_claim_packet": "Claim writer"}
    assert deps.declarative_tools_builder([]) == []
    assert deps.mcp_servers_builder([]) == []


# ------------------------------------------------ REVIEW-FINAL F-09 resolve failure


async def test_a_resolve_failure_after_active_posts_a_failed_summary() -> None:
    """F-09: the api flips the row `active` before answering, so a bad answer must close it."""
    api = FakeApi(resolve_error=ConfigUnavailableError("resolved config failed validation"))
    ctx = FakeJobContext(_metadata())
    spoken: list[str] = []

    async def _speaker(job_ctx: Any, line: str) -> None:
        spoken.append(line)

    await run_session(ctx, _deps(api, fallback_speaker=_speaker))

    assert [s.status for s in api.summaries] == ["failed"]
    assert api.summaries[0].error == "resolved config failed validation"
    assert api.summaries[0].transcript == []
    assert spoken == [CONFIG_UNAVAILABLE_LINE]
    assert ctx.shutdown_reasons == ["configuration unavailable"]


async def test_a_failing_summary_post_after_a_resolve_error_still_speaks_and_shuts_down() -> None:
    """The F-09 summary is best-effort: an unreachable api must not skip the fixed line."""

    class _DownApi(FakeApi):
        async def put_summary(self, session_id: str, summary: Any) -> None:
            raise ConfigUnavailableError("api unreachable")

    api = _DownApi(resolve_error=ConfigUnavailableError("api unreachable"))
    ctx = FakeJobContext(_metadata())
    spoken: list[str] = []

    async def _speaker(job_ctx: Any, line: str) -> None:
        spoken.append(line)

    await run_session(ctx, _deps(api, fallback_speaker=_speaker))

    assert spoken == [CONFIG_UNAVAILABLE_LINE]
    assert ctx.shutdown_reasons == ["configuration unavailable"]


# -------------------------------------------------- REVIEW-FINAL F-01 start failure


class _FailingAvatar(_FakeAvatar):
    """A bey/tavus stand-in whose `start()` raises, as a bad avatar key does."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = 0

    async def start(self, session: AgentSession[Any], room: rtc.Room) -> None:
        self.events.append("start")
        raise RuntimeError("avatar rejected the api key")

    async def aclose(self) -> None:
        self.closed += 1


class _TeardownUi(NoopUiChannel):
    """Counts `start()` / `close()` so the test can see `_deactivate` ran."""

    def __init__(self, session_id: str) -> None:
        super().__init__(session_id)
        self.started = 0
        self.closed = 0

    def start(self) -> None:
        self.started += 1

    def close(self) -> None:
        self.closed += 1


async def test_an_avatar_that_fails_to_start_records_the_session_failed_never_ended() -> None:
    """F-01: the row is `failed` with the error, the user hears why, the job ends."""
    avatar = _FailingAvatar()
    api = FakeApi(resolved_config(with_avatar=True))
    ctx = FakeJobContext(_metadata())
    spoken: list[str] = []
    uis: list[_TeardownUi] = []

    async def _speaker(job_ctx: Any, line: str) -> None:
        spoken.append(line)

    def _ui(**kw: Any) -> _TeardownUi:
        uis.append(_TeardownUi(kw.get("session_id", "")))
        return uis[-1]

    await run_session(
        ctx,
        _deps(
            api,
            factory=_RecordingFactory(avatar=avatar),
            fallback_speaker=_speaker,
            ui_channel_factory=_ui,
        ),
    )

    assert [s.status for s in api.summaries] == ["failed"]
    assert api.summaries[0].error == "avatar rejected the api key"
    assert spoken == [START_FAILED_LINE]
    assert "start failed" in ctx.shutdown_reasons
    assert avatar.closed == 1
    assert uis[0].started == 1 and uis[0].closed == 1
    assert "session_started" not in api.event_types()

    # The shutdown callback registered before `_start` runs when the job ends:
    # it must not overwrite `failed` with `ended` or post a second summary.
    await ctx.fire_shutdown("start failed")
    assert [s.status for s in api.summaries] == ["failed"]
    assert api.event_types().count("session_ended") == 1


async def test_a_session_start_that_raises_records_failed_and_closes_the_session() -> None:
    """F-01: a half-started session is closed before the fixed line gets its own session."""

    class _HalfStarter(RoomlessStarter):
        async def __call__(
            self, *, session: AgentSession[Any], agent: Any, room: Any, room_options: Any
        ) -> None:
            await super().__call__(session=session, agent=agent, room=room, room_options=room_options)
            raise RuntimeError("RoomIO could not publish the audio track")

    starter = _HalfStarter()
    api = FakeApi(resolved_config())
    ctx = FakeJobContext(_metadata())
    spoken: list[str] = []

    async def _speaker(job_ctx: Any, line: str) -> None:
        spoken.append(line)

    await run_session(ctx, _deps(api, fallback_speaker=_speaker, session_starter=starter))
    await asyncio.sleep(0.05)

    assert [s.status for s in api.summaries] == ["failed"]
    assert api.summaries[0].error == "RoomIO could not publish the audio track"
    assert spoken == [START_FAILED_LINE]
    assert "start failed" in ctx.shutdown_reasons
    # `plan.session.aclose()` ran: its `close` event ended the job (D-W2-9e handler).
    assert any(r.startswith("session closed: ") for r in ctx.shutdown_reasons)
    assert "session_started" not in api.event_types()

    await ctx.fire_shutdown("session closed: user_initiated")
    assert [s.status for s in api.summaries] == ["failed"]


async def test_a_start_failure_still_speaks_and_shuts_down_when_the_summary_cannot_be_posted() -> None:
    """F-01 with the api down: the fixed line and the job shutdown must not depend on it."""

    class _DownApi(FakeApi):
        async def put_summary(self, session_id: str, summary: Any) -> None:
            raise ConfigUnavailableError("api unreachable")

    api = _DownApi(resolved_config(with_avatar=True))
    ctx = FakeJobContext(_metadata())
    spoken: list[str] = []

    async def _speaker(job_ctx: Any, line: str) -> None:
        spoken.append(line)

    await run_session(
        ctx, _deps(api, factory=_RecordingFactory(avatar=_FailingAvatar()), fallback_speaker=_speaker)
    )

    assert spoken == [START_FAILED_LINE]
    assert "start failed" in ctx.shutdown_reasons
    await ctx.fire_shutdown("start failed")  # the observer is closed: no second attempt, no raise


# ------------------------------------------------------ REVIEW-FINAL F-02 idle hangup


class _ManualClock:
    """An injected `Deps.sleep`: records each delay and returns only when released."""

    def __init__(self) -> None:
        self.delays: list[float] = []
        self._release = asyncio.Event()

    async def sleep(self, delay_s: float) -> None:
        self.delays.append(delay_s)
        await self._release.wait()

    async def advance(self) -> None:
        """Let every pending sleep return, then give the woken tasks a turn."""
        self._release.set()
        await asyncio.sleep(0.01)


def _user_state(old: str, new: str) -> Any:
    from livekit.agents.voice.events import UserStateChangedEvent

    return UserStateChangedEvent(old_state=cast(Any, old), new_state=cast(Any, new))


async def _idle_session(
    *, idle_hangup_s: float | None = 120.0
) -> tuple[FakeJobContext, RoomlessStarter, _ManualClock]:
    starter = RoomlessStarter()
    clock = _ManualClock()
    ctx = FakeJobContext(_metadata())
    deps = _deps(FakeApi(resolved_config()), session_starter=starter, sleep=clock.sleep)
    deps.settings.idle_hangup_s = idle_hangup_s
    await run_session(ctx, deps)
    assert starter.session is not None
    assert starter.session.agent_state == "listening"
    return ctx, starter, clock


async def test_an_away_user_is_hung_up_after_the_idle_timeout() -> None:
    """F-02: `away` while the agent listens starts the timer; when it fires, the job ends."""
    ctx, starter, clock = await _idle_session()
    assert starter.session is not None

    starter.session.emit("user_state_changed", _user_state("listening", "away"))
    await asyncio.sleep(0)
    assert clock.delays == [120.0]
    assert "idle" not in ctx.shutdown_reasons

    await clock.advance()
    assert ctx.shutdown_reasons == ["idle"]


async def test_a_user_who_comes_back_cancels_the_idle_hangup() -> None:
    ctx, starter, clock = await _idle_session()
    assert starter.session is not None

    starter.session.emit("user_state_changed", _user_state("listening", "away"))
    await asyncio.sleep(0)
    starter.session.emit("user_state_changed", _user_state("away", "listening"))
    await clock.advance()

    assert clock.delays == [120.0]
    assert "idle" not in ctx.shutdown_reasons


@pytest.mark.parametrize("idle_hangup_s", [None, 0.0], ids=["none", "zero"])
async def test_a_disabled_idle_hangup_starts_no_timer(idle_hangup_s: float | None) -> None:
    ctx, starter, clock = await _idle_session(idle_hangup_s=idle_hangup_s)
    assert starter.session is not None

    starter.session.emit("user_state_changed", _user_state("listening", "away"))
    await clock.advance()

    assert clock.delays == []
    assert ctx.shutdown_reasons == []


async def test_away_while_the_agent_is_busy_starts_no_idle_timer() -> None:
    """Only a listening/idle agent idles out; a speaking or thinking one is still working."""
    ctx, starter, clock = await _idle_session()
    assert starter.session is not None
    starter.session._agent_state = "speaking"

    starter.session.emit("user_state_changed", _user_state("listening", "away"))
    await clock.advance()

    assert clock.delays == []
    assert ctx.shutdown_reasons == []


async def test_job_shutdown_cancels_a_pending_idle_timer() -> None:
    ctx, starter, clock = await _idle_session()
    assert starter.session is not None

    starter.session.emit("user_state_changed", _user_state("listening", "away"))
    await asyncio.sleep(0)
    await ctx.fire_shutdown("client disconnected")
    await clock.advance()

    assert clock.delays == [120.0]
    assert "idle" not in ctx.shutdown_reasons


# --------------------------------------------- REVIEW-FINAL F-11 background jobs


async def test_shutdown_cancels_background_jobs_before_the_pack_teardown() -> None:
    """F-11: an in-flight workflow must not outlive the hangup."""
    order: list[str] = []

    class _Runner(NoopBackgroundRunner):
        def cancel_all(self) -> int:
            order.append("cancel_all")
            return super().cancel_all()

    class _EndPack(NullPack):
        async def on_session_end(self, ctx: Any, reason: str) -> None:
            order.append("on_session_end")

    class _Loader(PackLoader):
        def get(self, pack_id: str) -> Any:
            return _EndPack()

    runner = _Runner()
    api = FakeApi(resolved_config())
    ctx = FakeJobContext(_metadata())
    await run_session(ctx, _deps(api, pack_loader=_Loader([]), background_runner_factory=lambda **kw: runner))

    async def _slow() -> None:
        await asyncio.sleep(10)

    job_id = runner.submit(name="sync_claim_packet", coro=_slow())
    await asyncio.sleep(0)
    await ctx.fire_shutdown("client disconnected")
    await asyncio.sleep(0)

    assert order == ["cancel_all", "on_session_end"]
    assert runner._tasks[job_id].cancelled()
    assert [s.status for s in api.summaries] == ["ended"]


# ----------------------------------------------------- D-W3-1 / F-03 seam with WP-B


async def test_a_builtin_tool_escalation_reaches_the_session_observer() -> None:
    """D-W3-1: `ctx.record_event` is bound to `SessionObserver.record` and reaches the api."""
    from lkap_agent.tools.builtin.escalate_to_human import build_escalate_to_human_tool

    built: list[Any] = []

    def _builtin(session_ctx: Any, disabled: list[str], http_enabled: bool) -> list[Any]:
        built.append(build_escalate_to_human_tool(session_ctx))
        return built

    api = FakeApi(resolved_config())
    ctx = FakeJobContext(_metadata())
    await run_session(ctx, _deps(api, builtin_tools_builder=_builtin))

    run_ctx = cast(Any, SimpleNamespace(function_call=SimpleNamespace(call_id="call-9")))
    await built[0](context=run_ctx, reason="caller asked for a person", urgency="high")
    await ctx.fire_shutdown("done")

    escalations = api.events_of("escalation")
    assert [e.payload for e in escalations] == [{"reason": "caller asked for a person", "urgency": "high"}]


async def test_an_assistant_turn_reaches_the_pack_agent_turn_hook() -> None:
    """F-03: `conversation_item_added` is wired to `PlatformAgent.on_conversation_item`."""
    turns: list[tuple[str, bool]] = []

    class _TurnPack(NullPack):
        async def on_agent_turn_completed(self, ctx: Any, text: str, interrupted: bool) -> None:
            turns.append((text, interrupted))

    class _Loader(PackLoader):
        def get(self, pack_id: str) -> Any:
            return _TurnPack()

    await run_session(
        FakeJobContext(_metadata()),
        _deps(FakeApi(resolved_config(greeting="Hello there!")), pack_loader=_Loader([])),
    )
    await asyncio.sleep(0.1)

    assert ("Hello there!", False) in turns
