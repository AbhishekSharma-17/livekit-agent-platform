"""v4_003_session_estimates: the creation-time estimate, reconciliation columns, new cost units (V4-15)

docs/v4/COSTS.md D-V4-43 (PLAN-V4 V4-15, pre-authorised by R-V4-47). Columns:

* ``sessions.estimate`` (JSON) — the ``CostEstimate`` snapshotted after creation
  at the pinned config version, trimmed; ``sessions.estimated_usd`` and
  ``sessions.reconciled_usd`` (``NUMERIC(12,6)``);
* ``usage_daily.estimated_usd`` (``NUMERIC(14,6)``);
* ``session_costs.price_source`` (``VARCHAR(16) NOT NULL DEFAULT 'table'``,
  CHECK ``table|live|workspace``), ``session_costs.vendor_usd``
  (``NUMERIC(12,6)``) and ``session_costs.vendor_ref`` (``VARCHAR(64)``).

``session_costs.unit_valid`` is widened to the six new ``pricing.Unit`` literals
(``text_tokens_in``, ``text_tokens_out``, ``audio_tokens_in``,
``audio_tokens_out``, ``cached_tokens_in``, ``requests``) — without it a
realtime or cached-token line could not be persisted. On SQLite the CHECK
change rebuilds ``session_costs`` (``batch_alter_table`` with the pre-migration
shape spelled out for ``copy_from``, as in ``v2_010``); Postgres alters in place.
No index changes.

Downgrade drops the new columns and restores the narrow CHECK. Rows priced
under a new unit would violate it, so the downgrade **deletes them first** (and
re-sums ``sessions.cost_usd`` is not attempted: a downgraded api simply shows
the remaining lines). The coordinator backs up the database before applying
either direction (HANDOFF rule 4).

``v5_001`` chains after this revision (PLAN-V5 §0.3).

Revision ID: v4_003_session_estimates
Revises: v4_002_provider_models
Create Date: 2026-09-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v4_003_session_estimates"
down_revision: str | None = "v4_002_provider_models"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

OLD_UNITS = "'tokens_in','tokens_out','audio_s_in','audio_s_out','chars','minutes','images'"
NEW_UNITS = (
    OLD_UNITS + ",'text_tokens_in','text_tokens_out','audio_tokens_in','audio_tokens_out',"
    "'cached_tokens_in','requests'"
)
PRICE_SOURCES = "'table','live','workspace'"


def _session_costs_table(*, after: bool) -> sa.Table:
    """The ``session_costs`` shape before (``v2_005``) or after this revision."""
    meta = sa.MetaData(naming_convention=NAMING_CONVENTION)
    columns: list[sa.schema.SchemaItem] = [
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), server_default="", nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("unit_price_usd", sa.Numeric(precision=18, scale=9), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("price_version", sa.String(length=32), server_default="", nullable=False),
    ]
    constraints: list[sa.schema.SchemaItem] = [
        sa.CheckConstraint(f"unit IN ({NEW_UNITS if after else OLD_UNITS})", name="unit_valid"),
    ]
    if after:
        columns += [
            sa.Column("price_source", sa.String(length=16), server_default="table", nullable=False),
            sa.Column("vendor_usd", sa.Numeric(precision=12, scale=6), nullable=True),
            sa.Column("vendor_ref", sa.String(length=64), nullable=True),
        ]
        constraints.append(
            sa.CheckConstraint(f"price_source IN ({PRICE_SOURCES})", name="price_source_valid")
        )
    constraints += [
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name="fk_session_costs_session_id_sessions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_session_costs"),
    ]
    table = sa.Table("session_costs", meta, *columns, *constraints)
    sa.Index("ix_session_costs_session", table.c.session_id)
    return table


def upgrade() -> None:
    """Add the estimate/reconciliation columns and widen ``session_costs.unit_valid``."""
    # Nullable, no default: a plain ALTER on both dialects (the `v2_011` precedent).
    op.add_column("sessions", sa.Column("estimate", sa.JSON(), nullable=True))
    op.add_column("sessions", sa.Column("estimated_usd", sa.Numeric(precision=12, scale=6), nullable=True))
    op.add_column("sessions", sa.Column("reconciled_usd", sa.Numeric(precision=12, scale=6), nullable=True))
    op.add_column("usage_daily", sa.Column("estimated_usd", sa.Numeric(precision=14, scale=6), nullable=True))
    with op.batch_alter_table(
        "session_costs",
        copy_from=_session_costs_table(after=False),
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        batch_op.add_column(
            sa.Column("price_source", sa.String(length=16), server_default="table", nullable=False)
        )
        batch_op.add_column(sa.Column("vendor_usd", sa.Numeric(precision=12, scale=6), nullable=True))
        batch_op.add_column(sa.Column("vendor_ref", sa.String(length=64), nullable=True))
        batch_op.drop_constraint("unit_valid", type_="check")
        batch_op.create_check_constraint("unit_valid", f"unit IN ({NEW_UNITS})")
        batch_op.create_check_constraint("price_source_valid", f"price_source IN ({PRICE_SOURCES})")


def downgrade() -> None:
    """Drop the new columns and restore the narrow unit CHECK (new-unit rows are deleted first)."""
    op.execute(f"DELETE FROM session_costs WHERE unit NOT IN ({OLD_UNITS})")
    with op.batch_alter_table(
        "session_costs",
        copy_from=_session_costs_table(after=True),
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        batch_op.drop_constraint("price_source_valid", type_="check")
        batch_op.drop_constraint("unit_valid", type_="check")
        batch_op.create_check_constraint("unit_valid", f"unit IN ({OLD_UNITS})")
        batch_op.drop_column("vendor_ref")
        batch_op.drop_column("vendor_usd")
        batch_op.drop_column("price_source")
    op.drop_column("usage_daily", "estimated_usd")
    op.drop_column("sessions", "reconciled_usd")
    op.drop_column("sessions", "estimated_usd")
    op.drop_column("sessions", "estimate")
