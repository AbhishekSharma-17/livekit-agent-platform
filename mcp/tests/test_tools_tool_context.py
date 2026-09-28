"""V6-07: `tool_create_http` and `tool_create_mcp` carry the tool-context fields (D-V6-22/23)."""

from __future__ import annotations

from typing import Any

from conftest import BUILDER_SCOPES

TOOL = {
    "name": "lookup_policy",
    "description": "Look a policy up",
    "parameters": {"type": "object", "properties": {"email": {"type": "string"}}},
    "url": "https://api.example.com/policies?email={{ email }}&tz={{ ctx.timezone }}",
    "method": "GET",
    "allowed_hosts": ["api.example.com"],
}


async def test_tool_create_http_plans_the_tool_context_fields(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "tool_create_http",
            **TOOL,
            requires_vars=["policy_no"],
            confirm_readback=["email"],
            bindings=[{"path": "/holder", "to": "details:card.holder"}],
            plan=True,
        )

    assert result["ok"] is True, result
    [step] = result["plan"]
    definition = step["body"]["definition"]
    assert definition["url"] == TOOL["url"]
    assert definition["requires_vars"] == ["policy_no"]
    assert definition["confirm_readback"] == ["email"]
    assert definition["bindings"] == [{"path": "/holder", "to": "details:card.holder"}]


async def test_tool_create_http_refuses_a_placeholder_in_the_host(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "tool_create_http", **{**TOOL, "url": "https://{{ var.tenant }}.example.com/x"}, plan=True
        )
        posts = mcp.transport.calls("POST", "/v1/tools")

    assert result.get("ok") is not True
    assert "scheme, host or port" in str(result)
    assert posts == []


async def test_tool_create_mcp_plans_the_per_tool_context(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "tool_create_mcp",
            name="crm",
            url="https://mcp.example.com/mcp",
            allowed_tools=["find_contact"],
            tool_context={
                "find_contact": {
                    "pinned_arguments": {"account": "{{ var.account_no }}"},
                    "bindings": [{"path": "/name", "to": "details:card.contact"}],
                }
            },
            plan=True,
        )

    assert result["ok"] is True, result
    [step] = result["plan"]
    spec = step["body"]["definition"]["tool_context"]["find_contact"]
    assert spec["pinned_arguments"] == {"account": "{{ var.account_no }}"}
    assert spec["bindings"] == [{"path": "/name", "to": "details:card.contact"}]
