"""`push_note` built-in tool (docs/ARCHITECTURE.md §7.3)."""

from __future__ import annotations

from typing import Any

from livekit.agents import FunctionTool, RunContext, function_tool
from packs.base import PackSessionContext


def build_push_note_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `push_note` tool bound to `ctx`."""

    @function_tool
    async def push_note(context: RunContext[Any], text: str, kind: str = "note") -> str:
        """Add a free-text note to the shared notebook UI.

        Args:
            text: The note text.
            kind: A pack-defined note category (default "note").
        """
        await ctx.ui.add_note(text, kind=kind)
        return "Noted."

    return push_note
