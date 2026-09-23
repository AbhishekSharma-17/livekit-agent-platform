"""v2_004_versions_providers: config history, provider settings, catalog cache

Creates CONTRACTS-V2 §1.3's new tables and backfills one
``agent_config_versions`` row per existing agent from its current ``config`` and
``config_version``, so the version list is never empty for a migrated agent.

Revision ID: v2_004_versions_providers
Revises: v2_003_scope_tables
Create Date: 2026-09-20
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v2_004_versions_providers"
down_revision: str | None = "v2_003_scope_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _backfill_versions() -> None:
    """Write one config version per agent from the agent's current config."""
    agents = sa.table(
        "agents",
        sa.column("id", sa.String),
        sa.column("config", sa.JSON),
        sa.column("config_version", sa.Integer),
    )
    versions = sa.table(
        "agent_config_versions",
        sa.column("id", sa.String),
        sa.column("agent_id", sa.String),
        sa.column("config_version", sa.Integer),
        sa.column("config", sa.JSON),
        sa.column("created_by", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("note", sa.Text),
    )
    connection = op.get_bind()
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    rows = connection.execute(sa.select(agents.c.id, agents.c.config, agents.c.config_version)).fetchall()
    payload = [
        {
            "id": uuid.uuid4().hex,
            "agent_id": row.id,
            "config_version": row.config_version,
            "config": row.config,
            "created_by": None,
            "created_at": now,
            "note": "migrated from v1",
        }
        for row in rows
    ]
    if payload:
        connection.execute(versions.insert(), payload)


def upgrade() -> None:
    """Create the version, provider-settings and catalog-cache tables."""
    op.create_table(
        "agent_config_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("agent_id", sa.String(length=32), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_agent_config_versions_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_config_versions")),
        sa.UniqueConstraint(
            "agent_id", "config_version", name=op.f("uq_agent_config_versions_agent_version")
        ),
    )
    op.create_index(
        "ix_agent_config_versions_agent",
        "agent_config_versions",
        ["agent_id", "config_version"],
        unique=False,
    )

    op.create_table(
        "workspace_providers",
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Integer(), server_default="1", nullable=False),
        sa.Column("default_credential_id", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_providers_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["default_credential_id"],
            ["credentials.id"],
            name=op.f("fk_workspace_providers_default_credential_id_credentials"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "provider_id", name=op.f("pk_workspace_providers")),
    )

    op.create_table(
        "provider_catalog_cache",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("credential_id", sa.String(length=32), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("ttl_s", sa.Integer(), server_default="3600", nullable=False),
        sa.CheckConstraint(
            "kind IN ('models','voices','avatars','personas')",
            name=op.f("ck_provider_catalog_cache_kind_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_catalog_cache")),
    )
    op.create_index(
        "ix_provider_catalog_cache_lookup",
        "provider_catalog_cache",
        ["provider_id", "credential_id", "kind"],
        unique=False,
    )

    # Plain ADD COLUMN: nullable, no constraints, so no SQLite table rebuild.
    with op.batch_alter_table("credentials", naming_convention=NAMING_CONVENTION) as batch_op:
        batch_op.add_column(sa.Column("last_test_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("last_test_ok", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("last_test_message", sa.Text(), nullable=True))
    # `knowledge_bases` carries no CHECK constraint, so reflection is lossless here
    # and the rebuild the new foreign key forces on SQLite is safe without copy_from.
    with op.batch_alter_table("knowledge_bases", naming_convention=NAMING_CONVENTION) as batch_op:
        batch_op.add_column(sa.Column("storage_config_id", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key(
            "fk_knowledge_bases_storage_config_id_storage_configs",
            "storage_configs",
            ["storage_config_id"],
            ["id"],
            ondelete="SET NULL",
        )

    _backfill_versions()


def downgrade() -> None:
    """Drop the new tables and the added columns."""
    with op.batch_alter_table("knowledge_bases", naming_convention=NAMING_CONVENTION) as batch_op:
        batch_op.drop_constraint("fk_knowledge_bases_storage_config_id_storage_configs", type_="foreignkey")
        batch_op.drop_column("storage_config_id")
    with op.batch_alter_table("credentials", naming_convention=NAMING_CONVENTION) as batch_op:
        batch_op.drop_column("last_test_message")
        batch_op.drop_column("last_test_ok")
        batch_op.drop_column("last_test_at")

    op.drop_index("ix_provider_catalog_cache_lookup", table_name="provider_catalog_cache")
    op.drop_table("provider_catalog_cache")
    op.drop_table("workspace_providers")
    op.drop_index("ix_agent_config_versions_agent", table_name="agent_config_versions")
    op.drop_table("agent_config_versions")
