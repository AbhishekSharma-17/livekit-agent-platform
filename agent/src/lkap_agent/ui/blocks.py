"""Panel v2 blocks: layout resolution, initial block states and validation.

Pure helpers shared by `UiChannel` (block state), `PlatformAgent` (block init)
and the block-writing built-in tools (CONTRACTS-V2 §4.4, ARCHITECTURE-V2
D-V2-12). Nothing here talks to LiveKit.

Block state lives in `UiState.blocks[<BlockSpec.id>]` as **plain JSON
dicts** (never model instances): `FormBlockState.schema_` carries the alias
`schema`, and `UiSnapshot.model_dump_json()` does not dump by alias, so a
stored model would put `schema_` on the wire.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Final

from lkap_contracts.agent_config import PanelLayout
from lkap_contracts.packs import PackManifest
from lkap_contracts.ui_protocol import (
    BlockSpec,
    BlockType,
    DocumentBlockState,
    FormBlockState,
    GalleryBlockState,
    KbCitationsBlockState,
    TableBlockState,
    TranscriptBlockState,
    VideoBlockState,
)
from pydantic import BaseModel, ValidationError

from lkap_agent.logging import get_logger

__all__ = [
    "BLOCK_STATE_MODELS",
    "COMPOSITE_PANEL_ID",
    "ENVELOPE_BLOCK_TYPES",
    "block_ids_of_type",
    "block_path",
    "describe_blocks",
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
}


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


def _dump(instance: BaseModel) -> dict[str, Any]:
    return instance.model_dump(mode="json", by_alias=True)


def _is_field(model: type[BaseModel], key: str) -> bool:
    if key in model.model_fields:
        return True
    return any(info.alias == key for info in model.model_fields.values())
