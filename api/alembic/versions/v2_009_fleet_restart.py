"""v2_009_fleet_restart: fleet_desired.restart_generation + restart_requested_at

Ruling R-V2-4 (PLAN-V2 §8, CONTRACTS-V2 §1.2): ``POST /v1/connections/{id}/fleet
{action: "restart"}`` bumps ``restart_generation``, which is part of
``desired_hash``; the supervisor's rolling replacement does the rest.

Schema only. Existing rows get ``restart_generation = 0`` and a NULL
``restart_requested_at``. Their stored ``desired_hash`` is still the pre-R-V2-4
value; the api rewrites it on the next read of ``GET /internal/v1/fleet/desired``
(``list_fleet_desired``), so a running supervised pool rolls once after this
migration plus an api restart. ``downgrade()`` drops both columns; the hashes
the api wrote in the meantime are recomputed the same way by the older code.

Revision ID: v2_009_fleet_restart
Revises: v2_008_agentconfig_v2
Create Date: 2026-09-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v2_009_fleet_restart"
down_revision: str | None = "v2_008_agentconfig_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the two restart columns to ``fleet_desired``."""
    with op.batch_alter_table("fleet_desired") as batch_op:
        batch_op.add_column(sa.Column("restart_generation", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("restart_requested_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Drop the two restart columns."""
    with op.batch_alter_table("fleet_desired") as batch_op:
        batch_op.drop_column("restart_requested_at")
        batch_op.drop_column("restart_generation")
