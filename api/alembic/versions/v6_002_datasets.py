"""v6_002_datasets: datasets (lookup tables) and the ``dataset`` tool kind

V6-16 (docs/v6/PLAN-V6.md D-V6-27, §0.3 ledger): three new tables and one widened CHECK.

* ``datasets`` — a workspace's lookup table: ``name``, ``slug`` (unique per workspace),
  ``format`` (``csv``/``json``), ``columns`` and ``key_columns`` (JSON), ``row_count``,
  ``storage_key`` (the uploaded bytes in the storage backend), ``sha256``, ``status``
  (``pending``/``ready``/``failed``).
* ``dataset_rows`` — one row per data row (``ordinal``, ``keys`` and ``row`` JSON), indexed on
  ``(dataset_id, ordinal)``; cascades from ``datasets``.
* ``dataset_keys`` — per declared key column, the row's normalised value, indexed on
  ``(dataset_id, column_name, value)`` for exact and prefix lookups; primary key
  ``(row_id, column_name)``; cascades from both. The ledger's ``column`` is spelt
  ``column_name`` (``column`` is reserved on SQLite and Postgres).
* ``tools.kind`` CHECK widens from ``('http','mcp','provider')`` to
  ``('http','mcp','provider','dataset')``. As in ``v5_010``, the pre-migration shape of
  ``tools`` is spelt out for ``copy_from`` so the SQLite rebuild keeps every constraint's name
  and ``ix_tools_workspace`` (SQLite reflection cannot see CHECK constraints). Nothing between
  ``v5_010`` and this revision changed ``tools`` (``v5_004`` only points a foreign key at it;
  the migration connection does not enable ``PRAGMA foreign_keys``, so the rebuild's drop of
  the old copy cascades nothing).

Postgres gets no GIN on ``dataset_rows.row``: lookups read ``dataset_keys`` only (the card's
"only if a need is measured").

The downgrade deletes ``dataset`` tool rows (the narrower CHECK would refuse them; older code
cannot run them), drops the three tables, then restores the old CHECK. Agents that listed those
tools keep a dangling id in ``tools.tool_ids``, which validation reports as "unknown tool".

Revision ID: v6_002_datasets
Revises: v5_008_memory
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v6_002_datasets"
down_revision: str | None = "v5_008_memory"
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

NARROW = "kind IN ('http','mcp','provider')"
WIDE = "kind IN ('http','mcp','provider','dataset')"


def _tools_table(check: str) -> sa.Table:
    """The ``tools`` shape (as ``v2_003`` left it, ``v5_010``'s CHECK) with the given kind CHECK."""
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
    """Create the dataset tables and widen ``tools.kind`` to include ``dataset``."""
    op.create_table(
        "datasets",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("columns", sa.JSON(), nullable=False),
        sa.Column("key_columns", sa.JSON(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('pending','ready','failed')", name=op.f("ck_datasets_status_valid")),
        sa.CheckConstraint("format IN ('csv','json')", name=op.f("ck_datasets_format_valid")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_datasets_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_datasets")),
        sa.UniqueConstraint("workspace_id", "slug", name=op.f("uq_datasets_workspace_slug")),
    )
    op.create_index("ix_datasets_workspace", "datasets", ["workspace_id"])

    op.create_table(
        "dataset_rows",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("dataset_id", sa.String(length=32), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("keys", sa.JSON(), nullable=False),
        sa.Column("row", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name=op.f("fk_dataset_rows_dataset_id_datasets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_rows")),
    )
    op.create_index("ix_dataset_rows_dataset_ordinal", "dataset_rows", ["dataset_id", "ordinal"])

    op.create_table(
        "dataset_keys",
        sa.Column("row_id", sa.String(length=32), nullable=False),
        sa.Column("column_name", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=32), nullable=False),
        sa.Column("value", sa.String(length=256), nullable=False),
        sa.ForeignKeyConstraint(
            ["row_id"],
            ["dataset_rows.id"],
            name=op.f("fk_dataset_keys_row_id_dataset_rows"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name=op.f("fk_dataset_keys_dataset_id_datasets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("row_id", "column_name", name=op.f("pk_dataset_keys")),
    )
    op.create_index("ix_dataset_keys_lookup", "dataset_keys", ["dataset_id", "column_name", "value"])

    with op.batch_alter_table(
        "tools", copy_from=_tools_table(NARROW), naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint("kind_valid", type_="check")
        batch_op.create_check_constraint("kind_valid", WIDE)


def downgrade() -> None:
    """Delete ``dataset`` tool rows, drop the dataset tables, restore the narrower CHECK."""
    op.execute("DELETE FROM tools WHERE kind = 'dataset'")
    op.drop_index("ix_dataset_keys_lookup", table_name="dataset_keys")
    op.drop_table("dataset_keys")
    op.drop_index("ix_dataset_rows_dataset_ordinal", table_name="dataset_rows")
    op.drop_table("dataset_rows")
    op.drop_index("ix_datasets_workspace", table_name="datasets")
    op.drop_table("datasets")
    with op.batch_alter_table(
        "tools", copy_from=_tools_table(WIDE), naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint("kind_valid", type_="check")
        batch_op.create_check_constraint("kind_valid", NARROW)
