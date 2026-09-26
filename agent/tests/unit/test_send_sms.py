"""`send_sms` (V5-25): the destination policy, both vendor shapes, the repeat guard and the event."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from fakes.fake_ctx import FakePackSessionContext, default_agent_config
from fakes.fake_room import FakeRemoteParticipant, FakeRoom
from livekit import rtc
from livekit.agents import RunContext, ToolError
from lkap_contracts.agent_config import ResolvedProvider, ToolsConfig
from lkap_contracts.common import ProviderRef
from lkap_contracts.telephony import SmsTarget, TelephonyConfig

from lkap_agent.tools.builtin import build_builtin_tools
from lkap_agent.tools.builtin.send_sms import build_send_sms_tool
from lkap_agent.tools.execution import policy_of
from lkap_agent.tools.vendors import VendorError, telnyx, twilio

SID = "AC" + "0" * 32
TWILIO_URL = f"https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json"
TELNYX_URL = "https://api.telnyx.com/v2/messages"
CALLER = "+15550001234"
DESK = "+15550009999"
FROM = "+15550100000"


@dataclass
class _Call:
    call_id: str = "call-1"


@dataclass
class _Run:
    function_call: _Call = field(default_factory=_Call)


def _run() -> RunContext:
    return cast(RunContext, _Run())


def _twilio() -> ResolvedProvider:
    return ResolvedProvider(
        provider_id="twilio-sms",
        python_class="",
        model=None,
        kwargs={"account_sid": SID, "auth_token": "token-not-real", "from_number": FROM},
    )


def _telnyx(**extra: Any) -> ResolvedProvider:
    return ResolvedProvider(
        provider_id="telnyx-sms",
        python_class="",
        model=None,
        kwargs={"api_key": "key-not-real", "from_number": FROM, **extra},
    )


def _ctx(
    *, channel: str = "sip_in", caller: str | None = CALLER, targets: list[SmsTarget] | None = None
) -> Any:
    room = FakeRoom()
    if caller is not None:
        room.add_remote_participant(
            FakeRemoteParticipant(
                "sip_caller",
                attributes={"sip.phoneNumber": caller},
                kind=rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
            )
        )
    config = default_agent_config(
        tools=ToolsConfig(sms=ProviderRef(provider_id="twilio-sms", credential_id="c")),
        telephony=TelephonyConfig(sms_targets=targets or []),
    )
    ctx = FakePackSessionContext(config=config, room=cast(rtc.Room, room))
    cast(Any, ctx).channel = channel
    return ctx


def _twilio_ok() -> respx.Route:
    return respx.post(TWILIO_URL).mock(
        return_value=httpx.Response(201, json={"sid": "SM1", "status": "queued"})
    )


# ------------------------------------------------------------------ destination policy
@respx.mock
async def test_on_a_phone_call_the_default_destination_is_the_caller() -> None:
    route = _twilio_ok()
    ctx = _ctx()
    tool = build_send_sms_tool(ctx, _twilio())

    result = await tool(context=_run(), message="Your claim reference is CLM-1234.")

    form = parse_qs(route.calls.last.request.content.decode())
    assert form == {"To": [CALLER], "From": [FROM], "Body": ["Your claim reference is CLM-1234."]}
    assert "sent to the caller" in result
    assert CALLER not in result


@respx.mock
async def test_the_word_caller_means_the_caller() -> None:
    route = _twilio_ok()
    tool = build_send_sms_tool(_ctx(), _twilio())

    await tool(context=_run(), message="hi", to="Caller")

    assert parse_qs(route.calls.last.request.content.decode())["To"] == [CALLER]


@respx.mock
async def test_an_unlabelled_number_is_refused_even_on_a_phone_call() -> None:
    route = _twilio_ok()
    tool = build_send_sms_tool(_ctx(targets=[SmsTarget(label="Claims desk", to=DESK)]), _twilio())

    for to in ("+15550007777", DESK, CALLER, "Sales"):
        with pytest.raises(ToolError, match="numbers cannot be typed in. Saved contacts: Claims desk"):
            await tool(context=_run(), message="hi", to=to)
    assert not route.called


@respx.mock
async def test_a_saved_contact_is_texted_by_label_case_insensitively() -> None:
    route = _twilio_ok()
    ctx = _ctx(channel="web", caller=None, targets=[SmsTarget(label="Claims desk", to=DESK)])
    tool = build_send_sms_tool(ctx, _twilio())

    result = await tool(context=_run(), message="Caller asked for a callback", to="claims DESK")

    assert parse_qs(route.calls.last.request.content.decode())["To"] == [DESK]
    assert "Claims desk" in result


async def test_off_a_phone_call_the_caller_cannot_be_texted() -> None:
    tool = build_send_sms_tool(_ctx(channel="web", caller=None), _twilio())

    with pytest.raises(ToolError, match="only on a phone call"):
        await tool(context=_run(), message="hi")


async def test_a_phone_call_without_the_caller_number_is_refused() -> None:
    tool = build_send_sms_tool(_ctx(caller="anonymous"), _twilio())

    with pytest.raises(ToolError, match="number is not available"):
        await tool(context=_run(), message="hi")


# ------------------------------------------------------------------ repeat guard and event
@respx.mock
async def test_the_same_message_is_sent_once_unless_the_caller_confirms() -> None:
    route = _twilio_ok()
    ctx = _ctx()
    tool = build_send_sms_tool(ctx, _twilio())

    await tool(context=_run(), message="Reference CLM-1")
    again = await tool(context=_run(), message="Reference CLM-1")
    assert "already sent" in again and route.call_count == 1

    await tool(context=_run(), message="Reference CLM-1", confirm_repeat=True)
    assert route.call_count == 2


@respx.mock
async def test_a_sent_message_records_sms_sent_without_the_text_or_the_full_number() -> None:
    _twilio_ok()
    ctx = _ctx()
    tool = build_send_sms_tool(ctx, _twilio())

    await tool(context=_run(), message="secret-ish body")

    [(event_type, payload)] = ctx.events
    assert event_type == "sms_sent"
    assert payload == {
        "to": "the caller",
        "to_last4": "1234",
        "provider": "twilio-sms",
        "message_id": "SM1",
        "status": "queued",
        "chars": 15,
    }
    assert "secret-ish" not in json.dumps(payload) and CALLER not in json.dumps(payload)


@pytest.mark.parametrize("message", ["", "   ", "x" * 481])
async def test_empty_or_long_messages_are_refused(message: str) -> None:
    tool = build_send_sms_tool(_ctx(), _twilio())

    with pytest.raises(ToolError):
        await tool(context=_run(), message=message)


async def test_without_a_resolved_key_the_tool_says_it_is_not_set_up() -> None:
    tool = build_send_sms_tool(_ctx(), None)

    with pytest.raises(ToolError, match="not set up"):
        await tool(context=_run(), message="hi")


async def test_a_missing_sending_number_is_not_set_up() -> None:
    provider = _twilio().model_copy(update={"kwargs": {"account_sid": SID, "auth_token": "t"}})
    tool = build_send_sms_tool(_ctx(), provider)

    with pytest.raises(ToolError, match="not set up"):
        await tool(context=_run(), message="hi")


# ------------------------------------------------------------------ vendor shapes
@respx.mock
async def test_twilio_uses_basic_auth_with_the_account_sid() -> None:
    route = _twilio_ok()

    receipt = await twilio.send(to=CALLER, body="b", from_number=FROM, account_sid=SID, auth_token="tok")

    request = route.calls.last.request
    assert request.headers["Authorization"].startswith("Basic ")
    assert receipt.message_id == "SM1" and receipt.status == "queued"


async def test_twilio_refuses_a_malformed_account_sid_before_any_call() -> None:
    with pytest.raises(VendorError, match="account SID is not valid"):
        await twilio.send(to=CALLER, body="b", from_number=FROM, account_sid="AC/../x", auth_token="t")


@respx.mock
async def test_telnyx_posts_json_with_a_bearer_key_and_the_profile() -> None:
    route = respx.post(TELNYX_URL).mock(
        return_value=httpx.Response(200, json={"data": {"id": "m-1", "to": [{"status": "queued"}]}})
    )
    tool = build_send_sms_tool(_ctx(), _telnyx(messaging_profile_id="prof-1"))

    await tool(context=_run(), message="hello")

    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer key-not-real"
    assert json.loads(request.content) == {
        "from": FROM,
        "to": CALLER,
        "text": "hello",
        "messaging_profile_id": "prof-1",
    }


@respx.mock
async def test_telnyx_leaves_out_an_unset_profile() -> None:
    route = respx.post(TELNYX_URL).mock(return_value=httpx.Response(200, json={"data": {"id": "m-1"}}))

    receipt = await telnyx.send(to=CALLER, body="b", from_number=FROM, api_key="k")

    assert "messaging_profile_id" not in json.loads(route.calls.last.request.content)
    assert receipt.status == "sent"


@respx.mock
async def test_a_vendor_refusal_is_a_tool_error() -> None:
    respx.post(TWILIO_URL).mock(return_value=httpx.Response(401, json={"message": "auth"}))
    tool = build_send_sms_tool(_ctx(), _twilio())

    with pytest.raises(ToolError, match="not sent: Twilio refused the key"):
        await tool(context=_run(), message="hi")


# ------------------------------------------------------------------ registration and execution
def test_send_sms_is_registered_only_with_tools_sms_and_runs_as_a_background_write() -> None:
    plain = FakePackSessionContext()
    assert "send_sms" not in {
        t.info.name for t in build_builtin_tools(plain, disabled=[], http_enabled=False)
    }

    tools = build_builtin_tools(_ctx(), disabled=[], http_enabled=False, providers={"sms": _twilio()})
    [tool] = [t for t in tools if t.info.name == "send_sms"]
    resolved = policy_of(tool).resolved  # type: ignore[union-attr]
    assert resolved.mode == "background"
    assert resolved.on_duplicate == "confirm"
    assert resolved.cancellable is False


def test_the_saved_contacts_are_named_in_the_tool_description() -> None:
    tool = build_send_sms_tool(_ctx(targets=[SmsTarget(label="Claims desk", to=DESK)]), _twilio())

    assert "Claims desk" in tool.info.description
    assert DESK not in tool.info.description
