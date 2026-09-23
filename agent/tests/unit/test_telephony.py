"""V2-17 worker telephony: caller report, DTMF in/out, transfer, tools (no LiveKit, no api)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, cast

import httpx
import pytest
from fakes.fake_ctx import FakePackSessionContext, default_agent_config
from fakes.fake_room import FakeRemoteParticipant, FakeRoom
from livekit import rtc
from livekit.agents import RunContext
from livekit.agents.llm.utils import build_legacy_openai_schema

from lkap_agent.telephony import (
    DTMF_TOPIC,
    DtmfCollector,
    HttpTelephonyApi,
    TelephonySession,
    TransferResult,
    build_telephony_tools,
    caller_info,
    dtmf_code,
    publish_digits,
    session_for,
    transfer_targets,
)
from lkap_agent.tools.builtin.transfer_call import match_target

SIP = rtc.ParticipantKind.PARTICIPANT_KIND_SIP
SIP_ATTRS = {
    "sip.phoneNumber": "+15557654321",
    "sip.trunkPhoneNumber": "+15551230000",
    "sip.callID": "SCL_1",
    "sip.trunkID": "ST_in_1",
}


# ------------------------------------------------------------------------- fakes
class DtmfLocalParticipant:
    """The local participant's `publish_dtmf`, recorded."""

    def __init__(self) -> None:
        self.identity = "lkap-agent"
        self.published: list[tuple[int, str]] = []

    async def publish_dtmf(self, *, code: int, digit: str) -> None:
        self.published.append((code, digit))


def sip_room(*participants: FakeRemoteParticipant) -> FakeRoom:
    room = FakeRoom("call-room")
    room.local_participant = DtmfLocalParticipant()  # type: ignore[assignment]
    for participant in participants:
        room.add_remote_participant(participant)
    return room


def caller(status: str | None = None, identity: str = "sip_+15557654321") -> FakeRemoteParticipant:
    attrs = dict(SIP_ATTRS)
    if status is not None:
        attrs["sip.callStatus"] = status
    return FakeRemoteParticipant(identity, attributes=attrs, kind=SIP)


@dataclass
class FakeTelephonyApi:
    reports: list[dict[str, Any]] = field(default_factory=list)
    transfers: list[tuple[str, str, str | None]] = field(default_factory=list)
    result: TransferResult = field(default_factory=lambda: TransferResult(ok=True, status="transferred"))

    async def report(self, body: dict[str, Any]) -> None:
        self.reports.append(body)

    async def transfer(self, session_id: str, to: str, participant_identity: str | None) -> TransferResult:
        self.transfers.append((session_id, to, participant_identity))
        return self.result

    async def aclose(self) -> None:
        return None


class NoSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.calls.append(delay)


@dataclass
class _Call:
    call_id: str = "fc-1"


@dataclass
class _Run:
    function_call: _Call = field(default_factory=_Call)


def run_ctx() -> RunContext[Any]:
    return cast(RunContext[Any], _Run())


def make_session(
    room: FakeRoom,
    *,
    channel: str = "sip_in",
    pack: Any = None,
    dtmf_to_model: bool = True,
    api: FakeTelephonyApi | None = None,
) -> tuple[TelephonySession, FakePackSessionContext, FakeTelephonyApi, list[tuple[str, dict[str, Any]]]]:
    ctx = FakePackSessionContext(room=cast(rtc.Room, room))
    events: list[tuple[str, dict[str, Any]]] = []
    fake_api = api or FakeTelephonyApi()
    session = TelephonySession(
        room=cast(rtc.Room, room),
        session_id="sess-1",
        channel=channel,
        pack_ctx=ctx,
        pack=pack,
        api=fake_api,
        record_event=lambda kind, payload: events.append((kind, payload)),
        dtmf_to_model=dtmf_to_model,
        sleep=NoSleep(),
        flush_after_s=0.01,
    )
    return session, ctx, fake_api, events


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


# ------------------------------------------------------------------------- units
@pytest.mark.parametrize(("digit", "code"), [("0", 0), ("9", 9), ("*", 10), ("#", 11), ("A", 12), ("D", 15)])
def test_dtmf_code_maps_rfc4733_events(digit: str, code: int) -> None:
    assert dtmf_code(digit) == code


def test_dtmf_code_rejects_non_keypad_characters() -> None:
    with pytest.raises(ValueError):
        dtmf_code("x")


def test_caller_info_orients_numbers_by_direction() -> None:
    assert caller_info(SIP_ATTRS, channel="sip_in") == {
        "from": "+15557654321",
        "to": "+15551230000",
        "call_id": "SCL_1",
        "trunk_id": "ST_in_1",
    }
    outbound = caller_info(SIP_ATTRS, channel="sip_out")
    assert (outbound["from"], outbound["to"]) == ("+15551230000", "+15557654321")


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({}, {}),
        (
            {"transfer_targets": {"Sales": "+15550001111", "Desk": "sip:desk@pbx.example.com"}},
            {"Sales": "+15550001111", "Desk": "sip:desk@pbx.example.com"},
        ),
        ({"transfer_targets": ["+15550001111"]}, {"+15550001111": "+15550001111"}),
        ({"transfer_targets": {"Bad": "call mom", "Ok": "tel:+15550002222"}}, {"Ok": "tel:+15550002222"}),
        ({"transfer_targets": "+15550001111"}, {}),
    ],
)
def test_transfer_targets_accepts_only_valid_destinations(
    settings: dict[str, Any], expected: dict[str, str]
) -> None:
    assert transfer_targets(settings) == expected


def test_match_target_by_label_or_listed_number_only() -> None:
    targets = {"Sales": "+15550001111"}

    assert match_target(targets, "sales") == "+15550001111"
    assert match_target(targets, "+15550001111") == "+15550001111"
    assert match_target(targets, "+19005550000") is None


async def test_publish_digits_sends_codes_with_gaps() -> None:
    room = sip_room()
    sleep = NoSleep()

    await publish_digits(cast(rtc.Room, room), "1*#", sleep=sleep)

    assert room.local_participant.published == [(1, "1"), (10, "*"), (11, "#")]  # type: ignore[attr-defined]
    assert sleep.calls == [0.3, 0.3]


async def test_publish_digits_invalid_sends_nothing() -> None:
    room = sip_room()

    with pytest.raises(ValueError):
        await publish_digits(cast(rtc.Room, room), "12x")

    assert room.local_participant.published == []  # type: ignore[attr-defined]


async def test_dtmf_collector_flushes_on_terminator_and_on_pause() -> None:
    entries: list[str] = []

    async def on_entry(entry: str) -> None:
        entries.append(entry)

    collector = DtmfCollector(on_entry, flush_after_s=0.01)
    for digit in "12#":
        collector.push(digit)
    await settle()
    collector.push("4")
    collector.push("5")
    assert collector.pending == "45"
    await asyncio.sleep(0.05)
    await collector.aclose()

    assert entries == ["12#", "45"]


# ---------------------------------------------------------------- caller report (#55)
async def test_inbound_leg_present_at_start_reports_answered_with_caller() -> None:
    room = sip_room(caller())
    session, _ctx, api, events = make_session(room)

    session.start()
    await settle()

    assert api.reports == [
        {
            "session_id": "sess-1",
            "status": "answered",
            "direction": "inbound",
            "participant_identity": "sip_+15557654321",
            "sip_call_id": "SCL_1",
            "from_e164": "+15557654321",
            "to_e164": "+15551230000",
        }
    ]
    assert events[0][0] == "sip_answered"
    assert session.participant_identity == "sip_+15557654321"


async def test_outbound_leg_reports_answered_only_when_call_status_turns_active() -> None:
    leg = caller(status="ringing", identity="sip-abc")
    room = sip_room(leg)
    session, _ctx, api, _events = make_session(room, channel="sip_out")

    session.start()
    await settle()
    assert api.reports == []
    leg.attributes["sip.callStatus"] = "active"
    room.emit("participant_attributes_changed", {"sip.callStatus": "active"}, leg)
    room.emit("participant_attributes_changed", {"sip.callStatus": "active"}, leg)
    await settle()

    assert [r["status"] for r in api.reports] == ["answered"]
    assert api.reports[0]["direction"] == "outbound"
    assert (api.reports[0]["from_e164"], api.reports[0]["to_e164"]) == ("+15551230000", "+15557654321")


async def test_leg_joining_later_is_reported_and_close_reports_completed() -> None:
    room = sip_room()
    session, _ctx, api, _events = make_session(room)
    session.start()

    room.add_remote_participant(caller())
    room.emit("participant_connected", room.remote_participants["sip_+15557654321"])
    await settle()
    await session.aclose(reason="caller hung up")
    await session.aclose()

    assert [r["status"] for r in api.reports] == ["answered", "completed"]
    assert api.reports[-1]["reason"] == "caller hung up"


async def test_close_before_answer_reports_failed() -> None:
    session, _ctx, api, _events = make_session(sip_room(caller(status="dialing")), channel="sip_out")
    session.start()

    await session.aclose(reason="no answer")

    assert api.reports == [
        {
            "session_id": "sess-1",
            "status": "failed",
            "participant_identity": "sip_+15557654321",
            "reason": "no answer",
        }
    ]


async def test_standard_participant_is_not_a_call_leg() -> None:
    room = sip_room(FakeRemoteParticipant("user-web"))
    session, _ctx, api, _events = make_session(room)

    session.start()
    await settle()

    assert api.reports == []


# --------------------------------------------------------------------------- DTMF in
async def test_dtmf_received_becomes_a_user_turn_and_a_dtmf_event() -> None:
    leg = caller()
    room = sip_room(leg)
    session, ctx, _api, events = make_session(room)
    session.start()

    for digit in "42#":
        room.emit("sip_dtmf_received", rtc.SipDTMF(code=dtmf_code(digit), digit=digit, participant=leg))  # type: ignore[arg-type]
    await settle()

    assert ("dtmf", {"direction": "received", "digits": "42#"}) in events
    replies = ctx.session.replies_generated  # type: ignore[attr-defined]
    assert len(replies) == 1  # the fake records `instructions`; the turn itself went as user_input


async def test_dtmf_pack_hook_consumes_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    class KeypadPack:
        async def on_dtmf(self, ctx: Any, digits: str) -> bool:
            seen.append(digits)
            return True

    leg = caller()
    room = sip_room(leg)
    session, ctx, _api, events = make_session(room, pack=KeypadPack())
    session.start()

    room.emit("sip_dtmf_received", rtc.SipDTMF(code=1, digit="1", participant=leg))  # type: ignore[arg-type]
    room.emit("sip_dtmf_received", rtc.SipDTMF(code=11, digit="#", participant=leg))  # type: ignore[arg-type]
    await settle()

    assert seen == ["1#"]
    assert ctx.session.replies_generated == []  # type: ignore[attr-defined]
    assert events[-1] == ("dtmf", {"direction": "received", "digits": "1#"})


async def test_dtmf_without_capability_is_only_recorded() -> None:
    leg = caller()
    room = sip_room(leg)
    session, ctx, _api, events = make_session(room, dtmf_to_model=False)
    session.start()

    room.emit("sip_dtmf_received", rtc.SipDTMF(code=11, digit="#", participant=leg))  # type: ignore[arg-type]
    await settle()

    assert ("dtmf", {"direction": "received", "digits": "#"}) in events
    assert ctx.session.replies_generated == []  # type: ignore[attr-defined]


# -------------------------------------------------------------------------- DTMF out
def _packet(
    body: dict[str, Any] | bytes, *, participant: Any = None, topic: str = DTMF_TOPIC
) -> rtc.DataPacket:
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    return rtc.DataPacket(data=data, kind=0, participant=participant, topic=topic)  # type: ignore[arg-type]


async def test_console_dtmf_packet_from_server_is_published() -> None:
    room = sip_room(caller())
    session, _ctx, _api, events = make_session(room)
    session.start()

    room.emit("data_received", _packet({"v": 1, "op": "dtmf", "digits": "9#", "call_id": "c1"}))
    await settle()

    assert room.local_participant.published == [(9, "9"), (11, "#")]  # type: ignore[attr-defined]
    assert ("dtmf", {"direction": "sent", "digits": "9#", "source": "console"}) in events


@pytest.mark.parametrize(
    "packet",
    [
        _packet({"v": 1, "op": "dtmf", "digits": "1"}, participant=object()),  # a room participant sent it
        _packet({"v": 1, "op": "dtmf", "digits": "1"}, topic="lkap.ui.state"),
        _packet({"v": 1, "op": "dtmf", "digits": "1x"}),
        _packet({"v": 1, "op": "hangup"}),
        _packet(b"not json"),
    ],
)
async def test_untrusted_or_malformed_dtmf_packets_are_ignored(packet: rtc.DataPacket) -> None:
    room = sip_room(caller())
    session, _ctx, _api, _events = make_session(room)
    session.start()

    room.emit("data_received", packet)
    await settle()

    assert room.local_participant.published == []  # type: ignore[attr-defined]


async def test_close_unregisters_room_handlers() -> None:
    leg = caller()
    room = sip_room(leg)
    session, _ctx, _api, events = make_session(room)
    session.start()
    await session.aclose()
    events.clear()

    room.emit("sip_dtmf_received", rtc.SipDTMF(code=1, digit="1", participant=leg))  # type: ignore[arg-type]
    room.emit("data_received", _packet({"v": 1, "op": "dtmf", "digits": "1"}))
    await settle()

    assert events == []
    assert room.local_participant.published == []  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------- tools
def _tools(session: TelephonySession, ctx: FakePackSessionContext, **kwargs: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {"disabled": [], "dtmf_enabled": True, "targets": {"Sales": "+15550001111"}}
    defaults.update(kwargs)
    return {tool.id: tool for tool in build_telephony_tools(session, ctx, **defaults)}


@pytest.mark.parametrize(
    ("kwargs", "names"),
    [
        ({}, {"send_dtmf", "transfer_call"}),
        ({"dtmf_enabled": False}, {"transfer_call"}),
        ({"targets": {}}, {"send_dtmf"}),
        ({"disabled": ["transfer_call", "send_dtmf"]}, set()),
    ],
)
def test_build_telephony_tools_gates_on_capability_allowlist_and_disabled(
    kwargs: dict[str, Any], names: set[str]
) -> None:
    session, ctx, _api, _events = make_session(sip_room())

    assert set(_tools(session, ctx, **kwargs)) == names


async def test_send_dtmf_tool_publishes_and_records() -> None:
    room = sip_room(caller())
    session, ctx, _api, events = make_session(room)
    tool = _tools(session, ctx)["send_dtmf"]

    result = await tool(context=run_ctx(), digits="1, 2 #")

    assert result == "Pressed 1 2 #."
    assert room.local_participant.published == [(1, "1"), (2, "2"), (11, "#")]  # type: ignore[attr-defined]
    assert events[-1] == ("dtmf", {"direction": "sent", "digits": "12#", "source": "tool"})


async def test_send_dtmf_tool_refuses_non_keypad_input() -> None:
    session, ctx, _api, _events = make_session(sip_room())

    result = await _tools(session, ctx)["send_dtmf"](context=run_ctx(), digits="call 911")

    assert result.startswith("Nothing sent")


def test_transfer_call_schema_lists_the_destinations() -> None:
    session, ctx, _api, _events = make_session(sip_room())
    schema = build_legacy_openai_schema(_tools(session, ctx)["transfer_call"], internally_tagged=True)

    assert "Sales" in schema["description"]
    assert set(schema["parameters"]["properties"]) == {"destination", "announcement"}


async def test_transfer_call_tool_announces_transfers_through_api_and_ends_job() -> None:
    room = sip_room(caller())
    session, ctx, api, events = make_session(room)
    session.start()
    ended: list[str] = []
    tool = build_telephony_tools(
        session,
        ctx,
        disabled=[],
        dtmf_enabled=False,
        targets={"Sales": "+15550001111"},
        shutdown=ended.append,
    )[0]

    result = await tool(context=run_ctx(), destination="sales")

    assert result == "Transferred."
    assert ctx.session.said == ["Please hold while I transfer your call."]  # type: ignore[attr-defined]
    assert api.transfers == [("sess-1", "+15550001111", "sip_+15557654321")]
    assert ended == ["call transferred"]
    assert ("transfer", {"to": "+15550001111", "ok": True, "status": "transferred", "reason": None}) in events


async def test_transfer_call_tool_unknown_destination_is_refused_without_api_call() -> None:
    session, ctx, api, _events = make_session(sip_room(caller()))
    tool = _tools(session, ctx)["transfer_call"]

    result = await tool(context=run_ctx(), destination="+19005550199")

    assert result.startswith("Unknown destination")
    assert api.transfers == []


async def test_transfer_call_tool_failure_keeps_call_and_tells_model() -> None:
    api = FakeTelephonyApi(result=TransferResult(ok=False, status="failed", reason="403 Forbidden"))
    session, ctx, _api, _events = make_session(sip_room(caller()), api=api)
    ended: list[str] = []
    tool = build_telephony_tools(
        session,
        ctx,
        disabled=[],
        dtmf_enabled=False,
        targets={"Sales": "+15550001111"},
        shutdown=ended.append,
    )[0]

    result = await tool(context=run_ctx(), destination="Sales")

    assert "403 Forbidden" in result
    assert ended == []


# ------------------------------------------------------------------ assembly wiring
@dataclass
class _Resolved:
    channel: str
    session_id: str = "sess-1"
    config: Any = field(default_factory=default_agent_config)


def test_session_for_skips_non_sip_channels() -> None:
    ctx = FakePackSessionContext()

    telephony = session_for(
        room=cast(rtc.Room, sip_room()),
        resolved=_Resolved(channel="web"),
        pack_ctx=ctx,
        pack=None,
        record_event=lambda *_: None,
        api=FakeTelephonyApi(),
    )

    assert telephony is None


async def test_session_for_sip_builds_tools_from_config_and_reports_once_started() -> None:
    room = sip_room(caller())
    config = default_agent_config()
    config.capabilities.dtmf = True
    config.pack_settings = {"transfer_targets": {"Front desk": "+15550003333"}}
    ctx = FakePackSessionContext(room=cast(rtc.Room, room), config=config)
    api = FakeTelephonyApi()

    telephony = session_for(
        room=cast(rtc.Room, room),
        resolved=_Resolved(channel="sip_in", config=config),
        pack_ctx=ctx,
        pack=None,
        record_event=lambda *_: None,
        api=api,
    )
    assert telephony is not None
    tools = telephony.tools(config=config)
    await settle()
    assert api.reports == []  # nothing happens before start()
    telephony.start()
    await settle()

    assert {getattr(t, "id", None) for t in tools} == {"send_dtmf", "transfer_call"}
    assert api.reports[0]["status"] == "answered"
    await telephony.aclose()


@dataclass
class _TransferNode:
    to: str
    mode: str = "cold"


async def test_flow_transfer_node_transfers_through_api() -> None:
    session, _ctx, api, events = make_session(sip_room(caller()))

    ok = await session.flow_transfer(_TransferNode(to="+15550004444"), state=None)

    assert ok is True
    assert api.transfers == [("sess-1", "+15550004444", "sip_+15557654321")]
    assert events[-1][0] == "transfer"


async def test_flow_transfer_warm_node_falls_back_to_cold_and_says_so() -> None:
    api = FakeTelephonyApi(result=TransferResult(ok=False, status="failed", reason="403"))
    session, _ctx, _api, events = make_session(sip_room(caller()), api=api)

    ok = await session.flow_transfer(_TransferNode(to="+15550004444", mode="warm"), state=None)

    assert ok is False
    assert events[0] == ("info", {"message": "warm transfer is not available yet; transferring cold"})


# ------------------------------------------------------------------------ http client
async def test_http_telephony_api_posts_with_service_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/transfer"):
            return httpx.Response(200, json={"ok": False, "status": "failed", "reason": "busy"})
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(base_url="http://api.test", transport=httpx.MockTransport(handler))
    api = HttpTelephonyApi("http://api.test", "svc-token", client=client)

    await api.report({"session_id": "s1", "status": "answered"})
    result = await api.transfer("s1", "+15550001111", "sip_x")
    await client.aclose()

    assert [r.url.path for r in seen] == [
        "/internal/v1/telephony/calls/report",
        "/internal/v1/telephony/sessions/s1/transfer",
    ]
    assert all(r.headers["X-Service-Token"] == "svc-token" for r in seen)
    assert json.loads(seen[1].content) == {"to": "+15550001111", "participant_identity": "sip_x"}
    assert result == TransferResult(ok=False, status="failed", reason="busy")


async def test_http_telephony_api_report_swallows_transport_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    client = httpx.AsyncClient(base_url="http://api.test", transport=httpx.MockTransport(handler))
    api = HttpTelephonyApi("http://api.test", "svc-token", client=client)

    await api.report({"session_id": "s1", "status": "answered"})
    result = await api.transfer("s1", "+15550001111", None)
    await client.aclose()

    assert result.ok is False and "unreachable" in (result.reason or "")


# ---------------------------------------------------------------- outbound answer wait
async def test_wait_for_answer_returns_once_call_status_turns_active() -> None:
    from lkap_agent.telephony import wait_for_answer

    leg = caller(status="ringing", identity="sip-out")
    room = sip_room(leg)

    waiter = asyncio.ensure_future(wait_for_answer(cast(rtc.Room, room), timeout_s=1.0))
    await settle()
    assert not waiter.done()
    leg.attributes["sip.callStatus"] = "active"
    room.emit("participant_attributes_changed", {"sip.callStatus": "active"}, leg)

    assert await waiter is leg


async def test_wait_for_answer_times_out_on_a_ringing_line() -> None:
    from lkap_agent.telephony import wait_for_answer

    room = sip_room(caller(status="ringing"))

    assert await wait_for_answer(cast(rtc.Room, room), timeout_s=0.02) is None


async def test_wait_for_answer_accepts_leg_without_status_attribute() -> None:
    from lkap_agent.telephony import wait_for_answer

    leg = caller()
    room = sip_room(leg)

    assert await wait_for_answer(cast(rtc.Room, room), timeout_s=0.02) is leg


async def test_wait_for_answer_gives_up_when_the_room_closes() -> None:
    from lkap_agent.telephony import wait_for_answer

    room = sip_room(caller(status="ringing"))
    waiter = asyncio.ensure_future(wait_for_answer(cast(rtc.Room, room), timeout_s=5.0))
    await settle()

    room.emit("disconnected", "room deleted")

    assert await waiter is None
