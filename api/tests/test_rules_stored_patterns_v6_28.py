"""V6-28 (R-V6-3 #166): a stored rule whose `matches` pattern the S6-4 scanner refuses loads,
validates with one error on that rule, and saving it is still refused."""

from __future__ import annotations

import json
from typing import Any

import httpx
from conftest import create_agent, inference_config
from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.rules import slow_pattern_message

from lkap_api.config_service import ValidationContext, validate
from lkap_api.db.models import Agent
from lkap_api.db.session import Database

REFUSED = "(fire|smoke)+"

_RULES: list[dict[str, Any]] = [
    {
        "id": "policy_known",
        "when": "var.policy_number is set",
        "then": [{"do": "status.set", "label": "Known"}],
    },
    {
        "id": "danger",
        "when": f"var.hazard matches /{REFUSED}/i",
        "then": [{"do": "status.set", "label": "Safety first", "tone": "danger"}],
    },
]
_EXTRACTION = {"enabled": True, "fields": [{"name": "policy_number"}, {"name": "hazard"}]}


def _stored_config() -> dict[str, Any]:
    config: dict[str, Any] = json.loads(inference_config().model_dump_json())
    config["extraction"] = _EXTRACTION
    config["rules"] = _RULES
    return config


def _rule_errors(issues: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [
        (issue["path"], issue["message"])
        for issue in issues
        if issue["path"].startswith("rules") and issue["severity"] == "error"
    ]


async def test_a_stored_rule_with_a_refused_pattern_loads_and_is_an_error_at_its_path(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    config = AgentConfig.model_validate(_stored_config())  # the contract reads the grammar only
    result = validate(ValidationContext(config=config))
    assert not result.ok
    expected = [("rules[1].when", slow_pattern_message("danger", REFUSED))]
    assert _rule_errors([issue.model_dump() for issue in result.issues]) == expected

    # An agent stored before S6-4 widened the scanner: it still loads and says what to fix.
    agent = await create_agent(admin_client, name="Demo — stored rule")
    async with database.session() as session:
        row = await session.get(Agent, agent["id"])
        assert row is not None
        row.config = _stored_config()
    loaded = await admin_client.get(f"/v1/agents/{agent['id']}")
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["config"]["rules"][1]["when"] == f"var.hazard matches /{REFUSED}/i"
    validated = await admin_client.post(f"/v1/agents/{agent['id']}/validate")
    assert validated.status_code == 200, validated.text
    assert _rule_errors(validated.json()["issues"]) == expected

    # Saving it is still refused (S6-4), now on the rule's path.
    saved = await admin_client.put(f"/v1/agents/{agent['id']}", json={"config": _stored_config()})
    assert saved.status_code == 422, saved.text
    assert _rule_errors(saved.json()["error"]["details"]["issues"]) == expected


async def test_creating_an_agent_with_a_refused_pattern_is_refused_at_the_rule(
    admin_client: httpx.AsyncClient,
) -> None:
    response = await admin_client.post(
        "/v1/agents", json={"name": "Demo — slow rule", "config": _stored_config()}
    )
    assert response.status_code == 422, response.text
    assert [path for path, _ in _rule_errors(response.json()["error"]["details"]["issues"])] == [
        "rules[1].when"
    ]
