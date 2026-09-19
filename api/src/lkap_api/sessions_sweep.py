"""Stale-session sweep — DECISIONS-W2 D-W2-2(b).

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
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult

from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import utcnow
from lkap_api.db.session import Database
from lkap_api.logging import get_logger
from lkap_api.settings import Settings

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


async def sweep_loop(db: Database, settings: Settings) -> None:
    """Run `sweep_once` forever, every `LKAP_SESSION_SWEEP_INTERVAL_S` seconds.

    One failed pass (e.g. a transient database error) is logged and never kills
    the loop — the next tick tries again.
    """
    while True:
        try:
            await sweep_once(db, settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a sweep failure must never kill the loop
            log.exception("sessions_sweep_failed")
        await asyncio.sleep(settings.session_sweep_interval_s)
