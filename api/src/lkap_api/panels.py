"""Effective panel layout resolution (R-V2-7, CONTRACTS-V2 §4.4 "Layout delivery").

The panel layout is configuration, not session state: the browser needs it
**before** the agent joins (pre-call and connecting states render the panel
skeleton), so it travels on `ConnectResponse.agent.panel`
(`AgentPublicOut.panel`) rather than the seq-ordered `UiState` channel.
`effective_layout` is the single function both `connect` and the worker's
`/internal/v1/sessions/{id}/resolved` call, so the two can never disagree.

`AgentConfig.panel` is not `Optional` — it always holds `PanelLayout()`'s
Pydantic default (`panel_id="composite", layout="side", blocks=[]`) even for
an agent nobody has ever opened the panel composer on, and
`config_service.seed_config_from_manifest` does not write `panel` at all
(ask #65's finding that prompted this ruling). So a config still holding
exactly that default is treated as "never customized" and falls through to
the pack's `default_panel`; only an agent with neither a customized `panel`
nor an installed pack with one gets the hardcoded default.

Block configs (R-V2-17): because the layout is public, every
``config.panel.blocks[i].config`` is checked against its block type's schema
(:mod:`lkap_contracts.blocks`) on save. :func:`block_config_issues` is registered
into ``config_service.VALIDATORS`` when this module is imported (the agents and
internal routers import it), so a table block saved with ``config.foo`` is a 422
at ``panel.blocks[0].config.foo``.
"""

from __future__ import annotations

from lkap_contracts.agent_config import AgentConfig, PanelLayout
from lkap_contracts.blocks import validate_panel_block_configs
from lkap_contracts.common import Issue
from lkap_contracts.packs import PackManifest

from lkap_api.config_service import ValidationContext, register_validator
from lkap_api.db.models import Agent

#: The literal `AgentConfig.panel` field default — the "never customized" marker.
_UNSET_PANEL = PanelLayout()


def effective_layout(agent: Agent, pack: PackManifest | None) -> PanelLayout:
    """Resolve the panel layout a session on `agent` actually renders.

    Args:
        agent: The agent row (its current `config` is parsed here).
        pack: `agent.pack_id`'s manifest, or `None` if that pack is no
            longer installed (e.g. `LKAP_PACKS` changed since the agent was
            created) — falls through past it to the hardcoded default.

    Returns:
        `config.panel` when it was ever customized (differs from the field's
        own Pydantic default); otherwise `pack.default_panel`; otherwise a
        bare `PanelLayout()` (`"composite"`, no blocks).
    """
    config = AgentConfig.model_validate(agent.config)
    if config.panel != _UNSET_PANEL:
        return config.panel
    if pack is not None and pack.default_panel is not None:
        return pack.default_panel
    return PanelLayout()


def block_config_issues(ctx: ValidationContext) -> list[Issue]:
    """Every panel block's ``config`` against its type's schema (R-V2-17), plus flow steps checks.

    The schemas cover the V5-08 quartet (``choices``, ``details``,
    ``markdown``, ``steps``) through :mod:`lkap_contracts.blocks`. A ``steps``
    block that follows the flow (``config.source == "flow"``) also gets two
    warnings when they apply (V5-08): the agent has no flow, so the block stays
    empty; or one of its ``steps`` ids names no agent node of the flow, so
    that step never moves.

    Args:
        ctx: The validation context; ``ctx.config.panel`` and ``ctx.config.flow`` are read.

    Returns:
        One ``error`` per unknown or invalid key, at ``panel.blocks[i].config.<key>``;
        the flow warnings at ``panel.blocks[i].config.source`` / ``.config.steps[j].id``.
    """
    issues = validate_panel_block_configs(ctx.config.panel.blocks)
    flow = ctx.config.flow
    node_ids = {node.id for node in flow.nodes if node.kind == "agent"} if flow is not None else set()
    for index, block in enumerate(ctx.config.panel.blocks):
        if block.type != "steps" or block.config.get("source") != "flow":
            continue
        base = f"panel.blocks[{index}].config"
        if flow is None:
            issues.append(
                Issue(
                    path=f"{base}.source",
                    message="this block follows the flow, but the agent has no flow, so it stays empty",
                    severity="warning",
                )
            )
            continue
        steps = block.config.get("steps")
        for j, step in enumerate(steps if isinstance(steps, list) else []):
            step_id = step.get("id") if isinstance(step, dict) else None
            if isinstance(step_id, str) and step_id and step_id not in node_ids:
                issues.append(
                    Issue(
                        path=f"{base}.steps[{j}].id",
                        message=f"'{step_id}' is not a step of the flow, so it never moves",
                        severity="warning",
                    )
                )
    return issues


register_validator(block_config_issues)
