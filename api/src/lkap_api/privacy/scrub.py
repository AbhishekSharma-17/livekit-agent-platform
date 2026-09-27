"""The post-call privacy scrub (V5-30, P §4.2 C10): the ``session_scrub`` job.

``AgentConfig.privacy.storage_tier`` says what is kept once a session ends:

* ``full`` — everything, as before V5-30; no job is enqueued.
* ``redacted`` — the transcript, every session-event payload, the final UI
  state and a warm transfer's ``calls.transfer_summary`` (ask #209) are
  rewritten in place with emails, card numbers and long numbers
  masked (:mod:`lkap_api.privacy.redact`), then, when ``scrub_model`` is set
  and callable from the api, the transcript turns also go through the LLM pass
  (:mod:`lkap_api.privacy.llm`) for names, addresses and other details.
* ``basic`` — the same, the tool payloads are dropped from the events
  (``args_redacted``, ``result_preview``, ``message_preview``) and the transfer
  summary is cleared.

The job runs once per session: it records a ``privacy_scrubbed`` session event
(its ``ts`` is the ``scrubbed_at`` the console shows, no column and no
migration) and does nothing when that event exists, so a retry or a second
enqueue never pays for a second LLM pass. It reads in one short transaction,
calls the model with no transaction open, and writes in a second one.

What it does not touch, by design: the recording (``recording.retention_days``
owns it), ``sessions.variables`` and ``caller`` (webhook and flow data the
builder asked for), and the post-call fields (the values the builder asked
the judge to extract). The QA summary is masked when the verdict already
exists at scrub time; a verdict posted later is not (the worker scores its
own, unscrubbed, copy of the transcript).
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from fastapi import BackgroundTasks
from lkap_contracts.agent_config import AgentConfig, StorageTier
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.costs import config_for_session
from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Call, SessionEvent, SessionQa, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.kinds import SESSION_SCRUB
from lkap_api.jobs.registry import job
from lkap_api.jobs.service import JobsService
from lkap_api.logging import get_logger
from lkap_api.privacy.llm import resolve_scrub_model, scrub_texts
from lkap_api.privacy.redact import redact_text, scrub_value

__all__ = [
    "PRIVACY_SCRUBBED_EVENT",
    "TOOL_PAYLOAD_KEYS",
    "ModelPass",
    "ScrubResult",
    "enqueue_scrub",
    "enqueue_scrub_if_due",
    "handle_session_scrub",
    "scrub_due",
    "scrub_session",
    "scrubbed_at",
]

log = get_logger(__name__)

#: The session event the scrub records; its `ts` is the session's `scrubbed_at`.
PRIVACY_SCRUBBED_EVENT: Final[str] = "privacy_scrubbed"

#: The payload keys the `basic` tier drops, per event type (docs/CONTRACTS.md §7).
TOOL_PAYLOAD_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "tool_call_started": ("args_redacted",),
    "tool_call_ended": ("result_preview",),
    "tool_call_updated": ("message_preview",),
}

#: The events that carry a transcript turn's text (the LLM pass rewrites them too).
_TURN_EVENTS: Final[frozenset[str]] = frozenset({"user_turn", "agent_turn"})

#: Sessions the worker has finished with.
_FINISHED: Final[frozenset[str]] = frozenset({"ended", "failed"})

ModelPass = Literal["none", "done", "failed", "unsupported"]


@dataclass(slots=True)
class ScrubResult:
    """What :func:`scrub_session` did."""

    status: Literal["scrubbed", "already_scrubbed", "not_due", "missing"]
    tier: StorageTier | None = None
    counts: dict[str, int] = field(default_factory=dict)
    model_pass: ModelPass = "none"
    tool_payloads_dropped: int = 0


def scrub_due(config: AgentConfig | None) -> bool:
    """Whether a session run with ``config`` is scrubbed once it ends (``storage_tier != "full"``)."""
    return config is not None and config.privacy.storage_tier != "full"


async def enqueue_scrub(
    jobs: JobsService, session_id: str, *, background_tasks: BackgroundTasks | None = None
) -> str:
    """Enqueue the ``session_scrub`` job for ``session_id``; returns the job id."""
    return await jobs.enqueue(SESSION_SCRUB, {"session_id": session_id}, background_tasks=background_tasks)


async def enqueue_scrub_if_due(
    jobs: JobsService,
    session_id: str,
    config: AgentConfig | None,
    *,
    background_tasks: BackgroundTasks | None = None,
) -> str | None:
    """Enqueue the scrub when the session's config asks for one; the summary's post-commit hook.

    Call it after the summary transaction commits (the job opens its own
    connection, ask #40's rule), with the config the session ran.

    Returns:
        The job id, or ``None`` for a ``full`` tier (nothing to do).
    """
    if not scrub_due(config):
        return None
    return await enqueue_scrub(jobs, session_id, background_tasks=background_tasks)


async def scrubbed_at(db: AsyncSession, session_id: str) -> dt.datetime | None:
    """When the scrub rewrote ``session_id``, from its ``privacy_scrubbed`` event; ``None`` if never."""
    ts: dt.datetime | None = await db.scalar(
        select(SessionEvent.ts)
        .where(SessionEvent.session_id == session_id, SessionEvent.type == PRIVACY_SCRUBBED_EVENT)
        .order_by(SessionEvent.id)
        .limit(1)
    )
    return ts


@job(SESSION_SCRUB)
async def handle_session_scrub(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Job handler: scrub the session named in ``payload["session_id"]``."""
    await scrub_session(ctx, str(payload["session_id"]))


async def _load(db: AsyncSession, session_id: str) -> SessionRow | None:
    # A job carries only the session id; the row decides the workspace.
    return (
        await db.execute(
            select(SessionRow)
            .where(SessionRow.id == session_id)
            .execution_options(**{CROSS_WORKSPACE_OPTION: True})
        )
    ).scalar_one_or_none()


def _drop_tool_payload(event_type: str, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    keys = [key for key in TOOL_PAYLOAD_KEYS.get(event_type, ()) if key in payload]
    if not keys:
        return payload, 0
    return {k: v for k, v in payload.items() if k not in keys}, len(keys)


async def scrub_session(ctx: JobContext, session_id: str) -> ScrubResult:
    """Scrub one finished session according to its agent's ``privacy.storage_tier``.

    Args:
        ctx: The job context (``database``, ``vault``, ``http``).
        session_id: The ``sessions.id`` to scrub.

    Returns:
        What was done; ``not_due`` for a ``full`` tier or an unfinished session.
    """
    # ------------------------------------------------------------------ read (short)
    async with ctx.database.session() as db:
        row = await _load(db, session_id)
        if row is None:
            return ScrubResult(status="missing")
        if await scrubbed_at(db, session_id) is not None:
            return ScrubResult(status="already_scrubbed")
        config = await config_for_session(db, row)
        if config is None or not scrub_due(config) or row.status not in _FINISHED:
            return ScrubResult(status="not_due")
        privacy = config.privacy
        transcript: list[Any] = list(row.transcript or [])
        final_ui_state = row.final_ui_state
        events = [
            (event.id, event.type, dict(event.payload or {}))
            for event in (
                await db.execute(
                    select(SessionEvent)
                    .where(SessionEvent.session_id == session_id)
                    .order_by(SessionEvent.id)
                )
            ).scalars()
        ]
        model = None
        model_reason: str | None = None
        if privacy.scrub_model is not None:
            model, model_reason = await resolve_scrub_model(
                db, ctx.vault, ctx.http, privacy.scrub_model, workspace_id=row.workspace_id
            )

    # ------------------------------------------------------------------ deterministic pass
    counts: Counter[str] = Counter()
    new_transcript: list[Any] = [scrub_value(turn, counts) for turn in transcript]
    new_final_state = scrub_value(final_ui_state, counts) if final_ui_state is not None else None
    dropped = 0
    changed_events: dict[int, dict[str, Any]] = {}
    event_payloads: dict[int, tuple[str, dict[str, Any]]] = {}
    for event_id, event_type, payload in events:
        kept = payload
        if privacy.storage_tier == "basic":
            kept, removed = _drop_tool_payload(event_type, payload)
            dropped += removed
        masked = scrub_value(kept, counts)
        event_payloads[event_id] = (event_type, masked)
        if masked != payload:
            changed_events[event_id] = masked

    # ------------------------------------------------------------ optional LLM pass (no transaction open)
    model_pass: ModelPass = "none"
    if privacy.scrub_model is not None:
        if model is None:
            model_pass = "unsupported"
            log.info("privacy_scrub_model_unsupported", session_id=session_id, reason=model_reason)
        else:
            texts = [str(turn.get("text", "")) if isinstance(turn, dict) else "" for turn in new_transcript]
            masked_texts, error = await scrub_texts(model.llm, texts) if texts else ([], None)
            if masked_texts is None:
                model_pass = "failed"
                log.warning("privacy_scrub_model_failed", session_id=session_id, reason=error)
            else:
                model_pass = "done"
                rewrites = {old: new for old, new in zip(texts, masked_texts, strict=True) if old != new}
                for turn, text in zip(new_transcript, masked_texts, strict=True):
                    if isinstance(turn, dict):
                        turn["text"] = text
                for event_id, (event_type, payload) in event_payloads.items():
                    said = payload.get("text")
                    if event_type in _TURN_EVENTS and isinstance(said, str) and said in rewrites:
                        changed_events[event_id] = {**payload, "text": rewrites[said]}

    # ------------------------------------------------------------------ write
    async with ctx.database.session() as db:
        row = await _load(db, session_id)
        if row is None:
            return ScrubResult(status="missing")
        if await scrubbed_at(db, session_id) is not None:  # a concurrent run won
            return ScrubResult(status="already_scrubbed")
        row.transcript = new_transcript
        row.final_ui_state = new_final_state
        for event_id, payload in changed_events.items():
            event = await db.get(SessionEvent, event_id)
            if event is not None and event.session_id == session_id:
                event.payload = payload
        qa = await db.get(SessionQa, session_id)
        if qa is not None and qa.summary:
            qa.summary = redact_text(qa.summary, counts)
        # Ask #209: a warm transfer's summary (V5-32) is model-written text about the caller.
        calls = (
            await db.execute(
                select(Call)
                .where(Call.session_id == session_id, Call.transfer_summary.is_not(None))
                .execution_options(**{CROSS_WORKSPACE_OPTION: True})
            )
        ).scalars()
        for call in calls:
            summary = call.transfer_summary or ""
            call.transfer_summary = None if privacy.storage_tier == "basic" else redact_text(summary, counts)
        result = ScrubResult(
            status="scrubbed",
            tier=privacy.storage_tier,
            counts=dict(counts),
            model_pass=model_pass,
            tool_payloads_dropped=dropped,
        )
        db.add(
            SessionEvent(
                session_id=session_id,
                ts=utcnow(),
                type=PRIVACY_SCRUBBED_EVENT,
                payload={
                    "tier": result.tier,
                    "replaced": result.counts,
                    "model_pass": result.model_pass,
                    "tool_payloads_dropped": dropped,
                },
            )
        )
    # Counts and kinds only: never a scrubbed or original value.
    log.info(
        "session_scrubbed",
        session_id=session_id,
        tier=result.tier,
        replaced=result.counts,
        model_pass=model_pass,
        tool_payloads_dropped=dropped,
    )
    return result
