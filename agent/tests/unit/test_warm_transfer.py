"""V5-32: warm transfer (WarmTransferTask, Cloud gating, cold fallback), the handoff block, AMD.

No trunk, no LiveKit server, no model: `fakes.fake_sip` stands in for the SIP and room
services and `fakes.fake_amd` for the detector. The SDK's own `WarmTransferTask` runs
for real with its LiveKit boundary (`get_job_context`, `rtc.Room`, `AgentSession`,
`BackgroundAudioPlayer`) patched, so its steps are observed in order.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, cast

import pytest
from fakes.fake_amd import FakeAMD, FakeAMDFactory
from fakes.fake_api import FakeApi, resolved_config
from fakes.fake_ctx import FakePackSessionContext
from fakes.fake_sip import (
    FakeActivity,
    FakeHoldPlayer,
    FakeSipJobContext,
    FakeSipRoom,
    FakeVoiceSession,
    consult_fakes,
)
from livekit import rtc
from livekit.agents import AMD, AMDCategory, ToolError, llm
from livekit.agents.beta.workflows import WarmTransferResult, WarmTransferTask
from livekit.agents.beta.workflows import warm_transfer as sdk_warm
from livekit.agents.voice.background_audio import BuiltinAudioClip
from livekit.api import MoveParticipantRequest
from lkap_contracts.agent_config import PanelLayout
from lkap_contracts.api_models import InternalTransferOut
from lkap_contracts.telephony import (
    AMD_RESULTS,
    AmdConfig,
    TelephonyConfig,
    TransferTarget,
    WarmTransferRoute,
)
from lkap_contracts.ui_protocol import BlockSpec

from lkap_agent import telephony as telephony_module
from lkap_agent.telephony import (
    DEFAULT_VOICEMAIL_MESSAGE,
    AmdRunner,
    HandOver,
    TelephonySession,
    WarmRequest,
    WarmResult,
    amd_config_for,
    amd_supported,
    build_telephony_tools,
    default_amd_factory,
    hang_up,
    run_warm_transfer,
    session_for,
    transfer_modes,
    wait_for_sip_participant,
    warm_transfer_task_class,
)

ROUTE = WarmTransferRoute(trunk_id="ST_out_1", caller_id="+15551230000", targets=["+15550009999"])
HANDOFF_PANEL = PanelLayout(blocks=[BlockSpec(id="handoff", type="handoff")])


# ------------------------------------------------------------------ SDK tripwires
def test_the_1_8_3_warm_transfer_surface_is_what_lkap_overrides() -> None:
    """A newer SDK that renames any of these must fail here, not on a live call."""
    params = inspect.signature(WarmTransferTask.__init__).parameters
    for name in ("sip_call_to", "sip_trunk_id", "sip_number", "chat_ctx", "ringing_timeout", "hold_audio"):
        assert name in params, name
    assert "extra_instructions" in params
    for method in ("on_enter", "_dial_human_agent", "_originate_human_agent", "_merge_calls", "_set_result"):
        assert inspect.iscoroutinefunction(getattr(WarmTransferTask, method)) or method == "_set_result", (
            method
        )
    assert BuiltinAudioClip.HOLD_MUSIC.value
    assert {"room", "identity", "destination_room"} <= set(MoveParticipantRequest.DESCRIPTOR.fields_by_name)
    assert issubclass(warm_transfer_task_class(), WarmTransferTask)


def test_the_1_8_3_amd_surface_is_what_lkap_calls() -> None:
    params = inspect.signature(AMD.__init__).parameters
    for name in ("llm", "stt", "participant_identity", "ivr_detection", "suppress_compatibility_warning"):
        assert name in params, name
    assert inspect.iscoroutinefunction(AMD.execute)
    assert tuple(c.value for c in AMDCategory) == AMD_RESULTS


# ------------------------------------------------------------ the SDK task, stepped
@pytest.fixture
def sdk_boundary(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], FakeSipJobContext, type[Any]]:
    """Patch the SDK module's LiveKit boundary; return the step log, the job context and the session class."""
    log: list[str] = []
    job = FakeSipJobContext(FakeSipRoom("lkap-call-abc"), log)
    room_cls, session_cls = consult_fakes(log)
    monkeypatch.setattr(sdk_warm, "get_job_context", lambda: job)
    monkeypatch.setattr(sdk_warm.rtc, "Room", room_cls)
    monkeypatch.setattr(sdk_warm, "AgentSession", session_cls)
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret-test-secret-test-secret")
    return log, job, session_cls


def _chat() -> llm.ChatContext:
    chat = llm.ChatContext.empty()
    chat.add_message(role="user", content="My car was hit in a car park and I want to change my claim.")
    chat.add_message(role="assistant", content="I can put you through to the claims desk.")
    return chat


def _task(log: list[str], steps: list[str], *, summary: str | None = "Caller wants to amend a claim.") -> Any:
    task = warm_transfer_task_class()(
        to="+15550009999", route=ROUTE, chat_ctx=_chat(), summary=summary, on_step=steps.append
    )
    task._activity = FakeActivity(FakeVoiceSession(log))
    task._background_audio = FakeHoldPlayer(log)
    return task


async def test_a_warm_transfer_runs_hold_consult_briefing_then_move(
    sdk_boundary: tuple[list[str], FakeSipJobContext, type[Any]],
) -> None:
    log, job, session_cls = sdk_boundary
    steps: list[str] = []
    task = _task(log, steps)

    await task.on_enter()
    assert steps == ["hold", "consult", "briefing"]
    assert log[:5] == ["hold:start", "hold:play", "consult:connect", "consult:brief", "sip:dial"]
    # The hold music is the SDK's built-in clip, looped.
    assert task._hold_audio.source == BuiltinAudioClip.HOLD_MUSIC
    # The consult call goes through the api-vetted route, into the private room, and waits for an answer.
    dial = job.api.dials[0]
    assert (dial.sip_trunk_id, dial.sip_call_to, dial.sip_number) == (
        "ST_out_1",
        "+15550009999",
        "+15551230000",
    )
    assert dial.room_name == "lkap-call-abc-human-agent" and dial.wait_until_answered
    # The briefing: the private agent's instructions carry the conversation and LKAP's summary.
    instructions = session_cls.sessions[0].agent.instructions
    briefing = instructions if isinstance(instructions, str) else instructions.render()
    assert "car park" in briefing and "Caller wants to amend a claim." in briefing

    await task._merge_calls()
    assert steps == ["hold", "consult", "briefing", "move"]
    move = job.api.moves[0]
    assert (move.identity, move.destination_room) == ("human-agent-sip", "lkap-call-abc")
    task._set_result(WarmTransferResult(human_agent_identity="human-agent-sip"))
    assert task.done()
    assert "hold:stop" in log


async def test_nobody_answering_the_consult_call_completes_the_task_with_a_tool_error(
    monkeypatch: pytest.MonkeyPatch, sdk_boundary: tuple[list[str], FakeSipJobContext, type[Any]]
) -> None:
    log, job, _session_cls = sdk_boundary
    job.api.dial_error = TimeoutError("ringing timeout")
    steps: list[str] = []
    task = _task(log, steps)

    await task.on_enter()

    assert steps == ["hold", "consult"]  # never briefed, never moved
    assert task.done() and job.api.moves == []
    assert "hold:stop" in log  # the caller is taken off hold
    # `await task` only works inside a tool call; the task's own future holds the error.
    error = task._AgentTask__fut.exception()
    assert isinstance(error, ToolError) and "could not dial" in str(error)


async def test_run_warm_transfer_maps_the_task_errors() -> None:
    class _Declines:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def __await__(self) -> Any:
            async def _run() -> None:
                raise ToolError("human agent declined to connect: busy")

            return _run().__await__()

    class _Connects(_Declines):
        def __await__(self) -> Any:
            async def _run() -> WarmTransferResult:
                return WarmTransferResult(human_agent_identity="human-agent-sip")

            return _run().__await__()

    request = WarmRequest(to="+15550009999", label="Claims", summary=None, chat_ctx=None, route=ROUTE)
    assert await run_warm_transfer(request, task_class=_Declines) == WarmResult(
        ok=False, outcome="declined", reason="human agent declined to connect: busy"
    )
    assert (await run_warm_transfer(request, task_class=_Connects)).outcome == "connected"


# ------------------------------------------------------------ hand_over: gating
class FakeWarm:
    """A warm-transfer runner: records the request and answers a canned result."""

    def __init__(self, result: WarmResult | None = None) -> None:
        self.result = result or WarmResult(ok=True, outcome="connected")
        self.requests: list[WarmRequest] = []

    async def __call__(self, request: WarmRequest) -> WarmResult:
        self.requests.append(request)
        return self.result


def _session(
    *,
    cloud: bool = True,
    route: WarmTransferRoute | None = ROUTE,
    warm: FakeWarm | None = None,
    api: FakeApi | None = None,
) -> tuple[TelephonySession, FakePackSessionContext, FakeApi, list[tuple[str, dict[str, Any]]], FakeWarm]:
    room = FakeSipRoom()
    ctx = FakePackSessionContext(room=cast(rtc.Room, room))
    api = api or FakeApi()
    events: list[tuple[str, dict[str, Any]]] = []
    runner = warm or FakeWarm()
    session = TelephonySession(
        room=cast(rtc.Room, room),
        session_id="sess-1",
        channel="sip_in",
        pack_ctx=ctx,
        pack=None,
        api=api,
        record_event=lambda kind, payload: events.append((kind, payload)),
        dtmf_to_model=False,
        panel=HANDOFF_PANEL,
        cloud=cloud,
        warm_route=route,
        warm_runner=runner,
    )
    return session, ctx, api, events, runner


def _handoff_states(ctx: FakePackSessionContext) -> list[str]:
    return [op["status"] for block_id, op in ctx.ui.block_sets if block_id == "handoff"]  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _record_block_sets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every `set_block` of the shared fake UI channel, in order."""
    from lkap_testing.fake_ctx import FakeUiChannel

    original = FakeUiChannel.set_block

    async def recording(self: Any, block_id: str, state: dict[str, Any]) -> None:
        if not hasattr(self, "block_sets"):
            self.block_sets = []
        self.block_sets.append((block_id, dict(state)))
        await original(self, block_id, state)

    monkeypatch.setattr(FakeUiChannel, "set_block", recording)


async def test_warm_on_cloud_runs_the_warm_task_and_reports_it_on_the_call() -> None:
    session, ctx, api, events, warm = _session()
    request = HandOver(label="Claims desk", to="+15550009999", mode="warm", summary="  Amend a claim. ")

    result = await session.hand_over(request)

    assert (result.ok, result.mode, result.outcome) == (True, "warm", "connected")
    assert warm.requests[0].summary == "Amend a claim." and warm.requests[0].route == ROUTE
    assert api.transfers == []  # no SIP REFER on a warm transfer
    assert _handoff_states(ctx) == ["requested", "connecting", "connected"]
    report = api.call_reports[-1]
    assert (report.status, report.transfer_mode, report.transfer_to, report.transfer_summary) == (
        "transferred",
        "warm",
        "+15550009999",
        "Amend a claim.",
    )
    (event,) = [payload for kind, payload in events if kind == "transfer"]
    assert (event["mode"], event["requested_mode"], event["outcome"], event["target"]) == (
        "warm",
        "warm",
        "connected",
        "Claims desk",
    )


@pytest.mark.parametrize(
    ("cloud", "route", "why"),
    [
        (False, ROUTE, "LiveKit Cloud"),
        (True, None, "no outbound line"),
        (True, WarmTransferRoute(trunk_id="ST_out_1", targets=["+15550001111"]), "not allowed"),
    ],
)
async def test_warm_off_cloud_or_without_a_route_falls_back_to_cold_and_keeps_the_summary(
    cloud: bool, route: WarmTransferRoute | None, why: str
) -> None:
    session, ctx, api, events, warm = _session(cloud=cloud, route=route)

    result = await session.hand_over(
        HandOver(label="Claims desk", to="+15550009999", mode="warm", summary="Amend a claim.")
    )

    assert (result.ok, result.mode) == (True, "cold")
    assert warm.requests == []
    assert api.transfers == [("sess-1", "+15550009999", "sip-call1")]
    assert _handoff_states(ctx) == ["requested", "connecting", "ended"]
    info = [payload["message"] for kind, payload in events if kind == "info"]
    assert len(info) == 1 and why in info[0]
    event = next(payload for kind, payload in events if kind == "transfer")
    assert (event["mode"], event["requested_mode"], event["summary"]) == ("cold", "warm", "Amend a claim.")
    report = api.call_reports[-1]
    assert (report.transfer_mode, report.transfer_summary) == ("cold", "Amend a claim.")


async def test_a_warm_timeout_resumes_the_caller_with_the_block_at_timeout() -> None:
    session, ctx, api, events, _warm = _session(
        warm=FakeWarm(WarmResult(ok=False, outcome="timeout", reason="could not dial human agent"))
    )

    result = await session.hand_over(HandOver(label="Claims desk", to="+15550009999", mode="warm"))

    assert (result.ok, result.outcome) == (False, "timeout")
    assert _handoff_states(ctx) == ["requested", "connecting", "timeout"]
    assert ctx.ui.state.blocks["handoff"]["reason"] == "Nobody answered."
    assert [r.status for r in api.call_reports] == []  # nothing transferred
    assert next(p for k, p in events if k == "transfer")["outcome"] == "timeout"


async def test_the_tool_raises_a_tool_error_on_a_warm_timeout_and_keeps_the_call() -> None:
    session, ctx, _api, _events, _warm = _session(
        warm=FakeWarm(WarmResult(ok=False, outcome="timeout", reason="could not dial human agent"))
    )
    ended: list[str] = []
    tool = build_telephony_tools(
        session,
        ctx,
        disabled=[],
        dtmf_enabled=False,
        targets={"Claims desk": "+15550009999"},
        modes={"Claims desk": "warm"},
        shutdown=ended.append,
    )[0]

    class _Run:
        class function_call:  # noqa: N801 - mirrors RunContext
            call_id = "fc-1"

    with pytest.raises(ToolError, match="did not connect"):
        await tool(cast(Any, _Run()), destination="claims desk", summary="Amend a claim.")
    assert ended == []


async def test_the_tool_ends_the_job_after_a_warm_transfer_connects() -> None:
    session, ctx, _api, _events, warm = _session()
    ended: list[str] = []
    tool = build_telephony_tools(
        session,
        ctx,
        disabled=[],
        dtmf_enabled=False,
        targets={"Claims desk": "+15550009999"},
        modes={"Claims desk": "warm"},
        shutdown=ended.append,
    )[0]

    class _Run:
        class function_call:  # noqa: N801 - mirrors RunContext
            call_id = "fc-1"

    assert await tool(cast(Any, _Run()), destination="Claims desk") == "Transferred."
    assert ended == ["call transferred"]
    assert warm.requests[0].label == "Claims desk"
    assert ctx.session.said[-1].startswith("You're now connected")  # type: ignore[attr-defined]


def test_cold_is_every_target_s_default_mode() -> None:
    telephony = TelephonyConfig(
        transfer_targets=[
            TransferTarget(label="Sales", to="+15550001111"),
            TransferTarget(label="Claims", to="+15550009999", mode="warm"),
        ]
    )
    assert transfer_modes(telephony) == {"Sales": "cold", "Claims": "warm"}


def test_session_for_reads_the_cloud_flag_and_the_route_from_the_resolved_document() -> None:
    resolved = resolved_config(channel="sip_in").model_copy(update={"warm_transfer": ROUTE})
    room = FakeSipRoom()
    ctx = FakePackSessionContext(room=cast(rtc.Room, room))
    session = session_for(
        room=cast(rtc.Room, room),
        resolved=resolved,
        pack_ctx=ctx,
        pack=None,
        record_event=lambda *_: None,
        api=FakeApi(),
    )
    assert session is not None
    assert session.warm_unavailable("+15550009999") is None
    self_hosted = resolved.model_copy(
        update={"connection": resolved.connection.model_copy(update={"deployment_type": "self_hosted"})}
    )
    other = session_for(
        room=cast(rtc.Room, room),
        resolved=self_hosted,
        pack_ctx=ctx,
        pack=None,
        record_event=lambda *_: None,
        api=FakeApi(),
    )
    assert other is not None and "Cloud" in (other.warm_unavailable("+15550009999") or "")


async def test_a_refused_cold_transfer_sets_the_block_to_timeout_with_a_plain_reason() -> None:
    api = FakeApi(transfer_result=InternalTransferOut(ok=False, status="refused", reason="not allowed"))
    session, ctx, _api, _events, _warm = _session(api=api)

    result = await session.hand_over(HandOver(label="Sales", to="+15550001111"))

    assert (result.ok, result.outcome) == (False, "refused")
    assert _handoff_states(ctx) == ["requested", "connecting", "timeout"]
    assert ctx.ui.state.blocks["handoff"]["reason"] == "That transfer is not allowed."


# ------------------------------------------------------------ answering-machine detection
def _runner(
    verdict: str = "human",
    *,
    config: AmdConfig | None = None,
    fail: bool = False,
    log: list[str] | None = None,
) -> tuple[AmdRunner, FakeAMDFactory, FakeApi, list[tuple[str, dict[str, Any]]], list[str], FakeVoiceSession]:
    log = log if log is not None else []
    factory = FakeAMDFactory(FakeAMD(verdict, fail=fail, log=log))
    api = FakeApi()
    events: list[tuple[str, dict[str, Any]]] = []
    hung: list[str] = []
    session = FakeVoiceSession(log)

    async def _hang_up(reason: str) -> None:
        log.append("hangup")
        hung.append(reason)

    runner = AmdRunner(
        session=session,
        config=config or AmdConfig(enabled=True),
        session_id="sess-1",
        participant_identity="sip-call1",
        api=api,
        record_event=lambda kind, payload: events.append((kind, payload)),
        hang_up=_hang_up,
        factory=factory,
    )
    return runner, factory, api, events, hung, session


async def test_amd_voicemail_with_leave_message_speaks_then_hangs_up_and_posts_the_result() -> None:
    config = AmdConfig(
        enabled=True, on_machine="leave_message", message="Please call Example Insurance back."
    )
    runner, factory, api, events, hung, _session = _runner("machine-vm", config=config)
    await runner.start()

    assert await runner.run() == "machine-vm"

    assert factory.calls[0]["participant_identity"] == "sip-call1"
    assert factory.calls[0]["ivr_detection"] is False
    assert runner._detector is None
    log = factory.detector.log
    assert log == [
        "amd:enter",
        "amd:execute",
        "say:Please call Example Insurance back.",
        "playout:Please call Example Insurance back.",
        "amd:exit",
        "hangup",
    ]
    assert [(r.status, r.amd_result) for r in api.call_reports] == [("answered", "machine-vm")]
    assert events == [
        ("voicemail", {"result": "machine-vm", "action": "leave_message", "message_left": True})
    ]
    assert hung == ["answering machine (machine-vm)"]


async def test_amd_voicemail_without_a_message_uses_the_default_line() -> None:
    runner, _f, _api, _e, _h, session = _runner(
        "machine-vm", config=AmdConfig(enabled=True, on_machine="leave_message")
    )
    await runner.start()
    await runner.run()
    assert session.said == [DEFAULT_VOICEMAIL_MESSAGE]


@pytest.mark.parametrize(
    ("verdict", "config", "action"),
    [
        ("machine-vm", AmdConfig(enabled=True), "hangup"),
        ("machine-unavailable", AmdConfig(enabled=True, on_machine="leave_message"), "hangup"),
        ("machine-ivr", AmdConfig(enabled=True), "hangup"),
    ],
)
async def test_amd_machines_are_hung_up_on(verdict: str, config: AmdConfig, action: str) -> None:
    runner, _f, api, events, hung, session = _runner(verdict, config=config)
    await runner.start()
    await runner.run()
    assert session.said == []
    assert events == [("voicemail", {"result": verdict, "action": action, "message_left": False})]
    assert hung and api.call_reports[0].amd_result == verdict


@pytest.mark.parametrize("verdict", ["human", "uncertain"])
async def test_amd_a_person_or_an_unsure_verdict_lets_the_call_go_on(verdict: str) -> None:
    runner, factory, api, events, hung, _session = _runner(verdict)
    await runner.start()
    assert await runner.run() == verdict
    assert hung == [] and events == []
    assert factory.detector.log == ["amd:enter", "amd:execute", "amd:exit"]
    assert api.call_reports[0].amd_result == verdict


async def test_amd_ivr_detection_toggles_the_detector_option_and_lets_the_agent_navigate() -> None:
    runner, factory, _api, events, hung, _session = _runner(
        "machine-ivr", config=AmdConfig(enabled=True, ivr_detection=True)
    )
    await runner.start()
    await runner.run()
    assert factory.calls[0]["ivr_detection"] is True
    assert events == [("voicemail", {"result": "machine-ivr", "action": "navigate", "message_left": False})]
    assert hung == []


async def test_amd_that_breaks_never_ends_the_call() -> None:
    runner, factory, api, events, hung, _session = _runner(fail=True)
    await runner.start()
    assert await runner.run() is None
    assert hung == [] and events == [] and api.call_reports == []
    assert factory.detector.log[-1] == "amd:exit"


def test_amd_applies_only_to_outbound_calls_that_ask_for_it() -> None:
    on = TelephonyConfig(amd=AmdConfig(enabled=True))
    out = resolved_config(channel="sip_out")
    out = out.model_copy(update={"config": out.config.model_copy(update={"telephony": on})})
    assert amd_config_for(out) == on.amd
    inbound = out.model_copy(update={"channel": "sip_in"})
    assert amd_config_for(inbound) is None
    assert amd_config_for(resolved_config(channel="sip_out")) is None  # off by default


def test_amd_needs_a_text_llm() -> None:
    class _Realtime:
        pass

    assert amd_supported(FakeVoiceSession(llm=_Realtime())) is False
    assert amd_supported(FakeVoiceSession(llm=None)) is False


async def test_hang_up_deletes_the_room_before_ending_the_job() -> None:
    job = FakeSipJobContext(FakeSipRoom("lkap-call-abc"))
    await hang_up(job, "answering machine (machine-vm)")
    assert job.log == ["job:delete_room:lkap-call-abc", "job:shutdown"]


async def test_wait_for_sip_participant_returns_a_ringing_leg_and_none_when_the_room_closes() -> None:
    room = FakeSipRoom()
    assert await wait_for_sip_participant(cast(rtc.Room, room), timeout_s=0.1) is not None
    empty = FakeSipRoom()
    empty.remote_participants.clear()
    waiter = asyncio.create_task(wait_for_sip_participant(cast(rtc.Room, empty), timeout_s=1.0))
    await asyncio.sleep(0)
    empty.emit("disconnected", "room deleted")
    assert await waiter is None


def test_the_module_keeps_the_beta_import_lazy() -> None:
    assert "WarmTransferTask" not in vars(telephony_module)


async def test_the_default_detector_reuses_the_agent_s_own_models() -> None:
    """`llm=None` / `stt=None`: never the LiveKit Inference models the SDK would pick on Cloud."""
    detector = default_amd_factory(FakeVoiceSession(), participant_identity="sip-call1", ivr_detection=True)
    assert isinstance(detector, AMD)
    assert detector._llm_config is None and detector._stt is None
    assert detector._ivr_detection is True
    assert detector._participant_identity == "sip-call1"
