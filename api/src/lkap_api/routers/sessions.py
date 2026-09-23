"""Session history for the console: list, detail, recording playback, cost, QA (PLAN-V2 V2-12).

Workspace scoping (V2-02 pattern, closing ask #25's sessions half): every
handler takes ``ctx: AdminCtxDep`` and every query filters on
``ctx.workspace_id``; a session of another workspace is a 404, never a leak
through a different status code (`auth/roles.py::ROUTE_POLICY` already has a
`/v1/sessions` entry from V2-02's wave, so no policy change is needed here).

Importing this module also imports :mod:`lkap_api.recordings` (side effect:
registers the `egress_ended` finalize handler and the `recording_finalize`
job kind) and :mod:`lkap_api.qa` (registers `qa_scoring`) — the same
"a router main.py already includes is how a package's handlers get imported"
trick `routers/webhooks.py` uses for `lkap_api.qa` (`from lkap_api import qa
as _qa`).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal, cast

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import RedirectResponse
from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.api_models import (
    QaOut,
    RecordingOut,
    SessionDetailOut,
    SessionEventOut,
    SessionEventPage,
    SessionOut,
    SessionPage,
    TranscriptTurn,
)
from lkap_contracts.api_models import SessionLatency as SessionLatencyOut
from lkap_contracts.common import SessionChannel
from lkap_contracts.ui_protocol import UiState
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import qa as _qa  # noqa: F401 - registers the qa_scoring job handler
from lkap_api import (
    recordings,  # registers egress_ended/recording_finalize handlers; resolve_storage_row below
)
from lkap_api.auth.deps import WorkspaceContext
from lkap_api.costs import config_for_session, render_cost
from lkap_api.db.models import Agent, LiveKitConnection, SessionEvent, SessionQa, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import SessionCost as SessionCostRow
from lkap_api.deps import AdminCtxDep, DbDep, HttpClientDep, SettingsDep, VaultDep
from lkap_api.errors import ApiError, NotFoundError
from lkap_api.jobs.deps import JobsDep
from lkap_api.logging import get_logger
from lkap_api.qa.job import enqueue_for_session
from lkap_api.qa.resolve import resolve_judge
from lkap_api.settings import Settings
from lkap_api.storage.resolve import storage_from_config
from lkap_api.vault import Vault

log = get_logger(__name__)

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])

#: How long a freshly-minted recording playback URL is valid for.
_RECORDING_URL_TTL_S = 3600


class QaJudgeUnavailableError(ApiError):
    """409 — the resolved judge is not one the api process can call directly (R-V2-5/asks #43)."""

    status_code = 409
    code = "qa_judge_unavailable"


def _to_out(row: SessionRow, agent_name: str) -> SessionOut:
    return SessionOut(
        id=row.id,
        agent_id=row.agent_id,
        agent_name=agent_name,
        config_version=row.config_version,
        room_name=row.room_name,
        status=row.status,
        pipeline_mode=row.pipeline_mode,
        created_at=row.created_at,
        started_at=row.started_at,
        ended_at=row.ended_at,
        usage=row.usage,
        error=row.error,
        channel=cast(SessionChannel, row.channel),
        connection_id=row.connection_id,
        cost_usd=Decimal(str(row.cost_usd)) if row.cost_usd is not None else None,
        disposition=row.disposition,
        recording_status=cast(_RecordingStatus, row.recording_status),
    )


async def _load_scoped(db: AsyncSession, ctx: WorkspaceContext, session_id: str) -> SessionRow:
    """Load a session of the caller's workspace (soft-deleted rows still resolve by id).

    Raises:
        NotFoundError: Unknown id, or a session belonging to another workspace
            (the same status code either way — no existence leak).
    """
    row = await db.scalar(
        select(SessionRow).where(SessionRow.id == session_id, SessionRow.workspace_id == ctx.workspace_id)
    )
    if row is None:
        raise NotFoundError(f"unknown session '{session_id}'")
    return row


async def _session_agent(db: AsyncSession, row: SessionRow) -> Agent | None:
    """The session's agent, looked up inside the session's own workspace."""
    stmt = select(Agent).where(Agent.id == row.agent_id, Agent.workspace_id == row.workspace_id)
    return (await db.execute(stmt)).scalar_one_or_none()


_QaStatus = Literal["pending", "done", "failed", "skipped"]
_Sentiment = Literal["positive", "neutral", "negative"]
_ScoredBy = Literal["worker", "api"]
_RecordingStatus = Literal["none", "requested", "active", "ready", "failed"]


def _qa_out(row: SessionQa | None) -> QaOut | None:
    if row is None:
        return None
    return QaOut(
        status=cast(_QaStatus, row.status),
        score=row.score,
        sentiment=cast(_Sentiment, row.sentiment) if row.sentiment else None,
        tags=list(row.tags or []),
        summary=row.summary or None,
        scored_at=row.scored_at,
        model=row.model or None,
        scored_by=cast(_ScoredBy, row.scored_by) if row.scored_by else None,
    )


async def _recording_out(
    db: AsyncSession, vault: Vault, settings: Settings, row: SessionRow, config: AgentConfig | None
) -> RecordingOut:
    """Build the recording block, minting a fresh signed URL when one is playable.

    `error` (docs/v2/_asks.md V2-20-3) is carried through on every branch —
    including the "not ready" one, which is the common case for a `failed`
    recording — since it's the console Recording tab's only way to learn why.
    """
    recording_status = cast(_RecordingStatus, row.recording_status)
    if recording_status != "ready" or not row.recording_object_key or not row.connection_id:
        return RecordingOut(
            status=recording_status, duration_s=row.recording_duration_s, error=row.recording_error
        )
    connection = await db.scalar(
        select(LiveKitConnection).where(
            LiveKitConnection.id == row.connection_id, LiveKitConnection.workspace_id == row.workspace_id
        )
    )
    if connection is None:
        return RecordingOut(
            status=recording_status, duration_s=row.recording_duration_s, error=row.recording_error
        )
    storage_config_id = config.recording.storage_config_id if config is not None else None
    storage_row = await recordings.resolve_storage_row(
        db, workspace_id=row.workspace_id, storage_config_id=storage_config_id, connection=connection
    )
    if storage_row is None:
        # A recording that finished before its storage config was deleted, or a
        # (should-not-happen) local-only workspace — nothing playable to link to.
        return RecordingOut(
            status=recording_status, duration_s=row.recording_duration_s, error=row.recording_error
        )
    backend = storage_from_config(storage_row, vault, settings=settings)
    url = await backend.signed_url(row.recording_object_key, expires_in_s=_RECORDING_URL_TTL_S)
    return RecordingOut(
        status=recording_status,
        url=url,
        duration_s=row.recording_duration_s,
        expires_at=utcnow() + dt.timedelta(seconds=_RECORDING_URL_TTL_S),
        error=row.recording_error,
    )


@router.get(
    "",
    response_model=SessionPage,
    summary="List sessions",
    description="Session rows, newest first, filtered by agent/status/channel/connection/date range.",
)
async def list_sessions(
    db: DbDep,
    ctx: AdminCtxDep,
    agent_id: str | None = Query(default=None, description="Only sessions of this agent"),
    status: str | None = Query(default=None, description="created | active | ended | failed"),
    channel: str | None = Query(
        default=None, description="web | test | text | sip_in | sip_out | widget | api"
    ),
    connection_id: str | None = Query(default=None, description="Only sessions run on this connection"),
    # noqa: B008 below — ruff's fastapi-Query exemption for B008 does not
    # recognise `datetime` annotations, unlike `str`/`int`/`bool` (verified
    # in isolation); the call-in-default is correct FastAPI usage regardless.
    from_: dt.datetime | None = Query(  # noqa: B008
        default=None, alias="from", description="Only sessions created at/after this time"
    ),
    to: dt.datetime | None = Query(  # noqa: B008
        default=None, description="Only sessions created before this time"
    ),
    limit: int = Query(default=25, ge=1, le=200, description="Maximum rows to return"),
    offset: int = Query(default=0, ge=0, description="Rows to skip"),
) -> SessionPage:
    """Return a page of the workspace's (non-deleted) sessions with their agent names."""
    conditions = [SessionRow.workspace_id == ctx.workspace_id, SessionRow.deleted_at.is_(None)]
    if agent_id:
        conditions.append(SessionRow.agent_id == agent_id)
    if status:
        conditions.append(SessionRow.status == status)
    if channel:
        conditions.append(SessionRow.channel == channel)
    if connection_id:
        conditions.append(SessionRow.connection_id == connection_id)
    if from_ is not None:
        conditions.append(SessionRow.created_at >= from_)
    if to is not None:
        conditions.append(SessionRow.created_at < to)

    stmt = select(SessionRow, Agent.name).join(Agent, Agent.id == SessionRow.agent_id).where(*conditions)
    count_stmt = select(func.count()).select_from(SessionRow).where(*conditions)
    rows = (await db.execute(stmt.order_by(SessionRow.created_at.desc()).limit(limit).offset(offset))).all()
    total = (await db.execute(count_stmt)).scalar_one()
    return SessionPage(items=[_to_out(row, name) for row, name in rows], total=total)


@router.get(
    "/{session_id}",
    response_model=SessionDetailOut,
    summary="Get a session",
    description=("One session including its transcript, final UI state, recording, cost, latency and QA."),
)
async def get_session(
    session_id: str, db: DbDep, ctx: AdminCtxDep, vault: VaultDep, settings: SettingsDep
) -> SessionDetailOut:
    """Return a session with every v2 detail block populated."""
    row = await _load_scoped(db, ctx, session_id)
    agent = await _session_agent(db, row)
    base = _to_out(row, agent.name if agent else "")
    transcript = (
        [TranscriptTurn.model_validate(turn) for turn in row.transcript]
        if row.transcript is not None
        else None
    )
    final_state = UiState.model_validate(row.final_ui_state) if row.final_ui_state else None
    qa_row = await db.get(SessionQa, session_id)
    config = await config_for_session(db, row)
    persisted_costs = list(
        (await db.execute(select(SessionCostRow).where(SessionCostRow.session_id == session_id)))
        .scalars()
        .all()
    )
    cost = render_cost(row, persisted_costs, config)
    latency = SessionLatencyOut.model_validate(row.latency) if row.latency else SessionLatencyOut()
    recording = await _recording_out(db, vault, settings, row, config)
    return SessionDetailOut(
        **base.model_dump(),
        transcript=transcript,
        final_ui_state=final_state,
        caller=row.caller,
        recording=recording,
        cost=cost,
        latency=latency,
        qa=_qa_out(qa_row),
        variables=row.variables or {},
    )


@router.get(
    "/{session_id}/recording",
    status_code=status.HTTP_302_FOUND,
    summary="Redirect to a playable recording URL",
    description="302s to a freshly signed URL when the recording is `ready`; 404 otherwise.",
)
async def get_recording(
    session_id: str, db: DbDep, ctx: AdminCtxDep, vault: VaultDep, settings: SettingsDep
) -> Response:
    """Redirect to a signed recording URL.

    Raises:
        NotFoundError: No recording, not yet ready, or its storage config is gone.
    """
    row = await _load_scoped(db, ctx, session_id)
    config = await config_for_session(db, row)
    out = await _recording_out(db, vault, settings, row, config)
    if out.url is None:
        raise NotFoundError(f"session '{session_id}' has no playable recording")
    return RedirectResponse(out.url, status_code=status.HTTP_302_FOUND)


@router.post(
    "/{session_id}/qa",
    response_model=QaOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-score a session's QA",
    description=(
        "Schedules a fresh QA pass through the api's own judge call. 409 `qa_judge_unavailable` "
        "when the resolved judge is not one the api process can call directly (e.g. LiveKit "
        "Inference) — set `qa.model` to a vendor-key provider to re-score, or rely on the "
        "worker's own verdict from the original session (R-V2-5)."
    ),
)
async def rescore_session(
    session_id: str, db: DbDep, ctx: AdminCtxDep, http: HttpClientDep, vault: VaultDep, jobs: JobsDep
) -> QaOut:
    """Validate the judge is callable, then enqueue a re-score.

    Raises:
        NotFoundError: Unknown session or its agent is gone.
        QaJudgeUnavailableError: The resolved judge is not api-callable.
    """
    row = await _load_scoped(db, ctx, session_id)
    agent = await _session_agent(db, row)
    if agent is None:
        raise NotFoundError(f"unknown agent '{row.agent_id}'")
    config = AgentConfig.model_validate(agent.config)
    resolution, reason = await resolve_judge(db, vault, http, config, workspace_id=row.workspace_id)
    if resolution is None:
        raise QaJudgeUnavailableError(
            reason or "no judge model resolvable for this agent", details={"session_id": session_id}
        )
    # `enqueue_for_session` opens its own database connection to write the
    # `jobs` row; committing first avoids the SQLite "database is locked"
    # deadlock this codebase documents at every other job-after-a-write call
    # site (ask #40, `routers/webhooks.py::redeliver`).
    await db.commit()
    await enqueue_for_session(jobs, session_id)
    log.info("session_qa_rescore_requested", session_id=session_id, judge=resolution.model_label)
    return QaOut(status="pending")


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a session (soft)",
    description="Marks the session deleted; it drops out of the list but a direct fetch by id still works.",
)
async def delete_session(session_id: str, db: DbDep, ctx: AdminCtxDep) -> Response:
    """Soft-delete a session."""
    row = await _load_scoped(db, ctx, session_id)
    if row.deleted_at is None:
        row.deleted_at = utcnow()
        await db.flush()
        log.info("session_deleted", session_id=session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{session_id}/events",
    response_model=SessionEventPage,
    summary="List session events",
    description="The append-only worker timeline; poll with `after_id` for new rows.",
)
async def list_session_events(
    session_id: str,
    db: DbDep,
    ctx: AdminCtxDep,
    after_id: int | None = Query(default=None, description="Return events with a greater id"),
    limit: int = Query(default=200, ge=1, le=1000, description="Maximum rows to return"),
) -> SessionEventPage:
    """Return the event timeline of a session in insertion order."""
    await _load_scoped(db, ctx, session_id)
    stmt = select(SessionEvent).where(SessionEvent.session_id == session_id)
    count_stmt = select(func.count()).select_from(SessionEvent).where(SessionEvent.session_id == session_id)
    if after_id is not None:
        stmt = stmt.where(SessionEvent.id > after_id)
        count_stmt = count_stmt.where(SessionEvent.id > after_id)
    rows = (await db.execute(stmt.order_by(SessionEvent.id).limit(limit))).scalars().all()
    total = (await db.execute(count_stmt)).scalar_one()
    return SessionEventPage(
        items=[SessionEventOut(id=r.id, ts=r.ts, type=r.type, payload=r.payload) for r in rows],
        total=total,
    )
