"""v2_001_tenancy: workspaces, users, members, cookie sessions, api keys, audit log

Creates the tenancy tables of CONTRACTS-V2 §1.1 and inserts the ``default``
workspace every v1 row is migrated into by ``v2_003_scope_tables``.

Revision ID: v2_001_tenancy
Revises: 4135323c6ecc
Create Date: 2026-09-20
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v2_001_tenancy"
down_revision: str | None = "4135323c6ecc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Kept literal rather than imported so the revision is self-describing.
DEFAULT_WORKSPACE_ID = "00000000000000000000000000000001"


def upgrade() -> None:
    """Create the tenancy tables and seed the default workspace."""
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
        sa.UniqueConstraint("slug", name=op.f("uq_workspaces_slug")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=200), server_default="", nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("is_platform_admin", sa.Integer(), server_default="0", nullable=False),
        sa.Column("disabled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "workspace_members",
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "role IN ('owner','admin','builder','viewer')",
            name=op.f("ck_workspace_members_role_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_members_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_workspace_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "user_id", name=op.f("pk_workspace_members")),
    )
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("user_agent", sa.String(length=512), server_default="", nullable=False),
        sa.Column("ip", sa.String(length=64), server_default="", nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_user_sessions_token_hash")),
    )
    op.create_index("ix_user_sessions_user", "user_sessions", ["user_id"], unique=False)
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_api_keys_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_api_keys_key_hash")),
    )
    op.create_index("ix_api_keys_workspace", "api_keys", ["workspace_id"], unique=False)
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=True),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("target_type", sa.String(length=64), server_default="", nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "actor_type IN ('user','api_key','system')",
            name=op.f("ck_audit_log_actor_type_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index("ix_audit_log_workspace", "audit_log", ["workspace_id", "ts"], unique=False)

    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    workspaces = sa.table(
        "workspaces",
        sa.column("id", sa.String),
        sa.column("slug", sa.String),
        sa.column("name", sa.String),
        sa.column("settings", sa.JSON),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    op.execute(
        workspaces.insert().values(
            id=DEFAULT_WORKSPACE_ID,
            slug="default",
            name="Default",
            settings={},
            created_at=now,
            updated_at=now,
        )
    )


def downgrade() -> None:
    """Drop the tenancy tables (the default workspace row goes with them)."""
    op.drop_index("ix_audit_log_workspace", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_api_keys_workspace", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_index("ix_user_sessions_user", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_table("workspace_members")
    op.drop_table("users")
    op.drop_table("workspaces")
