"""v2_008_agentconfig_v2: rewrite every stored AgentConfig from v1 to v2

Data-only. Every ``agents.config`` and ``agent_config_versions.config`` document
goes through :func:`lkap_contracts.migrate.agent_config_v1_to_v2`, which is pure,
idempotent and unit-tested in the contracts package (V2-00). ``agents.ui_panel_id``
is updated to mirror ``config.panel.panel_id`` (CONTRACTS-V2 §1.3), so the v1
``generic`` panel becomes ``composite``.

``downgrade()`` runs :func:`lkap_contracts.migrate.agent_config_v2_to_v1` and
restores the ``ui_panel_id`` mirror, mapping ``composite`` back to ``generic``.

Revision ID: v2_008_agentconfig_v2
Revises: v2_007_telephony
Create Date: 2026-09-20
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

import sqlalchemy as sa
from lkap_contracts.migrate import (
    COMPOSITE_PANEL_ID,
    agent_config_v1_to_v2,
    agent_config_v2_to_v1,
)

from alembic import op

revision: str = "v2_008_agentconfig_v2"
down_revision: str | None = "v2_007_telephony"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The v1 panel id `COMPOSITE_PANEL_ID` replaces, restored on downgrade.
LEGACY_GENERIC_PANEL_ID = "generic"

AGENTS = sa.table(
    "agents",
    sa.column("id", sa.String),
    sa.column("ui_panel_id", sa.String),
    sa.column("config", sa.JSON),
)
VERSIONS = sa.table(
    "agent_config_versions",
    sa.column("id", sa.String),
    sa.column("agent_id", sa.String),
    sa.column("config", sa.JSON),
)


def _as_document(value: Any) -> dict[str, Any]:
    """Return a stored JSON column value as a dict, whatever the driver handed back."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str | bytes):
        parsed: Any = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"stored config is not a JSON object: {type(value).__name__}")


def _rewrite(
    convert: Callable[[dict[str, Any], str], dict[str, Any]],
    panel_id_for: Callable[[dict[str, Any], str], str],
) -> None:
    """Rewrite every agent config and its version history with `convert`.

    Args:
        convert: Takes the stored document and the agent's current
            ``ui_panel_id`` and returns the rewritten document.
        panel_id_for: Takes the rewritten document and the current
            ``ui_panel_id`` and returns the value to store in ``ui_panel_id``.
    """
    connection = op.get_bind()
    agents = connection.execute(sa.select(AGENTS.c.id, AGENTS.c.ui_panel_id, AGENTS.c.config)).fetchall()
    for agent in agents:
        document = convert(_as_document(agent.config), agent.ui_panel_id)
        connection.execute(
            AGENTS.update()
            .where(AGENTS.c.id == agent.id)
            .values(config=document, ui_panel_id=panel_id_for(document, agent.ui_panel_id))
        )
        versions = connection.execute(
            sa.select(VERSIONS.c.id, VERSIONS.c.config).where(VERSIONS.c.agent_id == agent.id)
        ).fetchall()
        for version in versions:
            connection.execute(
                VERSIONS.update()
                .where(VERSIONS.c.id == version.id)
                .values(config=convert(_as_document(version.config), agent.ui_panel_id))
            )


def upgrade() -> None:
    """Rewrite every stored config to v2 and mirror the panel id."""

    def panel_id_for(document: dict[str, Any], current: str) -> str:
        panel = document.get("panel")
        if isinstance(panel, dict):
            panel_id = panel.get("panel_id")
            if isinstance(panel_id, str) and panel_id:
                return panel_id
        return current

    _rewrite(agent_config_v1_to_v2, panel_id_for)


def downgrade() -> None:
    """Rewrite every stored config back to v1 and restore the v1 panel id."""

    def convert(document: dict[str, Any], _ui_panel_id: str) -> dict[str, Any]:
        return agent_config_v2_to_v1(document)

    def panel_id_for(_document: dict[str, Any], current: str) -> str:
        return LEGACY_GENERIC_PANEL_ID if current == COMPOSITE_PANEL_ID else current

    _rewrite(convert, panel_id_for)
