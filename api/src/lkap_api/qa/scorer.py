"""Score one finished session against its `AgentConfig.qa` rubric.

JSON output is validated with Pydantic; a first malformed response gets one
repair retry (feeding the bad output back to the judge) before giving up —
this is the fix for the truncated/malformed-JSON fragility
`docs/research-v2/dograh.md` §3.6 reports against Dograh's own `qa` node on
Gemini models. Nothing here ever raises: every outcome, including "no judge
model configured" or "malformed JSON twice", is persisted as a
`session_qa` row (`status="done"` or `"failed"`), matching the
`kb.ingest.ingest_into_session` "never raise, always finish the row" contract.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from lkap_contracts.agent_config import AgentConfig, effective_qa
from lkap_contracts.qa import QaVerdict
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Agent, SessionQa, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.jobs.context import JobContext
from lkap_api.logging import get_logger
from lkap_api.qa.llm_client import JudgeLLM
from lkap_api.qa.resolve import resolve_judge
from lkap_api.qa.rubric import build_prompt

log = get_logger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```\s*$", re.MULTILINE)

#: Re-exported for callers that imported the api-local name before this
#: moved to `lkap_contracts.qa` (V2-08 follow-up, R-V2-5: worker and api
#: share one verdict schema).
QaResult = QaVerdict


@dataclass(slots=True, frozen=True)
class QaOutcome:
    """What `_run_scoring` decided to persist; `score_session` never inspects more than this."""

    action: Literal["skip", "done", "failed"]
    score: int | None = None
    sentiment: str | None = None
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    raw: dict[str, Any] | None = None
    model: str = ""
    error: str | None = None


def render_transcript(turns: list[Any]) -> str:
    """Render `sessions.transcript` (a list of `TranscriptTurn`-shaped dicts) as plain text."""
    lines: list[str] = []
    for turn in turns:
        if isinstance(turn, dict):
            role, text = turn.get("role", "?"), turn.get("text", "")
        else:
            role, text = getattr(turn, "role", "?"), getattr(turn, "text", "")
        lines.append(f"{role}: {text}")
    return "\n".join(lines) if lines else "(empty transcript)"


def parse_qa_result(raw_text: str) -> QaResult:
    """Parse a judge response into a `QaResult`, tolerating ```` ```json ```` fences.

    Raises:
        json.JSONDecodeError: If the (fence-stripped) text is not valid JSON.
        pydantic.ValidationError: If it is valid JSON but the wrong shape.
    """
    cleaned = _FENCE_RE.sub("", raw_text).strip()
    data = json.loads(cleaned)
    return QaResult.model_validate(data)


async def _complete_with_repair(
    llm: JudgeLLM, system: str, user: str
) -> tuple[QaResult | None, str, str | None]:
    """Call the judge, repairing a malformed JSON response once.

    Returns:
        `(result, last_raw_response, error)` — `result` is `None` iff both the
        first call and the repair call failed to parse, or either HTTP call
        itself failed; this function never raises.
    """
    try:
        raw = await llm.complete(system=system, user=user)
    except Exception as exc:  # noqa: BLE001 - judge-call failure, never raised
        return None, "", f"judge call failed: {exc}"[:500]
    try:
        return parse_qa_result(raw), raw, None
    except (json.JSONDecodeError, ValidationError) as exc:
        first_error = str(exc)

    repair_user = (
        f"{user}\n\nYour previous response could not be parsed as the requested JSON object "
        f"(error: {first_error}). Here is exactly what you returned:\n\n{raw}\n\n"
        "Return ONLY the corrected JSON object, no markdown fences, no commentary."
    )
    try:
        repaired = await llm.complete(system=system, user=repair_user)
    except Exception as exc:  # noqa: BLE001 - judge-call failure, never raised
        return None, raw, f"repair call failed: {exc}"[:500]
    try:
        return parse_qa_result(repaired), repaired, None
    except (json.JSONDecodeError, ValidationError) as exc:
        return None, repaired, f"malformed JSON after repair: {exc}"[:500]


async def _run_scoring(session: AsyncSession, ctx: JobContext, session_id: str) -> QaOutcome:
    # A job carries only the session id; the row decides the workspace.
    row = (
        await session.execute(
            select(SessionRow)
            .where(SessionRow.id == session_id)
            .execution_options(**{CROSS_WORKSPACE_OPTION: True})
        )
    ).scalar_one_or_none()
    if row is None:
        log.warning("qa_session_missing", session_id=session_id)
        return QaOutcome(action="skip")
    agent = (
        await session.execute(
            select(Agent).where(Agent.id == row.agent_id, Agent.workspace_id == row.workspace_id)
        )
    ).scalar_one_or_none()
    if agent is None:
        return QaOutcome(action="failed", error="agent not found")
    try:
        config = AgentConfig.model_validate(agent.config)
    except ValidationError as exc:
        return QaOutcome(action="failed", error=f"invalid agent config: {exc}"[:500])
    # R-V2-11 (asks V2-16-4): a flow `qa` node turns QA on and may carry its own rubric.
    qa = effective_qa(config)
    if not qa.enabled:
        log.debug("qa_disabled", session_id=session_id)
        return QaOutcome(action="skip")

    resolution, reason = await resolve_judge(
        session, ctx.vault, ctx.http, config, workspace_id=row.workspace_id
    )
    if resolution is None:
        return QaOutcome(action="failed", error=reason or "no judge resolvable")

    transcript_text = render_transcript(row.transcript or [])
    system, user = build_prompt(rubric_prompt=qa.rubric_prompt, transcript_text=transcript_text)
    result, raw_text, error = await _complete_with_repair(resolution.llm, system, user)
    if result is None:
        return QaOutcome(
            action="failed",
            model=resolution.model_label,
            error=error,
            raw={"last_response": raw_text} if raw_text else None,
        )
    return QaOutcome(
        action="done",
        score=result.score,
        sentiment=result.sentiment,
        tags=result.tags,
        summary=result.summary,
        raw=result.model_dump(),
        model=resolution.model_label,
    )


async def _persist(session: AsyncSession, session_id: str, outcome: QaOutcome) -> None:
    if outcome.action == "skip":
        return
    qa = await session.get(SessionQa, session_id)
    if qa is None:
        qa = SessionQa(session_id=session_id)
        session.add(qa)
    qa.scored_by = "api"  # this module only ever runs a re-score (R-V2-5); the worker sets "worker"
    if outcome.action == "done":
        qa.status = "done"
        qa.score = outcome.score
        qa.sentiment = outcome.sentiment
        qa.tags = outcome.tags
        qa.summary = outcome.summary
        qa.raw = outcome.raw
        qa.model = outcome.model
        qa.scored_at = utcnow()
        qa.error = None
    else:
        qa.status = "failed"
        qa.error = outcome.error
        qa.raw = outcome.raw
        if outcome.model:
            qa.model = outcome.model


async def score_session(ctx: JobContext, session_id: str) -> None:
    """Score `session_id` and write its `session_qa` row. Never raises.

    Args:
        ctx: The job context (`database`, `vault`, `http`).
        session_id: The `sessions.id` to score.
    """
    async with ctx.database.session() as session:
        try:
            outcome = await _run_scoring(session, ctx, session_id)
        except Exception as exc:  # noqa: BLE001 - the qa job must never raise or retry forever
            log.warning("qa_scoring_unexpected_error", session_id=session_id, error_type=type(exc).__name__)
            outcome = QaOutcome(action="failed", error=str(exc)[:500])
        await _persist(session, session_id, outcome)

    if outcome.action == "done":
        log.info("qa_scored", session_id=session_id, score=outcome.score, sentiment=outcome.sentiment)
    elif outcome.action == "failed":
        log.warning("qa_scoring_failed", session_id=session_id, error=outcome.error)
