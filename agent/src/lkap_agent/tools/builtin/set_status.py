"""`set_status` built-in tool — the "rubber stamp" (docs/ARCHITECTURE.md §7.3)."""

from __future__ import annotations

from typing import Any

from livekit.agents import FunctionTool, RunContext, function_tool
from lkap_contracts.ui_protocol import Tone
from packs.base import PackSessionContext


def build_set_status_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `set_status` tool bound to `ctx`."""

    @function_tool
    async def set_status(context: RunContext[Any], label: str, tone: Tone = "neutral") -> str:
        """Set the case status stamp shown in the UI.

        Args:
            label: Short status label, e.g. "Under review".
            tone: Visual tone: neutral, info, success, warning, or danger.
        """
        await ctx.ui.set_status(label, tone)
        return f"Status set to {label}."

    return set_status
