"""``worker_instances`` bookkeeping: register, heartbeat, per-connection fleet status.

CONTRACTS-V2 §1.2 / §3.4 / §5. Two callers write here, both with the service token:

* **the worker** (V2-07) — ``POST /internal/v1/workers/register`` once
  ``AgentServer`` has registered with LiveKit, then a heartbeat every
  :data:`HEARTBEAT_INTERVAL_S` seconds;
* **the supervisor** (V2-04) — registers each replica it starts (``managed_by
  = supervisor``, immediately followed by a ``starting`` heartbeat), and reports
  ``draining`` / ``gone`` when it stops one.

Status rules:

* ``register`` sets ``ready`` (the worker registers only after LiveKit accepted it).
* A heartbeat sets the reported status, except that ``draining`` only moves on
  to ``gone``: a draining worker never becomes dispatchable again.
* ``managed_by = supervisor`` is sticky for an ``instance_key``: a worker that
  registers itself as ``external`` under a key the supervisor already claimed
  is still the supervisor's replica.
* Rows that stop heartbeating are marked ``gone`` by :mod:`lkap_api.fleet.sweep`.

``LKAP_PACKS`` parity (REVIEW-FINAL F-17): the api seeds agents from its own packs
and the worker resolves an unknown ``pack_id`` to the null pack (generic panel, no
pack tools), so a worker that registers with a different pack set than the api's
logs a ``worker_pack_mismatch`` warning here, naming what is missing on each side.

Nothing here reads or writes a secret.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from lkap_contracts.api_models import FleetStatus, WorkerInstanceOut
from lkap_contracts.fleet import WorkerHeartbeatIn, WorkerRegisterIn, WorkerRegisterOut
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.config_service import installed_provider_ids
from lkap_api.connections.service import (
    default_connection,
    desired_hash_of,
    get_connection_by_id,
    write_fleet_desired,
)
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import FleetDesiredState, LiveKitConnection, WorkerInstance, new_id, utcnow
from lkap_api.errors import ConflictError, NotFoundError
from lkap_api.logging import get_logger

log = get_logger(__name__)

#: How often a worker heartbeats (CONTRACTS-V2 §5).
HEARTBEAT_INTERVAL_S = 30

#: A row silent for this long (three missed heartbeats) is ``gone`` (CONTRACTS-V2 §1.2).
STALE_AFTER = dt.timedelta(seconds=3 * HEARTBEAT_INTERVAL_S)

#: ``gone`` rows stay visible on the fleet card this long, then only in the table.
GONE_VISIBLE_FOR = dt.timedelta(hours=1)


async def _resolve_connection(db: AsyncSession, connection_id: str | None) -> LiveKitConnection:
    """The connection a worker belongs to; unset means the default workspace's default.

    Raises:
        NotFoundError: If ``connection_id`` names no connection.
        ConflictError: If it is unset and there is no default connection.
    """
    if connection_id:
        return await get_connection_by_id(db, connection_id)
    row = await default_connection(db, DEFAULT_WORKSPACE_ID)
    if row is None:
        raise ConflictError("no default connection exists; start the worker with LKAP_CONNECTION_ID set")
    return row


async def _by_key(db: AsyncSession, instance_key: str) -> WorkerInstance | None:
    row: WorkerInstance | None = await db.scalar(
        select(WorkerInstance).where(WorkerInstance.instance_key == instance_key)
    )
    return row


def pack_mismatch(api_pack_ids: Sequence[str], worker_pack_ids: Sequence[str]) -> tuple[list[str], list[str]]:
    """Compare the api's and a worker's pack ids (F-17).

    Args:
        api_pack_ids: The manifest ids the api discovered from its ``LKAP_PACKS``.
        worker_pack_ids: The ids the worker reported at registration.

    Returns:
        ``(missing_on_worker, unknown_to_api)``, both sorted; two empty lists mean parity.
    """
    api, worker = set(api_pack_ids), set(worker_pack_ids)
    return sorted(api - worker), sorted(worker - api)


async def register_worker(
    db: AsyncSession, payload: WorkerRegisterIn, *, api_pack_ids: Sequence[str] | None = None
) -> WorkerRegisterOut:
    """Insert or refresh the ``worker_instances`` row of one worker process.

    Args:
        db: Open session; the caller commits.
        payload: What the worker (or the supervisor on its behalf) reports.
        api_pack_ids: The api's own pack ids; when given, a worker reporting a
            different non-empty set is logged as ``worker_pack_mismatch`` (F-17).
            An empty report is not compared: the supervisor registers a replica
            before the worker itself reports its packs.

    Returns:
        The connection the worker is attributed to and the agent name it must serve.

    Raises:
        NotFoundError: For an unknown ``connection_id``.
        ConflictError: When ``connection_id`` is unset and there is no default connection.
    """
    connection = await _resolve_connection(db, payload.connection_id)
    now = utcnow()
    row = await _by_key(db, payload.instance_key)
    managed_by = payload.managed_by
    if row is None:
        row = WorkerInstance(id=new_id(), instance_key=payload.instance_key)
        db.add(row)
    elif row.managed_by == "supervisor" and managed_by == "external":
        managed_by = "supervisor"
    row.connection_id = connection.id
    row.image = payload.image
    row.sdk_version = payload.sdk_version
    row.installed_provider_ids = sorted(set(payload.installed_provider_ids))
    row.pack_ids = list(payload.pack_ids)
    row.registered_at = now
    row.last_heartbeat_at = now
    row.status = "ready"
    row.managed_by = managed_by
    await db.flush()
    log.info(
        "worker_registered",
        instance_key=row.instance_key,
        connection_id=connection.id,
        image=row.image,
        managed_by=managed_by,
        installed_providers=len(row.installed_provider_ids),
    )
    if api_pack_ids is not None and payload.pack_ids:
        missing_on_worker, unknown_to_api = pack_mismatch(api_pack_ids, payload.pack_ids)
        if missing_on_worker or unknown_to_api:
            log.warning(
                "worker_pack_mismatch",
                instance_key=row.instance_key,
                connection_id=connection.id,
                missing_on_worker=missing_on_worker,
                unknown_to_api=unknown_to_api,
                hint="set the same LKAP_PACKS for the api and the worker, then restart both",
            )
    return WorkerRegisterOut(connection_id=connection.id, agent_name=connection.agent_name)


async def record_heartbeat(db: AsyncSession, instance_key: str, payload: WorkerHeartbeatIn) -> WorkerInstance:
    """Apply one heartbeat.

    Args:
        db: Open session; the caller commits.
        instance_key: The worker's key.
        payload: Reported status and job count.

    Returns:
        The updated row.

    Raises:
        NotFoundError: If the key never registered (the worker should register again).
    """
    row = await _by_key(db, instance_key)
    if row is None:
        raise NotFoundError(f"unknown worker instance '{instance_key}'; register first")
    if row.status == "draining" and payload.status in ("starting", "ready"):
        log.debug("worker_heartbeat_kept_draining", instance_key=instance_key, reported=payload.status)
    else:
        row.status = payload.status
    row.last_heartbeat_at = utcnow()
    await db.flush()
    log.debug(
        "worker_heartbeat",
        instance_key=instance_key,
        status=row.status,
        active_jobs=payload.active_jobs,
    )
    return row


def instance_out(row: WorkerInstance) -> WorkerInstanceOut:
    """Render one ``worker_instances`` row."""
    return WorkerInstanceOut(
        instance_key=row.instance_key,
        image=row.image,
        sdk_version=row.sdk_version,
        installed_provider_ids=[str(i) for i in row.installed_provider_ids],
        pack_ids=[str(p) for p in row.pack_ids],
        status=row.status,
        managed_by=row.managed_by,
        registered_at=row.registered_at,
        last_heartbeat_at=row.last_heartbeat_at,
    )


async def request_restart(
    db: AsyncSession, connection: LiveKitConnection, packs: Sequence[str]
) -> FleetDesiredState:
    """Ask the supervisor to roll a supervised pool without a configuration change (R-V2-4).

    Bumps ``fleet_desired.restart_generation``, stamps ``restart_requested_at`` and
    recomputes ``desired_hash`` (which includes the generation). The supervisor sees a
    new hash and does its usual rolling replacement: start replacements, wait until
    they are ready, drain the old replicas one at a time. ``desired_replicas`` is not
    touched, so restarting a stopped pool changes nothing that runs.

    Args:
        db: Open session; the caller commits.
        connection: A ``supervised`` connection (the router checks the mode).
        packs: ``LKAP_PACKS`` module paths.

    Returns:
        The updated row.
    """
    state = await write_fleet_desired(db, connection, packs)
    state.restart_generation += 1
    state.restart_requested_at = utcnow()
    state.desired_hash = desired_hash_of(connection, packs, state.restart_generation)
    state.updated_at = utcnow()
    await db.flush()
    log.info(
        "fleet_restart_requested",
        connection_id=connection.id,
        restart_generation=state.restart_generation,
    )
    return state


async def fleet_status(
    db: AsyncSession, connection: LiveKitConnection, *, now: dt.datetime | None = None
) -> FleetStatus:
    """Build ``GET /v1/connections/{id}/fleet`` for one connection.

    ``desired_replicas`` is the supervised pool's desired size (``0`` for other
    modes). ``instances`` lists every live row plus rows that went ``gone`` in
    the last :data:`GONE_VISIBLE_FOR`, newest first. ``installed_provider_ids``
    is the union over live workers.
    """
    ts = now or utcnow()
    state = await db.get(FleetDesiredState, connection.id)
    supervised = state is not None and connection.deployment_mode == "supervised"
    desired = state.desired_replicas if state is not None and supervised else 0
    generation = state.restart_generation if state is not None else 0
    requested_at = state.restart_requested_at if state is not None else None
    cutoff = ts - GONE_VISIBLE_FOR
    rows = (
        await db.execute(
            select(WorkerInstance)
            .where(
                WorkerInstance.connection_id == connection.id,
                or_(
                    WorkerInstance.status != "gone",
                    WorkerInstance.last_heartbeat_at >= cutoff,
                    WorkerInstance.registered_at >= cutoff,
                ),
            )
            .order_by(WorkerInstance.registered_at.desc(), WorkerInstance.instance_key)
        )
    ).scalars()
    installed = await installed_provider_ids(db, connection.id)
    return FleetStatus(
        desired_replicas=desired,
        restart_generation=generation,
        restart_requested_at=requested_at,
        instances=[instance_out(row) for row in rows],
        installed_provider_ids=sorted(installed or ()),
        image=connection.worker_image,
    )
