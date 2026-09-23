"""v2_007_telephony: SIP trunks, dispatch rules, numbers and calls

CONTRACTS-V2 §1.6. Telephony itself is a Phase 1 *stretch* package (V2-17), but
its schema ships here so no later wave has to add a migration to the chain.

Revision ID: v2_007_telephony
Revises: v2_006_webhooks_jobs
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v2_007_telephony"
down_revision: str | None = "v2_006_webhooks_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the telephony tables."""
    op.create_table(
        "sip_trunks",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("connection_id", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("lk_trunk_id", sa.String(length=128), nullable=True),
        sa.Column("numbers", sa.JSON(), nullable=False),
        sa.Column("provider_hint", sa.String(length=16), server_default="other", nullable=False),
        sa.Column("address", sa.String(length=512), nullable=True),
        sa.Column("auth_username", sa.String(length=200), nullable=True),
        sa.Column("auth_password_ct", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("direction IN ('inbound','outbound')", name=op.f("ck_sip_trunks_direction_valid")),
        sa.CheckConstraint(
            "provider_hint IN ('twilio','telnyx','other')",
            name=op.f("ck_sip_trunks_provider_hint_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_sip_trunks_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["livekit_connections.id"],
            name=op.f("fk_sip_trunks_connection_id_livekit_connections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sip_trunks")),
    )
    op.create_index("ix_sip_trunks_workspace", "sip_trunks", ["workspace_id"], unique=False)

    op.create_table(
        "sip_dispatch_rules",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("connection_id", sa.String(length=32), nullable=False),
        sa.Column("lk_rule_id", sa.String(length=128), nullable=True),
        sa.Column("trunk_id", sa.String(length=32), nullable=False),
        sa.Column("numbers", sa.JSON(), nullable=False),
        sa.Column("agent_id", sa.String(length=32), nullable=False),
        sa.Column("room_prefix", sa.String(length=128), server_default="", nullable=False),
        sa.Column("pin", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_sip_dispatch_rules_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["livekit_connections.id"],
            name=op.f("fk_sip_dispatch_rules_connection_id_livekit_connections"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trunk_id"],
            ["sip_trunks.id"],
            name=op.f("fk_sip_dispatch_rules_trunk_id_sip_trunks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_sip_dispatch_rules_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sip_dispatch_rules")),
    )
    op.create_index("ix_sip_dispatch_rules_workspace", "sip_dispatch_rules", ["workspace_id"], unique=False)

    op.create_table(
        "phone_numbers",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("e164", sa.String(length=32), nullable=False),
        sa.Column("trunk_id", sa.String(length=32), nullable=True),
        sa.Column("inbound_agent_id", sa.String(length=32), nullable=True),
        sa.Column("label", sa.String(length=200), server_default="", nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_phone_numbers_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trunk_id"],
            ["sip_trunks.id"],
            name=op.f("fk_phone_numbers_trunk_id_sip_trunks"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["inbound_agent_id"],
            ["agents.id"],
            name=op.f("fk_phone_numbers_inbound_agent_id_agents"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_phone_numbers")),
        sa.UniqueConstraint("e164", name=op.f("uq_phone_numbers_e164")),
    )
    op.create_index("ix_phone_numbers_workspace", "phone_numbers", ["workspace_id"], unique=False)

    op.create_table(
        "calls",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.String(length=32), nullable=True),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("connection_id", sa.String(length=32), nullable=True),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("from_e164", sa.String(length=32), server_default="", nullable=False),
        sa.Column("to_e164", sa.String(length=32), server_default="", nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("sip_call_id", sa.String(length=128), nullable=True),
        sa.Column("lk_participant_identity", sa.String(length=200), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("hangup_reason", sa.String(length=128), nullable=True),
        sa.Column("transfer_to", sa.String(length=64), nullable=True),
        sa.CheckConstraint("direction IN ('inbound','outbound')", name=op.f("ck_calls_direction_valid")),
        sa.CheckConstraint(
            "status IN ('dialing','ringing','answered','no_answer','busy','failed',"
            "'completed','transferred')",
            name=op.f("ck_calls_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_calls_session_id_sessions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_calls_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["livekit_connections.id"],
            name=op.f("fk_calls_connection_id_livekit_connections"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calls")),
    )
    op.create_index("ix_calls_workspace", "calls", ["workspace_id"], unique=False)


def downgrade() -> None:
    """Drop the telephony tables."""
    op.drop_index("ix_calls_workspace", table_name="calls")
    op.drop_table("calls")
    op.drop_index("ix_phone_numbers_workspace", table_name="phone_numbers")
    op.drop_table("phone_numbers")
    op.drop_index("ix_sip_dispatch_rules_workspace", table_name="sip_dispatch_rules")
    op.drop_table("sip_dispatch_rules")
    op.drop_index("ix_sip_trunks_workspace", table_name="sip_trunks")
    op.drop_table("sip_trunks")
