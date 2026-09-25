"""The Apps tools (V5-18, docs/v5/COMPOSIO.md §7).

The api is the real scratch app; Composio is the api tests' ``FakeComposio``
(``api/tests/fakes/composio.py``) behind the router's adapter factory, so no
vendor is ever called. Every key is a placeholder.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import BUILDER_SCOPES, OPERATOR_SCOPES, READ_ONLY_SCOPES, all_log_text, dumped
from fakes.composio import VALID_KEY, ComposioWorld
from fastapi import FastAPI
from lkap_api.tool_providers.router import get_adapter_factory

APP_KEY = "acme-live-Qw4Er7Ty1Ui5Op9As3Df"
APPS = "/v1/tool-providers/composio"
APPS_TOOLS = {
    "apps_list",
    "apps_actions",
    "apps_connect",
    "apps_connections",
    "apps_connection_status",
    "apps_disconnect",
    "apps_add_tools",
}


@pytest.fixture
def world(app: FastAPI) -> ComposioWorld:
    """The fake Composio behind the scratch api."""
    fake = ComposioWorld()
    app.dependency_overrides[get_adapter_factory] = lambda: fake.factory
    return fake


@pytest.fixture
async def composio_key(admin: Any, world: ComposioWorld) -> str:
    """The workspace's Composio key."""
    response = await admin.post(
        "/v1/credentials",
        json={"provider_id": "composio", "label": "Composio", "secrets": {"api_key": VALID_KEY}},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def test_read_keys_see_the_read_tools_and_operators_see_all(key: Any, mcp_session: Any) -> None:
    reader = await key(READ_ONLY_SCOPES)
    operator = await key(OPERATOR_SCOPES)
    builder = await key(BUILDER_SCOPES)

    async with mcp_session(reader) as mcp:
        read_names = set(await mcp.tool_names()) & APPS_TOOLS
    async with mcp_session(builder) as mcp:
        builder_names = set(await mcp.tool_names()) & APPS_TOOLS
    async with mcp_session(operator) as mcp:
        operator_names = set(await mcp.tool_names()) & APPS_TOOLS

    assert read_names == {"apps_list", "apps_actions", "apps_connections", "apps_connection_status"}
    assert builder_names == read_names
    assert operator_names == APPS_TOOLS


async def test_apps_list_wraps_vendor_text_as_untrusted(
    key: Any, mcp_session: Any, world: ComposioWorld, composio_key: str
) -> None:
    raw = await key(READ_ONLY_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call("apps_list", query="calendar")

    assert result["ok"] is True, result
    item = result["data"]["items"][0]
    assert item["slug"] == "googlecalendar"
    assert item["name"]["untrusted"] is True and item["name"]["content"] == "Google Calendar"
    assert item["description"]["untrusted"] is True and item["description"]["source"] == "apps:googlecalendar"
    assert world.calls_of("list_toolkits")[0].kwargs["search"] == "calendar"


async def test_apps_actions_wraps_descriptions_and_keeps_risk(
    key: Any, mcp_session: Any, world: ComposioWorld, composio_key: str
) -> None:
    raw = await key(READ_ONLY_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call("apps_actions", toolkit="googlecalendar", important=True)

    assert result["ok"] is True, result
    risks = {item["slug"]: item["risk"] for item in result["data"]["items"]}
    assert risks == {"GOOGLECALENDAR_FIND_FREE_SLOTS": "read", "GOOGLECALENDAR_CREATE_EVENT": "write"}
    assert all(item["description"]["untrusted"] is True for item in result["data"]["items"])


async def test_apps_connect_plan_hides_the_fields(
    key: Any, mcp_session: Any, world: ComposioWorld, composio_key: str
) -> None:
    raw = await key(OPERATOR_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "apps_connect", toolkit="acmecrm", method="api_key", fields={"api_key": APP_KEY}, plan=True
        )
        sent = mcp.transport.calls("POST", f"{APPS}/connections")

    assert result["ok"] is True and sent == []
    step = result["plan"][0]
    assert step["path"] == f"{APPS}/connections"
    assert step["body"]["fields"] == {"api_key": "<inline secret>"}
    assert APP_KEY not in dumped(result)


async def test_apps_connect_managed_returns_the_link_for_the_human(
    key: Any, mcp_session: Any, world: ComposioWorld, composio_key: str
) -> None:
    raw = await key(OPERATOR_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call("apps_connect", toolkit="googlecalendar")
        status = await mcp.call("apps_connection_status", id=result["data"]["connection_id"])

    assert result["ok"] is True, result
    assert result["data"]["status"] == "initiated"
    assert result["data"]["redirect_url"].startswith("https://connect.example.com/")
    assert any("do not open it yourself" in step for step in result["next_steps"])
    assert status["data"]["status"] == "initiated"


async def test_apps_connect_with_a_key_forwards_it_and_never_echoes_it(
    key: Any,
    mcp_session: Any,
    world: ComposioWorld,
    composio_key: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw = await key(OPERATOR_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "apps_connect", toolkit="acmecrm", method="api_key", fields={"api_key": APP_KEY}
        )
        listed = await mcp.call("apps_connections")

    assert result["ok"] is True, result
    assert result["data"]["status"] == "active"
    assert APP_KEY in world.seen_values(), "the key reached Composio"
    assert APP_KEY not in dumped(result) and APP_KEY not in dumped(listed)
    assert APP_KEY not in all_log_text(caplog)
    assert listed["data"]["items"][0]["method"] == "api_key"


async def test_apps_disconnect_needs_confirmation(
    key: Any, mcp_session: Any, world: ComposioWorld, composio_key: str
) -> None:
    raw = await key(OPERATOR_SCOPES)

    async with mcp_session(raw) as mcp:
        connected = await mcp.call("apps_connect", toolkit="publicholidays", method="none")
        connection_id = connected["data"]["connection_id"]
        unconfirmed = await mcp.call("apps_disconnect", id=connection_id)
        sent_before = mcp.transport.calls("DELETE", f"{APPS}/connections/{connection_id}")
        confirmed = await mcp.call("apps_disconnect", id=connection_id, confirm=True)

    assert unconfirmed["ok"] is False and unconfirmed["error"]["code"] == "needs_confirmation"
    assert sent_before == []
    assert confirmed["ok"] is True and confirmed["data"]["status"] == "inactive"


async def test_apps_add_tools_stores_picks_and_plans_without_sending(
    key: Any, mcp_session: Any, world: ComposioWorld, composio_key: str
) -> None:
    raw = await key(OPERATOR_SCOPES)

    async with mcp_session(raw) as mcp:
        connected = await mcp.call(
            "apps_connect", toolkit="acmecrm", method="api_key", fields={"api_key": APP_KEY}
        )
        connection_id = connected["data"]["connection_id"]
        picked = await mcp.call(
            "apps_add_tools", connection_id=connection_id, actions=["ACMECRM_LIST_CONTACTS"]
        )
        planned = await mcp.call(
            "apps_add_tools", connection_id=connection_id, actions=["ACMECRM_LIST_CONTACTS"], plan=True
        )
        sent = [json.loads(r.content) for r in mcp.transport.requests if r.url.path == f"{APPS}/materialise"]

    assert picked["ok"] is True, picked
    assert picked["data"]["picked_actions"] == ["ACMECRM_LIST_CONTACTS"]
    assert planned["plan"][0]["body"]["allow_destructive"] is False
    assert len(sent) == 1


async def test_apps_list_without_a_key_explains_apps_are_not_enabled(
    key: Any, mcp_session: Any, world: ComposioWorld
) -> None:
    raw = await key(READ_ONLY_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call("apps_list")

    assert result["ok"] is False
    assert result["error"]["code"] == "apps_not_enabled"


# ------------------------------------------------------------------ V5-47: agents
async def _agent(mcp: Any, name: str = "Demo — Apps") -> dict[str, Any]:
    result = await mcp.call("agent_create", name=name, pack_id="generic")
    assert result["ok"] is True, result
    agent: dict[str, Any] = result["data"]["agent"]
    return agent


async def test_apps_add_tools_with_an_agent_creates_and_attaches_the_tools(
    key: Any, mcp_session: Any, world: ComposioWorld, composio_key: str
) -> None:
    raw = await key(OPERATOR_SCOPES)

    async with mcp_session(raw) as mcp:
        agent = await _agent(mcp)
        connected = await mcp.call(
            "apps_connect", toolkit="acmecrm", method="api_key", fields={"api_key": APP_KEY}
        )
        picked = await mcp.call(
            "apps_add_tools",
            connection_id=connected["data"]["connection_id"],
            actions=["ACMECRM_LIST_CONTACTS"],
            agent_id=agent["id"],
        )
        stored = await mcp.call("agent_get", id_or_slug=agent["id"])

    assert picked["ok"] is True, picked
    (tool_id,) = picked["data"]["tools_created"]
    config = stored["data"]["config"] if "config" in stored["data"] else stored["data"]["agent"]["config"]
    assert tool_id in config["tools"]["tool_ids"]
    assert config["tools"]["apps"]["mode"] == "actions"


async def test_agent_apps_mode_router_provisions_on_save_and_plans_without_sending(
    key: Any, mcp_session: Any, world: ComposioWorld, composio_key: str
) -> None:
    raw = await key(OPERATOR_SCOPES)

    async with mcp_session(raw) as mcp:
        agent = await _agent(mcp, "Demo — Apps scratch")
        await mcp.call("apps_connect", toolkit="acmecrm", method="api_key", fields={"api_key": APP_KEY})
        planned = await mcp.call("agent_apps_mode", id_or_slug=agent["id"], mode="router", plan=True)
        assert world.sessions == {}, "a plan sends nothing"
        saved = await mcp.call(
            "agent_apps_mode",
            id_or_slug=agent["id"],
            mode="router",
            allowed_toolkits=["AcmeCRM"],
            router={"search": True, "execute": True, "manage_connections": False},
        )
        off = await mcp.call("agent_apps_mode", id_or_slug=agent["id"], mode="off")

    assert planned["plan"][0]["method"] == "PUT"
    assert planned["plan"][0]["body"]["config"]["tools"]["apps"]["mode"] == "router"
    assert saved["ok"] is True, saved
    apps = saved["data"]["agent"]["config"]["tools"]["apps"]
    assert apps["mode"] == "router"
    assert apps["allowed_toolkits"] == ["acmecrm"]
    assert any("slower" in step for step in saved["next_steps"])
    assert len(world.calls_of("create_router_session")) == 1
    assert off["ok"] is True, off
    assert world.sessions == {}, "off removes the tool finder"


async def test_agent_apps_mode_sends_reviewed_actions_upper_cased(
    key: Any, mcp_session: Any, world: ComposioWorld, composio_key: str
) -> None:
    raw = await key(OPERATOR_SCOPES)

    async with mcp_session(raw) as mcp:
        agent = await _agent(mcp, "Demo — Apps scratch")
        planned = await mcp.call(
            "agent_apps_mode",
            id_or_slug=agent["id"],
            mode="router",
            reviewed_actions=[" acmecrm_delete_contact ", ""],
            plan=True,
        )

    apps = planned["plan"][0]["body"]["config"]["tools"]["apps"]
    assert apps["reviewed_actions"] == ["ACMECRM_DELETE_CONTACT"]
    assert world.sessions == {}, "a plan sends nothing"


async def test_agent_apps_mode_needs_agents_write(key: Any, mcp_session: Any) -> None:
    raw = await key(READ_ONLY_SCOPES)

    async with mcp_session(raw) as mcp:
        names = set(await mcp.tool_names())

    assert "agent_apps_mode" not in names
