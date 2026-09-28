"""`check_item` built-in tool (V6-06, D-V6-19): tick or untick one checklist item.

Registered with `set_checklist` for a panel with a `checklist` block. The item
is upserted in place on the envelope checklist; the agent's own tick clears a
caller-edit marker. A write: blocking, instant, never in the background; it
answers nothing on a realtime model (as `set_checklist`).
"""

from __future__ import annotations

from typing import Any

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.ui_protocol import UiPatchOp
from packs.base import PackSessionContext

from .set_checklist import MAX_HINT_CHARS, QUIET_MODES

__all__ = ["build_check_item_tool"]


def build_check_item_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `check_item` tool bound to `ctx`."""

    async def check_item(
        context: RunContext[Any], item_id: str, done: bool = True, hint: str = ""
    ) -> str | None:
        """Tick (or untick) one item of the checklist.

        Args:
            item_id: The item's id.
            done: True when it is provided, false to untick it.
            hint: An optional short hint to show under the item; leave empty to keep the current one.
        """
        items = list(ctx.ui.state.checklist)
        item = next((i for i in items if i.id == item_id), None)
        if item is None:
            ids = ", ".join(i.id for i in items) or "none (call set_checklist first)"
            raise ToolError(f"Unknown checklist item {item_id!r}; the items are: {ids}.")
        update: dict[str, Any] = {"done": done, "edited_by": None}
        cleaned = " ".join(hint.split())[:MAX_HINT_CHARS]
        if cleaned:
            update["hint"] = cleaned
        updated = item.model_copy(update=update)
        await ctx.ui.patch([UiPatchOp(op="upsert", path="/checklist", value=updated, key=item.id)])
        ctx.log.debug(
            "builtin_tool.check_item", call_id=context.function_call.call_id, item_id=item.id, done=done
        )
        if ctx.pipeline_mode in QUIET_MODES:
            return None
        return f"{'Ticked' if done else 'Unticked'} {item.id}."

    return function_tool(
        check_item,
        description=(
            "Tick an item of the caller's checklist once they have provided it (or untick it). "
            "Do it quietly; do not read the checklist aloud."
        ),
    )
