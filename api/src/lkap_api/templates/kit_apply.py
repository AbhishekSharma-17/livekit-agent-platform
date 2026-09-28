"""Adding a tool kit to an agent (V6-18, D-V6-26): plan it, preview it, apply it.

``POST /v1/tool-kits/{id}/instantiate`` runs :func:`instantiate_kit`:

1. **Plan.** Pick the variant, check the admin's settings (addresses pass the network
   guard), the lookup table (a ``dataset`` variant: the workspace's, not failed, enough key
   columns) or the connected app (a ``composio_action`` variant: ``admin`` +
   ``providers:write`` first, as picking app actions needs, S5-40), render the kit
   (:func:`lkap_api.templates.kits.render_kit`) and turn its tools into stored definitions:
   an HTTP tool gets ``allowed_hosts`` from its address and, without ``credential_id``, loses
   the headers that carry ``{{ secret.* }}`` (keys just unset); a template tool is
   instantiated with the kit's settings; a lookup gets the table and its key columns.
2. **Match.** Every item is matched before anything is added, so a second call with the
   same prefix adds nothing: a tool attached to (or owned by) the agent by name, a block by
   id (a ``shared`` block by type), the snippet by its markers, an extraction field, a rule, a
   flow node, edge or variable, the test case by id.
3. **Preview** (``dry_run``): the agent's configuration as it would be, validated with the
   planned tools standing in for rows; nothing is written.
4. **Apply**: the tools are created through ``POST /v1/tools``'s own checks (binding a key
   needs ``admin`` + ``providers:write``; a lookup's table must be the workspace's), app
   actions through the Apps pick (vendor schema, destructive refusal), then one new
   configuration version. A kit that would add a validation **error** the agent did not
   have is refused and nothing is kept (the request rolls back). One audit row with ids and
   counts only.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal
from urllib.parse import urlsplit

from lkap_contracts.agent_config import AgentConfig, NotifyTeamConfig
from lkap_contracts.agent_tests import AgentTest
from lkap_contracts.api_models import ToolCreate, ValidationResult
from lkap_contracts.extraction import ExtractionField
from lkap_contracts.flow import AgentNode, FlowSpec
from lkap_contracts.kits import (
    KIT_FLOW_ANCHOR,
    KitApp,
    KitChange,
    KitChangeStatus,
    KitFlowFragment,
    KitToolPlan,
    KitVariant,
    ToolKit,
    ToolKitInstantiate,
    ToolKitInstantiated,
    kit_markers,
)
from lkap_contracts.tool_providers import AppActionsPickIn
from lkap_contracts.tools import (
    DatasetToolDefinition,
    HttpToolDefinition,
    ToolDefinition,
)
from lkap_contracts.ui_protocol import BlockSpec
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import net_guard
from lkap_api.auth import audit
from lkap_api.auth.deps import WorkspaceContext
from lkap_api.auth.roles import Requirement
from lkap_api.config_service import validate, validation_context_for
from lkap_api.db.models import Agent, AgentConfigVersion, Credential, Dataset, Tool, utcnow
from lkap_api.errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.settings import Settings
from lkap_api.templates.kits import KitError, KitTokens, get_kit, raw_kit, render_kit, resolve_settings
from lkap_api.templates.tools import ToolTemplateError, instantiate_definition, tool_template
from lkap_api.tool_providers.adapter import AdapterFactory
from lkap_api.vault import Vault

__all__ = ["PLANNED_ID_PREFIX", "instantiate_kit"]

log = get_logger(__name__)

#: A composio variant picks app actions: the Apps admin gate (S5-40, as the pick route).
_APPS_ADMIN: Final = Requirement("admin", "providers:write")
#: Stand-in ids of tools a dry run would create (validation needs an id per attached tool).
PLANNED_ID_PREFIX: Final[str] = "kit-planned-"
#: The credential kind a key for kit tools (and the team webhook) must be.
_TOOL_SECRET_PROVIDER: Final[str] = "http-tool-secret"

ToolKind = Literal["http", "mcp", "provider", "dataset"]
_SECRET_RE: Final = re.compile(r"{{\s*secret\.([A-Za-z_][A-Za-z0-9_]*)\s*}}")
_DEFINITION_ADAPTER: TypeAdapter[ToolDefinition] = TypeAdapter(ToolDefinition)


def _refuse(message: str, **details: Any) -> UnprocessableEntityError:
    return UnprocessableEntityError(message, details=details or None)


@dataclass
class _PlannedTool:
    """One kit tool: what is (or would be) stored, and whether it exists already."""

    key: str
    label: str
    name: str
    kind: ToolKind
    status: KitChangeStatus
    tool_id: str | None = None
    definition: ToolDefinition | None = None
    fake: Any = None
    slug: str | None = None
    """App actions: the action slug."""


@dataclass
class _Plan:
    kit: ToolKit
    variant: KitVariant
    prefix: str
    tokens: KitTokens
    settings: dict[str, str]
    tools: list[_PlannedTool]
    app: KitApp | None = None
    connection_id: str | None = None
    notes: list[str] = field(default_factory=list)
    changes: list[KitChange] = field(default_factory=list)


# ------------------------------------------------------------------ the agent and its tools
async def _agent_tools(
    db: AsyncSession, workspace_id: str, agent: Agent, config: AgentConfig
) -> dict[str, Tool]:
    """The tools the agent has (attached, or owned by it), by name."""
    rows = (
        await db.execute(
            select(Tool).where(
                Tool.workspace_id == workspace_id,
                or_(Tool.id.in_(config.tools.tool_ids or [""]), Tool.agent_id == agent.id),
            )
        )
    ).scalars()
    by_name: dict[str, Tool] = {}
    for row in rows.all():
        by_name.setdefault(row.name, row)
    return by_name


def _http_definition(
    definition: HttpToolDefinition, credential_id: str | None, policy: net_guard.NetPolicy
) -> tuple[HttpToolDefinition, bool]:
    """An HTTP kit tool as stored: its host allowed, its key bound or its key headers dropped.

    Returns:
        The definition and whether key headers were dropped.

    Raises:
        UnprocessableEntityError: The address fails the network guard.
    """
    problem = net_guard.check_url(definition.url, policy)
    if problem is not None:
        raise _refuse(problem, field="settings", reason="blocked_destination")
    host = (urlsplit(definition.url).hostname or "").lower()
    update: dict[str, Any] = {"allowed_hosts": definition.allowed_hosts or [host]}
    dropped = False
    if credential_id is not None:
        update["credential_id"] = credential_id
    else:
        headers = {name: value for name, value in definition.headers.items() if not _SECRET_RE.search(value)}
        dropped = headers != definition.headers
        update["headers"] = headers
        update["credential_id"] = None
    data = {**definition.model_dump(mode="json"), **update}
    return HttpToolDefinition.model_validate(data), dropped


# ------------------------------------------------------------------ planning
async def _dataset_inputs(
    db: AsyncSession, workspace_id: str, variant: KitVariant, payload: ToolKitInstantiate
) -> tuple[str, list[str]]:
    """The lookup table and key columns of a ``dataset`` variant."""
    from lkap_api.datasets.service import load_dataset  # noqa: PLC0415 - keeps the import light

    if payload.dataset_id is None:
        raise _refuse(
            "this way of adding the kit reads a lookup table: pick one (dataset_id)",
            field="dataset_id",
            reason="dataset_required",
        )
    try:
        dataset: Dataset = await load_dataset(db, workspace_id, payload.dataset_id)
    except NotFoundError:
        raise _refuse(
            f"unknown lookup table '{payload.dataset_id}'", field="dataset_id", reason="unknown_dataset"
        ) from None
    if dataset.status == "failed":
        raise _refuse(
            f"the lookup table '{dataset.name}' failed to import; upload it again",
            field="dataset_id",
            reason="dataset_failed",
        )
    keys = [str(column.get("name")) for column in dataset.key_columns or [] if isinstance(column, Mapping)]
    chosen = list(dict.fromkeys(payload.key_columns)) if payload.key_columns else keys
    outside = [column for column in chosen if column not in keys]
    if outside:
        raise _refuse(
            f"'{outside[0]}' is not a key column of '{dataset.name}'; its key columns: {', '.join(keys)}",
            field="key_columns",
            reason="not_a_key_column",
        )
    needed = variant.requires.min_key_columns
    if len(chosen) < needed:
        raise _refuse(
            f"this kit checks {needed} answers together, so the lookup table needs at least {needed} key "
            f"columns ('{dataset.name}' has {len(chosen)})",
            field="key_columns",
            reason="too_few_key_columns",
        )
    return dataset.id, chosen


async def _app_inputs(
    db: AsyncSession, vault: Vault, ctx: WorkspaceContext, variant: KitVariant, payload: ToolKitInstantiate
) -> tuple[KitApp, str]:
    """The kit app matching the request's connected app (after the Apps admin gate)."""
    from lkap_api.tool_providers.service import load_connection  # noqa: PLC0415

    if not ctx.allows(_APPS_ADMIN):
        raise ForbiddenError(
            "adding a connected app's actions needs the 'admin' role (and the 'providers:write' scope "
            "for API keys), as picking actions in the Apps settings does",
            details={"required_role": _APPS_ADMIN.role, "required_scope": _APPS_ADMIN.scope},
        )
    apps = ", ".join(app.label for app in variant.apps)
    if payload.connection_id is None:
        raise _refuse(
            f"this way of adding the kit uses a connected app: connect {apps} under Apps first, then pick "
            "the connection (connection_id)",
            field="connection_id",
            reason="app_required",
        )
    try:
        conn = await load_connection(db, vault, ctx.workspace_id, payload.connection_id)
    except NotFoundError:
        raise _refuse(
            f"unknown app connection '{payload.connection_id}'", field="connection_id", reason="unknown_app"
        ) from None
    app = next((entry for entry in variant.apps if entry.toolkit == conn.toolkit), None)
    if app is None:
        raise _refuse(
            f"this kit works with {apps}; the connection is to '{conn.toolkit}'",
            field="connection_id",
            reason="app_not_supported",
        )
    if payload.actions is not None:
        if len(payload.actions) != len(app.actions):
            raise _refuse(
                f"give one action for each of the kit's {len(app.actions)}: "
                + ", ".join(action.label for action in app.actions),
                field="actions",
                reason="actions_count",
            )
        app = app.model_copy(
            update={
                "actions": [
                    action.model_copy(update={"slug": slug.strip().upper()})
                    for action, slug in zip(app.actions, payload.actions, strict=True)
                ]
            }
        )
    return app, conn.id


async def _check_credential(db: AsyncSession, workspace_id: str, credential_id: str) -> Credential:
    row = await db.scalar(
        select(Credential).where(Credential.id == credential_id, Credential.workspace_id == workspace_id)
    )
    if row is None:
        raise _refuse(
            f"unknown credential '{credential_id}'", field="credential_id", reason="unknown_credential"
        )
    if row.provider_id != _TOOL_SECRET_PROVIDER:
        raise _refuse(
            f"a kit takes a '{_TOOL_SECRET_PROVIDER}' key, not a '{row.provider_id}' key",
            field="credential_id",
            reason="wrong_credential_kind",
        )
    return row


async def _plan(
    db: AsyncSession,
    vault: Vault,
    app_settings: Settings,
    ctx: WorkspaceContext,
    kit_id: str,
    payload: ToolKitInstantiate,
    agent: Agent,
    config: AgentConfig,
) -> _Plan:
    raw = raw_kit(kit_id)
    if raw is None:
        raise NotFoundError(f"unknown tool kit '{kit_id}'")
    variants = [str(v.get("id")) for v in raw.get("variants") or []]
    variant_id = payload.variant or str(raw.get("default_variant"))
    if variant_id not in variants:
        raise _refuse(
            f"the kit '{kit_id}' has no variant '{variant_id}'; its variants: {', '.join(variants)}",
            field="variant",
            reason="unknown_variant",
            known=variants,
        )
    prefix = payload.block_prefix or str(raw.get("default_prefix"))
    preview = get_kit(kit_id)
    assert preview is not None  # noqa: S101 - every raw kit has a preview (load_kits)
    variant = preview.variant(variant_id)
    assert variant is not None  # noqa: S101 - checked above
    source = variant.source
    try:
        kit_settings = resolve_settings(preview, variant, payload.settings)
    except KitError as exc:
        raise _refuse(str(exc), field="settings", reason="invalid_setting") from exc
    app: KitApp | None = None
    connection_id: str | None = None
    dataset_id, key_columns = "", [""]
    if source == "composio_action":
        app, connection_id = await _app_inputs(db, vault, ctx, variant, payload)
    elif source == "dataset":
        dataset_id, key_columns = await _dataset_inputs(db, ctx.workspace_id, variant, payload)
    if payload.credential_id is not None:
        await _check_credential(db, ctx.workspace_id, payload.credential_id)
    tokens = KitTokens(prefix=prefix, settings=kit_settings, dataset_id=dataset_id, key_column=key_columns[0])
    names: dict[str, str] | None = None
    actions: list[_PlannedTool] = []
    if app is not None and connection_id is not None:
        # App actions are named by the app (and reused per connection): name the rules after them.
        actions = await _planned_actions(db, ctx.workspace_id, app, connection_id)
        names = {action.key: action.name for action in actions}
    try:
        kit = render_kit(
            raw, tokens, variant_id=variant_id, names=names, app=app.toolkit if app is not None else None
        )
    except KitError as exc:
        raise _refuse(str(exc), reason="kit_render") from exc
    variant = kit.variants[0]
    if app is not None:
        variant = variant.model_copy(update={"apps": [app]})
    plan = _Plan(
        kit=kit,
        variant=variant,
        prefix=prefix,
        tokens=tokens,
        settings=kit_settings,
        tools=[],
        app=app,
        connection_id=connection_id,
    )
    existing = await _agent_tools(db, ctx.workspace_id, agent, config)
    policy = net_guard.policy_from_settings(app_settings)
    dropped: list[str] = []
    for kit_tool in variant.tools:
        if kit_tool.only_with == "sms" and config.tools.sms is None:
            plan.changes.append(
                KitChange(
                    kind="tool",
                    id=kit_tool.key,
                    label=kit_tool.label,
                    status="skipped",
                    note="needs text messages: set up sending texts for this agent, then add the kit again",
                )
            )
            continue
        definition = _tool_definition(
            kit_tool.definition, kit_tool.template, kit_settings, payload.credential_id
        )
        if isinstance(definition, HttpToolDefinition):
            definition, lost = _http_definition(definition, payload.credential_id, policy)
            if lost and definition.name not in existing:
                dropped.append(definition.name)
        elif isinstance(definition, DatasetToolDefinition):
            try:
                definition = DatasetToolDefinition.model_validate(
                    {
                        **definition.model_dump(mode="json"),
                        "dataset_id": dataset_id,
                        "key_columns": key_columns,
                    }
                )
            except ValidationError as exc:
                raise _refuse(f"the lookup tool does not fit this table: {exc.errors()[0]['msg']}") from exc
        row = existing.get(definition.name)
        plan.tools.append(
            _PlannedTool(
                key=kit_tool.key,
                label=kit_tool.label,
                name=definition.name,
                kind=definition.kind,
                status="exists" if row is not None else "added",
                tool_id=row.id if row is not None else None,
                definition=_DEFINITION_ADAPTER.validate_python(row.definition)
                if row is not None
                else definition,
                fake=kit_tool.fake,
            )
        )
    plan.tools.extend(actions)
    if dropped:
        listed = ", ".join(sorted(set(dropped)))
        wanted = ", ".join(variant.requires.secret_names) or "its key"
        plan.notes.append(
            f"Added without a key: {listed} call with no key header. To authenticate, remove them and add "
            f"the kit again with a tool-secret key holding {wanted} (credential_id), or give each tool that "
            "key and its header on the Tools tab."
        )
    return plan


def _tool_definition(
    definition: ToolDefinition | None,
    template_id: str | None,
    kit_settings: Mapping[str, str],
    credential_id: str | None,
) -> ToolDefinition:
    if definition is not None:
        return definition
    template = tool_template(str(template_id))
    if template is None:  # the catalogue checks make this unreachable
        raise _refuse(f"unknown tool template '{template_id}'")
    defaults: dict[str, str | int | float | bool] = {
        spec.name: kit_settings[spec.name] for spec in template.defaults if spec.name in kit_settings
    }
    try:
        made = instantiate_definition(template, defaults, credential_id=credential_id or "")
    except ToolTemplateError as exc:
        raise _refuse(str(exc), field="settings", reason="invalid_setting") from exc
    return made.model_copy(update={"credential_id": credential_id})


async def _planned_actions(
    db: AsyncSession, workspace_id: str, app: KitApp, connection_id: str
) -> list[_PlannedTool]:
    """The app actions of a composio variant, matched with the connection's existing tools."""
    from lkap_api.tool_providers.materialise import provider_tools_of, tool_name_for  # noqa: PLC0415

    existing = {
        str((row.definition or {}).get("tool_slug", "")).upper(): row
        for row in await provider_tools_of(db, workspace_id, connection_id)
    }
    planned: list[_PlannedTool] = []
    for action in app.actions:
        row = existing.get(action.slug.upper())
        planned.append(
            _PlannedTool(
                key=action.key,
                label=action.label,
                name=row.name if row is not None else tool_name_for(app.toolkit, action.slug),
                kind="provider",
                status="exists" if row is not None else "added",
                tool_id=row.id if row is not None else None,
                definition=_DEFINITION_ADAPTER.validate_python(row.definition) if row is not None else None,
                fake=action.fake,
                slug=action.slug.upper(),
            )
        )
    return planned


# ------------------------------------------------------------------ the configuration
def _change(
    plan: _Plan, kind: Any, item_id: str, label: str, status: KitChangeStatus, note: str | None = None
) -> None:
    plan.changes.append(KitChange(kind=kind, id=item_id, label=label, status=status, note=note))


def _add_blocks(plan: _Plan, config: AgentConfig) -> None:
    blocks = [*plan.kit.blocks, *plan.variant.blocks]
    if not blocks:
        return
    panel = config.panel
    if panel.panel_id != "composite":
        raise _refuse(
            "this agent shows its own panel, so the kit's blocks have nowhere to go; switch the panel to "
            "blocks first",
            field="agent_id",
            reason="not_a_composite_panel",
        )
    current = list(panel.blocks)
    order = max((block.order for block in current), default=-1)
    for block in blocks:
        same_type = next((b for b in current if str(b.type) == str(block.type)), None)
        same_id = next((b for b in current if b.id == block.id), None)
        label = block.title or str(block.type)
        if block.shared and same_type is not None:
            _change(plan, "block", same_type.id, label, "exists", "the panel already has one")
            continue
        if same_id is not None:
            if str(same_id.type) != str(block.type):
                raise _refuse(
                    f"the panel already has a {same_id.type} block named '{block.id}'; add the kit with "
                    "another block_prefix",
                    field="block_prefix",
                    reason="block_id_taken",
                )
            _change(plan, "block", block.id, label, "exists")
            continue
        order += 1
        current.append(
            BlockSpec(id=block.id, type=block.type, title=block.title, config=dict(block.config), order=order)
        )
        _change(plan, "block", block.id, label, "added")
    config.panel = panel.model_copy(update={"blocks": current})


def _add_snippet(plan: _Plan, config: AgentConfig) -> str:
    opening, closing = kit_markers(plan.kit.id, plan.prefix)
    snippet = plan.variant.instructions_snippet or plan.kit.instructions_snippet
    section = f"{opening}\n{snippet}\n{closing}"
    if opening in config.instructions:
        _change(plan, "instructions", opening, "Instructions", "exists")
        return section
    base = config.instructions.rstrip()
    config.instructions = f"{base}\n\n{section}" if base else section
    _change(plan, "instructions", opening, "Instructions", "added")
    return section


def _add_extraction(plan: _Plan, config: AgentConfig) -> None:
    fields: list[ExtractionField] = [*plan.kit.variables, *plan.variant.variables]
    extraction = config.extraction
    current = list(extraction.fields)
    names = {item.name for item in current}
    for item in fields:
        if item.name in names:
            _change(plan, "variable", item.name, item.label, "exists")
            continue
        current.append(item)
        names.add(item.name)
        _change(plan, "variable", item.name, item.label, "added")
    update: dict[str, Any] = {"fields": current}
    wanted = plan.kit.extraction
    if wanted is not None:
        if wanted.enabled and not extraction.enabled:
            update["enabled"] = True
            _change(plan, "extraction", "enabled", "Live extraction", "added", "turned on")
        elif wanted.enabled:
            _change(plan, "extraction", "enabled", "Live extraction", "exists")
        if wanted.still_needed is not None:
            if extraction.still_needed is None:
                update["still_needed"] = wanted.still_needed
                _change(plan, "extraction", "still_needed", "Still-needed list", "added")
            else:
                _change(plan, "extraction", "still_needed", "Still-needed list", "exists")
    config.extraction = extraction.model_copy(update=update)


def _add_rules(plan: _Plan, config: AgentConfig) -> None:
    current = list(config.rules)
    ids = {rule.id for rule in current}
    for rule in [*plan.kit.rules, *plan.variant.rules]:
        if rule.id in ids:
            _change(plan, "rule", rule.id, rule.label, "exists")
            continue
        current.append(rule)
        ids.add(rule.id)
        _change(plan, "rule", rule.id, rule.label, "added")
    config.rules = current


def _add_flow(plan: _Plan, config: AgentConfig, anchor: str | None) -> None:
    fragment: KitFlowFragment | None = plan.variant.flow_nodes or plan.kit.flow_nodes
    if fragment is None:
        if anchor is not None:
            plan.notes.append("This kit has no flow steps, so flow_anchor was not used.")
        return
    flow = config.flow
    if anchor is None or flow is None or not flow.nodes:
        why = (
            "the agent has no flow; the kit works without one"
            if flow is None or not flow.nodes
            else "name the step to add them after (flow_anchor) to add them"
        )
        for node in fragment.nodes:
            _change(plan, "flow_node", node.id, node.label, "skipped", why)
        return
    nodes = list(flow.nodes)
    by_id = {node.id: node for node in nodes}
    anchor_node = by_id.get(anchor)
    if anchor_node is None:
        raise _refuse(f"the flow has no step '{anchor}'", field="flow_anchor", reason="unknown_step")
    if anchor_node.kind not in ("agent", "start"):
        raise _refuse(
            f"the kit's steps go after a conversation step; '{anchor}' is a {anchor_node.kind} step",
            field="flow_anchor",
            reason="not_a_conversation_step",
        )
    for node in fragment.nodes:
        if node.id in by_id:
            _change(plan, "flow_node", node.id, node.label, "exists")
            continue
        nodes.append(node)
        by_id[node.id] = node
        _change(plan, "flow_node", node.id, node.label, "added")
    edges = list(flow.edges)
    edge_ids = {edge.id for edge in edges}
    for edge in fragment.edges:
        if edge.id in edge_ids:
            _change(plan, "flow_edge", edge.id, edge.label or "", "exists")
            continue
        placed = edge.model_copy(
            update={
                "source": anchor if edge.source == KIT_FLOW_ANCHOR else edge.source,
                "target": anchor if edge.target == KIT_FLOW_ANCHOR else edge.target,
            }
        )
        edges.append(placed)
        edge_ids.add(edge.id)
        _change(plan, "flow_edge", edge.id, edge.label or "", "added")
    variables = list(flow.variables)
    known = {variable.name for variable in variables}
    for variable in fragment.variables:
        status: KitChangeStatus = "exists" if variable.name in known else "added"
        if status == "added":
            variables.append(variable)
            known.add(variable.name)
        _change(plan, "flow_variable", variable.name, variable.description, status)
    if isinstance(anchor_node, AgentNode):
        missing = [name for name in fragment.anchor_extract if name not in anchor_node.extract]
        if missing:
            updated = anchor_node.model_copy(update={"extract": [*anchor_node.extract, *missing]})
            nodes = [updated if node.id == anchor else node for node in nodes]
            _change(
                plan,
                "flow_node",
                anchor,
                anchor_node.label,
                "added",
                f"now also captures {', '.join(missing)}",
            )
    try:
        config.flow = FlowSpec.model_validate(
            {
                "v": flow.v,
                "nodes": [node.model_dump(mode="json") for node in nodes],
                "edges": [edge.model_dump(mode="json") for edge in edges],
                "variables": [variable.model_dump(mode="json") for variable in variables],
            }
        )
    except ValidationError as exc:
        raise _refuse(
            f"the kit's steps do not fit this flow: {exc.errors()[0]['msg']}",
            field="flow_anchor",
            reason="flow",
        ) from exc


def _add_test_case(plan: _Plan, config: AgentConfig, enabled: bool) -> None:
    case = plan.kit.test_case
    if case is None or not enabled:
        return
    case_id = f"kit-{plan.kit.id}-{plan.prefix}"[:64]
    if any(test.id == case_id for test in config.tests):
        _change(plan, "test_case", case_id, case.name, "exists")
        return
    mocks = {
        tool.name: tool.fake
        for tool in plan.tools
        if tool.fake is not None and tool.kind in ("http", "provider", "dataset")
    }
    note = None
    if any(tool.kind == "mcp" for tool in plan.tools):
        note = "the server's tools cannot answer from fakes, so this case calls them for real"
    config.tests = [
        *config.tests,
        AgentTest(
            id=case_id,
            name=case.name,
            persona_instructions=case.persona_instructions,
            scenario=case.scenario,
            expectations=case.expectations,
            mocks=mocks,
        ),
    ]
    _change(plan, "test_case", case_id, case.name, "added", note)


async def _add_notify_team(
    plan: _Plan,
    config: AgentConfig,
    db: AsyncSession,
    vault: Vault,
    workspace_id: str,
    credential_id: str | None,
) -> None:
    if "notify_team" not in plan.variant.configures:
        return
    if config.tools.notify_team is not None:
        _change(plan, "notify_team", "tools.notify_team", "Team notifications", "exists")
        return
    if credential_id is None:
        _change(
            plan,
            "notify_team",
            "tools.notify_team",
            "Team notifications",
            "skipped",
            "no key given: the hand-over still works, the team is not messaged",
        )
        plan.notes.append(
            "To also message your team, add the kit again with a tool-secret key holding the team's webhook "
            "address (credential_id), or set Team notifications on the agent's Tools tab."
        )
        return
    secret_name = plan.settings.get("webhook_secret_name", "TEAM_WEBHOOK_URL")
    row = await _check_credential(db, workspace_id, credential_id)
    if secret_name not in vault.decrypt(row.ciphertext):
        raise _refuse(
            f"the key holds no secret named '{secret_name}' (webhook_secret_name)",
            field="settings.webhook_secret_name",
            reason="unknown_secret_name",
        )
    config.tools.notify_team = NotifyTeamConfig(credential_id=credential_id, secret_name=secret_name)
    _change(plan, "notify_team", "tools.notify_team", "Team notifications", "added")


async def _configure(
    plan: _Plan,
    config: AgentConfig,
    payload: ToolKitInstantiate,
    db: AsyncSession,
    vault: Vault,
    workspace_id: str,
) -> tuple[AgentConfig, str]:
    """``config`` with the kit added (a copy) and the snippet section."""
    new = config.model_copy(deep=True)
    tool_ids = list(new.tools.tool_ids)
    for tool in plan.tools:
        if tool.tool_id is not None and tool.tool_id not in tool_ids:
            tool_ids.append(tool.tool_id)
        _change(plan, "tool", tool.name, tool.label, tool.status)
    new.tools.tool_ids = tool_ids
    if plan.app is not None and new.tools.apps.mode == "off":
        new.tools.apps.mode = "actions"
    _add_blocks(plan, new)
    section = _add_snippet(plan, new)
    _add_extraction(plan, new)
    _add_rules(plan, new)
    _add_flow(plan, new, payload.flow_anchor)
    _add_test_case(plan, new, payload.add_test_case)
    await _add_notify_team(plan, new, db, vault, workspace_id, payload.credential_id)
    try:
        checked = AgentConfig.model_validate(new.model_dump(mode="json"))
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise _refuse(f"the kit does not fit this agent: {where}: {first['msg']}", reason="config") from exc
    return checked, section


# ------------------------------------------------------------------ validation
def _error_keys(result: ValidationResult) -> set[tuple[str, str]]:
    return {(issue.path, issue.message) for issue in result.issues if issue.severity == "error"}


async def _validate(
    db: AsyncSession,
    agent: Agent,
    config: AgentConfig,
    *,
    planned: Sequence[_PlannedTool] = (),
) -> ValidationResult:
    """Validate ``config`` against the workspace, with ``planned`` tools standing in for rows."""
    from lkap_api.flows.validation import pack_tool_names_for  # noqa: PLC0415

    context = await validation_context_for(
        db, config, workspace_id=agent.workspace_id, connection_id=agent.connection_id
    )
    if planned:
        names = dict(context.tool_names_by_id or {})
        definitions: dict[str, Mapping[str, Any]] = dict(context.tool_definitions_by_id or {})
        for tool in planned:
            if tool.tool_id is None:
                continue
            names[tool.tool_id] = tool.name
            if tool.definition is not None:
                definitions[tool.tool_id] = tool.definition.model_dump(mode="json")
            else:
                definitions[tool.tool_id] = {"kind": tool.kind, "name": tool.name}
        context = dataclasses.replace(
            context,
            known_tool_ids=frozenset(names),
            tool_names_by_id=names,
            tool_definitions_by_id=definitions,
        )
    return validate(dataclasses.replace(context, pack_tool_names=pack_tool_names_for(agent.pack_id)))


# ------------------------------------------------------------------ the entry point
async def instantiate_kit(
    db: AsyncSession,
    vault: Vault,
    app_settings: Settings,
    ctx: WorkspaceContext,
    factory: AdapterFactory,
    kit_id: str,
    payload: ToolKitInstantiate,
) -> ToolKitInstantiated:
    """Add (or, with ``dry_run``, preview) a kit on one agent. See the module docstring.

    Raises:
        NotFoundError: No kit has this id, or the agent is not the workspace's.
        ForbiddenError: A connected app's variant without ``admin`` + ``providers:write``, or a
            key bound by a caller below ``admin`` (the ``POST /v1/tools`` rule).
        UnprocessableEntityError: A setting, table, app, prefix or anchor does not fit, a tool
            fails the tool checks, or the kit would add a validation error.
        ConflictError: The agent's stored configuration does not parse.
    """
    from lkap_api.routers.agents import load_scoped_agent  # noqa: PLC0415 - it imports the templates router

    agent = await load_scoped_agent(db, ctx, payload.agent_id)
    try:
        config = AgentConfig.model_validate(agent.config)
    except ValidationError as exc:
        raise ConflictError("the agent's stored configuration does not parse; fix it first") from exc
    plan = await _plan(db, vault, app_settings, ctx, kit_id, payload, agent, config)
    before = await _validate(db, agent, config)

    if payload.dry_run:
        for index, tool in enumerate(plan.tools):
            if tool.tool_id is None:
                tool.tool_id = f"{PLANNED_ID_PREFIX}{index}"
        new_config, section = await _configure(plan, config, payload, db, vault, ctx.workspace_id)
        result = await _validate(db, agent, new_config, planned=plan.tools)
        for tool in plan.tools:
            if tool.tool_id is not None and tool.tool_id.startswith(PLANNED_ID_PREFIX):
                tool.tool_id = None
        return _response(plan, section, agent.config_version, result, dry_run=True)

    await _create_tools(db, vault, app_settings, ctx, factory, plan, agent)
    if plan.app is not None:
        # The app named the actions: render the snippet and rules with the real names.
        names = {tool.key: tool.name for tool in plan.tools if tool.kind == "provider"}
        raw = raw_kit(kit_id)
        assert raw is not None  # noqa: S101 - planned from it above
        kit = render_kit(raw, plan.tokens, variant_id=plan.variant.id, names=names, app=plan.app.toolkit)
        plan.kit = kit
        plan.variant = kit.variants[0].model_copy(update={"apps": [plan.app]})
    new_config, section = await _configure(plan, config, payload, db, vault, ctx.workspace_id)
    result = await _validate(db, agent, new_config)
    added_errors = _error_keys(result) - _error_keys(before)
    if added_errors:
        raise UnprocessableEntityError(
            "adding this kit would make the agent's configuration invalid",
            details={
                "errors": sorted(message for _, message in added_errors),
                "issues": [issue.model_dump() for issue in result.issues if issue.severity == "error"],
            },
        )
    if new_config.model_dump(mode="json") != agent.config:
        agent.config = new_config.model_dump(mode="json")
        agent.config_version += 1
        agent.updated_at = utcnow()
        db.add(
            AgentConfigVersion(
                agent_id=agent.id,
                config_version=agent.config_version,
                config=agent.config,
                created_by=ctx.actor.id,
                note=f"added the {plan.kit.name} kit ({plan.prefix})",
            )
        )
        await db.flush()
    added = [change for change in plan.changes if change.status == "added"]
    if added:
        audit.record(
            db,
            workspace_id=ctx.workspace_id,
            actor_type=ctx.actor.actor_type,
            actor_id=ctx.actor.id,
            action="tool_kit.instantiate",
            target_type="agent",
            target_id=agent.id,
            payload={
                "kit_id": plan.kit.id,
                "variant": plan.variant.id,
                "prefix": plan.prefix,
                "tool_ids": [tool.tool_id for tool in plan.tools if tool.status == "added"],
                "added": _counts(added),
            },
        )
    log.info(
        "tool_kit_instantiated",
        kit_id=plan.kit.id,
        variant=plan.variant.id,
        agent_id=agent.id,
        added=len(added),
        config_version=agent.config_version,
    )
    return _response(plan, section, agent.config_version, result, dry_run=False)


async def _create_tools(
    db: AsyncSession,
    vault: Vault,
    app_settings: Settings,
    ctx: WorkspaceContext,
    factory: AdapterFactory,
    plan: _Plan,
    agent: Agent,
) -> None:
    from lkap_api.routers.tools import create_tool  # noqa: PLC0415 - the tools router imports the vault stack
    from lkap_api.tool_providers.service import pick_actions  # noqa: PLC0415

    for tool in plan.tools:
        if tool.status != "added" or tool.kind == "provider" or tool.definition is None:
            continue
        created = await create_tool(
            ToolCreate(
                agent_id=agent.id,
                kind=tool.definition.kind,
                name=tool.name,
                definition=tool.definition,
                enabled=True,
            ),
            db,
            vault,
            app_settings,
            ctx,
        )
        tool.tool_id = created.id
    wanted = [tool for tool in plan.tools if tool.kind == "provider" and tool.status == "added"]
    if plan.connection_id is None or not wanted:
        return
    await pick_actions(
        db,
        vault,
        factory,
        ctx,
        AppActionsPickIn(connection_id=plan.connection_id, actions=[str(tool.slug) for tool in wanted]),
    )
    from lkap_api.tool_providers.materialise import provider_tools_of  # noqa: PLC0415

    rows = {
        str((row.definition or {}).get("tool_slug", "")).upper(): row
        for row in await provider_tools_of(db, ctx.workspace_id, plan.connection_id)
    }
    for tool in wanted:
        row = rows.get(str(tool.slug))
        if row is not None:
            tool.tool_id = row.id
            tool.name = row.name
            tool.definition = _DEFINITION_ADAPTER.validate_python(row.definition)


def _counts(changes: Sequence[KitChange]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for change in changes:
        counts[change.kind] = counts.get(change.kind, 0) + 1
    return counts


def _response(
    plan: _Plan, section: str, config_version: int, result: ValidationResult, *, dry_run: bool
) -> ToolKitInstantiated:
    tools = [
        KitToolPlan(
            key=tool.key,
            name=tool.name,
            kind=tool.kind,
            status=tool.status,
            tool_id=tool.tool_id,
            definition=tool.definition,
        )
        for tool in plan.tools
    ]
    notes = list(plan.notes)
    if any(tool.kind == "mcp" and tool.status == "added" for tool in plan.tools):
        notes.append(
            "Sign in to the server from the tool's page (Tools → the server → Sign in), test it, then choose "
            "which of its tools the agent may use."
        )
    if plan.app is not None and any(tool.status == "added" for tool in plan.tools):
        notes.append(
            "App actions run on the connected account; review each action's settings on the Tools tab."
        )
    return ToolKitInstantiated(
        kit_id=plan.kit.id,
        variant=plan.variant.id,
        prefix=plan.prefix,
        dry_run=dry_run,
        changes=plan.changes,
        tools=tools,
        tool_ids=[tool.tool_id for tool in plan.tools if tool.tool_id is not None],
        instructions_snippet=section,
        config_version=config_version,
        notes=notes,
        validation=result,
    )
