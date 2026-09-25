"""`resolve_choice` built-in tool (V5-08, B1): record an option the caller said out loud.

The partner of `request_choice`: when the caller answers the question by voice
rather than tapping, the model calls this so the block shows the answer
(`status: "submitted"`, `selected`). A request still waiting on the block
resolves with the answer (`UiChannel.submit_block`, marked `via: "voice"` so
a realtime background waiter does not announce it back); with nothing pending
(a barge-in already withdrew it, or it timed out) the block is simply updated.
"""

from __future__ import annotations

import time
from typing import Any

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.ui_protocol import UiPatchOp
from packs.base import PackSessionContext

from lkap_agent.tools.builtin.request_choice import VIA_VOICE, choice_labels
from lkap_agent.ui.blocks import choice_selection_error, describe_blocks, pick_block, session_block_specs

__all__ = ["build_resolve_choice_tool"]


def build_resolve_choice_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `resolve_choice` tool bound to `ctx`."""
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["choices"])

    async def resolve_choice(context: RunContext[Any], selected: list[str], block_id: str = "") -> str:
        """Record the option the caller chose out loud, so their screen shows it.

        Args:
            selected: The ids of the options the caller chose.
            block_id: The choices block; leave empty when there is only one.
        """
        specs = session_block_specs(ctx.ui, ctx.config.panel)
        try:
            target = pick_block(specs, "choices", block_id or None)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        state = ctx.ui.state.blocks.get(target) or {}
        error = choice_selection_error(state, selected)
        if error is not None:
            raise ToolError(f"Cannot record that choice: {error}.")
        submit = getattr(ctx.ui, "submit_block", None)
        resolved = False
        if callable(submit):
            resolved = bool(await submit(target, {"selected": list(selected), "via": VIA_VOICE}))
        else:
            await ctx.ui.patch_block(
                target,
                [
                    UiPatchOp(op="set", path="/selected", value=list(selected)),
                    UiPatchOp(op="set", path="/status", value="submitted"),
                    UiPatchOp(op="set", path="/submitted_at", value=time.time()),
                ],
            )
        labels = choice_labels(state, list(selected))
        ctx.log.debug(
            "builtin_tool.resolve_choice",
            call_id=context.function_call.call_id,
            block_id=target,
            selected=list(selected),
            resolved_request=resolved,
        )
        return f"Recorded the caller's choice: {', '.join(labels)}."

    return function_tool(
        resolve_choice,
        description=(
            "Record the option the caller chose out loud for a question shown with request_choice, "
            f"so their screen shows it. Choices blocks: {inventory}."
        ),
    )
