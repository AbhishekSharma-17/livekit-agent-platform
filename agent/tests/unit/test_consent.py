"""V5-15: consent and disclosure in the worker.

The greeting and wording preparation (`session_builder.apply_compliance`), the
two tools against the **real** `UiChannel` over `FakeRoom`, the recording gate
(`main.RecordingConsentGate`) and a full `run_session` with a consent-gated
recording.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from fakes.fake_api import FakeApi, resolved_config
from fakes.fake_ctx import FakeBackgroundRunner, FakePackSessionContext, default_agent_config
from fakes.fake_room import FakeRemoteParticipant, FakeRoom
from livekit.agents import RunContext, ToolError
from lkap_contracts.agent_config import (
    DisclosureConfig,
    PanelLayout,
    RecordingConfig,
    ResolvedAgentConfig,
    VoiceConfig,
)
from lkap_contracts.compliance import COMPLIANCE_PRESETS, CONSENT_EVENT, ResolvedCompliance
from lkap_contracts.dispatch import DispatchMetadata
from lkap_contracts.ui_protocol import RPC_AGENT_ACTION, RPC_UI_REQUEST, AgentAction, BlockSpec
from test_main import FakeJobContext, RoomlessStarter, _deps

from lkap_agent.main import RecordingConsentGate, run_session
from lkap_agent.session_builder import DISCLOSURE_PLACEHOLDER, apply_compliance
from lkap_agent.tools.builtin import build_builtin_tools
from lkap_agent.tools.builtin.record_consent import CONSENT_GOODBYE, build_record_consent_tool
from lkap_agent.tools.builtin.request_consent import NOT_ANSWERED, build_request_consent_tool
from lkap_agent.ui.channel import BARGE_IN, UiChannel

IN = COMPLIANCE_PRESETS["in"]
DISCLOSURE = IN.disclosure_text
RECORDING_QUESTION = IN.recording_text


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ================================================================ apply_compliance


def _resolved(
    *,
    greeting: str = "Hello there!",
    disclosure: DisclosureConfig | None = None,
    blocks: list[BlockSpec] | None = None,
    recording: RecordingConfig | None = None,
    channel: str = "web",
    first_speaker: str = "agent",
    compliance: ResolvedCompliance | None = None,
) -> ResolvedAgentConfig:
    base = resolved_config(greeting=greeting, channel=channel)
    config = base.config.model_copy(
        update={
            "voice": VoiceConfig(greeting=greeting, first_speaker=first_speaker),  # type: ignore[arg-type]
            "disclosure": disclosure or DisclosureConfig(),
            "panel": PanelLayout(blocks=blocks or []),
            "recording": recording or RecordingConfig(),
        }
    )
    return base.model_copy(
        update={"config": config, "recording": recording or RecordingConfig(), "compliance": compliance}
    )


def test_the_default_puts_the_disclosure_in_front_of_the_greeting_once() -> None:
    once = apply_compliance(_resolved())
    assert once.config.voice.greeting == f"{DISCLOSURE} Hello there!"
    assert apply_compliance(once).config.voice.greeting == f"{DISCLOSURE} Hello there!"
    assert once.config.disclosure.text == DISCLOSURE


def test_a_disclosure_block_with_the_banner_does_not_repeat_the_greeting_line() -> None:
    blocks = [BlockSpec(id="ai", type="consent", config={"kind": "ai_disclosure"})]
    prepared = apply_compliance(_resolved(blocks=blocks))
    assert prepared.config.voice.greeting.count(DISCLOSURE) == 1
    assert prepared.config.panel.blocks[0].config["text"] == DISCLOSURE


def test_banner_only_leaves_a_web_greeting_byte_identical() -> None:
    prepared = apply_compliance(_resolved(disclosure=DisclosureConfig(position="banner")))
    assert prepared.config.voice.greeting == "Hello there!"
    assert "AI disclosure" not in prepared.config.instructions


@pytest.mark.parametrize("channel", ["sip_in", "sip_out"])
def test_banner_only_is_spoken_on_a_phone_call(channel: str) -> None:
    prepared = apply_compliance(_resolved(disclosure=DisclosureConfig(position="banner"), channel=channel))
    assert prepared.config.voice.greeting == f"{DISCLOSURE} Hello there!"


def test_a_disabled_disclosure_changes_nothing_but_removes_the_placeholder() -> None:
    off = DisclosureConfig(enabled=False)
    assert apply_compliance(_resolved(disclosure=off)).config.voice.greeting == "Hello there!"
    prepared = apply_compliance(
        _resolved(greeting=f"Hi! {DISCLOSURE_PLACEHOLDER} How can I help?", disclosure=off)
    )
    assert prepared.config.voice.greeting == "Hi! How can I help?"


def test_the_placeholder_marks_where_the_disclosure_goes() -> None:
    prepared = apply_compliance(
        _resolved(greeting=f"Hi, this is Acme. {DISCLOSURE_PLACEHOLDER} How can I help?")
    )
    assert prepared.config.voice.greeting == f"Hi, this is Acme. {DISCLOSURE} How can I help?"


def test_the_agent_text_then_the_workspace_text_win_over_the_preset() -> None:
    workspace = ResolvedCompliance(
        jurisdiction="eu", disclosure_text="An AI answers.", recording_text="Record?"
    )
    assert (
        apply_compliance(_resolved(compliance=workspace)).config.voice.greeting
        == "An AI answers. Hello there!"
    )
    own = DisclosureConfig(text="I'm an AI.")
    prepared = apply_compliance(_resolved(compliance=workspace, disclosure=own))
    assert prepared.config.voice.greeting == "I'm an AI. Hello there!"
    assert prepared.config.recording.consent_text == "Record?"


@pytest.mark.parametrize("greeting", ["", "Hello there!"])
def test_without_a_spoken_greeting_the_disclosure_becomes_an_instruction(greeting: str) -> None:
    prepared = apply_compliance(_resolved(greeting=greeting, first_speaker="user"))
    assert prepared.config.voice.greeting == greeting
    assert f'begin your first reply by saying exactly: "{DISCLOSURE}"' in prepared.config.instructions


def test_consent_blocks_get_the_workspace_wording_unless_they_have_their_own() -> None:
    blocks = [
        BlockSpec(id="rec", type="consent", config={"kind": "recording"}),
        BlockSpec(id="own", type="consent", config={"kind": "recording", "text": "Can we record?"}),
        BlockSpec(id="terms", type="consent", config={"kind": "terms"}),
    ]
    prepared = apply_compliance(_resolved(blocks=blocks))
    texts = {spec.id: spec.config.get("text") for spec in prepared.config.panel.blocks}
    assert texts == {"rec": RECORDING_QUESTION, "own": "Can we record?", "terms": None}


def test_recording_consent_asks_on_screen_with_a_block_and_out_loud_without() -> None:
    gated = RecordingConfig(enabled=True, require_consent=True)
    blocks = [BlockSpec(id="rec", type="consent", config={"kind": "recording"})]
    on_screen = apply_compliance(_resolved(recording=gated, blocks=blocks)).config.instructions
    assert "request_consent" in on_screen and RECORDING_QUESTION not in on_screen
    out_loud = apply_compliance(_resolved(recording=gated)).config.instructions
    assert f'ask exactly: "{RECORDING_QUESTION}"' in out_loud and "record_consent" in out_loud
    phone = apply_compliance(_resolved(recording=gated, blocks=blocks, channel="sip_in")).config.instructions
    assert RECORDING_QUESTION in phone
    ungated = apply_compliance(_resolved(recording=RecordingConfig(enabled=True))).config.instructions
    assert "Recording consent" not in ungated


def test_apply_compliance_keeps_the_resolved_recording_switch() -> None:
    base = resolved_config()
    resolved = base.model_copy(update={"recording": RecordingConfig(enabled=True)})
    assert apply_compliance(resolved).recording.enabled is True


# ================================================================ the tools


@dataclass
class _Call:
    call_id: str = "call-1"


@dataclass
class _RunCtx:
    function_call: _Call = field(default_factory=_Call)


def _run_ctx() -> RunContext[Any]:
    return cast(RunContext[Any], _RunCtx())


def _ctx(
    blocks: list[BlockSpec] | None = None,
    *,
    mode: Any = "cascaded",
    recording: RecordingConfig | None = None,
) -> tuple[FakePackSessionContext, UiChannel, FakeRoom, list[str]]:
    room = FakeRoom()
    room.add_remote_participant(FakeRemoteParticipant("web-ui"))
    room.local_participant.rpc_call_responses[RPC_UI_REQUEST] = json.dumps({"ok": True, "payload": {}})
    channel = UiChannel(room, "sess-1")  # type: ignore[arg-type]
    channel.start()
    specs = (
        [BlockSpec(id="rec", type="consent", config={"kind": "recording", "text": RECORDING_QUESTION})]
        if blocks is None
        else blocks
    )
    config = default_agent_config(panel=PanelLayout(blocks=specs), recording=recording or RecordingConfig())
    channel.init_blocks(specs)
    ctx = FakePackSessionContext(
        pipeline_mode=mode, config=config, ui=cast(Any, channel), room=cast(Any, room)
    )
    shutdowns: list[str] = []
    ctx.request_shutdown = shutdowns.append  # type: ignore[attr-defined]
    return ctx, channel, room, shutdowns


async def _block_submit(room: FakeRoom, block_id: str, values: dict[str, Any]) -> None:
    raw = AgentAction(
        action="block_submit", payload={"block_id": block_id, "values": values}
    ).model_dump_json()
    await room.local_participant.invoke_rpc(RPC_AGENT_ACTION, raw)


async def _until(predicate: Any) -> None:
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never became true")


def _consent_events(ctx: FakePackSessionContext) -> list[dict[str, Any]]:
    return [payload for kind, payload in ctx.events if kind == CONSENT_EVENT]


def _names(ctx: FakePackSessionContext) -> set[str]:
    return {t.info.name for t in build_builtin_tools(ctx, disabled=[], http_enabled=False)}


def test_both_tools_register_for_a_consent_block() -> None:
    ctx, _ch, _room, _ = _ctx()
    assert {"request_consent", "record_consent"} <= _names(ctx)


def test_record_consent_alone_registers_when_the_recording_waits_for_consent() -> None:
    ctx, _ch, _room, _ = _ctx([], recording=RecordingConfig(enabled=True, require_consent=True))
    names = _names(ctx)
    assert "record_consent" in names and "request_consent" not in names
    plain, _ch, _room, _ = _ctx([], recording=RecordingConfig(enabled=True))
    assert not {"record_consent", "request_consent"} & _names(plain)


def test_builtin_disabled_switches_the_consent_tools_off() -> None:
    ctx, _ch, _room, _ = _ctx()
    names = {
        t.info.name
        for t in build_builtin_tools(ctx, disabled=["request_consent", "record_consent"], http_enabled=False)
    }
    assert not {"request_consent", "record_consent"} & names


async def test_a_tap_accept_settles_the_block_and_records_the_hash_of_the_exact_text() -> None:
    ctx, channel, room, shutdowns = _ctx()
    task = asyncio.create_task(build_request_consent_tool(ctx)(context=_run_ctx()))
    await _until(lambda: channel.state.blocks["rec"]["status"] == "requested")
    await _block_submit(room, "rec", {"accepted": True})
    result = await task
    assert "agreed" in result
    state = channel.state.blocks["rec"]
    assert (state["status"], state["accepted"], state["method"]) == ("submitted", True, "tap")
    assert state["text_hash"] == _sha(RECORDING_QUESTION)
    assert _consent_events(ctx) == [
        {
            "kind": "recording",
            "accepted": True,
            "method": "tap",
            "text_hash": _sha(RECORDING_QUESTION),
            "block_id": "rec",
        }
    ]
    assert shutdowns == []


async def test_a_declined_required_consent_with_end_call_says_goodbye_and_ends() -> None:
    blocks = [
        BlockSpec(
            id="rec",
            type="consent",
            config={"kind": "recording", "text": RECORDING_QUESTION, "decline_action": "end_call"},
        )
    ]
    ctx, channel, room, shutdowns = _ctx(blocks)
    task = asyncio.create_task(build_request_consent_tool(ctx)(context=_run_ctx()))
    await _until(lambda: channel.state.blocks["rec"]["status"] == "requested")
    await _block_submit(room, "rec", {"accepted": False})
    result = await task
    assert "ending" in result
    assert cast(Any, ctx.session).said == [CONSENT_GOODBYE]
    assert shutdowns == ["consent declined"]
    assert _consent_events(ctx)[0]["accepted"] is False


async def test_a_decline_that_continues_does_not_end_the_call() -> None:
    ctx, channel, room, shutdowns = _ctx()
    task = asyncio.create_task(build_request_consent_tool(ctx)(context=_run_ctx()))
    await _until(lambda: channel.state.blocks["rec"]["status"] == "requested")
    await _block_submit(room, "rec", {"accepted": False})
    assert "declined" in await task
    assert shutdowns == [] and cast(Any, ctx.session).said == []


async def test_a_barge_in_withdraws_the_question_and_a_spoken_yes_is_recorded_as_voice() -> None:
    ctx, channel, _room, _ = _ctx()
    task = asyncio.create_task(build_request_consent_tool(ctx)(context=_run_ctx()))
    await _until(lambda: channel.pending_requests == {"rec": "request"})
    channel.cancel_pending(BARGE_IN, methods=["request"])
    assert await task == NOT_ANSWERED
    await _until(lambda: channel.state.blocks["rec"]["status"] == "cancelled")

    result = await build_record_consent_tool(ctx)(context=_run_ctx(), accepted=True)
    assert "agreed" in result
    state = channel.state.blocks["rec"]
    assert (state["status"], state["accepted"], state["method"]) == ("submitted", True, "voice")
    (event,) = _consent_events(ctx)
    assert (event["method"], event["text_hash"]) == ("voice", _sha(RECORDING_QUESTION))


async def test_a_timeout_is_a_not_answered_line(monkeypatch: pytest.MonkeyPatch) -> None:
    from lkap_agent.tools.builtin import request_consent as module  # noqa: PLC0415

    monkeypatch.setattr(module, "CONSENT_TIMEOUT_S", 0.01)
    ctx, channel, _room, _ = _ctx()
    assert await build_request_consent_tool(ctx)(context=_run_ctx()) == NOT_ANSWERED
    assert channel.state.blocks["rec"]["status"] == "cancelled"
    assert _consent_events(ctx) == []


@pytest.mark.parametrize("mode", ["realtime", "half_cascade"])
async def test_realtime_returns_none_and_a_tap_arrives_as_an_urgent_result(mode: Any) -> None:
    ctx, channel, room, _ = _ctx(mode=mode)
    background = cast(FakeBackgroundRunner, ctx.background)
    assert await build_request_consent_tool(ctx)(context=_run_ctx()) is None
    await _until(lambda: channel.state.blocks["rec"]["status"] == "requested")
    await _block_submit(room, "rec", {"accepted": True})
    await background.wait_idle()
    ((job_id, outcome, instructions),) = background.urgent_events
    assert (job_id, outcome["accepted"], outcome["method"]) == ("call-1", True, "tap")
    assert instructions is not None and "agreed" in instructions
    assert len(_consent_events(ctx)) == 1


async def test_realtime_spoken_answer_is_handed_to_the_waiting_request_and_not_announced() -> None:
    ctx, channel, _room, _ = _ctx(mode="realtime")
    background = cast(FakeBackgroundRunner, ctx.background)
    await build_request_consent_tool(ctx)(context=_run_ctx())
    await _until(lambda: channel.pending_requests == {"rec": "request"})
    assert "agreement" in await build_record_consent_tool(ctx)(context=_run_ctx(), accepted=True)
    await background.wait_idle()
    assert background.urgent_events == [] and background.routine_notes == []
    (event,) = _consent_events(ctx)
    assert (event["accepted"], event["method"]) == (True, "voice")
    assert channel.state.blocks["rec"]["method"] == "voice"


@pytest.mark.parametrize("channel_name", ["sip_in", "sip_out"])
async def test_on_a_phone_call_the_wording_is_read_out(channel_name: str) -> None:
    ctx, channel, _room, _ = _ctx()
    ctx.channel = channel_name  # type: ignore[attr-defined]
    result = json.loads(await build_request_consent_tool(ctx)(context=_run_ctx()))
    assert result["channel"] == "voice_only" and result["say"] == RECORDING_QUESTION
    assert channel.state.blocks["rec"]["status"] == "idle"


async def test_on_the_text_channel_the_question_is_asked_in_the_chat() -> None:
    ctx, _channel, _room, _ = _ctx()
    ctx.channel = "text"  # type: ignore[attr-defined]
    result = await build_request_consent_tool(ctx)(context=_run_ctx())
    assert RECORDING_QUESTION in result and "record_consent" in result


async def test_an_accepted_consent_is_not_asked_again() -> None:
    ctx, channel, _room, _ = _ctx()
    await build_record_consent_tool(ctx)(context=_run_ctx(), accepted=True)
    result = await build_request_consent_tool(ctx)(context=_run_ctx())
    assert "already agreed" in result
    assert channel.pending_requests == {}


async def test_record_consent_without_a_block_hashes_the_recording_question() -> None:
    gated = RecordingConfig(enabled=True, require_consent=True, consent_text="May we record this call?")
    ctx, _channel, _room, _ = _ctx([], recording=gated)
    result = await build_record_consent_tool(ctx)(context=_run_ctx(), accepted=True)
    assert "recording starts now" in result
    assert _consent_events(ctx) == [
        {
            "kind": "recording",
            "accepted": True,
            "method": "voice",
            "text_hash": _sha("May we record this call?"),
            "block_id": None,
        }
    ]


async def test_record_consent_refuses_a_bad_kind_or_an_unknown_block() -> None:
    ctx, _channel, _room, _ = _ctx()
    with pytest.raises(ToolError):
        await build_record_consent_tool(ctx)(context=_run_ctx(), accepted=True, kind="marketing")
    with pytest.raises(ToolError):
        await build_record_consent_tool(ctx)(context=_run_ctx(), accepted=True, block_id="nope")


async def test_record_consent_needs_a_block_id_when_several_blocks_are_ambiguous() -> None:
    blocks = [
        BlockSpec(id="rec", type="consent", config={"kind": "recording", "text": "Record?"}),
        BlockSpec(id="terms", type="consent", config={"kind": "terms", "text": "Terms?"}),
    ]
    ctx, channel, _room, _ = _ctx(blocks)
    with pytest.raises(ToolError):
        await build_record_consent_tool(ctx)(context=_run_ctx(), accepted=True)
    await build_record_consent_tool(ctx)(context=_run_ctx(), accepted=True, kind="terms")
    assert channel.state.blocks["terms"]["accepted"] is True
    assert channel.state.blocks["rec"]["accepted"] is None


# ================================================================ the recording gate


def test_the_gate_starts_once_on_the_first_accepted_recording_consent() -> None:
    gate = RecordingConsentGate()
    seen: list[tuple[str, dict[str, Any]]] = []
    starts: list[str] = []
    record = gate.wrap(lambda event_type, payload: seen.append((event_type, payload)))
    gate.when_accepted(lambda: starts.append("start"))
    record(CONSENT_EVENT, {"kind": "ai_disclosure", "accepted": True})
    record(CONSENT_EVENT, {"kind": "recording", "accepted": False})
    assert starts == [] and gate.declined
    record("info", {"message": "x"})
    record(CONSENT_EVENT, {"kind": "recording", "accepted": True})
    record(CONSENT_EVENT, {"kind": "recording", "accepted": True})
    assert starts == ["start"]
    assert [event_type for event_type, _ in seen] == [
        CONSENT_EVENT,
        CONSENT_EVENT,
        "info",
        CONSENT_EVENT,
        CONSENT_EVENT,
    ]


def test_the_gate_fires_at_once_when_the_caller_already_agreed() -> None:
    gate = RecordingConsentGate()
    gate.wrap(lambda *_: None)(CONSENT_EVENT, {"kind": "recording", "accepted": True})
    starts: list[int] = []
    gate.when_accepted(lambda: starts.append(1))
    assert starts == [1]


def _meta() -> str:
    return DispatchMetadata.model_validate(
        {
            "session_id": "sess-1",
            "agent_id": "agent-1",
            "config_version": 1,
            "participant_identity": "user-guest",
            "channel": "web",
        }
    ).model_dump_json()


class _OrderedApi(FakeApi):
    """Records how many `consent` events the api had when the recording start arrived."""

    def __init__(self, resolved: ResolvedAgentConfig) -> None:
        super().__init__(resolved, egress_id="EG_1")
        self.consents_at_start: list[int] = []

    async def start_recording(self, session_id: str) -> str:
        self.consents_at_start.append(len(self.events_of(CONSENT_EVENT)))
        return await super().start_recording(session_id)


def _gated_config() -> ResolvedAgentConfig:
    base = resolved_config()
    gated = RecordingConfig(enabled=True, require_consent=True)
    return base.model_copy(
        update={"recording": gated, "config": base.config.model_copy(update={"recording": gated})}
    )


@pytest.fixture
def _inference_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret")
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")


@pytest.mark.usefixtures("_inference_env")
async def test_a_consent_gated_recording_starts_only_after_the_consent_reached_the_api() -> None:
    api = _OrderedApi(_gated_config())
    ctx = FakeJobContext(_meta())
    starter = RoomlessStarter()
    await run_session(ctx, _deps(api, session_starter=starter))
    await asyncio.sleep(0.01)
    assert api.recording_starts == []

    record = starter.agent._ctx.record_event
    record(CONSENT_EVENT, {"kind": "recording", "accepted": True, "method": "voice", "text_hash": _sha("x")})
    await _until(lambda: api.recording_starts == ["sess-1"])
    assert api.consents_at_start == [1]
    await ctx.fire_shutdown("done")


@pytest.mark.usefixtures("_inference_env")
async def test_a_declined_recording_is_never_started() -> None:
    api = _OrderedApi(_gated_config())
    ctx = FakeJobContext(_meta())
    starter = RoomlessStarter()
    await run_session(ctx, _deps(api, session_starter=starter))
    starter.agent._ctx.record_event(
        CONSENT_EVENT, {"kind": "recording", "accepted": False, "method": "tap", "text_hash": _sha("x")}
    )
    await asyncio.sleep(0.01)
    await ctx.fire_shutdown("done")
    assert api.recording_starts == []
    assert [e.payload["accepted"] for e in api.events_of(CONSENT_EVENT)] == [False]
