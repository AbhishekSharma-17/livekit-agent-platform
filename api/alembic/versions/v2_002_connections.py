"""v2_002_connections: storage configs, LiveKit connections, worker fleet

Creates CONTRACTS-V2 §1.2 and performs the zero-downtime half of D-V2-6: when
``LIVEKIT_URL``/``LIVEKIT_API_KEY``/``LIVEKIT_API_SECRET`` and ``LKAP_MASTER_KEY``
are present in the *migration* environment, the env-configured deployment
becomes the workspace's default connection row. When they are not, the row is
left to :mod:`lkap_api.bootstrap`, which does the same work at api startup.

The environment is read with :func:`os.environ` rather than
:class:`~lkap_api.settings.Settings` on purpose: pydantic-settings would load a
``.env`` file, and migrations must never do that.

Revision ID: v2_002_connections
Revises: v2_001_tenancy
Create Date: 2026-09-20
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v2_002_connections"
down_revision: str | None = "v2_001_tenancy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_WORKSPACE_ID = "00000000000000000000000000000001"
CLOUD_URL_SUFFIX = ".livekit.cloud"


def _deployment_type(url: str) -> str:
    host = url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    return "cloud" if host.endswith(CLOUD_URL_SUFFIX) else "self_hosted"


def _seed_default_connection() -> None:
    """Insert the env-configured connection, or do nothing if it is not configured."""
    url = os.environ.get("LIVEKIT_URL", "").strip()
    api_key = os.environ.get("LIVEKIT_API_KEY", "").strip()
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "").strip()
    master_key = os.environ.get("LKAP_MASTER_KEY", "").strip()
    if not (url and api_key and api_secret and master_key):
        return

    from lkap_api.vault import Vault

    vault = Vault(master_key)
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    connections = sa.table(
        "livekit_connections",
        sa.column("id", sa.String),
        sa.column("workspace_id", sa.String),
        sa.column("slug", sa.String),
        sa.column("name", sa.String),
        sa.column("deployment_type", sa.String),
        sa.column("url", sa.String),
        sa.column("api_key_ct", sa.LargeBinary),
        sa.column("api_secret_ct", sa.LargeBinary),
        sa.column("credentials_version", sa.Integer),
        sa.column("agent_name", sa.String),
        sa.column("deployment_mode", sa.String),
        sa.column("replicas", sa.Integer),
        sa.column("worker_image", sa.String),
        sa.column("use_inference", sa.Integer),
        sa.column("is_default", sa.Integer),
        sa.column("status", sa.String),
        sa.column("capabilities", sa.JSON),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    op.execute(
        connections.insert().values(
            id=uuid.uuid4().hex,
            workspace_id=DEFAULT_WORKSPACE_ID,
            slug="default",
            name="Default",
            deployment_type=_deployment_type(url),
            url=url,
            api_key_ct=vault.encrypt({"api_key": api_key}),
            api_secret_ct=vault.encrypt({"api_secret": api_secret}),
            credentials_version=1,
            agent_name=os.environ.get("LKAP_AGENT_NAME", "lkap-agent").strip() or "lkap-agent",
            deployment_mode="external",
            replicas=1,
            worker_image="slim",
            use_inference=1,
            is_default=1,
            status="unverified",
            capabilities={},
            created_at=now,
            updated_at=now,
        )
    )


def upgrade() -> None:
    """Create the connection and fleet tables, then seed the default connection."""
    op.create_table(
        "storage_configs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("bucket", sa.String(length=200), server_default="", nullable=False),
        sa.Column("region", sa.String(length=64), nullable=True),
        sa.Column("endpoint_url", sa.String(length=512), nullable=True),
        sa.Column("prefix", sa.String(length=200), server_default="", nullable=False),
        sa.Column("access_key_ct", sa.LargeBinary(), nullable=True),
        sa.Column("secret_key_ct", sa.LargeBinary(), nullable=True),
        sa.Column("public_base_url", sa.String(length=512), nullable=True),
        sa.Column("is_default", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("kind IN ('s3','local')", name=op.f("ck_storage_configs_kind_valid")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_storage_configs_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_storage_configs")),
    )
    op.create_index("ix_storage_configs_workspace", "storage_configs", ["workspace_id"], unique=False)

    op.create_table(
        "livekit_connections",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("deployment_type", sa.String(length=16), nullable=False),
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("api_key_ct", sa.LargeBinary(), nullable=False),
        sa.Column("api_secret_ct", sa.LargeBinary(), nullable=False),
        sa.Column("credentials_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("agent_name", sa.String(length=128), nullable=False),
        sa.Column("deployment_mode", sa.String(length=16), nullable=False),
        sa.Column("replicas", sa.Integer(), server_default="1", nullable=False),
        sa.Column("worker_image", sa.String(length=16), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=True),
        sa.Column("use_inference", sa.Integer(), server_default="1", nullable=False),
        sa.Column("storage_config_id", sa.String(length=32), nullable=True),
        sa.Column("is_default", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "deployment_type IN ('cloud','self_hosted')",
            name=op.f("ck_livekit_connections_deployment_type_valid"),
        ),
        sa.CheckConstraint(
            "deployment_mode IN ('external','supervised','cloud_hosted')",
            name=op.f("ck_livekit_connections_deployment_mode_valid"),
        ),
        sa.CheckConstraint(
            "worker_image IN ('slim','full')",
            name=op.f("ck_livekit_connections_worker_image_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('unverified','ok','error')",
            name=op.f("ck_livekit_connections_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_livekit_connections_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["storage_config_id"],
            ["storage_configs.id"],
            name=op.f("fk_livekit_connections_storage_config_id_storage_configs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_livekit_connections")),
        sa.UniqueConstraint("workspace_id", "slug", name=op.f("uq_livekit_connections_workspace_slug")),
    )
    op.create_index(
        "ix_livekit_connections_default",
        "livekit_connections",
        ["workspace_id"],
        unique=True,
        sqlite_where=sa.text("is_default = 1"),
        postgresql_where=sa.text("is_default = 1"),
    )

    op.create_table(
        "worker_instances",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("connection_id", sa.String(length=32), nullable=False),
        sa.Column("instance_key", sa.String(length=200), nullable=False),
        sa.Column("image", sa.String(length=16), nullable=False),
        sa.Column("sdk_version", sa.String(length=64), server_default="", nullable=False),
        sa.Column("installed_provider_ids", sa.JSON(), nullable=False),
        sa.Column("pack_ids", sa.JSON(), nullable=False),
        sa.Column("registered_at", sa.DateTime(), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("managed_by", sa.String(length=16), nullable=False),
        sa.CheckConstraint("image IN ('slim','full')", name=op.f("ck_worker_instances_image_valid")),
        sa.CheckConstraint(
            "status IN ('starting','ready','draining','gone')",
            name=op.f("ck_worker_instances_status_valid"),
        ),
        sa.CheckConstraint(
            "managed_by IN ('external','supervisor','cloud')",
            name=op.f("ck_worker_instances_managed_by_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["livekit_connections.id"],
            name=op.f("fk_worker_instances_connection_id_livekit_connections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_instances")),
        sa.UniqueConstraint("instance_key", name=op.f("uq_worker_instances_instance_key")),
    )
    op.create_index(
        "ix_worker_instances_connection",
        "worker_instances",
        ["connection_id", "status"],
        unique=False,
    )

    op.create_table(
        "fleet_desired",
        sa.Column("connection_id", sa.String(length=32), nullable=False),
        sa.Column("desired_replicas", sa.Integer(), server_default="0", nullable=False),
        sa.Column("desired_hash", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["livekit_connections.id"],
            name=op.f("fk_fleet_desired_connection_id_livekit_connections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("connection_id", name=op.f("pk_fleet_desired")),
    )

    _seed_default_connection()


def downgrade() -> None:
    """Drop the connection and fleet tables."""
    op.drop_table("fleet_desired")
    op.drop_index("ix_worker_instances_connection", table_name="worker_instances")
    op.drop_table("worker_instances")
    op.drop_index("ix_livekit_connections_default", table_name="livekit_connections")
    op.drop_table("livekit_connections")
    op.drop_index("ix_storage_configs_workspace", table_name="storage_configs")
    op.drop_table("storage_configs")
