"""`resolve_slot` built-in tool (V5-43, B8): record a time the caller said out loud.

The partner of ``request_slot`` (the ``resolve_choice`` pattern): the caller
answers by voice, the model names the slot, and the block shows it
(``status: "submitted"``, ``selected``). A pending request resolves with the
answer (marked ``via: "voice"`` so a realtime background waiter stays quiet).
With the block's ``allow_custom`` on, a time that was not offered may be
recorded too (``start`` and ``end``, ISO 8601 with the UTC offset): it is added
to the block's slots as ``custom`` first.
"""

from __future__ import annotations

import json
import time
from typing import Any, Final

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.ui_protocol import MAX_SLOTS, TimeSlot, UiPatchOp
from packs.base import PackSessionContext
from pydantic import ValidationError

from lkap_agent.tools.builtin.request_choice import VIA_VOICE
from lkap_agent.tools.builtin.request_slot import slot_answer
from lkap_agent.ui.blocks import describe_blocks, find_slot, pick_block, session_block_specs

__all__ = ["CUSTOM_SLOT_ID", "build_resolve_slot_tool"]

#: The id a caller's own time gets on the block (``allow_custom``).
CUSTOM_SLOT_ID: Final[str] = "custom"


def build_resolve_slot_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `resolve_slot` tool bound to `ctx`."""
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["slots"])

    async def resolve_slot(
        context: RunContext[Any], slot_id: str = "", start: str = "", end: str = "", block_id: str = ""
    ) -> str:
        """Record the time the caller chose out loud, so their screen shows it.

        Args:
            slot_id: The id of the offered time the caller chose.
            start: Only for a time that was not offered (when allowed): ISO 8601 start with UTC offset.
            end: Only with start: ISO 8601 end with UTC offset.
            block_id: The times block; leave empty when there is only one.
        """
        specs = session_block_specs(ctx.ui, ctx.config.panel)
        try:
            target = pick_block(specs, "slots", block_id or None)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        config = next((s.config for s in specs if s.id == target), {})
        state = ctx.ui.state.blocks.get(target) or {}
        chosen = slot_id.strip()
        if not chosen:
            if not (start.strip() and end.strip()):
                raise ToolError("Pass the slot_id the caller chose (or start and end for another time).")
            if config.get("allow_custom") is not True:
                raise ToolError(
                    "Only the offered times can be booked here. Ask the caller to pick one of them."
                )
            try:
                custom = TimeSlot(id=CUSTOM_SLOT_ID, start=start.strip(), end=end.strip(), label="Your time")
            except ValidationError as exc:
                raise ToolError(f"That time is not valid: {exc.errors()[0]['msg']}.") from exc
            slots = [
                s for s in state.get("slots") or [] if isinstance(s, dict) and s.get("id") != CUSTOM_SLOT_ID
            ]
            if len(slots) >= MAX_SLOTS:
                slots = slots[: MAX_SLOTS - 1]
            await ctx.ui.patch_block(
                target, [UiPatchOp(op="set", path="/slots", value=[*slots, custom.model_dump(mode="json")])]
            )
            state = ctx.ui.state.blocks.get(target) or {}
            chosen = CUSTOM_SLOT_ID
        elif find_slot(state, chosen) is None:
            raise ToolError(f"Unknown slot {chosen!r}. Use one of the ids shown with request_slot.")
        submit = getattr(ctx.ui, "submit_block", None)
        resolved = False
        if callable(submit):
            resolved = bool(await submit(target, {"selected": chosen, "via": VIA_VOICE}))
        else:
            await ctx.ui.patch_block(
                target,
                [
                    UiPatchOp(op="set", path="/selected", value=chosen),
                    UiPatchOp(op="set", path="/status", value="submitted"),
                    UiPatchOp(op="set", path="/submitted_at", value=time.time()),
                ],
            )
        answer = slot_answer(ctx.ui.state.blocks.get(target) or {}, chosen)
        ctx.log.debug(
            "builtin_tool.resolve_slot",
            call_id=context.function_call.call_id,
            block_id=target,
            selected=chosen,
            resolved_request=resolved,
        )
        return json.dumps({"recorded": True, **(answer or {"selected": chosen})})

    return function_tool(
        resolve_slot,
        description=(
            "Record the time the caller chose out loud for times shown with request_slot, so their screen "
            f"shows it. Times blocks: {inventory}."
        ),
    )
