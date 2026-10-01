"""`pin_frame` built-in tool (docs/ARCHITECTURE.md §7.3, §8).

Capability-gated: `build_builtin_tools` only registers this when the agent
has `capabilities.camera` or `capabilities.screen_share` (ARCHITECTURE §8),
since it has nothing to encode otherwise.

V5-19: a pinned frame is also stored as a session file (`UiChannel.store_asset`,
kind `frame`), so the console keeps it after the call and `describe_asset` can
read it; when storing fails the frame is shown exactly as before.

V6-12 (D-V6-16, B9 annotation): on a panel with a `canvas` block the tool takes
`canvas_block_id` and also puts the pinned frame behind that drawing board, so
`draw_on_canvas` can circle what matters ("annotate" = `pin_frame` + `draw_on_canvas`).
Without a canvas the tool and its schema are exactly as before.
"""

from __future__ import annotations

import json
import time
from typing import Any

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.ui_protocol import CANVAS_ASSET_BACKGROUND_PREFIX, UiPatchOp
from packs.base import PackSessionContext

from lkap_agent.ui.blocks import block_ids_of_type, describe_blocks, session_block_specs

#: "the latest fresh frame (<=12 s old)" — docs/ARCHITECTURE.md §7.3.
PIN_FRAME_MAX_AGE_S = 12.0


async def _pin(ctx: PackSessionContext, caption: str, confirmed: bool, kind: str) -> tuple[str, str] | None:
    """Store and show the freshest frame; `(asset_id, source)`, or `None` when there is none."""
    result = await ctx.frames.latest_jpeg(max_age_s=PIN_FRAME_MAX_AGE_S)
    if result is None:
        return None
    jpeg_bytes, snapshot = result
    meta = {"source": snapshot.source, "confirmed": str(confirmed).lower()}
    # V5-19: the frame is also stored as a session file (best effort); the panel still
    # renders it from the bytes on `lkap.ui.asset`, stored or not.
    store = getattr(ctx.ui, "store_asset", None)
    if callable(store):
        asset_id = await store(jpeg_bytes, "image/jpeg", kind, caption=caption, meta=meta)
    else:
        asset_id = await ctx.ui.push_asset(jpeg_bytes, "image/jpeg", kind, caption=caption, meta=meta)
    return str(asset_id), snapshot.source


def build_pin_frame_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `pin_frame` tool bound to `ctx` (with `canvas_block_id` when the panel has a canvas)."""
    specs = session_block_specs(ctx.ui, ctx.config.panel)
    if block_ids_of_type(specs, "canvas"):
        return _build_with_canvas(ctx, describe_blocks(specs, ["canvas"]))

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
        pinned = await _pin(ctx, caption, confirmed, kind)
        if pinned is None:
            return "No fresh camera or screen frame is available to pin right now."
        asset_id, source = pinned
        ctx.log.debug(
            "builtin_tool.pin_frame",
            call_id=context.function_call.call_id,
            asset_id=asset_id,
            source=source,
        )
        return json.dumps({"pinned": True, "asset_id": asset_id})

    return pin_frame


def _build_with_canvas(ctx: PackSessionContext, boards: str) -> FunctionTool[..., Any]:
    """The V6-12 variant: the frame may also go behind a drawing board."""

    async def pin_frame(
        context: RunContext[Any],
        caption: str,
        confirmed: bool = False,
        kind: str = "photo",
        canvas_block_id: str = "",
    ) -> str:
        """Pin the current camera or screen frame to the shared notebook.

        Args:
            caption: A short caption describing the pinned frame.
            confirmed: Whether the caller has confirmed this frame is the right one.
            kind: A pack-defined asset kind (default "photo").
            canvas_block_id: Optional: a drawing board to put the frame behind, to mark it up next.
        """
        board = canvas_block_id.strip()
        if board and board not in block_ids_of_type(session_block_specs(ctx.ui, ctx.config.panel), "canvas"):
            raise ToolError(f"There is no drawing board {board!r}. Boards: {boards}.")
        pinned = await _pin(ctx, caption, confirmed, kind)
        if pinned is None:
            return "No fresh camera or screen frame is available to pin right now."
        asset_id, source = pinned
        answer: dict[str, Any] = {"pinned": True, "asset_id": asset_id}
        if board:
            await ctx.ui.patch_block(
                board,
                [
                    UiPatchOp(
                        op="set", path="/background", value=f"{CANVAS_ASSET_BACKGROUND_PREFIX}{asset_id}"
                    ),
                    UiPatchOp(op="set", path="/updated_at", value=time.time()),
                ],
            )
            answer["canvas_block_id"] = board
        ctx.log.debug(
            "builtin_tool.pin_frame",
            call_id=context.function_call.call_id,
            asset_id=asset_id,
            source=source,
            canvas_block_id=board or None,
        )
        return json.dumps(answer)

    return function_tool(
        pin_frame,
        description=(
            "Pin the current camera or screen frame to the shared notebook; with canvas_block_id it "
            "also goes behind that drawing board so you can mark it up with draw_on_canvas. "
            f"Boards: {boards}."
        ),
    )
