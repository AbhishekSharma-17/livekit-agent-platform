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
from lkap_agent.tools._http_safety import (
    HttpToolSecurityError,
    check_url_allowed,
    check_url_public,
    guarded_transport,
    truncate,
)

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
            async with httpx.AsyncClient(
                follow_redirects=False, timeout=definition.timeout_s, transport=guarded_transport()
            ) as client:
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
        platform_allowed_hosts: `LKAP_HTTP_TOOL_ALLOWED_HOSTS`. Since F-14 a
            request must pass the intersection of this list with each
            definition's own `allowed_hosts`, plus a private-range deny-list,
            enforced in `_http_safety.check_url_allowed` (docs/CONTRACTS.md §3/§9).

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


#: Called with `(definition, reason)` for each MCP server `build_mcp_servers` refuses.
McpSkipCallback = Callable[[McpServerDefinition, str], None]


def build_mcp_servers(
    defs: list[McpServerDefinition],
    *,
    on_skipped: McpSkipCallback | None = None,
    transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
) -> list[Any]:
    """Build one guarded `MCPServerHTTP` per `McpServerDefinition` whose URL is public.

    Every URL goes through `_http_safety.check_url_public` (http/https, a host,
    not in a private, loopback, link-local or metadata range) before a server
    is built: the api checks it at save time, but the worker is the process
    that connects. A refused server is skipped with a warning log and an
    `on_skipped` call, never an exception, so the session still starts with
    its other tools. Each built server is a
    :class:`~lkap_agent.tools.mcp_client.GuardedMCPServerHTTP`, whose client
    does not follow redirects and connects through `guarded_transport()`.

    No host allowlist applies: `LKAP_HTTP_TOOL_ALLOWED_HOSTS` semantics would
    refuse every MCP server whenever that list is unset (see `_http_safety`).

    `livekit.agents.mcp` needs the optional `mcp` package (the `mcp` extra of
    `livekit-agents` in `agent/pyproject.toml`). When the import fails this
    logs an error and returns `[]`, so the session degrades (no MCP tools)
    instead of failing.

    Args:
        defs: MCP server definitions from `ResolvedAgentConfig.tools`
            (already secret-substituted by the api).
        on_skipped: Called with `(definition, reason)` for each refused server
            (the worker records it as a session event). `reason` names the
            host, never the full URL.
        transport_factory: Builds each client's transport; defaults to
            `guarded_transport`. Tests pass a fake.

    Returns:
        Server instances, ready to pass to `Agent(mcp_servers=...)`.
    """
    if not defs:
        return []

    kept: list[McpServerDefinition] = []
    for definition in defs:
        try:
            check_url_public(definition.url)
        except HttpToolSecurityError as exc:
            reason = str(exc)
            _log.warning("declarative_tool.mcp_server_refused", mcp_server=definition.name, reason=reason)
            if on_skipped is not None:
                on_skipped(definition, reason)
            continue
        kept.append(definition)
    if not kept:
        return []

    try:
        # Imports `livekit.agents.llm.mcp`, which raises ImportError without the extra.
        from lkap_agent.tools.mcp_client import GuardedMCPServerHTTP  # noqa: PLC0415
    except ImportError:
        _log.error(
            "declarative_tool.mcp_extra_missing",
            detail="livekit-agents[mcp] extra is not installed; skipping MCP servers",
            count=len(kept),
        )
        return []

    return [
        GuardedMCPServerHTTP(
            url=definition.url,
            transport_type="streamable_http",
            allowed_tools=definition.allowed_tools,
            headers=definition.headers,
            timeout=definition.timeout_s,
            sse_read_timeout=definition.sse_read_timeout_s,
            transport_factory=transport_factory or guarded_transport,
        )
        for definition in kept
    ]
