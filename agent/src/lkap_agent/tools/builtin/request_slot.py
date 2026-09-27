"""`request_slot` built-in tool (V5-43, B8): offer bookable times and wait for the caller's pick.

The agent fetches availability with its own tools (a calendar integration),
then calls this with the times as ISO 8601 with their UTC offset. The tool
writes them into a ``slots`` block and waits on ``UiChannel.request_block``
(V5-02) like ``request_choice``: the caller taps a time, or says one and the
model calls ``resolve_slot``. The answer is always the block's own slot (the
browser sends only its id), returned with its start and end.

* **cascaded**: blocking, up to :data:`SLOT_TIMEOUT_S`.
* **realtime / half_cascade**: returns ``None`` at once; the pick arrives as an
  urgent background result (``PlatformAgent`` keeps the model silent after the
  call, R-V5-1).
* **phone**: nothing is shown; ``{"channel": "voice_only"}`` and the model
  offers two or three times out loud. **text chat**: the times come back for
  the model to list in its reply.

The times are shown in the caller's timezone (``timezone_mode="caller"``, the
default) or the business timezone (``"agent"``).
"""

from __future__ import annotations

import json
from typing import Any, Final

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.ui_protocol import MAX_SLOTS, TimeSlot, UiPatchOp
from packs.base import PackSessionContext
from pydantic import BaseModel, Field, ValidationError

from lkap_agent.locale import fallback_locale, session_locale
from lkap_agent.tools.builtin.request_choice import VIA_VOICE
from lkap_agent.tools.builtin.request_form import BACKGROUND_FORM_MODES
from lkap_agent.ui.blocks import (
    VOICE_ONLY_CHANNELS,
    describe_blocks,
    find_slot,
    pick_block,
    session_block_specs,
    slot_selection_error,
)

__all__ = ["SLOT_TIMEOUT_S", "SlotIn", "build_request_slot_tool", "slot_answer", "slot_timezone"]

#: How long the caller has to pick a time.
SLOT_TIMEOUT_S: Final[float] = 120.0


class SlotIn(BaseModel):
    """One time the model offers."""

    id: str = Field(description="Short machine id of the slot, e.g. mon_am.")
    start: str = Field(description="Start, ISO 8601 with its UTC offset, e.g. 2026-10-05T09:00:00+01:00.")
    end: str = Field(description="End, ISO 8601 with its UTC offset.")
    label: str = Field(default="", description="Optional short line, e.g. First visit of the day.")


def slot_timezone(ctx: PackSessionContext, config: dict[str, Any]) -> str:
    """The zone a `slots` block shows its times in: the caller's (default) or the business's."""
    locale = session_locale(ctx) or fallback_locale(ctx.config)
    return locale.business_timezone if config.get("timezone_mode") == "agent" else locale.caller_timezone


def slot_answer(state: dict[str, Any], selected: str) -> dict[str, Any] | None:
    """`{selected, start, end, label?}` for the block's own slot `selected`, or `None`."""
    slot = find_slot(state, selected)
    if slot is None:
        return None
    answer: dict[str, Any] = {"selected": selected, "start": slot.get("start"), "end": slot.get("end")}
    if slot.get("label"):
        answer["label"] = slot["label"]
    return answer


def _checked_slots(slots: list[SlotIn]) -> list[dict[str, Any]]:
    if not slots:
        raise ToolError("Offer at least one time.")
    if len(slots) > MAX_SLOTS:
        raise ToolError(f"Offer at most {MAX_SLOTS} times.")
    rows: list[dict[str, Any]] = []
    for slot in slots:
        try:
            model = TimeSlot(
                id=slot.id.strip(),
                start=slot.start.strip(),
                end=slot.end.strip(),
                label=slot.label.strip() or None,
            )
        except ValidationError as exc:
            raise ToolError(f"The time {slot.id!r} is not valid: {exc.errors()[0]['msg']}.") from exc
        rows.append(model.model_dump(mode="json", exclude_none=True))
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ToolError("Slot ids must be unique.")
    return rows


def build_request_slot_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `request_slot` tool bound to `ctx`."""
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["slots"])

    async def wait_for_slot(target: str) -> dict[str, Any] | None:
        """Wait for the caller's pick; the slot's `{selected, start, end, via?}` or `None`."""
        values = await ctx.ui.request_block(target, timeout_s=SLOT_TIMEOUT_S)
        if values is None:
            return None
        state = ctx.ui.state.blocks.get(target) or {}
        selected = values.get("selected")
        if slot_selection_error(state, selected) is not None or not isinstance(selected, str):
            ctx.log.warning("builtin_tool.request_slot.bad_answer", block_id=target)
            return None
        answer = slot_answer(state, selected)
        if answer is not None and values.get("via") == VIA_VOICE:
            answer["via"] = VIA_VOICE
        return answer

    async def request_slot(
        context: RunContext[Any], prompt: str, slots: list[SlotIn], block_id: str = ""
    ) -> str | None:
        """Show the caller times they can book and wait for their pick.

        Args:
            prompt: The question, e.g. When can our inspector come round?
            slots: The free times, with ISO 8601 start and end including the UTC offset.
            block_id: The times block; leave empty when there is only one.
        """
        specs = session_block_specs(ctx.ui, ctx.config.panel)
        try:
            target = pick_block(specs, "slots", block_id or None)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        rows = _checked_slots(slots)
        question = " ".join(prompt.split())
        if not question:
            raise ToolError("Give the question to show above the times.")
        channel = getattr(ctx, "channel", "web")
        call_id = context.function_call.call_id
        if channel in VOICE_ONLY_CHANNELS:
            ctx.log.debug("builtin_tool.request_slot", call_id=call_id, block_id=target, voice_only=True)
            return json.dumps({"channel": "voice_only"})
        if channel == "text":
            ctx.log.debug("builtin_tool.request_slot", call_id=call_id, block_id=target, text_channel=True)
            listed = "; ".join(f"{r['id']}: {r['start']} to {r['end']}" for r in rows)
            return (
                "Nothing was shown: this is a text chat. "
                f"Ask in the conversation: {question} Times: {listed}."
            )

        config = next((s.config for s in specs if s.id == target), {})
        content: dict[str, Any] = {
            "prompt": question,
            "slots": rows,
            "selected": None,
            "timezone": slot_timezone(ctx, config),
        }
        try:
            await ctx.ui.patch_block(
                target, [UiPatchOp(op="set", path=f"/{k}", value=v) for k, v in content.items()]
            )
        except ValidationError as exc:
            raise ToolError(f"Those times do not fit the block: {exc.errors()[0]['msg']}.") from exc
        background = ctx.pipeline_mode in BACKGROUND_FORM_MODES
        ctx.log.debug(
            "builtin_tool.request_slot",
            call_id=call_id,
            block_id=target,
            slots=len(rows),
            background=background,
        )

        if background:
            ctx.background.submit(
                name="request_slot",
                coro=wait_for_slot(target),
                urgent=lambda answer: answer is not None and answer.get("via") != VIA_VOICE,
                urgent_instructions=lambda answer: (
                    f"The caller just picked the time {answer['start']} to {answer['end']} on screen. "
                    "Confirm it briefly and continue."
                ),
                routine_note=lambda answer: (
                    None
                    if answer is not None
                    else f"The caller did not pick a time in the {target} block on screen."
                ),
                call_id=call_id,
            )
            return None

        answer = await wait_for_slot(target)
        if answer is None:
            return (
                "The caller did not pick a time on screen (they spoke, closed it or it timed out). "
                "If they said a time out loud, call resolve_slot; otherwise ask again."
            )
        return json.dumps(answer)

    return function_tool(
        request_slot,
        description=(
            "Show the caller times they can book (fetched with your calendar tools) and wait for their pick; "
            "returns the slot's start and end. Read at most three times aloud and say they can also tap one. "
            f"If the caller says a time instead, call resolve_slot. Times blocks: {inventory}."
        ),
    )
