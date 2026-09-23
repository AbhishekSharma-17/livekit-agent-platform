"""`recording_finalize` job: fan the `recording.ready` webhook out (ask #41).

Runs on its own `AsyncSession` (via `JobContext.database`), a tick after
`recordings.finalize.schedule_finalize_job` wrote its row on the producer's
own transaction — see that module's docstring for why the two are split.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Session as SessionRow
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.kinds import RECORDING_FINALIZE
from lkap_api.jobs.registry import job
from lkap_api.logging import get_logger
from lkap_api.webhooks import emit
from lkap_api.webhooks.events import RECORDING_READY

log = get_logger(__name__)


@job(RECORDING_FINALIZE)
async def handle_recording_finalize(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Emit `recording.ready` for a session whose recording is now `ready`.

    A no-op (logged) when the session is gone or its recording did not end up
    `ready` (still active, or failed) — nothing else needs to run for those.
    """
    session_id = str(payload["session_id"])
    async with ctx.database.session() as db:
        # The job carries only the session id (deliberately cross-workspace).
        session = (
            await db.execute(
                select(SessionRow)
                .where(SessionRow.id == session_id)
                .execution_options(**{CROSS_WORKSPACE_OPTION: True})
            )
        ).scalar_one_or_none()
        if session is None:
            log.warning("recording_finalize_session_missing", session_id=session_id)
            return
        if session.recording_status != "ready":
            log.info(
                "recording_finalize_skipped",
                session_id=session_id,
                recording_status=session.recording_status,
            )
            return
        workspace_id = session.workspace_id
        egress_id = session.recording_egress_id
        duration_s = session.recording_duration_s

    await emit(
        ctx.database,
        ctx.jobs,
        workspace_id=workspace_id,
        event_type=RECORDING_READY,
        data={"session_id": session_id, "egress_id": egress_id, "duration_s": duration_s},
    )
    log.info("recording_ready_emitted", session_id=session_id)
