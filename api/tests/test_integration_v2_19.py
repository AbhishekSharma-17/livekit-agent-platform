"""V2-19 integration rulings on the api side: R-V2-17 block configs and V2-16-4 re-score QA."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from conftest import create_agent, inference_config
from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.blocks import validate_panel_block_configs
from lkap_contracts.flow import FlowSpec

from lkap_api import panels
from lkap_api.config_service import VALIDATORS, ValidationContext, register_validator, validate
from lkap_api.db.models import Agent, Credential, SessionQa
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.service import JobsService
from lkap_api.qa.scorer import score_session
from lkap_api.settings import Settings
from lkap_api.vault import Vault

# Registered on import of `lkap_api.panels`; register explicitly so these tests never
# depend on collection order (the `lkap_api.catalogs.validation` pattern).
register_validator(panels.block_config_issues)


def _config_with_blocks(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    config: dict[str, Any] = json.loads(inference_config().model_dump_json())
    config["panel"] = {"panel_id": "composite", "layout": "side", "blocks": blocks}
    return config


# ------------------------------------------------------------------ R-V2-17 block configs
def test_the_block_config_validator_is_registered() -> None:
    assert panels.block_config_issues in VALIDATORS


async def test_an_unknown_block_config_key_is_a_422_at_its_path(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client, published=False)

    response = await admin_client.put(
        f"/v1/agents/{agent['id']}",
        json={"config": _config_with_blocks([{"id": "items", "type": "table", "config": {"foo": 1}}])},
    )

    assert response.status_code == 422, response.text
    issues = response.json()["error"]["details"]["issues"]
    assert ("panel.blocks[0].config.foo", "error") in [(i["path"], i["severity"]) for i in issues]


async def test_declared_block_config_keys_save(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client, published=False)
    blocks = [
        {"id": "items", "type": "table", "config": {"columns": [{"key": "item", "label": "Item"}]}},
        {"id": "policy", "type": "document", "config": {"url": "https://example.com/p.pdf", "page": 2}},
        {"id": "progress", "type": "custom", "config": {"kind": "flow_progress", "pack_field": [1, 2]}},
    ]

    response = await admin_client.put(
        f"/v1/agents/{agent['id']}", json={"config": _config_with_blocks(blocks)}
    )

    assert response.status_code == 200, response.text


def test_the_validator_reports_nothing_for_the_default_panel() -> None:
    assert validate(ValidationContext(config=inference_config())).issues == []


@pytest.mark.parametrize("module", ["packs.generic.manifest", "packs.insurance_claim.manifest"])
def test_both_pack_manifests_default_panels_validate(module: str) -> None:
    """R-V2-17 acceptance: the real packs' `default_panel` blocks fit the block schemas."""
    import importlib

    manifest = importlib.import_module(module).MANIFEST
    blocks = manifest.default_panel.blocks if manifest.default_panel is not None else []
    assert validate_panel_block_configs(blocks) == []


# --------------------------------------------------------------- V2-16-4 re-score QA
CHAT_URL = "https://api.openai.com/v1/chat/completions"


async def test_rescore_follows_effective_qa_for_a_flow_qa_node(
    database: Database, settings: Settings
) -> None:
    """A flow `qa` node turns QA on (R-V2-11) and its rubric is the judge's system prompt."""
    vault = Vault(settings.master_key)
    async with database.session() as session:
        credential = Credential(
            provider_id="openai-llm",
            label="judge",
            ciphertext=vault.encrypt({"api_key": "sk-test-key"}),
            fingerprint="…test",
        )
        session.add(credential)
        await session.flush()
        credential_id = credential.id
    flow = FlowSpec.model_validate(
        {
            "nodes": [
                {"id": "start", "kind": "start", "greeting": "Hello."},
                {"id": "ask", "kind": "agent", "label": "Ask", "instructions": "Ask for the name."},
                {"id": "done", "kind": "end", "disposition": "completed"},
                {"id": "qa", "kind": "qa", "rubric_prompt": "Score empathy only."},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "ask", "condition": "always"},
                {"id": "e2", "source": "ask", "target": "done", "condition": "The caller gave a name."},
            ],
        }
    )
    config = AgentConfig.model_validate(
        {
            **json.loads(inference_config().model_dump_json()),
            "flow": flow.model_dump(mode="json"),
            "qa": {
                "enabled": False,
                "model": {"provider_id": "openai-llm", "credential_id": credential_id},
            },
        }
    )
    async with database.session() as session:
        agent = Agent(slug="qa-flow-agent", name="QA flow", config=json.loads(config.model_dump_json()))
        session.add(agent)
        await session.flush()
        row = SessionRow(
            agent_id=agent.id,
            config_version=1,
            room_name="room-qa-flow",
            participant_identity="caller",
            participant_name="Caller",
            status="ended",
            pipeline_mode="cascaded",
            transcript=[{"role": "user", "text": "Hi", "ts": 0.0, "interrupted": False}],
        )
        session.add(row)
        await session.flush()
        session_id = row.id

    content = json.dumps({"score": 7, "sentiment": "neutral", "tags": [], "summary": "ok"})
    with respx.mock:
        route = respx.post(CHAT_URL).mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        )
        async with httpx.AsyncClient() as http:
            jobs = JobsService(database=database, settings=settings, vault=vault, http_client=http)
            ctx = JobContext(database=database, settings=settings, vault=vault, http=http, jobs=jobs)
            await score_session(ctx, session_id)

    assert route.call_count == 1
    sent = json.loads(route.calls[0].request.content)
    assert sent["messages"][0]["content"] == "Score empathy only."
    async with database.session() as session:
        qa = await session.get(SessionQa, session_id)
        assert qa is not None and qa.status == "done" and qa.score == 7
