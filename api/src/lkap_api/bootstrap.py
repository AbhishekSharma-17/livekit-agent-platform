"""Idempotent first-run bootstrap: default workspace, owner and connection.

This is the zero-downtime half of D-V2-6. The ``v2_00x`` migrations move every
v1 row into :data:`~lkap_api.db.constants.DEFAULT_WORKSPACE_ID` and create the
default connection from the environment *if* the migration could see it; this
module does the same work at api startup, so an operator who runs
``alembic upgrade head`` without ``LIVEKIT_*`` in the migration environment
still ends up with exactly one default connection, and every agent bound to it.

Run it as a command with ``python -m lkap_api.bootstrap``. Running it twice
creates nothing the second time.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import Result, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.constants import (
    DEFAULT_AGENT_NAME,
    DEFAULT_CONNECTION_NAME,
    DEFAULT_CONNECTION_SLUG,
    DEFAULT_WORKSPACE_ID,
    DEFAULT_WORKSPACE_NAME,
    DEFAULT_WORKSPACE_SLUG,
)
from lkap_api.db.models import (
    Agent,
    LiveKitConnection,
    Session,
    User,
    Workspace,
    WorkspaceMember,
    new_id,
)
from lkap_api.db.session import Database
from lkap_api.logging import get_logger
from lkap_api.settings import Settings, get_settings
from lkap_api.vault import Vault

log = get_logger(__name__)

#: URL suffix that identifies a LiveKit Cloud project (D-V2-6).
CLOUD_URL_SUFFIX = ".livekit.cloud"


@dataclass
class BootstrapResult:
    """What one bootstrap run created; every field is empty on a repeat run."""

    workspace_created: bool = False
    owner_created: bool = False
    connection_created: bool = False
    owner_password: str | None = None
    agents_bound: int = 0
    sessions_bound: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """True when the run wrote anything at all."""
        return bool(
            self.workspace_created
            or self.owner_created
            or self.connection_created
            or self.agents_bound
            or self.sessions_bound
        )


def deployment_type_for(url: str) -> str:
    """Return ``"cloud"`` for a LiveKit Cloud url, ``"self_hosted"`` otherwise.

    Args:
        url: The connection's websocket url, e.g. ``wss://x.livekit.cloud``.

    Returns:
        The ``livekit_connections.deployment_type`` value.
    """
    host = url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    return "cloud" if host.endswith(CLOUD_URL_SUFFIX) else "self_hosted"


def _password_hasher() -> Callable[[str], str] | None:
    """Return V2-02's argon2id hasher if that package has landed, else ``None``.

    V2-01 must not choose a password-hashing library on V2-02's behalf, and
    nothing can sign in before V2-02 ships, so the owner row is created with a
    NULL ``password_hash`` until then.
    """
    try:
        module = importlib.import_module("lkap_api.auth.passwords")
    except ImportError:
        return None
    hasher = getattr(module, "hash_password", None)
    return hasher if callable(hasher) else None


async def ensure_default_workspace(session: AsyncSession) -> bool:
    """Create the ``default`` workspace if it is missing.

    Args:
        session: An open session; the caller commits.

    Returns:
        True when the row was created by this call.
    """
    existing = await session.scalar(select(Workspace).where(Workspace.slug == DEFAULT_WORKSPACE_SLUG))
    if existing is not None:
        return False
    session.add(
        Workspace(
            id=DEFAULT_WORKSPACE_ID,
            slug=DEFAULT_WORKSPACE_SLUG,
            name=DEFAULT_WORKSPACE_NAME,
            settings={},
        )
    )
    await session.flush()
    return True


async def ensure_owner(session: AsyncSession, settings: Settings) -> tuple[bool, str | None]:
    """Create the first owner user and its membership if the workspace has none.

    Args:
        session: An open session; the caller commits.
        settings: Runtime settings (owner email and optional password).

    Returns:
        ``(created, password)`` where ``password`` is the generated value that
        must be shown to the operator once, or ``None`` when the password came
        from the environment or could not be hashed yet.
    """
    email = settings.bootstrap_owner_email.strip().lower()
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        return False, None

    hasher = _password_hasher()
    generated: str | None = None
    password_hash: str | None = None
    if hasher is not None:
        plaintext = settings.bootstrap_owner_password
        if not plaintext:
            generated = secrets.token_urlsafe(24)
            plaintext = generated
        password_hash = hasher(plaintext)

    user = User(id=new_id(), email=email, name="Owner", password_hash=password_hash)
    session.add(user)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=DEFAULT_WORKSPACE_ID, user_id=user.id, role="owner"))
    await session.flush()
    return True, generated


async def ensure_default_connection(session: AsyncSession, settings: Settings) -> bool:
    """Create the default connection from ``LIVEKIT_*`` when the workspace has none.

    Args:
        session: An open session; the caller commits.
        settings: Runtime settings supplying the url, key, secret and master key.

    Returns:
        True when the connection row was created by this call.
    """
    existing = await session.scalar(
        select(LiveKitConnection.id).where(LiveKitConnection.workspace_id == DEFAULT_WORKSPACE_ID)
    )
    if existing is not None:
        return False
    if not (settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret):
        return False

    vault = Vault(settings.master_key)
    session.add(
        LiveKitConnection(
            id=new_id(),
            workspace_id=DEFAULT_WORKSPACE_ID,
            slug=DEFAULT_CONNECTION_SLUG,
            name=DEFAULT_CONNECTION_NAME,
            deployment_type=deployment_type_for(settings.livekit_url),
            url=settings.livekit_url,
            api_key_ct=vault.encrypt({"api_key": settings.livekit_api_key}),
            api_secret_ct=vault.encrypt({"api_secret": settings.livekit_api_secret}),
            agent_name=settings.agent_name or DEFAULT_AGENT_NAME,
            deployment_mode="external",
            is_default=1,
        )
    )
    await session.flush()
    return True


async def bind_unbound_rows(session: AsyncSession) -> tuple[int, int]:
    """Point every unbound agent and session at the workspace's default connection.

    Args:
        session: An open session; the caller commits.

    Returns:
        ``(agents_bound, sessions_bound)`` row counts.
    """
    connection_id = await session.scalar(
        select(LiveKitConnection.id).where(
            LiveKitConnection.workspace_id == DEFAULT_WORKSPACE_ID,
            # `is_default` is Integer-as-bool, the v1 convention every model follows.
            LiveKitConnection.is_default == 1,
        )
    )
    if connection_id is None:
        return 0, 0
    agents = await session.execute(
        update(Agent)
        .where(Agent.workspace_id == DEFAULT_WORKSPACE_ID, Agent.connection_id.is_(None))
        .values(connection_id=connection_id)
    )
    sessions = await session.execute(
        update(Session)
        .where(Session.workspace_id == DEFAULT_WORKSPACE_ID, Session.connection_id.is_(None))
        .values(connection_id=connection_id)
    )
    return _rowcount(agents), _rowcount(sessions)


def _rowcount(result: Result[Any]) -> int:
    """Return an UPDATE's affected-row count, 0 when the driver does not report one."""
    count = getattr(result, "rowcount", None)
    return count if isinstance(count, int) and count > 0 else 0


async def bootstrap(database: Database, settings: Settings) -> BootstrapResult:
    """Run every bootstrap step once, in dependency order.

    Args:
        database: The process database.
        settings: Runtime settings.

    Returns:
        A :class:`BootstrapResult` describing what this run created.
    """
    result = BootstrapResult()
    async with database.session() as session:
        result.workspace_created = await ensure_default_workspace(session)
        result.owner_created, result.owner_password = await ensure_owner(session, settings)
        result.connection_created = await ensure_default_connection(session, settings)
        result.agents_bound, result.sessions_bound = await bind_unbound_rows(session)

    if result.owner_created and result.owner_password is None:
        result.notes.append("owner created without a password; V2-02 (lkap_api.auth.passwords) sets it")
    log.info(
        "bootstrap_complete",
        workspace_created=result.workspace_created,
        owner_created=result.owner_created,
        connection_created=result.connection_created,
        agents_bound=result.agents_bound,
        sessions_bound=result.sessions_bound,
        notes=result.notes,
    )
    if result.owner_password is not None:
        _announce_generated_password(settings, result.owner_password)
    return result


#: Where a generated owner password goes in ``LKAP_ENV=prod`` (relative to ``LKAP_DATA_DIR``).
OWNER_PASSWORD_FILE = "owner-password.txt"


def _announce_generated_password(settings: Settings, password: str) -> None:
    """Hand the operator a generated owner password without putting it in the log pipeline.

    ``dev`` keeps the one-time log line (RUNBOOK). In ``prod`` the password is
    written to ``<LKAP_DATA_DIR>/owner-password.txt`` (mode 0600) and only the
    path is logged, because production logs are shipped and retained (V2-21).
    """
    if settings.env != "prod":
        log.warning(
            "bootstrap_owner_password_generated",
            email=settings.bootstrap_owner_email,
            password=password,
            hint="shown once; store it now",
        )
        return
    path = Path(settings.data_dir) / OWNER_PASSWORD_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"{settings.bootstrap_owner_email} {password}\n")
    log.warning(
        "bootstrap_owner_password_generated",
        email=settings.bootstrap_owner_email,
        password_file=str(path),
        hint="read it once, sign in, change the password, then delete the file",
    )


async def _main() -> int:
    settings = get_settings()
    database = Database(settings.resolved_database_url)
    try:
        await bootstrap(database, settings)
    finally:
        await database.dispose()
    return 0


def main() -> int:
    """Entry point for ``python -m lkap_api.bootstrap``."""
    return asyncio.run(_main())


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
