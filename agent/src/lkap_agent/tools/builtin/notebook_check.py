"""`notebook_check` built-in tool (V6-08, D-V6-15): tick or untick an item of a notebook checklist.

Registered with `notebook_write` for a panel with a `notebook` block. The item is upserted
in place in the section's `items`; the agent's own tick clears a caller-edit marker. A
write: blocking, instant, never in the background; it answers nothing on a realtime model
(as `notebook_write`).
"""

from __future__ import annotations

import time
from typing import Any

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.ui_protocol import UiPatchOp
from packs.base import PackSessionContext
from pydantic import ValidationError

from lkap_agent.ui.blocks import notebook_section_state, session_block_specs

from .notebook_write import describe_notebooks, resolve_notebook_section
from .set_checklist import MAX_HINT_CHARS, QUIET_MODES

__all__ = ["build_notebook_check_tool"]


def build_notebook_check_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `notebook_check` tool bound to `ctx`."""
    inventory = describe_notebooks(session_block_specs(ctx.ui, ctx.config.panel))

    async def notebook_check(
        context: RunContext[Any],
        section_id: str,
        item_id: str,
        done: bool = True,
        hint: str = "",
        block_id: str = "",
    ) -> str | None:
        """Tick (or untick) one item of a checklist section of the notebook.

        Args:
            section_id: The checklist section.
            item_id: The item's id.
            done: True when it is provided, false to untick it.
            hint: An optional short hint to show under the item; leave empty to keep the current one.
            block_id: The notebook block; leave empty when there is only one.
        """
        target, section, state = resolve_notebook_section(ctx, block_id, section_id)
        if section.kind != "checklist":
            raise ToolError(f"Section {section.id} is not a checklist; use notebook_write for it.")
        content = notebook_section_state(state, section)
        items = [i for i in content.get("items") or [] if isinstance(i, dict)]
        item = next((i for i in items if i.get("id") == item_id), None)
        if item is None:
            ids = (
                ", ".join(str(i.get("id")) for i in items)
                or "none (write the items with notebook_write first)"
            )
            raise ToolError(f"Unknown item {item_id!r} in {section.id}; the items are: {ids}.")
        updated = {k: v for k, v in item.items() if k != "edited_by"}
        updated["done"] = done
        cleaned = " ".join(hint.split())[:MAX_HINT_CHARS]
        if cleaned:
            updated["hint"] = cleaned
        ops = [
            UiPatchOp(op="upsert", path=f"/sections/{section.id}/items", value=updated, key=item_id),
            UiPatchOp(op="set", path="/updated_at", value=time.time()),
        ]
        try:
            await ctx.ui.patch_block(target, ops)
        except ValidationError as exc:
            raise ToolError(f"That does not fit the notebook: {exc.errors()[0]['msg']}.") from exc
        ctx.log.debug(
            "builtin_tool.notebook_check",
            call_id=context.function_call.call_id,
            block_id=target,
            section_id=section.id,
            item_id=item_id,
            done=done,
        )
        if ctx.pipeline_mode in QUIET_MODES:
            return None
        return f"{'Ticked' if done else 'Unticked'} {item_id} in {section.id}."

    return function_tool(
        notebook_check,
        description=(
            "Tick an item of a checklist section of the notebook once the caller has provided it "
            f"(or untick it). Do it quietly; do not read the list aloud. Notebook sections: {inventory}."
        ),
    )
