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

from dataclasses import replace
from typing import Any, Literal

import httpx
from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from packs.base import PackSessionContext

from lkap_agent.tools._http_safety import (
    HttpToolSecurityError,
    check_url_allowed,
    guarded_transport,
    read_bounded,
)
from lkap_agent.tools.execution import (
    ResolvedExecution,
    ToolPolicy,
    attach_policy,
    blocking_policy,
    run_with_policy,
    tool_flags,
)
from lkap_agent.tools.untrusted import fence

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_RESULT_CHARS = 4000


def build_http_request_tool(
    ctx: PackSessionContext,
    *,
    platform_allowed_hosts: list[str] | None = None,
    user_agent: str | None = None,
    execution: ResolvedExecution | None = None,
) -> FunctionTool[..., Any]:
    """Build the generic `http_request` tool bound to `ctx`.

    Args:
        ctx: The session's `PackSessionContext`.
        platform_allowed_hosts: `LKAP_HTTP_TOOL_ALLOWED_HOSTS`, injected so
            this module never reads `Settings` directly (keeps it
            test-friendly, per docs/CONTRACTS.md §3).
        user_agent: `LKAP_HTTP_TOOL_USER_AGENT`, sent as `User-Agent` (asks #29).
        execution: The tool's execution policy (docs/v4/BACKGROUND-TOOLS.md); `None`
            runs it blocking. It is a read tool only per call: a GET runs under the
            policy, any other method always runs blocking (D-V4-32).
    """
    headers = {"User-Agent": user_agent} if user_agent else None
    policy = execution or blocking_policy("http_request")
    write_policy = replace(policy, mode="blocking", fillers=())
    flags, on_duplicate, duplicate_scope = tool_flags(policy)

    @function_tool(flags=flags, on_duplicate=on_duplicate, duplicate_scope=duplicate_scope)
    async def http_request(
        context: RunContext[Any], method: HttpMethod, url: str, body: str | None = None
    ) -> str:
        """Make an outbound HTTP call to an allowlisted host.

        Args:
            method: HTTP method to use.
            url: Full request URL; its host must be on the platform's outbound allowlist.
            body: Optional raw request body (sent as-is for POST/PUT/PATCH).
        """
        result: str = await run_with_policy(
            context,
            policy if method == "GET" else write_policy,
            lambda: _request(context, method, url, body),
        )
        return result

    async def _request(context: RunContext[Any], method: HttpMethod, url: str, body: str | None) -> str:
        try:
            check_url_allowed(url, tool_allowed_hosts=None, platform_allowed_hosts=platform_allowed_hosts)
        except HttpToolSecurityError as exc:
            raise ToolError(str(exc)) from exc

        try:
            async with (
                httpx.AsyncClient(
                    follow_redirects=False, timeout=DEFAULT_TIMEOUT_S, transport=guarded_transport()
                ) as client,
                client.stream(method, url, content=body, headers=headers) as response,
            ):
                # V5-27: a bounded read; the worker never holds an unbounded body.
                raw, cut = await read_bounded(response)
        except httpx.HTTPError as exc:
            raise ToolError(f"HTTP request failed ({type(exc).__name__})") from exc

        ctx.log.debug(
            "builtin_tool.http_request",
            call_id=context.function_call.call_id,
            method=method,
            host=httpx.URL(url).host,
            status=response.status_code,
            truncated=cut,
        )
        # V5-27 (S5-6, R-V5-15): a third-party body is data, never instructions.
        text = raw.decode(response.encoding or "utf-8", errors="replace")
        return fence(text, source="http:http_request", max_chars=DEFAULT_MAX_RESULT_CHARS)

    return attach_policy(http_request, ToolPolicy(resolved=policy))
