"""V6-18 (D-V6-26): tool kits — the catalogue, `GET /v1/tool-kits`, instantiation and its preview.

Every kit is added to a fresh agent on a workspace with no keys and must leave the agent
with no validation error; tools answer from fakes only (Composio through ``FakeComposio``,
no vendor is ever called).
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from auth_helpers import key_client, make_api_key
from conftest import create_agent
from fakes.composio import VALID_KEY, ComposioWorld
from fastapi import FastAPI
from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.kits import ToolKit, snippet_problems
from sqlalchemy import func, select

from lkap_api.db.models import Agent, AuditLog, Tool
from lkap_api.db.session import Database
from lkap_api.templates import kit_apply, kits
from lkap_api.templates.kits import KIT_ORDER, KitError, KitTokens, load_kits, load_raw_kits, render_kit
from lkap_api.tool_providers.router import get_adapter_factory

BASE = "/v1/tool-kits"
SETTINGS: dict[str, dict[str, Any]] = {
    "record_lookup": {"base_url": "https://records.example.com/api/records"},
    "case_ticket": {"base_url": "https://helpdesk.example.com/api/cases"},
    "verify_identity": {"base_url": "https://accounts.example.com/api/verify"},
    "payment_esign_link": {
        "base_url": "https://billing.example.com/api/links",
        "link_host": "pay.example.com",
    },
    "sheet_crm_log": {"base_url": "https://crm.example.com/api/call-logs"},
    "booking": {"event_type_id": 123456},
}
TABLE = (
    "Reference,Date of birth,Name,Status\r\n"
    "DEMO-1001,1985-03-14,Demo — Asha Rao,Active\r\n"
    "DEMO-1002,1990-07-01,Demo — Ben Cole,Closed\r\n"
)
#: Every (kit, variant) that needs no connected app.
KEYLESS = [
    (kit.id, variant.id)
    for kit in load_kits()
    for variant in kit.variants
    if variant.source != "composio_action"
]


async def _table(client: httpx.AsyncClient, keys: Mapping[str, str] | None = None) -> str:
    response = await client.post(
        "/v1/datasets",
        data={"name": "Demo — Records", "key_columns": json.dumps(keys or {"Reference": "string"})},
        files={"file": ("records.csv", TABLE.encode("utf-8"), "text/csv")},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _add(client: httpx.AsyncClient, kit_id: str, agent_id: str, **body: Any) -> httpx.Response:
    payload: dict[str, Any] = {"agent_id": agent_id, "settings": SETTINGS.get(kit_id, {}), **body}
    return await client.post(f"{BASE}/{kit_id}/instantiate", json=payload)


async def _agent(admin_client: httpx.AsyncClient, database: Database, agent_id: str) -> Agent:
    async with database.session() as session:
        row = await session.get(Agent, agent_id)
        assert row is not None
        return row


async def _tool_count(database: Database) -> int:
    async with database.session() as session:
        return int(await session.scalar(select(func.count()).select_from(Tool)) or 0)


async def _errors(admin_client: httpx.AsyncClient, agent_id: str) -> list[dict[str, Any]]:
    result = (await admin_client.post(f"/v1/agents/{agent_id}/validate")).json()
    return [issue for issue in result["issues"] if issue["severity"] == "error"]


# ------------------------------------------------------------------ the catalogue
def test_the_catalogue_has_the_eight_kits_in_order() -> None:
    assert [kit.id for kit in load_kits()] == list(KIT_ORDER)
    assert list(KIT_ORDER) == [
        "record_lookup",
        "case_ticket",
        "structured_intake",
        "verify_identity",
        "payment_esign_link",
        "notify_escalate",
        "sheet_crm_log",
        "booking",
    ]


@pytest.mark.parametrize("kit", load_kits(), ids=lambda kit: kit.id)
def test_every_snippet_is_clean_and_every_placeholder_is_filled(kit: ToolKit) -> None:
    for variant in kit.variants:
        text = variant.instructions_snippet or kit.instructions_snippet
        assert snippet_problems(text) == [], (variant.id, text)
    dumped = kit.model_dump_json()
    assert "{{ kit." not in dumped and "{{kit." not in dumped


@pytest.mark.parametrize("kit", load_kits(), ids=lambda kit: kit.id)
def test_a_kits_http_tools_are_https_and_name_only_their_listed_secrets(kit: ToolKit) -> None:
    for variant in kit.variants:
        for tool in variant.tools:
            definition = tool.definition
            if definition is None or definition.kind != "http":
                continue
            assert definition.url.startswith("https://")
            assert definition.allowed_hosts == [] and definition.credential_id is None
            named = set(re.findall(r"secret\.([A-Z_]+)", json.dumps(definition.model_dump())))
            assert named <= set(variant.requires.secret_names)


@pytest.mark.parametrize("kit", load_kits(), ids=lambda kit: kit.id)
def test_no_kit_rule_uses_a_pattern(kit: ToolKit) -> None:
    """S6-4: a `matches` runs on the worker loop; the catalogue ships none, so no kit can stall it."""
    rules = [*kit.rules, *(rule for variant in kit.variants for rule in variant.rules)]
    assert rules == [] or all("matches" not in rule.when.lower() for rule in rules)


def test_another_prefix_renames_what_the_kit_adds() -> None:
    raw = kits.raw_kit("record_lookup")
    assert raw is not None
    kit = render_kit(
        raw,
        KitTokens(prefix="policy", settings={"base_url": "https://records.example.com/v1"}),
        variant_id="rest",
    )
    [variant] = kit.variants
    assert variant.tools[0].definition is not None
    assert variant.tools[0].definition.name == "policy_lookup"
    assert kit.blocks[0].id == "policy_results"
    assert kit.rules[0].id == "policy_found" and kit.rules[0].when == "tool.policy_lookup.ok"
    assert "policy_lookup" in kit.instructions_snippet


def test_an_unknown_placeholder_is_a_load_error() -> None:
    raw = copy.deepcopy(load_raw_kits()[0])
    raw["instructions_snippet"] = "Call {{ kit.nothing }}."
    with pytest.raises(KitError, match="unknown placeholder"):
        kits._check(raw)


def test_a_snippet_with_a_link_is_a_load_error() -> None:
    raw = copy.deepcopy(load_raw_kits()[0])
    raw["instructions_snippet"] = "Send them to https://records.example.com to check."
    for variant in raw["variants"]:
        variant.pop("instructions_snippet", None)
    with pytest.raises(KitError, match="link"):
        kits._check(raw)


@pytest.mark.parametrize(
    ("kind", "value", "fragment"),
    [
        ("url", "http://records.example.com", "https://"),
        ("url", "https://user:pw@records.example.com", "https://"),
        ("url", "https://records.example.com/?q=1", "https://"),
        ("url", "https://records.example.com/{{ secret.API_KEY }}", "may not hold"),
        ("host", "pay example com", "site name"),
        ("integer", "twelve", "whole number"),
        ("text", "two\nlines", "one short line"),
    ],
)
def test_settings_refuse_values_that_do_not_fit(kind: str, value: str, fragment: str) -> None:
    from lkap_contracts.kits import KitSetting

    setting = KitSetting(name="value", label="Value", kind=kind)  # type: ignore[arg-type]
    with pytest.raises(KitError, match=re.escape(fragment)):
        kits._clean(setting, value)


# ------------------------------------------------------------------ the routes
async def test_list_and_get_tool_kits(admin_client: httpx.AsyncClient) -> None:
    listed = await admin_client.get(BASE)
    one = await admin_client.get(f"{BASE}/verify_identity")
    missing = await admin_client.get(f"{BASE}/nope")

    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == list(KIT_ORDER)
    assert one.status_code == 200 and one.json()["default_prefix"] == "verify"
    assert missing.status_code == 404


async def test_a_read_only_key_may_list_but_not_add(app: FastAPI, database: Database) -> None:
    _, raw = await make_api_key(database, ["agents:read"])
    async with key_client(app, raw) as reader:
        assert (await reader.get(BASE)).status_code == 200
        refused = await reader.post(f"{BASE}/structured_intake/instantiate", json={"agent_id": "x"})
    assert refused.status_code == 403


@pytest.mark.parametrize(("kit_id", "variant"), KEYLESS)
async def test_every_kit_adds_to_a_fresh_agent_on_a_keyless_workspace_with_no_errors(
    admin_client: httpx.AsyncClient, kit_id: str, variant: str
) -> None:
    agent = await create_agent(admin_client, name=f"Demo — {kit_id}")
    extra: dict[str, Any] = {"variant": variant}
    if kit_id == "verify_identity" and variant == "dataset":
        extra["dataset_id"] = await _table(admin_client, {"Reference": "string", "Date of birth": "string"})
    elif variant == "dataset":
        extra["dataset_id"] = await _table(admin_client)

    response = await _add(admin_client, kit_id, str(agent["id"]), **extra)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dry_run"] is False and body["validation"]["ok"] is True, body["validation"]
    assert await _errors(admin_client, str(agent["id"])) == []
    stored = (await admin_client.get(f"/v1/agents/{agent['id']}")).json()
    assert stored["config_version"] == body["config_version"] == int(agent["config_version"]) + 1
    config = AgentConfig.model_validate(stored["config"])
    assert f"<!-- kit:{kit_id}:" in config.instructions
    assert set(body["tool_ids"]) <= set(config.tools.tool_ids)
    for tool in body["tools"]:
        definition = tool["definition"] or {}
        assert "credential_id" not in definition or definition["credential_id"] is None
        assert "secret." not in json.dumps(definition.get("headers", {}))


async def test_adding_a_kit_twice_adds_nothing_the_second_time(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client)
    first = await _add(admin_client, "case_ticket", str(agent["id"]))
    tools_after_first = await _tool_count(database)

    second = await _add(admin_client, "case_ticket", str(agent["id"]))

    assert first.status_code == 200 and second.status_code == 200, second.text
    assert {change["status"] for change in second.json()["changes"]} == {"exists"}
    assert any("without a key" in note for note in first.json()["notes"])
    assert not any("without a key" in note for note in second.json()["notes"])
    assert second.json()["config_version"] == first.json()["config_version"]
    assert await _tool_count(database) == tools_after_first
    config = AgentConfig.model_validate(
        (await admin_client.get(f"/v1/agents/{agent['id']}")).json()["config"]
    )
    assert config.instructions.count("<!-- kit:case_ticket:case -->") == 1
    assert [block.id for block in config.panel.blocks].count("case_case") == 1


async def test_two_prefixes_add_the_kit_twice_side_by_side(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client)
    await _add(admin_client, "record_lookup", str(agent["id"]), variant="rest", block_prefix="policy")

    other = await _add(admin_client, "record_lookup", str(agent["id"]), variant="rest", block_prefix="order")

    assert other.status_code == 200, other.text
    config = AgentConfig.model_validate(
        (await admin_client.get(f"/v1/agents/{agent['id']}")).json()["config"]
    )
    assert {"policy_results", "order_results"} <= {block.id for block in config.panel.blocks}
    assert {"policy_found", "order_found"} <= {rule.id for rule in config.rules}
    assert await _errors(admin_client, str(agent["id"])) == []


async def test_a_dry_run_lists_the_changes_and_writes_nothing(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client)
    before = await _tool_count(database)

    response = await _add(admin_client, "verify_identity", str(agent["id"]), dry_run=True)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dry_run"] is True and body["validation"]["ok"] is True
    kinds = {(change["kind"], change["status"]) for change in body["changes"]}
    assert ("tool", "added") in kinds and ("rule", "added") in kinds and ("instructions", "added") in kinds
    assert [tool["name"] for tool in body["tools"]] == ["verify_check"]
    assert body["tools"][0]["tool_id"] is None
    assert body["instructions_snippet"].startswith("<!-- kit:verify_identity:verify -->")
    assert await _tool_count(database) == before
    stored = (await admin_client.get(f"/v1/agents/{agent['id']}")).json()
    assert stored["config_version"] == agent["config_version"]
    assert "kit:verify_identity" not in stored["config"]["instructions"]


async def test_the_one_time_code_tools_come_only_with_text_messages(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client)

    body = (await _add(admin_client, "verify_identity", str(agent["id"]))).json()

    skipped = [c for c in body["changes"] if c["kind"] == "tool" and c["status"] == "skipped"]
    assert {c["id"] for c in skipped} == {"send_code", "check_code"}
    assert all("text messages" in (c["note"] or "") for c in skipped)
    assert [tool["name"] for tool in body["tools"]] == ["verify_check"]


async def test_the_test_case_answers_from_the_kit_fakes(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client)

    await _add(admin_client, "case_ticket", str(agent["id"]))

    config = AgentConfig.model_validate(
        (await admin_client.get(f"/v1/agents/{agent['id']}")).json()["config"]
    )
    [case] = [test for test in config.tests if test.id == "kit-case_ticket-case"]
    assert case.mocks == {
        "case_create": {"id": "DEMO-CASE-1042", "status": "open"},
        "case_status": {"id": "DEMO-CASE-1042", "status": "in progress"},
    }


async def test_a_key_binds_the_tools_and_keeps_their_key_headers(admin_client: httpx.AsyncClient) -> None:
    key = (
        await admin_client.post(
            "/v1/credentials",
            json={
                "provider_id": "http-tool-secret",
                "label": "Records",
                "secrets": {"API_KEY": "rk-placeholder-1"},
            },
        )
    ).json()["id"]
    agent = await create_agent(admin_client)

    body = (
        await _add(admin_client, "record_lookup", str(agent["id"]), variant="rest", credential_id=key)
    ).json()

    [tool] = body["tools"]
    assert tool["definition"]["credential_id"] == key
    assert tool["definition"]["headers"] == {"Authorization": "Bearer {{ secret.API_KEY }}"}
    assert tool["definition"]["allowed_hosts"] == ["records.example.com"]
    assert not [n for n in body["notes"] if "without a key" in n]


async def test_a_builder_may_add_a_kit_but_not_bind_a_key(
    app: FastAPI, admin_client: httpx.AsyncClient, database: Database
) -> None:
    key = (
        await admin_client.post(
            "/v1/credentials",
            json={
                "provider_id": "http-tool-secret",
                "label": "Records",
                "secrets": {"API_KEY": "rk-placeholder-2"},
            },
        )
    ).json()["id"]
    agent = await create_agent(admin_client)
    _, raw = await make_api_key(database, ["agents:write"])
    async with key_client(app, raw) as builder:
        keyless = await _add(builder, "record_lookup", str(agent["id"]), variant="rest")
        keyed = await _add(
            builder,
            "record_lookup",
            str(agent["id"]),
            variant="rest",
            block_prefix="other",
            credential_id=key,
        )

    assert keyless.status_code == 200, keyless.text
    assert any("without a key" in note for note in keyless.json()["notes"])
    assert keyed.status_code == 403, keyed.text


@pytest.mark.parametrize(
    ("kit_id", "body", "fragment"),
    [
        ("record_lookup", {"variant": "rest", "settings": {}}, "needs a value for 'base_url'"),
        ("record_lookup", {"variant": "rest", "settings": {"base_url": "https://10.0.0.5/api"}}, "private"),
        (
            "record_lookup",
            {"variant": "rest", "settings": {"base_url": "http://records.example.com"}},
            "https://",
        ),
        ("record_lookup", {"variant": "nope"}, "no variant 'nope'"),
        ("record_lookup", {"variant": "dataset"}, "Pick one (dataset_id)"),
        ("record_lookup", {"variant": "dataset", "dataset_id": "missing"}, "unknown lookup table"),
        (
            "case_ticket",
            {"settings": {"base_url": "https://h.example.com", "colour": "red"}},
            "no setting(s) colour",
        ),
        ("structured_intake", {"block_prefix": "Bad Prefix"}, ""),
    ],
)
async def test_bad_requests_are_refused_and_leave_nothing_behind(
    admin_client: httpx.AsyncClient, database: Database, kit_id: str, body: dict[str, Any], fragment: str
) -> None:
    agent = await create_agent(admin_client)
    before = await _tool_count(database)

    response = await admin_client.post(f"{BASE}/{kit_id}/instantiate", json={"agent_id": agent["id"], **body})

    assert response.status_code == 422, response.text
    assert fragment in response.text
    assert await _tool_count(database) == before
    stored = (await admin_client.get(f"/v1/agents/{agent['id']}")).json()
    assert stored["config_version"] == agent["config_version"]


async def test_an_unknown_kit_or_agent_is_404(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client)
    assert (
        await admin_client.post(f"{BASE}/nope/instantiate", json={"agent_id": agent["id"]})
    ).status_code == 404
    assert (await _add(admin_client, "structured_intake", "no-such-agent")).status_code == 404


async def test_a_kit_with_blocks_needs_a_blocks_panel(admin_client: httpx.AsyncClient) -> None:
    config = json.loads(
        AgentConfig.model_validate(
            {
                **(await create_agent(admin_client, published=False))["config"],
                "panel": {"panel_id": "my_panel"},
            }
        ).model_dump_json()
    )
    agent = (
        await admin_client.post("/v1/agents", json={"name": "Demo — Own panel", "config": config})
    ).json()

    response = await _add(admin_client, "structured_intake", str(agent["id"]))

    assert response.status_code == 422
    assert "own panel" in response.json()["error"]["message"]


async def test_a_lookup_table_variant_uses_the_tables_key_columns(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client)
    table = await _table(admin_client, {"Reference": "string", "Date of birth": "string"})

    body = (
        await _add(admin_client, "verify_identity", str(agent["id"]), variant="dataset", dataset_id=table)
    ).json()

    [tool] = body["tools"]
    definition = tool["definition"]
    assert definition["dataset_id"] == table
    assert definition["key_columns"] == ["reference", "date_of_birth"]
    assert definition["return_columns"] == ["reference"]
    assert definition["bindings"] == [{"path": "/0/reference", "to": "var:verify_record"}]
    one_key = await _table(admin_client)
    refused = await _add(
        admin_client,
        "verify_identity",
        str(agent["id"]),
        variant="dataset",
        dataset_id=one_key,
        block_prefix="v2",
    )
    assert refused.status_code == 422 and "at least 2 key columns" in refused.text


async def test_flow_steps_hang_off_the_named_step(admin_client: httpx.AsyncClient) -> None:
    flow = {
        "nodes": [
            {"id": "start", "kind": "start"},
            {"id": "main", "kind": "agent", "instructions": "Help the caller."},
            {"id": "done", "kind": "end"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "main", "condition": "always"},
            {"id": "e2", "source": "main", "target": "done", "condition": "The caller is done."},
        ],
    }
    agent = await create_agent(admin_client)
    config = {**agent["config"], "flow": flow}  # type: ignore[dict-item]
    updated = await admin_client.put(f"/v1/agents/{agent['id']}", json={"config": config})
    assert updated.status_code == 200, updated.text

    skipped = (
        await _add(admin_client, "record_lookup", str(agent["id"]), variant="rest", dry_run=True)
    ).json()
    added = await _add(admin_client, "record_lookup", str(agent["id"]), variant="rest", flow_anchor="main")

    assert {c["status"] for c in skipped["changes"] if c["kind"] == "flow_node"} == {"skipped"}
    assert added.status_code == 200, added.text
    stored = AgentConfig.model_validate(
        (await admin_client.get(f"/v1/agents/{agent['id']}")).json()["config"]
    )
    assert stored.flow is not None
    step = next(node for node in stored.flow.nodes if node.id == "record_lookup_step")
    assert step.kind == "tool"
    main = next(node for node in stored.flow.nodes if node.id == "main")
    assert "record_reference" in getattr(main, "extract", [])
    assert {(e.source, e.target) for e in stored.flow.edges} >= {
        ("main", "record_lookup_step"),
        ("record_lookup_step", "main"),
    }
    assert await _errors(admin_client, str(agent["id"])) == []
    bad = await _add(
        admin_client, "record_lookup", str(agent["id"]), variant="rest", block_prefix="x", flow_anchor="done"
    )
    assert bad.status_code == 422 and "conversation step" in bad.text
    # The tool was created before the flow refused the kit: the rollback took it back.
    names = [row["name"] for row in (await admin_client.get("/v1/tools")).json()["items"]]
    assert "x_lookup" not in names and "record_lookup" in names


async def test_the_team_webhook_key_sets_team_notifications(admin_client: httpx.AsyncClient) -> None:
    key = (
        await admin_client.post(
            "/v1/credentials",
            json={
                "provider_id": "http-tool-secret",
                "label": "Team",
                "secrets": {"TEAM_WEBHOOK_URL": "https://hooks.example.com/placeholder"},
            },
        )
    ).json()["id"]
    agent = await create_agent(admin_client)
    other = await create_agent(admin_client, name="Other")

    keyless = (await _add(admin_client, "notify_escalate", str(other["id"]))).json()
    keyed = await _add(admin_client, "notify_escalate", str(agent["id"]), credential_id=key)
    wrong = await _add(
        admin_client,
        "notify_escalate",
        str(other["id"]),
        credential_id=key,
        block_prefix="esc2",
        settings={"webhook_secret_name": "NOPE"},
    )

    assert any(c["kind"] == "notify_team" and c["status"] == "skipped" for c in keyless["changes"])
    assert keyed.status_code == 200, keyed.text
    config = AgentConfig.model_validate(
        (await admin_client.get(f"/v1/agents/{agent['id']}")).json()["config"]
    )
    assert config.tools.notify_team is not None and config.tools.notify_team.credential_id == key
    assert config.extraction.enabled and "needs_person" in {f.name for f in config.extraction.fields}
    assert wrong.status_code == 422 and "NOPE" in wrong.text


async def test_an_instantiation_is_audited_with_ids_only(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client)

    await _add(admin_client, "payment_esign_link", str(agent["id"]))

    async with database.session() as session:
        rows = (
            (await session.execute(select(AuditLog).where(AuditLog.action == "tool_kit.instantiate")))
            .scalars()
            .all()
        )
    [row] = rows
    assert row.target_id == agent["id"]
    assert row.payload["kit_id"] == "payment_esign_link" and row.payload["prefix"] == "pay"
    assert "billing.example.com" not in json.dumps(row.payload)


# ------------------------------------------------------------------ connected apps (fake Composio)
@pytest.fixture
def world(app: FastAPI) -> ComposioWorld:
    fake = ComposioWorld()
    app.dependency_overrides[get_adapter_factory] = lambda: fake.factory
    return fake


def _acme_kit(monkeypatch: pytest.MonkeyPatch) -> None:
    """``sheet_crm_log`` with the fake's `acmecrm` app in place of the real ones."""
    raw = copy.deepcopy(kits.raw_kit("sheet_crm_log"))
    assert raw is not None
    composio = next(v for v in raw["variants"] if v["id"] == "composio")
    composio["apps"] = [
        {
            "toolkit": "acmecrm",
            "label": "Acme CRM",
            "actions": [
                {"slug": "ACMECRM_LIST_CONTACTS", "key": "log", "label": "Log", "fake": {"ok": True}}
            ],
        }
    ]
    preview = kits._check(raw)
    monkeypatch.setattr(kit_apply, "raw_kit", lambda kit_id: raw if kit_id == "sheet_crm_log" else None)
    monkeypatch.setattr(kit_apply, "get_kit", lambda kit_id: preview if kit_id == "sheet_crm_log" else None)


async def test_an_app_variant_needs_the_apps_admin_and_a_connection(
    app: FastAPI, admin_client: httpx.AsyncClient, database: Database, world: ComposioWorld
) -> None:
    agent = await create_agent(admin_client)
    _, raw = await make_api_key(database, ["agents:write"])
    async with key_client(app, raw) as builder:
        refused = await _add(builder, "sheet_crm_log", str(agent["id"]), variant="composio")

    unconnected = await _add(admin_client, "sheet_crm_log", str(agent["id"]), variant="composio")

    assert refused.status_code == 403 and "admin" in refused.text
    assert (
        unconnected.status_code == 422
        and "connect Google Sheets, Airtable under Apps first" in unconnected.text
    )
    assert world.calls == []


async def test_an_app_variant_picks_the_actions_and_names_them_in_the_rules(
    admin_client: httpx.AsyncClient, world: ComposioWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    _acme_kit(monkeypatch)
    await admin_client.post(
        "/v1/credentials",
        json={"provider_id": "composio", "label": "Composio", "secrets": {"api_key": VALID_KEY}},
    )
    connected = await admin_client.post(
        "/v1/tool-providers/composio/connections",
        json={"toolkit": "acmecrm", "method": "api_key", "fields": {"api_key": "acme-placeholder-1"}},
    )
    assert connected.status_code == 201, connected.text
    agent = await create_agent(admin_client)
    connection_id = connected.json()["connection_id"]

    preview = (
        await _add(
            admin_client,
            "sheet_crm_log",
            str(agent["id"]),
            variant="composio",
            connection_id=connection_id,
            dry_run=True,
        )
    ).json()
    response = await _add(
        admin_client, "sheet_crm_log", str(agent["id"]), variant="composio", connection_id=connection_id
    )

    assert preview["tools"][0]["name"] == "acmecrm_list_contacts" and preview["tools"][0]["tool_id"] is None
    assert response.status_code == 200, response.text
    body = response.json()
    [tool] = body["tools"]
    assert tool["kind"] == "provider" and tool["tool_id"]
    config = AgentConfig.model_validate(
        (await admin_client.get(f"/v1/agents/{agent['id']}")).json()["config"]
    )
    assert tool["tool_id"] in config.tools.tool_ids and config.tools.apps.mode == "actions"
    assert next(rule for rule in config.rules if rule.id == "log_logged").when == f"tool.{tool['name']}.ok"
    assert f"with {tool['name']}:" in config.instructions
    assert await _errors(admin_client, str(agent["id"])) == []
