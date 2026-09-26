"""Session files follow the session's recording retention (V5-19).

A session's stored files are deleted, bytes first, once its agent's
``recording.retention_days`` has elapsed since the session ended (the same rule
:func:`lkap_api.sessions_sweep.sweep_recording_retention` applies to the
recording). ``retention_days=None`` keeps them, like the recording. The config is
the session's pinned version when it still exists, else the agent's current one.

:func:`retention_loop` runs beside the sessions sweep (started from ``main.py``'s
lifespan); folding :func:`sweep_session_assets` into ``sessions_sweep.sweep_loop``
is an open ask, since that module belongs to another package.
"""

from __future__ import annotations

import asyncio
import datetime as dt

from sqlalchemy import select

from lkap_api.costs import config_for_session
from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import SessionAsset, utcnow
from lkap_api.db.session import Database
from lkap_api.logging import get_logger
from lkap_api.session_assets.service import purge_session_assets
from lkap_api.settings import Settings
from lkap_api.storage.resolve import default_storage

__all__ = ["retention_loop", "sweep_session_assets"]

log = get_logger(__name__)


async def sweep_session_assets(db: Database, settings: Settings, *, now: dt.datetime | None = None) -> int:
    """Delete the files of every ended session whose recording retention has elapsed.

    Returns:
        How many files were removed this pass.
    """
    ts = now or utcnow()
    storage = default_storage(settings)
    removed = 0
    async with db.session() as session:
        session_ids = (
            (
                await session.execute(
                    select(SessionAsset.session_id)
                    .distinct()
                    # The retention sweep runs platform-wide, across every workspace.
                    .execution_options(**{CROSS_WORKSPACE_OPTION: True})
                )
            )
            .scalars()
            .all()
        )
        for session_id in session_ids:
            row = (
                await session.execute(
                    select(SessionRow)
                    .where(SessionRow.id == session_id)
                    .execution_options(**{CROSS_WORKSPACE_OPTION: True})
                )
            ).scalar_one_or_none()
            if row is None or row.ended_at is None:
                continue
            try:
                config = await config_for_session(session, row)
            except Exception:  # noqa: BLE001 - a malformed stored config must not kill the sweep
                continue
            retention_days = config.recording.retention_days if config is not None else None
            if not retention_days or ts < row.ended_at + dt.timedelta(days=retention_days):
                continue
            assets = (
                (
                    await session.execute(
                        select(SessionAsset).where(
                            SessionAsset.session_id == row.id, SessionAsset.workspace_id == row.workspace_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            removed += await purge_session_assets(session, storage, assets)
    if removed:
        log.info("session_assets_purged_by_retention", count=removed)
    return removed


async def retention_loop(db: Database, settings: Settings) -> None:
    """Run :func:`sweep_session_assets` forever, every ``LKAP_SESSION_SWEEP_INTERVAL_S`` seconds.

    A failed pass is logged and never ends the loop.
    """
    while True:
        try:
            await sweep_session_assets(db, settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a sweep failure must never kill the loop
            log.exception("session_assets_sweep_failed")
        await asyncio.sleep(settings.session_sweep_interval_s)
