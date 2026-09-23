"""Connection CRUD, rotation, agent binding and the ``fleet_desired`` writer.

Every query on ``livekit_connections`` takes an explicit ``workspace_id``
(CONTRACTS-V2 §3.1); the admin router passes the caller's workspace. The only
unscoped lookup is :func:`get_connection_by_id`, used by service-token routes
(worker, supervisor) that are platform-wide by design.

Fleet desired state — the interface V2-04 (supervisor) builds on
-------------------------------------------------------------------
``fleet_desired`` has one row per connection (CONTRACTS-V2 §1.2, §5):

* :func:`compute_desired_hash` — ``sha256`` over the canonical JSON of
  ``{url, credentials_version, agent_name, image, packs, restart_generation}``
  (``restart_generation`` added by R-V2-4). Any change to one of
  those means every running replica of the pool is stale and must be drained
  and restarted (rotation included: ``credentials_version`` is in the hash).
* :func:`write_fleet_desired` — upserts the row for one connection. The api
  calls it on create, update, rotate and default changes, so the row is always
  current after any connection write. ``desired_replicas`` is ``0`` unless the
  connection is ``supervised``; for a supervised pool it is initialised from
  ``connection.replicas`` and reset to it only when the caller says so
  (``reset_replicas=True``: create, or an update that touched ``replicas`` or
  ``deployment_mode``). Rotation and unrelated edits keep the current value, so
  a pool someone stopped stays stopped.
* :func:`set_desired_replicas` — the write V2-04's ``POST …/fleet``
  ``start|stop|restart`` actions use (``stop`` → 0, ``start`` → n). It never
  touches ``connection.replicas`` (the configured size).
* :func:`list_fleet_desired` — what ``GET /internal/v1/fleet/desired`` returns:
  one :class:`~lkap_contracts.fleet.FleetDesired` per **supervised**
  connection, re-hashed against the current ``LKAP_PACKS`` on read (a pack
  change only reaches the api through a restart, never a connection write).
* ``restart`` without a config change (R-V2-4): V2-04's
  :func:`lkap_api.fleet.registry.request_restart` bumps
  ``fleet_desired.restart_generation``, which is part of the hash.

Workers receive their environment from ``GET /internal/v1/connections/{id}/worker-env``
(:func:`lkap_api.connections.bundle.worker_env`), fetched just in time.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

from lkap_contracts.api_models import (
    ConnectionCreate,
    ConnectionOut,
    ConnectionTestResult,
    ConnectionUpdate,
)
from lkap_contracts.common import SessionChannel
from lkap_contracts.connections import ConnectionCapabilities
from lkap_contracts.dispatch import DispatchMetadata
from lkap_contracts.fleet import FleetDesired
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.connections.clients import ConnectionClientFactory
from lkap_api.connections.probe import effective_capabilities
from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Agent, FleetDesiredState, LiveKitConnection, StorageConfig, new_id, utcnow
from lkap_api.errors import ConflictError, NotFoundError, UnprocessableEntityError
from lkap_api.livekit_tokens import TOKEN_TTL
from lkap_api.logging import get_logger
from lkap_api.vault import UNKNOWN_FINGERPRINT, Vault, VaultError

log = get_logger(__name__)

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_URL_SCHEMES = frozenset({"ws", "wss", "http", "https"})


# ------------------------------------------------------------------------- helpers
def validate_url(url: str) -> str:
    """Return a normalised connection url or raise a 422.

    Raises:
        UnprocessableEntityError: Unless the url is ``ws(s)://`` or ``http(s)://`` with a host.
    """
    cleaned = url.strip().rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in _URL_SCHEMES or not parsed.hostname:
        raise UnprocessableEntityError(
            "url must be a ws://, wss://, http:// or https:// LiveKit server url",
            details={"field": "url"},
        )
    return cleaned


def _validate_fields(
    *,
    slug: str | None = None,
    agent_name: str | None = None,
    replicas: int | None = None,
    deployment_type: str,
    deployment_mode: str,
) -> None:
    if slug is not None and not _SLUG_RE.match(slug):
        raise UnprocessableEntityError(
            "slug must be lowercase letters, digits and '-' (max 64)", details={"field": "slug"}
        )
    if agent_name is not None and not _AGENT_NAME_RE.match(agent_name):
        raise UnprocessableEntityError(
            "agent_name must be 1-128 letters, digits, '_', '.' or '-'", details={"field": "agent_name"}
        )
    if replicas is not None and not 0 <= replicas <= 32:
        raise UnprocessableEntityError("replicas must be between 0 and 32", details={"field": "replicas"})
    if deployment_mode == "cloud_hosted" and deployment_type != "cloud":
        raise UnprocessableEntityError(
            "cloud_hosted deployment requires a LiveKit Cloud connection",
            details={"field": "deployment_mode"},
        )


async def _check_storage(db: AsyncSession, workspace_id: str, storage_config_id: str | None) -> None:
    """Reject a ``storage_config_id`` that is not a storage config of the workspace."""
    if storage_config_id is None:
        return
    found = await db.scalar(
        select(StorageConfig.id).where(
            StorageConfig.workspace_id == workspace_id, StorageConfig.id == storage_config_id
        )
    )
    if found is None:
        raise UnprocessableEntityError(
            f"unknown storage config '{storage_config_id}'", details={"field": "storage_config_id"}
        )


def connection_fingerprint(vault: Vault, row: LiveKitConnection) -> str:
    """``"…"`` plus the last four characters of the connection's API key."""
    try:
        key = vault.decrypt(row.api_key_ct).get("api_key", "")
    except VaultError:
        return UNKNOWN_FINGERPRINT
    return "…" + key[-4:] if key else UNKNOWN_FINGERPRINT


def to_out(row: LiveKitConnection, vault: Vault) -> ConnectionOut:
    """Render a connection for admin callers; secrets are reduced to a fingerprint."""
    return ConnectionOut(
        id=row.id,
        workspace_id=row.workspace_id,
        slug=row.slug,
        name=row.name,
        deployment_type=row.deployment_type,
        url=row.url,
        fingerprint=connection_fingerprint(vault, row),
        credentials_version=row.credentials_version,
        agent_name=row.agent_name,
        deployment_mode=row.deployment_mode,
        replicas=row.replicas,
        worker_image=row.worker_image,
        region=row.region,
        use_inference=bool(row.use_inference),
        storage_config_id=row.storage_config_id,
        is_default=bool(row.is_default),
        status=row.status,
        capabilities=capabilities_of(row),
        last_checked_at=row.last_checked_at,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def capabilities_of(row: LiveKitConnection) -> ConnectionCapabilities:
    """The effective :class:`ConnectionCapabilities` of a stored connection."""
    return effective_capabilities(row.deployment_type, bool(row.use_inference), row.capabilities)


# ---------------------------------------------------------------------------- reads
async def list_connections(
    db: AsyncSession, workspace_id: str, *, limit: int = 25, offset: int = 0
) -> tuple[list[LiveKitConnection], int]:
    """Return one page of a workspace's connections, default first, then by name."""
    scoped = LiveKitConnection.workspace_id == workspace_id
    total = await db.scalar(select(func.count()).select_from(LiveKitConnection).where(scoped))
    rows = (
        await db.execute(
            select(LiveKitConnection)
            .where(scoped)
            .order_by(LiveKitConnection.is_default.desc(), LiveKitConnection.name, LiveKitConnection.id)
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    return list(rows), int(total or 0)


async def get_connection(db: AsyncSession, workspace_id: str, connection_id: str) -> LiveKitConnection:
    """Load a connection by id or slug inside one workspace.

    Raises:
        NotFoundError: If it does not exist in that workspace (never 403: no existence leak).
    """
    row = await db.scalar(
        select(LiveKitConnection).where(
            LiveKitConnection.workspace_id == workspace_id,
            (LiveKitConnection.id == connection_id) | (LiveKitConnection.slug == connection_id),
        )
    )
    if row is None:
        raise NotFoundError(f"unknown connection '{connection_id}'")
    return row


async def get_connection_by_id(db: AsyncSession, connection_id: str) -> LiveKitConnection:
    """Platform-wide lookup for service-token routes (worker, supervisor).

    Raises:
        NotFoundError: If no connection has that id.
    """
    row = (
        await db.execute(
            select(LiveKitConnection)
            .where(LiveKitConnection.id == connection_id)
            .execution_options(**{CROSS_WORKSPACE_OPTION: True})
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"unknown connection '{connection_id}'")
    return row


async def default_connection(db: AsyncSession, workspace_id: str) -> LiveKitConnection | None:
    """Return the workspace's default connection, if it has one."""
    row: LiveKitConnection | None = await db.scalar(
        select(LiveKitConnection).where(
            LiveKitConnection.workspace_id == workspace_id, LiveKitConnection.is_default == 1
        )
    )
    return row


async def resolve_agent_connection(db: AsyncSession, agent: Agent) -> LiveKitConnection:
    """Return the connection an agent's sessions run on.

    ``agents.connection_id`` is authoritative. A NULL binding (a v1-style agent
    created after bootstrap by a router that does not set it yet) falls back to
    the workspace's default connection, which is exactly what bootstrap would
    have bound it to.

    Raises:
        ConflictError: When the agent is unbound and the workspace has no
            default connection (CONTRACTS-V2 §1.3: refuse, never guess).
    """
    if agent.connection_id:
        row = await db.scalar(
            select(LiveKitConnection).where(
                LiveKitConnection.workspace_id == agent.workspace_id,
                LiveKitConnection.id == agent.connection_id,
            )
        )
        if row is not None:
            return row
    row = await default_connection(db, agent.workspace_id)
    if row is None:
        raise ConflictError(
            f"agent '{agent.slug}' is not bound to a LiveKit connection and the workspace has no default",
            details={"agent_id": agent.id},
        )
    if agent.connection_id is None:
        log.debug("agent_connection_defaulted", agent_id=agent.id, connection_id=row.id)
    return row


@dataclass(frozen=True, slots=True)
class MintedSession:
    """What ``connect`` returns to the browser for one session, per connection."""

    connection_id: str
    server_url: str
    participant_token: str
    agent_name: str


async def mint_session_token(
    db: AsyncSession,
    factory: ConnectionClientFactory,
    agent: Agent,
    *,
    session_id: str,
    room_name: str,
    identity: str,
    participant_name: str,
    channel: SessionChannel = "web",
    attributes: dict[str, str] | None = None,
    ttl: dt.timedelta = TOKEN_TTL,
) -> MintedSession:
    """Mint the participant token for an agent's session on **its** connection (D-V2-5).

    Resolves ``agent → connection``, signs with that connection's key/secret,
    dispatches ``connection.agent_name`` with ID-only metadata carrying
    ``connection_id`` and ``channel``, and returns ``connection.url`` as the
    browser's ``serverUrl``. ``connect`` should also store
    ``MintedSession.connection_id`` on the session row.

    Raises:
        ConflictError: If the agent has no connection and the workspace no default.
    """
    connection = await resolve_agent_connection(db, agent)
    token = factory.mint_participant_token(
        connection,
        room_name=room_name,
        identity=identity,
        participant_name=participant_name,
        dispatch=DispatchMetadata(
            session_id=session_id,
            agent_id=agent.id,
            config_version=agent.config_version,
            participant_identity=identity,
            channel=channel,
            connection_id=connection.id,
        ),
        attributes=attributes,
        ttl=ttl,
    )
    return MintedSession(
        connection_id=connection.id,
        server_url=connection.url,
        participant_token=token,
        agent_name=connection.agent_name,
    )


# ---------------------------------------------------------------------------- fleet
def compute_desired_hash(
    *,
    url: str,
    credentials_version: int,
    agent_name: str,
    image: str,
    packs: Sequence[str],
    restart_generation: int = 0,
) -> str:
    """Return the pool's desired-state hash (CONTRACTS-V2 §1.2, R-V2-4).

    Args:
        url: Connection url.
        credentials_version: Bumped by every rotation.
        agent_name: The name the pool registers under.
        image: ``slim`` or ``full``.
        packs: Dotted pack module paths (``LKAP_PACKS``), order-insensitive.
        restart_generation: Bumped by the fleet ``restart`` action (R-V2-4).

    Returns:
        A 64-character hex sha256.
    """
    canonical = json.dumps(
        {
            "url": url,
            "credentials_version": credentials_version,
            "agent_name": agent_name,
            "image": image,
            "packs": sorted(packs),
            "restart_generation": restart_generation,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def desired_hash_of(row: LiveKitConnection, packs: Sequence[str], restart_generation: int = 0) -> str:
    """:func:`compute_desired_hash` for a stored connection."""
    return compute_desired_hash(
        url=row.url,
        credentials_version=row.credentials_version,
        agent_name=row.agent_name,
        image=row.worker_image,
        packs=packs,
        restart_generation=restart_generation,
    )


async def write_fleet_desired(
    db: AsyncSession,
    row: LiveKitConnection,
    packs: Sequence[str],
    *,
    reset_replicas: bool = False,
) -> FleetDesiredState:
    """Upsert the ``fleet_desired`` row of one connection.

    Args:
        db: Open session; the caller commits.
        row: The connection, already flushed with its new values.
        packs: ``LKAP_PACKS`` module paths.
        reset_replicas: Set ``desired_replicas`` from ``row.replicas`` even if
            a row exists (connection create, or an edit of ``replicas`` /
            ``deployment_mode``).

    Returns:
        The written row.
    """
    supervised = row.deployment_mode == "supervised"
    state = await db.get(FleetDesiredState, row.id)
    if state is None:
        state = FleetDesiredState(
            connection_id=row.id,
            desired_replicas=row.replicas if supervised else 0,
            desired_hash=desired_hash_of(row, packs),
        )
        db.add(state)
    else:
        state.desired_hash = desired_hash_of(row, packs, state.restart_generation)
        if not supervised:
            state.desired_replicas = 0
        elif reset_replicas:
            state.desired_replicas = row.replicas
        state.updated_at = utcnow()
    await db.flush()
    return state


async def set_desired_replicas(
    db: AsyncSession, row: LiveKitConnection, replicas: int, packs: Sequence[str]
) -> FleetDesiredState:
    """Set a supervised pool's desired size without changing its configuration.

    This is the write behind V2-04's fleet ``start``/``stop`` actions.

    Raises:
        ConflictError: If the connection is not ``supervised``.
        UnprocessableEntityError: If ``replicas`` is negative or above 32.
    """
    if row.deployment_mode != "supervised":
        raise ConflictError(
            f"connection '{row.slug}' is {row.deployment_mode}; only supervised pools are managed here"
        )
    if not 0 <= replicas <= 32:
        raise UnprocessableEntityError("replicas must be between 0 and 32", details={"field": "replicas"})
    state = await write_fleet_desired(db, row, packs)
    state.desired_replicas = replicas
    await db.flush()
    return state


async def list_fleet_desired(db: AsyncSession, packs: Sequence[str]) -> list[FleetDesired]:
    """Return the desired state of every supervised pool, platform-wide.

    Rows are (re)written on the way out so a missing row or a stale hash (for
    example after ``LKAP_PACKS`` changed) is corrected before the supervisor
    sees it.

    Args:
        db: Open session; the caller commits.
        packs: ``LKAP_PACKS`` module paths.

    Returns:
        One :class:`FleetDesired` per supervised connection, ordered by id.
    """
    rows = (
        await db.execute(
            select(LiveKitConnection)
            .where(LiveKitConnection.deployment_mode == "supervised")
            .order_by(LiveKitConnection.id)
            .execution_options(**{CROSS_WORKSPACE_OPTION: True})
        )
    ).scalars()
    out: list[FleetDesired] = []
    for row in rows:
        state = await write_fleet_desired(db, row, packs)
        out.append(
            FleetDesired(
                connection_id=row.id,
                agent_name=row.agent_name,
                desired_replicas=state.desired_replicas,
                restart_generation=state.restart_generation,
                desired_hash=state.desired_hash,
                image=row.worker_image,
                packs=list(packs),
            )
        )
    return out


# ---------------------------------------------------------------------------- writes
async def _clear_default(db: AsyncSession, workspace_id: str) -> None:
    await db.execute(
        update(LiveKitConnection)
        .where(LiveKitConnection.workspace_id == workspace_id, LiveKitConnection.is_default == 1)
        .values(is_default=0)
    )
    await db.flush()


async def create_connection(
    db: AsyncSession,
    vault: Vault,
    workspace_id: str,
    payload: ConnectionCreate,
    packs: Sequence[str],
) -> LiveKitConnection:
    """Create a connection; the first one in a workspace becomes its default.

    Raises:
        UnprocessableEntityError: On an invalid url, slug, agent name or mode.
        ConflictError: If the slug is already used in the workspace.
    """
    url = validate_url(payload.url)
    _validate_fields(
        slug=payload.slug,
        agent_name=payload.agent_name,
        replicas=payload.replicas,
        deployment_type=payload.deployment_type,
        deployment_mode=payload.deployment_mode,
    )
    if not payload.api_key.strip() or not payload.api_secret.strip():
        raise UnprocessableEntityError("api_key and api_secret are required", details={"field": "api_key"})
    await _check_storage(db, workspace_id, payload.storage_config_id)
    clash = await db.scalar(
        select(LiveKitConnection.id).where(
            LiveKitConnection.workspace_id == workspace_id, LiveKitConnection.slug == payload.slug
        )
    )
    if clash is not None:
        raise ConflictError(f"a connection with slug '{payload.slug}' already exists")

    make_default = payload.is_default or (await default_connection(db, workspace_id)) is None
    if make_default:
        await _clear_default(db, workspace_id)
    row = LiveKitConnection(
        id=new_id(),
        workspace_id=workspace_id,
        slug=payload.slug,
        name=payload.name,
        deployment_type=payload.deployment_type,
        url=url,
        api_key_ct=vault.encrypt({"api_key": payload.api_key.strip()}),
        api_secret_ct=vault.encrypt({"api_secret": payload.api_secret.strip()}),
        credentials_version=1,
        agent_name=payload.agent_name,
        deployment_mode=payload.deployment_mode,
        replicas=payload.replicas,
        worker_image=payload.worker_image,
        region=payload.region,
        use_inference=int(payload.use_inference),
        storage_config_id=payload.storage_config_id,
        is_default=int(make_default),
        status="unverified",
        capabilities={},
    )
    db.add(row)
    await db.flush()
    await write_fleet_desired(db, row, packs, reset_replicas=True)
    log.info(
        "connection_created",
        connection_id=row.id,
        workspace_id=workspace_id,
        deployment_type=row.deployment_type,
        deployment_mode=row.deployment_mode,
        is_default=make_default,
    )
    return row


async def update_connection(
    db: AsyncSession,
    workspace_id: str,
    connection_id: str,
    payload: ConnectionUpdate,
    packs: Sequence[str],
) -> LiveKitConnection:
    """Apply a partial update; secrets change only through :func:`rotate_credentials`.

    A url change invalidates the last probe (``status`` → ``unverified``).

    Raises:
        NotFoundError: If the connection is not in the workspace.
        UnprocessableEntityError: On invalid values.
    """
    row = await get_connection(db, workspace_id, connection_id)
    changes = payload.model_dump(exclude_unset=True)
    if "url" in changes and changes["url"] is not None:
        changes["url"] = validate_url(changes["url"])
    _validate_fields(
        agent_name=changes.get("agent_name"),
        replicas=changes.get("replicas"),
        deployment_type=row.deployment_type,
        deployment_mode=changes.get("deployment_mode") or row.deployment_mode,
    )
    await _check_storage(db, workspace_id, changes.get("storage_config_id"))
    for name, value in changes.items():
        if value is None and name not in {"region", "storage_config_id"}:
            continue
        if name == "use_inference":
            value = int(bool(value))
        setattr(row, name, value)
    if "url" in changes and changes["url"] is not None:
        row.status = "unverified"
        row.capabilities = {}
        row.last_error = None
    row.updated_at = utcnow()
    await db.flush()
    await write_fleet_desired(
        db, row, packs, reset_replicas=bool({"replicas", "deployment_mode"} & set(changes))
    )
    log.info("connection_updated", connection_id=row.id, fields=sorted(changes))
    return row


async def rotate_credentials(
    db: AsyncSession,
    vault: Vault,
    workspace_id: str,
    connection_id: str,
    *,
    api_key: str,
    api_secret: str,
    packs: Sequence[str],
) -> LiveKitConnection:
    """Replace a connection's key/secret and bump ``credentials_version``.

    The version bump changes ``fleet_desired.desired_hash``, which is what makes
    a supervised pool drain and restart with the new secret (a running
    ``AgentServer`` cannot take new credentials, research §1.6).

    Raises:
        NotFoundError: If the connection is not in the workspace.
        UnprocessableEntityError: If either value is empty.
    """
    if not api_key.strip() or not api_secret.strip():
        raise UnprocessableEntityError("api_key and api_secret are required", details={"field": "api_key"})
    row = await get_connection(db, workspace_id, connection_id)
    row.api_key_ct = vault.encrypt({"api_key": api_key.strip()})
    row.api_secret_ct = vault.encrypt({"api_secret": api_secret.strip()})
    row.credentials_version += 1
    row.status = "unverified"
    row.last_error = None
    row.updated_at = utcnow()
    await db.flush()
    await write_fleet_desired(db, row, packs)
    log.info("connection_rotated", connection_id=row.id, credentials_version=row.credentials_version)
    return row


async def set_default_connection(
    db: AsyncSession, workspace_id: str, connection_id: str, packs: Sequence[str]
) -> LiveKitConnection:
    """Make a connection the workspace default (the partial unique index allows one).

    Raises:
        NotFoundError: If the connection is not in the workspace.
    """
    row = await get_connection(db, workspace_id, connection_id)
    if not row.is_default:
        await _clear_default(db, workspace_id)
        row.is_default = True
        row.updated_at = utcnow()
        await db.flush()
    await write_fleet_desired(db, row, packs)
    log.info("connection_default_set", connection_id=row.id, workspace_id=workspace_id)
    return row


async def delete_connection(db: AsyncSession, workspace_id: str, connection_id: str) -> None:
    """Delete a connection that no agent is bound to.

    Raises:
        NotFoundError: If the connection is not in the workspace.
        ConflictError: If agents are bound to it, or it is the workspace default.
    """
    row = await get_connection(db, workspace_id, connection_id)
    bound = await db.scalar(
        select(func.count())
        .select_from(Agent)
        .where(Agent.workspace_id == workspace_id, Agent.connection_id == row.id)
    )
    if bound:
        raise ConflictError(
            f"connection '{row.slug}' has {bound} bound agent(s); rebind them first",
            details={"agents_bound": int(bound)},
        )
    if row.is_default:
        raise ConflictError(
            f"connection '{row.slug}' is the workspace default; make another connection the default first"
        )
    await db.delete(row)
    await db.flush()
    log.info("connection_deleted", connection_id=row.id, workspace_id=workspace_id)


async def record_test_result(
    db: AsyncSession, row: LiveKitConnection, result: ConnectionTestResult
) -> LiveKitConnection:
    """Persist a probe outcome: ``status``, ``capabilities``, ``last_checked_at``, ``last_error``.

    A failed probe keeps the previously probed flags (the deployment did not
    lose SIP because a secret is wrong) and only records the error.
    """
    row.status = "ok" if result.ok else "error"
    row.last_checked_at = utcnow()
    row.last_error = None if result.ok else result.message
    if result.ok:
        row.capabilities = result.capabilities.model_dump(mode="json")
    await db.flush()
    return row


@dataclass(frozen=True, slots=True)
class UnsavedConnection:
    """A connection that exists only for one ``POST /v1/connections/test`` call.

    Carries freshly encrypted secrets so the probe goes through the same
    :class:`~lkap_api.connections.clients.ConnectionClientFactory` path as a
    saved row. Its random id keeps it out of any cache hit.
    """

    id: str
    url: str
    agent_name: str
    credentials_version: int
    api_key_ct: bytes
    api_secret_ct: bytes

    @classmethod
    def from_create(cls, vault: Vault, payload: ConnectionCreate) -> UnsavedConnection:
        """Build one from a create payload (validates the url)."""
        return cls(
            id=f"unsaved-{new_id()}",
            url=validate_url(payload.url),
            agent_name=payload.agent_name,
            credentials_version=1,
            api_key_ct=vault.encrypt({"api_key": payload.api_key.strip()}),
            api_secret_ct=vault.encrypt({"api_secret": payload.api_secret.strip()}),
        )
