"""Declarative tools built at runtime from admin-authored JSON.

docs/CONTRACTS.md §9: HTTP/webhook tools and MCP servers, defined by
`lkap_contracts.tools.HttpToolDefinition` / `McpServerDefinition` and shipped
to the worker inside `ResolvedAgentConfig.tools`. The api has already
substituted `{{ secret.NAME }}` in `headers`/`url`/`body_template` from the
tool's `http-tool-secret` credential before the worker ever sees these
definitions (CONTRACTS §9 "Note on tool secrets") — this module only renders
the remaining `{{ arg }}` placeholders against the model's call arguments.

Raw HTTP tool construction is verified against livekit-agents==1.8.2
(`llm/tool_context.py`, `llm/utils.py::_prepare_function_arguments`): a
`RawFunctionTool`'s wrapped function is called with the whole JSON args dict
bound to a parameter literally named `raw_arguments`, plus the injected
`RunContext` — `async def handler(raw_arguments: dict[str, object], context:
RunContext) -> str`, exactly as CONTRACTS §9 specifies.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import httpx
from livekit.agents import RunContext, ToolError, function_tool
from livekit.agents.llm import RawFunctionTool
from lkap_contracts.tools import HttpToolDefinition, McpServerDefinition

from lkap_agent.logging import get_logger
from lkap_agent.tools._http_safety import HttpToolSecurityError, check_url_allowed, truncate

_log = get_logger(__name__)

#: Matches `{{ arg_name }}`-style placeholders only (not `{{ secret.NAME }}`,
#: whose dot the identifier pattern excludes — those are left untouched here
#: because the api has already resolved them before this module runs).
_ARG_PATTERN = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")

Handler = Callable[[dict[str, object], RunContext[Any]], Awaitable[str]]


def _render(template: str, arguments: dict[str, Any], *, escape: Callable[[str], str]) -> str:
    """Substitute `{{ arg }}` placeholders in `template`, applying `escape` to each value."""

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in arguments:
            return match.group(0)  # leave unknown placeholders (e.g. an unresolved secret) as-is
        return escape(str(arguments[name]))

    return _ARG_PATTERN.sub(_sub, template)


def _render_url(template: str, arguments: dict[str, Any]) -> str:
    return _render(template, arguments, escape=lambda v: quote(v, safe=""))


def _render_json_string(template: str, arguments: dict[str, Any]) -> str:
    """Substitute into a JSON-string `body_template`, JSON-escaping each value.

    `json.dumps(v)[1:-1]` yields the escaped *content* of a JSON string
    (quotes stripped), so a `"` or newline in an argument can't break out of
    the enclosing string in the template.
    """
    return _render(template, arguments, escape=lambda v: json.dumps(v)[1:-1])


def _resolve_json_pointer(data: Any, pointer: str) -> Any:
    """A minimal RFC 6901 JSON Pointer resolver, e.g. `"/data/summary"`."""
    if not pointer or pointer == "/":
        return data
    current = data
    for raw_segment in pointer.lstrip("/").split("/"):
        segment = raw_segment.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(segment)]
        elif isinstance(current, dict):
            current = current[segment]
        else:
            raise KeyError(f"cannot resolve {pointer!r} past segment {segment!r}")
    return current


def _reject_unresolved(rendered: str, *, where: str) -> None:
    """Raise if `rendered` still contains a `{{ ... }}` placeholder.

    A raw tool call gets no per-parameter validation (`validated_arguments`
    returns `RawFunctionTool` arguments as-is), so a model call that omits a
    required argument — or a `{{ secret.NAME }}` the api failed to resolve —
    leaves a literal placeholder in the rendered text. Sending that to a
    third party is worse than failing the call, so this checks every
    rendered surface (url, headers, body), not just headers.
    """
    if "{{" in rendered:
        raise ToolError(f"unresolved template in {where}")


def _handler_for(definition: HttpToolDefinition, *, platform_allowed_hosts: list[str] | None) -> Handler:
    async def handler(raw_arguments: dict[str, object], context: RunContext[Any]) -> str:
        arguments: dict[str, Any] = dict(raw_arguments)
        url = _render_url(definition.url, arguments)
        _reject_unresolved(url, where="url")

        try:
            check_url_allowed(
                url,
                tool_allowed_hosts=definition.allowed_hosts,
                platform_allowed_hosts=platform_allowed_hosts,
            )
        except HttpToolSecurityError as exc:
            raise ToolError(str(exc)) from exc

        headers = {name: _render(value, arguments, escape=str) for name, value in definition.headers.items()}
        for name, value in headers.items():
            _reject_unresolved(value, where=f"header {name!r}")

        body: str | None = None
        if definition.method in ("POST", "PUT", "PATCH"):
            body = (
                _render_json_string(definition.body_template, arguments)
                if definition.body_template is not None
                else json.dumps(arguments)
            )
            _reject_unresolved(body, where="body")

        request_headers = dict(headers)
        if body is not None and not any(name.lower() == "content-type" for name in request_headers):
            request_headers["Content-Type"] = "application/json"

        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=definition.timeout_s) as client:
                response = await client.request(definition.method, url, content=body, headers=request_headers)
        except httpx.HTTPError as exc:
            raise ToolError(f"HTTP request failed: {exc}") from exc

        _log.debug(
            "declarative_tool.http_call",
            tool=definition.name,
            call_id=context.function_call.call_id,
            method=definition.method,
            host=httpx.URL(url).host,
            status=response.status_code,
        )

        result_text = response.text
        if definition.result_path:
            try:
                payload = response.json()
                result_text = json.dumps(_resolve_json_pointer(payload, definition.result_path))
            except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
                raise ToolError(f"could not extract result_path {definition.result_path!r}: {exc}") from exc

        return truncate(result_text, definition.max_result_chars)

    return handler


def build_http_tools(
    defs: list[HttpToolDefinition],
    *,
    platform_allowed_hosts: list[str] | None = None,
) -> list[RawFunctionTool[..., Any]]:
    """Build one `@function_tool(raw_schema=...)` per `HttpToolDefinition`.

    Args:
        defs: HTTP tool definitions from `ResolvedAgentConfig.tools`
            (already secret-substituted by the api).
        platform_allowed_hosts: `LKAP_HTTP_TOOL_ALLOWED_HOSTS`, unioned with
            each definition's own `allowed_hosts` (docs/CONTRACTS.md §3/§9).

    Returns:
        One `RawFunctionTool` per definition, ready to pass to `Agent(tools=...)`.
    """
    tools: list[RawFunctionTool[..., Any]] = []
    for definition in defs:
        parameters = definition.parameters
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            parameters = {"type": "object", "properties": {}, **(parameters or {})}
        tool = function_tool(
            _handler_for(definition, platform_allowed_hosts=platform_allowed_hosts),
            raw_schema={
                "name": definition.name,
                "description": definition.description,
                "parameters": parameters,
            },
        )
        tools.append(tool)
    return tools


def build_mcp_servers(defs: list[McpServerDefinition]) -> list[Any]:
    """Build one `mcp.MCPServerHTTP` per `McpServerDefinition`.

    Lazy-imports `livekit.agents.mcp`, which itself requires the optional
    `mcp` PyPI package (`pip install 'livekit-agents[mcp]'`) — **not**
    currently in `agent/pyproject.toml`'s `livekit-agents[...]` extras (that
    file is W0-SCAFFOLD-owned/read-only for this package; see this
    package's report for the blocker raised to W2-AGENT-INTEGRATION/deploy).
    When `defs` is non-empty but the import fails, this logs an error and
    returns `[]` so a worker without the extra degrades the session (no MCP
    tools available) instead of crashing at startup.

    Args:
        defs: MCP server definitions from `ResolvedAgentConfig.tools`
            (already secret-substituted by the api).

    Returns:
        `mcp.MCPServerHTTP` instances, ready to pass to `Agent(mcp_servers=...)`.
    """
    if not defs:
        return []

    try:
        from livekit.agents import mcp
    except ImportError:
        _log.error(
            "declarative_tool.mcp_extra_missing",
            detail="livekit-agents[mcp] extra is not installed; skipping MCP servers",
            count=len(defs),
        )
        return []

    return [
        mcp.MCPServerHTTP(
            url=definition.url,
            transport_type="streamable_http",
            allowed_tools=definition.allowed_tools,
            headers=definition.headers,
            timeout=definition.timeout_s,
            sse_read_timeout=definition.sse_read_timeout_s,
        )
        for definition in defs
    ]
