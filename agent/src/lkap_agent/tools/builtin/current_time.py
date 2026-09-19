"""`current_time` built-in tool (docs/ARCHITECTURE.md §7.3)."""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from livekit.agents import FunctionTool, RunContext, function_tool
from packs.base import PackSessionContext


def build_current_time_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `current_time` tool bound to `ctx`."""

    @function_tool
    async def current_time(context: RunContext[Any]) -> str:
        """Return the current date and time in the agent's configured timezone."""
        tz: tzinfo
        try:
            tz = ZoneInfo(ctx.config.timezone)
        except ZoneInfoNotFoundError:
            tz = UTC
        return datetime.now(tz).isoformat()

    return current_time
