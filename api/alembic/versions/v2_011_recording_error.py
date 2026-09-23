"""v2_011_recording_error: sessions.recording_error

docs/v2/_asks.md V2-20-3: a failed recording *start* (worker `recording`
session event, `status="failed"`) left `sessions.recording_status` at
`"none"` with no reason recorded anywhere queryable — the console's Recording
tab had nothing to show. `recording_status` already had a `'failed'` value in
its CHECK constraint (added by `v2_005_sessions_ext`), so only the reason
text was missing a home.

A plain nullable column needs no `batch_alter_table`/`copy_from` rebuild on
SQLite (no constraint touches it), unlike `v2_005`/`v2_010`'s CHECK changes.

Revision ID: v2_011_recording_error
Revises: v2_010_qa_status
Create Date: 2026-09-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v2_011_recording_error"
down_revision: str | None = "v2_010_qa_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add `sessions.recording_error` (nullable text, no default)."""
    op.add_column("sessions", sa.Column("recording_error", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop `sessions.recording_error`."""
    op.drop_column("sessions", "recording_error")
