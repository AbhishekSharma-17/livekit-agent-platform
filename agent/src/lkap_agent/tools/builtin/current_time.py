"""`current_time` built-in tool (docs/ARCHITECTURE.md §7.3, R-V5-10).

Answers in the caller's timezone by default (the session's
:class:`~lkap_agent.locale.SessionLocale`), and always includes the business
timezone's block, so "are you open now?" needs one call. Instant and never
backgrounded (`NEVER_BACKGROUND_TOOLS`).
"""

from __future__ import annotations

import json
from typing import Any

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from packs.base import PackSessionContext

from lkap_agent.locale import fallback_locale, moment_payload, session_locale


def build_current_time_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `current_time` tool bound to `ctx`."""

    @function_tool
    async def current_time(context: RunContext[Any], timezone: str | None = None) -> str:
        """Return the current date and time in the caller's timezone, with the business's time too.

        Args:
            timezone: An IANA timezone such as "America/New_York" to answer in instead of the caller's.
        """
        locale = session_locale(ctx) or fallback_locale(ctx.config)
        zone = locale.resolve_zone(timezone)
        if zone is None:
            raise ToolError(f"Unknown timezone '{timezone}'. Use an IANA name such as 'Europe/London'.")
        now = locale.clock()
        payload: dict[str, Any] = moment_payload(now, zone)
        if zone != locale.caller_timezone:
            payload["caller"] = moment_payload(now, locale.caller_timezone)
        payload["business"] = moment_payload(now, locale.business_timezone)
        return json.dumps(payload)

    return current_time
