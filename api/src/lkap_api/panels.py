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

V5-43: the ``link``, ``slots`` and ``cards`` configs are checked the same way (a
``link`` block with no ``allowed_hosts``, or a site that is not a plain host
name, is an error at ``panel.blocks[i].config.allowed_hosts``), and a ``link``
block on an agent set up for phone calls without text messages gets a tip
(:data:`LINK_ON_PHONE_MESSAGE`): phone callers cannot see it, so the agent can
only text the link when ``tools.sms`` is set.

V6-06: ``caller_can_edit`` is accepted by the ``details`` and ``checklist`` configs
only (anywhere else it is an unknown key, an error). Two tips: an editable block on an
agent set up for phone calls (:data:`CALLER_EDIT_ON_PHONE_MESSAGE`: phone callers see no
screen), and a picture model (``pipeline.image_gen``) on a composite panel with blocks
but no ``gallery`` (:data:`PICTURES_NEED_A_GALLERY_MESSAGE`: ``generate_image`` needs one).

V6-08: the ``notebook`` and ``layout`` configs are checked the same way; a notebook's
``caller_can_write`` gets the phone tip like ``caller_can_edit``; and every ``layout``
block's children are checked across the panel (:func:`lkap_contracts.blocks.layout_issues`:
a child that does not exist, the layout itself, another layout, or a block already claimed
by a layout is an error at ``panel.blocks[i].config.children[j].block_id``).

V6-12: the ``canvas`` config is checked the same way; a notebook ``ink`` section's
``canvas_block_id`` must name a canvas of the panel shown nowhere else
(:func:`lkap_contracts.blocks.canvas_claim_issues`, an error at
``panel.blocks[i].config.sections[j].canvas_block_id``); and a board the caller may draw on,
on an agent set up for phone calls, gets the phone tip (:data:`DRAWING_ON_PHONE_MESSAGE`).

V6-23: the ``signature``, ``chart``, ``timer``, ``code`` and ``cart`` configs are checked the
same way (strict, every bound from :mod:`lkap_contracts.blocks`: a chart kind that does not
exist, a timer longer than 4 hours, a code block over 20,000 characters, a currency that is not
a three-letter code, a signature wording over 2,000 characters), and a ``signature`` block on an
agent set up for phone calls gets its own tip (:data:`SIGNATURE_ON_PHONE_MESSAGE`: nothing can
be signed on a phone call).
"""

from __future__ import annotations

from typing import Final

from lkap_contracts.agent_config import AgentConfig, PanelLayout
from lkap_contracts.blocks import (
    canvas_caller_can_draw,
    canvas_claim_issues,
    layout_issues,
    validate_panel_block_configs,
)
from lkap_contracts.common import Issue
from lkap_contracts.packs import PackManifest
from lkap_contracts.ui_protocol import CALLER_EDIT_FLAGS, EDITABLE_BLOCK_TYPES

from lkap_api.config_service import ValidationContext, register_validator
from lkap_api.db.models import Agent

#: A `link` block on an agent set up for phone calls without text messages (V5-43, a tip).
LINK_ON_PHONE_MESSAGE: Final[str] = (
    "Tip: callers on a phone line cannot see links; set up text messages so the agent can text the "
    "link to them instead"
)

#: A block the caller may edit on an agent set up for phone calls (V6-06, a tip).
CALLER_EDIT_ON_PHONE_MESSAGE: Final[str] = (
    "Tip: callers on a phone line cannot see the panel, so only callers on the web page can change this block"
)
#: A drawing board the caller may draw on, on an agent set up for phone calls (V6-12, a tip).
DRAWING_ON_PHONE_MESSAGE: Final[str] = (
    "Tip: callers on a phone line cannot see the drawing board, so only callers on the web page can "
    "draw on it"
)
#: A signature block on an agent set up for phone calls (V6-23, a tip).
SIGNATURE_ON_PHONE_MESSAGE: Final[str] = (
    "Tip: callers on a phone line cannot see the panel, so they cannot sign; only callers on the web "
    "page can sign here"
)
#: A picture model on a panel with no gallery block (V6-06, a tip).
PICTURES_NEED_A_GALLERY_MESSAGE: Final[str] = (
    "Tip: a picture model is set, but the panel has no gallery block, so the agent cannot show "
    "pictures; add a gallery block"
)

#: The built-in panel that renders `PanelLayout.blocks`.
COMPOSITE_PANEL_ID: Final[str] = "composite"

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
    # V6-08 (D-V6-18): what each layout block claims, across the panel.
    issues += layout_issues(ctx.config.panel.blocks)
    # V6-12 (ask #57): which board each notebook ink section shows.
    issues += canvas_claim_issues(ctx.config.panel.blocks)
    config = ctx.config
    on_phone = config.capabilities.dtmf or bool(config.telephony.transfer_targets)
    if on_phone and config.tools.sms is None:
        issues += [
            Issue(path=f"panel.blocks[{index}]", message=LINK_ON_PHONE_MESSAGE, severity="warning")
            for index, block in enumerate(config.panel.blocks)
            if block.type == "link"
        ]
    if on_phone:
        # V6-08: each editable type names its own flag (`caller_can_write` on a notebook).
        issues += [
            Issue(
                path=f"panel.blocks[{index}].config.{CALLER_EDIT_FLAGS[block.type]}",
                message=CALLER_EDIT_ON_PHONE_MESSAGE,
                severity="warning",
            )
            for index, block in enumerate(config.panel.blocks)
            if block.type in EDITABLE_BLOCK_TYPES and block.config.get(CALLER_EDIT_FLAGS[block.type]) is True
        ]
        issues += [
            Issue(
                path=f"panel.blocks[{index}].config.caller_can_draw",
                message=DRAWING_ON_PHONE_MESSAGE,
                severity="warning",
            )
            for index, block in enumerate(config.panel.blocks)
            if block.type == "canvas" and canvas_caller_can_draw(block.id, config.panel.blocks)
        ]
        issues += [
            Issue(path=f"panel.blocks[{index}]", message=SIGNATURE_ON_PHONE_MESSAGE, severity="warning")
            for index, block in enumerate(config.panel.blocks)
            if block.type == "signature"
        ]
    panel = config.panel
    if (
        config.pipeline.image_gen is not None
        and panel.panel_id == COMPOSITE_PANEL_ID
        and panel.blocks
        and not any(block.type == "gallery" for block in panel.blocks)
        and "generate_image" not in config.tools.builtin_disabled
    ):
        issues.append(
            Issue(path="pipeline.image_gen", message=PICTURES_NEED_A_GALLERY_MESSAGE, severity="warning")
        )
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
