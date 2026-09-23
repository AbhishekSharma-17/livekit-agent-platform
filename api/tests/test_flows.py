"""V2-16: flow node specs, flow validation, versions/restore and the R-V2-8…12 rulings."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from conftest import create_agent, inference_config
from lkap_contracts.agent_config import AgentConfig, PipelineConfig, ProviderRef, QaConfig
from lkap_contracts.flow import AgentNode
from sqlalchemy import select

from lkap_api.config_service import VALIDATORS, ValidationContext, register_validator, resolve_providers, validate
from lkap_api.db.models import WebhookDelivery
from lkap_api.db.session import Database
from lkap_api.flows import allowed_tool_names, derived_mode, draft_flow_issues, flow_issues

# The flow validator registers itself on import; register explicitly so these tests
# never depend on collection order (see `lkap_api.catalogs.validation`).
register_validator(flow_issues)


def _flow(**agent_node: Any) -> dict[str, Any]:
    return {
        "nodes": [
            {"id": "start", "kind": "start", "greeting": "Hello."},
            {"id": "ask", "kind": "agent", "label": "Ask", "instructions": "Ask for the name.", **agent_node},
            {"id": "done", "kind": "end", "disposition": "completed"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "ask", "condition": "always"},
            {"id": "e2", "source": "ask", "target": "done", "condition": "The caller gave their name."},
        ],
        "variables": [{"name": "name", "type": "string"}],
    }


def _config(flow: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = json.loads(inference_config().model_dump_json())
    config["flow"] = flow
    config.update(overrides)
    return config


def _paths(issues: list[dict[str, Any]], severity: str | None = None) -> list[str]:
    return [i["path"] for i in issues if severity is None or i["severity"] == severity]


async def _validate(client: httpx.AsyncClient, agent_id: object, flow: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(f"/v1/agents/{agent_id}/flow/validate", json={"flow": flow})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def _credential(client: httpx.AsyncClient, provider_id: str) -> str:
    response = await client.post(
        "/v1/credentials", json={"provider_id": provider_id, "label": provider_id, "secrets": {"api_key": "k"}}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


# ------------------------------------------------------------------------ node specs
async def test_node_specs_list_every_kind_with_its_schema(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.get("/v1/flows/node-specs")

    assert response.status_code == 200
    body = response.json()
    assert [n["kind"] for n in body["nodes"]] == ["start", "agent", "end", "transfer", "global", "qa"]
    agent = next(n for n in body["nodes"] if n["kind"] == "agent")
    assert set(agent["json_schema"]["properties"]) == set(AgentNode.model_fields)


async def test_node_specs_need_a_member(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/flows/node-specs")).status_code == 401


# ------------------------------------------------------------- draft / structure
def test_draft_issues_point_at_the_offending_nodes_and_edges() -> None:
    flow = _flow()
    flow["nodes"].append({"id": "orphan", "kind": "agent", "instructions": "x"})
    flow["nodes"].append({"id": "g", "kind": "global"})
    flow["edges"].append({"id": "e3", "source": "done", "target": "ask"})
    flow["edges"].append({"id": "e4", "source": "ask", "target": "g"})

    spec, issues = draft_flow_issues(flow)

    assert spec is None
    paths = {i.path for i in issues}
    assert "flow.nodes[3]" in paths  # orphan is unreachable
    assert "flow.edges[2]" in paths  # an edge out of an end node
    assert "flow.edges[3].target" in paths  # an edge into a global node


def test_draft_issues_report_a_bad_node_without_hiding_the_others() -> None:
    flow = _flow()
    flow["nodes"][1]["id"] = "Bad Id"
    flow["nodes"].append({"kind": "transfer", "id": "xfer"})  # `to` is required

    spec, issues = draft_flow_issues(flow)

    assert spec is None
    assert {i.path for i in issues} == {"flow.nodes[1].id", "flow.nodes[3].to"}


def test_draft_without_a_start_node_is_an_error_at_the_node_list() -> None:
    spec, issues = draft_flow_issues({"nodes": [{"id": "a", "kind": "agent"}], "edges": []})
    assert spec is None
    assert [i.path for i in issues][0] == "flow.nodes"


def test_a_valid_draft_parses() -> None:
    spec, issues = draft_flow_issues(_flow())
    assert issues == []
    assert spec is not None and [n.id for n in spec.nodes] == ["start", "ask", "done"]


async def test_validate_endpoint_returns_structural_issues(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client)
    flow = _flow()
    flow["edges"] = flow["edges"][:1]  # `done` becomes unreachable

    body = await _validate(admin_client, agent["id"], flow)

    assert body["ok"] is False
    assert _paths(body["issues"]) == ["flow.nodes[2]"]


# -------------------------------------------------------------------- R-V2-10 tools
async def test_unknown_node_tool_is_an_error(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client)

    body = await _validate(admin_client, agent["id"], _flow(tools=["end_call", "does_not_exist"]))

    assert body["ok"] is False
    assert _paths(body["issues"], "error") == ["flow.nodes[1].tools[1]"]


async def test_a_tool_name_from_the_agents_tool_ids_is_accepted(admin_client: httpx.AsyncClient) -> None:
    created = await admin_client.post(
        "/v1/tools",
        json={
            "kind": "http",
            "name": "get_weather",
            "definition": {
                "kind": "http",
                "name": "get_weather",
                "description": "Weather",
                "parameters": {"type": "object", "properties": {}},
                "method": "GET",
                "url": "https://api.example.com/weather",
                "allowed_hosts": ["api.example.com"],
            },
        },
    )
    assert created.status_code == 201, created.text
    tool_id = created.json()["id"]
    config = _config(_flow(tools=["get_weather"]))
    config["tools"]["tool_ids"] = [tool_id]

    response = await admin_client.post("/v1/agents", json={"name": "Flow", "config": config})

    assert response.status_code == 201, response.text
    body = (await admin_client.post(f"/v1/agents/{response.json()['id']}/validate")).json()
    assert not [p for p in _paths(body["issues"], "error") if p.startswith("flow")]


async def test_a_block_tool_without_its_block_is_an_error(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client)

    body = await _validate(admin_client, agent["id"], _flow(tools=["show_document"]))

    (issue,) = body["issues"]
    assert issue["path"] == "flow.nodes[1].tools[0]"
    assert "document block" in issue["message"]


async def test_a_node_kb_outside_the_agents_kbs_is_an_error(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client)

    body = await _validate(admin_client, agent["id"], _flow(kb_ids=["kb-elsewhere"]))

    assert _paths(body["issues"], "error") == ["flow.nodes[1].kb_ids[0]"]


def test_allowed_tool_names_is_the_agent_level_union() -> None:
    config = AgentConfig.model_validate(
        {
            **_config(None),
            "tools": {"builtin_disabled": ["push_note"], "tool_ids": ["t1"], "http_request_enabled": False},
            "panel": {"blocks": [{"id": "doc", "type": "document"}]},
        }
    )

    names = allowed_tool_names(config, tool_names_by_id={"t1": "crm", "t2": "other"}, pack_tool_names=["lookup"])

    assert {"end_call", "show_document", "update_block", "crm", "lookup"} <= names
    assert not names & {"push_note", "http_request", "pin_frame", "table_append", "other"}


async def test_saving_an_invalid_flow_reference_returns_issues_in_the_422(
    admin_client: httpx.AsyncClient,
) -> None:
    agent = await create_agent(admin_client)

    response = await admin_client.put(
        f"/v1/agents/{agent['id']}", json={"config": _config(_flow(tools=["nope"]))}
    )

    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert "flow.nodes[1].tools[0]" in _paths(details["issues"])


# ---------------------------------------------------------------- R-V2-9 overrides
async def test_a_vendor_key_override_absent_from_the_pipeline_is_an_error(
    admin_client: httpx.AsyncClient,
) -> None:
    agent = await create_agent(admin_client)
    credential_id = await _credential(admin_client, "openai-llm")
    override = {"llm": {"provider_id": "openai-llm", "credential_id": credential_id, "model": "gpt-4.1"}}

    body = await _validate(admin_client, agent["id"], _flow(providers=override))

    assert _paths(body["issues"], "error") == ["flow.nodes[1].providers.llm"]


async def test_a_cheaper_model_on_the_pipelines_own_key_is_accepted(admin_client: httpx.AsyncClient) -> None:
    credential_id = await _credential(admin_client, "openai-llm")
    config = _config(None)
    config["pipeline"]["llm"] = {"provider_id": "openai-llm", "credential_id": credential_id, "model": "gpt-4.1"}
    created = await admin_client.post("/v1/agents", json={"name": "Keyed", "config": config})
    assert created.status_code == 201, created.text
    override = {"llm": {"provider_id": "openai-llm", "model": "gpt-4.1-mini"}}

    body = await _validate(admin_client, created.json()["id"], _flow(providers=override))

    assert body["issues"] == []


async def test_a_credential_free_override_and_a_kind_mismatch(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client)
    ok = await _validate(
        admin_client, agent["id"], _flow(providers={"tts": {"provider_id": "livekit-inference-tts"}})
    )
    wrong_kind = await _validate(
        admin_client, agent["id"], _flow(providers={"tts": {"provider_id": "livekit-inference-llm"}})
    )

    assert ok["issues"] == []
    assert _paths(wrong_kind["issues"], "error") == ["flow.nodes[1].providers.tts"]


def test_any_override_in_a_realtime_pipeline_is_a_warning() -> None:
    config = AgentConfig(
        instructions="x",
        pipeline=PipelineConfig(mode="realtime", realtime=ProviderRef(provider_id="google-realtime")),
        flow=_flow(providers={"llm": {"provider_id": "openai-llm", "credential_id": "c"}}),  # type: ignore[arg-type]
    )

    issues = flow_issues(ValidationContext(config=config, tool_names_by_id={}))

    assert [(i.path, i.severity) for i in issues] == [("flow.nodes[1].providers.llm", "warning")]


# ------------------------------------------------------------------------ R-V2-11 qa
def _qa_flow() -> dict[str, Any]:
    flow = _flow()
    flow["nodes"].append({"id": "qa", "kind": "qa", "rubric_prompt": "Score empathy."})
    return flow


def test_a_qa_node_with_qa_disabled_resolves_qa_llm_and_warns() -> None:
    config = AgentConfig.model_validate({**_config(_qa_flow()), "qa": {"enabled": False}})

    resolved = resolve_providers(config, {})
    result = validate(ValidationContext(config=config, tool_names_by_id={}))

    assert "qa_llm" in resolved
    assert ("qa.enabled", "warning") in [(i.path, i.severity) for i in result.issues]


def test_prompt_agents_resolve_no_qa_llm_while_qa_is_off() -> None:
    config = AgentConfig.model_validate({**_config(None), "qa": {"enabled": False}})
    assert "qa_llm" not in resolve_providers(config, {})


def test_a_qa_nodes_judge_model_is_validated_as_a_slot() -> None:
    config = AgentConfig.model_validate(_config(_qa_flow())).model_copy(
        update={"qa": QaConfig(enabled=False, model=ProviderRef(provider_id="openai-llm"))}
    )

    result = validate(ValidationContext(config=config, tool_names_by_id={}))

    assert "qa.model" in [i.path for i in result.issues if i.severity == "error"]  # needs a credential


# ---------------------------------------------------------------------- variables
def test_extracting_an_undeclared_variable_is_an_error() -> None:
    config = AgentConfig.model_validate(_config(_flow(extract=["name", "phone"])))

    issues = flow_issues(ValidationContext(config=config, tool_names_by_id={}))

    assert [(i.path, i.severity) for i in issues] == [("flow.nodes[1].extract[1]", "error")]


# ------------------------------------------------------------------------ R-V2-12 mode
async def test_mode_is_derived_on_save(admin_client: httpx.AsyncClient) -> None:
    created = await admin_client.post(
        "/v1/agents", json={"name": "Flowy", "mode": "prompt", "config": _config(_flow())}
    )
    assert created.status_code == 201, created.text
    assert created.json()["mode"] == "flow"  # the payload's mode is ignored on create
    agent_id = created.json()["id"]

    to_prompt = await admin_client.put(f"/v1/agents/{agent_id}", json={"config": _config(None)})
    assert to_prompt.status_code == 200, to_prompt.text
    assert to_prompt.json()["mode"] == "prompt"

    to_flow = await admin_client.put(f"/v1/agents/{agent_id}", json={"config": _config(_flow()), "mode": "flow"})
    assert to_flow.status_code == 200, to_flow.text
    assert to_flow.json()["mode"] == "flow"

    listed = (await admin_client.get("/v1/agents", params={"mode": "flow"})).json()
    assert [a["id"] for a in listed["items"]] == [agent_id]


@pytest.mark.parametrize(
    ("stored_flow", "payload"),
    [
        (None, {"mode": "flow"}),
        (_flow(), {"mode": "prompt"}),
        (None, {"mode": "flow", "config": _config({"nodes": [], "edges": []})}),
    ],
)
async def test_a_disagreeing_mode_is_422_at_the_mode_path(
    admin_client: httpx.AsyncClient, stored_flow: dict[str, Any] | None, payload: dict[str, Any]
) -> None:
    created = await admin_client.post("/v1/agents", json={"name": "M", "config": _config(stored_flow)})
    assert created.status_code == 201, created.text

    response = await admin_client.put(f"/v1/agents/{created.json()['id']}", json=payload)

    assert response.status_code == 422
    assert _paths(response.json()["error"]["details"]["issues"]) == ["mode"]


def test_derived_mode_needs_nodes() -> None:
    assert derived_mode(AgentConfig.model_validate(_config({"nodes": [], "edges": []}))) == "prompt"
    assert derived_mode(AgentConfig.model_validate(_config(_flow()))) == "flow"


# --------------------------------------------------------------------- versions
async def test_versions_list_get_and_restore_creates_a_new_version(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client, published=False)
    agent_id = agent["id"]
    saved = await admin_client.put(f"/v1/agents/{agent_id}", json={"config": _config(_flow())})
    assert saved.status_code == 200 and saved.json()["config_version"] == 2

    listed = (await admin_client.get(f"/v1/agents/{agent_id}/versions")).json()
    assert [v["config_version"] for v in listed["items"]] == [2, 1]
    assert listed["items"][0]["config"] is None  # the list omits bodies
    v1 = (await admin_client.get(f"/v1/agents/{agent_id}/versions/1")).json()
    assert v1["config"]["flow"] is None

    restored = await admin_client.post(f"/v1/agents/{agent_id}/versions/1/restore")

    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert body["config_version"] == 3
    assert body["mode"] == "prompt"  # R-V2-12 follows the restored config
    listed = (await admin_client.get(f"/v1/agents/{agent_id}/versions")).json()
    assert listed["items"][0]["note"] == "restored from version 1"
    assert (await admin_client.get(f"/v1/agents/{agent_id}/versions/9")).status_code == 404


# --------------------------------------------------------------- R-V2-8 summary
async def test_summary_disposition_and_variables_reach_the_session_and_the_webhook(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient, database: Database
) -> None:
    created = await admin_client.post(
        "/v1/webhooks", json={"url": "https://hooks.example.com/flow", "events": ["session.ended"]}
    )
    assert created.status_code == 201, created.text
    agent = await create_agent(admin_client, config=_config(_flow()))
    session_id = (await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})).json()["sessionId"]

    with respx.mock:
        respx.post("https://hooks.example.com/flow").mock(return_value=httpx.Response(200))
        response = await service_client.put(
            f"/internal/v1/sessions/{session_id}/summary",
            json={
                "status": "ended",
                "usage": {},
                "transcript": [],
                "disposition": "completed",
                "variables": {"name": "Ada"},
            },
        )
    assert response.status_code == 204

    detail = (await admin_client.get(f"/v1/sessions/{session_id}")).json()
    assert (detail["disposition"], detail["variables"]) == ("completed", {"name": "Ada"})
    async with database.session() as session:
        deliveries = (await session.execute(select(WebhookDelivery))).scalars().all()
    (delivery,) = [d for d in deliveries if d.event_type == "session.ended"]
    assert delivery.payload["data"]["disposition"] == "completed"
    assert delivery.payload["data"]["variables"] == {"name": "Ada"}


async def test_a_prompt_summary_stores_the_defaults(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    agent = await create_agent(admin_client)
    session_id = (await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})).json()["sessionId"]

    await service_client.put(
        f"/internal/v1/sessions/{session_id}/summary", json={"status": "ended", "usage": {}, "transcript": []}
    )

    detail = (await admin_client.get(f"/v1/sessions/{session_id}")).json()
    assert (detail["disposition"], detail["variables"]) == (None, {})


def test_flow_validator_is_registered() -> None:
    assert flow_issues in VALIDATORS
