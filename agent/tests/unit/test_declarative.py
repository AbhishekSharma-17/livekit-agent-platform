"""Tests for `lkap_agent.tools.declarative` — HTTP tools mocked at the
`httpx` transport boundary via `respx` (no real network). MCP servers are
covered here only for the empty list and the missing-extra path; the URL
guard, transport and redirect behaviour live in `test_mcp_guard.py`.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, cast

import httpx
import pytest
import respx
from fakes.fake_ctx import FakeRunContext
from livekit.agents import RunContext, ToolError
from livekit.agents.llm import ToolFlag
from lkap_contracts.tools import (
    HttpToolDefinition,
    McpServerDefinition,
    McpServerOrigin,
    ProviderToolDefinition,
    ToolExecution,
)

from lkap_agent.settings import DEFAULT_HTTP_TOOL_USER_AGENT
from lkap_agent.tools.declarative import build_http_tools, build_mcp_servers, build_mcp_toolsets
from lkap_agent.tools.execution import bind_agent_policy, policy_of


@dataclass
class _FakeFunctionCall:
    call_id: str = "call-1"


@dataclass
class _FakeRunContext:
    function_call: _FakeFunctionCall = field(default_factory=_FakeFunctionCall)


def _run_ctx() -> RunContext:
    return cast(RunContext, _FakeRunContext())


def _base_def(**overrides: Any) -> HttpToolDefinition:
    defaults: dict[str, Any] = {
        "name": "lookup_item",
        "description": "Look up an item.",
        "parameters": {
            "type": "object",
            "properties": {"item_id": {"type": "string"}},
            "required": ["item_id"],
        },
        "method": "GET",
        "url": "https://api.example.com/items/{{ item_id }}",
        "allowed_hosts": ["api.example.com"],
    }
    defaults.update(overrides)
    return HttpToolDefinition(**defaults)


class TestBuildHttpToolsHappyPath:
    @respx.mock
    async def test_get_call_returns_response_text(self) -> None:
        route = respx.get("https://api.example.com/items/42").mock(
            return_value=httpx.Response(200, text="ok")
        )
        (tool,) = build_http_tools([_base_def()])

        result = await tool(raw_arguments={"item_id": "42"}, context=_run_ctx())

        assert route.called
        assert result == "ok"

    @respx.mock
    async def test_sends_the_platform_user_agent_by_default(self) -> None:
        """Asks #29 / B-4: Wikimedia refuses httpx without a contact-bearing User-Agent."""
        route = respx.get("https://api.example.com/items/42").mock(
            return_value=httpx.Response(200, text="ok")
        )
        (tool,) = build_http_tools([_base_def()], user_agent=DEFAULT_HTTP_TOOL_USER_AGENT)

        await tool(raw_arguments={"item_id": "42"}, context=_run_ctx())

        sent = route.calls.last.request.headers["User-Agent"]
        assert sent == DEFAULT_HTTP_TOOL_USER_AGENT
        assert "https://github.com/" in sent  # the contact URL Wikimedia's policy asks for

    @respx.mock
    async def test_a_tools_own_user_agent_header_wins(self) -> None:
        route = respx.get("https://api.example.com/items/42").mock(
            return_value=httpx.Response(200, text="ok")
        )
        definition = _base_def(headers={"user-agent": "weather-demo (ops@example.com)"})
        (tool,) = build_http_tools([definition], user_agent=DEFAULT_HTTP_TOOL_USER_AGENT)

        await tool(raw_arguments={"item_id": "42"}, context=_run_ctx())

        assert route.calls.last.request.headers["User-Agent"] == "weather-demo (ops@example.com)"

    @respx.mock
    async def test_url_argument_is_url_encoded(self) -> None:
        route = respx.get("https://api.example.com/items/a%20b%2Fc").mock(
            return_value=httpx.Response(200, text="ok")
        )
        (tool,) = build_http_tools([_base_def()])

        await tool(raw_arguments={"item_id": "a b/c"}, context=_run_ctx())

        assert route.called

    @respx.mock
    async def test_default_body_is_json_of_arguments(self) -> None:
        route = respx.post("https://api.example.com/items/42").mock(
            return_value=httpx.Response(200, text="created")
        )
        definition = _base_def(method="POST", url="https://api.example.com/items/42")

        (tool,) = build_http_tools([definition])
        await tool(raw_arguments={"item_id": "42", "note": "hi"}, context=_run_ctx())

        sent = route.calls.last.request
        assert sent.headers["content-type"] == "application/json"
        assert sent.content == b'{"item_id": "42", "note": "hi"}'

    @respx.mock
    async def test_body_template_json_escapes_quotes_in_arguments(self) -> None:
        route = respx.post("https://api.example.com/items/42").mock(
            return_value=httpx.Response(200, text="created")
        )
        definition = _base_def(
            method="POST",
            url="https://api.example.com/items/42",
            body_template='{"comment": "{{ comment }}"}',
        )

        (tool,) = build_http_tools([definition])
        await tool(raw_arguments={"item_id": "42", "comment": 'she said "hi"'}, context=_run_ctx())

        sent = route.calls.last.request
        assert sent.content == b'{"comment": "she said \\"hi\\""}'

    @respx.mock
    async def test_result_path_extracts_json_pointer(self) -> None:
        respx.get("https://api.example.com/items/42").mock(
            return_value=httpx.Response(200, json={"data": {"summary": "brief"}})
        )
        definition = _base_def(result_path="/data/summary")

        (tool,) = build_http_tools([definition])
        result = await tool(raw_arguments={"item_id": "42"}, context=_run_ctx())

        assert result == '"brief"'

    @respx.mock
    async def test_result_is_truncated_to_max_result_chars(self) -> None:
        respx.get("https://api.example.com/items/42").mock(return_value=httpx.Response(200, text="x" * 100))
        definition = _base_def(max_result_chars=10)

        (tool,) = build_http_tools([definition])
        result = await tool(raw_arguments={"item_id": "42"}, context=_run_ctx())

        assert result == "x" * 10 + "... [truncated]"

    @respx.mock
    async def test_does_not_follow_redirects(self) -> None:
        """A 3xx from an allowed host must not be followed to a disallowed one."""
        respx.get("https://api.example.com/items/42").mock(
            return_value=httpx.Response(302, headers={"Location": "http://169.254.169.254/secret"})
        )
        (tool,) = build_http_tools([_base_def()])

        # No exception: httpx.AsyncClient(follow_redirects=False) returns the 302 itself,
        # it never even attempts a connection to the Location host.
        result = await tool(raw_arguments={"item_id": "42"}, context=_run_ctx())
        assert result == ""

    def test_parameters_missing_type_object_is_normalized(self) -> None:
        definition = _base_def(parameters={"properties": {"item_id": {"type": "string"}}})
        (tool,) = build_http_tools([definition])
        assert tool.info.raw_schema["parameters"]["type"] == "object"


class TestBuildHttpToolsSecurity:
    @respx.mock
    async def test_rejects_when_no_allowlist_matches(self) -> None:
        definition = _base_def(allowed_hosts=["other.example.com"])
        (tool,) = build_http_tools([definition], platform_allowed_hosts=["also-other.example.com"])

        with pytest.raises(ToolError, match="not on the outbound allowlist"):
            await tool(raw_arguments={"item_id": "42"}, context=_run_ctx())

    @respx.mock
    async def test_rejects_when_both_allowlists_empty(self) -> None:
        definition = _base_def(allowed_hosts=[])
        (tool,) = build_http_tools([definition], platform_allowed_hosts=None)

        with pytest.raises(ToolError, match="not on the outbound allowlist"):
            await tool(raw_arguments={"item_id": "42"}, context=_run_ctx())

    @respx.mock
    async def test_allows_via_platform_allowlist_when_tool_allowlist_empty(self) -> None:
        respx.get("https://api.example.com/items/42").mock(return_value=httpx.Response(200, text="ok"))
        definition = _base_def(allowed_hosts=[])

        (tool,) = build_http_tools([definition], platform_allowed_hosts=["api.example.com"])
        result = await tool(raw_arguments={"item_id": "42"}, context=_run_ctx())

        assert result == "ok"

    async def test_rejects_non_http_scheme(self) -> None:
        definition = _base_def(url="file:///etc/passwd")
        (tool,) = build_http_tools([definition])

        with pytest.raises(ToolError, match="unsupported URL scheme"):
            await tool(raw_arguments={"item_id": "42"}, context=_run_ctx())

    async def test_unresolved_secret_placeholder_in_header_is_rejected(self) -> None:
        definition = _base_def(headers={"Authorization": "Bearer {{ secret.API_KEY }}"})
        (tool,) = build_http_tools([definition])

        with pytest.raises(ToolError, match="unresolved template"):
            await tool(raw_arguments={"item_id": "42"}, context=_run_ctx())

    async def test_missing_required_argument_leaves_url_unresolved_and_is_rejected(self) -> None:
        # raw function tools get no per-parameter validation (the model's arguments
        # dict is passed through as-is), so a call missing `item_id` must not be sent
        # with a literal "{{ item_id }}" in the URL.
        (tool,) = build_http_tools([_base_def()])

        with pytest.raises(ToolError, match="unresolved template in url"):
            await tool(raw_arguments={}, context=_run_ctx())


class TestBuildMcpServers:
    def test_empty_defs_returns_empty_list_without_importing(self) -> None:
        assert build_mcp_servers([]) == []

    def test_missing_mcp_extra_degrades_to_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Pin the "extra not installed" failure with `None` sys.modules entries (the
        # standard "this import is broken" sentinel): `build_mcp_servers` imports
        # `lkap_agent.tools.mcp_client`, which imports `livekit.agents.llm.mcp`, and
        # either import raising ImportError must degrade to no MCP servers.
        monkeypatch.setitem(sys.modules, "livekit.agents.llm.mcp", None)
        monkeypatch.setitem(sys.modules, "lkap_agent.tools.mcp_client", None)
        defn = McpServerDefinition(name="tools-server", url="https://mcp.example.com/mcp")

        assert build_mcp_servers([defn]) == []


class TestExecutionPolicy:
    """V4-12 (docs/v4/BACKGROUND-TOOLS.md §4.2): flags and duplicate settings on built tools."""

    def test_a_blocking_tool_is_declared_exactly_as_before(self) -> None:
        (tool,) = build_http_tools([_base_def()])

        assert (tool.info.flags, tool.info.on_duplicate, tool.info.duplicate_scope) == (
            ToolFlag.NONE,
            "allow",
            "name",
        )
        assert "lk_agents_confirm_duplicate" not in json.dumps(tool.info.raw_schema)

    def test_a_get_tool_under_an_auto_default_is_cancellable_and_rejects_duplicates(self) -> None:
        (tool,) = build_http_tools([_base_def()], execution_default="auto")

        assert (tool.info.flags, tool.info.on_duplicate, tool.info.duplicate_scope) == (
            ToolFlag.CANCELLABLE,
            "reject",
            "name_and_args",
        )

    def test_a_post_tool_ignores_the_default_and_confirms_when_it_opts_in(self) -> None:
        (default_only,) = build_http_tools([_base_def(method="POST")], execution_default="auto")
        (opted,) = build_http_tools([_base_def(method="POST", execution=ToolExecution(mode="background"))])

        assert default_only.info.on_duplicate == "allow"
        assert (opted.info.flags, opted.info.on_duplicate) == (ToolFlag.NONE, "confirm")
        assert "lk_agents_confirm_duplicate" in opted.info.raw_schema["parameters"]["properties"]

    def test_a_flow_node_tool_is_built_blocking_below_1_8_3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("livekit.agents.__version__", "1.8.2")  # the downgrade path (R-V4-54)
        (tool,) = build_http_tools([_base_def(execution=ToolExecution(mode="background"))], flow_node=True)

        assert tool.info.flags == ToolFlag.NONE
        assert tool.info.on_duplicate == "allow"

    def test_the_policy_rebinds_to_an_agent_default(self) -> None:
        (tool,) = build_http_tools([_base_def()])

        (bound,) = bind_agent_policy([tool], execution_default="auto", flow_node=False)
        (same,) = bind_agent_policy([tool], execution_default="blocking", flow_node=False)

        assert bound is not tool and bound.info.flags == ToolFlag.CANCELLABLE
        assert same is tool

    @respx.mock
    async def test_a_background_tool_announces_then_returns_the_response(self) -> None:
        respx.get("https://api.example.com/items/42").mock(return_value=httpx.Response(200, text="ok"))
        (tool,) = build_http_tools([_base_def(execution=ToolExecution(mode="background"))])
        context = FakeRunContext()

        result = await tool(raw_arguments={"item_id": "42"}, context=cast(RunContext, context))

        assert result == "ok"
        assert context.updates == ["Working on lookup item."]


class TestBuildMcpToolsets:
    def test_each_server_becomes_a_toolset_over_the_guarded_server_with_its_tool_options(self) -> None:
        from livekit.agents.llm.mcp import MCPToolset  # noqa: PLC0415

        from lkap_agent.tools.mcp_client import GuardedMCPServerHTTP  # noqa: PLC0415

        defn = McpServerDefinition(
            name="crm",
            url="https://mcp.example.com/mcp",
            timeout_s=9,
            tool_options={
                "search": ToolExecution(mode="background", report_progress=True),
                "lookup": ToolExecution(),
            },
        )

        (toolset,) = build_mcp_toolsets([defn])

        assert isinstance(toolset, MCPToolset)
        assert toolset.id == "mcp_crm"
        assert isinstance(toolset._mcp_server, GuardedMCPServerHTTP)
        assert toolset._mcp_server._timeout == 9
        assert toolset._tool_options == {
            "search": {
                "flags": ToolFlag.NONE,
                "on_duplicate": "confirm",
                "duplicate_scope": "name_and_args",
                "report_progress": True,
            },
            "lookup": {
                "flags": ToolFlag.NONE,
                "on_duplicate": "allow",
                "duplicate_scope": "name",
                "report_progress": False,
            },
        }
        policies = policy_of(toolset)
        assert isinstance(policies, dict) and policies["search"].resolved.mode == "background"

    def test_flow_node_toolsets_keep_their_tools_blocking(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("livekit.agents.__version__", "1.8.2")  # the downgrade path (R-V4-54)
        defn = McpServerDefinition(
            name="crm",
            url="https://mcp.example.com/mcp",
            tool_options={"search": ToolExecution(mode="background", report_progress=True)},
        )

        (toolset,) = build_mcp_toolsets([defn], flow_node=True)

        assert toolset._tool_options["search"]["report_progress"] is False
        assert toolset._tool_options["search"]["on_duplicate"] == "allow"

    def test_the_old_name_is_an_alias_that_returns_toolsets(self) -> None:
        from livekit.agents.llm.mcp import MCPToolset  # noqa: PLC0415

        (toolset,) = build_mcp_servers([McpServerDefinition(name="crm", url="https://mcp.example.com/mcp")])

        assert isinstance(toolset, MCPToolset)


class TestComposioToolFinder:
    """V5-47 (COMPOSIO.md D-V5-C7): the tool finder's meta tools under the execution policy."""

    def test_multi_execute_is_never_backgrounded_whatever_it_declares(self) -> None:
        """Tripwire: running actions found at run time always waits for the result."""
        defn = McpServerDefinition(
            name="composio_tool_finder",
            url="https://backend.composio.dev/tool_router/trs_1/mcp",
            allowed_tools=["COMPOSIO_SEARCH_TOOLS", "COMPOSIO_MULTI_EXECUTE_TOOL"],
            tool_options={
                "COMPOSIO_SEARCH_TOOLS": ToolExecution(mode="auto", announce="One moment"),
                "COMPOSIO_MULTI_EXECUTE_TOOL": ToolExecution(mode="background", cancellable=True),
            },
            origin=McpServerOrigin(kind="router", remote_id="trs_1"),
        )

        (toolset,) = build_mcp_toolsets([defn])

        policies = policy_of(toolset)
        assert isinstance(policies, dict)
        multi = policies["COMPOSIO_MULTI_EXECUTE_TOOL"].resolved
        assert multi.mode == "blocking"
        assert multi.cancellable is False
        assert multi.downgraded_from == "background"
        assert policies["COMPOSIO_SEARCH_TOOLS"].resolved.mode == "auto"
        assert toolset._tool_options["COMPOSIO_MULTI_EXECUTE_TOOL"]["flags"] == ToolFlag.NONE

    def test_provider_and_http_definitions_share_the_declarative_builder(self) -> None:
        provider = ProviderToolDefinition(
            name="acmecrm_list_contacts",
            description="List contacts.",
            parameters={"type": "object", "properties": {}},
            tool_slug="ACMECRM_LIST_CONTACTS",
            connection_id="conn1",
            subject="ws:w1",
            headers={"x-api-key": "ak_placeholder_resolved"},
        )

        tools = build_http_tools([_base_def(), provider])

        assert [tool.info.name for tool in tools] == ["lookup_item", "acmecrm_list_contacts"]
