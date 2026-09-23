"""Egress finalisation: duration/status + the outbox row that fans out `recording.ready`.

Two producers learn that a recording finished, both already inside an open
request transaction when they find out (ARCHITECTURE-V2 D-V2-16):

* the `egress_ended` LiveKit webhook (`routers/hooks.py` -> `dispatch_webhook`),
  whose built-in handler (`connections/webhooks.py`, V2-03) already sets
  `recording_status` from a binary complete/not-complete read of the event;
  :func:`_on_egress_ended_finalize` is a **second** handler for the same event
  name (the registry is a list per event, ask #23) that adds duration and the
  fuller starting/active/ending/complete/failed/aborted/limit-reached mapping,
  entirely from this package's own file.
* the worker's shutdown-time `POST /internal/v1/sessions/{id}/recording`
  (`routers/internal.py`, ask #49) — the fallback for deployments where
  LiveKit's webhook cannot reach this api (no `LKAP_PUBLIC_BASE_URL`).

Both call :func:`apply_egress_result` then :func:`schedule_finalize_job` on the
*same* `AsyncSession` they already hold. `schedule_finalize_job` never calls
`JobsService.enqueue` — that method opens its own database connection to
write the job row, and calling it while the caller's own transaction is still
open is exactly the SQLite "database is locked" deadlock `docs/v2/_asks.md`
#40 documents for the (structurally identical) session-summary path. Writing
the `Job` row directly on the caller's session instead makes it commit
atomically with the rest of the update; the inline poller (or an `arq`
worker) picks it up a tick later on its own connection, where
`recordings.job.handle_recording_finalize` calls `webhooks.emit` safely.
"""

from __future__ import annotations

from livekit.protocol.egress import EgressStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.connections.webhooks import WebhookContext, register_webhook_handler
from lkap_api.costs import add_egress_cost_line
from lkap_api.db.models import Job, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.jobs.kinds import RECORDING_FINALIZE
from lkap_api.logging import get_logger

log = get_logger(__name__)

#: LiveKit `EgressStatus` -> `sessions.recording_status` (mirrors the worker's
#: own `_EGRESS_STATUS` table in `agent/src/lkap_agent/main.py`, kept in sync
#: by hand since the api does not depend on `lkap_agent`).
_STATUS_MAP: dict[int, str] = {
    int(EgressStatus.EGRESS_STARTING): "active",
    int(EgressStatus.EGRESS_ACTIVE): "active",
    int(EgressStatus.EGRESS_ENDING): "active",
    int(EgressStatus.EGRESS_COMPLETE): "ready",
    int(EgressStatus.EGRESS_FAILED): "failed",
    int(EgressStatus.EGRESS_ABORTED): "failed",
    int(EgressStatus.EGRESS_LIMIT_REACHED): "ready",
}


def status_from_egress(value: int) -> str:
    """Map a raw `EgressStatus` int onto a `sessions.recording_status` value."""
    return _STATUS_MAP.get(value, "active")


def schedule_finalize_job(db: AsyncSession, session_id: str) -> None:
    """Insert a `recording_finalize` job row on `db` without opening a second connection."""
    db.add(
        Job(kind=RECORDING_FINALIZE, payload={"session_id": session_id}, status="pending", run_at=utcnow())
    )


async def apply_egress_result(
    db: AsyncSession,
    session: SessionRow,
    *,
    status: str,
    duration_s: float | None,
    error: str | None = None,
) -> None:
    """Update a session's recording state and, once `ready`, its egress cost line.

    Does **not** flush or schedule the finalize job — callers do both once,
    after deciding whether this call actually changed anything (idempotency
    lives in the callers: the webhook path always calls this, the worker's
    best-effort report may repeat it after a network retry).

    Args:
        error: Why the recording failed, when `status == "failed"`
            (docs/v2/_asks.md V2-20-3). Cleared on any other status so a
            stale reason from an earlier failed attempt never lingers on a
            since-recovered recording.
    """
    session.recording_status = status
    session.recording_error = error if status == "failed" else None
    if duration_s is not None:
        session.recording_duration_s = duration_s
    if status == "ready":
        await add_egress_cost_line(db, session)


@register_webhook_handler("egress_ended")
async def _on_egress_ended_finalize(ctx: WebhookContext) -> None:
    """Duration, cost line and finalize-job half of `egress_ended`."""
    info = ctx.event.egress_info
    if not info.egress_id:
        return
    session: SessionRow | None = await ctx.db.scalar(
        select(SessionRow).where(
            SessionRow.workspace_id == ctx.connection.workspace_id,
            SessionRow.recording_egress_id == info.egress_id,
        )
    )
    if session is None:
        return
    durations = [f.duration for f in info.file_results if f.duration]
    duration_s = max(durations) / 1e9 if durations else None
    await apply_egress_result(
        ctx.db, session, status=status_from_egress(int(info.status)), duration_s=duration_s
    )
    schedule_finalize_job(ctx.db, session.id)
    await ctx.db.flush()
