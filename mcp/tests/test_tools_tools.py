"""The tool-management tools' background-execution arguments (V4-12, docs/v4/BACKGROUND-TOOLS.md §7)."""

from __future__ import annotations

from typing import Any

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
