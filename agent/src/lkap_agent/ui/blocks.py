"""Panel v2 blocks: layout resolution, initial block states and validation.

Pure helpers shared by `UiChannel` (block state), `PlatformAgent` (block init)
and the block-writing built-in tools (CONTRACTS-V2 §4.4, ARCHITECTURE-V2
D-V2-12). Nothing here talks to LiveKit; `open_citation` (V5-08) only drives
the channel it is handed.

Block state lives in `UiState.blocks[<BlockSpec.id>]` as **plain JSON
dicts** (never model instances): `FormBlockState.schema_` carries the alias
`schema`, and `UiSnapshot.model_dump_json()` does not dump by alias, so a
stored model would put `schema_` on the wire.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final

from lkap_contracts.agent_config import PanelLayout
from lkap_contracts.packs import PackManifest
from lkap_contracts.ui_protocol import (
    BlockSpec,
    BlockType,
    ChoicesBlockState,
    DetailsBlockState,
    DocumentBlockState,
    DocumentHighlight,
    FormBlockState,
    GalleryBlockState,
    KbCitation,
    KbCitationsBlockState,
    MarkdownBlockState,
    StepsBlockState,
    TableBlockState,
    TranscriptBlockState,
    VideoBlockState,
)
from pydantic import BaseModel, ValidationError

from lkap_agent.logging import get_logger

if TYPE_CHECKING:
    from lkap_agent.ui.channel import UiChannel

__all__ = [
    "ASSET_DOCUMENT_ID_KEY",
    "BLOCK_STATE_MODELS",
    "COMPOSITE_PANEL_ID",
    "ENVELOPE_BLOCK_TYPES",
    "FULL_PAGE_BBOX",
    "OPEN_CITATION",
    "VOICE_ONLY_CHANNELS",
    "block_ids_of_type",
    "block_path",
    "choice_selection_error",
    "citation_note",
    "describe_blocks",
    "find_citation",
    "flow_steps_specs",
    "flow_steps_state",
    "open_citation",
    "pick_block",
    "session_block_specs",
    "initial_block_state",
    "initial_block_states",
    "jsonable",
    "resolve_block_specs",
    "validate_block_state",
]

logger = get_logger(__name__)

#: The built-in panel that renders `PanelLayout.blocks`.
COMPOSITE_PANEL_ID: Final[str] = "composite"

#: Block types that render envelope fields (`status`, `notes`, ...) and hold `{}`.
#: `custom` is pack-defined and also starts as `{}`.
ENVELOPE_BLOCK_TYPES: Final[frozenset[BlockType]] = frozenset(
    {"status", "notes", "checklist", "activity", "custom"}
)

#: Block type -> the contracts state model that validates it.
BLOCK_STATE_MODELS: Final[dict[BlockType, type[BaseModel]]] = {
    "form": FormBlockState,
    "document": DocumentBlockState,
    "gallery": GalleryBlockState,
    "table": TableBlockState,
    "transcript": TranscriptBlockState,
    "video": VideoBlockState,
    "kb_citations": KbCitationsBlockState,
    "choices": ChoicesBlockState,
    "details": DetailsBlockState,
    "markdown": MarkdownBlockState,
    "steps": StepsBlockState,
}

#: Session channels with no screen: panel blocks are invisible there (V5-08).
VOICE_ONLY_CHANNELS: Final[frozenset[str]] = frozenset({"sip_in", "sip_out"})

#: `block_action` name a `kb_citations` block sends when the user taps a citation (E1).
OPEN_CITATION: Final[str] = "open_citation"

#: `AssetRef.meta` key naming the knowledge-base document an asset holds (V5-19 pushes them).
ASSET_DOCUMENT_ID_KEY: Final[str] = "document_id"

#: Page-relative (0..1) rectangle covering a whole page (as `show_document` uses).
FULL_PAGE_BBOX: Final[tuple[float, float, float, float]] = (0.0, 0.0, 1.0, 1.0)


def jsonable(value: Any) -> Any:
    """Turn `value` into plain JSON data (models dumped by alias, recursively)."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    return value


def block_path(block_id: str, relative: str = "") -> str:
    """The absolute `UiState` path of `relative` inside block `block_id`.

    Args:
        block_id: The block's `BlockSpec.id`.
        relative: A path relative to the block; `""` or `"/"` is the block itself.

    Returns:
        `"/blocks/<block_id>"` or `"/blocks/<block_id>/<relative...>"`.
    """
    if "/" in block_id or not block_id:
        raise ValueError(f"invalid block id: {block_id!r}")
    rest = relative.strip("/")
    return f"/blocks/{block_id}" + (f"/{rest}" if rest else "")


def resolve_block_specs(panel: PanelLayout, manifest: PackManifest | None = None) -> list[BlockSpec]:
    """The blocks this session's panel shows, in render order.

    Rules (PLAN-V2 V2-10): `AgentConfig.panel.blocks` when non-empty; otherwise
    the pack's `default_panel.blocks` when that default targets the same panel
    id; otherwise, for a custom (non-composite) panel, the pack's `blocks`
    (the blocks a custom panel expects). A custom panel with neither (the
    insurance notebook) gets no blocks.

    Args:
        panel: `AgentConfig.panel` (the resolved config's panel layout).
        manifest: The session's pack manifest, if any.

    Returns:
        The block specs sorted by `order` (stable for equal orders), with
        duplicate ids dropped after the first occurrence.
    """
    specs: list[BlockSpec] = list(panel.blocks)
    if not specs and manifest is not None:
        default = manifest.default_panel
        if default is not None and default.panel_id == panel.panel_id:
            specs = list(default.blocks)
        if not specs and panel.panel_id != COMPOSITE_PANEL_ID:
            specs = list(manifest.blocks)
    seen: set[str] = set()
    unique: list[BlockSpec] = []
    for spec in sorted(specs, key=lambda s: s.order):
        if spec.id in seen or "/" in spec.id or not spec.id:
            logger.warning("skipping a duplicate or invalid block id", block_id=spec.id)
            continue
        seen.add(spec.id)
        unique.append(spec)
    return unique


def initial_block_state(spec: BlockSpec) -> dict[str, Any]:
    """The empty state of one block.

    Envelope-backed types (`status`, `notes`, `checklist`, `activity`) and
    `custom` start as `{}`. Every other type starts from its contracts state
    model's defaults; `BlockSpec.config` keys that name a field of that model
    seed it (e.g. `{"columns": [...]}` on a `table`, `{"source":
    "user_camera"}` on a `video`). A config that does not validate is ignored
    with a warning rather than failing the session.
    """
    model = BLOCK_STATE_MODELS.get(spec.type)
    if model is None:
        return {}
    seed = {k: v for k, v in spec.config.items() if _is_field(model, k)}
    if spec.type == "details":
        # `details.fields` (the starting rows) seed `items` with no value yet (V5-08).
        fields = spec.config.get("fields")
        if isinstance(fields, list):
            seed["items"] = [
                {"key": f.get("key"), "label": f.get("label", ""), "type": f.get("type", "string")}
                for f in fields
                if isinstance(f, dict)
            ]
    try:
        return _dump(model.model_validate(seed))
    except ValidationError as exc:
        logger.warning("block config does not seed a valid state", block_id=spec.id, error=str(exc))
        return _dump(model())


def initial_block_states(specs: Iterable[BlockSpec]) -> dict[str, Any]:
    """`UiState.blocks` for a freshly started session, keyed by block id."""
    return {spec.id: initial_block_state(spec) for spec in specs}


def validate_block_state(block_type: BlockType | None, state: dict[str, Any]) -> dict[str, Any]:
    """Validate `state` against `block_type`'s model and return it as plain JSON.

    Unknown or envelope/custom types pass through unchanged (as JSON data).

    Raises:
        pydantic.ValidationError: When `state` does not fit the block type.
    """
    model = BLOCK_STATE_MODELS.get(block_type) if block_type is not None else None
    if model is None:
        return {str(k): jsonable(v) for k, v in state.items()}
    return _dump(model.model_validate(state))


def block_ids_of_type(specs: Iterable[BlockSpec], block_type: BlockType) -> list[str]:
    """Ids of every block of `block_type`, in render order."""
    return [spec.id for spec in specs if spec.type == block_type]


def session_block_specs(ui: Any, panel: PanelLayout) -> list[BlockSpec]:
    """The blocks of the running session.

    Prefers the specs the `UiChannel` was initialised with (they include the
    pack's `default_panel` fallback); falls back to `AgentConfig.panel.blocks`
    for channels without block support (test doubles, the no-op channel) and
    for code that runs before `PlatformAgent` seeds the channel (tool
    registration).
    """
    specs = getattr(ui, "block_specs", None)
    if isinstance(specs, dict) and specs:
        return list(specs.values())
    return resolve_block_specs(panel)


def pick_block(specs: Iterable[BlockSpec], block_type: BlockType, requested: str | None) -> str:
    """Resolve the block a tool call targets.

    Args:
        specs: The session's blocks.
        block_type: The type the tool writes.
        requested: The id the model asked for (may be empty).

    Returns:
        `requested` when it names a block of `block_type`; otherwise the only
        block of that type when there is exactly one.

    Raises:
        ValueError: With a model-readable message when the target is ambiguous
            or missing.
    """
    candidates = block_ids_of_type(specs, block_type)
    if requested and requested in candidates:
        return requested
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(f"This panel has no {block_type} block.")
    raise ValueError(f"Unknown {block_type} block {requested!r}; use one of: {', '.join(candidates)}.")


def describe_blocks(specs: Iterable[BlockSpec], types: Iterable[BlockType] | None = None) -> str:
    """A one-line inventory of the panel's blocks for a tool description."""
    wanted = set(types) if types is not None else None
    parts = [
        f"{spec.id} ({spec.type}{': ' + spec.title if spec.title else ''})"
        for spec in specs
        if wanted is None or spec.type in wanted
    ]
    return "; ".join(parts) if parts else "none"


def choice_selection_error(state: Mapping[str, Any], selected: Any) -> str | None:
    """Why `selected` is not a valid answer to a `choices` block, or `None` when it is.

    Valid: a list of distinct option ids of the block, exactly one unless the
    block is `multi`, at least one.
    """
    if not isinstance(selected, list) or not all(isinstance(s, str) for s in selected):
        return "selected must be a list of option ids"
    options = state.get("options")
    ids = [o.get("id") for o in options if isinstance(o, dict)] if isinstance(options, list) else []
    unknown = [s for s in selected if s not in ids]
    if unknown:
        return f"unknown option(s) {', '.join(unknown)}; the options are {', '.join(map(str, ids)) or 'none'}"
    if not selected:
        return "pick at least one option"
    if len(set(selected)) != len(selected):
        return "each option can be picked once"
    if len(selected) > 1 and not state.get("multi"):
        return "this question takes one answer"
    return None


def flow_steps_specs(specs: Iterable[BlockSpec]) -> list[BlockSpec]:
    """The `steps` blocks that follow the flow (`config.source == "flow"`, V5-08)."""
    return [spec for spec in specs if spec.type == "steps" and spec.config.get("source") == "flow"]


def flow_steps_state(
    config: Mapping[str, Any],
    *,
    nodes: Sequence[tuple[str, str]],
    path: Sequence[str],
    current: str,
    finished: bool,
) -> dict[str, Any]:
    """A `steps` block's state for the flow position (`source="flow"`, D-V5-33).

    The steps are the block's configured `steps` (ids name flow nodes) or,
    without any, the flow's agent nodes in spec order. A step is `active`
    when it is the current node, `done` once visited, `skipped` when a later
    step was reached without it, and `pending` otherwise; after the flow
    reached an end node every visited step is `done` and nothing is current.

    Args:
        config: The block's `BlockSpec.config`.
        nodes: `(node id, label)` of the flow's agent nodes, in spec order.
        path: `FlowState.path` (visited node ids, in order).
        current: `FlowState.current_node`.
        finished: Whether the flow reached an end node.

    Returns:
        A plain-JSON `StepsBlockState`.
    """
    configured = config.get("steps")
    steps: list[tuple[str, str]] = []
    if isinstance(configured, list):
        steps = [
            (str(s["id"]), str(s.get("label") or s["id"]))
            for s in configured
            if isinstance(s, dict) and s.get("id")
        ]
    if not steps:
        steps = list(nodes)
    visited = set(path)
    reached = [i for i, (step_id, _) in enumerate(steps) if step_id in visited]
    last_reached = max(reached) if reached else -1
    items: list[dict[str, Any]] = []
    for index, (step_id, label) in enumerate(steps):
        if step_id == current and not finished:
            status = "active"
        elif step_id in visited:
            status = "done"
        elif index < last_reached:
            status = "skipped"
        else:
            status = "pending"
        items.append({"id": step_id, "label": label, "status": status})
    active = current if not finished and any(step_id == current for step_id, _ in steps) else None
    return _dump(StepsBlockState.model_validate({"steps": items, "current": active}))


def find_citation(state: Mapping[str, Any], data: Mapping[str, Any]) -> KbCitation | None:
    """The citation a `block_action {name: "open_citation"}` names.

    Args:
        state: The `kb_citations` block's state.
        data: The action's `data`: `{chunk_id}` (preferred) or `{index}`.

    Returns:
        The citation, or `None` when it is not on the block (any more).
    """
    items = state.get("items")
    if not isinstance(items, list):
        return None
    chunk_id = data.get("chunk_id")
    index = data.get("index")
    raw: Any = None
    if isinstance(chunk_id, str) and chunk_id:
        raw = next((i for i in items if isinstance(i, dict) and i.get("chunk_id") == chunk_id), None)
    elif isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(items):
        raw = items[index]
    if not isinstance(raw, dict):
        return None
    try:
        return KbCitation.model_validate(raw)
    except ValidationError:
        return None


def citation_note(citation: KbCitation) -> str:
    """The highlight note for a citation: its heading path, else its filename."""
    if citation.heading_path:
        return " › ".join(citation.heading_path)
    return citation.filename


async def open_citation(ui: UiChannel, block_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
    """Handle `block_action {name: "open_citation"}` on a `kb_citations` block (E1, V5-08).

    Opens the cited page in the panel's first `document` block when the
    session holds the source document as an asset (an `AssetRef` whose
    `meta.document_id` is the citation's `document_id`; V5-19 delivers KB
    documents that way), highlighting the page with the heading path. Without
    a document block or a source the citation comes back, so the browser can
    preview the passage itself.

    Args:
        ui: The session's channel.
        block_id: The `kb_citations` block the action came from.
        data: The action's `data` (`{chunk_id}` or `{index}`).

    Returns:
        `{"opened": "document", "block_id", "page"}` when the document block
        now shows the page; otherwise `{"opened": False, "reason", "citation"?}`
        with `reason` one of `unknown_citation`, `no_document_block`,
        `no_source`.
    """
    citation = find_citation(ui.state.blocks.get(block_id) or {}, data)
    if citation is None:
        return {"opened": False, "reason": "unknown_citation"}
    documents = block_ids_of_type(ui.block_specs.values(), "document")
    preview = citation.model_dump(mode="json", exclude_none=True)
    if not documents:
        return {"opened": False, "reason": "no_document_block", "citation": preview}
    asset_id = next(
        (
            a.asset_id
            for a in reversed(ui.state.assets)
            if citation.document_id and a.meta.get(ASSET_DOCUMENT_ID_KEY) == citation.document_id
        ),
        None,
    )
    if asset_id is None:
        return {"opened": False, "reason": "no_source", "citation": preview}
    page = max(1, citation.page or 1)
    state = DocumentBlockState(
        asset_id=asset_id,
        page=page,
        highlights=[DocumentHighlight(page=page, bbox=FULL_PAGE_BBOX, note=citation_note(citation))],
    )
    target = documents[0]
    await ui.set_block(target, state.model_dump(mode="json"))
    ui.show_block(target)
    return {"opened": "document", "block_id": target, "page": page}


def _dump(instance: BaseModel) -> dict[str, Any]:
    return instance.model_dump(mode="json", by_alias=True)


def _is_field(model: type[BaseModel], key: str) -> bool:
    if key in model.model_fields:
        return True
    return any(info.alias == key for info in model.model_fields.values())
