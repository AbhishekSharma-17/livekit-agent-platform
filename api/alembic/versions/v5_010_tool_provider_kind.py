"""v5_010_tool_provider_kind: tools.kind gains 'provider'

V5-47 (docs/v5/COMPOSIO.md §3, D-V5-C8): a connected app's action is stored
as a ``tools`` row of kind ``provider`` (``ProviderToolDefinition``), so the
``ck_tools_kind_valid`` CHECK widens from ``('http','mcp')`` to
``('http','mcp','provider')``. Nothing else changes: no column, no index.

As in ``v2_003``/``v2_010``, the pre-migration shape of ``tools`` (the
``v2_003`` scoped shape) is spelled out for ``copy_from`` so the SQLite
rebuild keeps every constraint's name and the ``ix_tools_workspace`` index;
SQLite reflection cannot see CHECK constraints.

The downgrade deletes the ``provider`` rows first (the narrower CHECK would
refuse them; the older code cannot run them anyway) and restores the old
CHECK. Agents that listed them keep a dangling id in ``tools.tool_ids``,
which validation reports as "unknown tool".

Revision ID: v5_010_tool_provider_kind
Revises: v4_002_provider_models
Create Date: 2026-09-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v5_010_tool_provider_kind"
down_revision: str | None = "v4_002_provider_models"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_WORKSPACE_ID = "00000000000000000000000000000001"

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

NARROW = "kind IN ('http','mcp')"
WIDE = "kind IN ('http','mcp','provider')"


def _tools_table(check: str) -> sa.Table:
    """The ``tools`` shape (as ``v2_003`` left it) with the given kind CHECK."""
    meta = sa.MetaData(naming_convention=NAMING_CONVENTION)
    table = sa.Table(
        "tools",
        meta,
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("agent_id", sa.String(length=32), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False, server_default=DEFAULT_WORKSPACE_ID),
        sa.CheckConstraint(check, name="kind_valid"),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name="fk_tools_agent_id_agents", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_tools_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tools"),
    )
    sa.Index("ix_tools_workspace", table.c.workspace_id)
    return table


def upgrade() -> None:
    """Widen the ``tools.kind`` CHECK to include ``provider``."""
    with op.batch_alter_table(
        "tools", copy_from=_tools_table(NARROW), naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint("kind_valid", type_="check")
        batch_op.create_check_constraint("kind_valid", WIDE)


def downgrade() -> None:
    """Delete ``provider`` rows, then restore the ``('http','mcp')`` CHECK."""
    op.execute("DELETE FROM tools WHERE kind = 'provider'")
    with op.batch_alter_table(
        "tools", copy_from=_tools_table(WIDE), naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint("kind_valid", type_="check")
        batch_op.create_check_constraint("kind_valid", NARROW)
