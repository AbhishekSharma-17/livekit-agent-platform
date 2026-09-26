"""v5_002_session_uploads: session_assets

V5-19 (docs/v5/PLAN-V5.md §0.3, D-V5-35): the files of a session (a caller's
upload, a pinned camera frame, a drawn signature, a knowledge-base document
copied in so a citation can open it) are rows here; their bytes live in the
configured storage backend under ``sessions/<session id>/``, never in the
database. ``storage_key`` is server-generated (a random id plus the extension
of the sniffed media type) and unique; ``sha256`` is the hex digest of the
stored bytes. ``meta`` (JSON, nullable) carries ``block_id`` for an upload and
``document_id`` for a copied document; ``kind`` adds ``document`` to the
ledger's ``upload|frame|signature`` for the R-V5-5 copies.

A new table only: additive on both dialects, no rebuild of an existing table.

Chained after ``v5_009_consent``, the head when V5-19 started (ledger numbers
are not chain order).

Revision ID: v5_002_session_uploads
Revises: v5_004_mcp_oauth
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v5_002_session_uploads"
down_revision: str | None = "v5_004_mcp_oauth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``session_assets`` with its kind check and the per-session index."""
    op.create_table(
        "session_assets",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("session_id", sa.String(32), nullable=False),
        sa.Column("workspace_id", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("mime", sa.String(128), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('upload','frame','signature','document')", name=op.f("ck_session_assets_kind_valid")
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_session_assets_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_session_assets_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_assets")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_session_assets_storage_key")),
    )
    op.create_index("ix_session_assets_session", "session_assets", ["session_id", "created_at"])
    op.create_index("ix_session_assets_workspace", "session_assets", ["workspace_id"])


def downgrade() -> None:
    """Drop ``session_assets`` (the stored bytes stay in the storage backend under ``sessions/``)."""
    op.drop_index("ix_session_assets_workspace", table_name="session_assets")
    op.drop_index("ix_session_assets_session", table_name="session_assets")
    op.drop_table("session_assets")
