"""v4_002_provider_models: per-workspace model records (V4-07)

docs/v4/CUSTOM-MODELS.md D-V4-24 (PLAN-V4 V4-07, R-V4-24). One new table,
``provider_models``: one row per ``(workspace_id, provider_home, kind,
model_id)`` holding

* the admin's ``declared`` capabilities and the last probe's ``detected``
  ones (``ModelCapabilities`` JSON, nullable);
* the last "Test model" result: ``last_test_at``, ``last_test_ok``, the
  scrubbed ``last_test_message`` (<= 500), ``last_test_latency_ms``,
  ``last_test_cost_usd``, and the credential id **and fingerprint** it ran
  with (a rotated key no longer matches, which resets "Tested");
* catalog sightings: ``catalog_seen_at`` and ``catalog_missing_since``.

Unique ``(workspace_id, provider_home, kind, model_id)``, index on
``workspace_id``, FK to ``workspaces`` with ``ON DELETE CASCADE``. No FK on
``last_test_credential_id``: the fingerprint is the durable fact, and
deleting a key must not touch the record.

A new table only, so no ``batch_alter_table``: SQLite and Postgres run the
same ``CREATE TABLE``. The downgrade drops the table (and its rows; nothing
else references it).

Rehearsal log: ``docs/v4/_briefs/migration-rehearsal-v4.md``. Only the
coordinator applies this to the live database (HANDOFF rule 4).

Revision ID: v4_002_provider_models
Revises: v4_001_livekit_numbers
Create Date: 2026-09-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v4_002_provider_models"
down_revision: str | None = "v4_001_livekit_numbers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNIQUE_NAME = "uq_provider_models_workspace_home_kind_model"
INDEX_NAME = "ix_provider_models_workspace"


def upgrade() -> None:
    """Create ``provider_models``."""
    op.create_table(
        "provider_models",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("provider_home", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        sa.Column("declared", sa.JSON(), nullable=True),
        sa.Column("detected", sa.JSON(), nullable=True),
        sa.Column("last_test_at", sa.DateTime(), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_test_message", sa.String(length=500), nullable=True),
        sa.Column("last_test_latency_ms", sa.Integer(), nullable=True),
        sa.Column("last_test_cost_usd", sa.Numeric(precision=12, scale=6, asdecimal=False), nullable=True),
        sa.Column("last_test_credential_id", sa.String(length=32), nullable=True),
        sa.Column("last_test_fingerprint", sa.String(length=32), nullable=True),
        sa.Column("catalog_seen_at", sa.DateTime(), nullable=True),
        sa.Column("catalog_missing_since", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_provider_models_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_provider_models"),
        sa.UniqueConstraint("workspace_id", "provider_home", "kind", "model_id", name=UNIQUE_NAME),
    )
    op.create_index(INDEX_NAME, "provider_models", ["workspace_id"], unique=False)


def downgrade() -> None:
    """Drop ``provider_models`` (its rows go with it; nothing references the table)."""
    op.drop_index(INDEX_NAME, table_name="provider_models")
    op.drop_table("provider_models")
