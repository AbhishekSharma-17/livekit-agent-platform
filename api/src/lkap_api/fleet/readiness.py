"""Is a worker serving this connection? Counts for the console and the call-start check (V6-27).

Each worker serves exactly one connection: it registers under that connection's
``agent_name`` on that connection's LiveKit server, and reports itself to the api
(``worker_instances``, :mod:`lkap_api.fleet.registry`) once LiveKit accepted it, then
heartbeats every 30 s. An agent bound to a connection nobody started a worker for
has its calls wait forever, because LiveKit holds the dispatch until a worker with
that name appears.

How ready is decided
--------------------
A worker counts when its row is ``ready`` (or ``starting``: a supervised replica the
supervisor just launched) **and** it was heard from within :data:`READY_WITHIN` — the
same 90 s after which the sweep marks it ``gone``. Timestamps are compared directly,
so a row the sweep has not reached yet does not count as live.

When a call start is refused (:func:`ensure_worker_ready`)
----------------------------------------------------------
The worker table is authoritative only under these conditions, so the call start
answers 409 ``no_worker_running`` only when all of them hold; otherwise it logs
``call_start_no_worker`` and lets the call through (a false "no worker" is worse than
the old wait):

* ``LKAP_CALL_START_WORKER_CHECK`` is ``block`` (the default; ``warn`` and ``off`` exist
  for deployments whose workers run without ``LKAP_CONNECTION_ID``, which the api
  attributes to the default workspace's default connection);
* the connection is ``external`` or ``supervised``. ``cloud_hosted`` workers run on
  LiveKit Cloud and reach the api over the public internet, so their reports are not
  relied on;
* the api process has been up for :data:`STARTUP_GRACE` (one sweep window plus one
  heartbeat): rows survive a restart, but after a long outage every row looks stale
  until the next heartbeat arrives;
* no live ``ready``/``starting`` worker of this connection, and no live ``ready`` worker
  of another connection on the same LiveKit server under the same agent name (LiveKit
  would hand the call to that one).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Request
from lkap_contracts.api_models import FleetStatus
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.connections.service import agent_name_users
from lkap_api.db.models import LiveKitConnection, WorkerInstance, utcnow
from lkap_api.errors import ConflictError
from lkap_api.fleet.registry import HEARTBEAT_INTERVAL_S, STALE_AFTER, fleet_status
from lkap_api.logging import get_logger

log = get_logger(__name__)

#: A worker heard from within this window is live (the sweep marks it ``gone`` after it).
READY_WITHIN = STALE_AFTER

#: After an api start, wait this long before trusting an empty worker table.
STARTUP_GRACE = STALE_AFTER + dt.timedelta(seconds=HEARTBEAT_INTERVAL_S)

#: ``LKAP_CALL_START_WORKER_CHECK``.
WorkerCheckMode = Literal["block", "warn", "off"]

#: The modes whose workers' reports the call-start check relies on.
CHECKED_MODES = frozenset({"external", "supervised"})

#: What a signed-in builder sees on the call page.
NO_WORKER_MESSAGE = "No worker is running for connection '{name}'. Start one from Connections → {name}."

#: What a public caller sees (no internal names, nothing they can act on but waiting).
PUBLIC_NO_WORKER_MESSAGE = "This agent can't take calls right now. Please try again in a few minutes."


class NoWorkerRunningError(ConflictError):
    """409 — nothing is running that would answer a call on this connection."""

    code = "no_worker_running"


@dataclass(frozen=True, slots=True)
class WorkerReadiness:
    """Live workers that would answer a call on one connection."""

    #: This connection's ``ready`` workers.
    ready: int
    #: This connection's ``starting`` workers (a supervised replica on its way up).
    starting: int
    #: ``ready`` workers of other connections with the same server and agent name.
    shared: int

    @property
    def serving(self) -> bool:
        """Whether anything live would take a call dispatched under this agent name."""
        return self.ready + self.starting + self.shared > 0


def _live(now: dt.datetime) -> Any:
    cutoff = now - READY_WITHIN
    return or_(
        WorkerInstance.last_heartbeat_at >= cutoff,
        and_(WorkerInstance.last_heartbeat_at.is_(None), WorkerInstance.registered_at >= cutoff),
    )


async def ready_worker_counts(
    db: AsyncSession, connection_ids: Sequence[str], *, now: dt.datetime | None = None
) -> dict[str, int]:
    """Live ``ready`` workers per connection id (ids with none are absent).

    ``worker_instances`` is not a tenant table; callers pass ids they are allowed to see
    or only use the counts internally.
    """
    ids = sorted(set(connection_ids))
    if not ids:
        return {}
    rows = await db.execute(
        select(WorkerInstance.connection_id, func.count())
        .where(
            WorkerInstance.connection_id.in_(ids),
            WorkerInstance.status == "ready",
            _live(now or utcnow()),
        )
        .group_by(WorkerInstance.connection_id)
    )
    return {str(connection_id): int(count) for connection_id, count in rows.all()}


async def shared_agent_name_workers(
    db: AsyncSession, connection: LiveKitConnection, *, now: dt.datetime | None = None
) -> int:
    """Live ``ready`` workers of *other* connections (any workspace) on this server under this agent name.

    Only a count leaves this function; which connections they belong to does not.
    """
    others = await agent_name_users(
        db,
        workspace_id=connection.workspace_id,
        url=connection.url,
        agent_name=connection.agent_name,
        exclude_id=connection.id,
    )
    if not others:
        return 0
    counts = await ready_worker_counts(db, [other.id for other in others], now=now)
    return sum(counts.values())


async def worker_readiness(
    db: AsyncSession, connection: LiveKitConnection, *, now: dt.datetime | None = None
) -> WorkerReadiness:
    """Count what would answer a call on ``connection`` right now."""
    ts = now or utcnow()
    rows = await db.execute(
        select(WorkerInstance.status, func.count())
        .where(
            WorkerInstance.connection_id == connection.id,
            WorkerInstance.status.in_(("ready", "starting")),
            _live(ts),
        )
        .group_by(WorkerInstance.status)
    )
    by_status = {str(status): int(count) for status, count in rows.all()}
    ready, starting = by_status.get("ready", 0), by_status.get("starting", 0)
    shared = 0 if ready or starting else await shared_agent_name_workers(db, connection, now=ts)
    return WorkerReadiness(ready=ready, starting=starting, shared=shared)


def api_started_at(request: Request) -> dt.datetime | None:
    """When this api process started (``app.state.started_at``, set by ``create_app``)."""
    value = getattr(request.app.state, "started_at", None)
    return value if isinstance(value, dt.datetime) else None


def check_is_authoritative(
    connection: LiveKitConnection, *, now: dt.datetime, api_started_at: dt.datetime | None
) -> bool:
    """Whether an empty worker table may be trusted for this connection (see the module docstring)."""
    if connection.deployment_mode not in CHECKED_MODES:
        return False
    if api_started_at is None:
        return False
    return now - api_started_at >= STARTUP_GRACE


async def ensure_worker_ready(
    db: AsyncSession,
    connection: LiveKitConnection,
    *,
    mode: WorkerCheckMode,
    privileged: bool,
    api_started_at: dt.datetime | None,
    now: dt.datetime | None = None,
) -> WorkerReadiness | None:
    """Fail a call start fast when nothing would answer it (V6-27).

    Args:
        db: Open session.
        connection: The agent's connection (already resolved).
        mode: ``LKAP_CALL_START_WORKER_CHECK``.
        privileged: A builder of the workspace (console test mode) sees the connection's
            name and where to start a worker; a public caller sees a generic message.
        api_started_at: When this api process started (``app.state.started_at``);
            ``None`` means unknown, which never blocks.
        now: Injected clock for tests.

    Returns:
        The readiness that was checked, or ``None`` when the check is ``off``.

    Raises:
        NoWorkerRunningError: 409 ``no_worker_running`` when the check is ``block``,
            the worker table is authoritative for this connection and nothing is live.
    """
    if mode == "off":
        return None
    ts = now or utcnow()
    readiness = await worker_readiness(db, connection, now=ts)
    if readiness.serving:
        return readiness
    refuse = mode == "block" and check_is_authoritative(connection, now=ts, api_started_at=api_started_at)
    log.warning(
        "call_start_no_worker",
        connection_id=connection.id,
        deployment_mode=connection.deployment_mode,
        refused=refuse,
        mode=mode,
    )
    if not refuse:
        return readiness
    if privileged:
        raise NoWorkerRunningError(
            NO_WORKER_MESSAGE.format(name=connection.name),
            details={"connection_id": connection.id, "connection_name": connection.name},
        )
    raise NoWorkerRunningError(PUBLIC_NO_WORKER_MESSAGE)


def agent_name_warnings(agent_name: str, *, clash: bool, shared_workers: int) -> list[str]:
    """The non-blocking lines a saved connection's test and page show (V6-27)."""
    warnings: list[str] = []
    if clash:
        warnings.append(
            f"Another connection already uses the agent name '{agent_name}' on this LiveKit server. "
            "Calls are split between them. Change the agent name on one of them."
        )
    if shared_workers:
        noun = "worker" if shared_workers == 1 else "workers"
        verb = "is" if shared_workers == 1 else "are"
        warnings.append(
            f"{shared_workers} {noun} started for another connection {verb} running under the agent name "
            f"'{agent_name}' on this LiveKit server, so they also receive this connection's calls."
        )
    return warnings


async def fleet_status_with_workers(
    db: AsyncSession, connection: LiveKitConnection, *, now: dt.datetime | None = None
) -> FleetStatus:
    """:func:`lkap_api.fleet.registry.fleet_status` plus the V6-27 worker counts."""
    ts = now or utcnow()
    status = await fleet_status(db, connection, now=ts)
    ready = (await ready_worker_counts(db, [connection.id], now=ts)).get(connection.id, 0)
    shared = await shared_agent_name_workers(db, connection, now=ts)
    return status.model_copy(update={"ready_workers": ready, "shared_agent_name_workers": shared})
