"""`convert_time` built-in tool (R-V5-10; moved here from V5-25).

Converts a wall-clock time ("9:00", "2:30 pm") or an ISO date-time from one
timezone to another. ``caller`` and ``business`` name the session's two zones,
so "our 9 am opening in your time" is one call. A bare time is read on today's
date in the source zone (the session clock). Instant and never backgrounded
(`NEVER_BACKGROUND_TOOLS`).
"""

from __future__ import annotations

import json
from typing import Any

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from packs.base import PackSessionContext

from lkap_agent.locale import (
    SessionLocale,
    fallback_locale,
    local_date,
    moment_payload,
    parse_moment,
    session_locale,
)


def _zone(locale: SessionLocale, name: str) -> str:
    zone = locale.resolve_zone(name)
    if zone is None:
        raise ToolError(f"Unknown timezone '{name}'. Use an IANA name, 'caller' or 'business'.")
    return zone


def build_convert_time_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `convert_time` tool bound to `ctx`."""

    @function_tool
    async def convert_time(
        context: RunContext[Any], time: str, from_tz: str = "business", to_tz: str = "caller"
    ) -> str:
        """Convert a time from one timezone to another.

        Args:
            time: A time such as "09:00" or "2:30 pm" (today), or a date and time such as "2026-10-02T14:00".
            from_tz: The timezone the time is in: an IANA name, "caller" or "business".
            to_tz: The timezone to convert to: an IANA name, "caller" or "business".
        """
        locale = session_locale(ctx) or fallback_locale(ctx.config)
        source = _zone(locale, from_tz)
        target = _zone(locale, to_tz)
        moment = parse_moment(time, source, today=local_date(locale.clock(), source))
        if moment is None:
            raise ToolError(
                f"Could not read the time '{time}'. Use '14:30', '2:30 pm' or '2026-10-02T14:30'."
            )
        return json.dumps({"from": moment_payload(moment, source), "to": moment_payload(moment, target)})

    return convert_time
