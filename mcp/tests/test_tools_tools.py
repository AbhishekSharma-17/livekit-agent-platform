"""The tool-management tools' background-execution arguments (V4-12, docs/v4/BACKGROUND-TOOLS.md §7)
and the MCP auth union and connection test (V5-09)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from conftest import BUILDER_SCOPES

TOOL = {
    "name": "lookup_customer",
    "description": "Look up a customer record by email",
    "parameters": {"type": "object", "properties": {"email": {"type": "string"}}},
    "url": "https://api.example.com/customers?email={{ email }}",
    "method": "GET",
    "allowed_hosts": ["api.example.com"],
}


async def test_tool_create_http_plans_the_execution_in_the_definition(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "tool_create_http",
            **TOOL,
            execution={"mode": "auto", "announce": "Looking that up.", "fillers": ["Still checking."]},
            plan=True,
        )
        posts = mcp.transport.calls("POST", "/v1/tools")

    assert result["ok"] is True, result
    [step] = result["plan"]
    execution = step["body"]["definition"]["execution"]
    assert execution["mode"] == "auto"
    assert execution["announce"] == "Looking that up."
    assert execution["fillers"] == ["Still checking."]
    assert execution["auto_threshold_ms"] == 700
    assert posts == []


async def test_tool_create_http_without_execution_keeps_the_blocking_default(
    key: Any, mcp_session: Any
) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call("tool_create_http", **TOOL, plan=True)

    [step] = result["plan"]
    assert step["body"]["definition"]["execution"]["mode"] is None


async def test_tool_create_http_refuses_silent_reply_with_a_background_mode(
    key: Any, mcp_session: Any
) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "tool_create_http", **TOOL, silent_reply=True, execution={"mode": "background"}, plan=True
        )

    assert result.get("ok") is not True
    assert "silent_reply" in str(result)


async def test_tool_create_mcp_plans_the_tool_options(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "tool_create_mcp",
            name="crm",
            url="https://mcp.example.com/mcp",
            allowed_tools=["search"],
            tool_options={"search": {"mode": "background", "report_progress": True}},
            plan=True,
        )

    assert result["ok"] is True, result
    [step] = result["plan"]
    options = step["body"]["definition"]["tool_options"]
    assert options["search"]["mode"] == "background"
    assert options["search"]["report_progress"] is True


async def test_tool_update_sets_the_execution_of_an_http_tool(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        created = await mcp.call("tool_create_http", **TOOL)
        tool_id = created["data"]["tool"]["id"]
        planned = await mcp.call(
            "tool_update", tool_id=tool_id, patch={}, execution={"mode": "background"}, plan=True
        )
        applied = await mcp.call("tool_update", tool_id=tool_id, patch={}, execution={"mode": "auto"})

    [step] = planned["plan"]
    assert step["body"]["definition"]["execution"]["mode"] == "background"
    assert applied["ok"] is True, applied
    assert applied["data"]["definition"]["execution"]["mode"] == "auto"


async def test_tool_update_refuses_execution_on_an_mcp_server(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        created = await mcp.call("tool_create_mcp", name="crm", url="https://mcp.example.com/mcp")
        result = await mcp.call(
            "tool_update", tool_id=created["data"]["id"], patch={}, execution={"mode": "auto"}
        )

    assert result["ok"] is False
    assert "tool_options" in result["error"]["message"]


# ------------------------------------------------------------------ V5-09: MCP auth and tool_test
async def test_tool_create_mcp_plans_header_auth(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "tool_create_mcp",
            name="crm",
            url="https://mcp.example.com/mcp",
            auth={"kind": "header", "headers": {"x-api-key": "{{ secret.KEY }}"}, "credential_id": "cred_1"},
            plan=True,
        )

    assert result["ok"] is True, result
    [step] = result["plan"]
    assert step["body"]["definition"]["auth"] == {
        "kind": "header",
        "headers": {"x-api-key": "{{ secret.KEY }}"},
        "credential_id": "cred_1",
    }


async def test_tool_create_mcp_refuses_auth_together_with_the_older_fields(
    key: Any, mcp_session: Any
) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "tool_create_mcp",
            name="crm",
            url="https://mcp.example.com/mcp",
            auth={"kind": "none"},
            headers={"x-api-key": "k"},
        )

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_argument"


async def test_tool_create_mcp_older_headers_still_create_header_auth(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "tool_create_mcp", name="crm", url="https://mcp.example.com/mcp", headers={"x-team": "blue"}
        )

    assert result["ok"] is True, result
    assert result["data"]["definition"]["auth"]["kind"] == "header"
    assert any("tool_test" in step for step in result["next_steps"])


async def test_tool_create_mcp_oauth_is_accepted_pending_sign_in(key: Any, mcp_session: Any) -> None:
    # V5-14: an oauth MCP server is saved; the admin then signs in from the console.
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "tool_create_mcp", name="crm", url="https://mcp.example.com/mcp", auth={"kind": "oauth"}
        )

    assert result["ok"] is True
    assert result["data"]["definition"]["auth"]["kind"] == "oauth"


@pytest.mark.parametrize(
    "patch",
    [
        {"definition": {"headers": {"x-team": "green"}}},
        {"definition": {"auth": {"kind": "header", "headers": {"x-team": "green"}}}},
    ],
)
async def test_tool_update_changes_mcp_header_auth_in_either_spelling(
    key: Any, mcp_session: Any, patch: dict[str, Any]
) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        created = await mcp.call(
            "tool_create_mcp", name="crm", url="https://mcp.example.com/mcp", headers={"x-team": "blue"}
        )
        updated = await mcp.call("tool_update", tool_id=created["data"]["id"], patch=patch)

    assert updated["ok"] is True, updated
    definition = updated["data"]["definition"]
    assert definition["auth"]["headers"] == {"x-team": "green"}
    assert definition["headers"] == {"x-team": "green"}


async def test_tool_test_posts_the_test_route_and_wraps_the_names(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        created = await mcp.call("tool_create_mcp", name="crm", url="https://mcp.example.com/mcp")
        tool_id = created["data"]["id"]
        mcp.transport.fabricated[("POST", f"/v1/tools/{tool_id}/test")] = httpx.Response(
            200,
            json={"ok": True, "tool_names": ["lookup", "open_claim"], "tool_count": 2, "duration_ms": 12},
        )
        result = await mcp.call("tool_test", tool_id=tool_id)

    assert result["ok"] is True, result
    assert result["data"]["tool_count"] == 2
    assert result["data"]["tool_names"]["content"] == "lookup, open_claim"
    assert result["data"]["tool_names"]["source"] == f"mcp:{tool_id}"


async def test_tool_test_reports_a_failed_listing_as_a_warning(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        created = await mcp.call("tool_create_mcp", name="crm", url="https://mcp.example.com/mcp")
        tool_id = created["data"]["id"]
        mcp.transport.fabricated[("POST", f"/v1/tools/{tool_id}/test")] = httpx.Response(
            200, json={"ok": False, "reason": "needs_auth", "error": "the server answered 401"}
        )
        result = await mcp.call("tool_test", tool_id=tool_id)

    assert result["data"]["reason"] == "needs_auth"
    assert any("401" in warning for warning in result["warnings"])


# ------------------------------------------------------------------ V5-25 tool templates
CAL_NAMES = [
    "booking_check_availability",
    "booking_create",
    "booking_list",
    "booking_get",
    "booking_reschedule",
    "booking_cancel",
]


async def test_tool_templates_lists_the_cal_com_set(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call("tool_templates")

    assert result["ok"] is True, result
    ids = [item["id"] for item in result["data"]]
    assert ids[:6] == [f"cal_com.{name}" for name in CAL_NAMES]
    assert result["data"][1]["secret_names"] == ["CAL_API_KEY"]
    assert result["data"][1]["defaults"][0]["name"] == "event_type_id"


async def test_tool_create_from_template_plans_the_instantiate_request(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "tool_create_from_template",
            template_id="cal_com",
            secret_key_id="cred_1",
            defaults={"event_type_id": 42},
            names=["booking_create"],
            plan=True,
        )
        posts = mcp.transport.calls("POST", "/v1/tool-templates/cal_com/instantiate")

    assert result["ok"] is True, result
    [step] = result["plan"]
    assert step["method"] == "POST"
    assert step["path"] == "/v1/tool-templates/cal_com/instantiate"
    assert step["body"] == {
        "credential_id": "cred_1",
        "defaults": {"event_type_id": 42},
        "agent_id": None,
        "names": ["booking_create"],
    }
    assert posts == []


async def test_tool_create_from_template_creates_the_tools(key: Any, mcp_session: Any, admin: Any) -> None:
    response = await admin.post(
        "/v1/credentials",
        json={
            "provider_id": "http-tool-secret",
            "label": "Cal.com",
            "secrets": {"CAL_API_KEY": "cal_not_real"},
        },
    )
    assert response.status_code == 201, response.text
    # Binding a key to a tool needs `providers:write` as well (R-V2-33), as for tool_create_http.
    raw = await key([*BUILDER_SCOPES, "providers:write"])

    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "tool_create_from_template",
            template_id="cal_com",
            secret_key_id=response.json()["id"],
            defaults={"event_type_id": 42},
        )

    assert result["ok"] is True, result
    assert result["data"]["names"] == CAL_NAMES
    assert len(result["data"]["tool_ids"]) == 6
    assert any("agent_attach" in step for step in result["next_steps"])


async def test_the_tools_concept_and_the_booking_recipe_are_served(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        concept = await mcp.call("lkap_explain", topic="tools")
        recipe = await mcp.call("lkap_describe", kind="recipe", id="add-booking-tool")
        found = await mcp.call("lkap_search_docs", query="spell_back")

    assert concept["ok"] is True, concept
    assert concept["data"]["markdown"].startswith("# Tools: built-ins and templates")
    assert recipe["ok"] is True, recipe
    assert "tool_create_from_template" in recipe["data"]["markdown"]
    assert found["ok"] is True, found
    assert "lkap://concepts/tools" in str(found["data"])
