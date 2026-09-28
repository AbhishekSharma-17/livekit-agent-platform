"""`MCPServerHTTP` with the SSRF-guarded transport and no redirect following.

Verified against livekit-agents 1.8.3 (`livekit/agents/llm/mcp.py`) and
mcp 1.30.0; `tests/unit/test_sdk_tripwires.py` fails when an upgrade changes
any of the facts below (D-V5-11, research-v4 tools §4.3.10):

* `MCPServerHTTP.__init__` has no `auth`/`transport` kwarg; the httpx client is
  built by `_create_http_client(headers=None, timeout=None, auth=None)`, which
  hard-codes `follow_redirects=True`. `client_streams()` calls it with no
  arguments on the streamable-HTTP path and passes it as
  `httpx_client_factory` on the SSE path, so overriding that one method
  covers both.
* The override builds the client with `follow_redirects=False` and
  :func:`~lkap_agent.tools._http_safety.guarded_transport`, whose network
  backend resolves the host, refuses any private answer and connects to the
  checked address (DNS rebinding included).
* mcp 1.30.0's streamable-HTTP transport sends every request with
  `follow_redirects=False` itself and follows only a redirect that stays on the
  endpoint's origin (`mcp.shared._httpx_utils.stream_within_origin`); every
  other 3xx surfaces as `httpx.HTTPStatusError`. Such a same-origin hop still
  connects through the guarded transport.

V5-27 (S5-6, R-V5-15): every tool result is handed to the model inside an
``<untrusted source="mcp:<server>">`` fence (:func:`fenced_result_resolver`, passed as
`MCPServerHTTP`'s `tool_result_resolver`); the content inside keeps the SDK's
default shape (one item's JSON, or a JSON list of items). An `isError` result
still becomes the SDK's `ToolError` with the server's text, unfenced.

V6-21 (S6-3, ask #277): every tool's result also passes the session's `tool_output`
guardrail (:func:`~lkap_agent.tools.execution.guard_tool_output`), as an HTTP, app or
dataset tool's does through `run_with_policy`: :meth:`GuardedMCPServerHTTP._make_function_tool`
wraps the SDK's per-tool function in one that always takes the call's `RunContext`.

Importing this module imports `livekit.agents.llm.mcp`, which needs the
optional `mcp` package: :func:`lkap_agent.tools.declarative.build_mcp_servers`
imports it lazily, after probing for the extra.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Literal

import httpx
from livekit.agents import RunContext, ToolError, function_tool
from livekit.agents.llm.mcp import (
    MCPServerHTTP,
    MCPToolOptions,
    MCPToolResultContext,
    MCPToolResultResolver,
)

from lkap_agent.tools._http_safety import guarded_transport
from lkap_agent.tools.execution import guard_tool_output
from lkap_agent.tools.untrusted import fence

TransportFactory = Callable[[], httpx.AsyncBaseTransport]


def fenced_result_resolver(server: str) -> MCPToolResultResolver:
    """A `tool_result_resolver` that fences each result as ``mcp:<server>`` (S5-6)."""
    source = f"mcp:{server}"

    def _resolve(ctx: MCPToolResultContext) -> str:
        content = ctx.result.content
        if len(content) == 1:
            text = content[0].model_dump_json()
        elif content:
            text = json.dumps([item.model_dump() for item in content])
        else:
            raise ToolError(f"Tool '{ctx.tool_name}' completed without producing a result.")
        return fence(text, source=source)

    return _resolve


class GuardedMCPServerHTTP(MCPServerHTTP):
    """An MCP HTTP server whose client never follows redirects and never reaches a private address."""

    def __init__(
        self,
        url: str,
        transport_type: Literal["sse", "streamable_http"] | None = None,
        allowed_tools: list[str] | None = None,
        headers: dict[str, Any] | None = None,
        timeout: float = 5,
        sse_read_timeout: float = 60 * 5,
        client_session_timeout_seconds: float = 5,
        *,
        transport_factory: TransportFactory = guarded_transport,
        server_name: str = "server",
    ) -> None:
        """Same arguments as `MCPServerHTTP`, plus the transport seam and the fence's source.

        Args:
            url: The MCP endpoint (already checked by `check_url_public`).
            transport_type: `"streamable_http"` or `"sse"`.
            allowed_tools: Tool names to expose; `None` exposes all.
            headers: Static request headers (secrets already substituted by the api).
            timeout: Connect timeout in seconds.
            sse_read_timeout: Read timeout for the event stream in seconds.
            client_session_timeout_seconds: MCP session request timeout.
            transport_factory: Builds each client's transport; tests pass a fake.
            server_name: The definition's name; results are fenced as ``mcp:<server_name>``.
        """
        super().__init__(
            url=url,
            transport_type=transport_type,
            allowed_tools=allowed_tools,
            headers=headers,
            timeout=timeout,
            sse_read_timeout=sse_read_timeout,
            client_session_timeout_seconds=client_session_timeout_seconds,
            tool_result_resolver=fenced_result_resolver(server_name),
        )
        self._transport_factory = transport_factory

    def _create_http_client(
        self,
        headers: dict[str, Any] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        """The base client, with `follow_redirects=False` and the guarded transport."""
        kwargs: dict[str, Any] = {
            "follow_redirects": False,
            "timeout": timeout
            if timeout is not None
            else httpx.Timeout(self._timeout, read=self._sse_read_timeout),
            "headers": headers if headers is not None else self._headers,
            "transport": self._transport_factory(),
        }
        if auth is not None:
            kwargs["auth"] = auth
        self._http_client = httpx.AsyncClient(**kwargs)
        return self._http_client

    def _make_function_tool(
        self,
        name: str,
        description: str | None,
        input_schema: dict[str, Any],
        meta: dict[str, Any] | None,
        *,
        options: MCPToolOptions,
    ) -> Any:
        """The SDK's tool, its result passed through the ``tool_output`` guardrail (V6-21, S6-3).

        The returned tool always takes ``(ctx, raw_arguments)``; it forwards ``ctx`` to the
        SDK's function only when that one takes it (``report_progress``).
        """
        tool = super()._make_function_tool(name, description, input_schema, meta, options=options)
        impl = tool._func
        takes_ctx = bool(options.get("report_progress"))

        async def _guarded(ctx: RunContext[Any], raw_arguments: dict[str, Any]) -> Any:
            result = await (impl(ctx, raw_arguments) if takes_ctx else impl(raw_arguments))
            return await guard_tool_output(ctx, result)

        return function_tool(
            _guarded,
            raw_schema=dict(tool.info.raw_schema),
            flags=options["flags"],
            on_duplicate=options["on_duplicate"],
            duplicate_scope=options["duplicate_scope"],
        )
