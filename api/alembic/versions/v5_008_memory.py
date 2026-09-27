"""v5_008_memory: memory_subjects, memory_events

V5-40 (docs/v5/PLAN-V5.md §0.3, D-V5-17): caller memory across sessions. A
``memory_subjects`` row is one caller the memory knows per scope (the agent, or
``workspace``), keyed by the caller's pseudonymous id (the hex HMAC of the phone
number or chosen identity under the workspace's memory key), with the date its
memories are deleted (``retention_until``). ``memory_events`` is the content-free
audit trail (recalled, stored, forgotten, purged, expired, with a count). The
memories themselves live in the memory backend (Mem0), never in these tables; the
workspace's memory key is a vault ``credentials`` row (``provider_id =
"memory-key"``), which needs no schema change.

New tables only: additive on both dialects, no rebuild of an existing table.

Revision ID: v5_008_memory
Revises: v5_007_telephony_amd
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v5_008_memory"
down_revision: str | None = "v5_007_telephony_amd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``memory_subjects`` and ``memory_events`` with their constraints and indexes."""
    op.create_table(
        "memory_subjects",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("workspace_id", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.String(64), nullable=False),
        sa.Column("scope_key", sa.String(64), nullable=False),
        sa.Column("agent_id", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("retention_until", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_memory_subjects_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_subjects")),
        sa.UniqueConstraint(
            "workspace_id",
            "subject_id",
            "scope_key",
            name=op.f("uq_memory_subjects_workspace_subject_scope"),
        ),
    )
    op.create_index("ix_memory_subjects_retention", "memory_subjects", ["retention_until"])

    op.create_table(
        "memory_events",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("workspace_id", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(32), nullable=True),
        sa.Column("agent_id", sa.String(32), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('recalled','stored','forgotten','purged','expired')",
            name=op.f("ck_memory_events_kind_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_memory_events_session_id_sessions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_memory_events_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_events")),
    )
    op.create_index("ix_memory_events_subject", "memory_events", ["workspace_id", "subject_id"])
    op.create_index("ix_memory_events_session", "memory_events", ["session_id"])


def downgrade() -> None:
    """Drop both tables. The backend's memories and the memory-key rows are left as they are."""
    op.drop_index("ix_memory_events_session", table_name="memory_events")
    op.drop_index("ix_memory_events_subject", table_name="memory_events")
    op.drop_table("memory_events")
    op.drop_index("ix_memory_subjects_retention", table_name="memory_subjects")
    op.drop_table("memory_subjects")
