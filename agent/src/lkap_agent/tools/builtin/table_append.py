"""`table_append` built-in tool (CONTRACTS-V2 §4.4): add a row to a `table` block.

The row is a JSON-object string (see `update_block` for why not a `dict`).
Every row gets an `id` (kept if the model supplied one) because
`TableBlockState.selected_row`, keyed `upsert`/`remove` ops and the browser's
reducer all match rows by `id`. Keys that no column covers yet add a column
in the same patch, so the table never hides data the agent wrote.
"""

from __future__ import annotations

import uuid
from typing import Any

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.ui_protocol import TableColumn, UiPatchOp
from packs.base import PackSessionContext
from pydantic import ValidationError

from lkap_agent.tools.builtin.update_block import parse_json_object
from lkap_agent.ui.blocks import describe_blocks, pick_block, session_block_specs

__all__ = ["build_table_append_tool", "infer_column"]


def infer_column(key: str, value: Any) -> TableColumn:
    """A column for a key the table has not seen: label from the key, type from the value."""
    label = key.replace("_", " ").strip().capitalize() or key
    if isinstance(value, bool):
        kind = "boolean"
    elif isinstance(value, int | float):
        kind = "number"
    else:
        kind = "string"
    return TableColumn(key=key, label=label, type=kind)


def build_table_append_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `table_append` tool bound to `ctx`."""
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["table"])

    async def table_append(context: RunContext[Any], block_id: str, row: str) -> str:
        """Add one row to a table in the user's side panel.

        Args:
            block_id: The id of the table block.
            row: A JSON object mapping column keys to cell values, e.g. {"item": "Tyre", "cost": 120}.
        """
        specs = session_block_specs(ctx.ui, ctx.config.panel)
        try:
            target = pick_block(specs, "table", block_id)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        values = parse_json_object(row, "row")
        if not values:
            raise ToolError("row is empty; pass at least one cell.")
        values.setdefault("id", uuid.uuid4().hex[:12])

        current = ctx.ui.state.blocks.get(target) or {}
        columns = [c for c in current.get("columns", []) if isinstance(c, dict)]
        known = {c.get("key") for c in columns}
        added = [
            infer_column(k, v).model_dump(mode="json")
            for k, v in values.items()
            if k != "id" and k not in known
        ]

        ops: list[UiPatchOp] = []
        if added:
            ops.append(UiPatchOp(op="set", path="/columns", value=[*columns, *added]))
        ops.append(UiPatchOp(op="append", path="/rows", value=values))
        try:
            await ctx.ui.patch_block(target, ops)
        except ValidationError as exc:
            raise ToolError(f"That row does not fit the table: {exc.errors()[0]['msg']}.") from exc
        ctx.log.debug(
            "builtin_tool.table_append",
            call_id=context.function_call.call_id,
            block_id=target,
            row_id=values["id"],
            new_columns=[c["key"] for c in added],
        )
        return f"Row added to {target}."

    return function_tool(
        table_append,
        description=f"Add one row to a table in the user's side panel. Tables: {inventory}.",
    )
