"""v2_003_scope_tables: workspace scope on every v1 tenant table

Adds ``workspace_id`` (CONTRACTS-V2 §1.1) to ``agents``, ``credentials``,
``tools``, ``knowledge_bases`` and ``sessions``, plus the new ``agents``
columns. Existing rows are backfilled to ``DEFAULT_WORKSPACE_ID`` through the
column's server default, which is also what keeps v1 call sites working until
V2-02 supplies a real ``WorkspaceContext``.

Every ALTER runs through ``batch_alter_table(copy_from=...)``: SQLite cannot add
a foreign key or a check constraint in place, and its reflection cannot see
CHECK constraints, so the pre-migration shape of each table is spelled out here
rather than reflected — otherwise the rebuild would silently drop
``ck_sessions_status_valid`` and ``ck_tools_kind_valid``.

Revision ID: v2_003_scope_tables
Revises: v2_002_connections
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v2_003_scope_tables"
down_revision: str | None = "v2_002_connections"
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

#: Tables whose only change is a scoping column, in (table, index name) order.
SCOPED_ONLY = (
    ("credentials", "ix_credentials_workspace"),
    ("tools", "ix_tools_workspace"),
    ("knowledge_bases", "ix_knowledge_bases_workspace"),
    ("sessions", "ix_sessions_workspace"),
)


def _scope_columns(table_name: str) -> list[sa.schema.SchemaItem]:
    """Return the columns and constraints this revision adds to `table_name`."""
    items: list[sa.schema.SchemaItem] = [
        _workspace_column(),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=f"fk_{table_name}_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
    ]
    if table_name == "agents":
        items[1:1] = [
            sa.Column("connection_id", sa.String(length=32), nullable=True),
            sa.Column("mode", sa.String(length=16), nullable=False, server_default="prompt"),
            sa.Column("archived_at", sa.DateTime(), nullable=True),
            sa.Column("limits", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("allowed_origins", sa.JSON(), nullable=False, server_default="[]"),
        ]
        items += [
            sa.CheckConstraint("mode IN ('prompt','flow')", name="mode_valid"),
            sa.ForeignKeyConstraint(
                ["connection_id"],
                ["livekit_connections.id"],
                name="fk_agents_connection_id_livekit_connections",
                ondelete="RESTRICT",
            ),
        ]
    return items


def _v1_tables(*, scoped: bool = False) -> dict[str, sa.Table]:
    """Return each tenant table's shape before (or after) this revision.

    Args:
        scoped: When true, include the columns and constraints this revision
            adds, i.e. the shape `downgrade()` starts from.

    Returns:
        A mapping of table name to a standalone `Table` usable as `copy_from`.
    """
    meta = sa.MetaData(naming_convention=NAMING_CONVENTION)
    extra = _scope_columns if scoped else (lambda _name: [])
    agents = sa.Table(
        "agents",
        meta,
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("pack_id", sa.String(length=64), nullable=False),
        sa.Column("ui_panel_id", sa.String(length=64), nullable=False),
        sa.Column("published", sa.Integer(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agents"),
        sa.UniqueConstraint("slug", name="uq_agents_slug"),
        *extra("agents"),
    )
    sa.Table(
        "credentials",
        meta,
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("fingerprint", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_credentials"),
        *extra("credentials"),
    )
    sa.Table(
        "tools",
        meta,
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("agent_id", sa.String(length=32), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("kind IN ('http','mcp')", name="kind_valid"),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name="fk_tools_agent_id_agents", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tools"),
        *extra("tools"),
    )
    sa.Table(
        "knowledge_bases",
        meta,
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("embedder_id", sa.String(length=64), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_bases"),
        *extra("knowledge_bases"),
    )
    sessions = sa.Table(
        "sessions",
        meta,
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
        sa.CheckConstraint("status IN ('created','active','ended','failed')", name="status_valid"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name="fk_sessions_agent_id_agents"),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.UniqueConstraint("room_name", name="uq_sessions_room_name"),
        *extra("sessions"),
    )
    sa.Index("ix_sessions_agent", sessions.c.agent_id, sessions.c.created_at)
    return {"agents": agents, "sessions": sessions, **{t.name: t for t in meta.tables.values()}}


def _workspace_column() -> sa.Column[str]:
    return sa.Column(
        "workspace_id",
        sa.String(length=32),
        nullable=False,
        server_default=DEFAULT_WORKSPACE_ID,
    )


def upgrade() -> None:
    """Scope every v1 tenant table and add the new `agents` columns."""
    tables = _v1_tables()

    with op.batch_alter_table(
        "agents", copy_from=tables["agents"], naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.add_column(_workspace_column())
        batch_op.add_column(sa.Column("connection_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("mode", sa.String(length=16), nullable=False, server_default="prompt"))
        batch_op.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("limits", sa.JSON(), nullable=False, server_default="{}"))
        batch_op.add_column(sa.Column("allowed_origins", sa.JSON(), nullable=False, server_default="[]"))
        batch_op.create_check_constraint("mode_valid", "mode IN ('prompt','flow')")
        batch_op.create_foreign_key(
            "fk_agents_workspace_id_workspaces",
            "workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_agents_connection_id_livekit_connections",
            "livekit_connections",
            ["connection_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index("ix_agents_workspace", "agents", ["workspace_id"], unique=False)
    op.create_index("ix_agents_connection", "agents", ["connection_id"], unique=False)

    for table_name, index_name in SCOPED_ONLY:
        with op.batch_alter_table(
            table_name, copy_from=tables[table_name], naming_convention=NAMING_CONVENTION
        ) as batch_op:
            batch_op.add_column(_workspace_column())
            batch_op.create_foreign_key(
                f"fk_{table_name}_workspace_id_workspaces",
                "workspaces",
                ["workspace_id"],
                ["id"],
                ondelete="CASCADE",
            )
        columns = ["workspace_id", "created_at"] if table_name == "sessions" else ["workspace_id"]
        op.create_index(index_name, table_name, columns, unique=False)

    # The default connection may already exist (v2_002 saw the environment); bind
    # every agent to it now so `connect` never meets a NULL connection_id.
    agents = sa.table(
        "agents",
        sa.column("workspace_id", sa.String),
        sa.column("connection_id", sa.String),
    )
    connections = sa.table(
        "livekit_connections",
        sa.column("id", sa.String),
        sa.column("workspace_id", sa.String),
        sa.column("is_default", sa.Integer),
    )
    default_id = (
        sa.select(connections.c.id)
        .where(
            connections.c.workspace_id == DEFAULT_WORKSPACE_ID,
            connections.c.is_default == 1,
        )
        .scalar_subquery()
    )
    op.execute(
        agents.update()
        .where(agents.c.workspace_id == DEFAULT_WORKSPACE_ID, agents.c.connection_id.is_(None))
        .values(connection_id=default_id)
    )


def downgrade() -> None:
    """Drop the scoping columns and the new `agents` columns."""
    tables = _v1_tables(scoped=True)

    for table_name, index_name in reversed(SCOPED_ONLY):
        op.drop_index(index_name, table_name=table_name)
        with op.batch_alter_table(
            table_name, copy_from=tables[table_name], naming_convention=NAMING_CONVENTION
        ) as batch_op:
            batch_op.drop_constraint(f"fk_{table_name}_workspace_id_workspaces", type_="foreignkey")
            batch_op.drop_column("workspace_id")

    op.drop_index("ix_agents_connection", table_name="agents")
    op.drop_index("ix_agents_workspace", table_name="agents")
    with op.batch_alter_table(
        "agents", copy_from=tables["agents"], naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint("fk_agents_connection_id_livekit_connections", type_="foreignkey")
        batch_op.drop_constraint("fk_agents_workspace_id_workspaces", type_="foreignkey")
        batch_op.drop_constraint("mode_valid", type_="check")
        batch_op.drop_column("allowed_origins")
        batch_op.drop_column("limits")
        batch_op.drop_column("archived_at")
        batch_op.drop_column("mode")
        batch_op.drop_column("connection_id")
        batch_op.drop_column("workspace_id")
