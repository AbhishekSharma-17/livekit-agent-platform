"""SQLAlchemy 2 models — the DDL of docs/CONTRACTS.md §5.

SQLite is the default backend and Postgres works unchanged: no SQLite-only
types, JSON columns use :class:`sqlalchemy.JSON`, and all timestamps are
**naive UTC** (SQLite drops ``tzinfo`` on write, so storing naive values keeps
reads and writes symmetric on every backend).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from lkap_api.db.constants import DEFAULT_AGENT_NAME, DEFAULT_WORKSPACE_ID

#: Deterministic constraint names so Alembic autogenerate stays stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> dt.datetime:
    """Return the current time as a timezone-aware UTC ``datetime``.

    Values are aware everywhere in Python (so responses serialise with a ``Z``
    offset) and :class:`UtcDateTime` strips the offset on the way into the
    database, which SQLite and MySQL would drop anyway.
    """
    return dt.datetime.now(dt.UTC)


class UtcDateTime(TypeDecorator[dt.datetime]):
    """A ``DATETIME`` column that is naive UTC in the database and aware in Python.

    SQLite (and MySQL) silently drop ``tzinfo`` on write, so values are stored
    naive and re-tagged as UTC on the way out. Pydantic then serialises them as
    ISO-8601 with a ``Z`` offset, which is what CONTRACTS §7 promises and what
    ``new Date(...)`` in the browser needs to avoid reading them as local time.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect: Dialect) -> dt.datetime | None:
        """Normalise an incoming value to naive UTC for storage."""
        if value is None:
            return None
        if value.tzinfo is not None:
            return value.astimezone(dt.UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value: dt.datetime | None, dialect: Dialect) -> dt.datetime | None:
        """Tag a stored value as UTC on the way out."""
        return None if value is None else value.replace(tzinfo=dt.UTC)


def new_id() -> str:
    """Return a new uuid4 hex id, the primary key format used everywhere."""
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    """Declarative base carrying the shared metadata and naming convention."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


def workspace_fk(**kwargs: Any) -> Mapped[str]:
    """Return the `workspace_id` column every tenant table carries (CONTRACTS-V2 §1.1).

    The Python-side default is :data:`DEFAULT_WORKSPACE_ID` so v1 call sites that
    do not yet pass a workspace keep working after the tenancy migration; V2-02
    replaces the default with the value from ``WorkspaceContext``.
    """
    return mapped_column(
        String(32),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        default=DEFAULT_WORKSPACE_ID,
        **kwargs,
    )


# --------------------------------------------------------------------------- tenancy
class Workspace(Base):
    """One tenant: the scope of every agent, credential, session and connection."""

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class User(Base):
    """A platform user; `password_hash` is argon2id and NULL until a password is set."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_platform_admin: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    disabled_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class WorkspaceMember(Base):
    """A user's role in one workspace (role matrix in CONTRACTS-V2 §3.2)."""

    __tablename__ = "workspace_members"

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    __table_args__ = (CheckConstraint("role IN ('owner','admin','builder','viewer')", name="role_valid"),)


class UserSession(Base):
    """A cookie session; the raw token is never stored, only its sha256."""

    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    user_agent: Mapped[str] = mapped_column(String(512), nullable=False, default="", server_default="")
    ip: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")

    __table_args__ = (Index("ix_user_sessions_user", "user_id"),)


class ApiKey(Base):
    """A workspace-scoped machine credential; the raw key is shown once."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = workspace_fk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scopes: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)

    __table_args__ = (Index("ix_api_keys_workspace", "workspace_id"),)


class AuditLog(Base):
    """Append-only record of every mutating admin call.

    `workspace_id` carries no foreign key on purpose: the log must outlive the
    workspace it describes, including a workspace deletion.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    ts: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint("actor_type IN ('user','api_key','system')", name="actor_type_valid"),
        Index("ix_audit_log_workspace", "workspace_id", "ts"),
    )


# ----------------------------------------------------------------- connections/fleet
class StorageConfig(Base):
    """Where recordings and knowledge-base uploads are written."""

    __tablename__ = "storage_configs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = workspace_fk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="local")
    bucket: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    endpoint_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    prefix: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    access_key_ct: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    secret_key_ct: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    public_base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_default: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint("kind IN ('s3','local')", name="kind_valid"),
        Index("ix_storage_configs_workspace", "workspace_id"),
    )


class LiveKitConnection(Base):
    """One LiveKit deployment agents can be bound to (CONTRACTS-V2 §1.2).

    `api_key_ct` and `api_secret_ct` hold `Vault` ciphertext over a one-field
    secret bag (`{"api_key": ...}` / `{"api_secret": ...}`), the same encoding
    `credentials.ciphertext` uses, so `keys rotate` treats every column alike.
    """

    __tablename__ = "livekit_connections"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = workspace_fk()
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    deployment_type: Mapped[str] = mapped_column(String(16), nullable=False, default="cloud")
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key_ct: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    api_secret_ct: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    credentials_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    agent_name: Mapped[str] = mapped_column(String(128), nullable=False, default=DEFAULT_AGENT_NAME)
    deployment_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="external")
    replicas: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    worker_image: Mapped[str] = mapped_column(String(16), nullable=False, default="slim")
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    use_inference: Mapped[bool] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    storage_config_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("storage_configs.id", ondelete="SET NULL"), nullable=True
    )
    is_default: Mapped[bool] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unverified")
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_checked_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint("deployment_type IN ('cloud','self_hosted')", name="deployment_type_valid"),
        CheckConstraint(
            "deployment_mode IN ('external','supervised','cloud_hosted')", name="deployment_mode_valid"
        ),
        CheckConstraint("worker_image IN ('slim','full')", name="worker_image_valid"),
        CheckConstraint("status IN ('unverified','ok','error')", name="status_valid"),
        UniqueConstraint("workspace_id", "slug", name="uq_livekit_connections_workspace_slug"),
        Index(
            "ix_livekit_connections_default",
            "workspace_id",
            unique=True,
            sqlite_where=text("is_default = 1"),
            postgresql_where=text("is_default = 1"),
        ),
    )


class WorkerInstance(Base):
    """A worker process that registered itself against one connection's pool."""

    __tablename__ = "worker_instances"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    connection_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("livekit_connections.id", ondelete="CASCADE"), nullable=False
    )
    instance_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    image: Mapped[str] = mapped_column(String(16), nullable=False, default="slim")
    sdk_version: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    installed_provider_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    pack_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    registered_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    last_heartbeat_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="starting")
    managed_by: Mapped[str] = mapped_column(String(16), nullable=False, default="external")

    __table_args__ = (
        CheckConstraint("image IN ('slim','full')", name="image_valid"),
        CheckConstraint("status IN ('starting','ready','draining','gone')", name="status_valid"),
        CheckConstraint("managed_by IN ('external','supervisor','cloud')", name="managed_by_valid"),
        Index("ix_worker_instances_connection", "connection_id", "status"),
    )


class FleetDesiredState(Base):
    """The desired pool state the supervisor reconciles against (CONTRACTS-V2 §5)."""

    __tablename__ = "fleet_desired"

    connection_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("livekit_connections.id", ondelete="CASCADE"), primary_key=True
    )
    desired_replicas: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # R-V2-4 (v2_009_fleet_restart): bumped by `fleet {action: restart}`, part of the desired hash.
    restart_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    restart_requested_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    desired_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


# ------------------------------------------------------ agent versions and providers
class AgentConfigVersion(Base):
    """An immutable snapshot of `agents.config`, written on every change (D-V2-13)."""

    __tablename__ = "agent_config_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("agent_id", "config_version", name="uq_agent_config_versions_agent_version"),
        Index("ix_agent_config_versions_agent", "agent_id", "config_version"),
    )


class WorkspaceProvider(Base):
    """Per-workspace provider enablement; the absence of a row means enabled."""

    __tablename__ = "workspace_providers"

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    provider_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    default_credential_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class ProviderCatalogCache(Base):
    """A cached vendor catalog page (models, voices, avatars) used when Redis is absent."""

    __tablename__ = "provider_catalog_cache"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    items: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    fetched_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    ttl_s: Mapped[int] = mapped_column(Integer, nullable=False, default=3600, server_default="3600")

    __table_args__ = (
        CheckConstraint("kind IN ('models','voices','avatars','personas')", name="kind_valid"),
        Index("ix_provider_catalog_cache_lookup", "provider_id", "credential_id", "kind"),
    )


class Agent(Base):
    """A configured agent: its pack, panel, publication state and `AgentConfig`."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = workspace_fk()
    connection_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("livekit_connections.id", ondelete="RESTRICT"), nullable=True
    )
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    pack_id: Mapped[str] = mapped_column(String(64), nullable=False, default="generic")
    ui_panel_id: Mapped[str] = mapped_column(String(64), nullable=False, default="generic")
    published: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="prompt", server_default="prompt")
    archived_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    limits: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    allowed_origins: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint("mode IN ('prompt','flow')", name="mode_valid"),
        Index("ix_agents_workspace", "workspace_id"),
        Index("ix_agents_connection", "connection_id"),
    )


class Credential(Base):
    """An encrypted provider secret bag plus its display fingerprint."""

    __tablename__ = "credentials"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = workspace_fk()
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    last_test_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Integer, nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (Index("ix_credentials_workspace", "workspace_id"),)


class Tool(Base):
    """A declarative HTTP tool or MCP server, shared or owned by one agent."""

    __tablename__ = "tools"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = workspace_fk()
    agent_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("agents.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint("kind IN ('http','mcp')", name="kind_valid"),
        Index("ix_tools_workspace", "workspace_id"),
    )


class KnowledgeBase(Base):
    """A knowledge base; its vectors live in LanceDB, its metadata here."""

    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = workspace_fk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    embedder_id: Mapped[str] = mapped_column(String(64), nullable=False, default="fastembed-embedding")
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_config_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("storage_configs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (Index("ix_knowledge_bases_workspace", "workspace_id"),)


class KbDocument(Base):
    """One uploaded source file and its ingestion status."""

    __tablename__ = "kb_documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kb_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime: Mapped[str] = mapped_column(String(128), nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    __table_args__ = (CheckConstraint("status IN ('pending','ready','failed')", name="status_valid"),)


class KbChunk(Base):
    """A chunk of a document; the vector with the same id lives in LanceDB."""

    __tablename__ = "kb_chunks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kb_id: Mapped[str] = mapped_column(String(32), nullable=False)
    document_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class AgentKnowledgeBase(Base):
    """Join table attaching knowledge bases to agents."""

    __tablename__ = "agent_knowledge_bases"

    agent_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    kb_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True
    )


class Session(Base):
    """One conversation: created by `connect`, finished by the worker summary."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = workspace_fk()
    agent_id: Mapped[str] = mapped_column(String(32), ForeignKey("agents.id"), nullable=False)
    connection_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("livekit_connections.id", ondelete="SET NULL"), nullable=True
    )
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    room_name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    participant_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    participant_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    pipeline_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    ended_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    transcript: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    final_ui_state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="web", server_default="web")
    caller: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    recording_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="none", server_default="none"
    )
    recording_egress_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Why a `recording_status="failed"` recording failed (docs/v2/_asks.md
    #: V2-20-3), e.g. "recording/start answered HTTP 422". `None` otherwise —
    #: cleared on any later status change so a stale reason never lingers.
    recording_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    recording_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    recording_duration_s: Mapped[float | None] = mapped_column(Numeric(12, 3, asdecimal=False), nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6, asdecimal=False), nullable=True)
    latency: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    disposition: Mapped[str | None] = mapped_column(String(128), nullable=True)
    variables: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('created','active','ended','failed')", name="status_valid"),
        CheckConstraint(
            "channel IN ('web','test','text','sip_in','sip_out','widget','api')", name="channel_valid"
        ),
        CheckConstraint(
            "recording_status IN ('none','requested','active','ready','failed')",
            name="recording_status_valid",
        ),
        Index("ix_sessions_agent", "agent_id", "created_at"),
        Index("ix_sessions_workspace", "workspace_id", "created_at"),
        Index("ix_sessions_connection", "connection_id"),
    )


class SessionEvent(Base):
    """An append-only timeline row posted by the worker."""

    __tablename__ = "session_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ix_session_events_session", "session_id", "id"),)


# ------------------------------------------------------- session QA, cost, rollups
class SessionQa(Base):
    """LLM quality scoring of one finished session (CONTRACTS-V2 §1.4, R-V2-5).

    `status`/`scored_by` per `v2_010_qa_status` (PLAN-V2 §8 ruling R-V2-5): the
    worker scores at session end via `PUT /internal/v1/sessions/{id}/qa`
    (`scored_by="worker"`, `status="skipped"` when `qa.enabled` is false); the
    api's `qa/scorer.py` only re-scores (`scored_by="api"`), and only when the
    resolved judge is an OpenAI-compatible vendor-key provider.
    """

    __tablename__ = "session_qa"

    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    scored_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tags: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    scored_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('pending','done','failed','skipped')", name="status_valid"),
        CheckConstraint(
            "sentiment IS NULL OR sentiment IN ('positive','neutral','negative')",
            name="sentiment_valid",
        ),
        CheckConstraint("scored_by IS NULL OR scored_by IN ('worker','api')", name="scored_by_valid"),
    )


class SessionCost(Base):
    """One priced usage line of a session; the sum lands in `sessions.cost_usd`."""

    __tablename__ = "session_costs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(18, 6, asdecimal=False), nullable=False)
    unit_price_usd: Mapped[float] = mapped_column(Numeric(18, 9, asdecimal=False), nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6, asdecimal=False), nullable=False)
    price_version: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")

    __table_args__ = (
        CheckConstraint(
            "unit IN ('tokens_in','tokens_out','audio_s_in','audio_s_out','chars','minutes','images')",
            name="unit_valid",
        ),
        Index("ix_session_costs_session", "session_id"),
    )


class UsageDaily(Base):
    """Nightly rollup behind the analytics page."""

    __tablename__ = "usage_daily"

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sessions: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    minutes: Mapped[float] = mapped_column(
        Numeric(12, 3, asdecimal=False), nullable=False, default=0, server_default="0"
    )
    cost_usd: Mapped[float] = mapped_column(
        Numeric(12, 6, asdecimal=False), nullable=False, default=0, server_default="0"
    )
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


# -------------------------------------------------------------- webhooks and jobs
class WebhookEndpoint(Base):
    """An outbound webhook subscription; `secret_ct` is a `Vault` secret bag."""

    __tablename__ = "webhook_endpoints"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = workspace_fk()
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    secret_ct: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    events: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (Index("ix_webhook_endpoints_workspace", "workspace_id"),)


class WebhookDelivery(Base):
    """One attempt series for one event against one endpoint."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    endpoint_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    next_attempt_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    delivered_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('pending','delivered','failed','dead')", name="status_valid"),
        Index("ix_webhook_deliveries_due", "endpoint_id", "status", "next_attempt_at"),
    )


class Job(Base):
    """A background job row; with the `arq` backend this records outcomes only."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    run_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint("status IN ('pending','running','done','failed','dead')", name="status_valid"),
        Index("ix_jobs_due", "status", "run_at"),
    )


# ------------------------------------------------------------------------ telephony
class SipTrunk(Base):
    """A LiveKit SIP trunk mirrored into the platform (CONTRACTS-V2 §1.6)."""

    __tablename__ = "sip_trunks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = workspace_fk()
    connection_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("livekit_connections.id", ondelete="CASCADE"), nullable=False
    )
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    lk_trunk_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    numbers: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    provider_hint: Mapped[str] = mapped_column(
        String(16), nullable=False, default="other", server_default="other"
    )
    address: Mapped[str | None] = mapped_column(String(512), nullable=True)
    auth_username: Mapped[str | None] = mapped_column(String(200), nullable=True)
    auth_password_ct: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint("direction IN ('inbound','outbound')", name="direction_valid"),
        CheckConstraint("provider_hint IN ('twilio','telnyx','other')", name="provider_hint_valid"),
        Index("ix_sip_trunks_workspace", "workspace_id"),
    )


class SipDispatchRule(Base):
    """Routes inbound SIP calls on a trunk to one agent's room."""

    __tablename__ = "sip_dispatch_rules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = workspace_fk()
    connection_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("livekit_connections.id", ondelete="CASCADE"), nullable=False
    )
    lk_rule_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    trunk_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sip_trunks.id", ondelete="CASCADE"), nullable=False
    )
    numbers: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    agent_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    room_prefix: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    pin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    __table_args__ = (Index("ix_sip_dispatch_rules_workspace", "workspace_id"),)


class PhoneNumber(Base):
    """An E.164 number owned by a workspace and bound to a trunk."""

    __tablename__ = "phone_numbers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = workspace_fk()
    e164: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    trunk_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("sip_trunks.id", ondelete="SET NULL"), nullable=True
    )
    inbound_agent_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")

    __table_args__ = (Index("ix_phone_numbers_workspace", "workspace_id"),)


class Call(Base):
    """One telephony leg, linked to its session once the worker creates one."""

    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    workspace_id: Mapped[str] = workspace_fk()
    connection_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("livekit_connections.id", ondelete="SET NULL"), nullable=True
    )
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    from_e164: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    to_e164: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="dialing")
    sip_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lk_participant_identity: Mapped[str | None] = mapped_column(String(200), nullable=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    answered_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    ended_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, nullable=True)
    hangup_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transfer_to: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint("direction IN ('inbound','outbound')", name="direction_valid"),
        CheckConstraint(
            "status IN ('dialing','ringing','answered','no_answer','busy','failed',"
            "'completed','transferred')",
            name="status_valid",
        ),
        Index("ix_calls_workspace", "workspace_id"),
    )
