"""V6-17 (D-V6-28): flow `tool` steps — save-time validation, draft paths and the node spec."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from conftest import create_agent, inference_config
from lkap_contracts.agent_config import AgentConfig, PanelLayout
from lkap_contracts.flow import FlowSpec
from lkap_contracts.ui_protocol import BlockSpec

from lkap_api.config_service import ValidationContext, register_validator
from lkap_api.flows import draft_flow_issues, flow_issues

register_validator(flow_issues)


def _http(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": "http",
        "name": "policy_lookup",
        "description": "Look a policy up",
        "parameters": {
            "type": "object",
            "properties": {"policy": {"type": "string"}, "region": {"type": "string", "default": "eu"}},
            "required": ["policy", "region"],
        },
        "method": "GET",
        "url": "https://api.example.com/policies/{{ policy }}",
        "allowed_hosts": ["api.example.com"],
    }
    body.update(overrides)
    return body


def _mcp(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": "mcp",
        "name": "crm",
        "url": "https://mcp.example.com/mcp",
        "allowed_tools": ["find_contact", "create_ticket"],
    }
    body.update(overrides)
    return body


def _flow(**step: Any) -> dict[str, Any]:
    node = {
        "id": "lookup",
        "kind": "tool",
        "tool": "policy_lookup",
        "arguments": {"policy": "{{ var.policy_no }}"},
        "bindings": [
            {"path": "/holder", "to": "var:holder"},
            {"path": "/status", "to": "details:card.status"},
        ],
        "on": {"ok": "found_it", "error": "failed"},
        **step,
    }
    return {
        "nodes": [
            {"id": "start", "kind": "start"},
            {
                "id": "ask",
                "kind": "agent",
                "instructions": "Ask for the policy number.",
                "extract": ["policy_no"],
            },
            node,
            {"id": "found", "kind": "agent", "instructions": "Confirm the holder."},
            {"id": "done", "kind": "end"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "ask", "condition": "always"},
            {"id": "e2", "source": "ask", "target": "lookup", "condition": "The caller gave the number."},
            {"id": "found_it", "source": "lookup", "target": "found"},
            {"id": "failed", "source": "lookup", "target": "done"},
            {"id": "e3", "source": "found", "target": "done", "condition": "confirmed"},
        ],
        "variables": [{"name": "policy_no"}, {"name": "holder"}],
    }


def _config(flow: dict[str, Any]) -> AgentConfig:
    config = inference_config()
    config.panel = PanelLayout(
        blocks=[BlockSpec(id="card", type="details"), BlockSpec(id="pay", type="link")]
    )
    config.tools.tool_ids = ["t1"]
    config.flow = FlowSpec.model_validate(flow)
    return config


def _issues(
    flow: dict[str, Any], definition: dict[str, Any] | None = None, **ctx: Any
) -> list[tuple[str, str, str]]:
    definition = definition or _http()
    context = ValidationContext(
        config=_config(flow),
        tool_names_by_id={"t1": str(definition["name"])},
        tool_definitions_by_id={"t1": definition},
        **ctx,
    )
    return [(i.path, i.severity, i.message) for i in flow_issues(context)]


def _paths(issues: list[tuple[str, str, str]], severity: str | None = None) -> list[str]:
    return [path for path, level, _ in issues if severity is None or level == severity]


def test_a_valid_tool_step_has_no_issues() -> None:
    assert _issues(_flow()) == []


def test_a_flow_without_tool_steps_gets_no_tool_step_issues() -> None:
    flow = _flow()
    flow["nodes"] = [n for n in flow["nodes"] if n["id"] not in ("lookup", "found")]
    flow["edges"] = [
        {"id": "e1", "source": "start", "target": "ask", "condition": "always"},
        {"id": "e2", "source": "ask", "target": "done", "condition": "done"},
    ]
    assert _issues(flow) == []


@pytest.mark.parametrize(
    ("tool", "fragment"),
    [("not_attached", "not one of the agent's tools"), ("end_call", "is built in")],
)
def test_a_tool_step_calls_only_an_attached_tool(tool: str, fragment: str) -> None:
    issues = _issues(_flow(tool=tool))
    assert [(p, s) for p, s, _ in issues] == [("flow.nodes[2].tool", "error")]
    assert fragment in issues[0][2]


def test_a_tool_step_skips_the_attached_check_without_tool_rows() -> None:
    context = ValidationContext(config=_config(_flow(tool="anything")))
    assert [i.path for i in flow_issues(context) if i.severity == "error"] == []


def test_a_tool_that_reads_back_first_cannot_run_as_a_step() -> None:
    issues = _issues(_flow(), _http(confirm_readback=["policy"]))
    assert _paths(issues, "error") == ["flow.nodes[2].tool"]
    assert "reads values back" in issues[0][2]


def test_an_argument_the_tool_lacks_and_a_required_one_missing_are_warnings() -> None:
    definition = _http(
        parameters={
            "type": "object",
            "properties": {"policy": {"type": "string"}, "tz": {"type": "string"}},
            "required": ["policy", "tz"],
        }
    )
    issues = _issues(_flow(arguments={"policy": "{{ var.policy_no }}", "extra": "x"}), definition)
    assert [(p, s) for p, s, _ in issues] == [
        ("flow.nodes[2].arguments.extra", "warning"),
        ("flow.nodes[2].arguments", "warning"),
    ]
    assert "needs 'tz'" in issues[1][2]


@pytest.mark.parametrize(
    ("step", "path", "fragment"),
    [
        ({"mcp_tool": None}, "flow.nodes[2].mcp_tool", "choose which"),
        ({"mcp_tool": "delete_all"}, "flow.nodes[2].mcp_tool", "does not offer"),
    ],
)
def test_a_server_step_names_one_of_its_tools(step: dict[str, Any], path: str, fragment: str) -> None:
    issues = _issues(_flow(tool="crm", **step), _mcp())
    errors = [(p, m) for p, s, m in issues if s == "error"]
    assert [p for p, _ in errors] == [path]
    assert fragment in errors[0][1]


def test_a_server_step_with_one_of_its_tools_is_valid() -> None:
    assert _paths(_issues(_flow(tool="crm", mcp_tool="find_contact"), _mcp()), "error") == []


def test_a_server_tool_with_read_back_cannot_run_as_a_step() -> None:
    definition = _mcp(tool_context={"create_ticket": {"confirm_readback": ["email"]}})
    issues = _issues(_flow(tool="crm", mcp_tool="create_ticket"), definition)
    assert _paths(issues, "error") == ["flow.nodes[2].tool"]


def test_a_single_tool_with_a_server_tool_name_is_an_error() -> None:
    assert _paths(_issues(_flow(mcp_tool="x")), "error") == ["flow.nodes[2].mcp_tool"]


def test_bindings_write_declared_variables_and_real_blocks_only() -> None:
    bindings = [
        {"path": "/a", "to": "var:not_declared"},
        {"path": "/b", "to": "details:missing.key"},
        {"path": "/c", "to": "table:pay"},
    ]
    issues = _issues(_flow(bindings=bindings))
    assert _paths(issues, "error") == [
        "flow.nodes[2].bindings[0].to",
        "flow.nodes[2].bindings[1].to",
        "flow.nodes[2].bindings[2].to",
    ]
    assert "Add it under Variables" in issues[0][2]


def test_a_variable_nothing_sets_and_a_step_without_an_error_path_are_warnings() -> None:
    flow = _flow(arguments={"policy": "{{ var.nobody_sets_this }}"}, on={"ok": "found_it"})
    flow["edges"] = [e for e in flow["edges"] if e["id"] != "failed"]
    issues = _issues(flow)
    assert [(p, s) for p, s, _ in issues] == [
        ("flow.nodes[2].arguments.policy", "warning"),
        ("flow.nodes[2].on.error", "warning"),
    ]


def test_tool_step_paths_need_no_condition() -> None:
    issues = _issues(_flow())
    assert not [p for p in _paths(issues) if p.startswith("flow.edges")]


# ------------------------------------------------------------------------ drafts


def test_draft_tool_step_rules_point_at_the_step_and_the_path() -> None:
    flow = _flow(on={"error": "nowhere"})
    spec, issues = draft_flow_issues(flow)
    assert spec is None
    assert [(i.path, i.message) for i in issues] == [
        ("flow.nodes[2].on.ok", "a tool step needs a path for when the tool succeeds"),
        ("flow.nodes[2].on.error", "unknown path 'nowhere'"),
        (
            "flow.edges[2]",
            "this path leaves a tool step, but no outcome of the step (success, nothing found, "
            "failure) uses it",
        ),
        (
            "flow.edges[3]",
            "this path leaves a tool step, but no outcome of the step (success, nothing found, "
            "failure) uses it",
        ),
    ]


def test_draft_loop_of_tool_steps_marks_each_step() -> None:
    flow = {
        "nodes": [
            {"id": "start", "kind": "start"},
            {"id": "a", "kind": "tool", "tool": "t", "on": {"ok": "ab"}},
            {"id": "b", "kind": "tool", "tool": "t", "on": {"ok": "ba"}},
        ],
        "edges": [
            {"id": "s", "source": "start", "target": "a"},
            {"id": "ab", "source": "a", "target": "b"},
            {"id": "ba", "source": "b", "target": "a"},
        ],
    }
    spec, issues = draft_flow_issues(flow)
    assert spec is None
    assert [i.path for i in issues] == ["flow.nodes[1]", "flow.nodes[2]"]


def test_draft_bad_tool_step_field_path_has_no_kind_segment() -> None:
    flow = _flow(timeout_s=99)
    spec, issues = draft_flow_issues(flow)
    assert spec is None
    assert [i.path for i in issues] == ["flow.nodes[2].timeout_s"]


def test_a_valid_tool_step_draft_parses() -> None:
    spec, issues = draft_flow_issues(_flow())
    assert issues == []
    assert spec is not None


# ------------------------------------------------------------------------ http


async def test_node_specs_offer_the_tool_step(admin_client: httpx.AsyncClient) -> None:
    body = (await admin_client.get("/v1/flows/node-specs")).json()
    (tool,) = [n for n in body["nodes"] if n["kind"] == "tool"]
    assert tool["label"] == "Tool step"
    assert {"tool", "mcp_tool", "arguments", "bindings", "timeout_s", "on"} <= set(
        tool["json_schema"]["properties"]
    )


async def test_saving_a_tool_step_that_reads_back_is_refused(admin_client: httpx.AsyncClient) -> None:
    created = await admin_client.post(
        "/v1/tools",
        json={"kind": "http", "name": "policy_lookup", "definition": _http(confirm_readback=["policy"])},
    )
    assert created.status_code == 201, created.text
    config: dict[str, Any] = json.loads(inference_config().model_dump_json())
    config["flow"] = _flow(bindings=[{"path": "/holder", "to": "var:holder"}])
    config["tools"]["tool_ids"] = [created.json()["id"]]

    response = await admin_client.post("/v1/agents", json={"name": "Demo — Tool step", "config": config})

    assert response.status_code == 422, response.text
    assert "flow.nodes[2].tool" in response.text


async def test_the_validate_endpoint_reports_a_tool_step_that_is_not_attached(
    admin_client: httpx.AsyncClient,
) -> None:
    agent = await create_agent(admin_client)
    flow = _flow(bindings=[])
    response = await admin_client.post(f"/v1/agents/{agent['id']}/flow/validate", json={"flow": flow})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert [i["path"] for i in body["issues"] if i["severity"] == "error"] == ["flow.nodes[2].tool"]
