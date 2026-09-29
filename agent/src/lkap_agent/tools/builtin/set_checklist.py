"""`set_checklist` built-in tool (V6-06, D-V6-19): write the "still needed" checklist.

Until V6-06 only pack code could drive the envelope checklist
(`UiChannel.set_checklist`); this tool gives it to any agent whose panel has a
`checklist` block. The list replaces the checklist; an item already ticked
keeps its tick unless `keep_done` is false (the caller may have ticked it on
screen). A write: blocking, instant, never in the background. The model
writes it quietly and does not read it aloud; on a realtime model (and a
half cascade) the tool answers nothing, so the model does not stop to speak
about it (the `request_form` pattern, D-W2-9i).
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.agent_config import PipelineMode
from lkap_contracts.ui_protocol import ChecklistItem
from packs.base import PackSessionContext
from pydantic import BaseModel, Field

from lkap_agent.tools.json_args import json_list

__all__ = [
    "CHECKLIST_ID_PATTERN",
    "MAX_CHECKLIST_ITEMS",
    "QUIET_MODES",
    "ChecklistItemIn",
    "build_set_checklist_tool",
    "checklist_summary",
]

#: The most items one checklist holds.
MAX_CHECKLIST_ITEMS: Final[int] = 30
#: The longest item label and hint.
MAX_LABEL_CHARS: Final[int] = 120
MAX_HINT_CHARS: Final[int] = 200
#: Pipelines whose model is a `RealtimeModel`: the panel tools answer nothing there (V6-06).
QUIET_MODES: Final[frozenset[PipelineMode]] = frozenset({"realtime", "half_cascade"})
#: An item id: letters, digits and `_.:-`.
CHECKLIST_ID_PATTERN: Final[str] = r"^[A-Za-z0-9_.:-]{1,64}$"


class ChecklistItemIn(BaseModel):
    """One item the caller still has to provide."""

    id: str = Field(pattern=CHECKLIST_ID_PATTERN, description="A short id, e.g. photo_of_damage.")
    label: str = Field(description="What the caller sees, e.g. 'A photo of the damage'.")
    done: bool = Field(default=False, description="True once it is already provided.")
    blocking: bool = Field(default=False, description="True when the call cannot finish without it.")
    hint: str = Field(default="", description="An optional short hint shown under the item.")


#: JSON text of the items is read too (V6-30, F-2).
ChecklistItemList = Annotated[
    list[ChecklistItemIn], json_list('{"id": "photos", "label": "Photos of the damage", "done": false}')
]


def _clean(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


def checklist_summary(items: list[ChecklistItem]) -> str:
    """ "3 items, 1 done" for a tool answer."""
    done = sum(1 for item in items if item.done)
    return f"{len(items)} item{'s' if len(items) != 1 else ''}, {done} done"


def build_set_checklist_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `set_checklist` tool bound to `ctx`."""

    async def set_checklist(
        context: RunContext[Any], items: ChecklistItemList, keep_done: bool = True
    ) -> str | None:
        """Replace the checklist of what the caller still needs to provide.

        Args:
            items: Every item, in the order to show them.
            keep_done: Keep the tick of an item that is already ticked (default true).
        """
        if len(items) > MAX_CHECKLIST_ITEMS:
            raise ToolError(f"Keep the checklist to {MAX_CHECKLIST_ITEMS} items or fewer.")
        ids = [item.id for item in items]
        if len(set(ids)) != len(ids):
            raise ToolError("Item ids must be unique.")
        current = {item.id: item for item in ctx.ui.state.checklist}
        checklist: list[ChecklistItem] = []
        for item in items:
            label = _clean(item.label, MAX_LABEL_CHARS)
            if not label:
                raise ToolError(f"Give item {item.id} a label.")
            existing = current.get(item.id)
            done = item.done or (keep_done and existing is not None and existing.done)
            checklist.append(
                ChecklistItem(
                    id=item.id,
                    label=label,
                    done=done,
                    blocking=item.blocking,
                    hint=_clean(item.hint, MAX_HINT_CHARS) or None,
                    edited_by=existing.edited_by if existing is not None and done == existing.done else None,
                )
            )
        await ctx.ui.set_checklist(checklist)
        ctx.log.debug(
            "builtin_tool.set_checklist", call_id=context.function_call.call_id, items=len(checklist)
        )
        if ctx.pipeline_mode in QUIET_MODES:
            return None
        return f"The checklist now has {checklist_summary(checklist)}."

    return function_tool(
        set_checklist,
        description=(
            "Show the caller a checklist of what you still need from them, such as documents or "
            "details, and keep it current as things are provided. Update it quietly; do not read it "
            "aloud. Use check_item to tick one item."
        ),
    )
