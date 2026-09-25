"""`set_details` built-in tool (V5-08, B2): fill in the key-value card.

Each item is upserted by `key` (the tree-op `upsert`), so a confirmed fact
replaces its row in place. The label and type come from the row already on
the card (seeded from `config.fields`) unless the model gives a label; a new
key gets a label made from the key. A value of a `number` or `money` row is
stored as a number when it reads as one; an empty value clears the row's value.
Values are always passed as text: a `str | float` parameter is a union Gemini's
function declarations handle poorly.
"""

from __future__ import annotations

import time
from typing import Any

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.ui_protocol import UiPatchOp
from packs.base import PackSessionContext
from pydantic import BaseModel, Field, ValidationError

from lkap_agent.ui.blocks import describe_blocks, pick_block, session_block_specs

__all__ = ["DetailIn", "build_set_details_tool", "detail_item"]

_NUMERIC_TYPES = frozenset({"number", "money"})


class DetailIn(BaseModel):
    """One fact for the card."""

    key: str = Field(description="Machine key of the row, e.g. claim_no.")
    value: str = Field(description="The value as text; empty clears it.")
    label: str = Field(default="", description="What the caller sees; leave empty to keep the row's label.")


def _label_of(key: str) -> str:
    return key.replace("_", " ").strip().capitalize() or key


def _value_of(raw: str, value_type: str) -> str | float | None:
    text = raw.strip()
    if not text:
        return None
    if value_type in _NUMERIC_TYPES:
        try:
            return float(text.replace(",", ""))
        except ValueError:
            return text
    return text


def detail_item(item: DetailIn, existing: dict[str, Any] | None, *, now: float) -> dict[str, Any]:
    """The `DetailsItem` JSON for `item`, keeping the existing row's label, type and tone."""
    current = existing or {}
    value_type = str(current.get("type") or "string")
    row: dict[str, Any] = {
        "key": item.key,
        "label": item.label.strip() or str(current.get("label") or _label_of(item.key)),
        "value": _value_of(item.value, value_type),
        "type": value_type,
        "updated_at": now,
    }
    if current.get("tone") is not None:
        row["tone"] = current["tone"]
    return row


def build_set_details_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `set_details` tool bound to `ctx`."""
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["details"])

    async def set_details(context: RunContext[Any], items: list[DetailIn], block_id: str = "") -> str:
        """Add or update facts on the details card in the caller's side panel.

        Args:
            items: The facts to add or change, each by key.
            block_id: The details block; leave empty when there is only one.
        """
        specs = session_block_specs(ctx.ui, ctx.config.panel)
        try:
            target = pick_block(specs, "details", block_id or None)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        if not items:
            raise ToolError("Pass at least one item.")
        keys = [i.key.strip() for i in items]
        if not all(keys) or len(set(keys)) != len(keys):
            raise ToolError("Item keys must be unique and non-empty.")
        state = ctx.ui.state.blocks.get(target) or {}
        raw_rows = state.get("items")
        rows: list[Any] = raw_rows if isinstance(raw_rows, list) else []
        by_key = {r.get("key"): r for r in rows if isinstance(r, dict)}
        now = time.time()
        ops = [
            UiPatchOp(
                op="upsert",
                path="/items",
                value=detail_item(item.model_copy(update={"key": key}), by_key.get(key), now=now),
                key=key,
            )
            for item, key in zip(items, keys, strict=True)
        ]
        try:
            await ctx.ui.patch_block(target, ops)
        except ValidationError as exc:
            raise ToolError(f"Those details do not fit the card: {exc.errors()[0]['msg']}.") from exc
        ctx.log.debug(
            "builtin_tool.set_details", call_id=context.function_call.call_id, block_id=target, keys=keys
        )
        return f"Updated {len(keys)} detail{'s' if len(keys) != 1 else ''} on {target}."

    return function_tool(
        set_details,
        description=(
            "Add or update facts on the details card in the caller's side panel, such as a claim "
            "number or a date. Update it quietly as facts are confirmed; do not read the card aloud. "
            f"Details blocks: {inventory}."
        ),
    )
