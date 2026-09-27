"""The memory job kinds (V5-40): ``memory_remember`` after a session, ``memory_purge`` after a purge."""

from __future__ import annotations

from typing import Any

from fastapi import BackgroundTasks
from lkap_contracts.agent_config import AgentConfig

from lkap_api.jobs.context import JobContext
from lkap_api.jobs.kinds import MEMORY_PURGE, MEMORY_REMEMBER
from lkap_api.jobs.registry import job
from lkap_api.jobs.service import JobsService
from lkap_api.logging import get_logger
from lkap_api.memory.service import purge_subjects, remember_session

__all__ = [
    "enqueue_purge",
    "enqueue_remember_if_due",
    "handle_memory_purge",
    "handle_memory_remember",
    "remember_due",
]

log = get_logger(__name__)


def remember_due(config: AgentConfig | None) -> bool:
    """Whether a session run with ``config`` is written to memory once it ends."""
    return config is not None and config.memory.enabled


async def enqueue_remember_if_due(
    jobs: JobsService,
    session_id: str,
    config: AgentConfig | None,
    *,
    background_tasks: BackgroundTasks | None = None,
) -> str | None:
    """The summary's post-commit hook: enqueue ``memory_remember`` when memory is on.

    Call it after the summary transaction commits (the job opens its own connection).

    Returns:
        The job id, or ``None`` when memory is off (every agent saved before V5-40).
    """
    if not remember_due(config):
        return None
    return await jobs.enqueue(MEMORY_REMEMBER, {"session_id": session_id}, background_tasks=background_tasks)


async def enqueue_purge(
    jobs: JobsService,
    workspace_id: str,
    subjects: list[str],
    *,
    background_tasks: BackgroundTasks | None = None,
) -> str:
    """Enqueue the backend half of a workspace purge (after the purge's commit)."""
    return await jobs.enqueue(
        MEMORY_PURGE,
        {"workspace_id": workspace_id, "subject_ids": subjects},
        background_tasks=background_tasks,
    )


@job(MEMORY_REMEMBER)
async def handle_memory_remember(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Write a finished session to memory (once; a recorded ``memory_stored`` ends it)."""
    session_id = str(payload.get("session_id", ""))
    if not session_id:
        return
    await remember_session(ctx.database, ctx.vault, ctx.settings, session_id)


@job(MEMORY_PURGE)
async def handle_memory_purge(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Delete a purged workspace's backend entries (retried by the job engine on failure)."""
    raw = payload.get("subject_ids")
    subjects = [str(item) for item in raw] if isinstance(raw, list) else []
    deleted = await purge_subjects(ctx.settings, subjects)
    log.info(
        "memory_purge_done", workspace_id=payload.get("workspace_id"), subjects=len(subjects), deleted=deleted
    )
