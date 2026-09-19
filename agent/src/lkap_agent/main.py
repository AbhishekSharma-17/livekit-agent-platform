"""The `lkap-agent` worker: one `AgentServer`, one `rtc_session` (D1).

The decorated entrypoint is deliberately three lines long. Everything that can
go wrong lives in :func:`run_session`, which takes a `JobContextLike` and a
:class:`Deps` bundle, so the whole session lifecycle is exercisable offline with
fakes — no LiveKit connection, no api, no vendor keys.

The agent name is fixed by construction (DECISIONS-W2 §D-W2-11): the worker
registers with `rtc_session(agent_name="lkap-agent", on_request=only_lkap_jobs)`,
so the SDK's precedence (`LIVEKIT_AGENT_NAME_OVERRIDE` env -> explicit argument ->
`LIVEKIT_AGENT_NAME` env -> "") can never yield an unnamed worker that would take
automatic dispatch for every room in the shared project (including the
unrelated `other-project-agent`'s). `require_agent_name` only refuses a *set* source
that contradicts that name, and `only_lkap_jobs` rejects any job whose
`JobRequest.agent_name` is not `lkap-agent`. `livekit.toml`'s `[agent] name`
must equal `REQUIRED_AGENT_NAME` (a unit test parses it).

Start-up order (docs/ARCHITECTURE.md §4, adjusted for two verified SDK constraints):

    resolve config -> build providers/session/tools -> ctx.connect()
      -> avatar.start() + avatar.wait_for_join() (when configured)
      -> session.start(room=..., room_options=...)

Both constraints force `ctx.connect()` ahead of the rest, where the common
LiveKit template puts it last:

* `RoomOptions(participant_identity=...)` — which links RoomIO to the browser
  participant so an avatar participant can never be picked up — makes
  `RoomIO.start()` read `room.local_participant` eagerly, and `rtc.Room` raises
  "cannot access local participant before connecting".
* `bey`/`tavus` `AvatarSession.start()` mints the avatar's token from
  `room.name` and the job's local participant identity.

The rule §4 actually states still holds: the config is fetched **before**
connecting, so a bad config fails the job before the user hears anything. The
cost is the client's pre-connect audio buffer, which only helps agents that
connect lazily.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from livekit import rtc
from livekit.agents import Agent, AgentServer, AgentSession, JobProcess, JobRequest, inference
from livekit.agents import llm as lk_llm
from livekit.agents.voice.events import UserStateChangedEvent
from livekit.agents.voice.room_io import TextInputOptions
from lkap_contracts import providers as provider_registry
from lkap_contracts.agent_config import ResolvedAgentConfig
from lkap_contracts.api_models import SessionSummaryIn
from lkap_contracts.dispatch import DispatchMetadata
from lkap_contracts.ui_protocol import ActivityEvent, ChecklistItem, Tone, UiPatchOp, UiState
from packs.base import FrameSnapshot, Pack

from lkap_agent.config_client import (
    ApiKbClient,
    ConfigClient,
    ConfigClientProtocol,
    ConfigUnavailableError,
    SessionEndedError,
    SessionNotFoundError,
)
from lkap_agent.logging import configure_logging, get_logger
from lkap_agent.observability import SessionObserver, bind_session_context
from lkap_agent.packs.loader import PackLoader
from lkap_agent.platform_agent import (
    PlatformAgent,
    SessionContext,
    _noop_record_event,
    platform_text_input_cb,
)
from lkap_agent.providers.factory import BuiltProviders, ProviderFactory
from lkap_agent.session_builder import SessionBuilder, SessionPlan
from lkap_agent.settings import Settings, get_settings
from lkap_agent.workflow_llm import PromptJsonStructuredLLM

__all__ = [
    "REQUIRED_AGENT_NAME",
    "Deps",
    "JobContextLike",
    "effective_agent_name",
    "only_lkap_jobs",
    "prewarm",
    "require_agent_name",
    "run_session",
    "server",
]

logger = get_logger(__name__)

#: Spoken when the resolved config cannot be fetched (docs/ARCHITECTURE.md §4).
CONFIG_UNAVAILABLE_LINE = (
    "Sorry, this assistant is not available right now because its configuration "
    "could not be loaded. Please try again in a moment."
)

#: Spoken when the config resolved but the avatar or `session.start` failed (F-01).
START_FAILED_LINE = "Sorry, this assistant could not start. Please try again in a moment."


def _registry_default_model(provider_id: str) -> str:
    """The registry's `default_model` for `provider_id` (REVIEW-FINAL F-12).

    Raises:
        RuntimeError: If the registry entry has no default model, which would be
            a contracts bug rather than something a session could recover from.
    """
    model = provider_registry.get(provider_id).default_model
    if not model:
        raise RuntimeError(f"provider registry entry {provider_id!r} has no default_model")
    return model


#: The Inference TTS used only for the fixed failure lines; needs LiveKit credentials only.
FALLBACK_TTS_MODEL = _registry_default_model("livekit-inference-tts")

#: Used for pack workflows when the pipeline has no plain `llm.LLM` (realtime mode).
WORKFLOW_FALLBACK_LLM_MODEL = _registry_default_model("livekit-inference-llm")

_VAD_USERDATA_KEY = "vad"


# --------------------------------------------------------------------- context


class JobContextLike(Protocol):
    """The slice of `livekit.agents.JobContext` that `run_session` uses."""

    @property
    def job(self) -> Any:
        """The assigned job; only `.id` and `.metadata` are read."""
        ...

    @property
    def proc(self) -> Any:
        """The worker process; only `.userdata` is read."""
        ...

    @property
    def room(self) -> rtc.Room:
        """The room, connected by `connect()`."""
        ...

    async def connect(self) -> None:
        """Join the room."""
        ...

    def shutdown(self, reason: str = ...) -> None:
        """End the job."""
        ...

    def add_shutdown_callback(self, callback: Any) -> None:
        """Register a coroutine run when the job shuts down."""
        ...


# ------------------------------------------------------- placeholder runtime
# W1-AGENT-UI owns `ui/channel.py`, `vision.py` and `tools/background.py`.
# Until those land (and for unit tests), `Deps.from_env` falls back to the
# no-ops below so a session still runs: the agent talks, tools that do not
# touch the panel work, and nothing crashes on a missing import.


class NoopUiChannel:
    """A `packs.base.UiChannel` that records nothing and sends nothing."""

    def __init__(self, session_id: str = "") -> None:
        self.seq = 0
        self.state = UiState()
        self._session_id = session_id

    async def patch(self, ops: list[UiPatchOp]) -> None:
        """Drop the patch."""
        self.seq += 1

    async def snapshot(self) -> None:
        """Drop the snapshot."""

    async def set_status(self, label: str, tone: Tone) -> None:
        """Drop the status."""

    async def add_note(self, text: str, kind: str = "note", key: str | None = None) -> None:
        """Drop the note."""

    async def set_checklist(self, items: list[ChecklistItem]) -> None:
        """Drop the checklist."""

    async def push_asset(
        self,
        data: bytes,
        mime: str,
        kind: str,
        caption: str | None = None,
        meta: dict[str, str] | None = None,
    ) -> str:
        """Discard the asset and return a synthetic id."""
        return uuid.uuid4().hex

    async def activity(self, event: ActivityEvent) -> None:
        """Drop the activity event."""

    async def request_ui(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Report that no UI is listening."""
        return {"ok": False}


class NoopFrameBuffer:
    """A `packs.base.FrameBufferProto` that never has a frame."""

    def latest(self, max_age_s: float | None = None) -> FrameSnapshot | None:
        """Always `None`."""
        return None

    async def latest_jpeg(
        self, max_age_s: float | None = None, max_width: int = 1024
    ) -> tuple[bytes, FrameSnapshot] | None:
        """Always `None`."""
        return None


class NoopBackgroundRunner:
    """A `packs.base.BackgroundRunner` that runs jobs but delivers no replies."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def submit(
        self,
        *,
        name: str,
        coro: Awaitable[Any],
        on_result: Callable[[Any], Awaitable[None]] | None = None,
        urgent: Callable[[Any], bool] | None = None,
        urgent_instructions: Callable[[Any], str] | None = None,
        routine_note: Callable[[Any], str | None] | None = None,
        call_id: str | None = None,
    ) -> str:
        """Run `coro` and `on_result`; ignore the conversational delivery hooks."""
        job_id = call_id or uuid.uuid4().hex

        async def _run() -> None:
            try:
                result = await coro
            except Exception:
                logger.warning("background job failed", job=name, job_id=job_id, exc_info=True)
                return
            if on_result is not None:
                await on_result(result)

        self._tasks[job_id] = asyncio.create_task(_run())
        return job_id

    def cancel(self, job_id: str) -> None:
        """Cancel a still-running job."""
        task = self._tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()

    def cancel_all(self) -> int:
        """Cancel every still-running job; returns how many were cancelled."""
        running = [t for t in self._tasks.values() if not t.done()]
        for task in running:
            task.cancel()
        return len(running)


# ------------------------------------------------------------------------ deps


def _no_tools(*_args: Any, **_kwargs: Any) -> list[Any]:
    """Placeholder for W1-AGENT-TOOLS' tool builders."""
    return []


async def _sleep(delay_s: float) -> None:
    """The production clock for timers owned by `run_session`."""
    await asyncio.sleep(delay_s)


@dataclass(slots=True)
class Deps:
    """Everything `run_session` needs, injected so tests can replace any of it."""

    settings: Settings
    config_client: ConfigClientProtocol
    pack_loader: PackLoader
    provider_factory: ProviderFactory = field(default_factory=ProviderFactory)
    session_builder: SessionBuilder = field(default_factory=SessionBuilder)
    vad: Any | None = None
    #: Built per session; `None` in realtime mode (the model detects turns itself).
    turn_detector_factory: Callable[[], Any | None] = lambda: inference.TurnDetector()
    ui_channel_factory: Callable[..., Any] = lambda **kw: NoopUiChannel(kw.get("session_id", ""))
    frame_buffer_factory: Callable[..., Any] = lambda **kw: NoopFrameBuffer()
    background_runner_factory: Callable[..., Any] = lambda **kw: NoopBackgroundRunner()
    builtin_tools_builder: Callable[..., list[Any]] = _no_tools
    declarative_tools_builder: Callable[..., list[Any]] = _no_tools
    mcp_servers_builder: Callable[..., list[Any]] = _no_tools
    fallback_speaker: Callable[[JobContextLike, str], Awaitable[None]] | None = None
    #: Seam for `session.start`, so a unit test can run a session without a room.
    session_starter: Callable[..., Awaitable[None]] = field(default=lambda **kw: _start_session(**kw))
    #: Clock seam for the idle hangup timer (F-02); tests inject a controllable sleep.
    sleep: Callable[[float], Awaitable[None]] = _sleep

    @classmethod
    def from_env(cls, proc_userdata: dict[Any, Any] | None = None) -> Deps:
        """Build the production dependency set from settings and the worker process.

        Optional Wave-1 modules (`ui.channel`, `vision`, `tools.background`,
        `tools.builtin`, `tools.declarative`) are imported defensively: until the
        packages that own them land, the no-op fallbacks above keep the worker
        runnable end to end.
        """
        settings = get_settings()
        deps = cls(
            settings=settings,
            config_client=ConfigClient(settings.api_base_url, settings.service_token),
            pack_loader=PackLoader(settings.packs_list),
            vad=(proc_userdata or {}).get(_VAD_USERDATA_KEY),
            fallback_speaker=speak_fixed_line,
        )
        _wire_optional_modules(deps)
        return deps


def _wire_optional_modules(deps: Deps) -> None:
    """Point the factories at the real W1-AGENT-UI / W1-AGENT-TOOLS implementations.

    Each import is guarded so this package still imports, and a worker still
    runs, when a sibling package has not merged yet: the no-op fallbacks above
    keep the conversation working with a dark panel instead of failing the job.
    """
    allowed_hosts = deps.settings.http_tool_allowed_hosts_list

    try:
        from lkap_agent.ui.channel import UiChannel as UiChannelImpl  # noqa: PLC0415

        def _make_ui(
            *,
            room: rtc.Room,
            session_id: str,
            on_ui_action: Any = None,
            on_set_video_source: Any = None,
            log: Any = None,
            ui_identity: str | None = None,
        ) -> Any:
            return UiChannelImpl(
                room,
                session_id,
                ui_identity=ui_identity,
                on_ui_action=on_ui_action,
                on_set_video_source=on_set_video_source,
                log=log,
            )

        deps.ui_channel_factory = _make_ui
    except ImportError:
        logger.warning("lkap_agent.ui.channel not available: UI state will not be published")

    try:
        from lkap_agent.vision import FrameBuffer  # noqa: PLC0415

        def _make_frames(*, room: rtc.Room, participant_identity: str | None = None, **_: Any) -> Any:
            return FrameBuffer(room, participant_identity=participant_identity)

        deps.frame_buffer_factory = _make_frames
    except ImportError:
        logger.warning("lkap_agent.vision not available: camera and screen frames are ignored")

    try:
        from lkap_agent.tools.background import BackgroundToolRunner  # noqa: PLC0415

        def _make_background(
            *,
            ui: Any,
            session: AgentSession[Any],
            labels: dict[str, str] | None = None,
            record_event: Any = None,
            **_: Any,
        ) -> Any:
            return BackgroundToolRunner(ui, session, labels=labels, record_event=record_event)

        deps.background_runner_factory = _make_background
    except ImportError:
        logger.warning("lkap_agent.tools.background not available: background results stay silent")

    try:
        from lkap_agent.tools.builtin import build_builtin_tools  # noqa: PLC0415

        def _make_builtin(ctx: Any, disabled: list[str], http_enabled: bool) -> list[Any]:
            return list(
                build_builtin_tools(ctx, disabled, http_enabled, platform_allowed_hosts=allowed_hosts)
            )

        deps.builtin_tools_builder = _make_builtin
    except ImportError:
        logger.warning("lkap_agent.tools.builtin not available: no built-in tools registered")

    try:
        from lkap_agent.tools.declarative import build_http_tools, build_mcp_servers  # noqa: PLC0415

        def _make_http(defs: list[Any]) -> list[Any]:
            return list(build_http_tools(defs, platform_allowed_hosts=allowed_hosts))

        deps.declarative_tools_builder = _make_http
        deps.mcp_servers_builder = lambda defs: list(build_mcp_servers(defs))
    except ImportError:
        logger.warning("lkap_agent.tools.declarative not available: HTTP/MCP tools are skipped")


# -------------------------------------------------------------- failure path


async def speak_fixed_line(ctx: JobContextLike, line: str) -> None:
    """Say one fixed line through LiveKit Inference TTS, then tear down.

    Used when the config could not be resolved or the session could not start:
    the caller has already connected to the room (and closed any half-started
    session), so the user hears why nothing is happening instead of silence.
    Inference needs only the worker's own LiveKit credentials.
    """
    session: AgentSession[Any] = AgentSession(
        llm=cast(Any, None),
        stt=cast(Any, None),
        vad=None,
        tts=inference.TTS(model=FALLBACK_TTS_MODEL),
    )
    try:
        await session.start(Agent(instructions="Deliver one fixed message."), room=ctx.room)
        await session.say(line).wait_for_playout()
    except Exception:
        logger.warning("could not speak the fixed failure line", exc_info=True)
    finally:
        await session.aclose()


# ------------------------------------------------------------------- session


async def run_session(ctx: JobContextLike, deps: Deps) -> None:
    """Run one dispatched job from metadata to summary.

    Args:
        ctx: The job context (or any object satisfying :class:`JobContextLike`).
        deps: The injected collaborators.

    Raises:
        ValueError: If the dispatch metadata is missing or malformed — the api is
            the only writer of that field, so a bad value is a platform bug, not
            a user-visible failure worth speaking.
    """
    meta = _parse_dispatch_metadata(ctx)
    bind_session_context(
        session_id=meta.session_id,
        agent_id=meta.agent_id,
        job_id=str(getattr(ctx.job, "id", "")),
    )
    logger.info("job accepted", config_version=meta.config_version)

    try:
        resolved = await deps.config_client.resolve(meta.session_id)
    except ConfigUnavailableError as exc:
        logger.error("could not resolve the agent config", error=str(exc))
        if not isinstance(exc, SessionNotFoundError | SessionEndedError):
            # F-09: the api may already have flipped the row to `active`.
            await _report_resolve_failure(deps, meta.session_id, exc)
        await _fail_cleanly(ctx, deps, reason="configuration unavailable")
        return

    observer = SessionObserver(session_id=meta.session_id, client=deps.config_client)
    try:
        plan, agent = _assemble(ctx, deps, resolved, record_event=observer.record)
    except Exception as exc:
        logger.error("could not build the session", error=str(exc), exc_info=True)
        await observer.shutdown(reason="build failed", status="failed", error=str(exc))
        await _fail_cleanly(ctx, deps, reason="configuration invalid")
        return

    observer.attach(plan.session)
    idle = _IdleHangup(ctx=ctx, session=plan.session, delay_s=deps.settings.idle_hangup_s, sleep=deps.sleep)
    plan.session.on("user_state_changed", idle.on_user_state_changed)
    plan.session.on("function_tools_executed", agent.on_function_tools_executed)
    # F-03: drives the pack's `on_agent_turn_completed` hook.
    plan.session.on("conversation_item_added", agent.on_conversation_item)
    plan.session.on("error", _vision_degrade_handler(agent, observer))
    # D-W2-9e: `close_on_disconnect` only closes the AgentSession; the job (and
    # with it the shutdown callback that posts the summary) must be ended here.
    plan.session.on("close", _job_shutdown_handler(ctx))
    ctx.add_shutdown_callback(_shutdown_callback(agent, observer, idle))

    try:
        await _start(ctx, plan, agent, deps)
    except Exception as exc:
        await _abort_start(ctx, deps, plan, agent, observer, idle, exc)
        return
    observer.record("session_started", {"pipeline_mode": resolved.config.pipeline.mode})
    logger.info("session started", pack_id=resolved.pack_id, ui_panel_id=resolved.ui_panel_id)


async def _report_resolve_failure(deps: Deps, session_id: str, exc: ConfigUnavailableError) -> None:
    """Best-effort `failed` summary for a resolve that failed after the row went `active`.

    The api flips `created -> active` before it answers `resolved`, so a response
    the worker cannot use (validation error, transport failure on the way back)
    would otherwise leave the row `active` until the 6 h sweep (F-09).
    """
    try:
        await deps.config_client.put_summary(
            session_id, SessionSummaryIn(status="failed", usage={}, transcript=[], error=str(exc))
        )
    except Exception:
        logger.warning("could not post the failed summary after a resolve error", exc_info=True)


async def _abort_start(
    ctx: JobContextLike,
    deps: Deps,
    plan: SessionPlan,
    agent: PlatformAgent,
    observer: SessionObserver,
    idle: _IdleHangup,
    exc: Exception,
) -> None:
    """Tear down a session whose avatar or `session.start` failed (F-01).

    The order matters: the observer posts `failed` first, which makes the
    shutdown callback registered earlier a no-op (`SessionObserver.shutdown` is
    idempotent), so it can never overwrite the row with `ended`. The session is
    closed before the fixed line is spoken because `speak_fixed_line` starts a
    second `AgentSession` on the same room; a half-started first one would
    publish a second agent audio track.
    """
    logger.error("could not start the session", error=str(exc), exc_info=True)
    try:
        await observer.shutdown(reason="start failed", status="failed", error=str(exc))
    except Exception:
        # The observer is already marked closed, so nothing can later post `ended`.
        logger.warning("could not post the failed summary after a start error", exc_info=True)
    idle.close()
    _deactivate(agent)
    avatar_aclose = getattr(plan.avatar, "aclose", None)
    if callable(avatar_aclose):
        with contextlib.suppress(Exception):
            await avatar_aclose()
    with contextlib.suppress(Exception):
        await plan.session.aclose()
    await _fail_cleanly(ctx, deps, reason="start failed", line=START_FAILED_LINE)


class _IdleHangup:
    """Hangs up a session whose user stayed `away` for `delay_s` (F-02).

    The SDK reports `away` only after `voice.user_away_timeout_s` of silence
    while both sides are listening, and then only flips a state flag; nothing
    ends the room, so STT and Inference keep billing. This watcher starts a
    timer on `away` (when the agent is listening or idle), cancels it on any
    other user state, and calls `ctx.shutdown(reason="idle")` when it fires.
    """

    _IDLE_AGENT_STATES = ("listening", "idle")

    def __init__(
        self,
        *,
        ctx: JobContextLike,
        session: AgentSession[Any],
        delay_s: float | None,
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        self._ctx = ctx
        self._session = session
        self._delay_s = delay_s
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None
        # The last state seen on the event, not `session.user_state`: the handler
        # observes every transition, and a test can emit events without the SDK.
        self._user_state: str | None = None
        self._closed = False

    @property
    def pending(self) -> bool:
        """Whether an idle timer is currently running."""
        return self._task is not None and not self._task.done()

    def on_user_state_changed(self, ev: UserStateChangedEvent) -> None:
        """Synchronous `user_state_changed` handler: start or cancel the timer."""
        self._user_state = ev.new_state
        if ev.new_state != "away":
            self._cancel()
            return
        if self._closed or not self._delay_s or self.pending:
            return
        if self._session.agent_state not in self._IDLE_AGENT_STATES:
            return
        self._task = asyncio.create_task(self._fire(self._delay_s))

    async def _fire(self, delay_s: float) -> None:
        await self._sleep(delay_s)
        if self._closed or self._user_state != "away":
            return
        logger.info("hanging up idle session", idle_hangup_s=delay_s)
        self._ctx.shutdown(reason="idle")

    def _cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    def close(self) -> None:
        """Cancel any pending timer and ignore later events (session shutdown)."""
        self._closed = True
        self._cancel()


def _vision_degrade_handler(agent: PlatformAgent, observer: SessionObserver) -> Callable[[Any], None]:
    """Build the synchronous `error` handler for D-W2-8 R5 (vision auto-degrade)."""

    def _on_error(ev: Any) -> None:
        message = agent.on_session_error(ev)
        if message is not None:
            observer.record("error", {"message": message})

    return _on_error


def _job_shutdown_handler(ctx: JobContextLike) -> Callable[[Any], None]:
    """Build the synchronous `close` handler that ends the job (D-W2-9e)."""

    def _on_close(ev: Any) -> None:
        reason = getattr(ev, "reason", "unknown")
        reason_text = getattr(reason, "value", reason)
        logger.info("agent session closed, shutting down the job", reason=str(reason_text))
        ctx.shutdown(reason=f"session closed: {reason_text}")

    return _on_close


def _parse_dispatch_metadata(ctx: JobContextLike) -> DispatchMetadata:
    raw = getattr(ctx.job, "metadata", "") or ""
    if not raw.strip():
        raise ValueError(
            "job dispatched without metadata: lkap-agent only accepts explicit dispatch "
            "from POST /v1/agents/{id}/connect"
        )
    return DispatchMetadata.model_validate_json(raw)


def _assemble(
    ctx: JobContextLike,
    deps: Deps,
    resolved: ResolvedAgentConfig,
    *,
    record_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> tuple[SessionPlan, PlatformAgent]:
    """Build providers, the session, the pack context and the agent.

    The `UiChannel` needs a `on_ui_action` callback that reaches the pack *with*
    the session context, but the context needs the channel — so the callbacks
    close over a one-slot cell that is filled as soon as the context exists.
    """
    providers: BuiltProviders = deps.provider_factory.build_all(resolved)
    is_realtime = resolved.config.pipeline.mode == "realtime"
    plan = deps.session_builder.build(
        resolved,
        providers,
        vad=None if is_realtime else deps.vad,
        turn_detector=None if is_realtime else deps.turn_detector_factory(),
    )

    if plan.room_options.text_input is not False:
        # Typed chat must reach `on_user_turn_completed` like a spoken turn does.
        plan.room_options.text_input = TextInputOptions(text_input_cb=platform_text_input_cb)

    pack: Pack = deps.pack_loader.get(resolved.pack_id)
    log = logger.bind(pack_id=resolved.pack_id)
    cell: list[SessionContext] = []

    async def _on_ui_action(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not cell:  # pragma: no cover - the cell is filled before the room connects
            return {"ok": False, "error": "session not ready"}
        return await pack.on_ui_action(cell[0], action, payload)

    async def _on_set_video_source(source: str) -> None:
        if cell:
            cell[0].userdata["video_source"] = source
            # D-W2-4: the frame buffer honours the UI's selection; "none" clears it.
            set_preferred = getattr(cell[0].frames, "set_preferred_source", None)
            if callable(set_preferred):
                set_preferred(source if source in ("camera", "screen") else None)
        log.debug("ui selected a video source", source=source)

    ui = deps.ui_channel_factory(
        room=ctx.room,
        session_id=resolved.session_id,
        on_ui_action=_on_ui_action,
        on_set_video_source=_on_set_video_source,
        log=log,
        ui_identity=resolved.participant_identity,
    )
    frames = deps.frame_buffer_factory(room=ctx.room, participant_identity=resolved.participant_identity)
    session_ctx = SessionContext(
        session_id=resolved.session_id,
        agent_id=resolved.agent_id,
        pipeline_mode=resolved.config.pipeline.mode,
        config=resolved.config,
        pack_settings=dict(resolved.config.pack_settings),
        session=plan.session,
        room=ctx.room,
        ui=ui,
        frames=frames,
        kb=ApiKbClient(deps.config_client, resolved.kb_ids),
        workflow_llm=PromptJsonStructuredLLM(_workflow_model(providers)),
        background=deps.background_runner_factory(
            ui=ui,
            session=plan.session,
            labels={m.name: m.activity_label for m in pack.tool_meta() if m.activity_label},
            record_event=record_event,
        ),
        image_gen=providers.image_gen,
        log=log,
        record_event=record_event or _noop_record_event,
    )
    cell.append(session_ctx)

    tools: list[lk_llm.Tool | lk_llm.Toolset] = [
        *deps.builtin_tools_builder(
            session_ctx,
            resolved.config.tools.builtin_disabled,
            resolved.config.tools.http_request_enabled,
        ),
        *deps.declarative_tools_builder([t for t in resolved.tools if t.kind == "http"]),
        *pack.tools(session_ctx),
    ]
    mcp_servers = deps.mcp_servers_builder([t for t in resolved.tools if t.kind == "mcp"])

    agent = PlatformAgent(
        ctx=session_ctx,
        pack=pack,
        tools=tools,
        mcp_servers=mcp_servers or None,
        has_tts=plan.has_tts,
        vision_max_frame_age_s=deps.settings.vision_max_frame_age_s,
        record_event=record_event,
    )
    return plan, agent


def _workflow_model(providers: BuiltProviders) -> Any:
    """Pick the LLM that runs pack workflows.

    The dedicated `workflow_llm` slot wins; otherwise the conversational LLM in
    cascaded mode; otherwise a LiveKit Inference LLM, because a `RealtimeModel`
    is not an `llm.LLM` and cannot serve a one-shot JSON extraction. Like every
    other Inference object in the worker, it reads `LIVEKIT_API_KEY` /
    `LIVEKIT_API_SECRET` from the process environment (DECISIONS-W2 D-W2-6).
    """
    if providers.workflow_llm is not None:
        return providers.workflow_llm
    if providers.llm is not None:
        return providers.llm
    return inference.LLM(WORKFLOW_FALLBACK_LLM_MODEL)


async def _start_session(
    *,
    session: AgentSession[Any],
    agent: PlatformAgent,
    room: rtc.Room,
    room_options: Any,
) -> None:
    """The production `session.start` call; replaceable through `Deps`."""
    await session.start(agent, room=room, room_options=room_options)


async def _start(ctx: JobContextLike, plan: SessionPlan, agent: PlatformAgent, deps: Deps) -> None:
    """Connect, start the avatar if configured, then start the session.

    See the module docstring for why `ctx.connect()` leads: both the explicit
    participant link and the avatar session need a connected room.
    """
    await ctx.connect()
    _activate(agent)
    if plan.avatar is not None:
        await plan.avatar.start(plan.session, room=ctx.room)
        await plan.avatar.wait_for_join()
    await deps.session_starter(
        session=plan.session, agent=agent, room=ctx.room, room_options=plan.room_options
    )


def _activate(agent: PlatformAgent) -> None:
    """Start the room-bound collaborators that need a live participant.

    `UiChannel.start()` registers the `lkap.agent.action` RPC method and
    `FrameBuffer.start()` subscribes to track events; both are optional, so
    they are invoked only when the injected object provides them.
    """
    for collaborator in (agent.context.ui, agent.context.frames):
        start = getattr(collaborator, "start", None)
        if callable(start):
            start()


def _deactivate(agent: PlatformAgent) -> None:
    """Undo :func:`_activate`, tolerating collaborators that have no teardown."""
    for collaborator, method in ((agent.context.ui, "close"), (agent.context.frames, "stop")):
        stop = getattr(collaborator, method, None)
        if callable(stop):
            try:
                stop()
            except Exception:
                logger.debug("collaborator teardown failed", method=method, exc_info=True)


def _shutdown_callback(
    agent: PlatformAgent, observer: SessionObserver, idle: _IdleHangup
) -> Callable[[str], Awaitable[None]]:
    async def _on_shutdown(reason: str) -> None:
        idle.close()
        # F-11: in-flight workflows must not outlive the hangup.
        cancel_all = getattr(agent.context.background, "cancel_all", None)
        if callable(cancel_all):
            try:
                cancel_all()
            except Exception:
                logger.warning("could not cancel background jobs", exc_info=True)
        await agent.on_pack_session_end(reason)
        _deactivate(agent)
        await observer.shutdown(reason=reason, final_ui_state=agent.context.ui.state)

    return _on_shutdown


async def _fail_cleanly(
    ctx: JobContextLike, deps: Deps, *, reason: str, line: str = CONFIG_UNAVAILABLE_LINE
) -> None:
    """Connect, say the fixed line, and end the job.

    A second `ctx.connect()` is harmless: `JobContext.connect` returns early
    when already connected (livekit-agents 1.8.2 `job.py`).
    """
    if deps.fallback_speaker is not None:
        try:
            await ctx.connect()
            await deps.fallback_speaker(ctx, line)
        except Exception:
            logger.warning("failure path could not reach the room", exc_info=True)
    ctx.shutdown(reason=reason)


# -------------------------------------------------------------------- worker


#: The only dispatch name this worker may register under (DECISIONS-W2 §D-W2-11).
REQUIRED_AGENT_NAME = "lkap-agent"


def require_agent_name(environ: Mapping[str, str], settings: Settings) -> str:
    """Refuse to run when any *set* name source contradicts `lkap-agent`.

    The worker registers with an explicit `agent_name=REQUIRED_AGENT_NAME`, so an
    unset `LIVEKIT_AGENT_NAME` is fine (DECISIONS-W2 §D-W2-11). What is refused:
    a `LIVEKIT_AGENT_NAME_OVERRIDE` that differs (the SDK would register under
    it), a `LIVEKIT_AGENT_NAME` that differs (ignored by the SDK, but it means
    the process was launched from another agent's environment), or a settings
    value that differs.

    Args:
        environ: The process environment (`os.environ`).
        settings: The worker settings.

    Returns:
        The agent name, always `REQUIRED_AGENT_NAME`.

    Raises:
        RuntimeError: Naming the offending source when one disagrees.
    """
    override = environ.get("LIVEKIT_AGENT_NAME_OVERRIDE")
    if override and override != REQUIRED_AGENT_NAME:
        raise RuntimeError(
            f"LIVEKIT_AGENT_NAME_OVERRIDE={override!r} would register the worker under the wrong "
            f"name; this worker must register as {REQUIRED_AGENT_NAME!r}"
        )
    env_name = environ.get("LIVEKIT_AGENT_NAME")
    if env_name and env_name != REQUIRED_AGENT_NAME:
        raise RuntimeError(
            f"refusing to start: LIVEKIT_AGENT_NAME={env_name!r} contradicts the worker's name "
            f"{REQUIRED_AGENT_NAME!r}; this process looks like it was launched with another agent's env"
        )
    if settings.livekit_agent_name != REQUIRED_AGENT_NAME:
        raise RuntimeError(
            f"refusing to start: settings.livekit_agent_name={settings.livekit_agent_name!r} "
            f"must be {REQUIRED_AGENT_NAME!r}"
        )
    return REQUIRED_AGENT_NAME


def effective_agent_name(environ: Mapping[str, str]) -> str:
    """The name the SDK registers under, given the explicit `agent_name` argument.

    Mirrors `AgentServer.rtc_session`'s precedence: a set
    `LIVEKIT_AGENT_NAME_OVERRIDE` wins, otherwise the explicit argument.
    """
    return environ.get("LIVEKIT_AGENT_NAME_OVERRIDE") or REQUIRED_AGENT_NAME


async def only_lkap_jobs(req: JobRequest) -> None:
    """Accept only jobs dispatched to `lkap-agent`; reject everything else loudly.

    Automatic-dispatch jobs carry `agent_name == ""`; a job for another agent
    name can only reach this worker if it was mis-registered.
    """
    if req.agent_name != REQUIRED_AGENT_NAME:
        logger.error(
            "rejecting job dispatched to the wrong agent name",
            job_agent_name=req.agent_name,
            room=req.room.name,
        )
        await req.reject()
        return
    logger.info("accepting job", agent_name=req.agent_name, room=req.room.name)
    await req.accept()


def prewarm(proc: JobProcess) -> None:
    """Load the Silero VAD once per worker process, before any job arrives.

    Only logs the agent name: the hard guard runs once in `__main__`, and
    raising here would crash-loop every job process instead.
    """
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    from livekit.plugins import silero  # noqa: PLC0415  (heavy, prewarm-only import)

    proc.userdata[_VAD_USERDATA_KEY] = silero.VAD.load()
    logger.info(
        "worker process prewarmed",
        agent_name=effective_agent_name(os.environ),
        settings_agent_name=settings.livekit_agent_name,
    )


server = AgentServer(setup_fnc=prewarm)


@server.rtc_session(agent_name=REQUIRED_AGENT_NAME, on_request=only_lkap_jobs)
async def entrypoint(ctx: Any) -> None:
    """The one registered `rtc_session`; all logic lives in :func:`run_session`."""
    await run_session(ctx, Deps.from_env(ctx.proc.userdata))


if __name__ == "__main__":  # pragma: no cover - process entry
    from livekit.agents import cli  # noqa: PLC0415

    require_agent_name(os.environ, get_settings())
    cli.run_app(server)
