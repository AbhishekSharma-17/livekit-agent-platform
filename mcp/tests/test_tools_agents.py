"""Agent tools: merge-patch updates, validation, prompt/flow switch, confirm gates (§4.4, §4.11)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import BUILDER_SCOPES, READ_ONLY_SCOPES
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.kb.embed import FakeEmbedder
from lkap_contracts.agent_config import AgentConfig


def _flow() -> dict[str, Any]:
    return {
        "nodes": [
            {"id": "start", "kind": "start", "greeting": "Hello."},
            {"id": "ask", "kind": "agent", "label": "Ask", "instructions": "Ask for the name."},
            {"id": "done", "kind": "end", "disposition": "completed"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "ask", "condition": "always"},
            {"id": "e2", "source": "ask", "target": "done", "condition": "The caller gave their name."},
        ],
        "variables": [{"name": "name", "type": "string"}],
    }


async def _create(mcp: Any, name: str = "Intake") -> dict[str, Any]:
    result = await mcp.call("agent_create", name=name, pack_id="generic")
    assert result["ok"] is True, result
    agent: dict[str, Any] = result["data"]["agent"]
    return agent


async def test_agent_create_from_pack_seeds_config_and_validates(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "agent_create", name="Helper", pack_id="generic", patch={"instructions": "Be brief."}
        )

    agent = result["data"]["agent"]
    assert agent["pack_id"] == "generic" and agent["config"]["instructions"] == "Be brief."
    assert result["data"]["validation"] is not None
    assert result["next_steps"]


async def test_agent_update_patch_merges_over_the_stored_config_and_sends_a_full_config(
    key: Any, mcp_session: Any, admin: Any
) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        agent = await _create(mcp)
        result = await mcp.call(
            "agent_update", id_or_slug=agent["slug"], patch={"instructions": "Take FNOL claims."}
        )
        [put] = mcp.transport.calls("PUT", f"/v1/agents/{agent['id']}")
        sent = mcp.transport.bodies[put]

    assert result["ok"] is True, result
    stored = (await admin.get(f"/v1/agents/{agent['id']}")).json()
    assert stored["config"]["instructions"] == "Take FNOL claims."
    assert stored["config"]["pipeline"] == agent["config"]["pipeline"]
    assert stored["config_version"] == agent["config_version"] + 1
    AgentConfig.model_validate(sent["config"])  # the api saw a whole AgentConfig
    assert "mode" not in sent


async def test_agent_update_schema_invalid_patch_is_not_saved_and_returns_issues(
    key: Any, mcp_session: Any, admin: Any
) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        agent = await _create(mcp)
        result = await mcp.call(
            "agent_update", id_or_slug=agent["id"], patch={"pipeline": {"mode": "telepathy"}}
        )
        puts = mcp.transport.calls("PUT", f"/v1/agents/{agent['id']}")

    assert result["ok"] is False and result["error"]["code"] == "invalid_config"
    assert result["issues"] and any(issue["path"].startswith("pipeline") for issue in result["issues"])
    assert puts == []
    assert (await admin.get(f"/v1/agents/{agent['id']}")).json()["config_version"] == agent["config_version"]


async def test_agent_update_api_invalid_config_relays_the_422_issues_and_saves_nothing(
    key: Any, mcp_session: Any, admin: Any
) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        agent = await _create(mcp)
        bad = {"pipeline": {"stt": {"provider_id": "no-such-provider"}}}
        result = await mcp.call("agent_update", id_or_slug=agent["id"], patch=bad)

    assert result["ok"] is False and result["error"]["status"] == 422
    assert "nothing was saved" in result["error"]["message"]
    assert (await admin.get(f"/v1/agents/{agent['id']}")).json()["config_version"] == agent["config_version"]


async def test_agent_update_flow_patch_switches_mode_to_flow_and_null_back_to_prompt(
    key: Any, mcp_session: Any
) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        agent = await _create(mcp)
        to_flow = await mcp.call("agent_update", id_or_slug=agent["id"], patch={"flow": _flow()})
        to_prompt = await mcp.call("agent_update", id_or_slug=agent["id"], patch={"flow": None})

    assert to_flow["ok"] is True, to_flow
    assert to_flow["data"]["agent"]["mode"] == "flow"
    assert to_prompt["data"]["agent"]["mode"] == "prompt"
    assert to_prompt["data"]["agent"]["config"].get("flow") is None


async def test_agent_attach_adds_and_removes_kb_ids(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        agent = await _create(mcp)
        kb = (await mcp.call("kb_create", name="Policies"))["data"]
        attached = await mcp.call("agent_attach", id_or_slug=agent["id"], kb_ids=[kb["id"]])
        detached = await mcp.call("agent_attach", id_or_slug=agent["id"], kb_ids=[kb["id"]], remove=True)

    assert attached["data"]["agent"]["config"]["knowledge"]["kb_ids"] == [kb["id"]]
    assert detached["data"]["agent"]["config"]["knowledge"]["kb_ids"] == []


async def test_agent_publish_sets_published_and_returns_the_session_path(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        agent = await _create(mcp)
        result = await mcp.call("agent_publish", id_or_slug=agent["id"])

    assert result["data"]["agent"]["published"] is True
    assert result["data"]["session_path"] == f"/s/{agent['slug']}"


async def test_lkap_delete_agent_needs_confirmation_then_relays_the_409(
    key: Any, mcp_session: Any, database: Database, admin: Any
) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        agent = await _create(mcp)
        async with database.session() as session:
            session.add(
                SessionRow(
                    agent_id=agent["id"],
                    config_version=agent["config_version"],
                    room_name=f"room-{agent['id']}",
                    participant_identity="caller",
                    participant_name="Caller",
                    status="ended",
                    pipeline_mode="cascaded",
                )
            )
        unconfirmed = await mcp.call("lkap_delete", kind="agent", id=agent["id"])
        in_use = await mcp.call("lkap_delete", kind="agent", id=agent["id"], confirm=True)
        purge_unarchived = await mcp.call(
            "lkap_delete", kind="agent", id=agent["id"], purge=True, confirm=True
        )
        archive_unconfirmed = await mcp.call("agent_archive", id_or_slug=agent["id"])
        archived = await mcp.call("agent_archive", id_or_slug=agent["id"], confirm=True)
        purged = await mcp.call("lkap_delete", kind="agent", id=agent["id"], purge=True, confirm=True)

    assert unconfirmed["error"]["code"] == "needs_confirmation"
    assert (in_use["error"]["status"], in_use["error"]["code"]) == (409, "conflict")
    assert (purge_unarchived["error"]["status"], purge_unarchived["error"]["code"]) == (409, "conflict")
    assert archive_unconfirmed["error"]["code"] == "needs_confirmation"
    assert archived["data"]["archived_at"] is not None
    assert purged["data"] == {"deleted": True, "kind": "agent", "id": agent["id"]}
    assert (await admin.get(f"/v1/agents/{agent['id']}")).status_code == 404


async def test_agent_versions_restore_needs_confirmation(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        agent = await _create(mcp)
        await mcp.call("agent_update", id_or_slug=agent["id"], patch={"instructions": "v2"})
        versions = await mcp.call("agent_versions", id_or_slug=agent["id"])
        unconfirmed = await mcp.call("agent_versions", id_or_slug=agent["id"], restore=1)
        restored = await mcp.call("agent_versions", id_or_slug=agent["id"], restore=1, confirm=True)

    assert len(versions["data"]) >= 2
    assert unconfirmed["error"]["code"] == "needs_confirmation"
    assert restored["ok"] is True and restored["data"]["config"]["instructions"] != "v2"


# --------------------------------------------------------------------------- starter templates (V4-01)
@pytest.fixture
def fake_seed_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Template knowledge seeds embed with ``FakeEmbedder`` (the agent router resolves its own)."""

    async def _fake(*_args: object, **_kwargs: object) -> FakeEmbedder:
        return FakeEmbedder()

    monkeypatch.setattr("lkap_api.routers.agents.resolve_embedder", _fake)


async def test_agent_create_from_a_template_plans_the_template_id(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call("agent_create", name="Support", template_id="knowledge_assistant", plan=True)
        posts = mcp.transport.calls("POST", "/v1/agents")

    assert result["ok"] is True, result
    [step] = result["plan"]
    assert (step["method"], step["path"]) == ("POST", "/v1/agents")
    assert step["body"]["template_id"] == "knowledge_assistant"
    assert posts == []


async def test_agent_create_from_a_template_seeds_it_and_returns_its_next_steps(
    key: Any, mcp_session: Any, admin: Any, fake_seed_embedder: None
) -> None:
    raw = await key(BUILDER_SCOPES)
    template = (await admin.get("/v1/templates/knowledge_assistant")).json()["template"]

    async with mcp_session(raw) as mcp:
        result = await mcp.call("agent_create", name="Support", template_id="knowledge_assistant")
        [post] = mcp.transport.calls("POST", "/v1/agents")
        sent = mcp.transport.bodies[post]

    assert result["ok"] is True, result
    agent = result["data"]["agent"]
    assert sent["template_id"] == "knowledge_assistant" and sent["pack_id"] == "generic"
    assert agent["pack_id"] == "generic"
    assert agent["config"]["voice"]["greeting"] == template["greeting"]
    assert len(agent["config"]["knowledge"]["kb_ids"]) == 2
    assert result["data"]["validation"]["ok"] is True
    labels = [step["label"] for step in template["next_steps"]]
    assert result["next_steps"][: len(labels)] == labels


async def test_agent_create_from_a_code_pack_template_ignores_the_default_pack_id(
    key: Any, mcp_session: Any, fake_seed_embedder: None
) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call("agent_create", name="Claims", template_id="insurance_claim")

    assert result["ok"] is True, result
    assert result["data"]["agent"]["pack_id"] == "insurance_claim"


async def test_agent_create_relays_the_template_422_with_issues(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        agent = await _create(mcp)
        both = await mcp.call("agent_create", name="Both", template_id="blank", config=agent["config"])
        unknown = await mcp.call("agent_create", name="Nope", template_id="time_machine")

    assert both["ok"] is False and both["error"]["status"] == 422
    assert [issue["path"] for issue in both["issues"]] == ["template_id"]
    assert unknown["ok"] is False and unknown["error"]["status"] == 422
    assert "receptionist" in unknown["error"]["details"]["known"]


async def test_lkap_describe_template_returns_the_template_out(key: Any, mcp_session: Any) -> None:
    raw = await key(READ_ONLY_SCOPES)

    async with mcp_session(raw) as mcp:
        found = await mcp.call("lkap_describe", kind="template", id="receptionist")
        missing = await mcp.call("lkap_describe", kind="template", id="time_machine")

    assert found["ok"] is True, found
    assert found["data"]["template"]["id"] == "receptionist"
    assert found["data"]["pack"]["id"] == "generic" and found["data"]["derived"] is False
    assert missing["ok"] is False and missing["error"]["code"] == "not_found"
    assert "blank" in missing["error"]["details"]["known"]
    assert "insurance_claim" in missing["error"]["details"]["known"]


async def test_the_templates_resource_reads_live(key: Any, mcp_session: Any) -> None:
    raw = await key(READ_ONLY_SCOPES)

    async with mcp_session(raw) as mcp:
        resources = {str(r.uri) for r in (await mcp.session.list_resources()).resources}
        read = await mcp.session.read_resource("lkap://templates")  # type: ignore[arg-type]
        gets = mcp.transport.calls("GET", "/v1/templates")

    assert "lkap://templates" in resources
    body = json.loads(getattr(read.contents[0], "text", "{}"))
    ids = [item["template"]["id"] for item in body["items"]]
    assert ids[0] == "blank" and ids[-1] == "insurance_claim" and "receptionist" in ids
    assert gets, "the resource is a live api read"
