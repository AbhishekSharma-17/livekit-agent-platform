"""The insurance_claim `Pack`: tools, lifecycle hooks, and initial UI state.

Wires the deterministic core (W1-PACK-INSURANCE-CORE: schemas, rules, policy
directory, prompts, workflow, ui_state) and the four code tools (`tools.py`,
this work package) into `packs.base.Pack` per docs/CONTRACTS.md §8 and
docs/INSURANCE_PACK_MAPPING.md.

Deviation from docs/INSURANCE_PACK_MAPPING.md #3: that row describes
`session.say(greeting)` happening in `on_session_start`, but
`agent/src/lkap_agent/platform_agent.py::PlatformAgent.on_enter` (W1-AGENT-CORE,
already implemented) speaks `AgentConfig.voice.greeting` itself and *then* calls
`pack.on_session_start(ctx)`. `IMPLEMENTATION_PLAN.md`'s own line for this work
package only promises "initial snapshot from build_ui_state(blank)" for this
hook, which is what it does; saying the greeting here too would double-greet.
Flagged for W2-AGENT-INTEGRATION / the report.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from livekit.agents import ChatContext, ChatMessage, llm
from lkap_contracts.ui_protocol import UiPatchOp

from packs.base import Pack, PackSessionContext, ToolMeta
from packs.insurance_claim.manifest import MANIFEST
from packs.insurance_claim.tools import (
    INSURANCE_TOOL_META,
    build_insurance_tools,
    get_intake,
    model_label,
    render_and_patch,
    submit_workflow_run,
)
from packs.insurance_claim.ui_state import build_ui_state
from packs.insurance_claim.workflow import build_initial_workflow_state

__all__ = ["PACK", "USER_TURN_DEBOUNCE_S", "InsuranceClaimPack"]

#: Debounce window for the passive, transcript-triggered workflow run
#: (docs/INSURANCE_PACK_MAPPING.md #14: "debounced (2 s) run every 2nd user
#: turn when text changed"). Tests set this to `0` to run synchronously.
USER_TURN_DEBOUNCE_S = 2.0

_TURN_COUNT_KEY = "insurance_claim.turn_count"
_LAST_PASSIVE_TEXT_KEY = "insurance_claim.last_passive_text"
_DEBOUNCE_TASK_KEY = "insurance_claim.debounce_task"


class InsuranceClaimPack:
    """The insurance FNOL pack (docs/INSURANCE_PACK_MAPPING.md)."""

    manifest = MANIFEST

    def tools(self, ctx: PackSessionContext) -> list[llm.FunctionTool[..., Any]]:
        """The four code tools bound to this session (mapping #7-#10)."""
        return build_insurance_tools(ctx)

    def tool_meta(self) -> list[ToolMeta]:
        """Metadata for the four tools, independent of `ctx`."""
        return list(INSURANCE_TOOL_META)

    def initial_state(self, ctx: PackSessionContext) -> dict[str, Any]:
        """`UiState.custom` for a blank intake, before the claimant has spoken."""
        result = build_initial_workflow_state()
        state = build_ui_state(
            "",
            result,
            policy_record=None,
            camera_notes=[],
            sketch=None,
            previous_route=None,
            now=time.time(),
            model_label=model_label(ctx),
        )
        return state.custom

    async def on_session_start(self, ctx: PackSessionContext) -> None:
        """Push the initial notebook snapshot (the platform speaks the greeting)."""
        await render_and_patch(ctx, build_initial_workflow_state())
        await ctx.ui.snapshot()

    async def on_user_turn_completed(
        self, ctx: PackSessionContext, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        """Record the claimant's turn and debounce a passive workflow run (mapping #14).

        Runs a workflow pass every 2nd claimant turn (bounding LLM usage,
        docs/ARCHITECTURE.md §10.1), 2 s after the turn lands so a burst of
        quick turns collapses into one run, and only when the transcript text
        actually changed since the last passive run. This passive run is never
        urgent -- an emergency surfaced here only updates the UI and leaves a
        routine note; only the `sync_claim_packet` tool call can interrupt with
        `generate_reply` (mapping #8 vs #14).
        """
        intake = get_intake(ctx)
        text = (new_message.text_content or "").strip()
        if text:
            intake.participant_turns.append(text)

        turns = int(ctx.userdata.get(_TURN_COUNT_KEY, 0)) + 1
        ctx.userdata[_TURN_COUNT_KEY] = turns
        if not text or turns % 2 != 0:
            return

        current_text = intake.participant_text()
        if current_text == ctx.userdata.get(_LAST_PASSIVE_TEXT_KEY):
            return

        existing = ctx.userdata.get(_DEBOUNCE_TASK_KEY)
        if isinstance(existing, asyncio.Task) and not existing.done():
            existing.cancel()

        async def _debounced_run() -> None:
            try:
                await asyncio.sleep(USER_TURN_DEBOUNCE_S)
            except asyncio.CancelledError:
                return
            ctx.userdata[_LAST_PASSIVE_TEXT_KEY] = current_text
            submit_workflow_run(ctx, urgent_when_emergency=False)

        ctx.userdata[_DEBOUNCE_TASK_KEY] = asyncio.ensure_future(_debounced_run())

    async def on_agent_turn_completed(self, ctx: PackSessionContext, text: str, interrupted: bool) -> None:
        """No agent-turn behaviour: the pack only reacts to claimant turns and tool calls."""
        return None

    async def on_ui_action(
        self, ctx: PackSessionContext, action: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle `confirm_sketch` ("does this look right?", mapping #10); nothing else."""
        if action != "confirm_sketch":
            return {"ok": False, "error": f"insurance_claim pack has no ui_action handler for {action!r}"}

        intake = get_intake(ctx)
        if intake.sketch is None:
            return {"ok": False, "error": "no sketch to confirm"}

        intake.sketch = intake.sketch.model_copy(update={"confirmed": True})
        await ctx.ui.patch(
            [UiPatchOp(op="set", path="/custom/sketch", value=intake.sketch.model_dump(mode="json"))]
        )
        return {"ok": True, "payload": {"asset_id": intake.sketch.asset_id, "confirmed": True}}

    async def on_session_end(self, ctx: PackSessionContext, reason: str) -> None:
        """Leave a final note on the route the claim landed on.

        Guarded on `previous_route` (set by every `render_and_patch`, starting
        with `on_session_start`'s blank render) rather than `last_workflow`
        (only set once a claimant transcript has actually run through
        `ClaimWorkflow.run`): a session that never got past `on_session_start`
        has nothing to report and is the only case this skips.
        """
        intake = get_intake(ctx)
        if intake.previous_route is None:
            return
        await ctx.ui.add_note(
            f"Session ended ({reason}). Final route: {intake.previous_route}.", kind="aside"
        )


PACK: Pack = InsuranceClaimPack()
