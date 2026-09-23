"""v2_010_qa_status: session_qa.status gains 'skipped'; += scored_by

Ruling R-V2-5 (PLAN-V2 §8, CONTRACTS-V2 §1.4): the judge now runs in the
**worker** at session end (`PUT /internal/v1/sessions/{id}/qa`,
`scored_by="worker"`); the api's own `qa/scorer.py` only re-scores
(`scored_by="api"`), and only for an OpenAI-compatible vendor-key judge.
`status="skipped"` is what the worker reports when `AgentConfig.qa.enabled`
is false.

As in `v2_003`/`v2_005`, the pre-migration shape of `session_qa` is spelled
out for `copy_from` so the SQLite rebuild keeps every existing constraint's
name (`ck_session_qa_status_valid`, `ck_session_qa_sentiment_valid`,
`fk_session_qa_session_id_sessions`, `pk_session_qa`).

Revision ID: v2_010_qa_status
Revises: v2_009_fleet_restart
Create Date: 2026-09-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v2_010_qa_status"
down_revision: str | None = "v2_009_fleet_restart"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _session_qa_table(*, with_scored_by: bool) -> sa.Table:
    """The `session_qa` shape before (`v2_005`) or after (`v2_010`) this revision."""
    meta = sa.MetaData(naming_convention=NAMING_CONVENTION)
    columns: list[sa.schema.SchemaItem] = [
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
    ]
    if with_scored_by:
        columns.append(sa.Column("scored_by", sa.String(length=16), nullable=True))
    columns += [
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("sentiment", sa.String(length=16), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("model", sa.String(length=128), server_default="", nullable=False),
        sa.Column("scored_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    ]
    status_values = "'pending','done','failed','skipped'" if with_scored_by else "'pending','done','failed'"
    constraints: list[sa.schema.SchemaItem] = [
        sa.CheckConstraint(f"status IN ({status_values})", name="status_valid"),
        sa.CheckConstraint(
            "sentiment IS NULL OR sentiment IN ('positive','neutral','negative')", name="sentiment_valid"
        ),
    ]
    if with_scored_by:
        constraints.append(
            sa.CheckConstraint("scored_by IS NULL OR scored_by IN ('worker','api')", name="scored_by_valid")
        )
    constraints += [
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name="fk_session_qa_session_id_sessions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", name="pk_session_qa"),
    ]
    return sa.Table("session_qa", meta, *columns, *constraints)


def upgrade() -> None:
    """Add `scored_by` and widen the `status` CHECK to include `skipped`."""
    with op.batch_alter_table(
        "session_qa", copy_from=_session_qa_table(with_scored_by=False), naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.add_column(sa.Column("scored_by", sa.String(length=16), nullable=True))
        batch_op.drop_constraint("status_valid", type_="check")
        batch_op.create_check_constraint("status_valid", "status IN ('pending','done','failed','skipped')")
        batch_op.create_check_constraint(
            "scored_by_valid", "scored_by IS NULL OR scored_by IN ('worker','api')"
        )


def downgrade() -> None:
    """Drop `scored_by` and restore the narrower `status` CHECK.

    Existing `skipped` rows would violate the restored constraint, so they
    are rewritten to `pending` first.
    """
    op.execute("UPDATE session_qa SET status = 'pending' WHERE status = 'skipped'")
    with op.batch_alter_table(
        "session_qa", copy_from=_session_qa_table(with_scored_by=True), naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint("scored_by_valid", type_="check")
        batch_op.drop_constraint("status_valid", type_="check")
        batch_op.create_check_constraint("status_valid", "status IN ('pending','done','failed')")
        batch_op.drop_column("scored_by")
