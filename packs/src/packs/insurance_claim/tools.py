"""The insurance_claim pack's four code tools (docs/INSURANCE_PACK_MAPPING.md #7-#10).

Tool descriptions and parameter descriptions below are ported word-for-word from
the original Gemini ``FunctionDeclaration``\\ s (golden fixture
``packs/tests/insurance_claim/fixtures/insurance_declarations.json``; only wrapped
across lines to respect ``ruff``'s line length -- see
``packs/tests/insurance_claim/test_tools.py::test_tool_schema_matches_declarations_fixture``,
which collapses whitespace before comparing).

Behaviour parity (docs/ARCHITECTURE.md §7.2, §10.1):

- ``lookup_policy`` runs inline (it is a plain dict lookup, not I/O) and always
  returns a result the model can voice, in both pipeline modes.
- ``sync_claim_packet`` and ``draw_incident_sketch`` submit to
  ``ctx.background`` and return ``None`` in realtime mode (silent;
  ``reply_required=False``) or a short acknowledgement clause in cascaded mode
  (a cascaded LLM always voices a tool reply).
- ``pin_evidence_photo`` is inline like ``lookup_policy``: it always returns a
  compact result so the model can confirm what got pinned.

``ToolFlag.CANCELLABLE`` is set on all four (mapping #12): a user interruption
cancels the tool's *reply*, never the background job underneath it -- the UI
still gets its update.
"""

from __future__ import annotations

import json
import time
from typing import Any

from livekit.agents import FunctionTool, RunContext, function_tool
from livekit.agents.llm import ToolFlag
from lkap_contracts.ui_protocol import ActivityEvent, UiPatchOp

from packs.base import PackSessionContext, ToolMeta
from packs.insurance_claim.policy_directory import lookup_policy as _lookup_policy_record
from packs.insurance_claim.policy_directory import policy_status_headline
from packs.insurance_claim.prompts import sketch_prompt
from packs.insurance_claim.schemas import IntakeState, SketchState
from packs.insurance_claim.ui_state import build_ui_state_for_intake, camera_note
from packs.insurance_claim.workflow import (
    ClaimWorkflow,
    WorkflowResult,
    build_initial_workflow_state,
    voice_summary,
)

__all__ = [
    "INSURANCE_TOOL_META",
    "PIN_EVIDENCE_MAX_AGE_S",
    "build_draw_incident_sketch_tool",
    "build_insurance_tools",
    "build_lookup_policy_tool",
    "build_pin_evidence_photo_tool",
    "build_sync_claim_packet_tool",
    "get_intake",
    "model_label",
    "render_and_patch",
    "submit_workflow_run",
]

#: "the latest ws frame (<=12 s)" -- docs/INSURANCE_PACK_MAPPING.md #9.
PIN_EVIDENCE_MAX_AGE_S = 12.0


def get_intake(ctx: PackSessionContext) -> IntakeState:
    """Fetch (or lazily create) this session's `IntakeState` from `ctx.userdata`.

    `Pack.tools(ctx)` runs before `Pack.on_session_start(ctx)`
    (`PlatformAgent.__init__` builds the tool list before `on_enter` fires), so
    every entry point -- tools and hooks alike -- goes through this instead of
    assuming `on_session_start` has already run.
    """
    intake = ctx.userdata.get("intake")
    if not isinstance(intake, IntakeState):
        intake = IntakeState()
        ctx.userdata["intake"] = intake
    return intake


def model_label(ctx: PackSessionContext) -> str:
    """The workflow LLM's display name for the `LLM-001` UI event.

    Never a hardcoded model name (work package rule): reads
    `AgentConfig.pipeline.workflow_llm` (falling back to `llm`, then
    `realtime`) so whatever provider/model an admin configured is what shows
    up in the notebook.
    """
    pipeline = ctx.config.pipeline
    ref = pipeline.workflow_llm or pipeline.llm or pipeline.realtime
    if ref is None:
        return "the claim team"
    return ref.model or ref.provider_id


def _current_workflow_result(intake: IntakeState) -> WorkflowResult:
    """The last computed workflow result, or the blank-intake baseline."""
    if intake.last_workflow is not None:
        return WorkflowResult.model_validate(intake.last_workflow)
    return build_initial_workflow_state()


async def render_and_patch(ctx: PackSessionContext, result: WorkflowResult) -> None:
    """Recompute the notebook envelope from `result` and patch it into the UI.

    Ported orchestration for docs/INSURANCE_PACK_MAPPING.md #16: renders
    `/status`, `/progress`, `/notes`, `/checklist` and `/custom` (never
    `/assets`/`/activity`, which the live `UiChannel` owns), then stamps the
    new route back onto `intake.previous_route` so the next render's "Routing
    changed" event only fires when the route actually moves
    (`ui_state.build_ui_state_for_intake`'s documented contract).

    This is the single choke point for the tool-triggered and the passive
    workflow runs, so it also records the `escalation` session event
    (DECISIONS-W2 D-W3-1) on the route transition into
    `emergency_escalation` -- once per transition, never on a repeat run.
    """
    intake = get_intake(ctx)
    state = build_ui_state_for_intake(intake, result, now=time.time(), model_label=model_label(ctx))
    await ctx.ui.patch(
        [
            UiPatchOp(op="set", path="/status", value=state.status),
            UiPatchOp(op="set", path="/progress", value=state.progress),
            UiPatchOp(op="set", path="/notes", value=state.notes),
            UiPatchOp(op="set", path="/checklist", value=state.checklist),
            UiPatchOp(op="set", path="/custom", value=state.custom),
        ]
    )
    new_route = str(state.custom["route"])
    if new_route == "emergency_escalation" and intake.previous_route != new_route:
        ctx.record_event(
            "escalation",
            {"reason": "claim routed to emergency_escalation", "urgency": "high", "route": new_route},
        )
    intake.previous_route = new_route


def submit_workflow_run(
    ctx: PackSessionContext, *, urgent_when_emergency: bool, call_id: str | None = None
) -> str:
    """Submit one claim-workflow run to `ctx.background` (mapping #8 and #14).

    Args:
        ctx: The session context.
        urgent_when_emergency: `True` for the tool-triggered run (mapping #8:
            urgent exactly when routing is `emergency_escalation`); `False`
            for the debounced passive run from `on_user_turn_completed`
            (mapping #14: "never urgent-speaks", UI-and-note only).
        call_id: The tool call id, when submitted from a tool invocation.

    Returns:
        The background job id.
    """
    intake = get_intake(ctx)
    claim_workflow = ClaimWorkflow(ctx.workflow_llm)

    async def _job() -> WorkflowResult:
        return await claim_workflow.run(intake)

    async def _on_result(result: WorkflowResult) -> None:
        await render_and_patch(ctx, result)

    def _urgent(result: WorkflowResult) -> bool:
        return urgent_when_emergency and result.routing == "emergency_escalation"

    def _urgent_instructions(result: WorkflowResult) -> str:
        return (
            "Tell the claimant to contact emergency services if anyone is in danger "
            "and that a human representative will take over."
        )

    def _routine_note(result: WorkflowResult) -> str | None:
        return json.dumps(voice_summary(result))

    return ctx.background.submit(
        # `name` becomes `ActivityEvent.source` *and* the auto-derived `.label`
        # in `BackgroundToolRunner`/`FakeBackgroundRunner` (there is no separate
        # "label" parameter on `BackgroundRunner.submit`); the web notebook
        # panel keys off `source == tool name`, so this must be the tool's
        # name even though the passive debounced run has no `call_id` of its
        # own. See `INSURANCE_TOOL_META`'s comment for the resulting label text.
        name="sync_claim_packet",
        coro=_job(),
        on_result=_on_result,
        urgent=_urgent,
        urgent_instructions=_urgent_instructions,
        routine_note=_routine_note,
        call_id=call_id,
    )


def build_lookup_policy_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `lookup_policy` tool bound to `ctx` (mapping #7).

    Inline (a dict lookup, not I/O): verifies the policy, re-renders the
    notebook so policy fields/notes reflect it, and overrides the route stamp
    with a danger tone when the policy is lapsed or not found -- after the
    render, so it is not immediately clobbered by the route-derived stamp.
    """

    @function_tool(flags=ToolFlag.CANCELLABLE)
    async def lookup_policy(context: RunContext[Any], policy_number: str) -> str:
        """Verify an insurance policy number against the carrier policy directory. Runs
        in the background. Returns policyholder name, policy line, status, deductibles,
        and coverages when found.

        Args:
            policy_number: Policy number exactly as the claimant said it, for example
                H0-44721 or AUTO 90210.
        """
        intake = get_intake(ctx)
        record = _lookup_policy_record(policy_number)
        intake.policy_record = record
        await render_and_patch(ctx, _current_workflow_result(intake))

        found = bool(record.get("found"))
        urgent = not found or record.get("status") != "active"
        if urgent:
            await ctx.ui.set_status("Policy needs review", "danger")

        headline = policy_status_headline(dict(record)) if found else f"Policy {policy_number} not found"
        await ctx.ui.activity(
            ActivityEvent(
                id=context.function_call.call_id,
                ts=time.time(),
                source="lookup_policy",
                label="Policy desk",
                phase="done",
                headline=headline,
                urgent=urgent,
                detail={"policy_number": policy_number},
            )
        )
        return json.dumps(record)

    return lookup_policy


def build_sync_claim_packet_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `sync_claim_packet` tool bound to `ctx` (mapping #8)."""

    @function_tool(flags=ToolFlag.CANCELLABLE)
    async def sync_claim_packet(context: RunContext[Any], reason: str = "") -> str | None:
        """Send the full claimant conversation and camera observations so far to the
        background claim team. Returns the routing decision, severity, blocking intake
        items, required documents, and the next best question to ask.

        Args:
            reason: One short phrase on why you are syncing now, for example 'new loss
                facts' or 'injury mentioned'.
        """
        submit_workflow_run(ctx, urgent_when_emergency=True, call_id=context.function_call.call_id)
        if ctx.pipeline_mode == "realtime":
            return None
        return "Got it, updating the claim notes."

    return sync_claim_packet


def build_pin_evidence_photo_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `pin_evidence_photo` tool bound to `ctx` (mapping #9)."""

    @function_tool(flags=ToolFlag.CANCELLABLE)
    async def pin_evidence_photo(
        context: RunContext[Any],
        observation: str,
        confirmed: bool,
        claimant_description: str = "",
        evidence_type: str = "scene",
    ) -> str:
        """Tape the current camera frame into the claim notebook as evidence, with your
        own observation as the caption and whether it confirms what the claimant
        described. Call it only after you have looked at the camera and can describe
        what is in the frame.

        Args:
            observation: One or two concrete sentences describing only what you can
                actually see in the frame, for example 'Water line about two inches up
                the drywall next to the stairs.' Never restate the claimant's
                description as your own.
            confirmed: True only if the frame clearly shows what the claimant
                described. False if you cannot make it out, the frame is blurry or
                dark, or you see something different.
            claimant_description: What the claimant says this shows, in their words,
                for example 'a crack in the wall'. Leave empty if they did not describe
                it.
            evidence_type: Short category such as 'damage', 'receipt', 'document',
                'serial number', or 'scene'.
        """
        intake = get_intake(ctx)
        frame = await ctx.frames.latest_jpeg(max_age_s=PIN_EVIDENCE_MAX_AGE_S)
        if frame is None:
            return "No fresh camera frame is available to pin right now."

        jpeg_bytes, snapshot = frame
        captured_at = time.strftime("%H:%M", time.localtime())
        asset_id = await ctx.ui.push_asset(
            jpeg_bytes,
            "image/jpeg",
            "evidence",
            caption=observation,
            meta={
                "source": snapshot.source,
                "confirmed": "true" if confirmed else "false",
                "claimant_description": claimant_description,
                "evidence_type": evidence_type,
                "captured_at": captured_at,
            },
        )
        intake.camera_notes.append(camera_note(observation, claimant_description, confirmed))
        await ctx.ui.activity(
            ActivityEvent(
                id=context.function_call.call_id,
                ts=time.time(),
                source="pin_evidence_photo",
                label="Evidence",
                phase="done",
                headline=observation,
                urgent=False,
                detail={"asset_id": asset_id, "confirmed": "true" if confirmed else "false"},
            )
        )
        return json.dumps({"pinned": True, "asset_id": asset_id, "confirmed": confirmed})

    return pin_evidence_photo


def build_draw_incident_sketch_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `draw_incident_sketch` tool bound to `ctx` (mapping #10).

    Degrades gracefully with no crash in two independent places: `ctx.image_gen`
    is `None` when no image-gen credential is configured (returns a spoken
    apology, submits nothing), and the submitted job itself catches any
    provider failure so a routine note ("didn't come through") is delivered
    instead of the background job merely showing `phase="error"` with no
    conversational follow-up.
    """

    @function_tool(flags=ToolFlag.CANCELLABLE)
    async def draw_incident_sketch(context: RunContext[Any], scene_description: str) -> str | None:
        """Ask the claim team to draw a rough hand-drawn pen sketch of the incident
        scene into the notebook. Runs in the background for several seconds. Call it
        once you know the location, the layout, and what happened. Call it again with
        corrections.

        Args:
            scene_description: Illustrator brief in plain words: the space or
                intersection, where things are, what got damaged, and the direction of
                impact or water flow. Include labels to write on the sketch. Fold in
                any corrections the claimant gave.
        """
        intake = get_intake(ctx)
        image_gen = ctx.image_gen
        if image_gen is None:
            return (
                "Sketching isn't available in this session; tell the claimant you'll "
                "describe it in words instead."
            )

        version = (intake.sketch.version if intake.sketch is not None else 0) + 1
        prompt = sketch_prompt(scene_description)

        async def _job() -> tuple[bytes, str] | None:
            try:
                return await image_gen.generate(prompt)
            except Exception as exc:  # noqa: BLE001 - any provider failure degrades gracefully
                ctx.log.warning("draw_incident_sketch.generate_failed", error=str(exc))
                return None

        async def _on_result(result: tuple[bytes, str] | None) -> None:
            if result is None:
                return
            image_bytes, mime = result
            asset_id = await ctx.ui.push_asset(
                image_bytes,
                mime,
                "sketch",
                caption="Does this look right?",
                meta={"version": str(version), "brief": scene_description},
            )
            sketch = SketchState(asset_id=asset_id, brief=scene_description, version=version, confirmed=False)
            intake.sketch = sketch
            await ctx.ui.patch(
                [UiPatchOp(op="set", path="/custom/sketch", value=sketch.model_dump(mode="json"))]
            )

        def _routine_note(result: tuple[bytes, str] | None) -> str | None:
            if result is None:
                return "The sketch didn't come through this time; let the claimant know and offer to retry."
            return f"Sketch v{version} is in the notebook; ask if it looks right."

        ctx.background.submit(
            # See `submit_workflow_run`'s comment: `name` doubles as
            # `ActivityEvent.source`, which the web notebook panel requires to
            # equal the tool name.
            name="draw_incident_sketch",
            coro=_job(),
            on_result=_on_result,
            routine_note=_routine_note,
            call_id=context.function_call.call_id,
        )
        if ctx.pipeline_mode == "realtime":
            return None
        return "Sketching that now."

    return draw_incident_sketch


def build_insurance_tools(ctx: PackSessionContext) -> list[FunctionTool[..., Any]]:
    """All four insurance pack code tools, bound to `ctx` (docs/CONTRACTS.md §8)."""
    return [
        build_lookup_policy_tool(ctx),
        build_sync_claim_packet_tool(ctx),
        build_pin_evidence_photo_tool(ctx),
        build_draw_incident_sketch_tool(ctx),
    ]


#: Independent of `ctx` -- `Pack.tool_meta()` describes every tool `tools()` can
#: return (docs/CONTRACTS.md §8). `activity_label` here documents the intended
#: "team member" name (docs/INSURANCE_PACK_MAPPING.md #21). The two
#: background-run tools' actual `ActivityEvent.label` comes from
#: `BackgroundRunner.submit(name=...)` (`submit_workflow_run`,
#: `draw_incident_sketch`) via `BackgroundToolRunner._label()`, which
#: capitalizes `name` -- and `name` must equal the tool name so
#: `ActivityEvent.source` matches it too (the web notebook panel keys off
#: `source == tool name`). `BackgroundRunner.submit` has no separate `label`
#: parameter, so those two labels render as "Sync claim packet"/"Draw incident
#: sketch" rather than "Claim writer"/"Sketch artist" -- a known gap, flagged
#: in the work package report for W1-AGENT-UI/W2-AGENT-INTEGRATION to close
#: (e.g. by having `BackgroundToolRunner` look up `ToolMeta.activity_label`).
#: `lookup_policy` and `pin_evidence_photo` are inline (not backgrounded), so
#: their `source`/`label` are set directly in this file and already match.
INSURANCE_TOOL_META: list[ToolMeta] = [
    ToolMeta(name="lookup_policy", silent_reply=False, activity_label="Policy desk"),
    ToolMeta(name="sync_claim_packet", silent_reply=False, activity_label="Claim writer"),
    ToolMeta(name="pin_evidence_photo", silent_reply=False, activity_label="Evidence"),
    ToolMeta(name="draw_incident_sketch", silent_reply=False, activity_label="Sketch artist"),
]
