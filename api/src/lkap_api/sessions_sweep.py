"""Stale-session sweep (DECISIONS-W2 D-W2-2(b)) and recording retention (PLAN-V2 V2-12).

A token can be minted (`POST /connect` creates a `created` row) and never used
(the browser closed before joining, the worker crashed before posting a
summary). Left alone those rows would stay `created`/`active` forever, so a
background loop periodically marks them `failed`:

* `created` older than `LKAP_SESSION_STALE_CREATED_S` (default 600s) → `failed`,
  ``error="never started"``.
* `active` (``started_at`` set) older than `LKAP_SESSION_STALE_ACTIVE_S`
  (default 21600s) → `failed`, ``error="summary never received"``.

A later `PUT /internal/v1/sessions/{id}/summary` for a swept row still
overwrites status/usage/transcript unconditionally (the worker is the
authority when it does show up) — see `routers/internal.py::put_summary`,
which never guards on the current status.

The same loop also purges recordings past `AgentConfig.recording.retention_days`
(:func:`sweep_recording_retention`) — `main.py` (frozen, PLAN-V2 §"Exclusive
ownership") already starts :func:`sweep_loop` with exactly `(db, settings)`,
so retention rides along here rather than needing its own ticker wired in a
file this package does not own.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any, cast

from lkap_contracts.agent_config import AgentConfig
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult

from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Agent, LiveKitConnection, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.logging import get_logger
from lkap_api.settings import Settings
from lkap_api.storage.resolve import storage_from_config
from lkap_api.vault import Vault

log = get_logger(__name__)

NEVER_STARTED = "never started"
SUMMARY_NEVER_RECEIVED = "summary never received"


async def sweep_once(db: Database, settings: Settings, *, now: dt.datetime | None = None) -> tuple[int, int]:
    """Run one sweep pass, marking stale rows `failed`.

    Args:
        db: The process database.
        settings: Settings carrying the `LKAP_SESSION_*` thresholds.
        now: Injected clock for tests; defaults to the real current time.

    Returns:
        `(created_swept, active_swept)` — the number of rows each rule updated.
    """
    ts = now or utcnow()
    created_cutoff = ts - dt.timedelta(seconds=settings.session_stale_created_s)
    active_cutoff = ts - dt.timedelta(seconds=settings.session_stale_active_s)

    async with db.session() as session:
        created_result = await session.execute(
            update(SessionRow)
            .where(SessionRow.status == "created", SessionRow.created_at < created_cutoff)
            .values(status="failed", error=NEVER_STARTED, ended_at=ts)
        )
        active_result = await session.execute(
            update(SessionRow)
            .where(SessionRow.status == "active", SessionRow.started_at.is_not(None))
            .where(SessionRow.started_at < active_cutoff)
            .values(status="failed", error=SUMMARY_NEVER_RECEIVED, ended_at=ts)
        )

    created_swept = cast(CursorResult[Any], created_result).rowcount or 0
    active_swept = cast(CursorResult[Any], active_result).rowcount or 0
    if created_swept or active_swept:
        log.info("sessions_swept", created_swept=created_swept, active_swept=active_swept)
    return created_swept, active_swept


async def sweep_recording_retention(
    db: Database, settings: Settings, *, now: dt.datetime | None = None
) -> int:
    """Delete recordings whose `AgentConfig.recording.retention_days` has elapsed.

    Best-effort per row: a storage delete failure is logged and that row is
    left `ready` (retried next tick) rather than forgetting an object that may
    still exist. `retention_days=None` (the default) means "keep forever" and
    is never purged.

    Returns:
        How many recordings were purged this pass.
    """
    # Deferred: `lkap_api.connections.webhooks` imports this module for
    # `NEVER_STARTED` at its own module-scope, and `lkap_api.recordings`
    # (any submodule, since importing one always initialises the package
    # `__init__`) imports `lkap_api.connections.webhooks` back via
    # `recordings/finalize.py` — a module-level import here would deadlock
    # that pair mid-initialisation (ImportError: partially initialized
    # module). By call time (long after app startup) both are fully loaded.
    from lkap_api.recordings.storage import resolve_storage_row  # noqa: PLC0415

    ts = now or utcnow()
    vault = Vault(settings.master_key)
    purged = 0
    async with db.session() as session:
        rows = (
            await session.execute(
                select(SessionRow, Agent, LiveKitConnection)
                .join(Agent, Agent.id == SessionRow.agent_id)
                .outerjoin(LiveKitConnection, LiveKitConnection.id == SessionRow.connection_id)
                .where(SessionRow.recording_status == "ready", SessionRow.recording_object_key.is_not(None))
                # The retention sweep runs platform-wide, across every workspace.
                .execution_options(**{CROSS_WORKSPACE_OPTION: True})
            )
        ).all()
        for row, agent, connection in rows:
            if connection is None or row.ended_at is None:
                continue
            try:
                config = AgentConfig.model_validate(agent.config)
            except Exception:  # noqa: BLE001 - a malformed stored config must not kill the sweep
                continue
            retention_days = config.recording.retention_days
            if not retention_days or ts < row.ended_at + dt.timedelta(days=retention_days):
                continue
            storage_row = await resolve_storage_row(
                session,
                workspace_id=row.workspace_id,
                storage_config_id=config.recording.storage_config_id,
                connection=connection,
            )
            key = row.recording_object_key
            if storage_row is not None and key is not None:
                try:
                    await storage_from_config(storage_row, vault, settings=settings).delete(key)
                except Exception:  # noqa: BLE001 - keep the row `ready`; try again next tick
                    log.warning("recording_retention_delete_failed", session_id=row.id, exc_info=True)
                    continue
            row.recording_status = "none"
            row.recording_object_key = None
            purged += 1
    if purged:
        log.info("recordings_purged_by_retention", count=purged)
    return purged


async def sweep_loop(db: Database, settings: Settings) -> None:
    """Run `sweep_once` and `sweep_recording_retention` forever, every
    `LKAP_SESSION_SWEEP_INTERVAL_S` seconds.

    One failed pass (e.g. a transient database error) is logged and never kills
    the loop — the next tick tries again.
    """
    # Deferred for the same import-cycle reason as `sweep_recording_retention`'s import.
    from lkap_api.telephony.calls import sweep_stuck_calls  # noqa: PLC0415

    while True:
        try:
            await sweep_once(db, settings)
            await sweep_recording_retention(db, settings)
            await sweep_stuck_calls(db)  # R-V2-24: calls stuck in `dialing` / left open
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a sweep failure must never kill the loop
            log.exception("sessions_sweep_failed")
        await asyncio.sleep(settings.session_sweep_interval_s)
