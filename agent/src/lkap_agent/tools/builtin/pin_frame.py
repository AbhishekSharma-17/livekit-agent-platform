"""`pin_frame` built-in tool (docs/ARCHITECTURE.md §7.3, §8).

Capability-gated: `build_builtin_tools` only registers this when the agent
has `capabilities.camera` or `capabilities.screen_share` (ARCHITECTURE §8),
since it has nothing to encode otherwise.
"""

from __future__ import annotations

import json
from typing import Any

from livekit.agents import FunctionTool, RunContext, function_tool
from packs.base import PackSessionContext

#: "the latest fresh frame (<=12 s old)" — docs/ARCHITECTURE.md §7.3.
PIN_FRAME_MAX_AGE_S = 12.0


def build_pin_frame_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `pin_frame` tool bound to `ctx`."""

    @function_tool
    async def pin_frame(
        context: RunContext[Any], caption: str, confirmed: bool = False, kind: str = "photo"
    ) -> str:
        """Pin the current camera or screen frame to the shared notebook.

        Args:
            caption: A short caption describing the pinned frame.
            confirmed: Whether the caller has confirmed this frame is the right one.
            kind: A pack-defined asset kind (default "photo").
        """
        result = await ctx.frames.latest_jpeg(max_age_s=PIN_FRAME_MAX_AGE_S)
        if result is None:
            return "No fresh camera or screen frame is available to pin right now."

        jpeg_bytes, snapshot = result
        asset_id = await ctx.ui.push_asset(
            jpeg_bytes,
            "image/jpeg",
            kind,
            caption=caption,
            meta={"source": snapshot.source, "confirmed": str(confirmed).lower()},
        )
        ctx.log.debug(
            "builtin_tool.pin_frame",
            call_id=context.function_call.call_id,
            asset_id=asset_id,
            source=snapshot.source,
        )
        return json.dumps({"pinned": True, "asset_id": asset_id})

    return pin_frame
