"""V6-07 (D-V6-22/23): tool context placeholders, bindings, `requires_vars`, `confirm_readback`.

HTTP and app-action calls are mocked at the transport boundary (`respx`, `httpx.MockTransport`);
MCP tools are driven through `_make_function_tool` with a fake client session. No network.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
import respx
import structlog
from fakes.fake_ctx import FakePackSessionContext, FakeRunContext, default_agent_config
from fakes.fake_room import FakeRemoteParticipant, FakeRoom
from livekit import rtc
from livekit.agents import RunContext, ToolError
from lkap_contracts.agent_config import PanelLayout
from lkap_contracts.tool_context import ToolContextSpec
from lkap_contracts.tools import HttpToolDefinition, McpServerDefinition, ProviderToolDefinition
from lkap_contracts.ui_protocol import BlockSpec, ChecklistItem

from lkap_agent.tools.bindings import BINDINGS_EVENT, apply_bindings
from lkap_agent.tools.context import (
    BOUND_VARIABLES_USERDATA_KEY,
    VARIABLES_USERDATA_KEY,
    ToolCallContext,
    uses_tool_context,
)
from lkap_agent.tools.declarative import build_http_tools, build_mocked_provider_tool
from lkap_agent.tools.provider import EXECUTE_BASE, build_provider_tool

PHONE = "+15550100199"
DIGITS = "5550100199"

_PANEL = PanelLayout(
    blocks=[
        BlockSpec(id="card", type="details"),
        BlockSpec(id="results", type="table"),
        BlockSpec(id="pay", type="link"),
        BlockSpec(id="status", type="status"),
        BlockSpec(id="notes", type="notes"),
        BlockSpec(id="checklist", type="checklist"),
    ]
)


def _session(*, channel: str = "web", phone: str | None = None, **variables: Any) -> FakePackSessionContext:
    room = FakeRoom()
    if phone is not None:
        room.add_remote_participant(
            FakeRemoteParticipant(
                "sip-caller",
                attributes={"sip.phoneNumber": phone},
                kind=rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
            )
        )
    else:
        room.add_remote_participant(FakeRemoteParticipant("web-caller"))
    ctx = FakePackSessionContext(
        session_id="sess_demo",
        agent_id="agent_demo",
        config=default_agent_config(panel=_PANEL, timezone="Europe/Berlin"),
        room=cast(rtc.Room, room),
    )
    cast(Any, ctx).channel = channel
    ctx.userdata[VARIABLES_USERDATA_KEY] = dict(variables)
    return ctx


def _tool_context(session: FakePackSessionContext) -> ToolCallContext:
    return ToolCallContext(session, participant_identity="web-caller")


def _run(name: str = "lookup_policy", call_id: str = "call-1") -> RunContext[Any]:
    return cast(RunContext[Any], FakeRunContext(name=name, call_id=call_id))


def _http(**overrides: Any) -> HttpToolDefinition:
    body: dict[str, Any] = {
        "name": "lookup_policy",
        "description": "Look a policy up.",
        "parameters": {"type": "object", "properties": {"policy_no": {"type": "string"}}},
        "method": "GET",
        "url": "https://api.example.com/policies/{{ policy_no }}",
        "allowed_hosts": ["api.example.com"],
    }
    body.update(overrides)
    return HttpToolDefinition.model_validate(body)


async def _call(definition: HttpToolDefinition, context: ToolCallContext | None, **arguments: Any) -> str:
    (tool,) = build_http_tools([definition], context=context)
    result: str = await tool(raw_arguments=arguments, context=_run(definition.name))
    return result


# ---------------------------------------------------------------------- compatibility


@respx.mock
async def test_http_tool_without_new_fields_sends_byte_identical_request() -> None:
    """A definition that uses no V6-07 feature renders exactly what it did before (the fixture)."""
    route = respx.post("https://api.example.com/claims/C%2042").mock(
        return_value=httpx.Response(200, text="ok")
    )
    definition = _http(
        method="POST",
        url="https://api.example.com/claims/{{ claim }}",
        headers={"X-Api-Key": "resolved-placeholder-key"},
        body_template='{"note": "{{ note }}", "confirmed": "{{ confirmed }}"}',
        parameters={"type": "object", "properties": {"claim": {}, "note": {}, "confirmed": {}}},
    )
    session = _session(policy_no="P-1")
    sent: list[httpx.Request] = []
    for context in (None, _tool_context(session)):
        await _call(definition, context, claim="C 42", note='a "quote"', confirmed="yes")
        sent.append(route.calls.last.request)
    for request in sent:
        assert str(request.url) == "https://api.example.com/claims/C%2042"
        assert request.content == b'{"note": "a \\"quote\\"", "confirmed": "yes"}'
        assert request.headers["X-Api-Key"] == "resolved-placeholder-key"
        assert request.headers["Content-Type"] == "application/json"
    assert sent[0].headers.raw == sent[1].headers.raw
    (tool,) = build_http_tools([definition])
    assert "confirmed" in tool.info.raw_schema["parameters"]["properties"]  # the tool's own argument


def test_ctx_values_are_read_lazily_from_the_session() -> None:
    from lkap_agent.locale import LOCALE_USERDATA_KEY, fallback_locale

    session = _session(channel="sip_out", phone=PHONE)
    context = _tool_context(session)
    assert context.ctx_value("session_id") == "sess_demo"
    assert context.ctx_value("agent_id") == "agent_demo"
    assert context.ctx_value("channel") == "phone"
    assert context.ctx_value("caller_phone") == PHONE
    assert context.ctx_value("caller_identity") == "sip-caller"
    assert context.ctx_value("language") == "en"
    assert context.ctx_value("timezone") == "Europe/Berlin"  # the business zone until resolved
    locale = fallback_locale(session.config)
    locale.caller_timezone = "Asia/Kolkata"
    session.userdata[LOCALE_USERDATA_KEY] = locale  # resolved on the first turn (R-V5-10)
    assert context.ctx_value("timezone") == "Asia/Kolkata"
    web = _tool_context(_session(channel="widget"))
    assert web.ctx_value("channel") == "web"
    assert web.ctx_value("caller_phone") is None  # never on the web, whatever the attributes say
    assert _tool_context(_session(channel="text")).ctx_value("channel") == "text"


def test_uses_tool_context_only_for_definitions_with_the_new_features() -> None:
    assert not uses_tool_context(_http())
    assert uses_tool_context(_http(url="https://api.example.com/p?tz={{ ctx.timezone }}"))
    assert uses_tool_context(_http(bindings=[{"to": "note"}]))
    assert uses_tool_context(_http(requires_vars=["policy_no"]))
    mcp = McpServerDefinition(name="crm", url="https://mcp.example.com/mcp")
    assert not uses_tool_context(mcp)
    assert uses_tool_context(mcp.model_copy(update={"tool_context": {"find": ToolContextSpec()}}))


# ---------------------------------------------------------------------- placeholders


@respx.mock
async def test_ctx_and_var_placeholders_render_percent_encoded_in_path_and_query() -> None:
    route = respx.get(url__startswith="https://api.example.com/sessions/").mock(
        return_value=httpx.Response(200, text="ok")
    )
    definition = _http(
        url=(
            "https://api.example.com/sessions/{{ ctx.session_id }}"
            "?tz={{ ctx.timezone }}&p={{ var.policy_no }}&ch={{ ctx.channel }}&caller={{ ctx.caller_phone }}"
        ),
    )
    session = _session(channel="sip_in", phone=PHONE, policy_no="POL 7/9")
    await _call(definition, _tool_context(session))
    url = str(route.calls.last.request.url)
    assert url.startswith("https://api.example.com/sessions/sess_demo?")
    assert "tz=Europe%2FBerlin" in url
    assert "p=POL%207%2F9" in url
    assert "ch=phone" in url
    assert "caller=%2B15550100199" in url


@respx.mock
async def test_placeholder_values_json_escaped_in_body() -> None:
    route = respx.post("https://api.example.com/notes").mock(return_value=httpx.Response(200, text="ok"))
    definition = _http(
        method="POST",
        url="https://api.example.com/notes",
        body_template='{"holder": "{{ var.holder }}", "lang": "{{ ctx.language }}"}',
    )
    await _call(definition, _tool_context(_session(holder='Ada "the" Lovelace\n')))
    assert json.loads(route.calls.last.request.content) == {"holder": 'Ada "the" Lovelace\n', "lang": "en"}


@respx.mock
async def test_argument_text_that_looks_like_a_placeholder_is_never_expanded() -> None:
    route = respx.get(url__startswith="https://api.example.com/search").mock(
        return_value=httpx.Response(200, text="ok")
    )
    definition = _http(
        url="https://api.example.com/search?q={{ q }}&tz={{ ctx.timezone }}",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
    )
    session = _session(channel="sip_in", phone=PHONE)
    await _call(definition, _tool_context(session), q="{{ ctx.caller_phone }}")
    url = str(route.calls.last.request.url)
    assert DIGITS not in url
    assert "q=%7B%7B%20ctx.caller_phone%20%7D%7D" in url


@respx.mock
async def test_missing_ctx_value_refuses_without_calling_out() -> None:
    route = respx.get(url__startswith="https://api.example.com/").mock(return_value=httpx.Response(200))
    definition = _http(url="https://api.example.com/callers?phone={{ ctx.caller_phone }}")
    with pytest.raises(ToolError, match="I need the caller's phone number first"):
        await _call(definition, _tool_context(_session(channel="web")))
    assert not route.called


@respx.mock
async def test_missing_var_refuses_and_names_it() -> None:
    route = respx.get(url__startswith="https://api.example.com/").mock(return_value=httpx.Response(200))
    definition = _http(url="https://api.example.com/policies?p={{ var.policy_number }}")
    with pytest.raises(ToolError, match="I need policy number first"):
        await _call(definition, _tool_context(_session()))
    with pytest.raises(ToolError, match="I need policy number first"):
        await _call(definition, None)
    assert not route.called


@respx.mock
async def test_flow_variables_are_read_from_the_flow_state() -> None:
    route = respx.get(url__startswith="https://api.example.com/").mock(return_value=httpx.Response(200))
    session = _session(policy_no="from-plain-store")
    session.userdata["flow"] = SimpleNamespace(variables={"policy_no": "from-flow"})
    await _call(_http(url="https://api.example.com/p?n={{ var.policy_no }}"), _tool_context(session))
    assert str(route.calls.last.request.url).endswith("n=from-flow")


@respx.mock
async def test_caller_phone_never_reaches_a_log_line() -> None:
    respx.get(url__startswith="https://api.example.com/").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    definition = _http(
        url="https://api.example.com/callers?phone={{ ctx.caller_phone }}&who={{ ctx.caller_identity }}",
        bindings=[{"path": "/id", "to": "var:caller_ref"}],
    )
    session = _session(channel="sip_in", phone=PHONE)
    with structlog.testing.capture_logs() as logs:
        await _call(definition, _tool_context(session))
        with pytest.raises(ToolError):
            await _call(_http(url="https://api.example.com/x?p={{ var.nope }}"), _tool_context(session))
    assert logs, "the calls log something at debug level"
    rendered = json.dumps(logs, default=str)
    assert DIGITS not in rendered
    assert "sip-caller" not in rendered


@respx.mock
async def test_misplaced_placeholder_that_reaches_the_worker_refuses_every_call() -> None:
    route = respx.get(url__startswith="https://").mock(return_value=httpx.Response(200))
    bad = _http().model_copy(update={"url": "https://{{ var.host }}/x"})  # skips validation
    with pytest.raises(ToolError, match="set up incorrectly"):
        await _call(bad, _tool_context(_session(host="evil.example.com")))
    assert not route.called


# ---------------------------------------------------------------------- requires_vars / readback


@respx.mock
async def test_requires_vars_lists_every_missing_variable_and_sends_nothing() -> None:
    route = respx.get(url__startswith="https://api.example.com/").mock(return_value=httpx.Response(200))
    definition = _http(requires_vars=["policy_no", "date_of_birth"])
    with pytest.raises(ToolError, match="I need policy no and date of birth first"):
        await _call(definition, _tool_context(_session()), policy_no="P1")
    assert not route.called
    await _call(
        definition, _tool_context(_session(policy_no="P1", date_of_birth="1990-01-01")), policy_no="P1"
    )
    assert route.called


@respx.mock
async def test_confirm_readback_refuses_until_confirmed_and_strips_the_flag() -> None:
    route = respx.post("https://api.example.com/contacts").mock(
        return_value=httpx.Response(200, text="saved")
    )
    definition = _http(
        method="POST",
        url="https://api.example.com/contacts",
        parameters={"type": "object", "properties": {"email": {"type": "string"}}},
        confirm_readback=["email"],
    )
    (tool,) = build_http_tools([definition])
    assert tool.info.raw_schema["parameters"]["properties"]["confirmed"]["type"] == "boolean"
    assert "confirmed" not in definition.parameters["properties"]

    with pytest.raises(ToolError, match=r"read these back.*email: ada@example.com") as refused:
        await tool(raw_arguments={"email": "ada@example.com"}, context=_run())
    assert "confirmed=true" in str(refused.value)
    with pytest.raises(ToolError):
        await tool(raw_arguments={"email": "ada@example.com", "confirmed": False}, context=_run())
    assert not route.called

    await tool(raw_arguments={"email": "ada@example.com", "confirmed": True}, context=_run())
    assert json.loads(route.calls.last.request.content) == {"email": "ada@example.com"}


@respx.mock
async def test_confirm_readback_needs_a_caller_turn_after_the_readback() -> None:
    """V6-21 (S6-10): ``confirmed=true`` counts only after the refusal and a caller turn since."""
    from livekit.agents.llm import ChatContext

    route = respx.post("https://api.example.com/contacts").mock(
        return_value=httpx.Response(200, text="saved")
    )
    session = _session()
    history = ChatContext.empty()
    history.add_message(role="user", content="My email is ada@example.com.")
    cast(Any, session).session.history = history
    definition = _http(
        method="POST",
        url="https://api.example.com/contacts",
        parameters={"type": "object", "properties": {"email": {"type": "string"}}},
        confirm_readback=["email"],
    )
    (tool,) = build_http_tools([definition], context=_tool_context(session))
    confirmed = {"email": "ada@example.com", "confirmed": True}

    # Straight to confirmed=true, and again with no caller turn since: both refused.
    for _ in range(2):
        with pytest.raises(ToolError, match="read these back"):
            await tool(raw_arguments=dict(confirmed), context=_run())
    assert not route.called

    history.add_message(role="assistant", content="That is a-d-a at example dot com, right?")
    history.add_message(role="user", content="Yes, that's right.")
    await tool(raw_arguments=dict(confirmed), context=_run())
    assert route.call_count == 1
    assert json.loads(route.calls.last.request.content) == {"email": "ada@example.com"}


# ---------------------------------------------------------------------- bindings


@respx.mock
async def test_bindings_fill_the_panel_and_variables_before_the_model_reads_the_result() -> None:
    respx.get("https://api.example.com/policies/P1").mock(
        return_value=httpx.Response(
            200,
            json={
                "policy": {"holder": "Ada Lovelace", "status": "Active", "found": True},
                "claims": [{"id": "c1", "amount": 120, "kind": "glass"}, {"amount": 90, "kind": "tyre"}],
            },
        )
    )
    session = _session()
    session.ui.state.checklist = [ChecklistItem(id="policy_found", label="Policy found")]
    definition = _http(
        bindings=[
            {"path": "/policy/holder", "to": "details:card.holder"},
            {"path": "/claims", "to": "table:results"},
            {"path": "/policy/status", "to": "status"},
            {"path": "/policy/found", "to": "checklist:policy_found"},
            {"path": "/policy/holder", "to": "note"},
            {"path": "/policy/holder", "to": "var:holder_name"},
            {"path": "/policy/holder", "to": "details:pay.url"},
            {"path": "/missing", "to": "details:card.other"},
        ]
    )
    result = await _call(definition, _tool_context(session), policy_no="P1")

    assert "Ada Lovelace" in result  # the model still gets the (fenced) result
    card = session.ui.state.blocks["card"]["items"]
    assert [(row["key"], row["value"]) for row in card] == [("holder", "Ada Lovelace")]
    table = session.ui.state.blocks["results"]
    assert [row["kind"] for row in table["rows"]] == ["glass", "tyre"]
    assert all(row.get("id") for row in table["rows"])
    assert {column["key"] for column in table["columns"]} == {"amount", "kind"}
    assert session.ui.state.status is not None and session.ui.state.status.label == "Active"
    assert session.ui.state.checklist[0].done is True
    assert [note.text for note in session.ui.state.notes] == ["Ada Lovelace"]
    assert session.userdata[VARIABLES_USERDATA_KEY]["holder_name"] == "Ada Lovelace"
    assert session.userdata[BOUND_VARIABLES_USERDATA_KEY] == {"holder_name"}
    assert "pay" not in session.ui.state.blocks or not session.ui.state.blocks["pay"]

    (event,) = [e for e in session.ui.state.activity if e.id == "bindings-call-1"]
    assert event.detail is not None and event.detail["event"] == BINDINGS_EVENT
    assert len(event.detail["applied"]) == 6
    assert {s["reason"] for s in event.detail["skipped"]} == {"not_allowed", "not_found"}
    assert "Ada" not in json.dumps(event.model_dump())  # targets and counts, never values


@respx.mock
async def test_bindings_do_not_apply_on_an_error_status() -> None:
    respx.get("https://api.example.com/policies/P1").mock(
        return_value=httpx.Response(404, json={"holder": "Should not show"})
    )
    session = _session()
    definition = _http(bindings=[{"path": "/holder", "to": "details:card.holder"}])
    result = await _call(definition, _tool_context(session), policy_no="P1")
    assert "Should not show" in result  # the model still sees the body, as before
    assert "card" not in session.ui.state.blocks or not session.ui.state.blocks["card"].get("items")
    assert not session.ui.state.activity


@respx.mock
async def test_bindings_follow_result_path_and_bound_values_are_capped() -> None:
    respx.get("https://api.example.com/policies/P1").mock(
        return_value=httpx.Response(200, json={"data": {"summary": "x\x07" * 400, "rows": [{"a": 1}] * 150}})
    )
    session = _session()
    definition = _http(
        result_path="/data",
        bindings=[
            {"path": "/summary", "to": "details:card.summary"},
            {"path": "/rows", "to": "table:results"},
        ],
    )
    await _call(definition, _tool_context(session), policy_no="P1")
    (row,) = session.ui.state.blocks["card"]["items"]
    assert len(row["value"]) == 400  # control characters removed, then at most 500 characters
    assert "\x07" not in row["value"]
    assert len(session.ui.state.blocks["results"]["rows"]) == 100


async def test_apply_bindings_without_context_writes_nothing() -> None:
    from lkap_contracts.tool_context import ToolBinding

    report = await apply_bindings({"a": 1}, [ToolBinding(path="/a", to="var:a")], None, tool="t")
    assert report.applied == [] and report.skipped == []


# ---------------------------------------------------------------------- V6-21 (S6-2): bind after the guard

_SAFE = "Sorry, I can't share that."
_HOLDER_BINDINGS = [
    {"path": "/holder", "to": "details:card.holder"},
    {"path": "/holder", "to": "var:holder_name"},
]
_HOLDER_ARGUMENTS: dict[str, dict[str, Any]] = {
    "http": {"policy_no": "P1"},
    "provider": {"title": "x", "timezone": "UTC"},
    "dataset": {"policy_no": "P1"},
}


def _guarded_run(events: list[tuple[str, dict[str, Any]]]) -> Any:
    """A call whose session has a real `tool_output` guardrail: one regex rule on `SECRET-<digits>`."""
    from lkap_contracts.guardrails import GuardrailsConfig

    from lkap_agent.guardrails import ensure_session_guardrails
    from lkap_agent.tools.execution import set_tool_output_guard

    run = FakeRunContext(name="lookup_policy")
    config = GuardrailsConfig.model_validate(
        {"safe_reply": _SAFE, "tool_output": [{"kind": "regex", "name": "Secrets", "pattern": r"SECRET-\d+"}]}
    )
    host = SimpleNamespace(
        session_id="sess_demo",
        session=run.session,
        config=SimpleNamespace(guardrails=config),
        userdata={},
        record_event=lambda kind, payload: events.append((kind, payload)),
    )
    guard = ensure_session_guardrails(host)
    assert guard is not None
    set_tool_output_guard(run.session, guard.guard_tool_output)
    return run


def _holder_tool(kind: str, session: FakePackSessionContext, holder: str) -> Any:
    """An HTTP, app or dataset tool answering ``{"holder": holder}``, bound to the card and a variable."""
    from lkap_contracts.tools import DatasetToolDefinition

    from lkap_agent.tools.dataset import build_dataset_tool

    context = _tool_context(session)
    if kind == "http":
        respx.get("https://api.example.com/policies/P1").mock(
            return_value=httpx.Response(200, json={"holder": holder})
        )
        (tool,) = build_http_tools([_http(bindings=_HOLDER_BINDINGS)], context=context)
        return tool
    if kind == "provider":

        async def answer(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"holder": holder}, "successful": True})

        return build_provider_tool(
            _provider(bindings=_HOLDER_BINDINGS),
            transport_factory=lambda: httpx.MockTransport(answer),
            context=context,
        )
    definition = DatasetToolDefinition.model_validate(
        {
            "kind": "dataset",
            "name": "lookup_policy",
            "description": "Find the caller's policy.",
            "dataset_id": "0123456789abcdef0123456789abcdef",
            "key_columns": ["policy_no"],
            "bindings": [{"path": f"/0{b['path']}", "to": b["to"]} for b in _HOLDER_BINDINGS],
        }
    )
    return build_dataset_tool(definition, context=context, mock=[{"holder": holder}])


@respx.mock
@pytest.mark.parametrize("kind", ["http", "provider", "dataset"])
async def test_a_tool_output_trip_applies_no_bindings(kind: str) -> None:
    from lkap_agent.guardrails import TOOL_WITHHELD
    from lkap_agent.tools.execution import WITHHELD_EXTRA, set_tool_output_guard

    session = _session()
    events: list[tuple[str, dict[str, Any]]] = []
    run = _guarded_run(events)
    tool = _holder_tool(kind, session, "SECRET-1234")
    try:
        result = await tool(raw_arguments=_HOLDER_ARGUMENTS[kind], context=run)
    finally:
        set_tool_output_guard(run.session, None)

    assert result == TOOL_WITHHELD.format(safe_reply=_SAFE)
    assert run.function_call.extra[WITHHELD_EXTRA] is True
    assert not session.ui.state.blocks.get("card")  # the details block is unchanged
    assert "holder_name" not in session.userdata[VARIABLES_USERDATA_KEY]
    assert not [e for e in session.ui.state.activity if (e.detail or {}).get("event") == BINDINGS_EVENT]
    assert [p["action"] for _, p in events if p.get("stage") == "tool_output"] == ["replaced"]


@respx.mock
@pytest.mark.parametrize("kind", ["http", "provider", "dataset"])
async def test_a_clean_result_still_binds_after_the_guard(kind: str) -> None:
    from lkap_agent.tools.execution import WITHHELD_EXTRA, set_tool_output_guard

    session = _session()
    run = _guarded_run([])
    tool = _holder_tool(kind, session, "Ada Lovelace")
    try:
        await tool(raw_arguments=_HOLDER_ARGUMENTS[kind], context=run)
    finally:
        set_tool_output_guard(run.session, None)

    assert WITHHELD_EXTRA not in run.function_call.extra
    assert session.userdata[VARIABLES_USERDATA_KEY]["holder_name"] == "Ada Lovelace"
    assert session.ui.state.blocks["card"]["items"][0]["value"] == "Ada Lovelace"


# ---------------------------------------------------------------------- app actions


def _provider(**overrides: Any) -> ProviderToolDefinition:
    body: dict[str, Any] = {
        "name": "helpdesk_create_ticket",
        "description": "Open a ticket.",
        "parameters": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "timezone": {"type": "string"}, "email": {}},
            "required": ["title", "timezone"],
        },
        "tool_slug": "HELPDESK_CREATE_TICKET",
        "toolkit": "helpdesk",
        "connection_id": "conn1",
        "subject": "ws:w1",
        "headers": {"x-api-key": "ak-placeholder-key"},
    }
    body.update(overrides)
    return ProviderToolDefinition.model_validate(body)


async def test_provider_pinned_arguments_are_hidden_rendered_and_bound() -> None:
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": {"ticket": {"number": "T-9"}}, "successful": True})

    session = _session(policy_no="P1")
    definition = _provider(
        pinned_arguments={"timezone": "{{ ctx.timezone }}", "priority": 2},
        requires_vars=["policy_no"],
        bindings=[{"path": "/ticket/number", "to": "details:card.ticket"}],
    )
    tool = build_provider_tool(
        definition, transport_factory=lambda: httpx.MockTransport(handle), context=_tool_context(session)
    )
    schema = tool.info.raw_schema["parameters"]
    assert set(schema["properties"]) == {"title", "email"}
    assert schema["required"] == ["title"]

    await tool(raw_arguments={"title": "Glass", "timezone": "Evil/Zone"}, context=_run(definition.name))
    sent = json.loads(requests[-1].content)
    assert requests[-1].url == httpx.URL(f"{EXECUTE_BASE}/HELPDESK_CREATE_TICKET")
    assert sent["arguments"] == {"title": "Glass", "timezone": "Europe/Berlin", "priority": 2}
    assert session.ui.state.blocks["card"]["items"][0]["value"] == "T-9"


async def test_provider_missing_required_variable_sends_nothing() -> None:
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": {}, "successful": True})

    tool = build_provider_tool(
        _provider(requires_vars=["policy_no"]),
        transport_factory=lambda: httpx.MockTransport(handle),
        context=_tool_context(_session()),
    )
    with pytest.raises(ToolError, match="I need policy no first"):
        await tool(raw_arguments={"title": "x", "timezone": "UTC"}, context=_run())
    assert requests == []


async def test_mocked_provider_applies_checks_and_bindings_to_the_fixture() -> None:
    session = _session()
    definition = _provider(
        confirm_readback=["email"], bindings=[{"path": "/number", "to": "details:card.ticket"}]
    )
    tool = build_mocked_provider_tool(definition, {"number": "T-1"}, context=_tool_context(session))
    with pytest.raises(ToolError, match="read these back"):
        await tool(raw_arguments={"title": "x", "timezone": "UTC", "email": "a@example.com"}, context=_run())
    await tool(
        raw_arguments={"title": "x", "timezone": "UTC", "email": "a@example.com", "confirmed": True},
        context=_run(),
    )
    assert session.ui.state.blocks["card"]["items"][0]["value"] == "T-1"


# ---------------------------------------------------------------------- MCP


class _FakeClient:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any], **_: Any) -> Any:
        from mcp.types import CallToolResult, TextContent

        self.calls.append((name, arguments))
        return CallToolResult(content=[TextContent(type="text", text=self.payload)])


def _mcp_server(session: FakePackSessionContext, spec: ToolContextSpec) -> Any:
    from lkap_agent.tools.mcp_context import ContextMCPServerHTTP

    server = ContextMCPServerHTTP(
        url="https://mcp.example.com/mcp",
        transport_type="streamable_http",
        server_name="crm",
        tool_context={"find_contact": spec},
        context=_tool_context(session),
    )
    server._client = _FakeClient('{"name": "Ada", "tier": "gold"}')
    return server


def _mcp_options(report_progress: bool = False) -> Any:
    from livekit.agents.llm.mcp import _resolve_tool_options

    return _resolve_tool_options({"report_progress": report_progress})


@pytest.mark.parametrize("report_progress", [False, True])
async def test_mcp_tool_context_pins_checks_and_binds(report_progress: bool) -> None:
    session = _session(account_no="A-7")
    spec = ToolContextSpec(
        requires_vars=["account_no"],
        pinned_arguments={"account": "{{ var.account_no }}"},
        confirm_readback=["email"],
        bindings=[{"path": "/name", "to": "details:card.contact"}],
    )
    server = _mcp_server(session, spec)
    schema = {"type": "object", "properties": {"account": {}, "email": {}}, "required": ["account", "email"]}
    tool = server._make_function_tool(
        "find_contact", "Find a contact", schema, None, options=_mcp_options(report_progress)
    )
    parameters = tool.info.raw_schema["parameters"]
    assert set(parameters["properties"]) == {"email", "confirmed"}
    assert parameters["required"] == ["email"]

    with pytest.raises(ToolError, match="read these back"):
        await tool(raw_arguments={"email": "ada@example.com"}, ctx=_run("find_contact"))
    assert server._client.calls == []

    result = await tool(
        raw_arguments={"email": "ada@example.com", "confirmed": True}, ctx=_run("find_contact")
    )
    assert server._client.calls == [("find_contact", {"email": "ada@example.com", "account": "A-7"})]
    assert result.startswith('<untrusted source="mcp:crm">')
    assert session.ui.state.blocks["card"]["items"][0]["value"] == "Ada"


async def test_mcp_tool_without_tool_context_is_the_plain_sdk_tool() -> None:
    session = _session()
    server = _mcp_server(session, ToolContextSpec())
    tool = server._make_function_tool("other_tool", None, {"type": "object"}, None, options=_mcp_options())
    await tool(ctx=_run("other_tool"), raw_arguments={"q": 1})
    assert server._client.calls == [("other_tool", {"q": 1})]


def test_mcp_server_with_misplaced_placeholder_is_skipped_at_build() -> None:
    from lkap_agent.tools.declarative import build_guarded_mcp_servers

    good = McpServerDefinition(name="crm", url="https://mcp.example.com/mcp")
    bad = good.model_copy(update={"url": "https://mcp.example.com/{{ var.x }}"})  # skips validation
    skipped: list[str] = []
    servers = build_guarded_mcp_servers(
        [bad], on_skipped=lambda _d, reason: skipped.append(reason), host_ceiling=None
    )
    assert servers == []
    assert skipped and "url" in skipped[0]


# ---------------------------------------------------------------------- the session wiring


@pytest.mark.parametrize("with_context", [False, True])
async def test_run_session_hands_the_context_only_to_tools_that_use_it(with_context: bool) -> None:
    from fakes.fake_api import FakeApi, resolved_config
    from test_main import FakeJobContext, _deps, _metadata

    from lkap_agent.main import run_session
    from lkap_agent.tools.context import TOOL_CONTEXT_USERDATA_KEY

    http = _http(url="https://api.example.com/p?tz={{ ctx.timezone }}") if with_context else _http()
    mcp = McpServerDefinition(
        name="crm",
        url="https://mcp.example.com/mcp",
        tool_context={"find": ToolContextSpec(requires_vars=["account_no"])} if with_context else {},
    )
    http_calls: list[dict[str, Any]] = []
    mcp_calls: list[dict[str, Any]] = []

    def _http_builder(defs: list[Any], **kwargs: Any) -> list[Any]:
        http_calls.append(kwargs)
        return []

    def _mcp_builder(defs: list[Any], **kwargs: Any) -> list[Any]:
        mcp_calls.append(kwargs)
        return []

    resolved = resolved_config(channel="text", tools=[http, mcp])
    resolved = resolved.model_copy(update={"variables": {"account_no": "A-1"}})
    ctx = FakeJobContext(_metadata())
    deps = _deps(FakeApi(resolved), declarative_tools_builder=_http_builder, mcp_servers_builder=_mcp_builder)
    await run_session(ctx, deps)
    (http_kwargs,) = http_calls
    (mcp_kwargs,) = mcp_calls
    assert ("context" in http_kwargs) is with_context
    assert ("context" in mcp_kwargs) is with_context
    if with_context:
        context = http_kwargs["context"]
        assert isinstance(context, ToolCallContext)
        assert mcp_kwargs["context"] is context
        assert context.session.userdata[TOOL_CONTEXT_USERDATA_KEY] is context
        assert context.var_value("account_no") == "A-1"  # seeded from the session's variables
    await ctx.fire_shutdown("done")
