"""The LKAP worker: one `AgentServer`, one `rtc_session` (D1).

The decorated entrypoint is deliberately three lines long. Everything that can
go wrong lives in :func:`run_session`, which takes a `JobContextLike` and a
:class:`Deps` bundle, so the whole session lifecycle is exercisable offline with
fakes — no LiveKit connection, no api, no vendor keys.

**Agent name (D-W2-11, CONTRACTS-V2 §5).** One worker pool serves one
connection under that connection's agent name, handed to the process as
`LKAP_AGENT_NAME` (default `lkap-agent`). The name is read from the process
environment when this module is imported, because `rtc_session` resolves it at
decoration time, and the worker registers with an explicit
`rtc_session(agent_name=AGENT_NAME, on_request=only_lkap_jobs)`, so the SDK's
precedence (`LIVEKIT_AGENT_NAME_OVERRIDE` env -> explicit argument ->
`LIVEKIT_AGENT_NAME` env -> "") can never yield an unnamed worker that would
take automatic dispatch for every room in a shared project (including the
unrelated `other-project-agent`'s). `require_agent_name` refuses an empty name and
any *set* source that contradicts it, and `only_lkap_jobs` rejects any job
whose `JobRequest.agent_name` is not this worker's name. `livekit.toml`'s
`[agent] name` must equal `DEFAULT_AGENT_NAME` (a unit test parses it).

**Dispatch v2 (D-V2-5).** A dispatch that names a session is resolved with
`GET /internal/v1/sessions/{id}/resolved`; one that does not (a room the
platform did not create, e.g. inbound SIP) creates its session with
`POST /internal/v1/sessions/start`, using the job's room name and the
metadata's `agent_id`/`channel`.

Start-up order (docs/ARCHITECTURE.md §4, adjusted for two verified SDK constraints):

    resolve config -> build providers/session/tools -> ctx.connect()
      -> avatar.start() + avatar.wait_for_join() (when configured; a failure
         degrades the call to voice-only with an `error` event, asks #26)
      -> session.start(room=..., room_options=...)
      -> recording/start in the background (when `recording.enabled`)

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

**End of call.** `RoomOptions.close_on_disconnect` is off; `_ReconnectGrace`
owns it (REVIEW-FINAL F-33). A caller who leaves deliberately (client hang-up,
room deleted, call rejected — the SDK's own close-on-disconnect reasons) ends
the job at once, as before. Any other drop (network, signal loss) keeps the
job alive for `LKAP_RECONNECT_GRACE_S` (60 s) and cancels the shutdown if the
same identity rejoins. A session `close` still ends the job (D-W2-9e).
On the text channel (asks #33) any worker-owned shutdown request — `end_call`,
a flow end node, the grace, the idle hangup, a session `close` — starts the
shutdown callback `_TEXT_SHUTDOWN_SETTLE_S` later instead of after the SDK's
`AgentSession.aclose()`, so the pack's `on_session_end`, the background
cancel, the UI-channel close and the summary run while the session is still
being torn down; the SDK's own pass then awaits the same run.

**Phone calls (R-V2-20).** On `sip_in` / `sip_out` jobs `_assemble` builds a
`telephony.TelephonySession` (kept in `SessionContext.userdata["telephony"]`)
whose `send_dtmf` / `transfer_call` tools join the tool pool and whose
`flow_transfer` serves flow `transfer` nodes. `_start` waits on `sip_out` for
the callee to answer (`sip.callStatus == "active"`) right after
`ctx.connect()`; a dial that fails (the api deletes the room) or outlasts
`LKAP_SIP_ANSWER_TIMEOUT_S` ends the job with a `failed` summary and no spoken
line. `TelephonySession.start()` runs once the session is up and `aclose()` in
the shutdown callback before the summary (bounded to 5 s). A phone leg never
rejoins, so SIP jobs get no reconnect grace.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

from livekit import rtc
from livekit.agents import Agent, AgentServer, AgentSession, JobProcess, JobRequest, inference
from livekit.agents import llm as lk_llm
from livekit.agents.voice.events import UserStateChangedEvent
from livekit.agents.voice.room_io import TextInputOptions
from lkap_contracts import providers as provider_registry
from lkap_contracts.agent_config import ResolvedAgentConfig
from lkap_contracts.api_models import KbHit, SessionRecordingIn, SessionStartIn, SessionSummaryIn
from lkap_contracts.connections import TurnDetectorMode
from lkap_contracts.dispatch import DispatchMetadata
from lkap_contracts.flow import FlowState
from lkap_contracts.ui_protocol import ActivityEvent, ChecklistItem, Tone, UiPatchOp, UiState
from packs.base import FrameSnapshot, Pack, StructuredLLM

from lkap_agent.config_client import (
    ApiKbClient,
    ConfigClient,
    ConfigClientProtocol,
    ConfigUnavailableError,
    RecordingUnavailableError,
    SessionEndedError,
    SessionNotFoundError,
)
from lkap_agent.flow import FlowServices, build_flow_agent, is_flow, prepare_flow_resolved
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
from lkap_agent.qa import build_judge, score_session
from lkap_agent.registration import FleetClient, WorkerRegistration
from lkap_agent.session_builder import (
    SessionBuilder,
    SessionPlan,
    factory_view,
    is_text_channel,
    prepare_resolved,
)
from lkap_agent.settings import DEFAULT_AGENT_NAME, Settings, get_settings
from lkap_agent.telephony import (
    TelephonySession,
    apply_call_variables,
    is_sip_channel,
    seed_variables,
    session_for,
    wait_for_answer,
)
from lkap_agent.text_mode import handle_agent_action as handle_text_mode_action
from lkap_agent.workflow_llm import PromptJsonStructuredLLM

__all__ = [
    "AGENT_NAME",
    "DEFAULT_AGENT_NAME",
    "Deps",
    "JobContextLike",
    "configured_agent_name",
    "default_turn_detector",
    "effective_agent_name",
    "install_worker_registration",
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

#: Spoken when the config resolved but `session.start` failed (F-01). An avatar that
#: fails to start no longer ends the call: it degrades to voice-only (asks #26).
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

#: Disconnect reasons that mean the caller left on purpose: the job ends at
#: once, with no reconnect grace. The same set the SDK closes a session on
#: (`room_io.DEFAULT_CLOSE_ON_DISCONNECT_REASONS` in livekit-agents 1.8.2).
_DELIBERATE_LEAVE_REASONS: frozenset[int] = frozenset(
    {
        rtc.DisconnectReason.CLIENT_INITIATED,
        rtc.DisconnectReason.ROOM_DELETED,
        rtc.DisconnectReason.USER_REJECTED,
    }
)

#: How long the shutdown callback waits for LiveKit's `list_egress` answer.
_EGRESS_POLL_TIMEOUT_S = 5.0

#: On the text channel the summary starts this long after a shutdown request
#: (asks #33) instead of after the SDK's teardown (up to ~60 s): enough for the
#: SDK to deliver the triggering tool's `tool_call_ended` and a last reply.
_TEXT_SHUTDOWN_SETTLE_S = 1.5

#: How long the shutdown callback waits for the telephony teardown (R-V2-20: the
#: summary must still land within ~10 s of a hang-up).
_TELEPHONY_CLOSE_TIMEOUT_S = 5.0

#: `SessionContext.userdata` key of a phone call's `TelephonySession` (R-V2-20).
TELEPHONY_USERDATA_KEY = "telephony"


class CalleeNotAnsweredError(RuntimeError):
    """An outbound call's callee never answered; the job ends without speaking."""


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

    # V2 block methods (CONTRACTS-V2 §4.4; asks #66): no panel, so nothing to write.
    async def set_block(self, block_id: str, state: dict[str, Any]) -> None:
        """Drop the block state."""

    async def patch_block(self, block_id: str, ops: list[UiPatchOp]) -> None:
        """Drop the block patch."""

    async def request_form(
        self,
        block_id: str,
        schema: dict[str, Any],
        prefill: dict[str, Any] | None = None,
        timeout_s: float = 120,
    ) -> dict[str, Any] | None:
        """Nobody can fill a form in: answer as a timed-out request."""
        return None

    async def cite(self, block_id: str, hits: list[KbHit]) -> None:
        """Drop the citations."""

    async def request_block(
        self,
        block_id: str,
        *,
        timeout_s: float,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Nobody can answer a block: answer as a timed-out request."""
        return None

    @property
    def pending_requests(self) -> Mapping[str, Literal["request", "form"]]:
        """Nothing is ever pending."""
        return {}

    def cancel_pending(
        self, reason: str, *, methods: Iterable[Literal["request", "form"]] | None = None
    ) -> list[str]:
        """Nothing to release."""
        return []


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


def default_turn_detector(mode: TurnDetectorMode) -> Any:
    """The turn detector a cascaded / half-cascade session gets when its slot is empty.

    A connection without hosted Inference (`turn_detector_mode == "local"`)
    gets the local `v1-mini` model explicitly (ARCHITECTURE-V2 D-V2-4), so the
    SDK never tries the hosted `v1` first; otherwise the SDK chooses (hosted
    `v1` on LiveKit Cloud and in dev mode, `v1-mini` elsewhere).
    """
    if mode == "local":
        return inference.TurnDetector(version="v1-mini")
    return inference.TurnDetector()


#: Snapshot of an Egress as LiveKit reports it: `(status, duration_s)`.
EgressSnapshot = tuple[str, float | None]

_EGRESS_STATUS: dict[int, str] = {
    0: "active",  # EGRESS_STARTING
    1: "active",  # EGRESS_ACTIVE
    2: "active",  # EGRESS_ENDING
    3: "ready",  # EGRESS_COMPLETE
    4: "failed",  # EGRESS_FAILED
    5: "failed",  # EGRESS_ABORTED
    6: "ready",  # EGRESS_LIMIT_REACHED: the file up to the limit is complete
}


async def livekit_egress_status(settings: Settings, egress_id: str) -> EgressSnapshot | None:
    """Ask LiveKit (`list_egress`) for one Egress's state with the worker's own credentials.

    The api normally learns an Egress finished from the `egress_ended` webhook;
    without a public webhook URL it never does, so the worker reports what it
    sees at shutdown (ARCHITECTURE-V2 D-V2-16).

    Returns:
        `(status, duration_s)` with the status mapped onto `recording_status`
        values, or `None` when LiveKit does not know the id.
    """
    from livekit import api as lk_api  # noqa: PLC0415  (shutdown-path only)

    client = lk_api.LiveKitAPI(settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret)
    try:
        response = await asyncio.wait_for(
            client.egress.list_egress(lk_api.ListEgressRequest(egress_id=egress_id)),
            _EGRESS_POLL_TIMEOUT_S,
        )
    finally:
        await client.aclose()
    for info in response.items:
        if info.egress_id != egress_id:
            continue
        durations = [f.duration for f in info.file_results if f.duration]
        duration_s = max(durations) / 1e9 if durations else None
        return _EGRESS_STATUS.get(int(info.status), "active"), duration_s
    return None


@dataclass(slots=True)
class Deps:
    """Everything `run_session` needs, injected so tests can replace any of it."""

    settings: Settings
    config_client: ConfigClientProtocol
    pack_loader: PackLoader
    provider_factory: ProviderFactory = field(default_factory=ProviderFactory)
    session_builder: SessionBuilder = field(default_factory=SessionBuilder)
    vad: Any | None = None
    #: Builds the default turn detector per session from the connection's
    #: `turn_detector_mode`; not called in realtime mode, on the text channel, or
    #: when the `turn_detection` slot is filled.
    turn_detector_factory: Callable[[TurnDetectorMode], Any | None] = default_turn_detector
    ui_channel_factory: Callable[..., Any] = lambda **kw: NoopUiChannel(kw.get("session_id", ""))
    frame_buffer_factory: Callable[..., Any] = lambda **kw: NoopFrameBuffer()
    background_runner_factory: Callable[..., Any] = lambda **kw: NoopBackgroundRunner()
    builtin_tools_builder: Callable[..., list[Any]] = _no_tools
    declarative_tools_builder: Callable[..., list[Any]] = _no_tools
    mcp_servers_builder: Callable[..., list[Any]] = _no_tools
    fallback_speaker: Callable[[JobContextLike, str], Awaitable[None]] | None = None
    #: Seam for `session.start`, so a unit test can run a session without a room.
    session_starter: Callable[..., Awaitable[None]] = field(default=lambda **kw: _start_session(**kw))
    #: Clock seam for the idle hangup timer (F-02) and the reconnect grace (F-33).
    sleep: Callable[[float], Awaitable[None]] = _sleep
    #: Reads an Egress's state from LiveKit at shutdown; `None` disables the poll.
    egress_status: Callable[[str], Awaitable[EgressSnapshot | None]] | None = None

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
            egress_status=lambda egress_id: livekit_egress_status(settings, egress_id),
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
    user_agent = deps.settings.http_tool_user_agent

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
                build_builtin_tools(
                    ctx,
                    disabled,
                    http_enabled,
                    platform_allowed_hosts=allowed_hosts,
                    http_user_agent=user_agent,
                )
            )

        deps.builtin_tools_builder = _make_builtin
    except ImportError:
        logger.warning("lkap_agent.tools.builtin not available: no built-in tools registered")

    try:
        from lkap_agent.tools.declarative import build_http_tools, build_mcp_toolsets  # noqa: PLC0415

        def _make_http(defs: list[Any]) -> list[Any]:
            return list(build_http_tools(defs, platform_allowed_hosts=allowed_hosts, user_agent=user_agent))

        deps.declarative_tools_builder = _make_http
        deps.mcp_servers_builder = lambda defs, **kwargs: list(build_mcp_toolsets(defs, **kwargs))
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
    job_id = str(getattr(ctx.job, "id", ""))
    bind_session_context(session_id=meta.session_id or "", agent_id=meta.agent_id, job_id=job_id)
    logger.info(
        "job accepted",
        config_version=meta.config_version,
        channel=meta.channel,
        has_session=bool(meta.session_id),
    )
    _warn_on_connection_mismatch(meta, deps.settings)

    try:
        resolved = await _resolve_or_start(ctx, deps, meta)
    except ConfigUnavailableError as exc:
        logger.error("could not resolve the agent config", error=str(exc))
        if meta.session_id and not isinstance(exc, SessionNotFoundError | SessionEndedError):
            # F-09: the api may already have flipped the row to `active`.
            await _report_resolve_failure(deps, meta.session_id, exc)
        await _fail_cleanly(ctx, deps, reason="configuration unavailable")
        return
    if not meta.session_id:
        bind_session_context(session_id=resolved.session_id, agent_id=resolved.agent_id, job_id=job_id)

    observer = SessionObserver(session_id=resolved.session_id, client=deps.config_client)
    eager: _EagerShutdownContext | None = None
    if is_text_channel(resolved):
        # asks #33: post a typed chat's summary as soon as it ends, not after the SDK's teardown.
        eager = _EagerShutdownContext(ctx)
        ctx = eager
    try:
        resolved = prepare_flow_resolved(prepare_resolved(resolved))
        # R-V2-22: a prompt agent hears an outbound call's variables (flows seed `FlowState`).
        resolved = apply_call_variables(resolved)
        plan, agent = _assemble(ctx, deps, resolved, record_event=observer.record)
    except Exception as exc:
        logger.error("could not build the session", error=str(exc), exc_info=True)
        await observer.shutdown(reason="build failed", status="failed", error=str(exc))
        await _fail_cleanly(ctx, deps, reason="configuration invalid")
        return

    observer.attach(plan.session)
    telephony = _telephony_of(agent)
    idle = _IdleHangup(ctx=ctx, session=plan.session, delay_s=deps.settings.idle_hangup_s, sleep=deps.sleep)
    grace = _ReconnectGrace(
        ctx=ctx,
        identity=resolved.participant_identity,
        # R-V2-20: a phone leg never rejoins under the same identity; end the job at once.
        delay_s=0 if is_sip_channel(resolved.channel) else deps.settings.reconnect_grace_s,
        sleep=deps.sleep,
        record_event=observer.record,
    )
    recording = _Recording(deps=deps, session_id=resolved.session_id, record_event=observer.record)
    plan.session.on("user_state_changed", idle.on_user_state_changed)
    plan.session.on("function_tools_executed", agent.on_function_tools_executed)
    # F-03: drives the pack's `on_agent_turn_completed` hook.
    plan.session.on("conversation_item_added", agent.on_conversation_item)
    # V4-12 (D-V4-38): background and slow tools feed the activity block.
    plan.session.on("tool_execution_updated", agent.on_tool_execution)
    plan.session.on("error", _vision_degrade_handler(agent, observer))
    # D-W2-9e: a closed AgentSession (end_call, an error) must end the job too;
    # the shutdown callback posts the summary.
    plan.session.on("close", _job_shutdown_handler(ctx))
    grace.attach(ctx.room)
    on_shutdown = _OnceShutdown(
        _shutdown_callback(
            agent,
            observer,
            idle,
            grace,
            recording,
            quality=lambda: _score_quality(deps, resolved, observer),
            telephony=telephony,
        ),
        sleep=deps.sleep,
        settle_s=_TEXT_SHUTDOWN_SETTLE_S,
    )
    if eager is not None:
        eager.once = on_shutdown

    # The SDK inspects `callback.__code__` (livekit-agents 1.8.2 job.py), so it
    # must be a plain function, not a callable object like `_OnceShutdown`.
    async def _run_shutdown(reason: str) -> None:
        await on_shutdown(reason)

    ctx.add_shutdown_callback(_run_shutdown)

    try:
        await _start(ctx, plan, agent, deps, resolved)
    except Exception as exc:
        grace.close()
        # Nobody is listening on an unanswered phone: no fixed line (R-V2-20).
        speak = not isinstance(exc, CalleeNotAnsweredError)
        await _abort_start(ctx, deps, plan, agent, observer, idle, exc, speak=speak)
        return
    if telephony is not None:
        telephony.start()
    # V4-12: `voice.thinking_sound` during blocking tool waits (never on the text channel);
    # the player is closed in the shutdown path.
    from lkap_agent.session_builder import start_thinking_sound  # noqa: PLC0415

    stop_thinking_sound = await start_thinking_sound(plan, ctx.room)
    if stop_thinking_sound is not None:
        ctx.add_shutdown_callback(stop_thinking_sound)
    observer.record(
        "session_started",
        {
            "pipeline_mode": resolved.config.pipeline.mode,
            "channel": resolved.channel,
            "connection_id": resolved.connection.connection_id,
        },
    )
    if resolved.recording.enabled and not plan.text_only:
        recording.start()
    logger.info(
        "session started",
        pack_id=resolved.pack_id,
        ui_panel_id=resolved.ui_panel_id,
        mode=resolved.config.pipeline.mode,
        channel=resolved.channel,
    )


def _warn_on_connection_mismatch(meta: DispatchMetadata, settings: Settings) -> None:
    """Log a dispatch minted for another connection than the one this worker serves."""
    if meta.connection_id and settings.connection_id and meta.connection_id != settings.connection_id:
        logger.warning(
            "job dispatched for another connection than this worker's",
            dispatch_connection_id=meta.connection_id,
            worker_connection_id=settings.connection_id,
        )


def _job_room_name(ctx: JobContextLike) -> str:
    """The job's room name, available before `ctx.connect()` (from the job proto)."""
    job_room = getattr(ctx.job, "room", None)
    name = getattr(job_room, "name", "") or ""
    if not name:
        name = getattr(ctx.room, "name", "") or ""
    return str(name)


async def _resolve_or_start(ctx: JobContextLike, deps: Deps, meta: DispatchMetadata) -> ResolvedAgentConfig:
    """Resolve the dispatched session, or create it when the dispatch named none (D-V2-5).

    Raises:
        ConfigUnavailableError: If either call fails (subclasses for 404 / 409).
        ValueError: If a session-less dispatch carries no room name.
    """
    if meta.session_id:
        return await deps.config_client.resolve(meta.session_id)

    room_name = _job_room_name(ctx)
    if not room_name:
        raise ValueError("a dispatch without session_id needs the job's room name to start a session")
    logger.info("dispatch carries no session; creating one", room=room_name, channel=meta.channel)
    resolved = await deps.config_client.start_session(
        SessionStartIn(
            agent_id=meta.agent_id,
            room_name=room_name,
            channel=meta.channel,
            participant_identity=meta.participant_identity,
            caller=None,
            dispatch_metadata=meta.model_dump(mode="json"),
        )
    )
    if not meta.participant_identity:
        # The api names a placeholder identity for the row; nobody joins under it,
        # so link to the first caller instead (the SDK's default behaviour).
        resolved = resolved.model_copy(update={"participant_identity": ""})
    return resolved


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
    *,
    speak: bool = True,
) -> None:
    """Tear down a session whose `session.start` failed (F-01; an avatar failure degrades instead).

    `speak=False` (an outbound call nobody answered, R-V2-20) ends the job
    without the fixed line.

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
    if not speak:
        ctx.shutdown(reason=f"start failed: {exc}"[:200])
        return
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


class _ReconnectGrace:
    """Ends the job when the caller leaves, after a grace period for drops (F-33).

    `RoomOptions.close_on_disconnect` is off, so this watcher is what ends a
    job whose caller is gone. The caller is `identity` (the participant the api
    minted the token for) or, when that is empty (a worker-created session),
    the first participant that is neither an agent nor published on an agent's
    behalf (an avatar).

    A deliberate leave (:data:`_DELIBERATE_LEAVE_REASONS`) shuts the job down at
    once, which keeps the summary within seconds of a hang-up. Any other
    disconnect starts a `delay_s` timer that the same identity rejoining
    cancels; RoomIO re-links a rejoining participant by itself.
    """

    def __init__(
        self,
        *,
        ctx: JobContextLike,
        identity: str,
        delay_s: float | None,
        sleep: Callable[[float], Awaitable[None]],
        record_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._ctx = ctx
        self._identity = identity or None
        self._delay_s = delay_s
        self._sleep = sleep
        self._record_event = record_event
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def pending(self) -> bool:
        """Whether a reconnect grace timer is running."""
        return self._task is not None and not self._task.done()

    def attach(self, room: rtc.Room) -> None:
        """Subscribe to the room's participant events."""
        room.on("participant_disconnected", self.on_participant_disconnected)
        room.on("participant_connected", self.on_participant_connected)

    @staticmethod
    def _is_caller(participant: Any) -> bool:
        if getattr(participant, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            return False
        attributes = getattr(participant, "attributes", None) or {}
        return not attributes.get("lk.publish_on_behalf")

    def _matches(self, participant: Any) -> bool:
        if self._identity is not None:
            return bool(participant.identity == self._identity)
        if not self._is_caller(participant):
            return False
        self._identity = participant.identity
        return True

    def on_participant_disconnected(self, participant: Any) -> None:
        """Synchronous room handler: shut down now, or start the grace timer."""
        if self._closed or not self._matches(participant):
            return
        reason = getattr(participant, "disconnect_reason", None)
        reason_name = (
            rtc.DisconnectReason.Name(cast(rtc.DisconnectReason.ValueType, reason))
            if isinstance(reason, int) and reason in rtc.DisconnectReason.values()
            else "UNKNOWN_REASON"
        )
        if reason in _DELIBERATE_LEAVE_REASONS or not self._delay_s:
            logger.info("caller left, ending the job", identity=participant.identity, reason=reason_name)
            self._ctx.shutdown(reason=f"participant left: {reason_name}")
            return
        if self.pending:
            return
        logger.info(
            "caller dropped, waiting for a reconnect",
            identity=participant.identity,
            reason=reason_name,
            grace_s=self._delay_s,
        )
        if self._record_event is not None:
            self._record_event(
                "info", {"message": "caller disconnected; waiting to reconnect", "reason": reason_name}
            )
        self._task = asyncio.create_task(self._fire(self._delay_s))

    def on_participant_connected(self, participant: Any) -> None:
        """Synchronous room handler: a rejoining caller cancels the pending shutdown."""
        if self._identity is None or participant.identity != self._identity or not self.pending:
            return
        logger.info("caller reconnected", identity=participant.identity)
        if self._record_event is not None:
            self._record_event("info", {"message": "caller reconnected"})
        self._cancel()

    async def _fire(self, delay_s: float) -> None:
        await self._sleep(delay_s)
        if self._closed:
            return
        logger.info("caller did not reconnect, ending the job", grace_s=delay_s)
        self._ctx.shutdown(reason="participant did not reconnect")

    def _cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    def close(self) -> None:
        """Cancel any pending timer and ignore later events (session shutdown)."""
        self._closed = True
        self._cancel()


class _Recording:
    """Starts the session's Egress through the api and reports its end (D-V2-16).

    `start()` runs in the background once the session is up — the api's Egress
    call must not delay the greeting. The `recording` session event is recorded
    once the api confirms an egress id (or with `status="failed"` if it cannot
    start one). At shutdown, when an Egress was started, the worker reads its
    state from LiveKit (`list_egress`) and posts it to the api, which covers
    deployments where LiveKit's `egress_ended` webhook cannot reach the api.
    """

    def __init__(self, *, deps: Deps, session_id: str, record_event: Callable[[str, dict[str, Any]], None]):
        self._deps = deps
        self._session_id = session_id
        self._record_event = record_event
        self._task: asyncio.Task[None] | None = None
        self.egress_id: str | None = None

    def start(self) -> None:
        """Ask the api for the recording without blocking the caller."""
        self._task = asyncio.create_task(self._start())

    async def _start(self) -> None:
        try:
            egress_id = await self._deps.config_client.start_recording(self._session_id)
        except RecordingUnavailableError as exc:
            logger.warning("could not start the session recording", error=str(exc))
            self._record_event("recording", {"status": "failed", "error": str(exc)})
            # docs/v2/_asks.md V2-20-3: the timeline event above is easy to
            # miss (it's buried in the transcript view); persist the failure
            # on the session row itself too, via the same fallback route
            # `finalize()` uses for a *started* Egress's shutdown report, so
            # the console's Recording tab and the sessions list both show it.
            try:
                await self._deps.config_client.post_recording(
                    self._session_id,
                    SessionRecordingIn(egress_id="", status="failed", error=str(exc)[:500]),
                )
            except Exception:
                logger.warning("could not post the recording failure status", exc_info=True)
            return
        self.egress_id = egress_id
        logger.info("session recording started", egress_id=egress_id)
        self._record_event("recording", {"status": "active", "egress_id": egress_id})

    async def finalize(self) -> None:
        """Report the Egress state LiveKit sees now; best effort, never raises."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        if self.egress_id is None or self._deps.egress_status is None:
            return
        try:
            snapshot = await self._deps.egress_status(self.egress_id)
        except Exception:
            logger.warning("could not read the egress status at shutdown", exc_info=True)
            return
        if snapshot is None:
            return
        status, duration_s = snapshot
        try:
            await self._deps.config_client.post_recording(
                self._session_id,
                SessionRecordingIn(
                    egress_id=self.egress_id,
                    status=cast(Any, status),
                    duration_s=duration_s,
                ),
            )
        except Exception:
            logger.warning("could not post the recording status", exc_info=True)


def _vision_degrade_handler(agent: PlatformAgent, observer: SessionObserver) -> Callable[[Any], None]:
    """Build the synchronous `error` handler for D-W2-8 R5 (vision auto-degrade)."""

    def _on_error(ev: Any) -> None:
        message = agent.on_session_error(ev)
        if message is not None:
            observer.record("error", {"message": message})

    return _on_error


class _OnceShutdown:
    """Runs the job's shutdown callback exactly once, possibly before the SDK asks (asks #33).

    The SDK runs registered shutdown callbacks only after `AgentSession.aclose()`
    (bounded at 60 s), the session-report upload and `room.disconnect()`. On the
    text channel nothing else happens at the end of a call, so waiting for that
    only delays the summary: V4-06 saw it posted 51 s after `end_call`.
    :meth:`start` begins the callback at once; the SDK's later call awaits the
    same task instead of running it twice.
    """

    def __init__(
        self,
        callback: Callable[[str], Awaitable[None]],
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        settle_s: float = 0.0,
    ) -> None:
        self._callback = callback
        self._sleep = sleep
        self._settle_s = settle_s
        self._task: asyncio.Future[None] | None = None

    def start(self, reason: str, *, early: bool = False) -> asyncio.Future[None]:
        """Begin the shutdown callback (idempotent).

        `early=True` (a shutdown *request*, not the SDK's own pass) first waits
        `settle_s`, so what the SDK emits right after the triggering call — the
        `tool_call_ended` of `end_call`, a last assistant item — still reaches
        the observer before it closes.
        """
        if self._task is None:
            self._task = asyncio.ensure_future(self._run(reason, settle=early))
        return self._task

    async def _run(self, reason: str, *, settle: bool) -> None:
        if settle and self._settle_s > 0:
            await self._sleep(self._settle_s)
        await self._callback(reason)

    async def __call__(self, reason: str) -> None:
        await asyncio.shield(self.start(reason))


class _EagerShutdownContext:
    """A `JobContextLike` whose `shutdown()` starts the summary before the SDK tears down.

    Used on the text channel only (asks #33). Every shutdown trigger the worker
    owns — `end_call` (through `SessionContext.request_shutdown`), the flow's
    end node, the reconnect grace, the idle hangup and the session `close`
    handler — calls `shutdown()` on this object, so the summary goes out while
    the room is still connected; everything else is delegated.
    """

    def __init__(self, inner: JobContextLike) -> None:
        self._inner = inner
        self.once: _OnceShutdown | None = None

    @property
    def job(self) -> Any:
        return self._inner.job

    @property
    def proc(self) -> Any:
        return self._inner.proc

    @property
    def room(self) -> rtc.Room:
        return self._inner.room

    async def connect(self) -> None:
        await self._inner.connect()

    def add_shutdown_callback(self, callback: Any) -> None:
        self._inner.add_shutdown_callback(callback)

    def shutdown(self, reason: str = "user requested") -> None:
        if self.once is not None:
            self.once.start(reason, early=True)
        self._inner.shutdown(reason=reason)


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

    `resolved` is expected to have been through `session_builder.prepare_resolved`
    (text-channel slots dropped, avatar options and connection flags applied).
    """
    # `qa_llm` (R-V2-6) is built separately by `qa.build_judge` at session end,
    # never through `build_all`: `BuiltProviders` has no `qa_llm` field, so
    # passing it through here would raise a `TypeError` the moment `qa.enabled`
    # attaches the slot to `resolved.resolved`.
    session_view = factory_view(resolved)
    build_all_input = session_view.model_copy(
        update={"resolved": {k: v for k, v in session_view.resolved.items() if k != "qa_llm"}}
    )
    providers: BuiltProviders = deps.provider_factory.build_all(build_all_input)
    mode = resolved.config.pipeline.mode
    client_side_turns = mode != "realtime" and resolved.channel != "text"
    default_detector = (
        deps.turn_detector_factory(resolved.connection.capabilities.turn_detector_mode)
        if client_side_turns and providers.turn_detection is None
        else None
    )
    plan = deps.session_builder.build(
        resolved,
        providers,
        vad=deps.vad if client_side_turns else None,
        turn_detector=default_detector,
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
        # "" (a worker-created session with no dispatched identity) means "not
        # known yet": the channel and the frame buffer then fall back to the
        # first caller, as RoomIO does.
        ui_identity=resolved.participant_identity or None,
    )
    if is_text_channel(resolved):
        # V2-18: `rewind`/`inject_user_text` need the built `AgentSession` (not yet
        # in `cell`), so this closes over `plan` directly rather than routing
        # through `pack.on_ui_action` like `_on_ui_action` above. Kept separate
        # from the flow hooks (`is_flow`/`prepare_flow_resolved`) below.
        async def _on_text_action(action: str, payload: dict[str, Any]) -> dict[str, Any]:
            return await handle_text_mode_action(plan.session, action, payload)

        bind_text = getattr(ui, "bind", None)
        if callable(bind_text):
            bind_text(on_text_action=_on_text_action)
    frames = deps.frame_buffer_factory(
        room=ctx.room, participant_identity=resolved.participant_identity or None
    )
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
        channel=resolved.channel,
        request_shutdown=lambda reason: ctx.shutdown(reason=reason),
        llm_capabilities=plan.llm_capabilities,
    )
    cell.append(session_ctx)

    # R-V2-20: phone calls get their telephony wiring (none on web/test/text jobs).
    telephony = session_for(
        room=ctx.room,
        resolved=resolved,
        pack_ctx=session_ctx,
        pack=pack,
        record_event=record_event or _noop_record_event,
        api=deps.config_client,
    )
    if telephony is not None:
        session_ctx.userdata[TELEPHONY_USERDATA_KEY] = telephony

    tools: list[lk_llm.Tool | lk_llm.Toolset] = [
        *deps.builtin_tools_builder(
            session_ctx,
            resolved.config.tools.builtin_disabled,
            resolved.config.tools.http_request_enabled,
        ),
        # V5-47: `provider` (a connected app's action) is built by the same declarative builder.
        *deps.declarative_tools_builder([t for t in resolved.tools if t.kind in ("http", "provider")]),
        *pack.tools(session_ctx),
        *(
            telephony.tools(config=resolved.config, shutdown=lambda reason: ctx.shutdown(reason=reason))
            if telephony is not None
            else []
        ),
    ]
    # R-V4-71: an HTTP tool's `silent_reply` joins the agent's silent set (realtime always,
    # cascaded from livekit-agents 1.8.3); MCP tools have no such flag.
    silent_http = frozenset(t.name for t in resolved.tools if t.kind == "http" and t.silent_reply)
    emit = record_event or _noop_record_event

    def _on_mcp_skipped(definition: Any, reason: str) -> None:
        # CONTRACTS §7 has no `warning` event type; `error {message}` is the one the
        # console surfaces. The session still starts, without this server's tools.
        emit(
            "error",
            {"message": f"MCP server '{definition.name}' skipped: {reason}", "mcp_server": definition.name},
        )

    mcp_toolsets = deps.mcp_servers_builder(
        [t for t in resolved.tools if t.kind == "mcp"], on_skipped=_on_mcp_skipped
    )

    agent: PlatformAgent
    if is_flow(resolved):
        # V2-15: a flow agent starts at its first node; nodes pick their tools from this pool.
        agent = build_flow_agent(
            FlowServices(
                resolved=resolved,
                ctx=session_ctx,
                pack=pack,
                tool_pool=tools,
                provider_factory=deps.provider_factory,
                has_tts=plan.has_tts,
                mcp_definitions=[t for t in resolved.tools if t.kind == "mcp"],
                mcp_servers_builder=deps.mcp_servers_builder,
                vision_max_frame_age_s=deps.settings.vision_max_frame_age_s,
                record_event=record_event,
                shutdown=lambda reason: ctx.shutdown(reason=reason),
                transfer=telephony.flow_transfer if telephony is not None else None,
                initial_variables=seed_variables(resolved.variables),
                silent_reply_tools=silent_http,
            )
        )
    else:
        agent = PlatformAgent(
            ctx=session_ctx,
            pack=pack,
            tools=tools,
            mcp_toolsets=mcp_toolsets or None,
            has_tts=plan.has_tts,
            vision_max_frame_age_s=deps.settings.vision_max_frame_age_s,
            record_event=record_event,
            silent_reply_tools=silent_http,
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


async def _start(
    ctx: JobContextLike, plan: SessionPlan, agent: PlatformAgent, deps: Deps, resolved: ResolvedAgentConfig
) -> None:
    """Connect, wait for an outbound callee, start the avatar if configured, then the session.

    See the module docstring for why `ctx.connect()` leads: both the explicit
    participant link and the avatar session need a connected room. On
    `sip_out` the callee's leg joins while it still rings and RoomIO would link
    it at once, so the agent waits for `sip.callStatus == "active"` first
    (R-V2-20); the api deleting the room (a failed dial) ends the wait early.

    Raises:
        CalleeNotAnsweredError: The outbound callee never answered.
    """
    await ctx.connect()
    if resolved.channel == "sip_out":
        answered = await wait_for_answer(ctx.room, timeout_s=deps.settings.sip_answer_timeout_s)
        if answered is None:
            raise CalleeNotAnsweredError("the callee never answered")
    _activate(agent)
    if plan.avatar is not None:
        await _start_avatar_or_degrade(plan, ctx.room, agent, resolved)
    await deps.session_starter(
        session=plan.session, agent=agent, room=ctx.room, room_options=plan.room_options
    )


async def _start_avatar_or_degrade(
    plan: SessionPlan, room: rtc.Room, agent: PlatformAgent, resolved: ResolvedAgentConfig
) -> None:
    """Start the avatar; on any failure, continue voice-only with a session warning (asks #26).

    A vendor outage or a bad avatar key used to fail the whole call (F-01). Now
    the call goes on without video: the avatar is closed best-effort, and the
    session's audio output is reset because a plugin that got as far as
    `replace_audio_tail(DataStreamAudioOutput(...))` before failing (Simli, when
    its avatar never joins) would otherwise make `AgentSession.start` skip
    RoomIO's own audio track, leaving the caller in silence. Simli's `start()`
    logs a token failure and returns; the failure then surfaces as
    `wait_for_join()`'s 30 s `TimeoutError`, so both calls are guarded.
    """
    avatar: Any = plan.avatar
    try:
        await avatar.start(plan.session, room=room)
        await avatar.wait_for_join()
    except Exception as exc:
        avatar_ref = resolved.config.pipeline.avatar
        provider_id = avatar_ref.provider_id if avatar_ref is not None else ""
        logger.warning(
            "avatar failed to start; continuing voice-only",
            avatar_provider=provider_id,
            error=str(exc) or type(exc).__name__,
        )
        aclose = getattr(avatar, "aclose", None)
        if callable(aclose):
            with contextlib.suppress(Exception):
                await aclose()
        with contextlib.suppress(Exception):
            plan.session.output.audio = None
        plan.avatar = None
        # CONTRACTS §7 has no `warning` type; `error {message}` is the one the
        # console surfaces (the `_on_mcp_skipped` precedent in `_assemble`).
        agent.context.record_event(
            "error",
            {
                "message": "The avatar could not start; the call continues voice-only.",
                "avatar_provider": provider_id,
                "severity": "warning",
            },
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


def _telephony_of(agent: PlatformAgent) -> TelephonySession | None:
    """The job's `TelephonySession`, when it is a phone call (built by `_assemble`)."""
    telephony = agent.context.userdata.get(TELEPHONY_USERDATA_KEY)
    return telephony if isinstance(telephony, TelephonySession) else None


async def _close_telephony(telephony: TelephonySession, reason: str) -> None:
    """Report the leg's end (best effort, bounded so the summary stays within target)."""
    try:
        await asyncio.wait_for(telephony.aclose(reason=reason), _TELEPHONY_CLOSE_TIMEOUT_S)
    except Exception:
        logger.warning("telephony teardown failed", exc_info=True)


def _shutdown_callback(
    agent: PlatformAgent,
    observer: SessionObserver,
    idle: _IdleHangup,
    grace: _ReconnectGrace,
    recording: _Recording,
    *,
    quality: Callable[[], Awaitable[None]] | None = None,
    telephony: TelephonySession | None = None,
) -> Callable[[str], Awaitable[None]]:
    async def _on_shutdown(reason: str) -> None:
        idle.close()
        grace.close()
        # F-11: in-flight workflows must not outlive the hangup.
        cancel_all = getattr(agent.context.background, "cancel_all", None)
        if callable(cancel_all):
            try:
                cancel_all()
            except Exception:
                logger.warning("could not cancel background jobs", exc_info=True)
        await agent.on_pack_session_end(reason)
        if telephony is not None:
            # R-V2-20: the leg's `completed`/`failed` report goes out before the summary.
            await _close_telephony(telephony, reason)
        _deactivate(agent)
        # R-V2-8: `on_pack_session_end` (above) settled a flow's extractions; its final
        # `FlowState` rides in the summary. Prompt agents have no "flow" userdata.
        flow_state = agent.context.userdata.get("flow")
        await observer.shutdown(
            reason=reason,
            final_ui_state=agent.context.ui.state,
            flow=flow_state if isinstance(flow_state, FlowState) else None,
        )
        # Everything below runs after the summary is posted, so neither the
        # Egress poll (up to 5 s) nor the QA judge (R-V2-5, up to 30 s) delays
        # the ≤10 s summary target.
        await recording.finalize()
        if quality is not None:
            try:
                await quality()
            except Exception:
                logger.warning("qa scoring failed", exc_info=True)

    return _on_shutdown


async def _score_quality(deps: Deps, resolved: ResolvedAgentConfig, observer: SessionObserver) -> None:
    """Judge the finished session and `PUT` the verdict (R-V2-5/R-V2-6); best effort.

    The judge is built from the api-resolved `qa_llm` slot
    (`qa.build_judge`) — the api already walked `qa.model -> workflow_llm ->
    llm -> Inference default` and attached secrets at
    `GET .../resolved` / `POST .../sessions/start` time (R-V2-6); the worker
    no longer walks that chain itself (`_qa_judge` used to live here).
    """
    qa = resolved.config.qa
    judge: StructuredLLM | None = None
    label: str | None = None
    error: str | None = None
    if qa.enabled:
        judge, label, error = build_judge(deps.provider_factory, resolved)
    verdict = await score_session(
        qa=qa, transcript=observer.transcript, judge=judge, model_label=label, judge_error=error
    )
    await deps.config_client.put_qa(resolved.session_id, verdict)
    logger.info(
        "qa verdict sent (best effort)", status=verdict.status, score=verdict.score, model=verdict.model
    )


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


def configured_agent_name(environ: Mapping[str, str]) -> str:
    """`LKAP_AGENT_NAME`, stripped, or `DEFAULT_AGENT_NAME` when unset or blank.

    A blank value must never become the registration name: an unnamed worker
    takes automatic dispatch for every room in the project. `require_agent_name`
    refuses a blank `LKAP_AGENT_NAME` outright; this fallback only keeps the
    import-time decorator safe until that check runs.
    """
    return (environ.get("LKAP_AGENT_NAME") or "").strip() or DEFAULT_AGENT_NAME


#: The dispatch name this worker registers under and the only one it accepts
#: (D-W2-11, CONTRACTS-V2 §5). Read once, at import, because `rtc_session`
#: resolves its name at decoration time.
AGENT_NAME = configured_agent_name(os.environ)


def require_agent_name(environ: Mapping[str, str], settings: Settings) -> str:
    """Refuse to run unless every *set* name source agrees with `LKAP_AGENT_NAME`.

    The worker registers with an explicit `agent_name=AGENT_NAME`, so an unset
    `LIVEKIT_AGENT_NAME` is fine (DECISIONS-W2 §D-W2-11). What is refused:

    * an empty `LKAP_AGENT_NAME` (`settings.agent_name`);
    * a `settings.agent_name` that differs from the name the module registered
      under — e.g. `LKAP_AGENT_NAME` only in a `.env` file the decorator never
      saw;
    * a `LIVEKIT_AGENT_NAME_OVERRIDE` that differs (the SDK would register
      under it);
    * a `LIVEKIT_AGENT_NAME` (process env or settings) that differs (ignored
      by the SDK, but it means the process was launched from another agent's
      environment).

    Args:
        environ: The process environment (`os.environ`).
        settings: The worker settings.

    Returns:
        The agent name.

    Raises:
        RuntimeError: Naming the offending source when one disagrees.
    """
    expected = settings.agent_name.strip()
    if not expected:
        raise RuntimeError(
            "refusing to start: LKAP_AGENT_NAME is empty; an unnamed worker would take automatic "
            "dispatch for every room in the project"
        )
    registered = configured_agent_name(environ)
    if expected != registered:
        raise RuntimeError(
            f"refusing to start: settings.agent_name={expected!r} differs from the name the worker "
            f"registers under ({registered!r}, from the process environment); set LKAP_AGENT_NAME "
            "in the environment, not only in a .env file"
        )
    override = environ.get("LIVEKIT_AGENT_NAME_OVERRIDE")
    if override and override != expected:
        raise RuntimeError(
            f"LIVEKIT_AGENT_NAME_OVERRIDE={override!r} would register the worker under the wrong "
            f"name; this worker must register as {expected!r}"
        )
    for source, value in (
        ("LIVEKIT_AGENT_NAME", environ.get("LIVEKIT_AGENT_NAME")),
        ("settings.livekit_agent_name", settings.livekit_agent_name),
    ):
        if value and value != expected:
            raise RuntimeError(
                f"refusing to start: {source}={value!r} contradicts the worker's name {expected!r}; "
                "this process looks like it was launched with another agent's env"
            )
    return expected


def effective_agent_name(environ: Mapping[str, str]) -> str:
    """The name the SDK registers under, given the explicit `agent_name` argument.

    Mirrors `AgentServer.rtc_session`'s precedence: a set
    `LIVEKIT_AGENT_NAME_OVERRIDE` wins, otherwise the explicit argument
    (`LKAP_AGENT_NAME`, default `lkap-agent`).
    """
    return environ.get("LIVEKIT_AGENT_NAME_OVERRIDE") or configured_agent_name(environ)


async def only_lkap_jobs(req: JobRequest) -> None:
    """Accept only jobs dispatched to this worker's name; reject everything else loudly.

    Automatic-dispatch jobs carry `agent_name == ""`; a job for another agent
    name can only reach this worker if it was mis-registered.
    """
    if req.agent_name != AGENT_NAME:
        logger.error(
            "rejecting job dispatched to the wrong agent name",
            job_agent_name=req.agent_name,
            worker_agent_name=AGENT_NAME,
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
        settings_agent_name=settings.agent_name,
        connection_id=settings.connection_id,
    )


def install_worker_registration(agent_server: Any, settings: Settings) -> WorkerRegistration:
    """Register this worker with the api once LiveKit has accepted it (CONTRACTS-V2 §5).

    Runs in the main (server) process only: job processes never import the
    `__main__` block that calls this.

    Logs `api_base_url` at startup (docs/v2/_asks.md V2-20-2): unlike the api,
    the worker never guesses this value — `Settings.api_base_url` is a
    required field with no `PORT`-derived fallback (`settings.py`) — but the
    *value itself* can still be wrong if whatever launched this worker (the
    supervisor, a hand-written `export`) computed it incorrectly, which is
    exactly what happened in the V2-20 isolation incident. A worker that
    registers against the wrong api is otherwise silent until something times
    out, so this line is the first place to look.
    """
    logger.info(
        "worker starting",
        api_base_url=settings.api_base_url,
        agent_name=settings.agent_name,
        connection_id=settings.connection_id,
        managed_by=settings.managed_by,
    )
    registration = WorkerRegistration(
        client=FleetClient(settings.api_base_url, settings.service_token),
        settings=settings,
        server=agent_server,
        pack_ids=lambda: sorted(PackLoader(settings.packs_list).discover()),
    )
    registration.attach()
    return registration


#: Port of the SDK's worker HTTP (health) server. Unset keeps the SDK default
#: (ephemeral in `dev`, 8081 in `start`); the supervisor's subprocess backend
#: sets `0` so replicas on one host never collide on 8081 (V2-20).
WORKER_HTTP_PORT_ENV = "LKAP_WORKER_HTTP_PORT"


def worker_http_port(environ: Mapping[str, str]) -> int | None:
    """The worker HTTP server port from `LKAP_WORKER_HTTP_PORT`, or `None` for the SDK default.

    Raises:
        ValueError: If `LKAP_WORKER_HTTP_PORT` is not an integer in 0..65535.
    """
    raw = environ.get(WORKER_HTTP_PORT_ENV, "").strip()
    if not raw:
        return None
    try:
        port = int(raw)
    except ValueError:
        raise ValueError(f"{WORKER_HTTP_PORT_ENV} must be an integer, got {raw!r}") from None
    if not 0 <= port <= 65535:
        raise ValueError(f"{WORKER_HTTP_PORT_ENV} must be within 0..65535, got {port}")
    return port


_http_port = worker_http_port(os.environ)
server = (
    AgentServer(setup_fnc=prewarm) if _http_port is None else AgentServer(setup_fnc=prewarm, port=_http_port)
)


@server.rtc_session(agent_name=AGENT_NAME, on_request=only_lkap_jobs)
async def entrypoint(ctx: Any) -> None:
    """The one registered `rtc_session`; all logic lives in :func:`run_session`."""
    await run_session(ctx, Deps.from_env(ctx.proc.userdata))


if __name__ == "__main__":  # pragma: no cover - process entry
    from livekit.agents import cli  # noqa: PLC0415

    _settings = get_settings()
    require_agent_name(os.environ, _settings)
    _registration = install_worker_registration(server, _settings)
    cli.run_app(server)
