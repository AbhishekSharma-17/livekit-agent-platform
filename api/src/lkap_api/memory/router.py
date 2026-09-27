"""The console's memory routes (V5-40): forget a caller, purge the workspace, a session's memory.

``/v1/memory/*`` has no ``ROUTE_POLICY`` rule of its own, so it takes the
fail-closed default (``admin``, API-key scope ``*``); the session route sits
under ``/v1/sessions`` (``viewer`` / ``sessions:read``).
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks
from lkap_contracts.api_models import MemoryForgetOut, MemoryPurgeIn, MemoryPurgeOut, SessionMemoryOut
from sqlalchemy import select

from lkap_api.db.models import Session as SessionRow
from lkap_api.deps import AdminCtxDep, DbDep, SettingsDep
from lkap_api.errors import NotFoundError, UnprocessableEntityError
from lkap_api.jobs.deps import JobsDep
from lkap_api.logging import get_logger
from lkap_api.memory.jobs import enqueue_purge
from lkap_api.memory.service import forget_subject, purge_workspace, session_memory

__all__ = ["router"]

log = get_logger(__name__)

router = APIRouter(tags=["memory"])


@router.delete(
    "/v1/memory/subjects/{subject_id}",
    response_model=MemoryForgetOut,
    summary="Forget one caller",
    description=(
        "Deletes everything the memory holds about one caller of the workspace, in every agent's "
        "memory, and blanks the memories recorded on their sessions (each gets a "
        "`memory_forgotten` event). `subject_id` is the caller's pseudonymous id from "
        "`GET /v1/sessions/{id}/memory`. 404 for an id the workspace never had; 503 when the "
        "memory backend is unavailable (nothing is deleted then)."
    ),
)
async def forget_caller(
    subject_id: str, db: DbDep, ctx: AdminCtxDep, settings: SettingsDep
) -> MemoryForgetOut:
    """Forget one caller everywhere in the caller's workspace."""
    return await forget_subject(db, settings, ctx.workspace_id, subject_id)


@router.post(
    "/v1/memory/purge",
    response_model=MemoryPurgeOut,
    summary="Purge every caller memory of the workspace",
    description=(
        "Needs `confirm: true` (422 otherwise). Deletes the workspace's memory key and every "
        "caller record at once, so nothing stored can be tied to a caller again, blanks the "
        "memories recorded on sessions, then deletes the stored memories in the background "
        "(`job_id`). Cannot be undone."
    ),
)
async def purge_memories(
    payload: MemoryPurgeIn,
    db: DbDep,
    ctx: AdminCtxDep,
    jobs: JobsDep,
    background_tasks: BackgroundTasks,
) -> MemoryPurgeOut:
    """Purge the caller's workspace (after the commit, the backend half runs as a job)."""
    if not payload.confirm:
        raise UnprocessableEntityError("purging every caller memory needs confirm: true")
    out, subjects = await purge_workspace(db, ctx.workspace_id)
    if out.status == "nothing_to_purge":
        return out
    await db.commit()
    job_id = await enqueue_purge(jobs, ctx.workspace_id, subjects, background_tasks=background_tasks)
    return out.model_copy(update={"job_id": job_id})


@router.get(
    "/v1/sessions/{session_id}/memory",
    response_model=SessionMemoryOut,
    summary="What a session recalled and stored",
    description=(
        "The memories the session started with and the ones written after it, the caller's "
        "pseudonymous id (for `DELETE /v1/memory/subjects/{subject_id}`), and when they were "
        "forgotten. Empty lists when memory was off or the caller was anonymous."
    ),
)
async def get_session_memory(session_id: str, db: DbDep, ctx: AdminCtxDep) -> SessionMemoryOut:
    """A session's memory, scoped to the caller's workspace."""
    row = await db.scalar(
        select(SessionRow).where(SessionRow.id == session_id, SessionRow.workspace_id == ctx.workspace_id)
    )
    if row is None:
        raise NotFoundError(f"unknown session '{session_id}'")
    return await session_memory(db, row)
