"""v5_007_telephony_amd: calls.amd_result, calls.transfer_mode, calls.transfer_summary

V5-32 (docs/v5/PLAN-V5.md §0.3, D-V5-21). Additive only, three nullable columns on
``calls``:

* ``amd_result`` (``String(32)``): what answered an outbound call when
  answering-machine detection ran (``human``, ``machine-ivr``, ``machine-vm``,
  ``machine-unavailable``, ``uncertain``; the api validates the value against
  ``lkap_contracts.telephony.AmdResult``, so no CHECK constraint and no rebuild).
* ``transfer_mode`` (``String(8)``): ``cold`` / ``warm``, the transfer that ran.
* ``transfer_summary`` (``Text``): the agent's summary for the person the caller
  was handed to (at most 2000 characters, enforced by the contracts model).

Existing rows read ``NULL`` ("no detection, not transferred by the agent"). No
table rebuild on either dialect; ``status_valid`` is untouched (``transferred``
already exists).

Chained after ``v5_005_knowledge_connections``, the head when V5-32 started (ledger
numbers are not chain order).

Revision ID: v5_007_telephony_amd
Revises: v5_005_knowledge_connections
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v5_007_telephony_amd"
down_revision: str | None = "v5_005_knowledge_connections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS: tuple[str, ...] = ("amd_result", "transfer_mode", "transfer_summary")


def upgrade() -> None:
    """Add the three nullable ``calls`` columns (``ADD COLUMN`` on both dialects)."""
    op.add_column("calls", sa.Column("amd_result", sa.String(32), nullable=True))
    op.add_column("calls", sa.Column("transfer_mode", sa.String(8), nullable=True))
    op.add_column("calls", sa.Column("transfer_summary", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the three columns.

    SQLite uses its native ``ALTER TABLE ... DROP COLUMN`` (3.35+) rather than a
    batch rebuild, which would reflect the table and lose its CHECK constraints.
    """
    sqlite = op.get_bind().dialect.name == "sqlite"
    for column in reversed(COLUMNS):
        if sqlite:
            op.execute(f"ALTER TABLE calls DROP COLUMN {column}")
        else:
            op.drop_column("calls", column)
