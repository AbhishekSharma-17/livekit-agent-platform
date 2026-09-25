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

`sweep_orphaned_sessions` (ask #55, B-13) closes a third kind of stuck row: a
job that crashes in the SDK entrypoint *before*
`agent/src/lkap_agent/observability.py`'s `SessionObserver` attaches never
posts a single `session_events` row and never will again (the worker process
is gone) — see that module's docstring and CONTRACTS.md §7 for the event
types. Left to the general `LKAP_SESSION_STALE_ACTIVE_S` rule above (six
hours by default, sized for a live call that simply hasn't posted its summary
yet), a row like that sits `active` for hours. This sweep uses a much shorter,
separately configurable timeout (`LKAP_SESSION_ORPHAN_TIMEOUT_S`) but only
fires on a session with **zero** `session_events` rows, ever, whose
`started_at` (or `created_at` when even that is unset) is older than the
cutoff — a session that has posted even one event has already proven the job
started for real and is left alone here regardless of how quiet it has been
since, exactly the "genuinely long live call" this sweep must not touch.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any, cast

from lkap_contracts.agent_config import AgentConfig
from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult

from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Agent, Job, LiveKitConnection, SessionEvent, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.registry import job
from lkap_api.logging import get_logger
from lkap_api.settings import Settings
from lkap_api.storage.resolve import storage_from_config
from lkap_api.vault import Vault
from lkap_api.webhooks import emit
from lkap_api.webhooks.events import SESSION_ENDED

log = get_logger(__name__)

NEVER_STARTED = "never started"
SUMMARY_NEVER_RECEIVED = "summary never received"

#: `sessions.error` for a row `sweep_orphaned_sessions` closes (ask #55, B-13).
ORPHANED = "orphaned"

#: The `error` session event's `payload["message"]` for an orphaned session.
ORPHAN_MESSAGE = "Session never started: no worker activity"

#: Job kind of the outbox row `sweep_orphaned_sessions` writes for the
#: `session.ended` webhook — mirrors `telephony.calls.CALL_EVENT_JOB` and
#: `recordings.finalize.RECORDING_FINALIZE`: the sweep has only `db:
#: Database`, not a `JobsService`, and `webhooks.emit` opens its own
#: connection, so calling it while the sweep's own transaction is still open
#: would deadlock SQLite the same way `docs/v2/_asks.md` #40 documents for the
#: session-summary path. The row is added to the same session as the status
#: change, committed with it, and picked up a tick later by the job poller
#: (or an `arq` worker), which calls `emit` on its own connection.
SESSION_ORPHAN_EVENT_JOB = "session_orphan_event"


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


async def sweep_orphaned_sessions(db: Database, settings: Settings, *, now: dt.datetime | None = None) -> int:
    """Close an `active` session with **no events at all** and a stale start (ask #55, B-13).

    A job that crashes in the SDK entrypoint before the worker's
    `SessionObserver` attaches never posts a single `session_events` row —
    that observer is what turns every later `agent_state`/`user_turn`/
    `agent_turn`/`metrics`/`error` event into a row (CONTRACTS.md §7), so its
    absence for the session's entire life is the one signal that is *only*
    true for a job that died before the conversation ever really started.
    Deliberately **not** "last event older than the cutoff": a genuinely long
    live call can sit quiet — on hold, mid-thought, a slow caller — for longer
    than `LKAP_SESSION_ORPHAN_TIMEOUT_S` without a new event, and closing that
    would be exactly the "genuinely long live call" this sweep must leave
    alone; a session that has posted even one event has already proven the
    job started for real, so it is left to the coarser
    `LKAP_SESSION_STALE_ACTIVE_S` rule above ("summary never received")
    instead. `WorkerInstance.last_heartbeat_at` was considered too: it is a
    heartbeat for the *worker process* on a connection's pool, not for one
    session, so a healthy heartbeat can't tell a crashed job on that same
    worker from a live one — `session_events` is the only per-session
    liveness signal that exists.

    Args:
        db: The process database.
        settings: Settings carrying `LKAP_SESSION_ORPHAN_TIMEOUT_S`.
        now: Injected clock for tests; defaults to the real current time.

    Returns:
        How many sessions were closed this pass.
    """
    ts = now or utcnow()
    cutoff = ts - dt.timedelta(seconds=settings.session_orphan_timeout_s)
    has_event = select(SessionEvent.id).where(SessionEvent.session_id == SessionRow.id).exists()
    closed = 0
    async with db.session() as session:
        candidates = (
            (
                await session.execute(
                    select(SessionRow)
                    .where(SessionRow.status == "active")
                    .where(~has_event)
                    .where(func.coalesce(SessionRow.started_at, SessionRow.created_at) < cutoff)
                    # The orphan sweep runs platform-wide, across every workspace.
                    .execution_options(**{CROSS_WORKSPACE_OPTION: True})
                )
            )
            .scalars()
            .all()
        )
        for row in candidates:
            row.status = "failed"
            row.error = ORPHANED
            row.ended_at = ts
            session.add(
                SessionEvent(session_id=row.id, ts=ts, type="error", payload={"message": ORPHAN_MESSAGE})
            )
            session.add(
                Job(
                    kind=SESSION_ORPHAN_EVENT_JOB,
                    payload={
                        "workspace_id": row.workspace_id,
                        "data": {
                            "session_id": row.id,
                            "agent_id": row.agent_id,
                            "status": "failed",
                            "disposition": row.disposition,
                            "variables": row.variables or {},
                        },
                    },
                    status="pending",
                    run_at=ts,
                )
            )
            closed += 1
    if closed:
        log.info("orphaned_sessions_swept", count=closed)
    return closed


@job(SESSION_ORPHAN_EVENT_JOB)
async def _emit_session_orphan_event(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Fan `session.ended` out for a session `sweep_orphaned_sessions` just closed."""
    data = payload.get("data")
    await emit(
        ctx.database,
        ctx.jobs,
        workspace_id=str(payload["workspace_id"]),
        event_type=SESSION_ENDED,
        data=dict(data) if isinstance(data, dict) else {},
    )


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


async def sweep_app_connections(db: Database, settings: Settings, *, now: dt.datetime | None = None) -> int:
    """Expire connected-app sign-ins nobody finished within 10 minutes (V5-18, COMPOSIO.md D-V5-C5).

    The connection row stays (``expired``, needs reconnect) so the console can
    offer Reconnect; its single-use flow nonce is dropped.

    Returns:
        How many pending connections were expired this pass.
    """
    # Deferred like the other sweeps: keeps this module's import graph small.
    from lkap_api.tool_providers.service import expire_stale_connections  # noqa: PLC0415

    async with db.session() as session:
        return await expire_stale_connections(session, Vault(settings.master_key), now=now)


async def sweep_loop(db: Database, settings: Settings) -> None:
    """Run every sweep forever, every `LKAP_SESSION_SWEEP_INTERVAL_S` seconds:
    `sweep_orphaned_sessions`, `sweep_once`, `sweep_recording_retention` and
    `sweep_stuck_calls` (in that order — the orphan rule is the more specific
    one for a row that could match both it and `sweep_once`'s six-hour rule).

    One failed pass (e.g. a transient database error) is logged and never kills
    the loop — the next tick tries again.
    """
    # Deferred for the same import-cycle reason as `sweep_recording_retention`'s import.
    from lkap_api.telephony.calls import sweep_stuck_calls  # noqa: PLC0415

    while True:
        try:
            # The more specific ask #55/B-13 rule runs first: a row it would close (no
            # events, ever) also matches `sweep_once`'s coarser six-hour "active" rule
            # once it's that old, and the orphan rule's `error`/event/webhook are the
            # more useful diagnosis of the two.
            await sweep_orphaned_sessions(db, settings)
            await sweep_once(db, settings)
            await sweep_recording_retention(db, settings)
            await sweep_stuck_calls(db)  # R-V2-24: calls stuck in `dialing` / left open
            await sweep_app_connections(db, settings)  # V5-18: unfinished app sign-ins
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a sweep failure must never kill the loop
            log.exception("sessions_sweep_failed")
        await asyncio.sleep(settings.session_sweep_interval_s)
