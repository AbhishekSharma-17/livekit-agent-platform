"""`set_steps` built-in tool (V5-08, B4): move a `steps` timeline along.

Only for a `steps` block the agent drives (`config.source == "manual"`); a
block with `source="flow"` follows the flow and has no tool (the flow runtime
writes it on every step change). The model passes the steps that changed:
each is merged by `id` into the block (a new id is appended), its status is
one of `pending`, `active`, `done`, `skipped`, `failed`, and a status change
stamps `at`. `current` names the active step; without it, the first `active`
step becomes current.
"""

from __future__ import annotations

import time
from typing import Any, Final, get_args

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.ui_protocol import BlockSpec, StepsBlockState, StepStatus
from packs.base import PackSessionContext
from pydantic import BaseModel, Field, ValidationError

from lkap_agent.ui.blocks import describe_blocks, session_block_specs

__all__ = ["STEP_STATUSES", "StepIn", "build_set_steps_tool", "manual_steps_blocks", "merge_steps"]

#: Every `StepStatus`.
STEP_STATUSES: Final[tuple[str, ...]] = get_args(StepStatus)


class StepIn(BaseModel):
    """One step the model sets."""

    id: str = Field(description="Machine id of the step, e.g. photos.")
    status: StepStatus = Field(default="pending", description="Where the step stands.")
    label: str = Field(default="", description="What the caller sees; leave empty to keep the step's label.")
    note: str = Field(default="", description="An optional short note under the step.")


def manual_steps_blocks(specs: list[BlockSpec]) -> list[BlockSpec]:
    """The `steps` blocks `set_steps` may write (not following the flow)."""
    return [s for s in specs if s.type == "steps" and s.config.get("source") != "flow"]


def merge_steps(state: dict[str, Any], steps: list[StepIn], current: str, *, now: float) -> dict[str, Any]:
    """The block's new `StepsBlockState` JSON after merging `steps` by id.

    Raises:
        ToolError: On an unknown status, a new step with no label, or a
            `current` that names no step.
    """
    existing = (
        [dict(s) for s in state.get("steps", []) if isinstance(s, dict)]
        if isinstance(state.get("steps"), list)
        else []
    )
    by_id = {s.get("id"): s for s in existing}
    for step in steps:
        step_id = step.id.strip()
        if not step_id:
            raise ToolError("Every step needs an id.")
        if step.status not in STEP_STATUSES:
            raise ToolError(f"Unknown status {step.status!r}; use one of {', '.join(STEP_STATUSES)}.")
        row = by_id.get(step_id)
        if row is None:
            row = {"id": step_id, "label": step.label.strip() or step_id.replace("_", " ").capitalize()}
            existing.append(row)
            by_id[step_id] = row
        elif step.label.strip():
            row["label"] = step.label.strip()
        if row.get("status") != step.status:
            row["at"] = now
        row["status"] = step.status
        if step.note.strip():
            row["note"] = step.note.strip()
    ids = [s.get("id") for s in existing]
    if current and current not in ids:
        raise ToolError(f"current names no step; the steps are {', '.join(map(str, ids))}.")
    active = current or next((s.get("id") for s in existing if s.get("status") == "active"), None)
    try:
        return StepsBlockState.model_validate({"steps": existing, "current": active}).model_dump(mode="json")
    except ValidationError as exc:
        raise ToolError(f"Those steps do not fit the block: {exc.errors()[0]['msg']}.") from exc


def build_set_steps_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `set_steps` tool bound to `ctx`."""
    inventory = describe_blocks(manual_steps_blocks(session_block_specs(ctx.ui, ctx.config.panel)))

    async def set_steps(
        context: RunContext[Any], steps: list[StepIn], current: str = "", block_id: str = ""
    ) -> str:
        """Update the steps shown in the caller's side panel.

        Args:
            steps: The steps that changed (each by id); new ids are added at the end.
            current: The id of the step the caller is on now; leave empty to use the active one.
            block_id: The steps block; leave empty when there is only one.
        """
        candidates = [s.id for s in manual_steps_blocks(session_block_specs(ctx.ui, ctx.config.panel))]
        if block_id and block_id in candidates:
            target = block_id
        elif len(candidates) == 1:
            target = candidates[0]
        elif not candidates:
            raise ToolError("This panel has no steps block the agent can change.")
        else:
            raise ToolError(f"Unknown steps block {block_id!r}; use one of: {', '.join(candidates)}.")
        if not steps:
            raise ToolError("Pass at least one step.")
        state = merge_steps(ctx.ui.state.blocks.get(target) or {}, steps, current.strip(), now=time.time())
        await ctx.ui.set_block(target, state)
        ctx.log.debug(
            "builtin_tool.set_steps",
            call_id=context.function_call.call_id,
            block_id=target,
            steps={s.id: s.status for s in steps},
            current=state.get("current"),
        )
        return f"Steps updated on {target}."

    return function_tool(
        set_steps,
        description=(
            "Update the progress steps in the caller's side panel as the call moves on. "
            "Do this quietly; at most mention how many steps are done. "
            f"Steps blocks: {inventory}."
        ),
    )
