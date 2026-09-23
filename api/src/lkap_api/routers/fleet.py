"""Per-connection worker fleet status and start/stop/restart actions.

CONTRACTS-V2 §3.4: ``GET /v1/connections/{id}/fleet`` → ``FleetStatus`` and
``POST /v1/connections/{id}/fleet {action, replicas?}`` (supervised pools only).
Reading needs ``viewer`` (``connections:read``); acting needs ``admin``
(``connections:write``, §3.2 "fleet start/stop").

The actions only change the desired state; the supervisor converges on it
within one reconcile interval (R-V2-4):

* ``start`` — ``desired_replicas`` = ``replicas`` or the connection's configured
  ``replicas`` (V2-03's :func:`~lkap_api.connections.service.set_desired_replicas`).
* ``stop`` — ``desired_replicas`` = 0; workers drain (finish calls, take none).
* ``restart`` — bump ``restart_generation`` (part of ``desired_hash``); the
  supervisor rolls the pool: replacements first, then one drain at a time.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from lkap_contracts.api_models import FleetActionIn, FleetStatus

from lkap_api.auth.audit import record
from lkap_api.auth.deps import WorkspaceContext, require
from lkap_api.connections import service
from lkap_api.deps import DbDep, SettingsDep
from lkap_api.errors import ConflictError, UnprocessableEntityError
from lkap_api.fleet import registry
from lkap_api.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/v1/connections", tags=["fleet"])

FleetReaderDep = Annotated[WorkspaceContext, Depends(require("viewer", "connections:read"))]
FleetAdminDep = Annotated[WorkspaceContext, Depends(require("admin", "connections:write"))]


@router.get(
    "/{connection_id}/fleet",
    response_model=FleetStatus,
    summary="Worker fleet of a connection",
    description=(
        "The pool's desired size (supervised connections; 0 otherwise), the worker processes that "
        "registered for this connection (live ones plus those gone in the last hour) and the union "
        "of the provider ids the live workers can construct."
    ),
)
async def get_fleet(connection_id: str, ctx: FleetReaderDep, db: DbDep) -> FleetStatus:
    """Return the fleet status of one connection."""
    row = await service.get_connection(db, ctx.workspace_id, connection_id)
    return await registry.fleet_status(db, row)


@router.post(
    "/{connection_id}/fleet",
    response_model=FleetStatus,
    summary="Start, stop or restart a supervised pool",
    description=(
        "`start` sets the pool's desired size to `replicas` (default: the connection's configured "
        "`replicas`); `stop` sets it to 0 (workers drain: they finish running calls and take no new "
        "ones); `restart` rolls the pool without a configuration change (replacements start first, "
        "then the old workers drain one at a time). The supervisor applies the change within one "
        "reconcile interval. Supervised connections only (409 otherwise)."
    ),
)
async def fleet_action(
    connection_id: str,
    payload: FleetActionIn,
    ctx: FleetAdminDep,
    db: DbDep,
    settings: SettingsDep,
) -> FleetStatus:
    """Apply a fleet action.

    Raises:
        ConflictError: If the connection is not supervised.
        UnprocessableEntityError: For ``start`` with fewer than one replica.
    """
    row = await service.get_connection(db, ctx.workspace_id, connection_id)
    if row.deployment_mode != "supervised":
        raise ConflictError(
            f"connection '{row.slug}' is {row.deployment_mode}; only supervised pools are managed here"
        )
    match payload.action:
        case "start":
            replicas = payload.replicas if payload.replicas is not None else row.replicas
            if replicas < 1:
                raise UnprocessableEntityError(
                    "start needs at least one replica; use 'stop' to stop the pool",
                    details={"field": "replicas"},
                )
            await service.set_desired_replicas(db, row, replicas, settings.packs_list)
        case "stop":
            replicas = 0
            await service.set_desired_replicas(db, row, 0, settings.packs_list)
        case "restart":
            state = await registry.request_restart(db, row, settings.packs_list)
            replicas = state.desired_replicas
    record(
        db,
        workspace_id=ctx.workspace_id,
        actor_type=ctx.actor.actor_type,
        actor_id=ctx.actor.id,
        action=f"fleet.{payload.action}",
        target_type="connection",
        target_id=row.id,
        payload={"replicas": replicas},
    )
    log.info("fleet_action", connection_id=row.id, action=payload.action, replicas=replicas)
    return await registry.fleet_status(db, row)
