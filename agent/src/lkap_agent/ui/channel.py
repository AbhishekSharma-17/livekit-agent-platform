"""`UiChannel` — the agent's handle to the `lkap.ui.*` topics and RPCs.

Implements `packs.base.UiChannel` (docs/CONTRACTS.md §10) over a real
`livekit.rtc.Room`. State mutations are applied to a local copy of
`UiState` and mirrored to the browser as `UiSnapshot`/`UiPatch` JSON on the
`lkap.ui.state` text stream; images go out on the `lkap.ui.asset` byte
stream; `lkap.agent.action` (UI -> agent) is dispatched here to optional
platform/pack callbacks, and `lkap.ui.request` (agent -> UI) is a thin
`perform_rpc` wrapper.

v2 panel blocks (CONTRACTS-V2 §4.4): `UiState.blocks[<block id>]` holds each
block's state as plain JSON (see `lkap_agent.ui.blocks`), written through
`set_block` / `patch_block` / `cite` / `request_block` and carried by the same
snapshot/patch stream (paths under `/blocks/<id>`).

`request_block` (V5-02) is the generic blocking request every requestable
block (`RequestableState`) uses, as a two-channel round trip: the block state
flips to `status="requested"` (so a reconnecting browser re-renders the
request from a snapshot), a short `lkap.ui.request {method: "request"}` RPC
asks the browser to show it, and the answer normally comes back as
`lkap.agent.action {action: "block_submit"}`. At most one request is pending
per block. `request_form` is the v2 form request, now a thin alias over the
same machinery with `method="form"` and its v2 statuses. `submit_block`
(V5-08) answers a request from the agent side (a choice spoken aloud), and
`block_action {name: "open_citation"}` on a `kb_citations` block is handled
here (`ui.blocks.open_citation`) before any pack callback.

V5-43: a `link` block's `block_action {name: "opened"}` marks the link opened;
a `cards` block's `block_action` must name a card on the block and either
`select` (with `selectable`) or one of that card's buttons: a tap on the card
records `selected`, and the pack's and the platform's callbacks see only
checked actions. A `slots` answer (`block_submit {values: {selected}}`) is
checked against the block's own slots. `apply_link_outcome` applies the
outcome the api reports for a `link` block (a `link_completed` packet), only
while the link is `pending` or `opened`. `state_delta` (the AG-UI adapter,
`lkap_contracts.ui_agui`) writes RFC 6902 operations to the blocks listed in
:data:`STATE_DELTA_BLOCK_TYPES`, all or nothing, through the same validators
as `patch_block`.

Note on attribute naming: the byte-stream attribute and `AssetRef` field
carrying the caption are named `caption` (docs/CONTRACTS.md §10 wins over
docs/ARCHITECTURE.md §9's `caption_ref`, which is stale).

Caller files (V5-19): the browser streams a file on `lkap.ui.upload`
(attributes `block_id`, `name`, and `field` for a form) while an `upload`
block (or a form with that `file` field) is `requested`. `receive_upload`
takes it only from the caller's identity, refuses it before reading when the
declared size or the count is over the block's limits, stops reading past
`max_bytes`, sniffs the real type (`lkap_contracts.blocks.sniff_mime`) against
`accept`, and only then posts it to the api (`AssetApi.post_asset`, which
checks it again). A stored file is streamed back on `lkap.ui.asset` under its
stored id (the display path), appended to `/assets` (`stored: true`), to the
block's `files` and, for an image, to every gallery; a refused one becomes a
`rejected` row with the reason. The worker keeps the bytes of recent files in
memory for `describe_asset` and reads older ones back from the api. The file's
name and content never reach a log line.

Live captions (V5-31): while the panel has a ``captions`` block, both sides of
the conversation stream on ``lkap.captions`` as `CaptionSegment` JSON (one
message per update; an utterance keeps its ``id`` from its first interim to
its final). :class:`CaptionStream` builds the segments: the caller's from
`user_input_transcribed` (interim and final, with the transcriber's language),
the agent's from :class:`CaptionsTextOutput`, a text output the session builder
puts after RoomIO's transcription output
(`TextOutputOptions(next_in_chain=...)`), so the agent's words arrive already
in step with its audio (after the `TranscriptSynchronizer`), as deltas.
Interim agent captions are sent at most every :data:`CAPTION_INTERIM_INTERVAL_S`;
the final one always goes. A caption that cannot be sent is dropped (debug
log): captions never hold the conversation up.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import mimetypes
import time
import uuid
import weakref
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Final, Literal

from livekit import rtc
from livekit.agents.voice.io import TextOutput
from lkap_contracts.api_models import KbHit, SessionAssetOut
from lkap_contracts.blocks import UploadBlockConfig, accept_allows, safe_filename, sniff_mime
from lkap_contracts.tools import UPDATABLE_BLOCK_TYPES
from lkap_contracts.ui_agui import AguiPatchError, agui_delta_to_patch
from lkap_contracts.ui_protocol import (
    ACTIVITY_RING_SIZE,
    FORM_UPLOAD_KEY,
    FORM_WIDGET_KEY,
    RPC_AGENT_ACTION,
    RPC_UI_REQUEST,
    SNAPSHOT_EVERY_N_PATCHES,
    TOPIC_UI_ACTIVITY,
    TOPIC_UI_ASSET,
    TOPIC_UI_CAPTIONS,
    TOPIC_UI_STATE,
    TOPIC_UI_UPLOAD,
    ActivityEvent,
    AgentAction,
    AgentActionResult,
    AssetRef,
    BlockRequestPayload,
    BlockSpec,
    BlockSubmitPayload,
    CaptionSegment,
    CaptionSpeaker,
    ChecklistItem,
    FormBlockState,
    FormUploadSpec,
    KbCitation,
    LinkOutcome,
    Note,
    RequestableState,
    StateDeltaPayload,
    StatusStamp,
    Tone,
    UiPatch,
    UiPatchOp,
    UiRequest,
    UiRequestResult,
    UiSnapshot,
    UiState,
    UploadedFile,
    UploadRejection,
    UploadRejectReason,
)
from pydantic import BaseModel, ValidationError

from lkap_agent.config_client import AssetApi, AssetRejectedError, ConfigClient
from lkap_agent.logging import get_logger
from lkap_agent.ui.blocks import (
    ASSET_DOCUMENT_ID_KEY,
    BLOCK_STATE_MODELS,
    CARD_SELECT,
    LINK_OPENED,
    OPEN_CITATION,
    block_path,
    card_action_error,
    choice_selection_error,
    find_slot,
    initial_block_states,
    jsonable,
    open_citation,
    slot_selection_error,
    validate_block_state,
)

__all__ = [
    "ASSET_CACHE_BYTES",
    "BARGE_IN",
    "CAPTION_INTERIM_INTERVAL_S",
    "CaptionStream",
    "CaptionsTextOutput",
    "caption_tap_for",
    "register_caption_tap",
    "REQUEST_ACK_TIMEOUT_S",
    "STATE_DELTA_BLOCK_TYPES",
    "RequestMethod",
    "UiChannel",
    "rejection_message",
]

#: `Pack.on_ui_action(ctx, action, payload) -> payload` shape, bound by the caller.
OnUiAction = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
#: Attribute an avatar worker carries (`livekit.agents.types.ATTRIBUTE_PUBLISH_ON_BEHALF`).
_ATTRIBUTE_PUBLISH_ON_BEHALF = "lk.publish_on_behalf"
#: `set_video_source` payload handler: receives `"camera" | "screen" | "none"`.
OnSetVideoSource = Callable[[str], Awaitable[None]]
#: `block_action` handler: `(block_id, name, data) -> result payload`.
OnBlockAction = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]
#: `rewind`/`inject_user_text` handler: `(action, payload) -> result payload` (V2-18).
OnTextAction = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
#: Called with `(block_id, values)` for a `form_submit` / `block_submit` nobody is awaiting.
OnUnsolicitedForm = Callable[[str, dict[str, Any]], Awaitable[None]]
#: `SessionContext.record_event`-shaped session event sink.
RecordEvent = Callable[[str, dict[str, Any]], None]
#: The `lkap.ui.request` method a pending request was sent with: the generic
#: `request` (V5-02) or the legacy `form` alias.
RequestMethod = Literal["request", "form"]

#: `cancel_pending` reason for the caller speaking over a pending request
#: (`user_state == "speaking"`; the session wires it, V5-08).
BARGE_IN: Final[str] = "barge_in"

#: How long the `request` / `form` UI request may take to be answered. The
#: browser should acknowledge as soon as the request is visible; the answer
#: arrives separately via `block_submit` / `form_submit`, so this bounds only
#: the "show it" nudge. The receiving handler sees `responseTimeout` minus the
#: SDK's 7 s round-trip allowance (measured live: 10 s here arrived as 3 s in
#: the browser), so keep it well above 7 s.
REQUEST_ACK_TIMEOUT_S: Final[float] = 15.0
#: Response timeout for the fire-and-forget `show_block` UI request (same 7 s caveat).
SHOW_BLOCK_TIMEOUT_S: Final[float] = 10.0

#: How many bytes of recent session files the worker keeps in memory (older ones are
#: read back from the api when `describe_asset` needs them).
ASSET_CACHE_BYTES: Final[int] = 48 * 1024 * 1024
#: How many refused files an upload block lists (the newest).
_MAX_REJECTIONS: Final[int] = 10

#: The state fields a browser answer may write, per requestable block type (S5-3). A
#: `form` answer lands in `values` (its keys limited to the schema's properties); an
#: `upload` block's `files` are written by the worker only (V5-19); any other state
#: field (a consent block's `text`, `text_hash`, `method`, `at`, ...) is the agent's.
ANSWER_KEYS: Final[dict[str, frozenset[str]]] = {
    "consent": frozenset({"accepted"}),
    "choices": frozenset({"selected"}),
    # V5-43: the slot id only; its start and end come from the block's own slots.
    "slots": frozenset({"selected"}),
}
#: The block types a caller's `state_delta` (AG-UI) may write (V5-43): what `update_block`
#: may write, less `kb_citations` (a citation names a knowledge document the worker would
#: fetch) and `custom` (pack state packs may act on). Requestable blocks, links, consent,
#: uploads, captions and hand-offs are never written from the browser.
STATE_DELTA_BLOCK_TYPES: Final[frozenset[str]] = UPDATABLE_BLOCK_TYPES - {"kb_citations", "custom"}
#: Answer keys only the agent side (`submit_block`) may set: `via: "voice"` marks an answer
#: heard out loud, `turn_id` the user turn it was heard in (S5-3, S5-4). A browser's copy
#: is dropped.
AGENT_ONLY_ANSWER_KEYS: Final[frozenset[str]] = frozenset({"via", "turn_id"})


def rejection_message(reason: UploadRejectReason, *, max_bytes: int = 0, max_files: int = 0) -> str:
    """The line an upload block shows for a refused file (plain words, no jargon)."""
    match reason:
        case "too_large":
            return f"This file is too large. Send one up to {max(1, max_bytes // (1024 * 1024))} MB."
        case "type_not_allowed":
            return "This type of file can't be sent here. Send a photo or a PDF."
        case "too_many_files":
            return f"You can send up to {max_files} file{'s' if max_files != 1 else ''} here."
        case "empty":
            return "This file is empty."
        case "not_requested":
            return "Files can be sent here once the assistant asks for them."
        case _:
            return "This file could not be saved. Please try again."


@dataclass(slots=True, frozen=True)
class _UploadTarget:
    """Where a streamed file goes and the limits it must fit."""

    block_id: str
    field: str | None
    accept: list[str]
    max_files: int
    max_bytes: int


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _item_key(item: Any) -> Any:
    return getattr(item, "key", None) or getattr(item, "id", None)


def _upsert(items: list[Any], value: Any, key: str | None) -> None:
    """Match-by-`key`-or-`id` upsert semantics (docs/CONTRACTS.md §10)."""
    candidate = key if key is not None else _item_key(value)
    for idx, existing in enumerate(items):
        existing_key = _item_key(existing)
        if existing_key is not None and existing_key == candidate:
            items[idx] = value
            return
    items.append(value)


def _apply_list_op(items: list[Any], model_cls: type[BaseModel], op: UiPatchOp) -> None:
    """Apply `op` to a `UiState` list field, coercing `op.value` to `model_cls`.

    Mirrors `fakes.fake_ctx.FakeUiChannel`'s reference semantics exactly, so a
    pack/tool patching e.g. `/notes` with a plain dict behaves identically
    against the real `UiChannel` and the test fake.
    """
    if op.op == "append":
        items.append(model_cls.model_validate(op.value))
    elif op.op == "remove":
        items[:] = [i for i in items if _item_key(i) != op.key]
    elif op.op == "upsert":
        _upsert(items, model_cls.model_validate(op.value), op.key)
    elif op.op == "set":
        items[:] = [model_cls.model_validate(v) for v in (op.value or [])]
    else:  # pragma: no cover - defensive, UiPatchOp.op is a closed Literal
        raise ValueError(f"unknown UiPatchOp.op: {op.op!r}")


def _apply_custom_op(custom: dict[str, Any], segments: list[str], op: UiPatchOp) -> None:
    container = custom
    for key in segments[:-1]:
        container = container.setdefault(key, {})
    leaf = segments[-1]
    if op.op == "set":
        container[leaf] = op.value
    elif op.op == "remove":
        container.pop(leaf, None)
    elif op.op == "append":
        container.setdefault(leaf, []).append(op.value)
    elif op.op == "upsert":
        _upsert(container.setdefault(leaf, []), op.value, op.key)
    else:  # pragma: no cover - defensive
        raise ValueError(f"unknown UiPatchOp.op: {op.op!r}")


def _tree_item_matches(item: Any, key: str) -> bool:
    return isinstance(item, dict) and (item.get("key") == key or item.get("id") == key)


def _tree_upsert(items: list[Any], value: Any, key: str | None) -> None:
    candidate = key
    if candidate is None and isinstance(value, dict):
        candidate = value.get("key") or value.get("id")
    if candidate is not None:
        for idx, existing in enumerate(items):
            if _tree_item_matches(existing, candidate):
                items[idx] = value
                return
    items.append(value)


def _tree_child(container: Any, segment: str) -> Any:
    """Descend one segment, creating (or replacing a scalar with) a dict as the web reducer does."""
    if isinstance(container, list):
        if not segment.isdigit() or int(segment) >= len(container):
            return None
        index = int(segment)
        if not isinstance(container[index], dict | list):
            container[index] = {}
        return container[index]
    child = container.get(segment)
    if not isinstance(child, dict | list):
        child = container[segment] = {}
    return child


def _apply_tree_op(root: dict[str, Any], segments: list[str], op: UiPatchOp) -> None:
    """Generic JSON-tree op for `/blocks/...`, mirroring `web/src/lib/ui-state.ts::applyOp`.

    Values are stored as plain JSON (`jsonable`). `upsert` / keyed `remove`
    match list items by `key` or `id`; an unkeyed `remove` deletes the leaf.
    A numeric segment indexes into a list; an out-of-range index is a no-op,
    as in the browser.
    """
    container: Any = root
    for segment in segments[:-1]:
        container = _tree_child(container, segment)
        if container is None:
            return
    leaf = segments[-1]
    value = jsonable(op.value)
    if isinstance(container, list):
        if not leaf.isdigit() or int(leaf) >= len(container):
            return
        index = int(leaf)
        if op.op == "remove" and op.key is None:
            del container[index]
        elif op.op == "set":
            container[index] = value
        else:
            current = container[index]
            items = current if isinstance(current, list) else []
            _apply_tree_list_op(items, value, op)
            container[index] = items
        return

    if op.op == "set":
        container[leaf] = value
    elif op.op == "remove" and op.key is None:
        container.pop(leaf, None)
    else:
        current = container.get(leaf)
        items = current if isinstance(current, list) else []
        _apply_tree_list_op(items, value, op)
        container[leaf] = items


def _apply_tree_list_op(items: list[Any], value: Any, op: UiPatchOp) -> None:
    if op.op == "append":
        items.append(value)
    elif op.op == "upsert":
        _tree_upsert(items, value, op.key)
    elif op.op == "remove" and op.key is not None:
        items[:] = [i for i in items if not _tree_item_matches(i, op.key)]
    else:  # pragma: no cover - defensive, UiPatchOp.op is a closed Literal
        raise ValueError(f"unknown UiPatchOp.op: {op.op!r}")


def apply_patch_op(state: UiState, op: UiPatchOp) -> None:
    """Apply one `UiPatchOp` to `state` in place (docs/CONTRACTS.md §10 semantics).

    `/activity` is additionally trimmed to the last `ACTIVITY_RING_SIZE` entries
    after every mutation, per docs/ARCHITECTURE.md §9. `/blocks/...` (v2) is a
    plain JSON tree (see `_apply_tree_op`).
    """
    segments = _segments(op.path)
    if not segments:
        raise ValueError(f"empty UiPatchOp.path: {op.path!r}")
    top, rest = segments[0], segments[1:]

    if top == "blocks":
        if rest:
            _apply_tree_op(state.blocks, rest, op)
        elif op.op == "set":
            state.blocks = dict(jsonable(op.value) or {})
        else:  # pragma: no cover - defensive
            raise ValueError(f"unsupported op {op.op!r} on root /blocks")
        return

    if top == "custom":
        if rest:
            _apply_custom_op(state.custom, rest, op)
        elif op.op == "set":
            state.custom = dict(op.value or {})
        else:  # pragma: no cover - defensive
            raise ValueError(f"unsupported op {op.op!r} on root /custom")
        return

    if rest:  # pragma: no cover - defensive
        raise ValueError(f"nested paths are only supported under /custom: {op.path!r}")

    if top == "status":
        state.status = StatusStamp.model_validate(op.value) if op.value is not None else None
    elif top == "progress":
        state.progress = op.value
    elif top == "notes":
        _apply_list_op(state.notes, Note, op)
    elif top == "checklist":
        _apply_list_op(state.checklist, ChecklistItem, op)
    elif top == "assets":
        _apply_list_op(state.assets, AssetRef, op)
    elif top == "activity":
        _apply_list_op(state.activity, ActivityEvent, op)
        del state.activity[:-ACTIVITY_RING_SIZE]
    else:
        raise ValueError(f"unknown UiState field: {top!r}")


@dataclass(slots=True)
class _PendingRequest:
    """The one pending request of a block: its waiter and the method it was sent with."""

    future: asyncio.Future[dict[str, Any] | None]
    method: RequestMethod


class UiChannel:
    """Agent-side `lkap.ui.*` / `lkap.agent.action` handle for one session.

    Broadcasts (`lkap.ui.state`, `lkap.ui.activity`, `lkap.ui.asset`) go to
    every subscriber in the room (no `destination_identities`) since a
    session room has exactly one *browser* participant. `request_ui` (agent
    -> UI RPC) needs that participant's identity: pass `ui_identity` when an
    avatar provider (bey/tavus) is configured, since its `lk.publish_on_behalf`
    participant also joins the room as a second remote participant and would
    otherwise be picked up by the `room.remote_participants` fallback.

    The platform binds the v2 block callbacks after construction with
    :meth:`bind` and seeds the panel's blocks with :meth:`init_blocks`
    (`PlatformAgent` does both), so the worker's channel factory keeps its v1
    signature.
    """

    def __init__(
        self,
        room: rtc.Room,
        session_id: str,
        *,
        ui_identity: str | None = None,
        on_set_video_source: OnSetVideoSource | None = None,
        on_ui_action: OnUiAction | None = None,
        on_block_action: OnBlockAction | None = None,
        on_text_action: OnTextAction | None = None,
        record_event: RecordEvent | None = None,
        log: Any = None,
        asset_api: AssetApi | None = None,
    ) -> None:
        self.seq = 0
        self.state = UiState()
        self._room = room
        self._session_id = session_id
        self._ui_identity = ui_identity
        self._on_set_video_source = on_set_video_source
        self._on_ui_action = on_ui_action
        self._on_block_action = on_block_action
        self._on_text_action = on_text_action
        self._on_unsolicited_form: OnUnsolicitedForm | None = None
        self._record_event = record_event
        self._patches_since_snapshot = 0
        self._block_specs: dict[str, BlockSpec] = {}
        self._blocks_initialized = False
        self._pending: dict[str, _PendingRequest] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False
        self._log = log or get_logger(__name__).bind(session_id=session_id)
        # V5-19: the api's session-file routes, recent file bytes, and the files a form's
        # `file` fields received (so a submitted asset id is one this session really stored).
        self._asset_api = asset_api
        self._owned_asset_client: ConfigClient | None = None
        self._asset_api_unavailable = False
        self._asset_cache: OrderedDict[str, tuple[str, bytes]] = OrderedDict()
        self._asset_cache_bytes = 0
        self._form_uploads: dict[tuple[str, str], list[str]] = {}
        self._upload_handler_registered = False
        self._upload_lock = asyncio.Lock()
        # V5-43, ruling on ask #309: `state_delta` from the page is opt-in per agent.
        self._accept_state_delta = False
        self._state_delta_refusal_logged = False

    def start(self) -> None:
        """Register the `lkap.agent.action` RPC handler and the `lkap.ui.upload` byte-stream handler.

        The byte-stream registration is guarded: minimal room doubles do not
        implement it, and a room that already has an upload handler keeps it.
        """
        self._room.local_participant.register_rpc_method(RPC_AGENT_ACTION, self._handle_agent_action)
        register = getattr(self._room, "register_byte_stream_handler", None)
        if callable(register):
            try:
                register(TOPIC_UI_UPLOAD, self._on_upload_stream)
                self._upload_handler_registered = True
            except ValueError:
                self._log.warning("an lkap.ui.upload handler is already registered on this room")

    def close(self) -> None:
        """Unregister the handlers and release every pending request with `None`.

        The unregister is guarded because some minimal test doubles (e.g.
        `fakes.fake_room.FakeRoom`) implement `register_rpc_method` but not the
        (rarely needed, per-session-room) `unregister_rpc_method`.
        """
        self._closed = True
        for entry in self._pending.values():
            if not entry.future.done():
                entry.future.set_result(None)
        self._pending.clear()
        for task in list(self._tasks):
            task.cancel()
        unregister = getattr(self._room.local_participant, "unregister_rpc_method", None)
        if unregister is not None:
            unregister(RPC_AGENT_ACTION)
        if self._upload_handler_registered:
            unregister_stream = getattr(self._room, "unregister_byte_stream_handler", None)
            if callable(unregister_stream):
                unregister_stream(TOPIC_UI_UPLOAD)
            self._upload_handler_registered = False
        self._asset_cache.clear()
        self._asset_cache_bytes = 0
        client, self._owned_asset_client = self._owned_asset_client, None
        if client is not None:
            try:
                asyncio.get_running_loop().create_task(client.aclose())
            except RuntimeError:
                pass  # no loop left: the process is exiting and the pool goes with it

    # --- platform wiring (not part of packs.base.UiChannel) ----------------------

    def bind(
        self,
        *,
        on_block_action: OnBlockAction | None = None,
        on_unsolicited_form: OnUnsolicitedForm | None = None,
        on_text_action: OnTextAction | None = None,
        record_event: RecordEvent | None = None,
        asset_api: AssetApi | None = None,
        accept_state_delta: bool | None = None,
    ) -> None:
        """Attach the platform callbacks for block actions, late form submissions and events.

        Only the arguments that are not `None` replace the current callbacks.
        `on_text_action` (V2-18) handles `rewind`/`inject_user_text`; `main.py`
        binds it only for `channel="text"` sessions, before `PlatformAgent`
        binds the block callbacks, so calling `bind` twice never clobbers it.
        `asset_api` (V5-19) is the worker's api client for session files; without
        one the channel builds its own from the worker settings on first use.
        """
        if on_block_action is not None:
            self._on_block_action = on_block_action
        if on_unsolicited_form is not None:
            self._on_unsolicited_form = on_unsolicited_form
        if on_text_action is not None:
            self._on_text_action = on_text_action
        if record_event is not None:
            self._record_event = record_event
        if asset_api is not None:
            self._asset_api = asset_api
        if accept_state_delta is not None:
            # `PanelLayout.accept_state_delta` (V5-43, ask #309): off unless the builder turns it on.
            self._accept_state_delta = accept_state_delta

    def init_blocks(self, specs: Iterable[BlockSpec]) -> None:
        """Seed `state.blocks` with the panel's empty block states (once per session).

        Runs before the first snapshot, so the seq-1 snapshot already carries
        the blocks (D-W2-9a). A second call (e.g. a flow handing off to another
        `PlatformAgent`) keeps the live block states and only learns new specs.
        """
        specs = list(specs)
        for spec in specs:
            self._block_specs.setdefault(spec.id, spec)
        if self._blocks_initialized:
            for block_id, state in initial_block_states(specs).items():
                self.state.blocks.setdefault(block_id, state)
            return
        self._blocks_initialized = True
        self.state.blocks = {**initial_block_states(specs), **self.state.blocks}

    @property
    def block_specs(self) -> dict[str, BlockSpec]:
        """The panel's block specs by id (a copy)."""
        return dict(self._block_specs)

    # --- packs.base.UiChannel -------------------------------------------------

    async def patch(self, ops: list[UiPatchOp]) -> None:
        """Send a `UiPatch` with `ops`, applying them to `state` and bumping `seq`."""
        ops = [_normalize_block_op(op) for op in ops]
        for op in ops:
            apply_patch_op(self.state, op)
        self.seq += 1
        message = UiPatch(seq=self.seq, session_id=self._session_id, ops=ops)
        await self._room.local_participant.send_text(message.model_dump_json(), topic=TOPIC_UI_STATE)
        self._log.debug("ui_patch_sent", seq=self.seq, op_count=len(ops))
        self._patches_since_snapshot += 1
        if self._patches_since_snapshot >= SNAPSHOT_EVERY_N_PATCHES:
            await self.snapshot()

    async def snapshot(self) -> None:
        """Send a full `UiSnapshot` of the current `state`."""
        if self.seq == 0:
            self.seq = 1
        message = UiSnapshot(seq=self.seq, session_id=self._session_id, state=self.state)
        await self._room.local_participant.send_text(message.model_dump_json(), topic=TOPIC_UI_STATE)
        self._patches_since_snapshot = 0
        self._log.debug("ui_snapshot_sent", seq=self.seq)

    async def set_status(self, label: str, tone: Tone) -> None:
        """Patch `/status` to `StatusStamp(label=label, tone=tone)`."""
        await self.patch([UiPatchOp(op="set", path="/status", value=StatusStamp(label=label, tone=tone))])

    async def add_note(self, text: str, kind: str = "note", key: str | None = None) -> None:
        """Append (or, with `key`, upsert) a `Note` onto `/notes`."""
        note = Note(id=str(uuid.uuid4()), text=text, kind=kind, ts=time.time(), key=key)
        op: Any = "upsert" if key else "append"
        await self.patch([UiPatchOp(op=op, path="/notes", value=note, key=key)])

    async def set_checklist(self, items: list[ChecklistItem]) -> None:
        """Replace `/checklist`."""
        await self.patch([UiPatchOp(op="set", path="/checklist", value=list(items))])

    async def push_asset(
        self,
        data: bytes,
        mime: str,
        kind: str,
        caption: str | None = None,
        meta: dict[str, str] | None = None,
    ) -> str:
        """Stream `data` on `lkap.ui.asset`, then patch the matching `AssetRef` onto
        `/assets`. Returns the new `asset_id`.

        v2: an image is also appended to every `gallery` block's `asset_ids`,
        in the same patch, so a pinned frame shows up in the gallery.
        """
        return await self._publish_asset(data, mime, kind=kind, caption=caption, meta=meta)

    async def _publish_asset(
        self,
        data: bytes,
        mime: str,
        *,
        kind: str,
        caption: str | None = None,
        meta: dict[str, str] | None = None,
        asset_id: str | None = None,
        stored: bool = False,
        name: str | None = None,
        extra_ops: Iterable[UiPatchOp] = (),
    ) -> str:
        """Stream `data` on `lkap.ui.asset` and patch its `AssetRef` (plus `extra_ops`) in one patch.

        `asset_id` is the stored asset's id for a file the api holds (V5-19),
        else a fresh one. Images also join every gallery block.
        """
        asset_id = asset_id or str(uuid.uuid4())
        ext = (mimetypes.guess_extension(mime) or "").lstrip(".") or "bin"
        attributes = {
            "asset_id": asset_id,
            "kind": kind,
            "mime": mime,
            "session_id": self._session_id,
        }
        if caption is not None:
            attributes["caption"] = caption

        writer = await self._room.local_participant.stream_bytes(
            f"{asset_id}.{ext}", mime_type=mime, attributes=attributes, topic=TOPIC_UI_ASSET
        )
        await writer.write(data)
        await writer.aclose()
        self._log.debug("ui_asset_pushed", asset_id=asset_id, kind=kind, mime=mime, size=len(data))
        self._remember(asset_id, mime, data)

        extra: dict[str, Any] = {"stored": True, "name": name, "size": len(data)} if stored else {}
        ref = AssetRef(
            asset_id=asset_id, kind=kind, mime=mime, caption=caption, meta=meta or {}, ts=time.time(), **extra
        )
        ops = [UiPatchOp(op="append", path="/assets", value=ref)]
        if mime.startswith("image/"):
            ops.extend(
                UiPatchOp(op="append", path=block_path(block_id, "asset_ids"), value=asset_id)
                for block_id, spec in self._block_specs.items()
                if spec.type == "gallery" and block_id in self.state.blocks
            )
        ops.extend(extra_ops)
        await self.patch(ops)
        return asset_id

    # --- session files (V5-19) --------------------------------------------------

    def _assets(self) -> AssetApi | None:
        """The api's session-file routes: the bound client, else one built from the worker settings."""
        if self._asset_api is not None:
            return self._asset_api
        if self._asset_api_unavailable:
            return None
        try:
            from lkap_agent.settings import get_settings  # noqa: PLC0415 - only when first needed

            settings = get_settings()
            client = ConfigClient(settings.api_base_url, settings.service_token)
        except Exception:  # noqa: BLE001 - no api settings: files are refused, never crash the call
            self._asset_api_unavailable = True
            self._log.warning("session files cannot be stored: no api client is available")
            return None
        self._asset_api = self._owned_asset_client = client
        return client

    def _remember(self, asset_id: str, mime: str, data: bytes) -> None:
        """Keep a file's bytes for `describe_asset`, evicting the oldest past `ASSET_CACHE_BYTES`."""
        if len(data) > ASSET_CACHE_BYTES:
            return
        previous = self._asset_cache.pop(asset_id, None)
        if previous is not None:
            self._asset_cache_bytes -= len(previous[1])
        self._asset_cache[asset_id] = (mime, data)
        self._asset_cache_bytes += len(data)
        while self._asset_cache_bytes > ASSET_CACHE_BYTES and self._asset_cache:
            _, (_, evicted) = self._asset_cache.popitem(last=False)
            self._asset_cache_bytes -= len(evicted)

    async def asset_bytes(self, asset_id: str) -> tuple[bytes, str] | None:
        """The bytes and type of an asset of this session: from memory, else (stored ones) the api.

        Returns:
            `(data, mime)`, or `None` when the asset is unknown to this session or gone.
        """
        cached = self._asset_cache.get(asset_id)
        if cached is not None:
            self._asset_cache.move_to_end(asset_id)
            return cached[1], cached[0]
        ref = next((a for a in self.state.assets if a.asset_id == asset_id), None)
        api = self._assets() if ref is not None and ref.stored else None
        if ref is None or api is None:
            return None
        try:
            data = await api.asset_content(self._session_id, asset_id)
        except AssetRejectedError as exc:
            self._log.debug("session file could not be read back", asset_id=asset_id, status=exc.status)
            return None
        self._remember(asset_id, ref.mime, data)
        return data, ref.mime

    async def store_asset(
        self,
        data: bytes,
        mime: str,
        kind: str,
        caption: str | None = None,
        meta: dict[str, str] | None = None,
        *,
        store_kind: Literal["frame", "signature"] = "frame",
    ) -> str:
        """Store a file the agent made (a pinned frame) through the api, then show it (V5-19).

        Best effort: when the api refuses or is unreachable the file is shown
        exactly as `push_asset` always did (not stored), so the panel never
        loses the picture.

        Returns:
            The asset id (the stored id when the api kept it).
        """
        api = self._assets()
        stored: SessionAssetOut | None = None
        if api is not None:
            try:
                stored = await api.post_asset(
                    self._session_id,
                    data,
                    name=f"{kind}{mimetypes.guess_extension(mime) or ''}",
                    mime=mime,
                    kind=store_kind,
                    meta={k: v for k, v in (meta or {}).items() if k in ("source", "caption")},
                )
            except AssetRejectedError as exc:
                self._log.warning("a pinned file was not stored", status=exc.status)
        if stored is None:
            return await self._publish_asset(data, mime, kind=kind, caption=caption, meta=meta)
        return await self._publish_asset(
            data,
            stored.mime,
            kind=kind,
            caption=caption,
            meta=meta,
            asset_id=stored.id,
            stored=True,
            name=stored.name,
        )

    async def asset_from_document(self, document_id: str) -> tuple[str | None, str | None]:
        """Make sure the session holds a cited KB document, copying it in on first use (R-V5-5).

        Returns:
            `(asset_id, None)` when the session holds it (now or already), else
            `(None, reason)`: `no_preview` for a document a citation cannot show,
            `no_source` when it is not one of the agent's or the api is unavailable.
        """
        held = next(
            (
                a.asset_id
                for a in reversed(self.state.assets)
                if a.meta.get(ASSET_DOCUMENT_ID_KEY) == document_id
            ),
            None,
        )
        if held is not None:
            return held, None
        api = self._assets()
        if api is None:
            return None, "no_source"
        try:
            stored = await api.asset_from_document(self._session_id, document_id)
            data = await api.asset_content(self._session_id, stored.id)
        except AssetRejectedError as exc:
            self._log.debug("cited document not copied", document_id=document_id, status=exc.status)
            return None, ("no_preview" if exc.status == 415 else "no_source")
        asset_id = await self._publish_asset(
            data,
            stored.mime,
            kind="document",
            caption=stored.name,
            meta={ASSET_DOCUMENT_ID_KEY: document_id},
            asset_id=stored.id,
            stored=True,
            name=stored.name,
        )
        return asset_id, None

    def _on_upload_stream(self, reader: Any, participant_identity: str) -> None:
        """`lkap.ui.upload` byte-stream handler (synchronous, as the SDK calls it)."""
        self._spawn(self.receive_upload(reader, participant_identity))

    def _is_caller(self, identity: str) -> bool:
        """Whether `identity` is the session's caller (never an avatar or another agent)."""
        try:
            return identity == self._remote_identity()
        except RuntimeError:
            return False

    def _upload_target(self, block_id: str, field: str | None) -> _UploadTarget | None:
        """The requested `upload` block (or form `file` field) a stream may fill, with its limits."""
        state = self.state.blocks.get(block_id) if block_id else None
        if not isinstance(state, dict) or state.get("status") != "requested":
            return None
        block_type = self._block_type(block_id)
        if block_type == "upload":
            try:
                config = UploadBlockConfig.model_validate(self._block_specs[block_id].config)
            except ValidationError:
                config = UploadBlockConfig()
            return _UploadTarget(block_id, None, list(config.accept), config.max_files, config.max_bytes)
        if block_type == "form" and field:
            schema = state.get("schema")
            properties = schema.get("properties") if isinstance(schema, dict) else None
            prop = properties.get(field) if isinstance(properties, dict) else None
            if isinstance(prop, dict) and prop.get(FORM_WIDGET_KEY) == "file":
                try:
                    spec = FormUploadSpec.model_validate(prop.get(FORM_UPLOAD_KEY) or {})
                except ValidationError:
                    spec = FormUploadSpec()
                return _UploadTarget(block_id, field, list(spec.accept), spec.max_files, spec.max_bytes)
        return None

    def _received(self, target: _UploadTarget) -> int:
        if target.field is not None:
            return len(self._form_uploads.get((target.block_id, target.field), []))
        files = (self.state.blocks.get(target.block_id) or {}).get("files")
        return len(files) if isinstance(files, list) else 0

    async def _reject_upload(
        self, block_id: str, name: str, reason: UploadRejectReason, *, target: _UploadTarget | None = None
    ) -> None:
        """Show why a file was refused (on an `upload` block) and record it (no filename in the event)."""
        if self._block_type(block_id) == "upload" and block_id in self.state.blocks:
            rejection = UploadRejection(
                name=name,
                reason=reason,
                message=rejection_message(
                    reason,
                    max_bytes=target.max_bytes if target else 0,
                    max_files=target.max_files if target else 0,
                ),
            )
            earlier = (self.state.blocks.get(block_id) or {}).get("rejected")
            kept = earlier[-(_MAX_REJECTIONS - 1) :] if isinstance(earlier, list) else []
            await self.patch(
                [
                    # The last few refusals only: a flood of bad files cannot grow the state.
                    UiPatchOp(op="set", path=block_path(block_id, "rejected"), value=[*kept, rejection]),
                    UiPatchOp(op="set", path=block_path(block_id, "progress"), value=None),
                ]
            )
        self._record("block_update", {"block_id": block_id, "op": "file_rejected", "reason": reason})

    async def _read_upload(self, reader: Any, target: _UploadTarget, declared: int) -> bytes | None:
        """Read the stream, stopping (`None`) as soon as it passes `max_bytes`; progress in quarters."""
        chunks: list[bytes] = []
        total = 0
        quarter = 0
        async for chunk in reader:
            total += len(chunk)
            if total > target.max_bytes:
                return None
            chunks.append(chunk)
            if target.field is None and declared > 0 and (reached := min(3, total * 4 // declared)) > quarter:
                quarter = reached
                await self.patch(
                    [UiPatchOp(op="set", path=block_path(target.block_id, "progress"), value=quarter / 4)]
                )
        return b"".join(chunks)

    async def receive_upload(self, reader: Any, sender_identity: str) -> UploadedFile | None:
        """Take one file from `lkap.ui.upload`, check it, store it through the api and show it.

        Refused, with a `rejected` row on an upload block: a stream for a block
        that is not asking (`not_requested`), one file too many, a declared or
        actual size over `max_bytes` (the declared size is checked before a
        byte is read), an empty file, bytes that are not an allowed type
        (sniffed, whatever the name or declared type says), and anything the api
        refuses. A stream from anyone but the caller is dropped silently.

        Returns:
            The stored file, or `None` when it was refused.
        """
        info = getattr(reader, "info", None)
        attributes = dict(getattr(info, "attributes", None) or {})
        block_id = str(attributes.get("block_id") or "")
        field = str(attributes.get("field") or "") or None
        name = safe_filename(attributes.get("name") or getattr(info, "name", None))
        declared = int(getattr(info, "size", 0) or 0)
        try:
            if not self._is_caller(sender_identity):
                self._log.debug("an upload from a participant other than the caller was dropped")
                return None
            # One file at a time: parallel streams can never slip past `max_files` together.
            async with self._upload_lock:
                return await self._check_and_store(reader, block_id, field, name, declared)
        finally:
            close = getattr(reader, "close", None)
            if callable(close):
                close()

    async def _check_and_store(
        self, reader: Any, block_id: str, field: str | None, name: str, declared: int
    ) -> UploadedFile | None:
        target = self._upload_target(block_id, field)
        if target is None:
            await self._reject_upload(block_id, name, "not_requested")
            return None
        if self._received(target) >= target.max_files:
            await self._reject_upload(block_id, name, "too_many_files", target=target)
            return None
        if declared > target.max_bytes:
            await self._reject_upload(block_id, name, "too_large", target=target)
            return None
        try:
            data = await self._read_upload(reader, target, declared)
        except Exception:  # noqa: BLE001 - an aborted stream (rtc.StreamError) is a failed upload
            await self._reject_upload(block_id, name, "failed", target=target)
            return None
        if data is None:
            await self._reject_upload(block_id, name, "too_large", target=target)
            return None
        if declared > 0 and len(data) < declared:
            # S5-46: a cancel mid-send still closes the stream cleanly; a file shorter than the
            # size the browser declared (`ByteStreamInfo.size`, the stream's `total_length`) is
            # a truncated file, never stored. No declared size keeps the old behaviour.
            self._log.debug("upload shorter than its declared size", received=len(data), declared=declared)
            await self._reject_upload(block_id, name, "failed", target=target)
            return None
        if not data:
            await self._reject_upload(block_id, name, "empty", target=target)
            return None
        mime = sniff_mime(data)
        if mime is None or not accept_allows(target.accept, mime):
            await self._reject_upload(block_id, name, "type_not_allowed", target=target)
            return None
        return await self._store_upload(target, name, mime, data)

    async def _store_upload(
        self, target: _UploadTarget, name: str, mime: str, data: bytes
    ) -> UploadedFile | None:
        api = self._assets()
        if api is None:
            await self._reject_upload(target.block_id, name, "failed", target=target)
            return None
        meta = {"block_id": target.block_id, **({"field": target.field} if target.field else {})}
        try:
            stored = await api.post_asset(
                self._session_id, data, name=name, mime=mime, kind="upload", meta=meta
            )
        except AssetRejectedError as exc:
            reasons: dict[int, UploadRejectReason] = {
                413: "too_large",
                415: "type_not_allowed",
                409: "too_many_files",
            }
            reason: UploadRejectReason = reasons.get(exc.status, "failed")
            await self._reject_upload(target.block_id, name, reason, target=target)
            return None
        file = UploadedFile(
            asset_id=stored.id, name=stored.name, mime=stored.mime, size=stored.size, sha256=stored.sha256
        )
        ops: list[UiPatchOp] = []
        if target.field is None:
            ops = [
                UiPatchOp(op="append", path=block_path(target.block_id, "files"), value=file),
                UiPatchOp(op="set", path=block_path(target.block_id, "progress"), value=None),
            ]
        else:
            self._form_uploads.setdefault((target.block_id, target.field), []).append(stored.id)
        await self._publish_asset(
            data,
            stored.mime,
            kind="upload",
            caption=stored.name,
            meta=meta,
            asset_id=stored.id,
            stored=True,
            name=stored.name,
            extra_ops=ops,
        )
        self._record(
            "block_update",
            {
                "block_id": target.block_id,
                "block_type": self._block_type(target.block_id),
                "op": "file_received",
                "asset_id": stored.id,
                "mime": stored.mime,
                "size": stored.size,
            },
        )
        return file

    def _verified_form_values(self, block_id: str, values: dict[str, Any]) -> dict[str, Any]:
        """A form answer limited to the schema's fields, whose `file` fields keep only stored asset ids.

        S5-3: a key the form's schema does not declare is dropped (a schema
        without `properties` keeps the answer as is). V5-19: a `file` field
        keeps only the asset ids this session stored for that field.
        """
        state = self.state.blocks.get(block_id)
        schema = state.get("schema") if isinstance(state, dict) else None
        properties = schema.get("properties") if isinstance(schema, dict) else None
        if not isinstance(properties, dict) or not properties:
            return values
        cleaned = {key: value for key, value in values.items() if key in properties}
        for field, prop in properties.items():
            if not (isinstance(prop, dict) and prop.get(FORM_WIDGET_KEY) == "file") or field not in cleaned:
                continue
            received = self._form_uploads.get((block_id, str(field)), [])
            submitted = cleaned[field] if isinstance(cleaned[field], list) else []
            cleaned[field] = [asset_id for asset_id in submitted if asset_id in received]
        return cleaned

    async def activity(self, event: ActivityEvent) -> None:
        """Record an `ActivityEvent`: broadcast on `lkap.ui.activity` *and* upsert it
        into `state.activity` (same `id` replaces an earlier entry; last 30 kept) so
        late joiners/reconnects see it via a snapshot too (docs/CONTRACTS.md §10).
        """
        await self._room.local_participant.send_text(event.model_dump_json(), topic=TOPIC_UI_ACTIVITY)
        await self.patch([UiPatchOp(op="upsert", path="/activity", value=event, key=event.id)])

    async def caption(self, segment: CaptionSegment) -> None:
        """Send one live caption on `lkap.captions` (V5-31); never stored in the state."""
        await self._room.local_participant.send_text(segment.model_dump_json(), topic=TOPIC_UI_CAPTIONS)

    async def request_ui(
        self, method: str, payload: dict[str, Any], *, response_timeout: float | None = None
    ) -> dict[str, Any]:
        """RPC `lkap.ui.request` (agent -> UI); returns the UI's response payload.

        Args:
            method: A `UiRequest.method`.
            payload: The method's payload (keys fixed by R-V2-3b / CONTRACTS-V2 §4.4).
            response_timeout: Seconds to wait for the browser's answer; `None`
                keeps the SDK default.
        """
        request = UiRequest.model_validate({"method": method, "payload": payload})
        identity = self._remote_identity()
        kwargs: dict[str, Any] = {}
        if response_timeout is not None:
            kwargs["response_timeout"] = response_timeout
        response_payload = await self._room.local_participant.perform_rpc(
            destination_identity=identity,
            method=RPC_UI_REQUEST,
            payload=request.model_dump_json(),
            **kwargs,
        )
        result = UiRequestResult.model_validate_json(response_payload)
        return result.payload

    async def set_block(self, block_id: str, state: dict[str, Any]) -> None:
        """Replace block `block_id`'s state (`set /blocks/<block_id>`).

        Raises:
            pydantic.ValidationError: When the block is in the layout and
                `state` does not fit its type's state model.
        """
        value = validate_block_state(self._block_type(block_id), state)
        await self.patch([UiPatchOp(op="set", path=block_path(block_id), value=value)])
        self._record(
            "block_update", {"block_id": block_id, "block_type": self._block_type(block_id), "op": "set"}
        )

    async def patch_block(self, block_id: str, ops: list[UiPatchOp]) -> None:
        """Apply `ops` (paths relative to the block) to block `block_id` in one `UiPatch`.

        The result is validated against the block type's model before
        anything is sent; an invalid result raises and leaves state untouched.

        Raises:
            pydantic.ValidationError: When the patched state no longer fits
                the block type.
        """
        if not ops:
            return
        absolute = self._validated_block_ops(block_id, ops)
        await self.patch(absolute)
        self._record(
            "block_update", {"block_id": block_id, "block_type": self._block_type(block_id), "op": "patch"}
        )

    async def request_block(
        self,
        block_id: str,
        *,
        timeout_s: float,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Ask the user to answer requestable block `block_id` and wait (V5-02).

        The caller writes the block's content first (options, schema, prompt:
        whatever its type renders); this method only drives the request:

        1. Patch `/blocks/<id>/status = "requested"` and `/submitted_at =
           null`, validated against the block type. A reconnecting browser
           renders the pending request from the snapshot alone.
        2. RPC `lkap.ui.request {method: "request", payload: {block_id,
           timeout_s, **payload}}` with a short response timeout
           (`BlockRequestPayload`; `payload` may carry `schema` and any other
           key the block type defines). The browser acks at once; an inline
           `{values}` or `{cancelled: true}` answer is accepted too, and an
           RPC failure is logged while the wait continues.
        3. `lkap.agent.action {action: "block_submit", payload: {block_id,
           values} | {block_id, cancelled: true}}` resolves the request.

        Released with `None` (status `cancelled`) on a user cancel, a timeout
        and `cancel_pending`; released with `None` (no status change: the new
        request owns the block) by a second request on the same block; and
        with `None` on session close. Realtime pipelines run the wait on the
        session's `BackgroundRunner` and deliver an answer as an urgent
        background result, as `request_form` does (the calling tool's job).

        Args:
            block_id: The requestable block (`RequestableState`, or a `custom`
                or unknown block, whose state is not typed).
            timeout_s: Seconds the user has to answer.
            payload: Extra `BlockRequestPayload` keys for the browser.

        Returns:
            The submitted values, or `None` on cancel, timeout, barge-in, a
            newer request or session close.

        Raises:
            ValueError: When `block_id` is a known block type that cannot be
                requested (e.g. a `table`).
            pydantic.ValidationError: When `timeout_s` or `payload` does not
                fit `BlockRequestPayload`.
        """
        if not self._is_requestable(block_id):
            raise ValueError(f"block {block_id!r} ({self._block_type(block_id)}) cannot be requested")
        rpc_payload = BlockRequestPayload.model_validate(
            {**(payload or {}), "block_id": block_id, "timeout_s": timeout_s}
        ).model_dump(mode="json", by_alias=True, exclude_none=True)
        ops = self._validated_block_ops(
            block_id,
            [
                UiPatchOp(op="set", path="status", value="requested"),
                UiPatchOp(op="set", path="submitted_at", value=None),
            ],
        )
        return await self._await_request(
            block_id, method="request", ops=ops, rpc_payload=rpc_payload, timeout_s=timeout_s
        )

    async def request_form(
        self,
        block_id: str,
        schema: dict[str, Any],
        prefill: dict[str, Any] | None = None,
        timeout_s: float = 120,
    ) -> dict[str, Any] | None:
        """Show a JSON-schema form in block `block_id` and wait for the user.

        Deprecated alias of `request_block` (V5-02), kept for one release with
        the v2 wire and statuses unchanged; no runtime warning. It runs on the
        same pending-request machinery, so `block_submit`, `cancel_pending` and
        `close` release it too.

        1. `set /blocks/<id>` = `FormBlockState{schema, values: prefill,
           status: "requested", submitted_at: null}`.
        2. RPC `lkap.ui.request {method: "form", payload: {block_id, schema,
           prefill}}` with a short response timeout. An answer carrying
           `values` resolves the form; `{cancelled: true}` returns `None`; any
           other answer or an RPC error is logged and the wait continues (the
           form is already visible from the block state).
        3. `lkap.agent.action {action: "form_submit" | "block_submit",
           payload: {block_id, values}}` resolves the form (see `_accept_form`).

        Unlike the generic request, a cancel resets the block to `idle` and a
        timeout keeps `status: "requested"`, so a late submission still lands
        in state and reaches the agent through the unsolicited-form callback.

        Returns:
            The submitted values, or `None` on cancel, timeout or session close.
        """
        prefill_values = dict(prefill or {})
        # V5-19: a new form starts with no received files for its `file` fields.
        for key in [k for k in self._form_uploads if k[0] == block_id]:
            del self._form_uploads[key]
        state = FormBlockState.model_validate(
            {"schema": schema, "values": prefill_values, "status": "requested"}
        ).model_dump(mode="json", by_alias=True)
        return await self._await_request(
            block_id,
            method="form",
            ops=[UiPatchOp(op="set", path=block_path(block_id), value=state)],
            rpc_payload={"block_id": block_id, "schema": schema, "prefill": prefill_values},
            timeout_s=timeout_s,
        )

    @property
    def pending_requests(self) -> dict[str, RequestMethod]:
        """Block id -> method of every request still waiting for the user.

        The session reads it to decide whether a barge-in has anything to
        cancel (V5-08 wires `cancel_pending(BARGE_IN)` on `user_state ==
        "speaking"`).
        """
        return {block_id: e.method for block_id, e in self._pending.items() if not e.future.done()}

    def cancel_pending(self, reason: str, *, methods: Iterable[RequestMethod] | None = None) -> list[str]:
        """Release every pending request with `None` (e.g. `reason=BARGE_IN`).

        Synchronous, so a `user_state_changed` handler can call it directly:
        the waiters are released at once and the state patches (`cancelled`
        for `request`, `idle` for the legacy `form`) follow on a task, skipped
        for a block a newer request has claimed in the meantime.

        Args:
            reason: Why, recorded on the session event (`barge_in`, ...).
            methods: Only release requests sent with these methods; `None`
                releases all of them.

        Returns:
            The ids of the blocks whose request was released.
        """
        wanted = set(methods) if methods is not None else None
        released: list[str] = []
        for block_id, entry in list(self._pending.items()):
            if entry.future.done() or (wanted is not None and entry.method not in wanted):
                continue
            entry.future.set_result(None)
            released.append(block_id)
            self._spawn(self._mark_cancelled(block_id, entry.method, reason))
        if released:
            self._log.debug("ui_requests_cancelled", block_ids=released, reason=reason)
        return released

    async def submit_block(self, block_id: str, values: dict[str, Any]) -> bool:
        """Answer a requestable block from the agent side, exactly as a `block_submit` would (V5-08).

        For an answer the caller gave some other way, e.g. by voice
        (`resolve_choice`): the block is marked `submitted` and a pending
        request resolves with `values`. With nothing pending, the block is
        still marked submitted but the unsolicited-submit callback is **not**
        called (the model already knows the answer: it gave it).

        Args:
            block_id: The block.
            values: The answer, in the shape a browser would submit.

        Returns:
            Whether a pending request was resolved.

        Raises:
            ValueError: When the block is unknown or cannot be requested.
        """
        if block_id not in self.state.blocks and block_id not in self._block_specs:
            raise ValueError(f"unknown block: {block_id!r}")
        if not self._is_requestable(block_id):
            raise ValueError(f"block {block_id!r} ({self._block_type(block_id)}) cannot be submitted")
        entry = self._pending.get(block_id)
        waiting = entry is not None and not entry.future.done()
        if entry is not None and entry.method == "form":
            await self._accept_form(block_id, values, notify=False)
        else:
            await self._accept_request(block_id, values, notify=False)
        return waiting

    async def _await_request(
        self,
        block_id: str,
        *,
        method: RequestMethod,
        ops: list[UiPatchOp],
        rpc_payload: dict[str, Any],
        timeout_s: float,
    ) -> dict[str, Any] | None:
        """Claim the block's pending slot, publish the request and wait for the answer."""
        if self._closed:
            return None
        previous = self._pending.pop(block_id, None)
        if previous is not None and not previous.future.done():
            previous.future.set_result(None)
        future: asyncio.Future[dict[str, Any] | None] = asyncio.get_running_loop().create_future()
        entry = _PendingRequest(future=future, method=method)
        self._pending[block_id] = entry
        try:
            await self.patch(ops)
            self._record(
                "block_update",
                {
                    "block_id": block_id,
                    "block_type": "form" if method == "form" else self._block_type(block_id),
                    "op": "form_requested" if method == "form" else "block_requested",
                },
            )
            self._spawn(self._send_request(block_id, method, rpc_payload, future))
            return await asyncio.wait_for(future, timeout=timeout_s)
        except TimeoutError:
            self._log.debug("ui_request_timed_out", block_id=block_id, method=method, timeout_s=timeout_s)
            if method == "request" and self._pending.get(block_id) is entry:
                await self._mark_cancelled(block_id, method, "timeout")
            return None
        finally:
            if self._pending.get(block_id) is entry:
                del self._pending[block_id]

    async def cite(self, block_id: str, hits: list[KbHit]) -> None:
        """Replace `/blocks/<block_id>/items` with `hits` as `KbCitation`s.

        Each citation carries the hit's `document_id` and, when the hit has
        them in its `meta` (V5-01 ingest locators, surfaced by V5-04), the
        `page`, `heading_path` and character offsets (V5-08).
        """
        # Unset locators stay off the wire, so a pre-V5-01 chunk's citation keeps its v2 shape.
        items = [_citation_of(h).model_dump(mode="json", exclude_none=True) for h in hits]
        await self.patch([UiPatchOp(op="set", path=block_path(block_id, "items"), value=items)])
        self._record("block_update", {"block_id": block_id, "block_type": "kb_citations", "op": "cite"})

    def show_block(self, block_id: str) -> None:
        """Ask the browser to bring block `block_id` into view (fire and forget)."""

        async def _show() -> None:
            try:
                await self.request_ui(
                    "show_block", {"block_id": block_id}, response_timeout=SHOW_BLOCK_TIMEOUT_S
                )
            except Exception as exc:  # noqa: BLE001 - a missed scroll is never worth an error
                self._log.debug("show_block_failed", block_id=block_id, error=str(exc))

        self._spawn(_show())

    # --- lkap.agent.action dispatch (UI -> agent) ------------------------------

    async def _handle_agent_action(self, data: rtc.RpcInvocationData) -> str:
        if not self._is_caller(data.caller_identity):
            # S5-23: only the session's caller answers blocks or drives the panel (an avatar
            # worker or anyone else in the room is dropped), as uploads already are.
            self._log.debug("agent_action_dropped: not the caller", caller_identity=data.caller_identity)
            return AgentActionResult(ok=False, error="not the session's caller").model_dump_json()
        try:
            action = AgentAction.model_validate_json(data.payload)
            result = await self._dispatch_agent_action(action)
        except Exception as exc:  # noqa: BLE001 - forwarded to the caller as a typed error
            self._log.debug("agent_action_failed", error=str(exc))
            result = AgentActionResult(ok=False, error=str(exc))
        return result.model_dump_json()

    async def _dispatch_agent_action(self, action: AgentAction) -> AgentActionResult:
        self._log.debug("agent_action_received", action=action.action)
        if action.action == "get_snapshot":
            await self.snapshot()
            return AgentActionResult(ok=True)

        if action.action == "set_video_source":
            if self._on_set_video_source is None:
                return AgentActionResult(ok=False, error="set_video_source is not supported")
            source = str(action.payload.get("source", "none"))
            await self._on_set_video_source(source)
            return AgentActionResult(ok=True)

        if action.action == "ui_action":
            if self._on_ui_action is None:
                return AgentActionResult(ok=False, error="no ui_action handler is configured")
            name = str(action.payload.get("name", ""))
            data = dict(action.payload.get("data") or {})
            payload = await self._on_ui_action(name, data)
            return AgentActionResult(ok=True, payload=payload)

        if action.action == "form_submit":
            return await self._handle_form_submit(action.payload)

        if action.action == "block_submit":
            return await self._handle_block_submit(action.payload)

        if action.action == "state_delta":
            return await self._handle_state_delta(action.payload)

        if action.action == "block_action":
            block_id = str(action.payload.get("block_id", ""))
            if not block_id:
                return AgentActionResult(ok=False, error="block_action needs a block_id")
            name = str(action.payload.get("name", ""))
            data = dict(action.payload.get("data") or {})
            if name == OPEN_CITATION and self._block_type(block_id) == "kb_citations":
                # E1 (V5-08): the platform opens the cited page; packs never see it.
                return AgentActionResult(ok=True, payload=await open_citation(self, block_id, data))
            if self._block_type(block_id) == "link":
                # V5-43: the caller opened the link; packs never see it.
                opened = await self._link_opened(block_id) if name == LINK_OPENED else False
                return AgentActionResult(ok=True, payload={"opened": opened})
            if self._block_type(block_id) == "cards":
                refused = await self._card_action(block_id, name, data)
                if refused is not None:
                    return AgentActionResult(ok=False, error=refused)
                data = {"card_id": data["card_id"]}
            if self._on_block_action is None:
                # Default no-op (CONTRACTS-V2 §4.4): acknowledged, nothing to return.
                return AgentActionResult(ok=True)
            payload = await self._on_block_action(block_id, name, data)
            return AgentActionResult(ok=True, payload=payload)

        if action.action in ("rewind", "inject_user_text"):
            if self._on_text_action is None:
                return AgentActionResult(ok=False, error=f"{action.action} is not supported")
            payload = await self._on_text_action(action.action, dict(action.payload))
            return AgentActionResult(ok=True, payload=payload)

        return AgentActionResult(ok=False, error=f"unsupported action: {action.action!r}")

    async def _handle_form_submit(self, payload: dict[str, Any]) -> AgentActionResult:
        block_id = str(payload.get("block_id", ""))
        if not block_id or (block_id not in self.state.blocks and block_id not in self._block_specs):
            return AgentActionResult(ok=False, error=f"unknown form block: {block_id!r}")
        if self._block_type(block_id) not in (None, "form"):
            return AgentActionResult(ok=False, error=f"block {block_id!r} is not a form")
        if payload.get("cancelled") is True:
            await self._submit(block_id, None)
            return AgentActionResult(ok=True)
        values = payload.get("values")
        if not isinstance(values, dict):
            return AgentActionResult(ok=False, error="form_submit needs a values object")
        await self._submit(block_id, values)
        return AgentActionResult(ok=True)

    async def _handle_block_submit(self, payload: dict[str, Any]) -> AgentActionResult:
        """`block_submit` (V5-02): the answer to a `request` (or `form`) on any requestable block."""
        try:
            submit = BlockSubmitPayload.model_validate(payload)
        except ValidationError:
            return AgentActionResult(
                ok=False, error="block_submit needs a block_id and either a values object or cancelled: true"
            )
        block_id = submit.block_id
        if block_id not in self.state.blocks and block_id not in self._block_specs:
            return AgentActionResult(ok=False, error=f"unknown block: {block_id!r}")
        if block_id not in self._pending and not self._is_requestable(block_id):
            return AgentActionResult(ok=False, error=f"block {block_id!r} cannot be submitted")
        await self._submit(block_id, None if submit.cancelled else submit.values)
        return AgentActionResult(ok=True)

    async def _handle_state_delta(self, payload: dict[str, Any]) -> AgentActionResult:
        """`state_delta` (V5-43): an AG-UI `STATE_DELTA` on the blocks a caller may write.

        Refused outright unless the agent's `PanelLayout.accept_state_delta` is on (bound by
        `PlatformAgent`; default off, ruling on ask #309); the refusal is logged once per session.

        Every operation must address `/blocks/<id>/...` of a block whose type is in
        :data:`STATE_DELTA_BLOCK_TYPES`; the delta is translated with
        `lkap_contracts.ui_agui.agui_delta_to_patch` against the current state and every
        touched block is validated against its state model before one patch is sent.
        Anything wrong refuses the whole delta and changes nothing.
        """
        if not self._accept_state_delta:
            if not self._state_delta_refusal_logged:
                self._state_delta_refusal_logged = True
                self._log.info(
                    "state_delta refused: this agent's panel does not accept changes from the page"
                )
            return AgentActionResult(ok=False, error="state_delta is not enabled for this agent's panel")
        try:
            parsed = StateDeltaPayload.model_validate(payload)
        except ValidationError:
            return AgentActionResult(
                ok=False, error="state_delta needs a delta of 1 to 100 JSON Patch operations"
            )
        try:
            ops = agui_delta_to_patch(parsed.delta, self.state)
        except AguiPatchError as exc:
            return AgentActionResult(ok=False, error=f"state_delta refused: {exc}")
        grouped: dict[str, list[UiPatchOp]] = {}
        for op in ops:
            segments = _segments(op.path)
            block_id = segments[1]
            if self._block_type(block_id) not in STATE_DELTA_BLOCK_TYPES:
                return AgentActionResult(
                    ok=False, error=f"state_delta refused: block {block_id!r} cannot be changed from the page"
                )
            relative = "/" + "/".join(segments[2:])
            grouped.setdefault(block_id, []).append(op.model_copy(update={"path": relative}))
        try:
            for block_id, block_ops in grouped.items():
                self._validated_block_ops(block_id, block_ops)
        except ValidationError as exc:
            return AgentActionResult(
                ok=False,
                error=f"state_delta refused: the result does not fit the block ({exc.errors()[0]['msg']})",
            )
        if ops:
            await self.patch(ops)
        for block_id in grouped:
            self._record(
                "block_update",
                {"block_id": block_id, "block_type": self._block_type(block_id), "op": "state_delta"},
            )
        return AgentActionResult(ok=True, payload={"applied": len(ops)})

    async def _link_opened(self, block_id: str) -> bool:
        """Mark a `pending` link `opened` (V5-43); any other status is left alone."""
        state = self.state.blocks.get(block_id) or {}
        if state.get("status") != "pending":
            return False
        await self.patch_block(
            block_id,
            [
                UiPatchOp(op="set", path="status", value="opened"),
                UiPatchOp(op="set", path="opened_at", value=time.time()),
            ],
        )
        return True

    async def _card_action(self, block_id: str, name: str, data: dict[str, Any]) -> str | None:
        """Check a `cards` block action and record a card pick (V5-43); the refusal reason, or `None`."""
        state = self.state.blocks.get(block_id) or {}
        spec = self._block_specs.get(block_id)
        error = card_action_error(state, spec.config if spec is not None else {}, name, data)
        if error is not None:
            return error
        if name == CARD_SELECT and state.get("selected") != data["card_id"]:
            await self.patch_block(block_id, [UiPatchOp(op="set", path="selected", value=data["card_id"])])
        return None

    async def apply_link_outcome(
        self, *, block_id: str | None, reference: str | None, status: LinkOutcome
    ) -> dict[str, Any] | None:
        """Apply the outcome the api reported for a `link` block (V5-43).

        The block is the one named `block_id` (a `link` block), else the `link` block
        whose `reference` matches. It changes only while `pending` or `opened`, so a
        repeated or late report does nothing.

        Returns:
            The block's state after the change (plus `block_id`), or `None` when no
            link block matched or it was not waiting for an outcome.
        """
        links = [bid for bid, spec in self._block_specs.items() if spec.type == "link"]
        target: str | None = None
        if block_id is not None:
            target = block_id if block_id in links else None
        elif reference is not None:
            target = next(
                (bid for bid in links if (self.state.blocks.get(bid) or {}).get("reference") == reference),
                None,
            )
        if target is None:
            return None
        state = self.state.blocks.get(target) or {}
        if state.get("status") not in ("pending", "opened"):
            return None
        if reference is not None and block_id is not None and state.get("reference") not in (None, reference):
            return None
        await self.patch_block(
            target,
            [
                UiPatchOp(op="set", path="status", value=status),
                UiPatchOp(op="set", path="completed_at", value=time.time()),
            ],
        )
        self._record("link_completed", {"block_id": target, "status": status, "kind": state.get("kind")})
        return {"block_id": target, **(self.state.blocks.get(target) or {})}

    async def _submit(self, block_id: str, values: dict[str, Any] | None) -> None:
        """Route an answer (`None` = the user cancelled) by the pending request's method.

        With nothing pending, a `form` block keeps the legacy form handling and
        every other block the generic one. Every caller of this method is a
        browser answer, so the keys only the agent side may set
        (:data:`AGENT_ONLY_ANSWER_KEYS`) are dropped here (S5-3).
        """
        if values is not None:
            values = {key: value for key, value in values.items() if key not in AGENT_ONLY_ANSWER_KEYS}
        entry = self._pending.get(block_id)
        method: RequestMethod
        if entry is not None:
            method = entry.method
        else:
            method = "form" if self._block_type(block_id) == "form" else "request"
        if values is None:
            future = entry.future if entry is not None else None
            if future is not None and not future.done():
                future.set_result(None)
            await self._mark_cancelled(block_id, method, "user")
        elif method == "form":
            await self._accept_form(block_id, values)
        else:
            await self._accept_request(block_id, values)

    async def _accept_form(self, block_id: str, values: dict[str, Any], *, notify: bool = True) -> None:
        """Store submitted `values`, record `form_submitted`, and hand them to the waiter.

        One patch, three ops: `set .../values`, `set .../status = "submitted"`,
        `set .../submitted_at`. With nobody waiting (timed out, cancelled
        tool), the values go to the unsolicited-form callback instead. A `file`
        field keeps only the asset ids this session stored for it (V5-19).
        """
        values = self._verified_form_values(block_id, jsonable(values))
        await self.patch(
            [
                UiPatchOp(op="set", path=block_path(block_id, "values"), value=values),
                UiPatchOp(op="set", path=block_path(block_id, "status"), value="submitted"),
                UiPatchOp(op="set", path=block_path(block_id, "submitted_at"), value=time.time()),
            ]
        )
        self._record("form_submitted", {"block_id": block_id, "values": values})
        await self._deliver(block_id, values, notify=notify)

    async def _accept_request(self, block_id: str, values: dict[str, Any], *, notify: bool = True) -> None:
        """Generic submit: mark the block `submitted` and hand `values` to the waiter.

        The values are stored at `.../values` when the block's state model has
        that field (or the block is untyped). Otherwise each key of `values`
        that names a field of the state model is written there when the result
        still validates (V5-08: a `choices` answer `{selected: [...]}` lands
        in `selected`); anything else is left to the requesting tool.
        """
        values = jsonable(values)
        if self._block_type(block_id) == "form":
            values = self._verified_form_values(block_id, values)
        if self._block_type(block_id) == "slots":
            values = self._slot_values(block_id, values)
        ops: list[UiPatchOp] = []
        if self._stores_values(block_id):
            ops.append(UiPatchOp(op="set", path=block_path(block_id, "values"), value=values))
        else:
            ops += self._answer_field_ops(block_id, values)
        ops += [
            UiPatchOp(op="set", path=block_path(block_id, "status"), value="submitted"),
            UiPatchOp(op="set", path=block_path(block_id, "submitted_at"), value=time.time()),
        ]
        await self.patch(ops)
        self._record(
            "block_update",
            {
                "block_id": block_id,
                "block_type": self._block_type(block_id),
                "op": "block_submitted",
                "values": values,
            },
        )
        await self._deliver(block_id, values, notify=notify)

    def _slot_values(self, block_id: str, values: dict[str, Any]) -> dict[str, Any]:
        """A `slots` answer as the worker trusts it (V5-43): the id, and the block's own start and end.

        A browser's `start` and `end` (or anything else) are dropped, so neither the waiting
        tool nor a late-answer reply can see a time the block never offered.
        """
        kept = {k: v for k, v in values.items() if k in ANSWER_KEYS["slots"] | AGENT_ONLY_ANSWER_KEYS}
        slot = find_slot(self.state.blocks.get(block_id) or {}, str(kept.get("selected")))
        if slot is not None:
            kept.update({"start": slot.get("start"), "end": slot.get("end")})
        return kept

    def _answer_field_ops(self, block_id: str, values: dict[str, Any]) -> list[UiPatchOp]:
        """`set` ops for the keys of `values` the block type lists as answer keys (:data:`ANSWER_KEYS`).

        S5-3: only a type's own answer keys are written (`consent` → `accepted`,
        `choices` → `selected`); every other key, including another state field
        such as a consent block's `text` or `method`, is ignored. Returns nothing
        when the block is untyped, has no answer keys (`upload`: V5-19, its
        `files` are written by the worker only) or the answer would not
        validate against the block's state model.
        """
        block_type = self._block_type(block_id)
        model = BLOCK_STATE_MODELS.get(block_type)
        allowed = ANSWER_KEYS.get(block_type, frozenset()) if isinstance(block_type, str) else frozenset()
        if model is None or not allowed:
            return []
        if block_type == "choices" and "selected" in values:
            error = choice_selection_error(self.state.blocks.get(block_id) or {}, values["selected"])
            if error is not None:
                self._log.debug("choice answer not stored", block_id=block_id, error=error)
                return []
        if block_type == "slots" and "selected" in values:
            error = slot_selection_error(self.state.blocks.get(block_id) or {}, values["selected"])
            if error is not None:
                self._log.debug("slot answer not stored", block_id=block_id, error=error)
                return []
        protected = set(RequestableState.model_fields)
        ops = [
            UiPatchOp(op="set", path=key, value=value)
            for key, value in values.items()
            if key in allowed and key in model.model_fields and key not in protected
        ]
        if not ops:
            return []
        try:
            return self._validated_block_ops(block_id, ops)
        except ValidationError:
            self._log.debug("block answer not stored: it does not fit the block", block_id=block_id)
            return []

    async def _deliver(self, block_id: str, values: dict[str, Any], *, notify: bool = True) -> None:
        """Resolve the block's waiter, or, with nobody waiting, call the unsolicited handler."""
        entry = self._pending.get(block_id)
        if entry is not None and not entry.future.done():
            entry.future.set_result(values)
            return
        if notify and self._on_unsolicited_form is not None:
            try:
                await self._on_unsolicited_form(block_id, values)
            except Exception:  # noqa: BLE001 - the submission is already stored and recorded
                self._log.warning("unsolicited form handler failed", block_id=block_id, exc_info=True)

    async def _mark_cancelled(self, block_id: str, method: RequestMethod, reason: str) -> None:
        """Write the cancelled status: `cancelled` for `request`, `idle` for the legacy `form`.

        Skipped when a newer request has claimed the block since the release
        (so a late patch never hides that request) and after `close`.
        """
        current = self._pending.get(block_id)
        if self._closed or (current is not None and not current.future.done()):
            return
        event: dict[str, Any]
        if method == "form":
            await self.patch([UiPatchOp(op="set", path=block_path(block_id, "status"), value="idle")])
            event = {"block_id": block_id, "block_type": "form", "op": "form_cancelled"}
        else:
            await self.patch([UiPatchOp(op="set", path=block_path(block_id, "status"), value="cancelled")])
            event = {"block_id": block_id, "block_type": self._block_type(block_id), "op": "block_cancelled"}
        if reason != "user":
            event["reason"] = reason
        self._record("block_update", event)

    async def _send_request(
        self,
        block_id: str,
        method: RequestMethod,
        payload: dict[str, Any],
        future: asyncio.Future[dict[str, Any] | None],
    ) -> None:
        try:
            result = await self.request_ui(method, payload, response_timeout=REQUEST_ACK_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - the request is visible from state; keep waiting
            self._log.debug("ui_request_rpc_failed", block_id=block_id, method=method, error=str(exc))
            return
        if future.done():
            return
        if result.get("cancelled") is True:
            await self._submit(block_id, None)
        elif isinstance(result.get("values"), dict):
            await self._submit(block_id, result["values"])

    # --- helpers ----------------------------------------------------------------

    def _block_type(self, block_id: str) -> Any:
        spec = self._block_specs.get(block_id)
        return spec.type if spec is not None else None

    def _is_requestable(self, block_id: str) -> bool:
        """Whether a request may target the block: a `RequestableState` type, `custom` or untyped."""
        block_type = self._block_type(block_id)
        if block_type is None or block_type == "custom":
            return True
        model = BLOCK_STATE_MODELS.get(block_type)
        return model is not None and issubclass(model, RequestableState)

    def _stores_values(self, block_id: str) -> bool:
        """Whether a generic submission is written to the block's `values` field."""
        model = BLOCK_STATE_MODELS.get(self._block_type(block_id))
        return model is None or "values" in model.model_fields

    def _validated_block_ops(self, block_id: str, ops: list[UiPatchOp]) -> list[UiPatchOp]:
        """Rewrite block-relative `ops` to absolute paths, checking the result fits the block type.

        Raises:
            pydantic.ValidationError: When the patched state no longer fits.
        """
        absolute = [op.model_copy(update={"path": block_path(block_id, op.path)}) for op in ops]
        block_type = self._block_type(block_id)
        if block_type is not None:
            trial = UiState(blocks={block_id: copy.deepcopy(self.state.blocks.get(block_id, {}))})
            for op in absolute:
                apply_patch_op(trial, _normalize_block_op(op))
            validate_block_state(block_type, trial.blocks.get(block_id) or {})
        return absolute

    def _record(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._record_event is None:
            return
        try:
            self._record_event(event_type, payload)
        except Exception:  # noqa: BLE001 - observability must never break the UI path
            self._log.debug("session event not recorded", event_type=event_type, exc_info=True)

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _remote_identity(self) -> str:
        """The browser participant's identity (DECISIONS-W2 D-W2-7).

        Prefers the api-minted `ui_identity`; the fallback skips an avatar
        worker (`lk.publish_on_behalf`) and any other agent participant.
        """
        if self._ui_identity is not None:
            return self._ui_identity
        for participant in self._room.remote_participants.values():
            attributes = getattr(participant, "attributes", None) or {}
            if attributes.get(_ATTRIBUTE_PUBLISH_ON_BEHALF):
                continue
            if getattr(participant, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
                continue
            return str(participant.identity)
        raise RuntimeError("request_ui: no remote participant connected")


def _citation_of(hit: KbHit) -> KbCitation:
    """A `KbCitation` for `hit`, with the locators its `meta` carries (when it has one)."""
    meta = getattr(hit, "meta", None)
    meta = meta if isinstance(meta, dict) else {}
    page = meta.get("page")
    heading = meta.get("heading_path")
    start, end = meta.get("char_start"), meta.get("char_end")
    return KbCitation(
        chunk_id=hit.chunk_id,
        filename=hit.filename,
        score=hit.score,
        text=hit.text,
        document_id=getattr(hit, "document_id", None) or None,
        page=page if isinstance(page, int) and not isinstance(page, bool) else None,
        heading_path=[str(h) for h in heading] if isinstance(heading, list) and heading else None,
        char_start=start if isinstance(start, int) and not isinstance(start, bool) else None,
        char_end=end if isinstance(end, int) and not isinstance(end, bool) else None,
    )


def _normalize_block_op(op: UiPatchOp) -> UiPatchOp:
    """Make a `/blocks/...` op's value plain JSON, so it goes on the wire by alias."""
    if _segments(op.path)[:1] != ["blocks"]:
        return op
    return op.model_copy(update={"value": jsonable(op.value)})


# ------------------------------------------------------------------- live captions (V5-31)

_captions_log = get_logger(__name__)

#: The fewest seconds between two interim captions of the agent (the final always goes).
CAPTION_INTERIM_INTERVAL_S: Final[float] = 0.25

#: Sends one caption (`UiChannel.caption`).
CaptionSender = Callable[[CaptionSegment], Awaitable[None]]


class CaptionStream:
    """Builds and sends the caption segments of one session, in order.

    Args:
        send: Sends one segment (`UiChannel.caption`).
        show_user: Caption the caller (a `captions` block has `show_user`).
        show_agent: Caption the agent (a `captions` block has `show_agent`).
        agent_language: The agent's current reply language, read per segment.
        clock: Test seam for `time.monotonic`.
        wall_clock: Test seam for `time.time` (the segment's `ts`).
    """

    def __init__(
        self,
        send: CaptionSender,
        *,
        show_user: bool = True,
        show_agent: bool = True,
        agent_language: Callable[[], str | None] = lambda: None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._send = send
        self.show_user = show_user
        self.show_agent = show_agent
        self._agent_language = agent_language
        self._clock = clock
        self._wall_clock = wall_clock
        self._counter = 0
        self._user_id: str | None = None
        self._agent_id: str | None = None
        self._agent_text = ""
        self._agent_last_sent = 0.0
        self._tail: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    def _next_id(self, speaker: CaptionSpeaker) -> str:
        self._counter += 1
        return f"{speaker[0]}-{self._counter}"

    def _emit(
        self, segment_id: str, speaker: CaptionSpeaker, text: str, final: bool, language: str | None
    ) -> None:
        segment = CaptionSegment(
            id=segment_id, speaker=speaker, text=text, final=final, language=language, ts=self._wall_clock()
        )
        previous = self._tail

        async def _send() -> None:
            if previous is not None:
                with contextlib.suppress(BaseException):
                    await previous
            try:
                await self._send(segment)
            except Exception:  # noqa: BLE001 - a lost caption must never break the call
                _captions_log.debug("caption not sent", speaker=speaker, exc_info=True)

        try:
            task = asyncio.get_running_loop().create_task(_send())
        except RuntimeError:  # no running loop (sync tests): drop it
            return
        self._tail = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---------------------------------------------------------------- the caller

    def user(self, text: str, *, final: bool, language: str | None = None) -> None:
        """A transcript of the caller (`user_input_transcribed`): interim or final."""
        if not self.show_user:
            return
        text = text.strip()
        if not text and not final:
            return
        if self._user_id is None:
            if not text:
                return
            self._user_id = self._next_id("user")
        self._emit(self._user_id, "user", text, final, language)
        if final:
            self._user_id = None

    # ------------------------------------------------------------------ the agent

    def agent_delta(self, delta: str) -> None:
        """The next words of the agent, already timed to its audio."""
        if not self.show_agent or not delta:
            return
        if self._agent_id is None:
            self._agent_id = self._next_id("agent")
            self._agent_text = ""
            self._agent_last_sent = 0.0
        self._agent_text += delta
        now = self._clock()
        if self._agent_text.strip() and now - self._agent_last_sent >= CAPTION_INTERIM_INTERVAL_S:
            self._agent_last_sent = now
            self._emit(self._agent_id, "agent", self._agent_text.strip(), False, self._agent_language())

    def agent_flush(self) -> None:
        """The agent finished (or was interrupted in) an utterance: send it as final."""
        if self._agent_id is None:
            return
        text = self._agent_text.strip()
        segment_id, self._agent_id, self._agent_text = self._agent_id, None, ""
        if text:
            self._emit(segment_id, "agent", text, True, self._agent_language())

    async def drain(self) -> None:
        """Wait for the captions already queued (tests and shutdown)."""
        if self._tail is not None:
            with contextlib.suppress(BaseException):
                await self._tail


class CaptionsTextOutput(TextOutput):
    """A text output after RoomIO's transcription output that feeds the agent's captions.

    It sees exactly what the browser's transcription sees, in step with the
    audio. Until :meth:`bind` gives it a :class:`CaptionStream` (a session whose
    panel has a `captions` block) it does nothing.
    """

    def __init__(self) -> None:
        super().__init__(label="LKAPCaptions", next_in_chain=None)
        self._stream: CaptionStream | None = None

    def bind(self, stream: CaptionStream | None) -> None:
        """Start (or stop, with `None`) feeding `stream`."""
        self._stream = stream

    @property
    def bound(self) -> bool:
        """Whether a caption stream is attached."""
        return self._stream is not None

    async def capture_text(self, text: str) -> None:
        """One delta of the agent's words."""
        if self._stream is not None:
            self._stream.agent_delta(str(text))

    def flush(self) -> None:
        """The end of one agent utterance."""
        if self._stream is not None:
            self._stream.agent_flush()


#: `AgentSession` -> its captions tap (weak: the tap goes with its session).
_CAPTION_TAPS: weakref.WeakKeyDictionary[Any, CaptionsTextOutput] = weakref.WeakKeyDictionary()


def register_caption_tap(session: Any, tap: CaptionsTextOutput) -> None:
    """Remember the tap the session builder put in `session`'s room options."""
    try:
        _CAPTION_TAPS[session] = tap
    except TypeError:  # a test double that cannot be weakly referenced
        _captions_log.debug("captions tap not registered: the session cannot be weakly referenced")


def caption_tap_for(session: Any) -> CaptionsTextOutput | None:
    """The captions tap of `session`, if the session builder made one."""
    try:
        return _CAPTION_TAPS.get(session)
    except TypeError:
        return None
