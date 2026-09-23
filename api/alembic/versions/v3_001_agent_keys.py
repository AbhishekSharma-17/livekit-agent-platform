"""v3_001_agent_keys: api_keys += kind, client, last_client

docs/v3/AGENT-ACCESS.md §7 and D-V3-9 (PLAN-V3 V3-00, R-V3-10): keys minted
for AI coding agents are ``kind='agent'`` and remember the ``client`` chosen at
minting (``claude-code``, ``codex`` …); ``last_client`` is the product of the
last ``X-LKAP-Client`` header the key was used with, written at the same 60 s
resolution as ``last_used_at``. Every existing key becomes ``standard``.

``kind`` carries a CHECK constraint, so SQLite needs a table rebuild. As in
``v2_010``, the table's shape is spelled out for ``copy_from`` (it is unchanged
since ``v2_001_tenancy``) so the rebuild keeps every constraint's name and the
``ix_api_keys_workspace`` index. On Postgres the batch runs as plain
``ALTER TABLE`` statements.

Rehearsal log: ``docs/v3/_briefs/migration-rehearsal-v3.md``. Only the
coordinator applies this to the live database (HANDOFF rule 4).

Revision ID: v3_001_agent_keys
Revises: v2_011_recording_error
Create Date: 2026-09-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v3_001_agent_keys"
down_revision: str | None = "v2_011_recording_error"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

KIND_CHECK = "kind IN ('standard','agent')"


def _api_keys_table(*, with_agent_columns: bool) -> sa.Table:
    """The ``api_keys`` shape before (``v2_001``) or after (``v3_001``) this revision."""
    meta = sa.MetaData(naming_convention=NAMING_CONVENTION)
    columns: list[sa.schema.SchemaItem] = [
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
    ]
    constraints: list[sa.schema.SchemaItem] = []
    if with_agent_columns:
        columns += [
            sa.Column("kind", sa.String(length=16), server_default="standard", nullable=False),
            sa.Column("client", sa.String(length=64), nullable=True),
            sa.Column("last_client", sa.String(length=64), nullable=True),
        ]
        constraints.append(sa.CheckConstraint(KIND_CHECK, name="kind_valid"))
    constraints += [
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_api_keys_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_api_keys"),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
        sa.Index("ix_api_keys_workspace", "workspace_id"),
    ]
    return sa.Table("api_keys", meta, *columns, *constraints)


def upgrade() -> None:
    """Add ``kind`` (default ``standard``, CHECKed), ``client`` and ``last_client``."""
    with op.batch_alter_table(
        "api_keys", copy_from=_api_keys_table(with_agent_columns=False), naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.add_column(
            sa.Column("kind", sa.String(length=16), server_default="standard", nullable=False)
        )
        batch_op.add_column(sa.Column("client", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("last_client", sa.String(length=64), nullable=True))
        batch_op.create_check_constraint("kind_valid", KIND_CHECK)


def downgrade() -> None:
    """Drop the three columns and the ``kind`` CHECK; every key survives as it was."""
    with op.batch_alter_table(
        "api_keys", copy_from=_api_keys_table(with_agent_columns=True), naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint("kind_valid", type_="check")
        batch_op.drop_column("last_client")
        batch_op.drop_column("client")
        batch_op.drop_column("kind")
