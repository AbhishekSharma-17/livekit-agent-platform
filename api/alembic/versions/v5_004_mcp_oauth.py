"""v5_004_mcp_oauth: pending MCP sign-ins and the OAuth clients LKAP holds (V5-14)

docs/v5/PLAN-V5.md §0.3 (the migration ledger) and the V5-14 card
(research-v4 tools §4.3.2):

* ``mcp_oauth_clients``: one OAuth client per workspace and authorization
  server (``issuer``), either registered dynamically (``dcr``, reused by every
  tool that signs in at that issuer) or pre-registered by an admin
  (``preregistered``, holding the client secret the admin supplied). Secrets
  live in ``ciphertext``, a Fernet vault bag.
* ``mcp_oauth_flows``: one row per sign-in in progress, keyed by the SHA-256 of
  the browser's ``state``; the PKCE verifier is a vault ciphertext; ten minutes,
  single use (``consumed_at``). Rows are short-lived and removed by the
  sessions sweep.

Additive only: two new tables, nothing existing changes. The downgrade drops
both (pending sign-ins and stored registrations go with them; the ``mcp-oauth``
credentials a finished sign-in wrote stay in ``credentials``).

Revision ID: v5_004_mcp_oauth
Revises: v5_003_pgvector
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v5_004_mcp_oauth"
down_revision: str | None = "v5_003_pgvector"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLIENTS_INDEX = "ix_mcp_oauth_clients_workspace_issuer"
FLOWS_TOOL_INDEX = "ix_mcp_oauth_flows_tool"
FLOWS_EXPIRES_INDEX = "ix_mcp_oauth_flows_expires"


def upgrade() -> None:
    """Create ``mcp_oauth_clients`` and ``mcp_oauth_flows``."""
    op.create_table(
        "mcp_oauth_clients",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("registration", sa.String(length=16), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("token_endpoint_auth_method", sa.String(length=32), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("registration_client_uri", sa.Text(), nullable=True),
        sa.Column("client_secret_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "registration IN ('preregistered','dcr')",
            name="ck_mcp_oauth_clients_registration_valid",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_mcp_oauth_clients_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mcp_oauth_clients"),
    )
    op.create_index(CLIENTS_INDEX, "mcp_oauth_clients", ["workspace_id", "issuer"], unique=False)

    op.create_table(
        "mcp_oauth_flows",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("tool_id", sa.String(length=32), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=True),
        sa.Column("verifier_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("iss_parameter_supported", sa.Boolean(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("token_endpoint", sa.Text(), nullable=False),
        sa.Column("revocation_endpoint", sa.Text(), nullable=True),
        sa.Column("registration", sa.String(length=16), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_row_id", sa.String(length=32), nullable=True),
        sa.Column("token_endpoint_auth_method", sa.String(length=32), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "registration IN ('preregistered','cimd','dcr')",
            name="ck_mcp_oauth_flows_registration_valid",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_mcp_oauth_flows_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tool_id"],
            ["tools.id"],
            name="fk_mcp_oauth_flows_tool_id_tools",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["client_row_id"],
            ["mcp_oauth_clients.id"],
            name="fk_mcp_oauth_flows_client_row_id_mcp_oauth_clients",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("state_hash", name="pk_mcp_oauth_flows"),
    )
    op.create_index(FLOWS_TOOL_INDEX, "mcp_oauth_flows", ["tool_id"], unique=False)
    op.create_index(FLOWS_EXPIRES_INDEX, "mcp_oauth_flows", ["expires_at"], unique=False)


def downgrade() -> None:
    """Drop both tables (flows first: they reference the clients)."""
    op.drop_index(FLOWS_EXPIRES_INDEX, table_name="mcp_oauth_flows")
    op.drop_index(FLOWS_TOOL_INDEX, table_name="mcp_oauth_flows")
    op.drop_table("mcp_oauth_flows")
    op.drop_index(CLIENTS_INDEX, table_name="mcp_oauth_clients")
    op.drop_table("mcp_oauth_clients")
