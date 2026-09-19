"""Session history for the console: list, detail and the event timeline."""

from __future__ import annotations

from fastapi import APIRouter, Query
from lkap_contracts.api_models import (
    SessionDetailOut,
    SessionEventOut,
    SessionEventPage,
    SessionOut,
    SessionPage,
    TranscriptTurn,
)
from lkap_contracts.ui_protocol import UiState
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import Agent, SessionEvent
from lkap_api.db.models import Session as SessionRow
from lkap_api.deps import AdminDep, DbDep
from lkap_api.errors import NotFoundError

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


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
    )


async def _load(db: AsyncSession, session_id: str) -> SessionRow:
    row = await db.get(SessionRow, session_id)
    if row is None:
        raise NotFoundError(f"unknown session '{session_id}'")
    return row


@router.get(
    "",
    response_model=SessionPage,
    summary="List sessions",
    description="Session rows, newest first, optionally filtered by agent and status.",
)
async def list_sessions(
    db: DbDep,
    _admin: AdminDep,
    agent_id: str | None = Query(default=None, description="Only sessions of this agent"),
    status: str | None = Query(default=None, description="created | active | ended | failed"),
    limit: int = Query(default=50, ge=1, le=500, description="Maximum rows to return"),
    offset: int = Query(default=0, ge=0, description="Rows to skip"),
) -> SessionPage:
    """Return a page of sessions with their agent names."""
    stmt = select(SessionRow, Agent.name).join(Agent, Agent.id == SessionRow.agent_id)
    count_stmt = select(func.count()).select_from(SessionRow)
    if agent_id:
        stmt = stmt.where(SessionRow.agent_id == agent_id)
        count_stmt = count_stmt.where(SessionRow.agent_id == agent_id)
    if status:
        stmt = stmt.where(SessionRow.status == status)
        count_stmt = count_stmt.where(SessionRow.status == status)
    rows = (await db.execute(stmt.order_by(SessionRow.created_at.desc()).limit(limit).offset(offset))).all()
    total = (await db.execute(count_stmt)).scalar_one()
    return SessionPage(items=[_to_out(row, name) for row, name in rows], total=total)


@router.get(
    "/{session_id}",
    response_model=SessionDetailOut,
    summary="Get a session",
    description="One session including its final transcript and UI state, when the worker posted them.",
)
async def get_session(session_id: str, db: DbDep, _admin: AdminDep) -> SessionDetailOut:
    """Return a session with transcript and final UI state."""
    row = await _load(db, session_id)
    agent = await db.get(Agent, row.agent_id)
    base = _to_out(row, agent.name if agent else "")
    transcript = (
        [TranscriptTurn.model_validate(turn) for turn in row.transcript]
        if row.transcript is not None
        else None
    )
    final_state = UiState.model_validate(row.final_ui_state) if row.final_ui_state else None
    return SessionDetailOut(**base.model_dump(), transcript=transcript, final_ui_state=final_state)


@router.get(
    "/{session_id}/events",
    response_model=SessionEventPage,
    summary="List session events",
    description="The append-only worker timeline; poll with `after_id` for new rows.",
)
async def list_session_events(
    session_id: str,
    db: DbDep,
    _admin: AdminDep,
    after_id: int | None = Query(default=None, description="Return events with a greater id"),
    limit: int = Query(default=200, ge=1, le=1000, description="Maximum rows to return"),
) -> SessionEventPage:
    """Return the event timeline of a session in insertion order."""
    await _load(db, session_id)
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
