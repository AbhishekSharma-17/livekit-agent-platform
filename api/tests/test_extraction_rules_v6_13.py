"""V6-13 (D-V6-24/25): the extraction and rules validator, the save-time refusal and the privacy tier."""

from __future__ import annotations

import json
from typing import Any

import httpx
from conftest import inference_config
from lkap_contracts.agent_config import AgentConfig, PanelLayout
from lkap_contracts.extraction import ExtractionConfig
from lkap_contracts.flow import FlowSpec
from lkap_contracts.rules import Rule
from lkap_contracts.ui_protocol import BlockSpec

from lkap_api.config_service import ValidationContext, extraction_rules_issues, validate
from lkap_api.privacy.scrub import VALUE_PAYLOAD_KEYS, _drop_tool_payload

_PANEL = PanelLayout(
    blocks=[
        BlockSpec(id="summary", type="details"),
        BlockSpec(id="todo", type="checklist"),
        BlockSpec(id="pay", type="link"),
    ]
)

_EXTRACTION = {
    "enabled": True,
    "fields": [
        {"name": "policy_number", "required": True, "show_in": "details:summary"},
        {"name": "hazard"},
    ],
    "still_needed": "checklist",
}

_RULES = [
    {
        "id": "policy_known",
        "when": "var.policy_number is set",
        "then": [{"do": "checklist.set_item", "id": "verify", "label": "Verify the policy"}],
    },
    {
        "id": "danger",
        "when": "var.hazard matches /fire|smoke/i",
        "then": [{"do": "status.set", "label": "Safety first", "tone": "danger"}],
    },
]


def _config(**overrides: Any) -> AgentConfig:
    config = inference_config()
    config.panel = _PANEL
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def _issues(ctx: ValidationContext) -> list[tuple[str, str, str]]:
    return [(issue.path, issue.severity, issue.message) for issue in extraction_rules_issues(ctx)]


def test_extraction_rules_issues_agent_without_them_has_none() -> None:
    assert _issues(ValidationContext(config=_config())) == []


def test_extraction_rules_issues_valid_config_is_clean() -> None:
    config = _config(
        extraction=ExtractionConfig.model_validate(_EXTRACTION),
        rules=[Rule.model_validate(rule) for rule in _RULES],
    )
    ctx = ValidationContext(
        config=config, tool_definitions_by_id={}, tool_names_by_id={}, pack_tool_names=frozenset()
    )
    assert _issues(ctx) == []
    assert not [issue for issue in validate(ctx).issues if issue.path.startswith(("rules", "extraction"))]


def test_extraction_rules_issues_rule_naming_a_missing_block_is_an_error() -> None:
    rule = Rule.model_validate(
        {
            "id": "r",
            "when": "var.hazard is set",
            "then": [{"do": "details.set", "block_id": "gone", "key": "k", "value": "v"}],
        }
    )
    config = _config(extraction=ExtractionConfig.model_validate(_EXTRACTION), rules=[rule])
    issues = _issues(ValidationContext(config=config))
    assert ("rules[0].then[0].block_id", "error") in {(path, severity) for path, severity, _ in issues}


def test_extraction_rules_issues_show_in_a_link_block_is_an_error() -> None:
    extraction = ExtractionConfig.model_validate(
        {"enabled": True, "fields": [{"name": "a", "show_in": "details:pay"}]}
    )
    (issue,) = _issues(ValidationContext(config=_config(extraction=extraction)))
    assert issue[:2] == ("extraction.fields[0].show_in", "error")
    assert "link block" in issue[2]


def test_extraction_rules_issues_unknown_tool_and_variable_warn_when_names_are_known() -> None:
    rule = Rule.model_validate(
        {
            "id": "r",
            "when": "tool.lookup_policy.ok and var.unset is set",
            "then": [{"do": "note.push", "text": "x"}],
        }
    )
    config = _config(rules=[rule])
    ctx = ValidationContext(
        config=config, tool_definitions_by_id={}, tool_names_by_id={}, pack_tool_names=frozenset()
    )
    messages = [message for _, severity, message in _issues(ctx) if severity == "warning"]
    assert any("tool.lookup_policy" in message for message in messages)
    assert any("var.unset" in message for message in messages)


def test_extraction_rules_issues_attached_tool_and_bound_variable_count() -> None:
    rule = Rule.model_validate(
        {
            "id": "r",
            "when": "tool.lookup_policy.ok and var.holder is set",
            "then": [{"do": "note.push", "text": "x"}],
        }
    )
    config = _config(rules=[rule])
    config.tools.tool_ids = ["t1"]
    definition = {
        "kind": "http",
        "name": "lookup_policy",
        "bindings": [{"path": "/holder", "to": "var:holder"}],
    }
    ctx = ValidationContext(
        config=config,
        tool_definitions_by_id={"t1": definition},
        tool_names_by_id={"t1": "lookup_policy"},
        pack_tool_names=frozenset(),
    )
    assert _issues(ctx) == []


def test_extraction_rules_issues_mcp_tool_skips_the_tool_name_check() -> None:
    rule = Rule.model_validate(
        {"id": "r", "when": "tool.crm_find.ok", "then": [{"do": "note.push", "text": "x"}]}
    )
    config = _config(rules=[rule])
    config.tools.tool_ids = ["t1"]
    ctx = ValidationContext(
        config=config,
        tool_definitions_by_id={"t1": {"kind": "mcp", "name": "crm", "url": "https://mcp.example.com/mcp"}},
        tool_names_by_id={"t1": "crm"},
        pack_tool_names=frozenset(),
    )
    assert _issues(ctx) == []


def test_extraction_rules_issues_node_exit_and_flow_overlap() -> None:
    flow = FlowSpec.model_validate(
        {
            "nodes": [
                {"id": "start", "kind": "start"},
                {"id": "intake", "kind": "agent", "extract": ["hazard"]},
            ],
            "edges": [{"id": "e1", "source": "start", "target": "intake"}],
            "variables": [{"name": "hazard"}],
        }
    )
    extraction = ExtractionConfig.model_validate(
        {**_EXTRACTION, "triggers": [{"kind": "node_exit", "nodes": ["nowhere"]}]}
    )
    issues = {
        (path, severity)
        for path, severity, _ in _issues(ValidationContext(config=_config(flow=flow, extraction=extraction)))
    }
    assert ("extraction.fields[1].name", "warning") in issues
    assert ("extraction.triggers[0].nodes", "warning") in issues


def test_scrub_drops_extraction_values_but_keeps_the_names() -> None:
    payload = {"trigger": "turn", "status": "ok", "fields": {"a": True}, "values": {"a": "PX-1"}}
    kept, removed = _drop_tool_payload("extraction", payload, VALUE_PAYLOAD_KEYS)
    assert kept == {"trigger": "turn", "status": "ok", "fields": {"a": True}} and removed == 1
    assert _drop_tool_payload("rule_fired", {"rule_id": "r"}, VALUE_PAYLOAD_KEYS) == ({"rule_id": "r"}, 0)


# --------------------------------------------------------------------------- the api


async def test_create_agent_with_extraction_and_rules_round_trips(admin_client: httpx.AsyncClient) -> None:
    config = json.loads(_config().model_dump_json())
    config["panel"]["blocks"] = [block for block in config["panel"]["blocks"] if block["type"] != "link"]
    config["extraction"] = _EXTRACTION
    config["rules"] = _RULES
    response = await admin_client.post("/v1/agents", json={"name": "Demo — intake", "config": config})
    assert response.status_code == 201, response.text
    stored = response.json()["config"]
    assert stored["extraction"]["fields"][0]["name"] == "policy_number"
    assert [rule["id"] for rule in stored["rules"]] == ["policy_known", "danger"]
    validated = await admin_client.post(f"/v1/agents/{response.json()['id']}/validate")
    assert validated.status_code == 200, validated.text
    paths = [issue["path"] for issue in validated.json()["issues"]]
    assert not [path for path in paths if path.startswith(("rules", "extraction"))]


async def test_create_agent_with_an_unreadable_condition_is_refused(admin_client: httpx.AsyncClient) -> None:
    config = json.loads(_config().model_dump_json())
    config["rules"] = [{"id": "bad", "when": "__import__('os')", "then": [{"do": "note.push", "text": "x"}]}]
    response = await admin_client.post("/v1/agents", json={"name": "Demo — bad rule", "config": config})
    assert response.status_code == 422, response.text
    assert "does not read" in response.text


async def test_create_agent_without_the_new_keys_reads_them_as_off(admin_client: httpx.AsyncClient) -> None:
    """Compatibility: a config posted without `extraction`/`rules` stores them off and empty."""
    config = json.loads(inference_config().model_dump_json())
    del config["extraction"], config["rules"]
    response = await admin_client.post("/v1/agents", json={"name": "Demo — plain", "config": config})
    assert response.status_code == 201, response.text
    stored = response.json()["config"]
    assert stored["extraction"]["enabled"] is False and stored["rules"] == []
