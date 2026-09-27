"""v5_006_agent_tests: agent_test_runs, agent_test_results

V5-29 (docs/v5/PLAN-V5.md §0.3, D-V5-29): text simulations and judges. The
test *cases* live in the agent's configuration (``AgentConfig.tests``) so they
version with it — no cases table, as the card rules. A run is one row of
``agent_test_runs`` (pinned to the ``config_version`` it started on, its totals
in ``summary``); each case of a run is one row of ``agent_test_results`` with
its scratch session, the tool mocks that session's resolve delivers
(``ResolvedAgentConfig.tool_mocks``) and the case's verdict. The ledger's
``agent_tests`` name is not used: that table would hold editable cases, which
the card rules out.

New tables only: additive on both dialects, no rebuild of an existing table.
Deleting an agent deletes its runs (and their results); purging a session sets
its results' ``session_id`` to null.

Revision ID: v5_006_agent_tests
Revises: v5_002_session_uploads
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v5_006_agent_tests"
down_revision: str | None = "v5_002_session_uploads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``agent_test_runs`` and ``agent_test_results`` with their checks and indexes."""
    op.create_table(
        "agent_test_runs",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("workspace_id", sa.String(32), nullable=False),
        sa.Column("agent_id", sa.String(32), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','passed','failed','inconclusive','error')",
            name=op.f("ck_agent_test_runs_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_agent_test_runs_agent_id_agents"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_agent_test_runs_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_test_runs")),
    )
    op.create_index("ix_agent_test_runs_agent", "agent_test_runs", ["agent_id", "created_at"])
    op.create_index("ix_agent_test_runs_workspace", "agent_test_runs", ["workspace_id"])

    op.create_table(
        "agent_test_results",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("workspace_id", sa.String(32), nullable=False),
        sa.Column("case_id", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(32), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("mocks", sa.JSON(), nullable=True),
        sa.Column("verdict", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','running','passed','failed','inconclusive','error')",
            name=op.f("ck_agent_test_results_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_test_runs.id"],
            name=op.f("fk_agent_test_results_run_id_agent_test_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_agent_test_results_session_id_sessions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_agent_test_results_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_test_results")),
    )
    op.create_index("ix_agent_test_results_run", "agent_test_results", ["run_id", "ordinal"])
    op.create_index("ix_agent_test_results_session", "agent_test_results", ["session_id"])
    op.create_index("ix_agent_test_results_workspace", "agent_test_results", ["workspace_id"])


def downgrade() -> None:
    """Drop both tables (the scratch sessions the runs created stay, like any other session)."""
    op.drop_index("ix_agent_test_results_workspace", table_name="agent_test_results")
    op.drop_index("ix_agent_test_results_session", table_name="agent_test_results")
    op.drop_index("ix_agent_test_results_run", table_name="agent_test_results")
    op.drop_table("agent_test_results")
    op.drop_index("ix_agent_test_runs_workspace", table_name="agent_test_runs")
    op.drop_index("ix_agent_test_runs_agent", table_name="agent_test_runs")
    op.drop_table("agent_test_runs")
