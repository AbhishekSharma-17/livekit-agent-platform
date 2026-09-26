"""`record_consent` built-in tool (V5-15, B5), and the one place a consent answer is settled.

The partner of `request_consent`: when the caller answers a consent question
out loud (on any channel), the model calls this with their answer. It is
registered for a `consent` block, and also without any block when the agent
records only after consent (`recording.require_consent`), so a phone caller
can agree by voice.

:func:`settle_consent` is shared with `request_consent`. Whichever path learns
the answer settles it exactly once:

* the `consent` block (when there is one) gets `accepted`, `method`, `at` and
  `text_hash` (the SHA-256 of the exact wording shown, `lkap_contracts.
  compliance.consent_text_hash`);
* a `consent` session event `{kind, accepted, method, text_hash, block_id}`
  is recorded. The worker's recording gate (`main.py`) listens for it: with
  `recording.require_consent`, Egress starts only after a `recording`
  consent is accepted, and never after a decline;
* a declined **required** consent whose block says `decline_action="end_call"`
  says a polite goodbye and ends the call (as `end_call` does).

With a request still waiting on the block (a realtime `request_consent`), the
answer is handed to that request (`UiChannel.submit_block`) and the waiter
settles it; otherwise this tool settles it itself.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from typing import Any, Final, Literal, cast

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool, get_job_context
from lkap_contracts.blocks import ConsentBlockConfig
from lkap_contracts.compliance import (
    COMPLIANCE_PRESETS,
    CONSENT_EVENT,
    DEFAULT_JURISDICTION,
    ConsentEvent,
    ConsentKind,
    ConsentMethod,
    consent_text_hash,
)
from lkap_contracts.ui_protocol import BlockSpec, UiPatchOp
from packs.base import PackSessionContext
from pydantic import ValidationError

from lkap_agent.ui.blocks import describe_blocks, session_block_specs

__all__ = [
    "CONSENT_GOODBYE",
    "VIA_VOICE",
    "ConsentOutcome",
    "build_record_consent_tool",
    "consent_block_config",
    "consent_block_text",
    "outcome_message",
    "recording_consent_text",
    "settle_consent",
]

#: `via` value an answer carries when it was given out loud (the waiter stays quiet about it).
VIA_VOICE: Final[str] = "voice"

#: What the agent says before ending the call on a declined required consent.
CONSENT_GOODBYE: Final[str] = (
    "I understand. We can't continue without your agreement, so I'll end the call here. Thank you, goodbye."
)


#: What was settled: the `consent` event's fields plus `ending` (the call is being ended).
ConsentOutcome = dict[str, Any]

#: `kind` values `record_consent` accepts (empty = the only block, or recording).
_KINDS: Final[frozenset[str]] = frozenset({"", "recording", "ai_disclosure", "terms", "custom"})


def consent_block_config(spec: BlockSpec | None) -> ConsentBlockConfig:
    """The block's `ConsentBlockConfig`, or the defaults when it does not validate (or there is no block)."""
    if spec is None:
        return ConsentBlockConfig()
    try:
        return ConsentBlockConfig.model_validate(spec.config)
    except ValidationError:
        return ConsentBlockConfig()


def recording_consent_text(ctx: PackSessionContext) -> str:
    """The recording question when no block carries one (`recording.consent_text`, filled by the worker)."""
    text = (ctx.config.recording.consent_text or "").strip()
    return text or COMPLIANCE_PRESETS[DEFAULT_JURISDICTION].recording_text


def consent_block_text(ctx: PackSessionContext, spec: BlockSpec) -> str:
    """The exact wording of a consent block: its state's `text`, else its config's (both set at start)."""
    state = ctx.ui.state.blocks.get(spec.id) or {}
    text = state.get("text") if isinstance(state, dict) else None
    if isinstance(text, str) and text.strip():
        return text
    config_text = consent_block_config(spec).text
    if config_text.strip():
        return config_text
    kind = consent_block_config(spec).kind
    if kind == "recording":
        return recording_consent_text(ctx)
    if kind == "ai_disclosure":
        return (ctx.config.disclosure.text or "").strip() or COMPLIANCE_PRESETS[
            DEFAULT_JURISDICTION
        ].disclosure_text
    return ""


def _shutdown_fn(ctx: PackSessionContext) -> Callable[[str], None]:
    requested = getattr(ctx, "request_shutdown", None)
    if callable(requested):
        return cast(Callable[[str], None], requested)

    def _default(reason: str) -> None:
        get_job_context().shutdown(reason=reason)

    return _default


async def _say_goodbye_and_end(ctx: PackSessionContext) -> None:
    """Say :data:`CONSENT_GOODBYE`, wait for it to play, then end the job (as `end_call`)."""
    try:
        if ctx.pipeline_mode == "realtime":
            result: Any = ctx.session.generate_reply(instructions=f"Say goodbye: {CONSENT_GOODBYE}")
        else:
            result = ctx.session.say(CONSENT_GOODBYE)
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001 - ending the call must not depend on the goodbye playing
        ctx.log.warning("consent goodbye could not be spoken; ending anyway", exc_info=True)
    finally:
        _shutdown_fn(ctx)("consent declined")


async def settle_consent(
    ctx: PackSessionContext,
    *,
    spec: BlockSpec | None,
    kind: ConsentKind,
    accepted: bool,
    method: ConsentMethod,
    text: str,
) -> ConsentOutcome:
    """Record one consent answer: the block, the `consent` event and a decline's end of call.

    Args:
        ctx: The session context.
        spec: The consent block answered, or `None` (a spoken answer with no block).
        kind: What the consent covers.
        accepted: The caller's answer.
        method: `tap` or `voice`.
        text: The exact wording the caller was shown or read; hashed as is.

    Returns:
        What was settled; `ending` is true when the call is being ended.
    """
    digest = consent_text_hash(text)
    now = time.time()
    if spec is not None:
        state = ctx.ui.state.blocks.get(spec.id) or {}
        ops = [
            UiPatchOp(op="set", path="/accepted", value=accepted),
            UiPatchOp(op="set", path="/method", value=method),
            UiPatchOp(op="set", path="/at", value=now),
            UiPatchOp(op="set", path="/text_hash", value=digest),
        ]
        if not isinstance(state, dict) or state.get("status") != "submitted":
            ops += [
                UiPatchOp(op="set", path="/status", value="submitted"),
                UiPatchOp(op="set", path="/submitted_at", value=now),
            ]
        try:
            await ctx.ui.patch_block(spec.id, ops)
        except Exception:  # noqa: BLE001 - the answer is recorded on the event either way
            ctx.log.warning("consent block could not be updated", block_id=spec.id, exc_info=True)
    event = ConsentEvent(
        kind=kind,
        accepted=accepted,
        method=method,
        text_hash=digest,
        block_id=spec.id if spec is not None else None,
    )
    ctx.record_event(CONSENT_EVENT, event.model_dump())
    config = consent_block_config(spec)
    ending = bool(
        not accepted and spec is not None and config.required and config.decline_action == "end_call"
    )
    ctx.log.debug(
        "builtin_tool.consent_settled",
        kind=kind,
        accepted=accepted,
        method=method,
        block_id=event.block_id,
        ending=ending,
    )
    outcome: ConsentOutcome = {**event.model_dump(), "ending": ending}
    if ending:
        await _say_goodbye_and_end(ctx)
    return outcome


def outcome_message(ctx: PackSessionContext, outcome: ConsentOutcome) -> str:
    """What the model is told once a consent answer is settled."""
    kind = outcome["kind"]
    if outcome["ending"]:
        return "The caller declined a required consent; the call is ending. Say nothing more."
    if outcome["accepted"]:
        if kind == "recording" and ctx.config.recording.enabled and ctx.config.recording.require_consent:
            return "The caller agreed to be recorded; the recording starts now. Continue."
        return f"The caller agreed ({kind.replace('_', ' ')}). Continue."
    if kind == "recording" and ctx.config.recording.enabled:
        return "The caller declined to be recorded. The call will not be recorded; carry on normally."
    return f"The caller declined ({kind.replace('_', ' ')}). Respect it and carry on."


def _consent_specs(ctx: PackSessionContext) -> list[BlockSpec]:
    return [s for s in session_block_specs(ctx.ui, ctx.config.panel) if s.type == "consent"]


def _pick_consent_block(specs: list[BlockSpec], block_id: str, kind: str) -> BlockSpec | None:
    """The block a spoken answer is for: by id, else the only one (of `kind`), else `None` with no blocks."""
    if not specs:
        return None
    if block_id:
        match = next((s for s in specs if s.id == block_id), None)
        if match is None:
            raise ToolError(
                f"Unknown consent block {block_id!r}; use one of: {', '.join(s.id for s in specs)}."
            )
        return match
    candidates = [s for s in specs if not kind or consent_block_config(s).kind == kind]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates and kind == "recording":
        return None
    ids = ", ".join(s.id for s in (candidates or specs))
    raise ToolError(f"Say which consent block this answer is for: one of {ids}.")


def build_record_consent_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `record_consent` tool bound to `ctx`."""
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["consent"])

    async def record_consent(
        context: RunContext[Any],
        accepted: bool,
        method: Literal["voice", "tap"] = "voice",
        block_id: str = "",
        kind: str = "",
    ) -> str:
        """Record the caller's yes or no to a consent question, such as agreeing to be recorded.

        Args:
            accepted: True when the caller agreed, false when they said no.
            method: voice when they answered out loud; tap when you were told they answered on screen.
            block_id: The consent block; leave empty when there is only one.
            kind: What they answered about (recording when there is no consent block).
        """
        if method not in ("voice", "tap"):
            raise ToolError("method must be voice or tap.")
        if kind not in _KINDS:
            raise ToolError("kind must be recording, ai_disclosure, terms or custom, or empty.")
        specs = _consent_specs(ctx)
        spec = _pick_consent_block(specs, block_id, kind)
        call_id = context.function_call.call_id
        if spec is None:
            answered_kind: ConsentKind = "recording"
            text = recording_consent_text(ctx)
        else:
            answered_kind = consent_block_config(spec).kind
            text = consent_block_text(ctx, spec)
            submit = getattr(ctx.ui, "submit_block", None)
            pending = getattr(ctx.ui, "pending_requests", {}) or {}
            if callable(submit) and spec.id in pending:
                # A request_consent is still waiting (realtime): it settles the answer.
                await submit(spec.id, {"accepted": accepted, "via": method})
                ctx.log.debug(
                    "builtin_tool.record_consent", call_id=call_id, block_id=spec.id, handed_over=True
                )
                return (
                    "Recorded the caller's agreement." if accepted else "Recorded that the caller declined."
                )
        outcome = await settle_consent(
            ctx, spec=spec, kind=answered_kind, accepted=accepted, method=method, text=text
        )
        ctx.log.debug(
            "builtin_tool.record_consent",
            call_id=call_id,
            block_id=spec.id if spec is not None else None,
            kind=answered_kind,
            accepted=accepted,
        )
        return outcome_message(ctx, outcome)

    return function_tool(
        record_consent,
        description=(
            "Record the caller's yes or no to a consent question (for example, agreeing to be recorded) "
            "when they answer out loud, or when you are told they answered a consent block on screen "
            "(method tap). Only record what the caller actually said. "
            f"Consent blocks: {inventory}."
        ),
    )
