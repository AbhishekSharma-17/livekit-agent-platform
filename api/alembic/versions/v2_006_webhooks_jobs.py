"""v2_006_webhooks_jobs: webhook endpoints, durable deliveries, job rows

CONTRACTS-V2 §1.5. ``webhook_endpoints.secret_ct`` holds the same ``Vault``
secret-bag ciphertext the other ``*_ct`` columns use, so
``python -m lkap_api.keys rotate`` can re-encrypt it without a per-table codec.

Revision ID: v2_006_webhooks_jobs
Revises: v2_005_sessions_ext
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v2_006_webhooks_jobs"
down_revision: str | None = "v2_005_sessions_ext"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the webhook and job tables."""
    # Flag columns are sa.Boolean (they were sa.Integer until the first Postgres run,
    # 2026-09-24). SQLite stores a Boolean as 0/1 in the same INTEGER-affinity column,
    # so databases already migrated need no new revision.
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("secret_ct", sa.LargeBinary(), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_webhook_endpoints_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_endpoints")),
    )
    op.create_index("ix_webhook_endpoints_workspace", "webhook_endpoints", ["workspace_id"], unique=False)

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("endpoint_id", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','delivered','failed','dead')",
            name=op.f("ck_webhook_deliveries_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_id"],
            ["webhook_endpoints.id"],
            name=op.f("fk_webhook_deliveries_endpoint_id_webhook_endpoints"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_deliveries")),
    )
    op.create_index(
        "ix_webhook_deliveries_due",
        "webhook_deliveries",
        ["endpoint_id", "status", "next_attempt_at"],
        unique=False,
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("run_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','running','done','failed','dead')",
            name=op.f("ck_jobs_status_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index("ix_jobs_due", "jobs", ["status", "run_at"], unique=False)


def downgrade() -> None:
    """Drop the webhook and job tables."""
    op.drop_index("ix_jobs_due", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_webhook_deliveries_due", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_index("ix_webhook_endpoints_workspace", table_name="webhook_endpoints")
    op.drop_table("webhook_endpoints")
