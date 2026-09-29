"""v6_003_credential_last_used: credentials.last_used_at

V6-32 (user request 2026-09-30): the key list shows when a key was last used. Nothing stored
already says so for every use (a session's resolve, a tool call, a catalog read, a
knowledge-base embed), so the api stamps a nullable ``credentials.last_used_at`` when it
decrypts a key for one of those, at most once a minute per key
(``lkap_api.credential_usage.mark_used``). Additive only: existing rows read ``NULL`` ("not
used since this release"); no table rebuild on either dialect.

Chained after ``v6_002_datasets``, the only head when V6-32 started. The coordinator applies it
to the dev database after a backup (PLAN-V6 §0.1); the package never does.

Revision ID: v6_003_credential_last_used
Revises: v6_002_datasets
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v6_003_credential_last_used"
down_revision: str | None = "v6_002_datasets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable ``credentials.last_used_at`` column (``ADD COLUMN`` on both dialects)."""
    op.add_column("credentials", sa.Column("last_used_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Drop ``credentials.last_used_at`` (only the "last used" line is lost).

    SQLite uses its native ``ALTER TABLE ... DROP COLUMN`` (3.35+) rather than a
    batch rebuild, which would reflect the table and could lose its constraints.
    """
    if op.get_bind().dialect.name == "sqlite":
        op.execute("ALTER TABLE credentials DROP COLUMN last_used_at")
    else:
        op.drop_column("credentials", "last_used_at")
