"""Panel v2 blocks: layout resolution, initial block states and validation.

Pure helpers shared by `UiChannel` (block state), `PlatformAgent` (block init)
and the block-writing built-in tools (CONTRACTS-V2 §4.4, ARCHITECTURE-V2
D-V2-12). Nothing here talks to LiveKit; `open_citation` (V5-08) only drives
the channel it is handed.

Caller edits (V6-06, D-V6-19): `caller_edit_refusal` and `check_caller_edit`
check a `block_action {name: "edit"}` against the block (a `details` value or a
`checklist` tick, only with `caller_can_edit`); `caller_edit_message` is the one
line the model hears, the change fenced as `<untrusted source="caller_edit">`.

Notebooks (V6-08, D-V6-15): `notebook_sections` reads a notebook's sections from its
config (the order they render in); its state keys each section's content by id, seeded
empty by `initial_block_state`. A caller may edit a notebook with `caller_can_write`: add
or change a note in a text section, tick an item, change a details value; never an ink
section. A `layout` block (D-V6-18) holds no state.

Canvases (V6-12, D-V6-16): a `canvas` block's state (`CanvasBlockState`) starts blank, or on
the live camera when its config says so (a config `background: "asset"` waits for the agent to
put a picture on it); `canvas_config` reads its config. A notebook `ink` section's state names
the board its config names (`canvas_block_id`). The caller's strokes are written by the
channel's ink handler (`lkap_agent.ui.ink`), never by a caller edit.

Block state lives in `UiState.blocks[<BlockSpec.id>]` as **plain JSON
dicts** (never model instances): `FormBlockState.schema_` carries the alias
`schema`, and `UiSnapshot.model_dump_json()` does not dump by alias, so a
stored model would put `schema_` on the wire.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from lkap_contracts.agent_config import PanelLayout
from lkap_contracts.blocks import CanvasBlockConfig, NotebookBlockConfig, NotebookSectionConfig
from lkap_contracts.packs import PackManifest
from lkap_contracts.ui_protocol import (
    CALLER_EDIT_FLAGS,
    EDITABLE_BLOCK_TYPES,
    MAX_CALLER_EDIT_CHARS,
    MAX_NOTEBOOK_ENTRIES,
    BlockSpec,
    BlockType,
    CanvasBlockState,
    CaptionsBlockState,
    CardsBlockState,
    ChecklistEdit,
    ChecklistItem,
    ChoicesBlockState,
    ConsentBlockState,
    DetailsBlockState,
    DetailsEdit,
    DocumentBlockState,
    DocumentHighlight,
    FormBlockState,
    GalleryBlockState,
    HandoffBlockState,
    HandoffStatus,
    KbCitation,
    KbCitationsBlockState,
    LinkBlockState,
    MarkdownBlockState,
    NotebookBlockState,
    NotebookEdit,
    SlotsBlockState,
    StepsBlockState,
    TableBlockState,
    TranscriptBlockState,
    UiPatchOp,
    UploadBlockState,
    VideoBlockState,
)
from pydantic import BaseModel, ValidationError

from lkap_agent.logging import get_logger
from lkap_agent.tools.untrusted import fence, strip_control

if TYPE_CHECKING:
    from lkap_agent.ui.channel import UiChannel

__all__ = [
    "ASSET_DOCUMENT_ID_KEY",
    "BLOCK_STATE_MODELS",
    "CALLER_EDIT_SOURCE",
    "CARD_SELECT",
    "CallerEdit",
    "caller_edit_message",
    "caller_edit_refusal",
    "check_caller_edit",
    "LINK_OPENED",
    "card_action_error",
    "find_card",
    "find_slot",
    "slot_selection_error",
    "COMPOSITE_PANEL_ID",
    "ENVELOPE_BLOCK_TYPES",
    "FULL_PAGE_BBOX",
    "OPEN_CITATION",
    "VOICE_ONLY_CHANNELS",
    "block_ids_of_type",
    "block_path",
    "canvas_config",
    "choice_selection_error",
    "citation_note",
    "describe_blocks",
    "find_citation",
    "flow_steps_specs",
    "flow_steps_state",
    "open_citation",
    "pick_block",
    "set_handoff",
    "session_block_specs",
    "initial_block_state",
    "initial_block_states",
    "jsonable",
    "NOTEBOOK_ENTRY_ID_PREFIX",
    "empty_notebook_sections",
    "new_notebook_entry_id",
    "notebook_section_ops",
    "notebook_section_state",
    "notebook_sections",
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
    "consent": ConsentBlockState,
    "upload": UploadBlockState,
    # asks #202 (V5-31): seeded from the config and validated on every patch.
    "captions": CaptionsBlockState,
    "handoff": HandoffBlockState,
    # V5-43
    "link": LinkBlockState,
    "slots": SlotsBlockState,
    "cards": CardsBlockState,
    # V6-08: a notebook's sections (a `layout` holds no state and starts as `{}`).
    "notebook": NotebookBlockState,
    # V6-12: a drawing board.
    "canvas": CanvasBlockState,
}

#: `block_action` name a `cards` block sends when the caller taps a card itself (V5-43).
CARD_SELECT: Final[str] = "select"
#: `block_action` name a `link` block sends when the caller opens the link (V5-43).
LINK_OPENED: Final[str] = "opened"

#: Session channels with no screen: panel blocks are invisible there (V5-08).
VOICE_ONLY_CHANNELS: Final[frozenset[str]] = frozenset({"sip_in", "sip_out"})

#: `block_action` name a `kb_citations` block sends when the user taps a citation (E1).
OPEN_CITATION: Final[str] = "open_citation"

#: `AssetRef.meta` key naming the knowledge-base document an asset holds (V5-19 pushes them).
ASSET_DOCUMENT_ID_KEY: Final[str] = "document_id"

#: The fence source of a caller's edit in what the model hears (V6-06, `FENCED_SITES`).
CALLER_EDIT_SOURCE: Final[str] = "caller_edit"

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
    if spec.type == "notebook":
        # V6-08: the config lists the sections; the state keys each one's content by id.
        return _dump(NotebookBlockState.model_validate({"sections": empty_notebook_sections(spec)}))
    if spec.type == "canvas":
        # V6-12: the config's `background` says what the board starts on; only the live camera
        # is a state value from the start (a picture is put on it by the agent, per session).
        live = canvas_config(spec).background == "live_camera"
        return _dump(CanvasBlockState(background="live_camera" if live else "none"))
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
        state = _dump(model.model_validate(seed))
    except ValidationError as exc:
        logger.warning("block config does not seed a valid state", block_id=spec.id, error=str(exc))
        state = _dump(model())
    return state


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


def slot_selection_error(state: Mapping[str, Any], selected: Any) -> str | None:
    """Why `selected` is not a valid answer to a `slots` block, or `None` when it is (V5-43).

    Valid: the id of one of the block's own slots (a browser's start and end are never read).
    """
    if not isinstance(selected, str) or not selected:
        return "selected must be a slot id"
    if find_slot(state, selected) is None:
        slots = state.get("slots")
        ids = [str(s.get("id")) for s in slots if isinstance(s, dict)] if isinstance(slots, list) else []
        return f"unknown slot {selected}; the slots are {', '.join(ids) or 'none'}"
    return None


def find_slot(state: Mapping[str, Any], slot_id: str) -> dict[str, Any] | None:
    """The slot `slot_id` of a `slots` block's state, or `None`."""
    slots = state.get("slots")
    if not isinstance(slots, list):
        return None
    return next((s for s in slots if isinstance(s, dict) and s.get("id") == slot_id), None)


def find_card(state: Mapping[str, Any], card_id: Any) -> dict[str, Any] | None:
    """The card `card_id` of a `cards` block's state, or `None`."""
    cards = state.get("cards")
    if not isinstance(card_id, str) or not isinstance(cards, list):
        return None
    return next((c for c in cards if isinstance(c, dict) and c.get("id") == card_id), None)


def card_action_error(
    state: Mapping[str, Any], config: Mapping[str, Any], name: str, data: Mapping[str, Any]
) -> str | None:
    """Why a `block_action` on a `cards` block is refused, or `None` when it names a real card and action.

    `select` (a tap on the card) needs `selectable` (default on); any other name must be one
    of the card's own action buttons.
    """
    card = find_card(state, data.get("card_id"))
    if card is None:
        return "unknown card"
    if name == CARD_SELECT:
        return None if config.get("selectable", True) is not False else "cards cannot be picked here"
    actions = card.get("actions")
    names = [a.get("name") for a in actions if isinstance(a, dict)] if isinstance(actions, list) else []
    return None if name in names else f"the card has no {name!r} button"


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
    `meta.document_id` is the citation's `document_id`), highlighting the page
    with the heading path. A document the session does not hold yet is copied
    in on first use (R-V5-5: `UiChannel.asset_from_document`, which asks the
    api to copy it from the agent's knowledge bases and streams it to the
    browser); nothing is pre-loaded. Without a document block or a source the
    citation comes back, so the browser can preview the passage itself.

    Args:
        ui: The session's channel.
        block_id: The `kb_citations` block the action came from.
        data: The action's `data` (`{chunk_id}` or `{index}`).

    Returns:
        `{"opened": "document", "block_id", "page"}` when the document block
        now shows the page; otherwise `{"opened": False, "reason", "citation"?}`
        with `reason` one of `unknown_citation`, `no_document_block`,
        `no_source`, `no_preview` (a document type the panel cannot show).
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
    if asset_id is None and citation.document_id:
        fetch = getattr(ui, "asset_from_document", None)
        if callable(fetch):
            asset_id, reason = await fetch(citation.document_id)
            if asset_id is None:
                return {"opened": False, "reason": reason or "no_source", "citation": preview}
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


async def set_handoff(
    ui: Any,
    panel: PanelLayout,
    status: HandoffStatus,
    *,
    mode: str | None = None,
    target: str | None = None,
    agent_name: str | None = None,
    reason: str | None = None,
) -> list[str]:
    """Write the hand-off state into every `handoff` block of the session (V5-32).

    `transfer_call` drives it `requested -> connecting -> connected | timeout |
    ended`; a panel without a `handoff` block is a no-op. A block that cannot be
    written (a channel without block support) is skipped with a debug line: the
    transfer itself must never fail on the panel.

    Args:
        ui: The session's `UiChannel`.
        panel: The agent's panel layout (fallback when the channel holds no specs).
        status: The new status.
        mode: The transfer that runs (`cold` / `warm`).
        target: The destination's label (never its number).
        agent_name: The person's name once connected, when known.
        reason: A short plain-words line (`timeout`).

    Returns:
        The ids of the blocks written.
    """
    ids = block_ids_of_type(session_block_specs(ui, panel), "handoff")
    if not ids:
        return []
    state = HandoffBlockState(
        status=status,
        mode=mode if mode in ("cold", "warm") else None,
        target=target,
        agent_name=agent_name,
        reason=reason,
    )
    written: list[str] = []
    for block_id in ids:
        try:
            await ui.set_block(block_id, _dump(state))
        except Exception:  # noqa: BLE001 - the panel is advisory; the transfer goes on
            logger.debug("handoff block not written", block_id=block_id, exc_info=True)
            continue
        written.append(block_id)
    return written


# --------------------------------------------------------------------- caller edits (V6-06)


@dataclass(frozen=True, slots=True)
class CallerEdit:
    """A caller's edit of a block, checked against the block (V6-06, D-V6-19).

    Attributes:
        block_type: The edited block's type (one of ``EDITABLE_BLOCK_TYPES``).
        block_ops: Ops relative to the block (a ``details`` value), for the block validators.
        envelope_ops: Absolute ops on the envelope (a ``checklist`` tick).
        data: What the platform and the pack are handed: ``{key, label, value}`` for a
            details row, ``{item_id, label, done}`` for a checklist item. ``value`` is the
            caller's text, cleaned (control characters dropped, spaces collapsed).
        changed: ``False`` when the edit changes nothing; it is then neither applied nor told.
    """

    block_type: str
    block_ops: list[UiPatchOp] = field(default_factory=list)
    envelope_ops: list[UiPatchOp] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    changed: bool = True


def caller_edit_refusal(spec: BlockSpec | None) -> str | None:
    """Why the caller may not edit the block `spec`, or `None` when they may (V6-06).

    A caller may edit a block of ``EDITABLE_BLOCK_TYPES`` whose config sets
    ``caller_can_edit``. Every other built-in block refuses, requestable, link,
    consent, upload, captions and handoff blocks included (a ``custom`` block is the
    pack's, and never reaches here).
    """
    if spec is None:
        return "unknown block"
    if spec.type not in EDITABLE_BLOCK_TYPES:
        return f"a {spec.type} block cannot be changed from the page"
    # V6-08: each editable type names its own flag (`caller_can_write` on a notebook).
    if spec.config.get(CALLER_EDIT_FLAGS.get(spec.type, "caller_can_edit")) is not True:
        return "this block cannot be changed from the page"
    return None


def _clean_caller_text(text: str) -> str:
    return " ".join(strip_control(text).split())


def _details_value(text: str, value_type: str) -> str | float | None:
    """A details value as `set_details` stores it: empty clears, numbers stay numbers."""
    if not text:
        return None
    if value_type in ("number", "money"):
        try:
            return float(text.replace(",", ""))
        except ValueError:
            return text
    return text


def check_caller_edit(
    spec: BlockSpec,
    state: Mapping[str, Any],
    checklist: Sequence[ChecklistItem],
    data: Mapping[str, Any],
    *,
    now: float,
) -> CallerEdit | str:
    """Check a caller's ``block_action {name: "edit", data}`` against the block (V6-06).

    Call :func:`caller_edit_refusal` first. A ``details`` edit (:class:`DetailsEdit`)
    changes the value of a row already on the card, keeping its label and type; a
    ``checklist`` edit (:class:`ChecklistEdit`) ticks or unticks an item of the envelope
    checklist. Either marks the row or item ``edited_by: "caller"``.

    Args:
        spec: The edited block (an editable type that allows edits).
        state: The block's current state (``details``).
        checklist: The envelope checklist (``checklist``).
        data: The action's ``data`` as the browser sent it.
        now: The time stamped on a changed details row.

    Returns:
        The checked edit, or a plain-words refusal.
    """
    if spec.type == "details":
        try:
            details = DetailsEdit.model_validate(data)
        except ValidationError:
            return f"a change needs the row's key and a value of at most {MAX_CALLER_EDIT_CHARS} characters"
        rows = state.get("items")
        row = (
            next(
                (r for r in rows if isinstance(r, dict) and r.get("key") == details.key),
                None,
            )
            if isinstance(rows, list)
            else None
        )
        if row is None:
            return f"the card has no row {details.key!r}"
        text = _clean_caller_text(details.value)
        value = _details_value(text, str(row.get("type") or "string"))
        label = str(row.get("label") or details.key)
        summary: dict[str, Any] = {"key": details.key, "label": label, "value": text}
        if row.get("value") == value:
            return CallerEdit(block_type="details", data=summary, changed=False)
        updated = {**row, "value": value, "updated_at": now, "edited_by": "caller"}
        op = UiPatchOp(op="upsert", path="/items", value=updated, key=details.key)
        return CallerEdit(block_type="details", block_ops=[op], data=summary)
    if spec.type == "checklist":
        try:
            tick = ChecklistEdit.model_validate(data)
        except ValidationError:
            return "a tick needs the item's id and whether it is done"
        item = next((i for i in checklist if i.id == tick.item_id), None)
        if item is None:
            return f"the checklist has no item {tick.item_id!r}"
        ticked: dict[str, Any] = {"item_id": item.id, "label": item.label, "done": tick.done}
        if item.done == tick.done:
            return CallerEdit(block_type="checklist", data=ticked, changed=False)
        new_item = item.model_copy(update={"done": tick.done, "edited_by": "caller"})
        op = UiPatchOp(op="upsert", path="/checklist", value=new_item, key=item.id)
        return CallerEdit(block_type="checklist", envelope_ops=[op], data=ticked)
    if spec.type == "notebook":
        return _check_notebook_edit(spec, state, data, now=now)
    return f"a {spec.type} block cannot be changed from the page"  # pragma: no cover - refused earlier


def _list_of(content: Mapping[str, Any], key: str) -> list[Any]:
    value = content.get(key)
    return value if isinstance(value, list) else []


def _check_notebook_edit(
    spec: BlockSpec, state: Mapping[str, Any], data: Mapping[str, Any], *, now: float
) -> CallerEdit | str:
    """A caller's :class:`NotebookEdit` checked against the notebook's sections (V6-08)."""
    try:
        edit = NotebookEdit.model_validate(data)
    except ValidationError:
        return (
            "a change names the section and one change: a note of at most "
            f"{MAX_CALLER_EDIT_CHARS} characters, a tick, or a row's value"
        )
    section = next((s for s in notebook_sections(spec) if s.id == edit.section_id), None)
    if section is None:
        return f"the notebook has no section {edit.section_id!r}"
    wanted = {"note": "text", "tick": "checklist", "row": "details"}[edit.change]
    if section.kind == "ink":
        return "drawings cannot be changed this way"
    if section.kind != wanted:
        return f"the section {section.id!r} holds a {section.kind}, not a {wanted}"
    title = section.title or section.id
    content = notebook_section_state(state, section)
    base = f"/sections/{section.id}"
    ops = notebook_section_ops(state, section)
    summary: dict[str, Any] = {"section_id": section.id, "section": title}
    if edit.change == "tick":
        items = _list_of(content, "items")
        item = next((i for i in items if isinstance(i, dict) and i.get("id") == edit.item_id), None)
        if item is None:
            return f"the list has no item {edit.item_id!r}"
        summary.update(item_id=item["id"], label=str(item.get("label") or item["id"]), done=edit.done)
        if bool(item.get("done")) == edit.done:
            return CallerEdit(block_type="notebook", data=summary, changed=False)
        updated = {**item, "done": edit.done, "edited_by": "caller"}
        ops.append(UiPatchOp(op="upsert", path=f"{base}/items", value=updated, key=item["id"]))
    elif edit.change == "row":
        rows = _list_of(content, "items")
        row = next((r for r in rows if isinstance(r, dict) and r.get("key") == edit.key), None)
        if row is None:
            return f"the summary has no row {edit.key!r}"
        text = _clean_caller_text(edit.value or "")
        value = _details_value(text, str(row.get("type") or "string"))
        summary.update(key=row["key"], label=str(row.get("label") or row["key"]), value=text)
        if row.get("value") == value:
            return CallerEdit(block_type="notebook", data=summary, changed=False)
        updated = {**row, "value": value, "updated_at": now, "edited_by": "caller"}
        ops.append(UiPatchOp(op="upsert", path=f"{base}/items", value=updated, key=row["key"]))
    else:
        entries = _list_of(content, "entries")
        text = _clean_caller_text(edit.text or "")
        if edit.entry_id is None:
            if not text:
                return "write something first"
            if len(entries) >= MAX_NOTEBOOK_ENTRIES:
                return "this section is full"
            entry = {"id": new_notebook_entry_id(), "text": text, "author": "caller", "ts": now}
            ops.append(UiPatchOp(op="append", path=f"{base}/entries", value=entry))
            summary.update(key=entry["id"], change="added", text=text)
        else:
            current = next((e for e in entries if isinstance(e, dict) and e.get("id") == edit.entry_id), None)
            if current is None:
                return f"the section has no note {edit.entry_id!r}"
            summary.update(key=current["id"])
            if not text:
                ops.append(UiPatchOp(op="remove", path=f"{base}/entries", key=current["id"]))
                summary.update(change="removed", text=_clean_caller_text(str(current.get("text") or "")))
            elif text == current.get("text"):
                return CallerEdit(
                    block_type="notebook", data={**summary, "change": "changed", "text": text}, changed=False
                )
            else:
                edited = "caller" if current.get("author") != "caller" else current.get("edited_by")
                updated = {**current, "text": text, "edited_by": edited}
                ops.append(UiPatchOp(op="upsert", path=f"{base}/entries", value=updated, key=current["id"]))
                summary.update(change="changed", text=text)
    ops.append(UiPatchOp(op="set", path="/updated_at", value=now))
    return CallerEdit(block_type="notebook", block_ops=ops, data=summary)


# --------------------------------------------------------------------- notebook (V6-08)

#: Prefix of the ids the platform gives notebook entries. Keys the agent chooses never contain
#: `:` (`notebook_write` refuses one), so an entry's id never matches another entry's key.
NOTEBOOK_ENTRY_ID_PREFIX: Final[str] = "n:"


def new_notebook_entry_id() -> str:
    """A fresh, unguessable notebook entry id (``n:`` and 10 hex characters)."""
    return f"{NOTEBOOK_ENTRY_ID_PREFIX}{uuid.uuid4().hex[:10]}"


def notebook_sections(spec: BlockSpec) -> list[NotebookSectionConfig]:
    """The sections a notebook block's config lists, in order (none when the config is invalid)."""
    try:
        return list(NotebookBlockConfig.model_validate(spec.config).sections)
    except ValidationError:
        logger.warning("notebook config does not validate", block_id=spec.id)
        return []


def _empty_section(section: NotebookSectionConfig) -> dict[str, Any]:
    match section.kind:
        case "text":
            return {"kind": "text", "entries": []}
        case "checklist" | "details":
            return {"kind": section.kind, "items": []}
        case _:
            # V6-12: the section shows the board its config names (ask #57).
            return {"kind": "ink", "canvas_block_id": section.canvas_block_id}


def empty_notebook_sections(spec: BlockSpec) -> dict[str, Any]:
    """The empty content of every section of a notebook block, keyed by section id."""
    return {section.id: _empty_section(section) for section in notebook_sections(spec)}


def notebook_section_state(state: Mapping[str, Any], section: NotebookSectionConfig) -> dict[str, Any]:
    """A section's current content, or its empty content when the state lacks it or it has another kind."""
    sections = state.get("sections")
    content = sections.get(section.id) if isinstance(sections, dict) else None
    if isinstance(content, dict) and content.get("kind") == section.kind:
        return content
    return _empty_section(section)


def notebook_section_ops(state: Mapping[str, Any], section: NotebookSectionConfig) -> list[UiPatchOp]:
    """Ops (relative to the block) that give `section` its empty content when the state lacks it.

    A config changed after the session started, or a state written before it, may miss a
    section or hold another kind under its id; writing into it starts it afresh.
    """
    sections = state.get("sections")
    content = sections.get(section.id) if isinstance(sections, dict) else None
    if isinstance(content, dict) and content.get("kind") == section.kind:
        return []
    return [UiPatchOp(op="set", path=f"/sections/{section.id}", value=_empty_section(section))]


def caller_edit_message(spec: BlockSpec, data: Mapping[str, Any]) -> str:
    """The one line the model hears about a caller's edit (V6-06); the change itself is fenced.

    The caller typed the value, so the whole change travels inside
    ``<untrusted source="caller_edit">`` (R-V5-15): data to check, never instructions.
    """
    label = str(data.get("label") or data.get("key") or data.get("item_id") or "")
    where = spec.title or spec.id
    if spec.type == "notebook":
        change = _notebook_change(where, data)
    elif spec.type == "checklist":
        change = f'ticked "{label}"' if data.get("done") else f'unticked "{label}"'
        change += f" on the checklist {where}"
    else:
        value = str(data.get("value") or "")
        change = f'changed "{label}" on {where} to "{value}"' if value else f'cleared "{label}" on {where}'
    body = fence(change, source=CALLER_EDIT_SOURCE, max_chars=MAX_CALLER_EDIT_CHARS + 200)
    return (
        f"[The caller edited the panel on screen: {body}. Take it into account, confirm it with "
        "the caller if it matters, and carry on.]"
    )


def _notebook_change(where: str, data: Mapping[str, Any]) -> str:
    """What a caller's notebook edit did, in words (V6-08); the caller's text is quoted as is."""
    section = f'the notebook section "{data.get("section") or data.get("section_id") or ""}"'
    if "item_id" in data:
        label = str(data.get("label") or data.get("item_id"))
        verb = "ticked" if data.get("done") else "unticked"
        return f'{verb} "{label}" in {section} of {where}'
    if "value" in data:
        label = str(data.get("label") or data.get("key"))
        value = str(data.get("value") or "")
        if value:
            return f'changed "{label}" in {section} of {where} to "{value}"'
        return f'cleared "{label}" in {section} of {where}'
    text = str(data.get("text") or "")
    match data.get("change"):
        case "added":
            return f'added a note to {section} of {where}: "{text}"'
        case "removed":
            return f'removed the note "{text}" from {section} of {where}'
        case _:
            return f'changed a note in {section} of {where} to "{text}"'


def canvas_config(spec: BlockSpec) -> CanvasBlockConfig:
    """A canvas block's config (V6-12); the defaults (nothing the caller may do) when it is invalid."""
    try:
        return CanvasBlockConfig.model_validate(spec.config)
    except ValidationError:
        logger.warning("canvas config does not validate", block_id=spec.id)
        return CanvasBlockConfig()


def _dump(instance: BaseModel) -> dict[str, Any]:
    return instance.model_dump(mode="json", by_alias=True)


def _is_field(model: type[BaseModel], key: str) -> bool:
    if key in model.model_fields:
        return True
    return any(info.alias == key for info in model.model_fields.values())
