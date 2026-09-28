"""V6-07 (D-V6-22/23): the tool-context validator and the save-time refusal of misplaced placeholders."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from conftest import inference_config
from lkap_contracts.agent_config import AgentConfig, PanelLayout
from lkap_contracts.flow import FlowSpec
from lkap_contracts.ui_protocol import BlockSpec

from lkap_api.config_service import ValidationContext, tool_context_issues, validate

_PANEL = PanelLayout(
    blocks=[
        BlockSpec(id="card", type="details"),
        BlockSpec(id="results", type="table"),
        BlockSpec(id="pay", type="link"),
        BlockSpec(id="ask", type="form"),
    ]
)


def _http(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": "http",
        "name": "lookup_policy",
        "description": "Look a policy up",
        "parameters": {"type": "object", "properties": {"policy_no": {"type": "string"}}},
        "method": "GET",
        "url": "https://api.example.com/policies/{{ policy_no }}",
        "allowed_hosts": ["api.example.com"],
    }
    body.update(overrides)
    return body


def _config(**overrides: Any) -> AgentConfig:
    config = inference_config()
    config.panel = _PANEL
    config.tools.tool_ids = ["t1"]
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def _issues(definition: dict[str, Any], **overrides: Any) -> list[tuple[str, str, str]]:
    ctx = ValidationContext(config=_config(**overrides), tool_definitions_by_id={"t1": definition})
    return [(issue.path, issue.severity, issue.message) for issue in tool_context_issues(ctx)]


def test_tool_context_issues_definition_without_new_fields_has_none() -> None:
    assert _issues(_http()) == []
    assert _issues({"kind": "mcp", "name": "crm", "url": "https://mcp.example.com/mcp"}) == []


def test_tool_context_issues_skipped_without_tool_definitions() -> None:
    assert tool_context_issues(ValidationContext(config=_config())) == []


def test_tool_context_issues_valid_bindings_and_placeholders_pass() -> None:
    definition = _http(
        url="https://api.example.com/policies/{{ policy_no }}?tz={{ ctx.timezone }}",
        bindings=[
            {"path": "/holder", "to": "details:card.holder"},
            {"path": "/claims", "to": "table:results"},
            {"to": "var:holder"},
        ],
    )
    assert _issues(definition) == []


@pytest.mark.parametrize(
    ("to", "fragment"),
    [
        ("details:missing.key", "not on this agent's panel"),
        ("table:card", "it is a details block"),
        ("details:pay.url", "never written"),
        ("table:ask", "never written"),
    ],
)
def test_tool_context_issues_binding_to_a_wrong_block_is_an_error(to: str, fragment: str) -> None:
    ((path, severity, message),) = _issues(_http(bindings=[{"to": to}]))
    assert path == "tools[0].definition.bindings[0].to"
    assert severity == "error"
    assert fragment in message


@pytest.mark.parametrize("to", ["status", "note", "checklist:policy_found"])
def test_tool_context_issues_display_target_without_its_block_warns(to: str) -> None:
    ((_path, severity, message),) = _issues(_http(bindings=[{"to": to}]))
    assert severity == "warning"
    assert "no" in message and "block" in message


def test_tool_context_issues_a_stored_row_with_a_host_placeholder_is_an_error() -> None:
    """The contract refuses it at save; a row stored another way is still caught here."""
    issues = _issues(_http(url="https://{{ var.tenant }}.example.com/x"))
    assert ("tools[0].definition.url", "error") in [(path, severity) for path, severity, _m in issues]


def test_tool_context_issues_mcp_per_tool_bindings_are_checked() -> None:
    definition = {
        "kind": "mcp",
        "name": "crm",
        "url": "https://mcp.example.com/mcp",
        "tool_context": {"find_contact": {"bindings": [{"to": "details:pay.x"}]}},
    }
    ((path, severity, _message),) = _issues(definition)
    assert path == "tools[0].definition.tool_context.find_contact.bindings[0].to"
    assert severity == "error"


def test_tool_context_issues_flow_variable_nothing_sets_warns_once() -> None:
    flow = FlowSpec.model_validate(
        {
            "variables": [{"name": "policy_no"}],
            "nodes": [
                {"id": "start", "kind": "start", "greeting": "Hi"},
                {"id": "main", "kind": "agent", "instructions": "Help."},
            ],
            "edges": [{"id": "e1", "source": "start", "target": "main"}],
        }
    )
    definition = _http(
        url="https://api.example.com/p?n={{ var.policy_no }}&d={{ var.dob }}",
        requires_vars=["dob", "holder"],
        bindings=[{"to": "var:holder"}],
    )
    issues = _issues(definition, flow=flow)
    assert [(path, severity) for path, severity, _m in issues] == [
        ("tools[0].definition.requires_vars", "warning")
    ]
    assert "'dob'" in issues[0][2]


def test_validate_runs_the_tool_context_check() -> None:
    ctx = ValidationContext(
        config=_config(), tool_definitions_by_id={"t1": _http(bindings=[{"to": "table:pay"}])}
    )
    result = validate(ctx)
    assert result.ok is False
    assert any(issue.path == "tools[0].definition.bindings[0].to" for issue in result.issues)


# ---------------------------------------------------------------------- save time (the contract)


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"url": "https://{{ ctx.session_id }}.example.com/x"}, "scheme, host or port"),
        ({"url": "https://api.example.com:{{ var.port }}/x"}, "scheme, host or port"),
        ({"headers": {"X-Caller": "{{ ctx.caller_phone }}"}}, "header"),
        ({"url": "https://api.example.com/{{ ctx.home_address }}"}, "not a session value"),
        ({"bindings": [{"to": "link:pay"}]}, "binding target"),
    ],
)
async def test_create_tool_misplaced_context_is_refused_at_save(
    admin_client: httpx.AsyncClient, overrides: dict[str, Any], fragment: str
) -> None:
    definition = _http(**overrides)
    response = await admin_client.post(
        "/v1/tools", json={"kind": "http", "name": definition["name"], "definition": definition}
    )
    assert response.status_code == 422, response.text
    assert fragment in response.text


async def test_create_tool_with_context_fields_round_trips(admin_client: httpx.AsyncClient) -> None:
    definition = _http(
        url="https://api.example.com/policies/{{ policy_no }}?tz={{ ctx.timezone }}",
        requires_vars=["policy_no"],
        confirm_readback=["policy_no"],
        bindings=[{"path": "/holder", "to": "details:card.holder"}],
    )
    response = await admin_client.post(
        "/v1/tools", json={"kind": "http", "name": definition["name"], "definition": definition}
    )
    assert response.status_code == 201, response.text
    stored = (await admin_client.get(f"/v1/tools/{response.json()['id']}")).json()["definition"]
    assert stored["url"] == definition["url"]
    assert stored["requires_vars"] == ["policy_no"]
    assert stored["confirm_readback"] == ["policy_no"]
    assert stored["bindings"] == [{"path": "/holder", "to": "details:card.holder"}]
