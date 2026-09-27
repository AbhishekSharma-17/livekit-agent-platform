"""v5_005_knowledge_connections: knowledge connections and the knowledge base's store binding

V5-20 (docs/v5/PLAN-V5.md §0.3, D-V5-16, D-V5-19, D-V5-37). Additive only:

* ``knowledge_connections`` (new): ``id, workspace_id → workspaces ON DELETE
  CASCADE, name, kind, settings JSON (non-secret), credential_id → credentials
  ON DELETE SET NULL, status (unverified|ok|error), last_checked_at,
  last_error, capabilities JSON, created_at, updated_at``, indexed by
  workspace. ``kind`` (qdrant|pinecone|weaviate|cohere_rerank|voyage_rerank) has
  no CHECK constraint so a later kind (V5-45's ``ragie``) needs no rebuild; the
  api validates it against the contracts literal.
* ``knowledge_bases``: ``connection_id`` (nullable, → ``knowledge_connections``,
  no ``ondelete`` action: a connection with knowledge bases is refused with a
  409 before any delete), ``kind`` (``managed`` by default, so every existing
  row reads as before) and ``external_ref`` (nullable). Existing knowledge
  bases keep ``connection_id = NULL`` = the platform's own store, unchanged.

On SQLite the new foreign key rebuilds ``knowledge_bases`` through
``batch_alter_table`` (the ``v2_004`` precedent: the table carries no CHECK
constraint, so reflection is lossless, and the migration connection does not
enable ``PRAGMA foreign_keys``, so dropping the old copy cascades nothing).

Chained after ``v5_006_agent_tests`` (re-chained at merge; V5-20 started on ``v5_002_session_uploads``)
(ledger numbers are not chain order).

Revision ID: v5_005_knowledge_connections
Revises: v5_006_agent_tests
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v5_005_knowledge_connections"
down_revision: str | None = "v5_006_agent_tests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
KB_CONNECTION_FK = "fk_knowledge_bases_connection_id_knowledge_connections"


def upgrade() -> None:
    """Create ``knowledge_connections`` and bind ``knowledge_bases`` to it."""
    op.create_table(
        "knowledge_connections",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("workspace_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("credential_id", sa.String(32), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('unverified','ok','error')", name=op.f("ck_knowledge_connections_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            name=op.f("fk_knowledge_connections_credential_id_credentials"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_knowledge_connections_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_connections")),
    )
    op.create_index("ix_knowledge_connections_workspace", "knowledge_connections", ["workspace_id"])

    with op.batch_alter_table("knowledge_bases", naming_convention=NAMING_CONVENTION) as batch_op:
        batch_op.add_column(sa.Column("connection_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("kind", sa.String(length=16), nullable=False, server_default="managed"))
        batch_op.add_column(sa.Column("external_ref", sa.String(length=512), nullable=True))
        batch_op.create_foreign_key(KB_CONNECTION_FK, "knowledge_connections", ["connection_id"], ["id"])


def downgrade() -> None:
    """Drop the knowledge base columns, then ``knowledge_connections``.

    A knowledge base stored through a connection falls back to the platform's
    own store on downgrade (its vectors stay in the vendor's service); re-index
    it with ``python -m lkap_api.kb.jobs reindex --kb <id>`` afterwards.
    """
    with op.batch_alter_table("knowledge_bases", naming_convention=NAMING_CONVENTION) as batch_op:
        batch_op.drop_constraint(KB_CONNECTION_FK, type_="foreignkey")
        batch_op.drop_column("external_ref")
        batch_op.drop_column("kind")
        batch_op.drop_column("connection_id")
    op.drop_index("ix_knowledge_connections_workspace", table_name="knowledge_connections")
    op.drop_table("knowledge_connections")
