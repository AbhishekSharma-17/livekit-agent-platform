"""v5_009_consent: sessions.consent_state

V5-15 (docs/v5/PLAN-V5.md §0.3): a session's consent answers are ``consent``
session events; the latest answer per kind is also kept on the session row as
``sessions.consent_state`` (``lkap_contracts.compliance.ConsentState``, JSON,
nullable) so the recording start can refuse a consent-gated recording without
reading the event timeline. Additive only: existing rows read ``NULL`` ("no
answer yet"); no table rebuild on either dialect.

Chained after ``v5_010_tool_provider_kind``, the head when V5-15 started (the
ledger id ``v5_009`` was reserved before V5-47 took ``v5_010``).

Revision ID: v5_009_consent
Revises: v5_003_pgvector
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v5_009_consent"
down_revision: str | None = "v5_003_pgvector"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable ``sessions.consent_state`` JSON column (``ADD COLUMN`` on both dialects)."""
    op.add_column("sessions", sa.Column("consent_state", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Drop ``sessions.consent_state`` (the ``consent`` events keep every answer).

    SQLite uses its native ``ALTER TABLE ... DROP COLUMN`` (3.35+) rather than a
    batch rebuild, which would reflect the table and lose its CHECK constraints.
    """
    if op.get_bind().dialect.name == "sqlite":
        op.execute("ALTER TABLE sessions DROP COLUMN consent_state")
    else:
        op.drop_column("sessions", "consent_state")
