"""Scope-shaped tool lists (D-V3-6, R-V3-13), the dial gate (R-V3-7) and attribution (R-V3-11)."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import BUILDER_SCOPES, CLIENT_NAME, OPERATOR_SCOPES, READ_ONLY_SCOPES

from lkap_mcp.server import OPTIONAL_TOOL_MODULES, load_tool_modules

#: Every tool that writes (its only purpose is a write).
WRITE_TOOLS = {
    "connection_create",
    "connection_test",
    "connection_rotate",
    "provider_settings",
    "provider_key_create",
    "provider_key_test",
    "agent_create",
    "agent_update",
    "agent_validate",
    "agent_publish",
    "agent_archive",
    "agent_attach",
    "agent_flow_validate",
    "kb_create",
    "kb_add_document",
    "kb_search",
    "tool_create_http",
    "tool_create_mcp",
    "tool_update",
    "tool_dry_run",
    "session_rescore",
    "webhook_create",
    "webhook_test",
    "webhook_list",
    "webhook_deliveries",
    "call_place",
    "call_control",
    "lkap_delete",
}


async def test_list_tools_read_only_key_has_no_write_tool_and_agent_create_is_unknown(
    key: Any, mcp_session: Any
) -> None:
    raw = await key(READ_ONLY_SCOPES)

    async with mcp_session(raw) as mcp:
        listed = (await mcp.session.list_tools()).tools
        names = {tool.name for tool in listed}
        unknown = await mcp.call("agent_create", name="Nope")

    assert not names & WRITE_TOOLS
    assert {"me", "lkap_guide", "agent_list", "connection_list", "activity", "session_list"} <= names
    assert all(tool.annotations and tool.annotations.readOnlyHint for tool in listed), [
        tool.name for tool in listed if not (tool.annotations and tool.annotations.readOnlyHint)
    ]
    assert unknown["is_error"] and "Unknown tool" in unknown["text"]


async def test_list_tools_builder_key_sees_agent_tools_but_not_connection_writes(
    key: Any, mcp_session: Any
) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        names = set(await mcp.tool_names())

    assert {"agent_create", "agent_update", "kb_create", "tool_create_http", "lkap_delete"} <= names
    assert not {"connection_create", "connection_rotate", "provider_key_create", "webhook_create"} & names


async def test_list_tools_read_only_mode_hides_writes_even_for_an_operator_key(
    key: Any, mcp_session: Any
) -> None:
    raw = await key(OPERATOR_SCOPES)

    async with mcp_session(raw, read_only=True) as mcp:
        listed = (await mcp.session.list_tools()).tools
        fleet = await mcp.call("connection_fleet", id="default", action="restart", confirm=True)

    assert not {tool.name for tool in listed} & WRITE_TOOLS
    assert all(tool.annotations and tool.annotations.readOnlyHint for tool in listed)
    assert fleet["error"]["code"] == "forbidden"


@pytest.mark.parametrize(
    ("scopes", "allow_dial", "expected"),
    [
        ([*OPERATOR_SCOPES, "calls:write"], True, True),
        ([*OPERATOR_SCOPES, "calls:write"], False, False),
        (OPERATOR_SCOPES, True, False),
    ],
)
async def test_list_tools_dial_tools_need_calls_write_and_allow_dial(
    key: Any, mcp_session: Any, scopes: list[str], allow_dial: bool, expected: bool
) -> None:
    raw = await key(scopes)

    async with mcp_session(raw, allow_dial=allow_dial) as mcp:
        names = set(await mcp.tool_names())

    assert ({"call_place", "call_control"} <= names) is expected
    assert "telephony_overview" in names


async def test_call_place_without_confirm_is_needs_confirmation_and_dials_nothing(
    key: Any, mcp_session: Any
) -> None:
    raw = await key([*OPERATOR_SCOPES, "calls:write"])

    async with mcp_session(raw, allow_dial=True) as mcp:
        result = await mcp.call("call_place", agent_id="a1", to_e164="+15550100")
        posted = mcp.transport.calls("POST", "/v1/calls")

    assert result["error"]["code"] == "needs_confirmation"
    assert posted == []


async def test_every_request_carries_x_lkap_client_with_the_handshake_client_name(
    key: Any, mcp_session: Any
) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        await mcp.call("me")
        await mcp.call("kb_create", name="Policies")
        await mcp.call("agent_list")
        requests = list(mcp.transport.requests)

    assert requests[0].url.path == "/v1/api-keys/self"
    for request in requests:
        header = request.headers.get("X-LKAP-Client", "")
        assert header.startswith("lkap-mcp/") and f"client={CLIENT_NAME}" in header, (request.url, header)
    tools = {req.headers["X-LKAP-Client"].split("tool=")[1].split(";")[0] for req in requests[1:]}
    assert {"me", "kb_create", "agent_list"} <= tools


async def test_me_reports_scopes_workspace_and_dial_state(key: Any, mcp_session: Any) -> None:
    raw = await key(READ_ONLY_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call("me")

    data = result["data"]
    assert result["ok"] is True
    assert sorted(data["key"]["scopes"]) == sorted(READ_ONLY_SCOPES)
    assert data["workspace"]["slug"] == "default"
    assert data["dial_enabled"] is False and "db" in data["health"]
    assert data["health"]["connections"]["n"] >= 1  # the bootstrapped default connection
    assert raw not in result["_text"]


async def test_revoked_key_sees_only_the_public_tools_and_me_relays_401(
    key: Any, mcp_session: Any, admin: Any
) -> None:
    raw = await key(BUILDER_SCOPES)
    listed = await admin.get("/v1/api-keys", params={"limit": 200})
    [row] = [item for item in listed.json()["items"] if raw.startswith(item["prefix"])]
    assert (await admin.delete(f"/v1/api-keys/{row['id']}")).status_code == 204

    async with mcp_session(raw) as mcp:
        names = set(await mcp.tool_names())
        result = await mcp.call("me")

    assert names == {
        "lkap_guide",
        "lkap_explain",
        "lkap_describe",
        "lkap_search_docs",
        "me",
        "workspace_get",
        "api_request",
    }
    assert result["ok"] is False and result["error"]["status"] == 401
    assert "console" in (result["error"]["hint"] or "")


async def test_argument_validation_error_does_not_echo_the_input_value(key: Any, mcp_session: Any) -> None:
    raw = await key(OPERATOR_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "provider_key_create", provider_id="openai-llm", label="x", secrets={"api_key": 987654321987}
        )

    assert result["is_error"] is True
    assert "987654321987" not in result["text"]
    assert "input_value=<hidden>" in result["text"]


def test_load_tool_modules_skips_only_a_missing_optional_module() -> None:
    assert load_tool_modules(["lkap_mcp.tools.discovery", *OPTIONAL_TOOL_MODULES])
    with pytest.raises(ModuleNotFoundError):
        load_tool_modules(["lkap_mcp.tools.not_a_module"])
