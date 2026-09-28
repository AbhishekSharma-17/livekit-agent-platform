"""`clear_canvas` built-in tool (V6-12, D-V6-16): wipe a drawing board.

Registered for a panel with a `canvas` block. `which="all"` removes the agent's marks and
the caller's strokes, `"shapes"` only the agent's marks, `"strokes"` only what the caller
drew (which also lifts a "board full" state). The background stays (`draw_on_canvas(
background="none")` removes it). A write: blocking, instant, never in the background; it
answers nothing on a realtime model (a half cascade too).
"""

from __future__ import annotations

import time
from typing import Any, Literal

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.ui_protocol import UiPatchOp
from packs.base import PackSessionContext
from pydantic import ValidationError

from lkap_agent.ui.blocks import describe_blocks, session_block_specs

from .draw_on_canvas import resolve_canvas
from .set_checklist import QUIET_MODES

__all__ = ["build_clear_canvas_tool"]

#: What `clear_canvas` removes.
ClearWhich = Literal["all", "shapes", "strokes"]


def build_clear_canvas_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `clear_canvas` tool bound to `ctx`."""
    boards = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["canvas"])

    async def clear_canvas(
        context: RunContext[Any], block_id: str = "", which: ClearWhich = "all"
    ) -> str | None:
        """Wipe the drawing board.

        Args:
            block_id: The drawing board; leave empty when there is only one.
            which: all (everything), shapes (only your marks) or strokes (only what the caller drew).
        """
        target, state = resolve_canvas(ctx, block_id)
        ops: list[UiPatchOp] = []
        if which in ("all", "shapes"):
            ops.append(UiPatchOp(op="set", path="/shapes", value=[]))
        if which in ("all", "strokes"):
            ops.append(UiPatchOp(op="set", path="/strokes", value=[]))
            ops.append(UiPatchOp(op="set", path="/limit_reached", value=False))
        ops.append(UiPatchOp(op="set", path="/updated_at", value=time.time()))
        try:
            await ctx.ui.patch_block(target, ops)
        except ValidationError as exc:  # pragma: no cover - emptying a list always fits
            raise ToolError(f"The board could not be cleared: {exc.errors()[0]['msg']}.") from exc
        ctx.log.debug(
            "builtin_tool.clear_canvas",
            call_id=context.function_call.call_id,
            block_id=target,
            which=which,
            strokes=len(state.get("strokes") or []),
            shapes=len(state.get("shapes") or []),
        )
        if ctx.pipeline_mode in QUIET_MODES:
            return None
        return f"Cleared {target} ({which})."

    return function_tool(
        clear_canvas,
        description=(
            "Wipe the drawing board: everything, only your marks, or only what the caller drew. "
            f"Boards: {boards}."
        ),
    )
