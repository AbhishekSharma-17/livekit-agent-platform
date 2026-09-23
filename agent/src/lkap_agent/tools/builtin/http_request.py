"""`http_request` built-in tool (docs/ARCHITECTURE.md §7.3).

Only registered when `AgentConfig.tools.http_request_enabled` is true
(`build_builtin_tools` gates on this); every outbound call is still checked
against the platform allowlist (`LKAP_HTTP_TOOL_ALLOWED_HOSTS`) before any
connection is attempted, via the same `_http_safety` guard the declarative
HTTP tools use. There is no per-tool `allowed_hosts` here (this is one
generic tool, not an admin-authored definition), so it only ever succeeds
when a platform allowlist is configured.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx
from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from packs.base import PackSessionContext

from lkap_agent.tools._http_safety import (
    HttpToolSecurityError,
    check_url_allowed,
    guarded_transport,
    truncate,
)

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_RESULT_CHARS = 4000


def build_http_request_tool(
    ctx: PackSessionContext,
    *,
    platform_allowed_hosts: list[str] | None = None,
) -> FunctionTool[..., Any]:
    """Build the generic `http_request` tool bound to `ctx`.

    Args:
        ctx: The session's `PackSessionContext`.
        platform_allowed_hosts: `LKAP_HTTP_TOOL_ALLOWED_HOSTS`, injected so
            this module never reads `Settings` directly (keeps it
            test-friendly, per docs/CONTRACTS.md §3).
    """

    @function_tool
    async def http_request(
        context: RunContext[Any], method: HttpMethod, url: str, body: str | None = None
    ) -> str:
        """Make an outbound HTTP call to an allowlisted host.

        Args:
            method: HTTP method to use.
            url: Full request URL; its host must be on the platform's outbound allowlist.
            body: Optional raw request body (sent as-is for POST/PUT/PATCH).
        """
        try:
            check_url_allowed(url, tool_allowed_hosts=None, platform_allowed_hosts=platform_allowed_hosts)
        except HttpToolSecurityError as exc:
            raise ToolError(str(exc)) from exc

        try:
            async with httpx.AsyncClient(
                follow_redirects=False, timeout=DEFAULT_TIMEOUT_S, transport=guarded_transport()
            ) as client:
                response = await client.request(method, url, content=body)
        except httpx.HTTPError as exc:
            raise ToolError(f"HTTP request failed: {exc}") from exc

        ctx.log.debug(
            "builtin_tool.http_request",
            call_id=context.function_call.call_id,
            method=method,
            host=httpx.URL(url).host,
            status=response.status_code,
        )
        return truncate(response.text, DEFAULT_MAX_RESULT_CHARS)

    return http_request
