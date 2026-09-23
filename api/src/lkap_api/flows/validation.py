"""Flow validation (PLAN-V2 V2-16; rulings R-V2-9, R-V2-10, R-V2-11, R-V2-12).

Two entry points:

* :func:`flow_issues` — registered into ``config_service.VALIDATORS`` (never by
  editing ``config_service``'s body), so every save/validate/restore of an agent
  checks its ``config.flow`` against what the worker will actually run:

  - **tool references (R-V2-10)**: every node/global tool name is in the
    agent-level union — the enabled built-ins, the block tools whose block is
    in ``config.panel``, the installed packs' tool names and the ``name`` of
    each tool row in ``config.tools.tool_ids``; node knowledge bases are a
    subset of ``config.knowledge.kb_ids``. The builder's pickers list exactly
    :func:`allowed_tool_names`.
  - **provider overrides (R-V2-9)**: mirrors the worker's
    ``lkap_agent.flow.providers.resolve_override`` — an override is applied
    only when it needs no new secret (the same provider, and the same or no
    credential, as a slot the api already resolves; or a credential-free
    provider). Anything else is an ``error``; any override in a realtime or
    half-cascade pipeline is a ``warning`` (the worker ignores it there).
  - **QA node (R-V2-11)**: a ``warning`` at ``qa.enabled`` when it is false and
    the flow has a ``qa`` node (QA runs anyway).
  - **variables**: ``extract`` names an undeclared variable → ``error`` (the
    worker skips it); ``{{ name }}`` placeholders of undeclared variables →
    ``warning``.

* :func:`draft_flow_issues` — structural checks of an unsaved flow with an
  addressable path per finding (``FlowSpec``'s own validator raises one
  message for the whole graph, which cannot put a dot on a node).

The built-in and block tool names mirror
``agent/src/lkap_agent/tools/builtin/__init__.py`` (``BUILTIN_TOOL_NAMES``,
``BLOCK_TOOL_NAMES`` and the block types each needs); the api cannot import the
worker, so a change there must be repeated here (see ``docs/v2/_asks.md``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Final, Literal

from lkap_contracts.agent_config import AgentConfig, ProviderRef, effective_qa
from lkap_contracts.api_models import Issue
from lkap_contracts.flow import (
    AgentNode,
    FlowEdge,
    FlowNode,
    FlowSpec,
    GlobalNode,
    QaNode,
    VariableSpec,
)
from lkap_contracts.providers import get
from pydantic import TypeAdapter, ValidationError

from lkap_api.config_service import INFERENCE_DEFAULT, ValidationContext, register_validator
from lkap_api.packs import discover_manifests
from lkap_api.settings import get_settings

#: `lkap_agent.tools.builtin.BUILTIN_TOOL_NAMES`, in the worker's order.
BUILTIN_TOOL_NAMES: Final[tuple[str, ...]] = (
    "end_call",
    "search_knowledge",
    "http_request",
    "describe_current_frame",
    "pin_frame",
    "push_note",
    "set_status",
    "escalate_to_human",
    "current_time",
)

#: Built-ins the worker registers only with camera or screen share on.
VISION_TOOL_NAMES: Final[frozenset[str]] = frozenset({"describe_current_frame", "pin_frame"})

#: `BLOCK_TOOL_NAMES` → the panel block types that make the worker register each one.
BLOCK_TOOL_TYPES: Final[dict[str, frozenset[str]]] = {
    "update_block": frozenset({"document", "gallery", "table", "transcript", "video", "kb_citations", "custom"}),
    "show_document": frozenset({"document"}),
    "table_append": frozenset({"table"}),
    "request_form": frozenset({"form"}),
}

#: Already-resolved slots whose secrets a node override may reuse (the worker's `_REUSABLE_SLOTS`).
OverrideSlot = Literal["llm", "tts"]

#: `{{ name }}` placeholders, as `lkap_agent.flow.variables` renders them.
_PLACEHOLDER_RE: Final[re.Pattern[str]] = re.compile(r"\{\{\s*([a-z][a-z0-9_]{0,63})\s*\}\}")

_NODE_ADAPTER: Final[TypeAdapter[FlowNode]] = TypeAdapter(FlowNode)
_EDGE_ADAPTER: Final[TypeAdapter[FlowEdge]] = TypeAdapter(FlowEdge)
_VARIABLE_ADAPTER: Final[TypeAdapter[VariableSpec]] = TypeAdapter(VariableSpec)

#: Node kinds that may never be an edge endpoint (mirrors `lkap_contracts.flow`).
_UNCONNECTABLE_KINDS: Final[frozenset[str]] = frozenset({"global", "qa"})


# --------------------------------------------------------------------------- mode
def derived_mode(config: AgentConfig) -> Literal["prompt", "flow"]:
    """``agents.mode`` for a configuration (R-V2-12): ``flow`` iff ``config.flow`` has nodes."""
    return "flow" if config.flow is not None and config.flow.nodes else "prompt"


# -------------------------------------------------------------------------- tools
def installed_pack_tool_names() -> frozenset[str]:
    """Tool names of every installed pack (``LKAP_PACKS``).

    ``ValidationContext`` does not carry the agent's pack, so this is the union
    over installed packs — a name from another installed pack passes here and
    is dropped with a warning by the worker (asks: V2-16-2).
    """
    names: set[str] = set()
    for manifest in discover_manifests(get_settings().packs_list):
        names.update(manifest.tool_names)
    return frozenset(names)


def allowed_tool_names(
    config: AgentConfig,
    *,
    tool_names_by_id: Mapping[str, str],
    pack_tool_names: Iterable[str] = (),
) -> set[str]:
    """The agent-level tool names a flow node may reference (R-V2-10).

    Mirrors what the worker's ``build_builtin_tools`` registers for this
    config (disabled built-ins, ``http_request_enabled`` and the vision tools'
    capability gate included) plus the block tools whose block is in the
    panel, the pack's tools and the ``name`` of each selected tool row.

    Args:
        config: The agent configuration.
        tool_names_by_id: ``{tool_id: name}`` for the workspace's tool rows.
        pack_tool_names: The pack's tool names.

    Returns:
        The allowed names.
    """
    disabled = set(config.tools.builtin_disabled)
    has_vision = config.capabilities.camera or config.capabilities.screen_share
    names: set[str] = set()
    for name in BUILTIN_TOOL_NAMES:
        if name in disabled:
            continue
        if name == "http_request" and not config.tools.http_request_enabled:
            continue
        if name in VISION_TOOL_NAMES and not has_vision:
            continue
        names.add(name)
    block_types = {block.type for block in config.panel.blocks}
    for name, types in BLOCK_TOOL_TYPES.items():
        if name not in disabled and block_types & types:
            names.add(name)
    names.update(pack_tool_names)
    names.update(tool_names_by_id[i] for i in config.tools.tool_ids if i in tool_names_by_id)
    return names


#: The block a block tool needs, in the author's terms.
_BLOCK_TOOL_NEEDS: Final[dict[str, str]] = {
    "update_block": "a table, document, gallery, sources, transcript, video or pack block",
    "show_document": "a document block",
    "table_append": "a table block",
    "request_form": "a form block",
}


def _tool_issue(config: AgentConfig, name: str) -> str:
    """Why ``name`` is not an allowed tool, in the author's terms."""
    if name in BLOCK_TOOL_TYPES:
        if name in config.tools.builtin_disabled:
            return f"tool '{name}' is switched off for this agent (Panel section)"
        return f"tool '{name}' needs {_BLOCK_TOOL_NEEDS[name]} in the panel"
    if name in BUILTIN_TOOL_NAMES:
        if name in config.tools.builtin_disabled or name == "http_request":
            return f"built-in tool '{name}' is off for this agent (Tools section)"
        return f"tool '{name}' needs camera or screen share (Panel & capabilities)"
    return f"unknown tool '{name}' — a node can use only the agent's own tools (Tools section)"


def _tool_and_kb_issues(ctx: ValidationContext, flow: FlowSpec) -> list[Issue]:
    config = ctx.config
    issues: list[Issue] = []
    if ctx.tool_names_by_id is not None:
        allowed = allowed_tool_names(
            config, tool_names_by_id=ctx.tool_names_by_id, pack_tool_names=installed_pack_tool_names()
        )
        for i, node in enumerate(flow.nodes):
            if not isinstance(node, AgentNode | GlobalNode):
                continue
            for j, name in enumerate(node.tools):
                if name not in allowed:
                    issues.append(Issue(path=f"flow.nodes[{i}].tools[{j}]", message=_tool_issue(config, name)))
    agent_kbs = set(config.knowledge.kb_ids)
    for i, node in enumerate(flow.nodes):
        if not isinstance(node, AgentNode | GlobalNode):
            continue
        for j, kb_id in enumerate(node.kb_ids):
            if kb_id not in agent_kbs:
                issues.append(
                    Issue(
                        path=f"flow.nodes[{i}].kb_ids[{j}]",
                        message=f"knowledge base '{kb_id}' is not one of the agent's knowledge bases "
                        "(Knowledge section)",
                    )
                )
    return issues


# ---------------------------------------------------------------------- overrides
def _override_bases(config: AgentConfig) -> dict[OverrideSlot, list[tuple[str, ProviderRef | None]]]:
    """``(resolved provider id, stored ref)`` for every slot a node override may reuse.

    Mirrors ``config_service.resolve_providers`` (which slots exist, and the
    ``workflow_llm`` / ``qa_llm`` fallbacks) together with the worker's
    ``resolve_override`` (which stored ref its credential check compares with:
    ``pipeline.<slot>``, or ``qa.model`` for ``qa_llm`` — ``None`` skips it).
    """
    pipeline = config.pipeline
    llm_bases: list[tuple[str, ProviderRef | None]] = []
    if pipeline.llm is not None:
        llm_bases.append((pipeline.llm.provider_id, pipeline.llm))
    workflow_ref = pipeline.workflow_llm or (
        pipeline.llm if pipeline.llm is not None else ProviderRef(provider_id=INFERENCE_DEFAULT["llm"])
    )
    llm_bases.append((workflow_ref.provider_id, pipeline.workflow_llm))
    qa = effective_qa(config)
    if qa.enabled:
        llm_bases.append(((qa.model or workflow_ref).provider_id, qa.model))
    tts_bases: list[tuple[str, ProviderRef | None]] = []
    if pipeline.tts is not None:
        tts_bases.append((pipeline.tts.provider_id, pipeline.tts))
    return {"llm": llm_bases, "tts": tts_bases}


def _credential_matches(ref: ProviderRef, base_ref: ProviderRef) -> bool:
    return ref.credential_id is None or ref.credential_id == base_ref.credential_id


def override_issue(config: AgentConfig, slot: OverrideSlot, ref: ProviderRef) -> Issue | None:
    """Why the worker would not apply a cascaded node override, or ``None`` when it would.

    Args:
        config: The agent configuration (cascaded pipeline).
        slot: ``llm`` or ``tts``.
        ref: The node's override.

    Returns:
        An ``error`` issue (path filled in by the caller), or ``None``.
    """
    try:
        spec = get(ref.provider_id)
    except KeyError:
        return Issue(path="", message=f"unknown provider '{ref.provider_id}'")
    if spec.kind != slot:
        return Issue(path="", message=f"'{spec.id}' is a {spec.kind} provider, not {slot}")
    for provider_id, base_ref in _override_bases(config)[slot]:
        if provider_id != ref.provider_id:
            continue
        if base_ref is not None and not _credential_matches(ref, base_ref):
            continue
        return None
    if not spec.requires_credential:
        return None
    return Issue(
        path="",
        message=(
            f"'{spec.id}' needs a key this agent's pipeline does not use; a node can switch to a "
            f"different model on a provider the pipeline already uses (same key), or to a provider "
            f"that needs no key"
        ),
    )


def _override_issues(config: AgentConfig, flow: FlowSpec) -> list[Issue]:
    mode = config.pipeline.mode
    issues: list[Issue] = []
    for i, node in enumerate(flow.nodes):
        if not isinstance(node, AgentNode) or not node.providers:
            continue
        for slot, ref in node.providers.items():
            path = f"flow.nodes[{i}].providers.{slot}"
            if mode != "cascaded":
                issues.append(
                    Issue(
                        path=path,
                        message=f"ignored in {mode.replace('_', '-')} mode: realtime and half-cascade flows "
                        "share one model",
                        severity="warning",
                    )
                )
                continue
            problem = override_issue(config, slot, ref)
            if problem is not None:
                issues.append(Issue(path=path, message=problem.message))
    return issues


# ---------------------------------------------------------------------- variables
def _variable_issues(flow: FlowSpec) -> list[Issue]:
    declared = {v.name for v in flow.variables}
    issues: list[Issue] = []
    for i, node in enumerate(flow.nodes):
        if isinstance(node, AgentNode):
            for j, name in enumerate(node.extract):
                if name not in declared:
                    issues.append(
                        Issue(
                            path=f"flow.nodes[{i}].extract[{j}]",
                            message=f"'{name}' is not a flow variable; add it under Variables",
                        )
                    )
        for field, text in _template_fields(node):
            for name in sorted(set(_PLACEHOLDER_RE.findall(text)) - declared):
                issues.append(
                    Issue(
                        path=f"flow.nodes[{i}].{field}",
                        message=f"'{{{{ {name} }}}}' is not a flow variable, so it always renders empty",
                        severity="warning",
                    )
                )
    for j, edge in enumerate(flow.edges):
        if not edge.condition.strip():
            issues.append(
                Issue(
                    path=f"flow.edges[{j}].condition",
                    message="the condition is empty, so the model cannot tell when to take this path",
                    severity="warning",
                )
            )
        for name in sorted(set(_PLACEHOLDER_RE.findall(edge.transition_speech or "")) - declared):
            issues.append(
                Issue(
                    path=f"flow.edges[{j}].transition_speech",
                    message=f"'{{{{ {name} }}}}' is not a flow variable, so it always renders empty",
                    severity="warning",
                )
            )
    return issues


def _template_fields(node: FlowNode) -> list[tuple[str, str]]:
    """``(field, text)`` for every templated text field of a node."""
    fields: list[tuple[str, str]] = []
    for name in ("instructions", "greeting", "farewell", "announce"):
        value = getattr(node, name, None)
        if isinstance(value, str) and value:
            fields.append((name, value))
    return fields


# ---------------------------------------------------------------------------- qa
def _qa_issues(config: AgentConfig, flow: FlowSpec) -> list[Issue]:
    if config.qa.enabled or not any(isinstance(n, QaNode) for n in flow.nodes):
        return []
    return [
        Issue(
            path="qa.enabled",
            message="QA runs because the flow has a QA node",
            severity="warning",
        )
    ]


# ----------------------------------------------------------------------- registry
def flow_issues(ctx: ValidationContext) -> list[Issue]:
    """Every save-time flow finding for ``ctx.config`` (registered into ``VALIDATORS``).

    Public so a test can register it explicitly instead of relying on import
    order (``register_validator`` dedups by identity).
    """
    flow = ctx.config.flow
    if flow is None or not flow.nodes:
        return []
    return [
        *_tool_and_kb_issues(ctx, flow),
        *_override_issues(ctx.config, flow),
        *_variable_issues(flow),
        *_qa_issues(ctx.config, flow),
    ]


register_validator(flow_issues)


# -------------------------------------------------------------------------- draft
def _loc(errors: list[Any]) -> str:
    """The first pydantic error's location as a ``.a.b`` / ``[i]`` suffix."""
    if not errors:
        return ""
    suffix = ""
    for part in errors[0].get("loc", ()):
        if isinstance(part, int):
            suffix += f"[{part}]"
        elif part not in ("start", "agent", "end", "global", "transfer", "qa"):
            suffix += f".{part}"
    return suffix


def _parse_items(
    raw: Any, adapter: TypeAdapter[Any], prefix: str, issues: list[Issue]
) -> list[tuple[int, Any]]:
    """Parse each list item on its own so one bad item does not hide the others."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        issues.append(Issue(path=prefix, message="must be a list"))
        return []
    parsed: list[tuple[int, Any]] = []
    for index, item in enumerate(raw):
        try:
            parsed.append((index, adapter.validate_python(item)))
        except ValidationError as exc:
            errors = exc.errors(include_input=False)
            message = str(errors[0].get("msg", "invalid")) if errors else "invalid"
            issues.append(Issue(path=f"{prefix}[{index}]{_loc(list(errors))}", message=message))
    return parsed


def draft_flow_issues(raw: Mapping[str, Any]) -> tuple[FlowSpec | None, list[Issue]]:
    """Structural checks of an unsaved flow, each at an addressable path.

    The same rules as ``FlowSpec``'s own validator (CONTRACTS-V2 §4.5) — one
    start node, at most one global node, edges between existing nodes and never
    to or from a global or QA node, no edge out of an end node, every node
    reachable from the start — plus unique edge ids and variable names.

    Args:
        raw: The ``flow`` object as the builder sends it.

    Returns:
        ``(spec, issues)``: ``spec`` is the parsed :class:`FlowSpec` when there
        is no structural error, else ``None``.
    """
    issues: list[Issue] = []
    nodes = _parse_items(raw.get("nodes"), _NODE_ADAPTER, "flow.nodes", issues)
    edges = _parse_items(raw.get("edges"), _EDGE_ADAPTER, "flow.edges", issues)
    variables = _parse_items(raw.get("variables"), _VARIABLE_ADAPTER, "flow.variables", issues)
    if issues:
        return None, issues
    if not nodes and not edges:
        return FlowSpec(variables=[v for _i, v in variables]), []

    index_of: dict[str, int] = {}
    kind_of: dict[str, str] = {}
    for i, node in nodes:
        if node.id in index_of:
            issues.append(Issue(path=f"flow.nodes[{i}].id", message=f"duplicate node id '{node.id}'"))
            continue
        index_of[node.id] = i
        kind_of[node.id] = node.kind

    starts = [i for i, node in nodes if node.kind == "start"]
    if not starts:
        issues.append(Issue(path="flow.nodes", message="a flow needs a start node"))
    for i in starts[1:]:
        issues.append(Issue(path=f"flow.nodes[{i}]", message="a flow has exactly one start node"))
    for i in [i for i, node in nodes if node.kind == "global"][1:]:
        issues.append(Issue(path=f"flow.nodes[{i}]", message="a flow has at most one global node"))

    seen_edges: set[str] = set()
    for j, edge in edges:
        if edge.id in seen_edges:
            issues.append(Issue(path=f"flow.edges[{j}].id", message=f"duplicate edge id '{edge.id}'"))
        seen_edges.add(edge.id)
        for role, node_id in (("source", edge.source), ("target", edge.target)):
            kind = kind_of.get(node_id)
            if kind is None:
                issues.append(Issue(path=f"flow.edges[{j}].{role}", message=f"unknown node '{node_id}'"))
            elif kind in _UNCONNECTABLE_KINDS:
                issues.append(
                    Issue(path=f"flow.edges[{j}].{role}", message=f"a {kind} node cannot be connected")
                )
        if kind_of.get(edge.source) == "end":
            issues.append(Issue(path=f"flow.edges[{j}]", message="an end node has no outgoing paths"))

    seen_vars: set[str] = set()
    for k, variable in variables:
        if variable.name in seen_vars:
            issues.append(Issue(path=f"flow.variables[{k}].name", message=f"duplicate variable '{variable.name}'"))
        seen_vars.add(variable.name)

    if len(starts) == 1:
        start_id = dict(nodes)[starts[0]].id
        reachable = _reachable(start_id, [e for _j, e in edges])
        for i, node in nodes:
            if node.kind not in (*_UNCONNECTABLE_KINDS, "start") and node.id not in reachable:
                issues.append(
                    Issue(path=f"flow.nodes[{i}]", message="not reachable from the start node — connect it")
                )

    if issues:
        return None, issues
    spec = FlowSpec(
        nodes=[n for _i, n in nodes], edges=[e for _j, e in edges], variables=[v for _k, v in variables]
    )
    return spec, []


def _reachable(start_id: str, edges: list[FlowEdge]) -> set[str]:
    outgoing: dict[str, list[str]] = {}
    for edge in edges:
        outgoing.setdefault(edge.source, []).append(edge.target)
    seen = {start_id}
    queue = [start_id]
    while queue:
        for target in outgoing.get(queue.pop(), []):
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return seen
