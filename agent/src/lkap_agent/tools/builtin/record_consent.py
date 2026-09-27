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

V5-27 (S5-3, S5-4): the hashed wording is always the worker's own (the block's
config text, else the workspace preset), never the block's state, which a
browser answer could have touched. The model records only what it *heard*:
`record_consent` has no `method` (a tap is settled by the channel's submit,
never by the model) and needs a user turn after the question; that turn's id
(the `ChatMessage` id the observer puts on the `user_turn` event) is stored
as the event's `turn_id`. What this session recorded is kept on a
:class:`ConsentLedger`, which "already agreed" reads.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final, cast

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool, get_job_context, llm
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
    "CONSENT_LEDGER_KEY",
    "NO_ANSWER_HEARD",
    "USER_TURN_WAIT_S",
    "VIA_VOICE",
    "ConsentLedger",
    "ConsentOutcome",
    "build_record_consent_tool",
    "consent_block_config",
    "consent_block_text",
    "consent_ledger",
    "heard_turn_id",
    "ledger_key",
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

#: What the model is told when no caller answer was heard after the question (S5-4).
NO_ANSWER_HEARD: Final[str] = (
    "No answer from the caller has been heard since the question. Ask it, wait for their answer, "
    "then call record_consent."
)
#: How long `record_consent` waits for the caller's words to reach the history on a realtime
#: pipeline, where the user transcript can land after the model's function call.
USER_TURN_WAIT_S: Final[float] = 2.0
_USER_TURN_POLL_S: Final[float] = 0.1
_REALTIME_MODES: Final[frozenset[str]] = frozenset({"realtime", "half_cascade"})
#: `ConsentEvent.turn_id`'s longest value.
_MAX_TURN_ID_CHARS: Final[int] = 64
#: `SessionContext.userdata` key of the session's :class:`ConsentLedger` (platform-private,
#: like `knowledge.KNOWLEDGE_STATE_KEY`).
CONSENT_LEDGER_KEY: Final[str] = "_lkap_consent"


@dataclass
class ConsentLedger:
    """What one session recorded about consent (S5-3, S5-4).

    `answers` holds the latest recorded answer per consent (a block id, or
    ``kind:<kind>`` without a block), written only by :func:`settle_consent`
    right after the `consent` event: a browser writing `accepted` on a block
    never counts as agreement. `asked_at` is when `request_consent` last asked
    each block's question; a spoken answer must come after it.
    """

    answers: dict[str, bool] = field(default_factory=dict)
    asked_at: dict[str, float] = field(default_factory=dict)


def consent_ledger(ctx: PackSessionContext) -> ConsentLedger:
    """The session's :class:`ConsentLedger`, created on first use (in `ctx.userdata`)."""
    ledger = ctx.userdata.get(CONSENT_LEDGER_KEY)
    if not isinstance(ledger, ConsentLedger):
        ledger = ConsentLedger()
        ctx.userdata[CONSENT_LEDGER_KEY] = ledger
    return ledger


def ledger_key(spec: BlockSpec | None, kind: str) -> str:
    """The ledger key of a consent: its block id, or ``kind:<kind>`` without a block."""
    return spec.id if spec is not None else f"kind:{kind}"


def _latest_user_turn(ctx: PackSessionContext, asked_at: float | None) -> str | None:
    """The id of the newest user message after the question, or `None`.

    With `asked_at` (the question was put by `request_consent`) the message must
    be newer than it; without it (the model asked out loud) it must follow an
    assistant message, the question.
    """
    history = getattr(getattr(ctx, "session", None), "history", None)
    items = getattr(history, "items", None) or []
    after_question = asked_at is not None
    found: str | None = None
    for item in items:
        if not isinstance(item, llm.ChatMessage):
            continue
        if item.role == "assistant":
            after_question = True
        elif (
            item.role == "user"
            and after_question
            and item.text_content
            and (asked_at is None or item.created_at >= asked_at)
        ):
            found = item.id
    return found[:_MAX_TURN_ID_CHARS] if found else None


async def heard_turn_id(ctx: PackSessionContext, asked_at: float | None) -> str | None:
    """The user turn a spoken consent answer was heard in (S5-4); `None` when there is none.

    On a realtime pipeline the caller's words can reach the history after the
    model called the tool, so this waits up to :data:`USER_TURN_WAIT_S` there.
    """
    turn = _latest_user_turn(ctx, asked_at)
    if turn is not None or ctx.pipeline_mode not in _REALTIME_MODES:
        return turn
    waited = 0.0
    while turn is None and waited < USER_TURN_WAIT_S:
        await asyncio.sleep(_USER_TURN_POLL_S)
        waited += _USER_TURN_POLL_S
        turn = _latest_user_turn(ctx, asked_at)
    return turn


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
    """The exact wording of a consent block, computed by the worker (S5-3).

    The block's config `text` (`apply_compliance` fills an empty `recording`
    or `ai_disclosure` one from the workspace wording at session start), else
    the preset for its kind. Never the block's state: `request_consent` writes
    this wording there, and the hash must not depend on what else could.
    """
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
    turn_id: str | None = None,
) -> ConsentOutcome:
    """Record one consent answer: the block, the `consent` event and a decline's end of call.

    Args:
        ctx: The session context.
        spec: The consent block answered, or `None` (a spoken answer with no block).
        kind: What the consent covers.
        accepted: The caller's answer.
        method: `tap` or `voice`.
        text: The exact wording the caller was shown or read, computed by the
            worker (:func:`consent_block_text`); hashed as is.
        turn_id: The user turn a `voice` answer was heard in (S5-4); ignored for a tap.

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
        turn_id=turn_id if method == "voice" else None,
    )
    ctx.record_event(CONSENT_EVENT, event.model_dump())
    consent_ledger(ctx).answers[ledger_key(spec, kind)] = accepted
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
        block_id: str = "",
        kind: str = "",
    ) -> str:
        """Record the caller's spoken yes or no to a consent question, such as agreeing to be recorded.

        Args:
            accepted: True when the caller agreed, false when they said no.
            block_id: The consent block; leave empty when there is only one.
            kind: What they answered about (recording when there is no consent block).
        """
        if kind not in _KINDS:
            raise ToolError("kind must be recording, ai_disclosure, terms or custom, or empty.")
        specs = _consent_specs(ctx)
        spec = _pick_consent_block(specs, block_id, kind)
        call_id = context.function_call.call_id
        answered_kind: ConsentKind = "recording" if spec is None else consent_block_config(spec).kind
        # S5-4: a spoken answer needs a caller turn after the question; its id is the audit anchor.
        asked_at = consent_ledger(ctx).asked_at.get(ledger_key(spec, answered_kind))
        turn_id = await heard_turn_id(ctx, asked_at)
        if turn_id is None:
            ctx.log.debug("builtin_tool.record_consent", call_id=call_id, refused="no user turn")
            raise ToolError(NO_ANSWER_HEARD)
        if spec is None:
            text = recording_consent_text(ctx)
        else:
            text = consent_block_text(ctx, spec)
            submit = getattr(ctx.ui, "submit_block", None)
            pending = getattr(ctx.ui, "pending_requests", {}) or {}
            if callable(submit) and spec.id in pending:
                # A request_consent is still waiting (realtime): it settles the answer.
                await submit(spec.id, {"accepted": accepted, "via": VIA_VOICE, "turn_id": turn_id})
                ctx.log.debug(
                    "builtin_tool.record_consent", call_id=call_id, block_id=spec.id, handed_over=True
                )
                return (
                    "Recorded the caller's agreement." if accepted else "Recorded that the caller declined."
                )
        outcome = await settle_consent(
            ctx,
            spec=spec,
            kind=answered_kind,
            accepted=accepted,
            method="voice",
            text=text,
            turn_id=turn_id,
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
            "when they answer out loud. Only record what the caller actually said, after you asked; "
            "an answer tapped on screen is recorded by itself. "
            f"Consent blocks: {inventory}."
        ),
    )
