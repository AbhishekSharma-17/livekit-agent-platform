"""`run_session` v2 (PLAN-V2 V2-07): dispatch without a session, recordings, latency,
reconnect grace, `first_speaker`, and connection-driven turn detection — all offline."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fakes.fake_api import FakeApi, resolved_config
from livekit import rtc
from livekit.agents import NOT_GIVEN
from lkap_contracts.agent_config import RecordingConfig, ResolvedAgentConfig
from lkap_contracts.connections import ConnectionCapabilities, ConnectionInfo
from lkap_contracts.dispatch import DispatchMetadata
from test_main import (
    FakeJobContext,
    RoomlessStarter,
    _deps,
    _ManualClock,
    _RecordingFactory,
)

from lkap_agent.config_client import RecordingUnavailableError, SessionEndedError, SessionNotFoundError
from lkap_agent.main import run_session
from lkap_agent.providers.factory import BuiltProviders


@pytest.fixture(autouse=True)
def _inference_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-W2-6: Inference objects read their credentials from the process env."""
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret")
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")


def _meta(
    *,
    session_id: str | None = "sess-1",
    identity: str = "user-guest",
    channel: str = "web",
    connection_id: str = "",
) -> str:
    return DispatchMetadata.model_validate(
        {
            "session_id": session_id,
            "agent_id": "agent-1",
            "config_version": 1,
            "participant_identity": identity,
            "channel": channel,
            "connection_id": connection_id,
        }
    ).model_dump_json()


def _participant(identity: str, reason: int = rtc.DisconnectReason.SIGNAL_CLOSE, **attrs: str) -> Any:
    return SimpleNamespace(
        identity=identity,
        kind=rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD,
        attributes=dict(attrs),
        disconnect_reason=reason,
    )


def _with_recording(config: ResolvedAgentConfig) -> ResolvedAgentConfig:
    return config.model_copy(update={"recording": RecordingConfig(enabled=True)})


# ------------------------------------------------------------ dispatch v2


async def test_run_session_missing_session_id_calls_sessions_start_with_channel_from_metadata() -> None:
    """D-V2-5: a dispatch without a session creates it from the job's room and metadata."""
    api = FakeApi(resolved_config(participant_identity="sip_in-caller"))
    ctx = FakeJobContext(_meta(session_id=None, identity="", channel="sip_in"))
    starter = RoomlessStarter()

    await run_session(ctx, _deps(api, session_starter=starter))

    assert api.resolve_calls == []
    (request,) = api.start_calls
    assert (request.agent_id, request.room_name, request.channel) == ("agent-1", "lkap-room-1", "sip_in")
    assert request.participant_identity == ""
    assert request.dispatch_metadata["channel"] == "sip_in"
    assert ctx.connected == 1
    # Nobody joins under the api's placeholder identity: RoomIO links the first caller.
    assert starter.room_options is not None
    assert starter.room_options.participant_identity is NOT_GIVEN
    await ctx.fire_shutdown("done")
    assert api.summaries and api.summaries[0].status == "ended"


async def test_run_session_empty_session_id_is_treated_as_no_session() -> None:
    """The v1-era empty string still means "no session yet"."""
    api = FakeApi(resolved_config())
    ctx = FakeJobContext(_meta(session_id=""))

    await run_session(ctx, _deps(api))

    assert api.resolve_calls == [] and len(api.start_calls) == 1
    assert api.start_calls[0].participant_identity == "user-guest"


async def test_run_session_with_session_id_resolves_and_never_starts_a_session() -> None:
    api = FakeApi(resolved_config())
    ctx = FakeJobContext(_meta())

    await run_session(ctx, _deps(api))

    assert api.resolve_calls == ["sess-1"] and api.start_calls == []


@pytest.mark.parametrize("error", [SessionNotFoundError("no agent"), SessionEndedError("dup room")])
async def test_run_session_sessions_start_failure_fails_cleanly_without_a_summary(error: Exception) -> None:
    """No session row exists to mark failed; the caller hears the fixed line and the job ends."""
    spoken: list[str] = []

    async def _speaker(_ctx: Any, line: str) -> None:
        spoken.append(line)

    api = FakeApi(resolve_error=error)
    ctx = FakeJobContext(_meta(session_id=None))

    await run_session(ctx, _deps(api, fallback_speaker=_speaker))

    assert api.summaries == []
    assert spoken and ctx.shutdown_reasons == ["configuration unavailable"]


async def test_run_session_session_started_event_carries_channel_and_connection() -> None:
    config = resolved_config().model_copy(update={"connection": ConnectionInfo(connection_id="conn-a")})
    api = FakeApi(config)
    ctx = FakeJobContext(_meta())

    await run_session(ctx, _deps(api))
    await ctx.fire_shutdown("done")

    (started,) = api.events_of("session_started")
    assert started.payload == {"pipeline_mode": "cascaded", "channel": "web", "connection_id": "conn-a"}


# ------------------------------------------------------------- recording


async def test_recording_enabled_starts_egress_after_connect_and_records_the_event() -> None:
    api = FakeApi(_with_recording(resolved_config()), egress_id="EG_1")
    ctx = FakeJobContext(_meta())

    await run_session(ctx, _deps(api))
    await asyncio.sleep(0.01)

    assert ctx.connected == 1
    assert api.recording_starts == ["sess-1"]
    await ctx.fire_shutdown("done")
    (event,) = api.events_of("recording")
    assert event.payload == {"status": "active", "egress_id": "EG_1"}


async def test_recording_disabled_never_calls_recording_start() -> None:
    api = FakeApi(resolved_config())
    ctx = FakeJobContext(_meta())

    await run_session(ctx, _deps(api))
    await asyncio.sleep(0.01)
    await ctx.fire_shutdown("done")

    assert api.recording_starts == [] and api.events_of("recording") == []


async def test_recording_start_failure_records_a_failed_event_and_keeps_the_call() -> None:
    """docs/v2/_asks.md V2-20-3: the timeline event alone left `sessions.recording_status`

    at `"none"` — the session row itself must also learn about the failure,
    via the same `post_recording` fallback route a *started* Egress's
    shutdown report uses.
    """
    api = FakeApi(
        _with_recording(resolved_config()),
        recording_error=RecordingUnavailableError("recording/start answered HTTP 501"),
    )
    ctx = FakeJobContext(_meta())

    await run_session(ctx, _deps(api))
    await asyncio.sleep(0.01)
    await ctx.fire_shutdown("done")

    (event,) = api.events_of("recording")
    assert event.payload["status"] == "failed"
    assert ctx.shutdown_reasons == []
    assert api.summaries[0].status == "ended"
    (recording,) = api.recordings
    assert recording.status == "failed"
    assert recording.egress_id == ""
    assert recording.error is not None and "recording/start answered HTTP 501" in recording.error


async def test_shutdown_polls_the_egress_and_posts_its_state() -> None:
    """D-V2-16 fallback: without a webhook the worker reports what LiveKit sees."""
    polled: list[str] = []

    async def _egress_status(egress_id: str) -> tuple[str, float | None]:
        polled.append(egress_id)
        return "ready", 12.5

    api = FakeApi(_with_recording(resolved_config()), egress_id="EG_2")
    ctx = FakeJobContext(_meta())

    await run_session(ctx, _deps(api, egress_status=_egress_status))
    await asyncio.sleep(0.01)
    await ctx.fire_shutdown("done")

    assert polled == ["EG_2"]
    assert api.call_log.index("summary") < api.call_log.index("recording"), (
        "the poll never delays the summary"
    )
    (recording,) = api.recordings
    assert (recording.egress_id, recording.status, recording.duration_s) == ("EG_2", "ready", 12.5)


async def test_shutdown_skips_the_egress_poll_when_no_recording_started() -> None:
    polled: list[str] = []

    async def _egress_status(egress_id: str) -> tuple[str, float | None]:
        polled.append(egress_id)
        return "ready", None

    api = FakeApi(resolved_config())
    ctx = FakeJobContext(_meta())

    await run_session(ctx, _deps(api, egress_status=_egress_status))
    await ctx.fire_shutdown("done")

    assert polled == [] and api.recordings == []


# --------------------------------------------------------- reconnect grace (F-33)


async def _grace_session(
    grace_s: float | None = 60.0, identity: str = "user-guest"
) -> tuple[FakeJobContext, _ManualClock, FakeApi]:
    clock = _ManualClock()
    api = FakeApi(resolved_config(participant_identity=identity))
    ctx = FakeJobContext(_meta(identity=identity))
    deps = _deps(api, sleep=clock.sleep)
    deps.settings.reconnect_grace_s = grace_s
    deps.settings.idle_hangup_s = None
    await run_session(ctx, deps)
    return ctx, clock, api


async def test_participant_drop_keeps_the_job_for_the_grace_period_then_shuts_down() -> None:
    ctx, clock, _api = await _grace_session()

    ctx.room.emit("participant_disconnected", _participant("user-guest"))
    await asyncio.sleep(0)

    assert clock.delays == [60.0]
    assert ctx.shutdown_reasons == [], "the job must survive the drop"
    await clock.advance()
    assert ctx.shutdown_reasons == ["participant did not reconnect"]


async def test_participant_rejoining_within_the_grace_period_cancels_the_shutdown() -> None:
    ctx, clock, api = await _grace_session()

    ctx.room.emit("participant_disconnected", _participant("user-guest"))
    await asyncio.sleep(0)
    ctx.room.emit("participant_connected", _participant("user-guest"))
    await clock.advance()

    assert ctx.shutdown_reasons == []
    await ctx.fire_shutdown("done")
    messages = [e.payload.get("message") for e in api.events_of("info")]
    assert "caller reconnected" in messages


@pytest.mark.parametrize(
    "reason",
    [
        rtc.DisconnectReason.CLIENT_INITIATED,
        rtc.DisconnectReason.ROOM_DELETED,
        rtc.DisconnectReason.USER_REJECTED,
    ],
)
async def test_a_deliberate_leave_ends_the_job_at_once(reason: int) -> None:
    """A hang-up keeps v1's prompt summary: no grace for the SDK's close-on-disconnect reasons."""
    ctx, clock, _api = await _grace_session()

    ctx.room.emit("participant_disconnected", _participant("user-guest", reason=reason))
    await asyncio.sleep(0)

    assert clock.delays == []
    assert len(ctx.shutdown_reasons) == 1 and ctx.shutdown_reasons[0].startswith("participant left")


async def test_another_participant_leaving_is_ignored() -> None:
    ctx, clock, _api = await _grace_session()

    ctx.room.emit("participant_disconnected", _participant("someone-else"))
    await clock.advance()

    assert clock.delays == [] and ctx.shutdown_reasons == []


@pytest.mark.parametrize("grace_s", [None, 0.0])
async def test_a_disabled_grace_ends_the_job_when_the_caller_drops(grace_s: float | None) -> None:
    ctx, clock, _api = await _grace_session(grace_s=grace_s)

    ctx.room.emit("participant_disconnected", _participant("user-guest"))
    await asyncio.sleep(0)

    assert clock.delays == [] and len(ctx.shutdown_reasons) == 1


async def test_without_a_dispatched_identity_the_first_caller_is_watched_and_avatars_ignored() -> None:
    ctx, clock, _api = await _grace_session(identity="")

    avatar = _participant("bey-avatar", **{"lk.publish_on_behalf": "agent-1"})
    ctx.room.emit("participant_disconnected", avatar)
    await asyncio.sleep(0)
    assert clock.delays == []

    ctx.room.emit("participant_disconnected", _participant("sip_+15550100"))
    await asyncio.sleep(0)
    assert clock.delays == [60.0]


async def test_job_shutdown_cancels_a_pending_reconnect_grace() -> None:
    ctx, clock, _api = await _grace_session()

    ctx.room.emit("participant_disconnected", _participant("user-guest"))
    await asyncio.sleep(0)
    await ctx.fire_shutdown("session closed")
    await clock.advance()

    assert "participant did not reconnect" not in ctx.shutdown_reasons


# --------------------------------------------------------------- latency


async def test_latency_from_assistant_turn_metrics_is_posted_before_the_summary() -> None:
    from livekit.agents import llm
    from livekit.agents.voice.events import ConversationItemAddedEvent

    starter = RoomlessStarter()
    api = FakeApi(resolved_config(greeting=""))
    ctx = FakeJobContext(_meta())
    await run_session(ctx, _deps(api, session_starter=starter))
    assert starter.session is not None

    for e2e, ttft, ttfb in ((0.8, 0.3, 0.2), (1.2, 0.5, 0.4)):
        message = llm.ChatMessage(
            role="assistant",
            content=["ok"],
            metrics={"e2e_latency": e2e, "llm_node_ttft": ttft, "tts_node_ttfb": ttfb},
        )
        starter.session.emit("conversation_item_added", ConversationItemAddedEvent(item=message))
    await ctx.fire_shutdown("done")

    (metrics,) = api.metrics
    assert metrics.latency.turns == 2
    assert metrics.latency.eou_to_first_audio_ms_p50 == pytest.approx(1000.0)
    assert metrics.latency.llm_ttft_ms_p95 == pytest.approx(490.0)
    assert metrics.usage_lines == []


async def test_no_latency_is_posted_for_a_session_without_measured_turns() -> None:
    api = FakeApi(resolved_config(greeting=""))
    ctx = FakeJobContext(_meta())

    await run_session(ctx, _deps(api))
    await ctx.fire_shutdown("done")

    assert api.metrics == []


# ------------------------------------------------------------- first_speaker


@pytest.mark.parametrize(("first_speaker", "greets"), [("agent", True), ("user", False)])
async def test_first_speaker_user_skips_the_greeting(first_speaker: str, greets: bool) -> None:
    config = resolved_config(greeting="Hello there!")
    voice = config.config.voice.model_copy(update={"first_speaker": first_speaker})
    config = config.model_copy(update={"config": config.config.model_copy(update={"voice": voice})})
    starter = RoomlessStarter()
    api = FakeApi(config)
    ctx = FakeJobContext(_meta())

    await run_session(ctx, _deps(api, session_starter=starter))
    await asyncio.sleep(0.1)

    assert ("Hello there!" in starter.assistant_turns()) is greets


# ------------------------------------------------------- turn detection defaults


async def _modes_seen(config: ResolvedAgentConfig, factory: Any | None = None) -> list[str]:
    seen: list[str] = []

    def _detector(mode: str) -> None:
        seen.append(mode)
        return None

    ctx = FakeJobContext(_meta())
    await run_session(ctx, _deps(FakeApi(config), factory=factory, turn_detector_factory=_detector))
    await ctx.fire_shutdown("done")
    return seen


@pytest.mark.parametrize("mode", ["hosted", "local"])
async def test_the_default_turn_detector_follows_the_connection_turn_detector_mode(mode: str) -> None:
    caps = ConnectionCapabilities(turn_detector_mode=mode)  # type: ignore[arg-type]
    config = resolved_config().model_copy(
        update={"connection": ConnectionInfo(connection_id="c", capabilities=caps)}
    )

    assert await _modes_seen(config) == [mode]


async def test_realtime_mode_builds_no_turn_detector() -> None:
    assert await _modes_seen(resolved_config(mode="realtime", with_tts=False)) == []


async def test_a_configured_turn_detection_slot_replaces_the_default_detector() -> None:
    class _WithDetector(_RecordingFactory):
        def build_all(self, resolved: Any, *, optional: Any = None) -> BuiltProviders:
            built = super().build_all(resolved, optional=optional)
            built.turn_detection = object()
            return built

    assert await _modes_seen(resolved_config(), factory=_WithDetector()) == []


async def test_a_session_without_a_dispatched_identity_gives_the_ui_and_frames_no_identity() -> None:
    """ "" is "not known yet": the channel and the frame buffer must fall back, not match nobody."""
    seen: dict[str, Any] = {}

    def _ui(**kw: Any) -> Any:
        from lkap_agent.main import NoopUiChannel

        seen["ui"] = kw["ui_identity"]
        return NoopUiChannel(kw.get("session_id", ""))

    def _frames(**kw: Any) -> Any:
        from lkap_agent.main import NoopFrameBuffer

        seen["frames"] = kw["participant_identity"]
        return NoopFrameBuffer()

    api = FakeApi(resolved_config(participant_identity="sip_in-caller"))
    ctx = FakeJobContext(_meta(session_id=None, identity="", channel="sip_in"))

    await run_session(ctx, _deps(api, ui_channel_factory=_ui, frame_buffer_factory=_frames))

    assert seen == {"ui": None, "frames": None}
