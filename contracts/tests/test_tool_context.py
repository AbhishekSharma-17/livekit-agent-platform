"""V6-07: tool context placeholders, bindings, requires_vars and confirm_readback (D-V6-22/23)."""

from typing import Any, get_args

import pytest
from pydantic import ValidationError

from lkap_contracts.common import SessionChannel
from lkap_contracts.tool_context import (
    CTX_LABELS,
    CTX_PLACEHOLDERS,
    MAX_BINDINGS,
    TOOL_CHANNEL_OF,
    BindingTarget,
    ToolBinding,
    context_placeholders,
    parse_binding_target,
    placeholder_issues,
    tool_channel,
    url_authority,
)
from lkap_contracts.tools import HttpToolDefinition, McpServerDefinition, ProviderToolDefinition

_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {"email": {"type": "string"}, "amount": {"type": "number"}},
}


def _http(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "lookup",
        "description": "Look a record up",
        "parameters": _PARAMS,
        "method": "GET",
        "url": "https://api.example.com/records",
        "allowed_hosts": ["api.example.com"],
    }
    return {**base, **overrides}


def _provider(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "create_ticket",
        "description": "Open a ticket",
        "parameters": _PARAMS,
        "tool_slug": "HELPDESK_CREATE_TICKET",
        "connection_id": "conn_1",
        "subject": "ws:ws_1",
    }
    return {**base, **overrides}


def _fields(definition: Any) -> list[str]:
    return [issue.field for issue in placeholder_issues(definition)]


# ------------------------------------------------------------------------ the ctx names


def test_ctx_placeholders_every_name_has_a_label() -> None:
    assert set(CTX_LABELS) == set(CTX_PLACEHOLDERS)
    assert "timezone" in CTX_PLACEHOLDERS  # closes V5 ask #150 (D-V6-32)


def test_tool_channel_every_session_channel_maps_to_three_words() -> None:
    assert set(TOOL_CHANNEL_OF) == set(get_args(SessionChannel))
    assert set(TOOL_CHANNEL_OF.values()) == {"web", "phone", "text"}
    assert tool_channel("sip_in") == "phone"
    assert tool_channel("text") == "text"
    assert tool_channel("widget") == "web"
    assert tool_channel("something-new") == "web"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://a.example.com/{{ id }}", []),
        ("{{ ctx.timezone }} {{var.policy_no}} {{ amount }}", [("ctx", "timezone"), ("var", "policy_no")]),
        ("{{ secret.api_key }}", []),
        (None, []),
    ],
)
def test_context_placeholders_lists_ctx_and_var_only(
    text: str | None, expected: list[tuple[str, str]]
) -> None:
    assert [(ref.namespace, ref.name) for ref in context_placeholders(text)] == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://api.example.com/x?y=1", ("https", "api.example.com")),
        ("https://user@api.example.com:8443/x", ("https", "user@api.example.com:8443")),
        ("api.example.com/x", ("", "api.example.com")),
        ("https://api.example.com{{ var.x }}/y", ("https", "api.example.com{{ var.x }}")),
    ],
)
def test_url_authority_splits_before_the_first_path_character(url: str, expected: tuple[str, str]) -> None:
    assert url_authority(url) == expected


# ------------------------------------------------------------------------ binding targets


@pytest.mark.parametrize(
    ("to", "target"),
    [
        ("details:card.holder_name", BindingTarget("details", block_id="card", key="holder_name")),
        ("table:results", BindingTarget("table", block_id="results")),
        ("checklist:policy_found", BindingTarget("checklist", key="policy_found")),
        ("status", BindingTarget("status")),
        ("note", BindingTarget("note")),
        ("var:policy_no", BindingTarget("var", key="policy_no")),
    ],
)
def test_parse_binding_target_valid_forms_parse(to: str, target: BindingTarget) -> None:
    assert parse_binding_target(to) == target
    assert ToolBinding(path="/a", to=to).target() == target


@pytest.mark.parametrize(
    "to",
    [
        "link:pay",
        "details:card",
        "details:.key",
        "table:",
        "var:Policy",
        "var:1x",
        "status:danger",
        "notes",
        "",
    ],
)
def test_parse_binding_target_invalid_forms_raise(to: str) -> None:
    with pytest.raises(ValueError, match="binding target"):
        parse_binding_target(to)


def test_tool_binding_path_must_be_a_pointer() -> None:
    assert ToolBinding(path="", to="note").path == ""
    with pytest.raises(ValidationError):
        ToolBinding(path="policy/holder", to="note")


# ------------------------------------------------------------------------ HTTP definitions


def test_http_definition_without_new_fields_is_unchanged() -> None:
    definition = HttpToolDefinition.model_validate(_http())
    assert definition.requires_vars == []
    assert definition.confirm_readback == []
    assert definition.bindings == []
    assert placeholder_issues(definition) == []


def test_http_definition_ctx_and_var_in_path_query_and_body_are_accepted() -> None:
    definition = HttpToolDefinition.model_validate(
        _http(
            method="POST",
            url=(
                "https://api.example.com/sessions/{{ ctx.session_id }}"
                "?tz={{ ctx.timezone }}&p={{ var.policy_no }}"
            ),
            body_template='{"phone": "{{ ctx.caller_phone }}", "email": "{{ email }}"}',
            requires_vars=["policy_no"],
            confirm_readback=["email"],
            bindings=[{"path": "/holder", "to": "details:card.holder"}, {"to": "var:holder"}],
        )
    )
    assert placeholder_issues(definition) == []


@pytest.mark.parametrize(
    "url",
    [
        "https://{{ ctx.session_id }}.example.com/x",
        "https://api.example.com{{ var.path }}",
        "https://api.example.com:{{ var.port }}/x",
        "https://{{ var.user }}@api.example.com/x",
        "{{ var.scheme }}://api.example.com/x",
        "{{ var.whole_url }}",
    ],
)
def test_http_definition_placeholder_in_scheme_or_authority_is_refused(url: str) -> None:
    with pytest.raises(ValidationError, match="scheme, host or port"):
        HttpToolDefinition.model_validate(_http(url=url))


def test_http_definition_placeholder_in_a_header_is_refused() -> None:
    with pytest.raises(ValidationError, match="may not be used in a header"):
        HttpToolDefinition.model_validate(_http(headers={"X-Caller": "{{ ctx.caller_phone }}"}))


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"url": "https://api.example.com/{{ ctx.email }}"}, "url"),
        ({"url": "https://api.example.com/{{ ctx.a-b }}"}, "url"),
        ({"url": "https://api.example.com/{{ var.PolicyNo }}"}, "url"),
        ({"method": "POST", "body_template": '{"a": "{{ ctx.nope }}"}'}, "body_template"),
        ({"requires_vars": ["Policy"]}, "requires_vars[0]"),
        ({"confirm_readback": ["phone"]}, "confirm_readback[0]"),
        ({"confirm_readback": ["confirmed"]}, "confirm_readback[0]"),
        ({"confirm_readback": ["email", "email"]}, "confirm_readback[1]"),
    ],
)
def test_placeholder_issues_http_problems_name_their_field(overrides: dict[str, Any], field: str) -> None:
    assert field in _fields(_http(**overrides))
    with pytest.raises(ValidationError):
        HttpToolDefinition.model_validate(_http(**overrides))


def test_placeholder_issues_confirmed_argument_clash_is_refused() -> None:
    params = {"type": "object", "properties": {"email": {"type": "string"}, "confirmed": {"type": "boolean"}}}
    with pytest.raises(ValidationError, match="already has an argument named 'confirmed'"):
        HttpToolDefinition.model_validate(_http(parameters=params, confirm_readback=["email"]))


def test_http_definition_bindings_are_bounded() -> None:
    too_many = [{"to": "note"}] * (MAX_BINDINGS + 1)
    with pytest.raises(ValidationError):
        HttpToolDefinition.model_validate(_http(bindings=too_many))


def test_placeholder_issues_accepts_raw_json_too() -> None:
    raw = _http(url="https://{{ ctx.agent_id }}.example.com/", bindings=[{"to": "link:pay"}])
    fields = _fields(raw)
    assert "url" in fields
    assert "bindings[0].to" in fields


# ------------------------------------------------------------------------ provider definitions


def test_provider_definition_pinned_arguments_accept_placeholders() -> None:
    definition = ProviderToolDefinition.model_validate(
        _provider(
            pinned_arguments={"timezone": "{{ ctx.timezone }}", "priority": 2, "notify": True},
            confirm_readback=["email"],
            bindings=[{"path": "/id", "to": "details:ticket.number"}],
        )
    )
    assert definition.pinned_arguments["priority"] == 2
    assert placeholder_issues(definition) == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"pinned_arguments": {"tz": "{{ ctx.zone }}"}},
        {"pinned_arguments": {"email": "a@example.com"}, "confirm_readback": ["email"]},
        {"headers": {"x-api-key": "{{ var.key }}"}},
    ],
)
def test_provider_definition_problems_are_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ProviderToolDefinition.model_validate(_provider(**overrides))


# ------------------------------------------------------------------------ MCP definitions


def test_mcp_definition_tool_context_is_accepted_per_tool() -> None:
    definition = McpServerDefinition.model_validate(
        {
            "name": "crm",
            "url": "https://mcp.example.com/mcp",
            "allowed_tools": ["find_contact"],
            "tool_context": {
                "find_contact": {
                    "requires_vars": ["account_no"],
                    "pinned_arguments": {"account": "{{ var.account_no }}"},
                    "bindings": [{"path": "/name", "to": "details:card.name"}],
                }
            },
        }
    )
    assert definition.tool_context["find_contact"].requires_vars == ["account_no"]


def test_mcp_definition_tool_context_outside_allowed_tools_is_refused() -> None:
    with pytest.raises(ValidationError, match="outside allowed_tools"):
        McpServerDefinition.model_validate(
            {
                "name": "crm",
                "url": "https://mcp.example.com/mcp",
                "allowed_tools": ["find_contact"],
                "tool_context": {"delete_contact": {}},
            }
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"url": "https://mcp.example.com/{{ ctx.session_id }}"},
        {"auth": {"kind": "header", "headers": {"Authorization": "{{ var.token }}"}}},
    ],
)
def test_mcp_definition_placeholders_in_url_or_headers_are_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="may not be used"):
        McpServerDefinition.model_validate({"name": "crm", "url": "https://mcp.example.com/mcp", **overrides})


def test_mcp_definition_readback_checked_against_cached_schema() -> None:
    raw = {
        "kind": "mcp",
        "name": "crm",
        "url": "https://mcp.example.com/mcp",
        "cached_tools": [
            {"name": "find_contact", "input_schema": {"type": "object", "properties": {"q": {}}}}
        ],
        "tool_context": {"find_contact": {"confirm_readback": ["email"]}},
    }
    assert "tool_context.find_contact.confirm_readback[0]" in _fields(raw)
