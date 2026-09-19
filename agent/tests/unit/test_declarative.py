"""Tests for `lkap_agent.tools.declarative` — HTTP tools mocked at the
`httpx` transport boundary via `respx` (no real network); MCP servers tested
against a faked `livekit.agents.mcp` module (the real `mcp` PyPI package is
not installed — see the package report for why, and the dedicated test
below for the resulting graceful-degradation path).
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from typing import Any, cast

import httpx
import pytest
import respx
from livekit.agents import RunContext, ToolError
from lkap_contracts.tools import HttpToolDefinition, McpServerDefinition

from lkap_agent.tools.declarative import build_http_tools, build_mcp_servers


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
        # `mcp` (the PyPI package) genuinely is not installed in this environment
        # (agent/pyproject.toml doesn't pin the `livekit-agents[mcp]` extra yet —
        # see this package's report), but pin the failure explicitly with a `None`
        # sys.modules entry (the standard "this import is broken" sentinel — `from
        # .llm import mcp` raises ModuleNotFoundError, which `except ImportError`
        # catches) so the test stays deterministic once that extra is added.
        monkeypatch.setitem(sys.modules, "livekit.agents.llm.mcp", None)
        defn = McpServerDefinition(name="tools-server", url="https://mcp.example.com/mcp")

        assert build_mcp_servers([defn]) == []

    def test_builds_mcp_server_http_with_expected_kwargs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_module = types.ModuleType("livekit.agents.llm.mcp")
        calls: list[dict[str, Any]] = []

        class _FakeMCPServerHTTP:
            def __init__(self, **kwargs: Any) -> None:
                calls.append(kwargs)

        fake_module.MCPServerHTTP = _FakeMCPServerHTTP  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "livekit.agents.llm.mcp", fake_module)

        defn = McpServerDefinition(
            name="tools-server",
            url="https://mcp.example.com/mcp",
            headers={"Authorization": "Bearer resolved-secret"},
            allowed_tools=["do_thing"],
            timeout_s=5,
            sse_read_timeout_s=300,
        )

        servers = build_mcp_servers([defn])

        assert len(servers) == 1
        assert calls == [
            {
                "url": "https://mcp.example.com/mcp",
                "transport_type": "streamable_http",
                "allowed_tools": ["do_thing"],
                "headers": {"Authorization": "Bearer resolved-secret"},
                "timeout": 5,
                "sse_read_timeout": 300,
            }
        ]
