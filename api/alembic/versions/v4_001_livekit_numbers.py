"""v4_001_livekit_numbers: LiveKit-hosted phone numbers (V4-05)

docs/v4/PHONE-NUMBERS.md §4.1, D-V4-14 and D-V4-17 (PLAN-V4 V4-05, R-V4-11):

* ``phone_numbers`` += ``source`` (``'trunk'`` for every existing row, CHECKed
  against ``('trunk','livekit')``), ``connection_id`` (FK, cascade), the
  LiveKit mirror fields ``lk_number_id`` (unique per workspace),
  ``lk_status``, ``lk_inbound_status``, ``lk_rule_ids`` (JSON, ``[]``),
  ``region`` and ``lk_synced_at``.
* ``sip_dispatch_rules.trunk_id`` becomes nullable (a hosted number's managed
  rule has no trunk) and the table gains ``phone_number_id`` (FK, ``SET
  NULL``), the managed-rule link that replaces the ``(trunk_id, numbers ==
  [e164])`` match.
* Backfill: a rule whose ``(workspace_id, trunk_id, numbers)`` equals a
  number's ``(workspace_id, trunk_id, [e164])`` and whose ``agent_id`` is the
  number's ``inbound_agent_id`` gets that number's id. Done in Python because
  JSON equality is not portable SQL.

SQLite needs table rebuilds (nullable change, CHECK, FKs), so as in ``v3_001``
each table's shape is spelled out for ``copy_from`` and keeps every
constraint name and index. On Postgres the batches run as plain ``ALTER
TABLE`` statements.

The downgrade **deletes** what the older schema cannot hold, and logs the
counts: every dispatch rule without a trunk (a hosted number's managed rule;
the LiveKit object is left in the project) and every ``source='livekit'``
number row (the number stays in the LiveKit project; a later Refresh mirrors
it again). Trunk numbers and trunk rules survive unchanged.

Rehearsal log: ``docs/v4/_briefs/migration-rehearsal-v4.md``. Only the
coordinator applies this to the live database (HANDOFF rule 4).

Revision ID: v4_001_livekit_numbers
Revises: v3_001_agent_keys
Create Date: 2026-09-25
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "v4_001_livekit_numbers"
down_revision: str | None = "v3_001_agent_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SOURCE_CHECK = "source IN ('trunk','livekit')"
LK_NUMBER_UNIQUE = "uq_phone_numbers_workspace_lk_number"
FK_NUMBER_CONNECTION = "fk_phone_numbers_connection_id_livekit_connections"
FK_RULE_NUMBER = "fk_sip_dispatch_rules_phone_number_id_phone_numbers"


def _phone_numbers_table(*, with_livekit_columns: bool) -> sa.Table:
    """The ``phone_numbers`` shape before (``v2_007``) or after (``v4_001``) this revision."""
    meta = sa.MetaData(naming_convention=NAMING_CONVENTION)
    sa.Table("workspaces", meta, sa.Column("id", sa.String(length=32), primary_key=True))
    sa.Table("sip_trunks", meta, sa.Column("id", sa.String(length=32), primary_key=True))
    sa.Table("agents", meta, sa.Column("id", sa.String(length=32), primary_key=True))
    sa.Table("livekit_connections", meta, sa.Column("id", sa.String(length=32), primary_key=True))
    columns: list[sa.schema.SchemaItem] = [
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("e164", sa.String(length=32), nullable=False),
        sa.Column("trunk_id", sa.String(length=32), nullable=True),
        sa.Column("inbound_agent_id", sa.String(length=32), nullable=True),
        sa.Column("label", sa.String(length=200), server_default="", nullable=False),
    ]
    constraints: list[sa.schema.SchemaItem] = []
    if with_livekit_columns:
        columns += [
            sa.Column("source", sa.String(length=16), server_default="trunk", nullable=False),
            sa.Column("connection_id", sa.String(length=32), nullable=True),
            sa.Column("lk_number_id", sa.String(length=128), nullable=True),
            sa.Column("lk_status", sa.String(length=16), nullable=True),
            sa.Column("lk_inbound_status", sa.String(length=16), nullable=True),
            sa.Column("lk_rule_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
            sa.Column("region", sa.String(length=200), server_default="", nullable=False),
            sa.Column("lk_synced_at", sa.DateTime(), nullable=True),
        ]
        constraints += [
            sa.CheckConstraint(SOURCE_CHECK, name="source_valid"),
            sa.ForeignKeyConstraint(
                ["connection_id"], ["livekit_connections.id"], name=FK_NUMBER_CONNECTION, ondelete="CASCADE"
            ),
            sa.UniqueConstraint("workspace_id", "lk_number_id", name=LK_NUMBER_UNIQUE),
        ]
    constraints += [
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_phone_numbers_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trunk_id"], ["sip_trunks.id"], name="fk_phone_numbers_trunk_id_sip_trunks", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["inbound_agent_id"],
            ["agents.id"],
            name="fk_phone_numbers_inbound_agent_id_agents",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_phone_numbers"),
        sa.UniqueConstraint("e164", name="uq_phone_numbers_e164"),
        sa.Index("ix_phone_numbers_workspace", "workspace_id"),
    ]
    return sa.Table("phone_numbers", meta, *columns, *constraints)


def _dispatch_rules_table(*, v4: bool) -> sa.Table:
    """The ``sip_dispatch_rules`` shape before (``v2_007``) or after (``v4_001``) this revision."""
    meta = sa.MetaData(naming_convention=NAMING_CONVENTION)
    sa.Table("workspaces", meta, sa.Column("id", sa.String(length=32), primary_key=True))
    sa.Table("sip_trunks", meta, sa.Column("id", sa.String(length=32), primary_key=True))
    sa.Table("agents", meta, sa.Column("id", sa.String(length=32), primary_key=True))
    sa.Table("livekit_connections", meta, sa.Column("id", sa.String(length=32), primary_key=True))
    sa.Table("phone_numbers", meta, sa.Column("id", sa.String(length=32), primary_key=True))
    columns: list[sa.schema.SchemaItem] = [
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("connection_id", sa.String(length=32), nullable=False),
        sa.Column("lk_rule_id", sa.String(length=128), nullable=True),
        sa.Column("trunk_id", sa.String(length=32), nullable=v4),
        sa.Column("numbers", sa.JSON(), nullable=False),
        sa.Column("agent_id", sa.String(length=32), nullable=False),
        sa.Column("room_prefix", sa.String(length=128), server_default="", nullable=False),
        sa.Column("pin", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    ]
    constraints: list[sa.schema.SchemaItem] = []
    if v4:
        columns.append(sa.Column("phone_number_id", sa.String(length=32), nullable=True))
        constraints.append(
            sa.ForeignKeyConstraint(
                ["phone_number_id"], ["phone_numbers.id"], name=FK_RULE_NUMBER, ondelete="SET NULL"
            )
        )
    constraints += [
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_sip_dispatch_rules_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["livekit_connections.id"],
            name="fk_sip_dispatch_rules_connection_id_livekit_connections",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trunk_id"],
            ["sip_trunks.id"],
            name="fk_sip_dispatch_rules_trunk_id_sip_trunks",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name="fk_sip_dispatch_rules_agent_id_agents", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sip_dispatch_rules"),
        sa.Index("ix_sip_dispatch_rules_workspace", "workspace_id"),
    ]
    return sa.Table("sip_dispatch_rules", meta, *columns, *constraints)


def _backfill_phone_number_ids() -> int:
    """Link every existing trunk-managed rule to its number; returns how many were linked."""
    bind = op.get_bind()
    numbers = sa.table(
        "phone_numbers",
        sa.column("id", sa.String()),
        sa.column("workspace_id", sa.String()),
        sa.column("e164", sa.String()),
        sa.column("trunk_id", sa.String()),
        sa.column("inbound_agent_id", sa.String()),
    )
    rules = sa.table(
        "sip_dispatch_rules",
        sa.column("id", sa.String()),
        sa.column("workspace_id", sa.String()),
        sa.column("trunk_id", sa.String()),
        sa.column("numbers", sa.JSON()),
        sa.column("agent_id", sa.String()),
        sa.column("phone_number_id", sa.String()),
    )
    by_key: dict[tuple[Any, Any, Any, Any], str] = {}
    for row in bind.execute(sa.select(numbers).where(numbers.c.trunk_id.is_not(None))):
        if row.inbound_agent_id:
            by_key[(row.workspace_id, row.trunk_id, row.e164, row.inbound_agent_id)] = row.id
    linked = 0
    for rule in bind.execute(sa.select(rules).where(rules.c.trunk_id.is_not(None))).all():
        called = list(rule.numbers or [])
        if len(called) != 1:
            continue
        number_id = by_key.get((rule.workspace_id, rule.trunk_id, called[0], rule.agent_id))
        if number_id is None:
            continue
        bind.execute(sa.update(rules).where(rules.c.id == rule.id).values(phone_number_id=number_id))
        linked += 1
    return linked


def upgrade() -> None:
    """Add the hosted-number columns, relax ``trunk_id``, add ``phone_number_id``, backfill it."""
    with op.batch_alter_table(
        "phone_numbers",
        copy_from=_phone_numbers_table(with_livekit_columns=False),
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        batch_op.add_column(sa.Column("source", sa.String(length=16), server_default="trunk", nullable=False))
        batch_op.add_column(sa.Column("connection_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("lk_number_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("lk_status", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("lk_inbound_status", sa.String(length=16), nullable=True))
        batch_op.add_column(
            sa.Column("lk_rule_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False)
        )
        batch_op.add_column(sa.Column("region", sa.String(length=200), server_default="", nullable=False))
        batch_op.add_column(sa.Column("lk_synced_at", sa.DateTime(), nullable=True))
        batch_op.create_check_constraint("source_valid", SOURCE_CHECK)
        batch_op.create_foreign_key(
            FK_NUMBER_CONNECTION, "livekit_connections", ["connection_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.create_unique_constraint(LK_NUMBER_UNIQUE, ["workspace_id", "lk_number_id"])

    with op.batch_alter_table(
        "sip_dispatch_rules", copy_from=_dispatch_rules_table(v4=False), naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.alter_column("trunk_id", existing_type=sa.String(length=32), nullable=True)
        batch_op.add_column(sa.Column("phone_number_id", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key(
            FK_RULE_NUMBER, "phone_numbers", ["phone_number_id"], ["id"], ondelete="SET NULL"
        )

    linked = _backfill_phone_number_ids()
    log.info("v4_001: linked %d existing managed dispatch rule(s) to their number", linked)


def downgrade() -> None:
    """Delete trunk-less rules and hosted-number rows (logged), then drop the v4 columns."""
    bind = op.get_bind()
    trunkless = bind.execute(sa.text("DELETE FROM sip_dispatch_rules WHERE trunk_id IS NULL")).rowcount
    log.warning(
        "v4_001 downgrade: deleted %d dispatch rule(s) without a trunk (LiveKit-hosted numbers' managed "
        "rules; the LiveKit objects stay in the project)",
        trunkless,
    )
    with op.batch_alter_table(
        "sip_dispatch_rules", copy_from=_dispatch_rules_table(v4=True), naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint(FK_RULE_NUMBER, type_="foreignkey")
        batch_op.drop_column("phone_number_id")
        batch_op.alter_column("trunk_id", existing_type=sa.String(length=32), nullable=False)

    hosted = bind.execute(sa.text("DELETE FROM phone_numbers WHERE source = 'livekit'")).rowcount
    log.warning(
        "v4_001 downgrade: deleted %d LiveKit-hosted number row(s) (the numbers stay in the LiveKit "
        "project; Refresh mirrors them again after an upgrade)",
        hosted,
    )
    with op.batch_alter_table(
        "phone_numbers",
        copy_from=_phone_numbers_table(with_livekit_columns=True),
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        batch_op.drop_constraint(LK_NUMBER_UNIQUE, type_="unique")
        batch_op.drop_constraint(FK_NUMBER_CONNECTION, type_="foreignkey")
        batch_op.drop_constraint("source_valid", type_="check")
        batch_op.drop_column("lk_synced_at")
        batch_op.drop_column("region")
        batch_op.drop_column("lk_rule_ids")
        batch_op.drop_column("lk_inbound_status")
        batch_op.drop_column("lk_status")
        batch_op.drop_column("lk_number_id")
        batch_op.drop_column("connection_id")
        batch_op.drop_column("source")
