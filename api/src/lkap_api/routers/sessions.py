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

V5-30 adds the post-call fields (`QaOut.fields`, the CSV export's columns), the
privacy scrub (`scrubbed_at`, `POST .../scrub`) and, on delete, the removal of
the session's stored files and recording (S5-36).

V5-37 adds the supervisor listen-in: ``POST .../listen-token`` (a hidden,
subscribe-only room token) and ``POST .../whisper`` (written guidance sent to
the room's agent with the server API, never to the caller). Both need the
``sessions:listen`` scope (``builder``+; ``sessions:write`` implies it) and
each call writes its own audit row (``session.listen`` / ``session.whisper``).
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
from decimal import Decimal
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from fastapi.responses import RedirectResponse
from livekit.api import ListParticipantsRequest, SendDataRequest
from livekit.protocol.models import DataPacket, ParticipantInfo
from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.api_models import (
    MAX_SUPERVISOR_LABEL_CHARS,
    SUPERVISOR_TOPIC,
    QaOut,
    RecordingOut,
    SessionDetailOut,
    SessionEventOut,
    SessionEventPage,
    SessionListenTokenOut,
    SessionOut,
    SessionPage,
    SessionScrubOut,
    SessionWhisperIn,
    SessionWhisperOut,
    SupervisorWhisperPacket,
    TranscriptTurn,
)
from lkap_contracts.api_models import SessionLatency as SessionLatencyOut
from lkap_contracts.common import SessionChannel
from lkap_contracts.ui_protocol import UiState
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import (
    privacy,  # registers the privacy validator and the session_scrub handler
    recordings,  # registers egress_ended/recording_finalize handlers; resolve_storage_row below
)
from lkap_api import qa as _qa  # noqa: F401 - registers the qa_scoring job handler
from lkap_api.auth import audit
from lkap_api.auth.deps import WorkspaceContext, require
from lkap_api.connections.clients import ClientFactoryDep
from lkap_api.connections.service import get_connection
from lkap_api.costs import config_for_session, cost_context, render_cost
from lkap_api.db.models import Agent, LiveKitConnection, SessionEvent, SessionQa, new_id, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import SessionCost as SessionCostRow
from lkap_api.deps import AdminCtxDep, DbDep, HttpClientDep, SettingsDep, VaultDep
from lkap_api.errors import ApiError, ConflictError, NotFoundError
from lkap_api.jobs.deps import JobsDep
from lkap_api.livekit_tokens import LISTEN_TOKEN_TTL, mint_listener_token, supervisor_identity
from lkap_api.logging import get_logger
from lkap_api.privacy.fields import csv_cell, qa_fields
from lkap_api.qa.job import enqueue_for_session
from lkap_api.qa.resolve import resolve_judge
from lkap_api.settings import Settings
from lkap_api.storage.resolve import storage_from_config
from lkap_api.telephony.common import raise_upstream
from lkap_api.vault import Vault

log = get_logger(__name__)

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])

#: V5-37: listening in and whispering (`auth/roles.py::ROUTE_POLICY` has the same rule).
ListenCtxDep = Annotated[WorkspaceContext, Depends(require("builder", "sessions:listen"))]

#: How long a freshly-minted recording playback URL is valid for.
_RECORDING_URL_TTL_S = 3600

#: V5-30: the most rows one CSV export returns (newest first).
MAX_EXPORT_ROWS = 5000

#: The CSV export's fixed columns; one column per post-call field follows them.
EXPORT_COLUMNS: tuple[str, ...] = (
    "session_id",
    "agent_id",
    "agent_name",
    "channel",
    "status",
    "created_at",
    "ended_at",
    "duration_s",
    "cost_usd",
    "disposition",
    "qa_status",
    "qa_score",
    "qa_sentiment",
)


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
        estimated_usd=Decimal(str(row.estimated_usd)) if row.estimated_usd is not None else None,
        reconciled_usd=Decimal(str(row.reconciled_usd)) if row.reconciled_usd is not None else None,
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
        fields=qa_fields(row.raw),
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
    conditions = _session_filters(
        ctx,
        agent_id=agent_id,
        status=status,
        channel=channel,
        connection_id=connection_id,
        from_=from_,
        to=to,
    )

    stmt = select(SessionRow, Agent.name).join(Agent, Agent.id == SessionRow.agent_id).where(*conditions)
    count_stmt = select(func.count()).select_from(SessionRow).where(*conditions)
    rows = (await db.execute(stmt.order_by(SessionRow.created_at.desc()).limit(limit).offset(offset))).all()
    total = (await db.execute(count_stmt)).scalar_one()
    return SessionPage(items=[_to_out(row, name) for row, name in rows], total=total)


def _session_filters(
    ctx: WorkspaceContext,
    *,
    agent_id: str | None,
    status: str | None,
    channel: str | None,
    connection_id: str | None,
    from_: dt.datetime | None,
    to: dt.datetime | None,
) -> list[Any]:
    """The `WHERE` terms shared by the list and the CSV export (non-deleted, this workspace)."""
    conditions: list[Any] = [SessionRow.workspace_id == ctx.workspace_id, SessionRow.deleted_at.is_(None)]
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
    return conditions


async def _field_columns(
    db: AsyncSession, ctx: WorkspaceContext, agent_id: str | None, qa_rows: list[SessionQa | None]
) -> list[str]:
    """The post-call field columns: the agent's current fields in order, then any older ones seen."""
    names: list[str] = []
    if agent_id:
        agent = await db.scalar(
            select(Agent).where(Agent.id == agent_id, Agent.workspace_id == ctx.workspace_id)
        )
        if agent is not None:
            try:
                names = [field.name for field in AgentConfig.model_validate(agent.config).qa.fields]
            except ValueError:
                names = []
    seen = {key for qa in qa_rows if qa is not None for key in qa_fields(qa.raw)}
    return names + sorted(seen - set(names))


def _duration_s(row: SessionRow) -> float | None:
    start = row.started_at or row.created_at
    if row.ended_at is None or start is None:
        return None
    return round((row.ended_at - start).total_seconds(), 1)


@router.get(
    "/export.csv",
    response_class=Response,
    summary="Export sessions as CSV",
    description=(
        "The same filters as the list, newest first, at most 5,000 rows, as a CSV file: the session, "
        "agent, channel, status, times, cost, disposition and QA score, then one column per post-call "
        "field (V5-30; the agent's current fields first when `agent_id` is given, then any other field "
        "found in the rows). A text cell that would start a spreadsheet formula gets a leading `'`."
    ),
    responses={200: {"content": {"text/csv": {}}, "description": "The CSV file."}},
)
async def export_sessions_csv(
    db: DbDep,
    ctx: AdminCtxDep,
    agent_id: str | None = Query(default=None, description="Only sessions of this agent"),
    status: str | None = Query(default=None, description="created | active | ended | failed"),
    channel: str | None = Query(
        default=None, description="web | test | text | sip_in | sip_out | widget | api"
    ),
    connection_id: str | None = Query(default=None, description="Only sessions run on this connection"),
    from_: dt.datetime | None = Query(  # noqa: B008 - see list_sessions
        default=None, alias="from", description="Only sessions created at/after this time"
    ),
    to: dt.datetime | None = Query(  # noqa: B008 - see list_sessions
        default=None, description="Only sessions created before this time"
    ),
    limit: int = Query(default=1000, ge=1, le=MAX_EXPORT_ROWS, description="Maximum rows to return"),
) -> Response:
    """Return the workspace's (non-deleted) sessions as a CSV attachment."""
    conditions = _session_filters(
        ctx,
        agent_id=agent_id,
        status=status,
        channel=channel,
        connection_id=connection_id,
        from_=from_,
        to=to,
    )
    stmt = (
        select(SessionRow, Agent.name, SessionQa)
        .join(Agent, Agent.id == SessionRow.agent_id)
        .outerjoin(SessionQa, SessionQa.session_id == SessionRow.id)
        .where(*conditions)
        .order_by(SessionRow.created_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    field_names = await _field_columns(db, ctx, agent_id, [qa for _, _, qa in rows])
    headers = [*EXPORT_COLUMNS, *(f"field_{n}" if n in EXPORT_COLUMNS else n for n in field_names)]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row, agent_name, qa in rows:
        values = qa_fields(qa.raw) if qa is not None else {}
        writer.writerow(
            [
                csv_cell(row.id),
                csv_cell(row.agent_id),
                csv_cell(agent_name),
                csv_cell(row.channel),
                csv_cell(row.status),
                csv_cell(row.created_at.isoformat() if row.created_at else None),
                csv_cell(row.ended_at.isoformat() if row.ended_at else None),
                csv_cell(_duration_s(row)),
                csv_cell(row.cost_usd),
                csv_cell(row.disposition),
                csv_cell(qa.status if qa is not None else None),
                csv_cell(qa.score if qa is not None else None),
                csv_cell(qa.sentiment if qa is not None else None),
                *(csv_cell(values.get(name)) for name in field_names),
            ]
        )
    log.info("sessions_exported", rows=len(rows), field_columns=len(field_names))
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="sessions.csv"', "Cache-Control": "no-store"},
    )


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
    cost = render_cost(row, persisted_costs, config, await cost_context(db, row, config))
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
        scrubbed_at=await privacy.scrubbed_at(db, session_id),
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


@router.post(
    "/{session_id}/scrub",
    response_model=SessionScrubOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Scrub a session now",
    description=(
        "Queues the post-call privacy scrub (V5-30) for a finished session of an agent whose "
        "`privacy.storage_tier` is `redacted` or `basic`: emails, card numbers and long numbers are "
        "masked in the transcript, events and final panel state (and names and addresses when a "
        "cleanup model is set); `basic` also drops tool arguments and results. It normally runs by "
        "itself when the session ends; this is for sessions that ended before the tier was chosen. "
        "Runs once per session (`already_scrubbed` afterwards). 409 when the agent keeps sessions in "
        "full or the session has not ended."
    ),
)
async def scrub_session_now(
    session_id: str, db: DbDep, ctx: AdminCtxDep, jobs: JobsDep, background_tasks: BackgroundTasks
) -> SessionScrubOut:
    """Enqueue the scrub of one session.

    Raises:
        NotFoundError: Unknown session.
        ConflictError: 409 for a `full` tier or a session that has not ended.
    """
    row = await _load_scoped(db, ctx, session_id)
    done_at = await privacy.scrubbed_at(db, session_id)
    if done_at is not None:
        return SessionScrubOut(status="already_scrubbed", scrubbed_at=done_at)
    config = await config_for_session(db, row)
    if not privacy.scrub_due(config):
        raise ConflictError(
            "this agent keeps sessions in full. Choose 'redacted' or 'basic' in its privacy settings first",
            details={"session_id": session_id, "reason": "storage_tier_full"},
        )
    if row.status not in ("ended", "failed"):
        raise ConflictError(
            "the session has not ended yet", details={"session_id": session_id, "reason": "not_ended"}
        )
    # The job opens its own connection (ask #40): nothing of this request is pending, but commit anyway.
    await db.commit()
    job_id = await privacy.enqueue_scrub(jobs, session_id, background_tasks=background_tasks)
    log.info("session_scrub_requested", session_id=session_id)
    return SessionScrubOut(status="queued", job_id=job_id)


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a session (soft)",
    description=(
        "Marks the session deleted; it drops out of the list but a direct fetch by id still works. "
        "Its stored files and its recording are removed at once (V5-30), not at the end of the "
        "recording retention."
    ),
)
async def delete_session(
    session_id: str, db: DbDep, ctx: AdminCtxDep, settings: SettingsDep, vault: VaultDep
) -> Response:
    """Soft-delete a session and remove its files and recording (S5-36)."""
    row = await _load_scoped(db, ctx, session_id)
    if row.deleted_at is None:
        row.deleted_at = utcnow()
        await db.flush()
        log.info("session_deleted", session_id=session_id)
    # Also on a repeated delete: a purge that failed the first time is retried.
    await privacy.purge_session_files(db, settings, vault, row)
    await db.flush()
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


# ------------------------------------------------------------ listen-in (V5-37)
class SessionNotLiveError(ApiError):
    """409 — the session has no live room to listen to (not active, or no connection)."""

    status_code = 409
    code = "not_live"


class NoAgentInRoomError(ApiError):
    """409 — no agent participant is in the session's room to receive a whisper."""

    status_code = 409
    code = "no_agent"


async def _live_connection(db: AsyncSession, row: SessionRow) -> LiveKitConnection:
    """The connection of an active session.

    Raises:
        SessionNotLiveError: The session is not ``active`` or has no connection.
    """
    if row.status != "active":
        raise SessionNotLiveError(
            "the session is not live", details={"session_id": row.id, "status": row.status}
        )
    if not row.connection_id:
        raise SessionNotLiveError(
            "the session has no LiveKit connection", details={"session_id": row.id, "status": row.status}
        )
    return await get_connection(db, row.workspace_id, row.connection_id)


def _supervisor_label(ctx: WorkspaceContext) -> str:
    """A display label for the caller: a member's name, an API key's name, else ``Supervisor``."""
    actor = ctx.actor
    name = ""
    if actor.user is not None:
        name = actor.user.name
    elif actor.api_key is not None:
        name = actor.api_key.name
    return (name.strip() or "Supervisor")[:MAX_SUPERVISOR_LABEL_CHARS]


def _audit_listen(
    db: AsyncSession, ctx: WorkspaceContext, action: str, row: SessionRow, **payload: Any
) -> None:
    """One audit row per listen or whisper; identifiers only, never the whisper's text."""
    audit.record(
        db,
        workspace_id=ctx.workspace_id,
        actor_type=ctx.actor.actor_type,
        actor_id=ctx.actor.id,
        action=action,
        target_type="session",
        target_id=row.id,
        payload={"room": row.room_name, **payload},
    )


@router.post(
    "/{session_id}/listen-token",
    response_model=SessionListenTokenOut,
    summary="Listen in to a live session",
    description=(
        "Mints a LiveKit token that joins only this session's room, hidden from the caller and the "
        "agent, able to hear the room but never to publish audio, video or data; it expires after 15 "
        "minutes. Needs the `sessions:listen` scope (`builder` or above; `sessions:write` implies it). "
        "409 `not_live` unless the session is active. Every call is audit-logged (`session.listen`)."
    ),
)
async def listen_token(
    session_id: str, db: DbDep, ctx: ListenCtxDep, factory: ClientFactoryDep
) -> SessionListenTokenOut:
    """Mint a hidden, listen-only token for the session's room.

    Raises:
        NotFoundError: Unknown session (or another workspace's).
        SessionNotLiveError: The session is not active or has no connection.
    """
    row = await _load_scoped(db, ctx, session_id)
    conn = await _live_connection(db, row)
    creds = factory.credentials(conn)
    identity = supervisor_identity(ctx.actor.id)
    label = _supervisor_label(ctx)
    token = mint_listener_token(
        api_key=creds.api_key,
        api_secret=creds.api_secret,
        room_name=row.room_name,
        identity=identity,
        participant_name=label,
        ttl=LISTEN_TOKEN_TTL,
    )
    expires_at = utcnow() + LISTEN_TOKEN_TTL
    _audit_listen(db, ctx, "session.listen", row, identity=identity, expires_at=expires_at.isoformat())
    await db.flush()
    log.info("session_listen_token_minted", session_id=row.id, identity=identity)
    return SessionListenTokenOut(
        serverUrl=creds.url,
        participantToken=token,
        roomName=row.room_name,
        participantName=label,
        identity=identity,
        sessionId=row.id,
        expiresAt=expires_at,
    )


@router.post(
    "/{session_id}/whisper",
    response_model=SessionWhisperOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Whisper to a live session's agent",
    description=(
        "Sends written guidance to the agent of a live session. Only the agent receives it (the "
        "caller never hears or sees it); the agent treats it as a supervisor's note that shapes its "
        "next reply, or speaks at once with `reply_now`. Needs `sessions:listen`. 409 `not_live` "
        "unless the session is active, 409 `no_agent` when no agent is in the room. Audit-logged "
        "(`session.whisper`, without the text); the worker records a `supervisor_whisper` event."
    ),
)
async def whisper(
    session_id: str, body: SessionWhisperIn, db: DbDep, ctx: ListenCtxDep, factory: ClientFactoryDep
) -> SessionWhisperOut:
    """Hand a whisper to the room's agent participants with the server API.

    Raises:
        NotFoundError: Unknown session (or another workspace's).
        SessionNotLiveError: The session is not active or has no connection.
        NoAgentInRoomError: No agent participant is in the room.
    """
    row = await _load_scoped(db, ctx, session_id)
    conn = await _live_connection(db, row)
    packet = SupervisorWhisperPacket(
        id=new_id(), session_id=row.id, text=body.text, reply_now=body.reply_now, by=_supervisor_label(ctx)
    )
    agents: list[str] = []
    try:
        async with factory.api(conn) as lk:
            listing = await lk.room.list_participants(ListParticipantsRequest(room=row.room_name))
            agents = [p.identity for p in listing.participants if p.kind == ParticipantInfo.Kind.AGENT]
            if agents:
                await lk.room.send_data(
                    SendDataRequest(
                        room=row.room_name,
                        data=packet.model_dump_json().encode(),
                        kind=DataPacket.Kind.RELIABLE,
                        topic=SUPERVISOR_TOPIC,
                        destination_identities=agents,
                    )
                )
    except Exception as exc:  # noqa: BLE001 - mapped to an api error
        raise_upstream(exc, action="sending the whisper")
    if not agents:
        raise NoAgentInRoomError(
            "no agent is in the room to receive the whisper", details={"session_id": row.id}
        )
    _audit_listen(
        db,
        ctx,
        "session.whisper",
        row,
        whisper_id=packet.id,
        chars=len(body.text),
        sha256=hashlib.sha256(body.text.encode()).hexdigest()[:16],
        reply_now=body.reply_now,
    )
    await db.flush()
    log.info(
        "session_whisper_sent",
        session_id=row.id,
        whisper_id=packet.id,
        chars=len(body.text),
        agents=len(agents),
        reply_now=body.reply_now,
    )
    return SessionWhisperOut(id=packet.id, delivered_to=len(agents))
