"""V6-23 (D-V6-20): the `signature`, `chart`, `timer`, `code` and `cart` blocks in the worker.

The **real** `UiChannel` over a fake room: a signature request (the Sign / Not now answer, the
picture through the canvas snapshot path, the `signature` event with the wording's hash, the
barge-in cancel of R-V5-1, a late answer dropped), the four display tools and their bounds,
`update_block` on the three blocks it may now write, `describe_panel`'s five cases, the silent
list, and the parity and compatibility pins. No network, no paid call.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast

import pytest
from fakes.fake_ctx import FakeBackgroundRunner, FakeLogger, FakePackSessionContext, default_agent_config
from fakes.fake_room import FakeRemoteParticipant
from livekit.agents import RunContext, ToolError
from livekit.agents.llm import ToolContext
from livekit.agents.llm._provider_format import google as google_format
from lkap_contracts import tools as shared
from lkap_contracts.agent_config import PanelLayout
from lkap_contracts.ui_protocol import (
    RPC_AGENT_ACTION,
    RPC_UI_REQUEST,
    SIGNATURE_EVENT,
    TIMER_ENDED_EVENT,
    AgentAction,
    BlockSpec,
    CartBlockState,
    ChartBlockState,
    SignatureBlockState,
    SignatureEvent,
    TimerBlockState,
)
from test_canvas_v6_12 import PNG, InkRoom
from test_ui_blocks_agent import _agent, _config, _RecordingSession, _tools_executed, _user_state
from test_upload import JPEG, FakeAssetApi, FakeReader

from lkap_agent.tools.builtin import build_builtin_tools
from lkap_agent.tools.builtin import start_timer as timer_module
from lkap_agent.tools.builtin.cart_set import CartAdjustmentIn, CartLineIn, build_cart_set_tool
from lkap_agent.tools.builtin.describe_panel import describe_panel_state
from lkap_agent.tools.builtin.request_signature import (
    NO_PICTURE,
    NOT_SIGNED,
    build_request_signature_tool,
)
from lkap_agent.tools.builtin.show_chart import ChartPointIn, build_show_chart_tool
from lkap_agent.tools.builtin.show_code import build_show_code_tool
from lkap_agent.tools.builtin.start_timer import build_start_timer_tool
from lkap_agent.tools.builtin.update_block import build_update_block_tool
from lkap_agent.ui.blocks import initial_block_state
from lkap_agent.ui.channel import BARGE_IN, STATE_DELTA_BLOCK_TYPES, UiChannel

CALLER = "web-ui"
WORDING = "I accept the repair estimate of 1,240.00 USD and agree to the work starting on Monday."
SIGN = BlockSpec(id="sign", type="signature", title="Sign here")
CHART = BlockSpec(id="chart", type="chart", config={"kind": "line"})
TIMER = BlockSpec(id="clock", type="timer", config={"max_seconds": 600})
CODE = BlockSpec(id="snippet", type="code", config={"max_chars": 200})
CART = BlockSpec(id="order", type="cart", config={"currency": "EUR", "max_lines": 3})
NEXT = [SIGN, CHART, TIMER, CODE, CART]
NEXT_TOOLS = {"request_signature", "show_chart", "start_timer", "show_code", "cart_set"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class _Call:
    call_id: str = "call-1"


@dataclass
class _RunCtx:
    function_call: _Call


def _run_ctx(call_id: str = "call-1") -> RunContext[Any]:
    return cast(RunContext[Any], _RunCtx(_Call(call_id)))


@dataclass
class Setup:
    ctx: FakePackSessionContext
    ui: UiChannel
    room: InkRoom
    api: FakeAssetApi
    events: list[tuple[str, dict[str, Any]]]


def _setup(
    blocks: list[BlockSpec] | None = None,
    *,
    mode: Any = "cascaded",
    channel: str = "web",
    snapshot_answer: dict[str, Any] | None = None,
) -> Setup:
    room = InkRoom()
    room.add_remote_participant(FakeRemoteParticipant(CALLER))
    room.local_participant.rpc_call_responses[RPC_UI_REQUEST] = json.dumps(
        snapshot_answer if snapshot_answer is not None else {"ok": True, "payload": {}}
    )
    api = FakeAssetApi()
    events: list[tuple[str, dict[str, Any]]] = []
    ui = UiChannel(room, "sess-1", asset_api=api)  # type: ignore[arg-type]
    ui.start()
    specs = blocks if blocks is not None else NEXT
    ui.init_blocks(specs)
    ui.bind(record_event=lambda kind, payload: events.append((kind, payload)))
    config = default_agent_config(panel=PanelLayout(blocks=specs))
    ctx = FakePackSessionContext(
        pipeline_mode=mode, config=config, ui=cast(Any, ui), room=cast(Any, room), log=FakeLogger()
    )
    ctx.channel = channel  # type: ignore[attr-defined]
    return Setup(ctx, ui, room, api, events)


async def _until(predicate: Any) -> None:
    for _ in range(400):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never became true")


async def _block_submit(s: Setup, block_id: str, values: dict[str, Any] | None) -> None:
    payload: dict[str, Any] = {"block_id": block_id}
    payload.update({"values": values} if values is not None else {"cancelled": True})
    raw = AgentAction(action="block_submit", payload=payload).model_dump_json()
    await s.room.local_participant.invoke_rpc(RPC_AGENT_ACTION, raw, caller_identity=CALLER)


def _snapshot_asked(s: Setup, block_id: str = "sign") -> bool:
    return any(
        json.loads(c.payload) == {"v": 1, "method": "snapshot", "payload": {"block_id": block_id}}
        for c in s.room.local_participant.rpc_calls
    )


def _signature_events(events: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [payload for kind, payload in events if kind == SIGNATURE_EVENT]


def _sign(s: Setup) -> SignatureBlockState:
    return SignatureBlockState.model_validate(s.ui.state.blocks["sign"])


# ================================================================ parity and compatibility


def test_the_five_blocks_register_exactly_their_tools_and_update_block() -> None:
    for spec, tool in zip(
        NEXT, ["request_signature", "show_chart", "start_timer", "show_code", "cart_set"], strict=True
    ):
        s = _setup([spec])
        names = {t.info.name for t in build_builtin_tools(s.ctx, disabled=[], http_enabled=False)}
        block_tools = names & set(shared.BLOCK_TOOL_NAMES)
        expected = {tool, "describe_panel"} | (
            {"update_block"} if spec.type in {"chart", "code", "cart"} else set()
        )
        assert block_tools == expected, spec.type


def test_a_panel_without_the_five_blocks_registers_the_same_tools_as_before() -> None:
    """The compatibility pin (§0.1): no existing panel gains or loses a tool."""
    older = sorted(
        set().union(*shared.BLOCK_TOOL_TYPES.values()) - {"signature", "chart", "timer", "code", "cart"}
    )
    s = _setup([BlockSpec(id=f"b_{t}", type=t) for t in older])  # type: ignore[arg-type]
    names = {t.info.name for t in build_builtin_tools(s.ctx, disabled=[], http_enabled=False)}
    assert names & set(shared.BLOCK_TOOL_NAMES) == set(shared.BLOCK_TOOL_NAMES) - NEXT_TOOLS


def test_builtin_disabled_switches_the_five_tools_off() -> None:
    s = _setup()
    names = {t.info.name for t in build_builtin_tools(s.ctx, disabled=sorted(NEXT_TOOLS), http_enabled=False)}
    assert not names & NEXT_TOOLS


@pytest.mark.parametrize("use_parameters_json_schema", [True, False])
def test_the_five_tool_schemas_have_no_property_less_objects_for_gemini(
    use_parameters_json_schema: bool,
) -> None:
    s = _setup()
    tools = [
        t for t in build_builtin_tools(s.ctx, disabled=[], http_enabled=False) if t.info.name in NEXT_TOOLS
    ]
    assert {t.info.name for t in tools} == NEXT_TOOLS
    declarations = google_format.to_fnc_ctx(
        ToolContext(tools), use_parameters_json_schema=use_parameters_json_schema
    )

    def walk(schema: Any) -> None:
        if isinstance(schema, dict):
            if str(schema.get("type")).upper().endswith("OBJECT"):
                assert schema.get("properties"), schema
            for value in schema.values():
                walk(value)
        elif isinstance(schema, list):
            for value in schema:
                walk(value)

    for declaration in declarations:
        walk(declaration.get("parameters_json_schema") or declaration.get("parameters"))


def test_config_keys_seed_the_starting_state() -> None:
    wording = BlockSpec(id="sign", type="signature", config={"disclosure_text": WORDING})
    assert initial_block_state(wording)["disclosure_text"] == WORDING
    assert initial_block_state(CHART)["kind"] == "line"
    assert (
        initial_block_state(BlockSpec(id="t", type="timer", config={"mode": "elapsed"}))["mode"] == "elapsed"
    )
    assert initial_block_state(CART)["currency"] == "EUR"
    assert initial_block_state(CODE) == {"code": "", "language": None, "title": None, "updated_at": None}


# ================================================================ request_signature


async def test_a_signature_is_signed_stored_as_a_signature_asset_and_recorded_with_the_wording_hash() -> None:
    s = _setup()
    task = asyncio.create_task(
        build_request_signature_tool(s.ctx)(context=_run_ctx(), disclosure_text=WORDING)
    )
    await _until(lambda: s.ui.pending_requests == {"sign": "request"})
    assert _sign(s).disclosure_text == WORDING and _sign(s).status == "requested"

    await _block_submit(s, "sign", {"signed": True, "asset_id": "forged", "text_hash": "0" * 64})
    await _until(lambda: _snapshot_asked(s))
    await s.ui.receive_upload(FakeReader(PNG, block_id="sign", name="sig.png"), CALLER)

    assert await task == "The caller signed on screen; the signature is saved with the call. Continue."
    (posted,) = s.api.posted
    assert posted.kind == "signature" and posted.meta == {"block_id": "sign", "source": "signature"}
    state = _sign(s)
    assert (state.status, state.signed, state.text_hash) == ("submitted", True, _sha(WORDING))
    assert state.asset_id is not None and state.asset_id != "forged"
    (event,) = _signature_events(s.ctx.events)
    assert (
        event
        == SignatureEvent(
            block_id="sign", signed=True, text_hash=_sha(WORDING), asset_id=state.asset_id
        ).model_dump()
    )
    (ref,) = s.ui.state.assets
    assert ref.kind == "signature" and ref.stored


async def test_a_signature_request_cancels_on_barge_in() -> None:
    """R-V5-1: the caller speaking over the request withdraws it, like the other generic requests."""
    session = _RecordingSession()
    agent, channel, room, events = _agent(_config(PanelLayout(blocks=[SIGN])), session=session)
    room.local_participant.rpc_call_responses[RPC_UI_REQUEST] = json.dumps({"ok": True, "payload": {}})
    [(_, on_user_state)] = session.handlers
    agent.context.log = FakeLogger()
    tool = build_request_signature_tool(agent.context)
    task = asyncio.create_task(tool(context=_run_ctx(), disclosure_text=WORDING))
    await _until(lambda: channel.pending_requests == {"sign": "request"})

    on_user_state(_user_state("speaking"))

    assert await task == NOT_SIGNED
    await _until(lambda: channel.state.blocks["sign"]["status"] == "cancelled")
    assert channel.state.blocks["sign"]["signed"] is None
    assert _signature_events(events) == []
    assert (
        "block_update",
        {"block_id": "sign", "block_type": "signature", "op": "block_cancelled", "reason": "barge_in"},
    ) in events
    assert not any(json.loads(c.payload)["method"] == "snapshot" for c in room.local_participant.rpc_calls)
    del agent


async def test_a_barge_in_through_the_channel_leaves_nothing_signed() -> None:
    s = _setup()
    task = asyncio.create_task(
        build_request_signature_tool(s.ctx)(context=_run_ctx(), disclosure_text=WORDING)
    )
    await _until(lambda: s.ui.pending_requests == {"sign": "request"})
    assert s.ui.cancel_pending(BARGE_IN, methods=["request"]) == ["sign"]
    assert await task == NOT_SIGNED
    assert _signature_events(s.ctx.events) == [] and s.api.posted == []


async def test_not_now_is_recorded_as_declined_without_a_picture() -> None:
    s = _setup()
    task = asyncio.create_task(
        build_request_signature_tool(s.ctx)(context=_run_ctx(), disclosure_text=WORDING)
    )
    await _until(lambda: s.ui.pending_requests == {"sign": "request"})
    await _block_submit(s, "sign", {"signed": False})
    assert await task == "The caller chose not to sign. Respect it and carry on."
    assert not _snapshot_asked(s) and s.api.posted == []
    state = _sign(s)
    assert (state.signed, state.asset_id, state.text_hash) == (False, None, _sha(WORDING))
    (event,) = _signature_events(s.ctx.events)
    assert (event["signed"], event["asset_id"]) == (False, None)


async def test_a_sign_whose_picture_never_arrives_records_nothing() -> None:
    s = _setup(snapshot_answer={"ok": False, "payload": {"error": "no board"}})
    task = asyncio.create_task(
        build_request_signature_tool(s.ctx)(context=_run_ctx(), disclosure_text=WORDING)
    )
    await _until(lambda: s.ui.pending_requests == {"sign": "request"})
    await _block_submit(s, "sign", {"signed": True})
    assert await task == NO_PICTURE
    assert _signature_events(s.ctx.events) == []
    assert _sign(s).signed is None and _sign(s).status == "cancelled"


async def test_a_signature_picture_that_is_not_a_png_is_refused() -> None:
    s = _setup()
    task = asyncio.create_task(
        build_request_signature_tool(s.ctx)(context=_run_ctx(), disclosure_text=WORDING)
    )
    await _until(lambda: s.ui.pending_requests == {"sign": "request"})
    await _block_submit(s, "sign", {"signed": True})
    await _until(lambda: _snapshot_asked(s))
    await s.ui.receive_upload(FakeReader(JPEG, block_id="sign", name="sig.jpg"), CALLER)
    assert await task == NO_PICTURE
    assert s.api.posted == [] and _signature_events(s.ctx.events) == []


async def test_an_unrequested_signature_picture_is_refused() -> None:
    s = _setup()
    await s.ui.receive_upload(FakeReader(PNG, block_id="sign"), CALLER)
    assert s.api.posted == []
    assert (
        "block_update",
        {"block_id": "sign", "op": "file_rejected", "reason": "not_requested"},
    ) in s.events


async def test_a_signature_answer_with_no_request_waiting_is_dropped() -> None:
    s = _setup()
    unsolicited: list[tuple[str, dict[str, Any]]] = []

    async def _late(block_id: str, values: dict[str, Any]) -> None:
        unsolicited.append((block_id, values))

    s.ui.bind(on_unsolicited_form=_late)
    before = dict(s.ui.state.blocks["sign"])
    await _block_submit(s, "sign", {"signed": True})
    assert s.ui.state.blocks["sign"] == before
    assert unsolicited == []
    assert (
        "block_update",
        {"block_id": "sign", "block_type": "signature", "op": "late_answer_dropped"},
    ) in s.events


async def test_the_block_wording_wins_over_the_models() -> None:
    fixed = BlockSpec(id="sign", type="signature", config={"disclosure_text": WORDING})
    s = _setup([fixed])
    task = asyncio.create_task(
        build_request_signature_tool(s.ctx)(context=_run_ctx(), disclosure_text="I agree to pay 1 million.")
    )
    await _until(lambda: s.ui.pending_requests == {"sign": "request"})
    assert _sign(s).disclosure_text == WORDING
    await _block_submit(s, "sign", {"signed": False})
    await task
    (event,) = _signature_events(s.ctx.events)
    assert event["text_hash"] == _sha(WORDING)


@pytest.mark.parametrize(
    ("wording", "message"), [("", "wording"), ("  \x00 ", "wording"), ("x" * 2001, "2000")]
)
async def test_a_request_without_usable_wording_is_refused(wording: str, message: str) -> None:
    s = _setup()
    with pytest.raises(ToolError, match=message):
        await build_request_signature_tool(s.ctx)(context=_run_ctx(), disclosure_text=wording)
    assert s.ui.pending_requests == {}


async def test_control_characters_are_stripped_from_the_models_wording() -> None:
    s = _setup()
    task = asyncio.create_task(
        build_request_signature_tool(s.ctx)(
            context=_run_ctx(), disclosure_text="I agree\x07 to the terms.\x1b"
        )
    )
    await _until(lambda: s.ui.pending_requests == {"sign": "request"})
    assert _sign(s).disclosure_text == "I agree to the terms."
    s.ui.cancel_pending("test")
    await task


@pytest.mark.parametrize("channel", ["sip_in", "sip_out", "text"])
async def test_nothing_can_be_signed_on_a_phone_call_or_a_text_chat(channel: str) -> None:
    s = _setup(channel=channel)
    answer = await build_request_signature_tool(s.ctx)(context=_run_ctx(), disclosure_text=WORDING)
    assert answer is not None and json.loads(answer)["signed"] is False
    assert s.ui.pending_requests == {} and _sign(s).disclosure_text == ""
    assert _signature_events(s.ctx.events) == []


@pytest.mark.parametrize("mode", ["realtime", "half_cascade"])
async def test_realtime_returns_none_and_the_signature_arrives_as_an_urgent_result(mode: Any) -> None:
    s = _setup(mode=mode)
    background = cast(FakeBackgroundRunner, s.ctx.background)
    assert await build_request_signature_tool(s.ctx)(context=_run_ctx(), disclosure_text=WORDING) is None
    await _until(lambda: s.ui.pending_requests == {"sign": "request"})
    await _block_submit(s, "sign", {"signed": True})
    await _until(lambda: _snapshot_asked(s))
    await s.ui.receive_upload(FakeReader(PNG, block_id="sign"), CALLER)
    await background.wait_idle()
    ((job_id, outcome, instructions),) = background.urgent_events
    assert (job_id, outcome["signed"]) == ("call-1", True)
    assert instructions is not None and "signed" in instructions


@pytest.mark.parametrize(
    ("mode", "silenced"), [("realtime", True), ("half_cascade", True), ("cascaded", False)]
)
def test_the_signature_reply_is_silent_on_realtime_models(mode: Any, silenced: bool) -> None:
    agent, *_ = _agent(_config(PanelLayout(blocks=[SIGN]), mode=mode))
    event = _tools_executed("request_signature")
    agent.on_function_tools_executed(event)
    assert event.has_tool_reply is not silenced


@pytest.mark.parametrize("channel_name", ["sip_in", "sip_out"])
def test_the_signature_reply_is_kept_on_a_phone_call(channel_name: str) -> None:
    from lkap_agent.packs.loader import NullPack  # noqa: PLC0415
    from lkap_agent.platform_agent import PlatformAgent  # noqa: PLC0415

    agent, *_ = _agent(_config(PanelLayout(blocks=[SIGN]), mode="realtime"))
    agent.context.channel = channel_name
    phone_agent = PlatformAgent(ctx=agent.context, pack=NullPack(), has_tts=True)
    event = _tools_executed("request_signature")
    phone_agent.on_function_tools_executed(event)
    assert event.has_tool_reply is True


# ================================================================ show_chart


def _points(n: int) -> list[ChartPointIn]:
    return [ChartPointIn(label=f"d{i}", value=float(i)) for i in range(n)]


async def test_show_chart_draws_the_blocks_kind_and_caps_the_text() -> None:
    s = _setup()
    answer = await build_show_chart_tool(s.ctx)(
        context=_run_ctx(), points=_points(3), title="Claims\x07 by  day", unit="claims"
    )
    assert answer is not None and "on screen" in answer
    state = ChartBlockState.model_validate(s.ui.state.blocks["chart"])
    assert (state.kind, state.title, len(state.points)) == ("line", "Claims by day", 3)


async def test_a_chart_with_201_points_is_refused() -> None:
    s = _setup()
    before = dict(s.ui.state.blocks["chart"])
    with pytest.raises(ToolError, match="at most 200"):
        await build_show_chart_tool(s.ctx)(context=_run_ctx(), points=_points(201))
    assert s.ui.state.blocks["chart"] == before
    await build_show_chart_tool(s.ctx)(context=_run_ctx(), points=_points(200))
    assert len(s.ui.state.blocks["chart"]["points"]) == 200


async def test_a_chart_with_201_points_is_refused_through_update_block() -> None:
    s = _setup()
    points = [{"label": f"d{i}", "value": i} for i in range(201)]
    with pytest.raises(ToolError, match="does not fit"):
        await build_update_block_tool(s.ctx)(
            context=_run_ctx(), block_id="chart", patch=json.dumps({"points": points})
        )
    assert s.ui.state.blocks["chart"]["points"] == []


@pytest.mark.parametrize(
    ("kind", "points", "message"),
    [
        ("gauge", [ChartPointIn(label="a", value=1), ChartPointIn(label="b", value=2)], "one value"),
        ("pie", [ChartPointIn(label="a", value=-1)], "0 or more"),
        ("scatter", [ChartPointIn(label="a", value=1)], "kind"),
    ],
)
async def test_a_chart_that_does_not_fit_its_kind_is_refused(
    kind: str, points: list[ChartPointIn], message: str
) -> None:
    s = _setup()
    with pytest.raises(ToolError, match=message):
        await build_show_chart_tool(s.ctx)(context=_run_ctx(), points=points, kind=kind)


async def test_update_block_may_change_a_chart_within_its_bounds() -> None:
    s = _setup()
    patch = json.dumps(
        {"kind": "pie", "points": [{"label": "Home", "value": 3}, {"label": "Car", "value": 5}]}
    )
    assert (
        await build_update_block_tool(s.ctx)(context=_run_ctx(), block_id="chart", patch=patch)
        == "Updated chart."
    )
    assert s.ui.state.blocks["chart"]["kind"] == "pie"


# ================================================================ start_timer


async def _no_wait(_seconds: float) -> None:
    await asyncio.sleep(0)


async def test_a_timer_runs_ends_records_timer_ended_and_notes_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(timer_module, "_sleep", _no_wait)
    s = _setup()
    background = cast(FakeBackgroundRunner, s.ctx.background)
    answer = await build_start_timer_tool(s.ctx)(
        context=_run_ctx(), seconds=90, label="Find the policy number"
    )
    assert answer is not None and "1 min 30 s" in answer
    await background.wait_idle()
    state = TimerBlockState.model_validate(s.ui.state.blocks["clock"])
    assert (state.status, state.mode, state.duration_s) == ("ended", "countdown", 90)
    assert state.ended_at is not None
    assert (TIMER_ENDED_EVENT, {"block_id": "clock", "mode": "countdown", "duration_s": 90}) in s.ctx.events
    ((_job, note),) = background.routine_notes
    assert "Find the policy number" in note and "run out" in note
    assert [name for _, name in background.submitted] == ["timer"]


async def test_a_new_timer_replaces_the_running_one_and_the_old_end_never_fires() -> None:
    s = _setup()
    background = cast(FakeBackgroundRunner, s.ctx.background)
    tool = build_start_timer_tool(s.ctx)
    await tool(context=_run_ctx(), seconds=300)
    await asyncio.sleep(0)
    await tool(context=_run_ctx(), seconds=120, mode="elapsed")
    await asyncio.sleep(0)
    first, second = (job for job, _ in background.submitted)
    assert background.cancelled == [first]
    state = TimerBlockState.model_validate(s.ui.state.blocks["clock"])
    assert (state.status, state.mode, state.duration_s) == ("running", "elapsed", 120)
    await tool(context=_run_ctx(), seconds=0)
    assert background.cancelled == [first, second]
    assert s.ui.state.blocks["clock"]["status"] == "stopped"
    await background.wait_idle()
    assert not [kind for kind, _ in s.ctx.events if kind == TIMER_ENDED_EVENT]
    assert background.routine_notes == []


@pytest.mark.parametrize(
    ("seconds", "mode", "label"), [(601, "", ""), (-1, "", ""), (60, "alarm", ""), (60, "", "x" * 81)]
)
async def test_a_timer_outside_its_bounds_is_refused(seconds: int, mode: str, label: str) -> None:
    s = _setup()
    with pytest.raises(ToolError):
        await build_start_timer_tool(s.ctx)(context=_run_ctx(), seconds=seconds, mode=mode, label=label)
    assert s.ui.state.blocks["clock"]["status"] == "idle"


async def test_a_timer_still_runs_on_a_phone_call_and_is_quiet_on_realtime() -> None:
    phone = _setup(channel="sip_in")
    answer = await build_start_timer_tool(phone.ctx)(context=_run_ctx(), seconds=60)
    assert answer is not None and "cannot see" in answer
    assert phone.ui.state.blocks["clock"]["status"] == "running"
    realtime = _setup(mode="realtime")
    assert await build_start_timer_tool(realtime.ctx)(context=_run_ctx(), seconds=60) is None


# ================================================================ show_code


async def test_show_code_keeps_lines_and_strips_other_control_characters() -> None:
    s = _setup()
    answer = await build_show_code_tool(s.ctx)(
        context=_run_ctx(), code='{\n\t"a": 1\x1b[31m\n}\n', language="JSON", title="Record"
    )
    assert answer is not None and "on screen" in answer
    state = s.ui.state.blocks["snippet"]
    assert state["code"] == '{\n\t"a": 1[31m\n}' and state["language"] == "json"


@pytest.mark.parametrize(
    ("code", "language", "message"),
    [("x" * 201, "", "at most 200"), ("print(1)", "<script>", "label"), ("   ", "", "Pass the code")],
)
async def test_show_code_refuses_what_does_not_fit(code: str, language: str, message: str) -> None:
    s = _setup()
    with pytest.raises(ToolError, match=message):
        await build_show_code_tool(s.ctx)(context=_run_ctx(), code=code, language=language)
    assert s.ui.state.blocks["snippet"]["code"] == ""


# ================================================================ cart_set


async def test_cart_set_adds_up_the_totals() -> None:
    s = _setup()
    answer = await build_cart_set_tool(s.ctx)(
        context=_run_ctx(),
        lines=[
            CartLineIn(id="filter", name="Water filter", quantity=3, unit_price=0.1),
            CartLineIn(id="visit", name="Technician visit", unit_price=89),
        ],
        adjustments=[CartAdjustmentIn(label="Discount", amount=-10)],
    )
    assert answer is not None and "79.30 EUR" in answer
    state = CartBlockState.model_validate(s.ui.state.blocks["order"])
    assert [line.line_total for line in state.lines] == [0.3, 89.0]
    assert (state.currency, state.subtotal, state.total) == ("EUR", 89.3, 79.3)


@pytest.mark.parametrize(
    ("lines", "currency", "message"),
    [
        ([CartLineIn(id=f"l{i}", name="x", unit_price=1) for i in range(4)], "", "at most 3"),
        ([CartLineIn(id="a", name="x", unit_price=1)], "euro", "three-letter"),
        ([CartLineIn(id="a", name="x", unit_price=1)] * 2, "", "unique"),
        ([CartLineIn(id="a", name="x", unit_price=-5)], "", "greater than or equal"),
        ([CartLineIn(id="a", name="x", unit_price=1, quantity=0)], "", "greater than or equal"),
    ],
)
async def test_cart_set_refuses_what_does_not_fit(
    lines: list[CartLineIn], currency: str, message: str
) -> None:
    s = _setup()
    with pytest.raises(ToolError, match=message):
        await build_cart_set_tool(s.ctx)(context=_run_ctx(), lines=lines, currency=currency)
    assert s.ui.state.blocks["order"]["lines"] == []


async def test_update_block_recomputes_a_carts_totals_and_refuses_setting_them() -> None:
    s = _setup()
    tool = build_update_block_tool(s.ctx)
    patch = json.dumps(
        {"lines": [{"id": "a", "name": "Pad", "quantity": 2, "unit_price": 4.5, "line_total": 999}]}
    )
    assert await tool(context=_run_ctx(), block_id="order", patch=patch) == "Updated order."
    state = CartBlockState.model_validate(s.ui.state.blocks["order"])
    assert (state.lines[0].line_total, state.subtotal, state.total) == (9.0, 9.0, 9.0)
    with pytest.raises(ToolError, match="cannot be set"):
        await tool(context=_run_ctx(), block_id="order", patch='{"total": 0}')
    with pytest.raises(ToolError, match="lists of objects"):
        await tool(context=_run_ctx(), block_id="order", patch='{"lines": ["a pad"]}')
    four = [{"id": f"l{i}", "name": "x", "unit_price": 1} for i in range(4)]
    with pytest.raises(ToolError, match="at most 3"):
        await tool(context=_run_ctx(), block_id="order", patch=json.dumps({"lines": four}))
    assert s.ui.state.blocks["order"]["total"] == 9.0


@pytest.mark.parametrize("block_type", ["signature", "timer"])
async def test_update_block_refuses_a_signature_and_a_timer(block_type: str) -> None:
    s = _setup()
    target = next(spec.id for spec in NEXT if spec.type == block_type)
    before = dict(s.ui.state.blocks[target])
    with pytest.raises(ToolError, match="request_signature|start_timer"):
        await build_update_block_tool(s.ctx)(
            context=_run_ctx(), block_id=target, patch='{"signed": true, "status": "ended"}'
        )
    assert s.ui.state.blocks[target] == before


@pytest.mark.parametrize("block_id", ["chart", "snippet", "order", "sign", "clock"])
async def test_a_page_state_delta_never_writes_the_five_blocks(block_id: str) -> None:
    s = _setup()
    s.ui.bind(accept_state_delta=True)
    before = json.dumps(s.ui.state.blocks[block_id], sort_keys=True)
    raw = AgentAction(
        action="state_delta",
        payload={"delta": [{"op": "replace", "path": f"/blocks/{block_id}/updated_at", "value": 1.0}]},
    ).model_dump_json()
    answer = json.loads(
        await s.room.local_participant.invoke_rpc(RPC_AGENT_ACTION, raw, caller_identity=CALLER)
    )
    assert answer["ok"] is False
    assert json.dumps(s.ui.state.blocks[block_id], sort_keys=True) == before
    assert not {"chart", "code", "cart", "signature", "timer"} & STATE_DELTA_BLOCK_TYPES


@pytest.mark.parametrize("tool", ["show_chart", "show_code", "cart_set"])
async def test_the_display_tools_show_nothing_on_a_phone_call(tool: str) -> None:
    s = _setup(channel="sip_in")
    before = json.dumps(s.ui.state.blocks, sort_keys=True)
    match tool:
        case "show_chart":
            answer = await build_show_chart_tool(s.ctx)(context=_run_ctx(), points=_points(2))
        case "show_code":
            answer = await build_show_code_tool(s.ctx)(context=_run_ctx(), code="x = 1")
        case _:
            answer = await build_cart_set_tool(s.ctx)(
                context=_run_ctx(), lines=[CartLineIn(id="a", name="x", unit_price=2)]
            )
    assert answer is not None and json.loads(answer)["visible"] is False
    assert json.dumps(s.ui.state.blocks, sort_keys=True) == before


@pytest.mark.parametrize("mode", ["realtime", "half_cascade"])
async def test_the_display_tools_answer_nothing_on_realtime(mode: Any) -> None:
    s = _setup(mode=mode)
    assert await build_show_chart_tool(s.ctx)(context=_run_ctx(), points=_points(2)) is None
    assert await build_show_code_tool(s.ctx)(context=_run_ctx(), code="x = 1") is None
    assert (
        await build_cart_set_tool(s.ctx)(
            context=_run_ctx(), lines=[CartLineIn(id="a", name="x", unit_price=2)]
        )
        is None
    )


# ================================================================ describe_panel


async def test_describe_panel_summarises_the_five_blocks_without_the_code() -> None:
    s = _setup()
    await build_show_chart_tool(s.ctx)(context=_run_ctx(), points=_points(3), title="Claims")
    await build_show_code_tool(s.ctx)(
        context=_run_ctx(), code="SECRET_LOOKING = 1\nprint(2)", language="python"
    )
    await build_cart_set_tool(s.ctx)(
        context=_run_ctx(), lines=[CartLineIn(id="a", name="Pad", quantity=2, unit_price=4.5)]
    )
    await build_start_timer_tool(s.ctx)(context=_run_ctx(), seconds=120, label="Policy number")
    entries = {
        e["id"]: e for e in describe_panel_state(NEXT, s.ui.state.model_dump(mode="json", by_alias=True))
    }
    assert entries["chart"]["kind"] == "line" and entries["chart"]["points"] == 3
    assert entries["chart"]["values"][:2] == ["d0: 0.0", "d1: 1.0"]
    assert entries["snippet"] == {"id": "snippet", "type": "code", "language": "python", "lines": 2}
    assert "SECRET_LOOKING" not in json.dumps(entries)
    assert entries["order"]["lines"] == ["2 x Pad"] and entries["order"]["total"] == 9.0
    assert entries["clock"]["status"] == "running" and 0 < entries["clock"]["seconds_left"] <= 120
    assert entries["sign"] == {"id": "sign", "type": "signature", "title": "Sign here", "status": "idle"}
