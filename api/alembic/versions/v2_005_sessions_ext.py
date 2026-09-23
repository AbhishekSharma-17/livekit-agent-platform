"""v2_005_sessions_ext: channel, recording, cost, latency on sessions (+ QA tables)

Extends ``sessions`` per CONTRACTS-V2 §1.4 and creates ``session_qa``,
``session_costs`` and ``usage_daily``. Existing rows get ``channel='web'`` (every
v1 session came from the browser ``connect`` route) and inherit their agent's
connection.

As in ``v2_003``, the pre-migration shape of ``sessions`` is spelled out for
``copy_from`` so the SQLite rebuild keeps ``ck_sessions_status_valid``.

Revision ID: v2_005_sessions_ext
Revises: v2_004_versions_providers
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v2_005_sessions_ext"
down_revision: str | None = "v2_004_versions_providers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

#: Columns this revision adds to `sessions`, in declaration order.
NEW_SESSION_COLUMNS: tuple[tuple[str, sa.types.TypeEngine[object], str | None], ...] = (
    ("connection_id", sa.String(length=32), None),
    ("channel", sa.String(length=16), "web"),
    ("caller", sa.JSON(), None),
    ("recording_status", sa.String(length=16), "none"),
    ("recording_egress_id", sa.String(length=128), None),
    ("recording_object_key", sa.String(length=512), None),
    ("recording_duration_s", sa.Numeric(precision=12, scale=3), None),
    ("cost_usd", sa.Numeric(precision=12, scale=6), None),
    ("latency", sa.JSON(), None),
    ("disposition", sa.String(length=128), None),
    ("variables", sa.JSON(), None),
    ("deleted_at", sa.DateTime(), None),
)


def _sessions_table(*, extended: bool) -> sa.Table:
    """Return the `sessions` shape before or after this revision."""
    meta = sa.MetaData(naming_convention=NAMING_CONVENTION)
    columns: list[sa.schema.SchemaItem] = [
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("agent_id", sa.String(length=32), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("room_name", sa.String(length=128), nullable=False),
        sa.Column("participant_identity", sa.String(length=128), nullable=False),
        sa.Column("participant_name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("pipeline_mode", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("transcript", sa.JSON(), nullable=True),
        sa.Column("final_ui_state", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "workspace_id",
            sa.String(length=32),
            nullable=False,
            server_default="00000000000000000000000000000001",
        ),
    ]
    constraints: list[sa.schema.SchemaItem] = [
        sa.CheckConstraint("status IN ('created','active','ended','failed')", name="status_valid"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name="fk_sessions_agent_id_agents"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_sessions_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.UniqueConstraint("room_name", name="uq_sessions_room_name"),
    ]
    if extended:
        columns += [
            sa.Column(name, type_, nullable=(default is None), server_default=default)
            for name, type_, default in NEW_SESSION_COLUMNS
        ]
        constraints += [
            sa.CheckConstraint(
                "channel IN ('web','test','text','sip_in','sip_out','widget','api')",
                name="channel_valid",
            ),
            sa.CheckConstraint(
                "recording_status IN ('none','requested','active','ready','failed')",
                name="recording_status_valid",
            ),
            sa.ForeignKeyConstraint(
                ["connection_id"],
                ["livekit_connections.id"],
                name="fk_sessions_connection_id_livekit_connections",
                ondelete="SET NULL",
            ),
        ]
    table = sa.Table("sessions", meta, *columns, *constraints)
    sa.Index("ix_sessions_agent", table.c.agent_id, table.c.created_at)
    sa.Index("ix_sessions_workspace", table.c.workspace_id, table.c.created_at)
    return table


def upgrade() -> None:
    """Extend `sessions` and create the QA, cost and rollup tables."""
    with op.batch_alter_table(
        "sessions", copy_from=_sessions_table(extended=False), naming_convention=NAMING_CONVENTION
    ) as batch_op:
        for name, type_, default in NEW_SESSION_COLUMNS:
            batch_op.add_column(sa.Column(name, type_, nullable=default is None, server_default=default))
        batch_op.create_check_constraint(
            "channel_valid", "channel IN ('web','test','text','sip_in','sip_out','widget','api')"
        )
        batch_op.create_check_constraint(
            "recording_status_valid",
            "recording_status IN ('none','requested','active','ready','failed')",
        )
        batch_op.create_foreign_key(
            "fk_sessions_connection_id_livekit_connections",
            "livekit_connections",
            ["connection_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_sessions_connection", "sessions", ["connection_id"], unique=False)

    op.create_table(
        "session_qa",
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("sentiment", sa.String(length=16), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("model", sa.String(length=128), server_default="", nullable=False),
        sa.Column("scored_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('pending','done','failed')", name=op.f("ck_session_qa_status_valid")),
        sa.CheckConstraint(
            "sentiment IS NULL OR sentiment IN ('positive','neutral','negative')",
            name=op.f("ck_session_qa_sentiment_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_session_qa_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", name=op.f("pk_session_qa")),
    )

    op.create_table(
        "session_costs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), server_default="", nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("unit_price_usd", sa.Numeric(precision=18, scale=9), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("price_version", sa.String(length=32), server_default="", nullable=False),
        sa.CheckConstraint(
            "unit IN ('tokens_in','tokens_out','audio_s_in','audio_s_out','chars','minutes','images')",
            name=op.f("ck_session_costs_unit_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_session_costs_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_costs")),
    )
    op.create_index("ix_session_costs_session", "session_costs", ["session_id"], unique=False)

    op.create_table(
        "usage_daily",
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("agent_id", sa.String(length=32), nullable=False),
        sa.Column("sessions", sa.Integer(), server_default="0", nullable=False),
        sa.Column("minutes", sa.Numeric(precision=12, scale=3), server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), server_default="0", nullable=False),
        sa.Column("failed", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_usage_daily_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "day", "agent_id", name=op.f("pk_usage_daily")),
    )

    # Every v1 session came from the browser `connect` route and belongs to the
    # connection its agent is now bound to.
    sessions = sa.table(
        "sessions",
        sa.column("agent_id", sa.String),
        sa.column("connection_id", sa.String),
    )
    agents = sa.table(
        "agents",
        sa.column("id", sa.String),
        sa.column("connection_id", sa.String),
    )
    op.execute(
        sessions.update()
        .where(sessions.c.connection_id.is_(None))
        .values(
            connection_id=sa.select(agents.c.connection_id)
            .where(agents.c.id == sessions.c.agent_id)
            .scalar_subquery()
        )
    )


def downgrade() -> None:
    """Drop the QA, cost and rollup tables and the new `sessions` columns."""
    op.drop_table("usage_daily")
    op.drop_index("ix_session_costs_session", table_name="session_costs")
    op.drop_table("session_costs")
    op.drop_table("session_qa")

    op.drop_index("ix_sessions_connection", table_name="sessions")
    with op.batch_alter_table(
        "sessions", copy_from=_sessions_table(extended=True), naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint("fk_sessions_connection_id_livekit_connections", type_="foreignkey")
        batch_op.drop_constraint("recording_status_valid", type_="check")
        batch_op.drop_constraint("channel_valid", type_="check")
        for name, _type, _default in reversed(NEW_SESSION_COLUMNS):
            batch_op.drop_column(name)
