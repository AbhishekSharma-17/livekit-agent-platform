"""Worker registration, heartbeats and the supervisor's desired-state feed.

CONTRACTS-V2 §3.4 (internal, ``X-Service-Token``) and §5:

* ``POST /internal/v1/workers/register`` — a worker process (or the supervisor
  for a replica it just started) announces itself; returns the connection it is
  attributed to and the agent name it must serve.
* ``POST /internal/v1/workers/{instance_key}/heartbeat`` — status + job count.
* ``GET /internal/v1/fleet/desired`` — one ``FleetDesired`` per supervised
  connection. It re-writes stale ``fleet_desired`` rows on the way out
  (V2-03's :func:`~lkap_api.connections.service.list_fleet_desired`), so it uses
  the committing database dependency.

The router's lifespan runs the worker-instance sweep
(:func:`lkap_api.fleet.sweep.sweep_loop`) for the life of the application.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import APIRouter, FastAPI, Response, status
from lkap_contracts.fleet import FleetDesired, WorkerHeartbeatIn, WorkerRegisterIn, WorkerRegisterOut

from lkap_api.connections.service import list_fleet_desired
from lkap_api.db.session import Database
from lkap_api.deps import DbDep, ServiceDep, SettingsDep
from lkap_api.fleet import registry
from lkap_api.fleet.sweep import sweep_loop
from lkap_api.logging import get_logger

log = get_logger(__name__)


@asynccontextmanager
async def _sweep_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run the worker-instance sweep while the application is up."""
    database: Database | None = getattr(app.state, "db", None)
    if database is None:
        log.warning("worker_sweep_not_started", reason="no database on app.state")
        yield
        return
    task: asyncio.Task[None] = asyncio.create_task(sweep_loop(database))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


router = APIRouter(prefix="/internal/v1", tags=["fleet-internal"], lifespan=_sweep_lifespan)


@router.post(
    "/workers/register",
    response_model=WorkerRegisterOut,
    summary="Register a worker process",
    description=(
        "Called once per worker process after it registered with LiveKit (and by the supervisor for "
        "each replica it starts). Upserts `worker_instances` by `instance_key`; an unset "
        "`connection_id` attributes the worker to the default connection."
    ),
)
async def register_worker(payload: WorkerRegisterIn, db: DbDep, _service: ServiceDep) -> WorkerRegisterOut:
    """Upsert a worker instance."""
    return await registry.register_worker(db, payload)


@router.post(
    "/workers/{instance_key}/heartbeat",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Worker heartbeat",
    description=(
        "Every 30 s. Updates `status` and `last_heartbeat_at`; 404 means the instance is unknown and "
        "must register again. A `draining` instance can only move on to `gone`."
    ),
)
async def heartbeat(
    instance_key: str, payload: WorkerHeartbeatIn, db: DbDep, _service: ServiceDep
) -> Response:
    """Record a heartbeat."""
    await registry.record_heartbeat(db, instance_key, payload)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/fleet/desired",
    response_model=list[FleetDesired],
    summary="Desired state of every supervised pool",
    description=(
        "What the supervisor reconciles against: one entry per `supervised` connection with its "
        "desired replica count and desired-state hash. Contains no secrets."
    ),
)
async def fleet_desired(db: DbDep, settings: SettingsDep, _service: ServiceDep) -> list[FleetDesired]:
    """Return the desired fleet."""
    return await list_fleet_desired(db, settings.packs_list)
