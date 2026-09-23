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
`set_block` / `patch_block` / `cite` / `request_form` and carried by the same
snapshot/patch stream (paths under `/blocks/<id>`). `request_form` is a
two-channel round trip: the block state flips to `status="requested"` (so a
reconnecting browser re-renders the form from a snapshot), a short
`lkap.ui.request {method: "form"}` RPC asks the browser to show it, and the
values normally come back as `lkap.agent.action {action: "form_submit"}`.

Note on attribute naming: the byte-stream attribute and `AssetRef` field
carrying the caption are named `caption` (docs/CONTRACTS.md §10 wins over
docs/ARCHITECTURE.md §9's `caption_ref`, which is stale).
"""

from __future__ import annotations

import asyncio
import copy
import mimetypes
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Final

from livekit import rtc
from lkap_contracts.api_models import KbHit
from lkap_contracts.ui_protocol import (
    ACTIVITY_RING_SIZE,
    RPC_AGENT_ACTION,
    RPC_UI_REQUEST,
    SNAPSHOT_EVERY_N_PATCHES,
    TOPIC_UI_ACTIVITY,
    TOPIC_UI_ASSET,
    TOPIC_UI_STATE,
    ActivityEvent,
    AgentAction,
    AgentActionResult,
    AssetRef,
    BlockSpec,
    ChecklistItem,
    FormBlockState,
    KbCitation,
    Note,
    StatusStamp,
    Tone,
    UiPatch,
    UiPatchOp,
    UiRequest,
    UiRequestResult,
    UiSnapshot,
    UiState,
)
from pydantic import BaseModel

from lkap_agent.logging import get_logger
from lkap_agent.ui.blocks import block_path, initial_block_states, jsonable, validate_block_state

__all__ = ["UiChannel"]

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
#: Called with `(block_id, values)` for a `form_submit` nobody is awaiting.
OnUnsolicitedForm = Callable[[str, dict[str, Any]], Awaitable[None]]
#: `SessionContext.record_event`-shaped session event sink.
RecordEvent = Callable[[str, dict[str, Any]], None]

#: How long the `form` UI request may take to be answered. The browser should
#: acknowledge as soon as the form is visible; the values arrive separately via
#: `form_submit`, so this bounds only the "show the form" nudge. The receiving
#: handler sees `responseTimeout` minus the SDK's 7 s round-trip allowance
#: (measured live: 10 s here arrived as 3 s in the browser), so keep it well
#: above 7 s.
FORM_REQUEST_ACK_TIMEOUT_S: Final[float] = 15.0
#: Response timeout for the fire-and-forget `show_block` UI request (same 7 s caveat).
SHOW_BLOCK_TIMEOUT_S: Final[float] = 10.0


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
        self._pending_forms: dict[str, asyncio.Future[dict[str, Any] | None]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False
        self._log = log or get_logger(__name__).bind(session_id=session_id)

    def start(self) -> None:
        """Register the `lkap.agent.action` RPC handler on the local participant."""
        self._room.local_participant.register_rpc_method(RPC_AGENT_ACTION, self._handle_agent_action)

    def close(self) -> None:
        """Unregister the RPC handler and release every pending `request_form` with `None`.

        The unregister is guarded because some minimal test doubles (e.g.
        `fakes.fake_room.FakeRoom`) implement `register_rpc_method` but not the
        (rarely needed, per-session-room) `unregister_rpc_method`.
        """
        self._closed = True
        for future in self._pending_forms.values():
            if not future.done():
                future.set_result(None)
        self._pending_forms.clear()
        for task in list(self._tasks):
            task.cancel()
        unregister = getattr(self._room.local_participant, "unregister_rpc_method", None)
        if unregister is not None:
            unregister(RPC_AGENT_ACTION)

    # --- platform wiring (not part of packs.base.UiChannel) ----------------------

    def bind(
        self,
        *,
        on_block_action: OnBlockAction | None = None,
        on_unsolicited_form: OnUnsolicitedForm | None = None,
        on_text_action: OnTextAction | None = None,
        record_event: RecordEvent | None = None,
    ) -> None:
        """Attach the platform callbacks for block actions, late form submissions and events.

        Only the arguments that are not `None` replace the current callbacks.
        `on_text_action` (V2-18) handles `rewind`/`inject_user_text`; `main.py`
        binds it only for `channel="text"` sessions, before `PlatformAgent`
        binds the block callbacks, so calling `bind` twice never clobbers it.
        """
        if on_block_action is not None:
            self._on_block_action = on_block_action
        if on_unsolicited_form is not None:
            self._on_unsolicited_form = on_unsolicited_form
        if on_text_action is not None:
            self._on_text_action = on_text_action
        if record_event is not None:
            self._record_event = record_event

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
        asset_id = str(uuid.uuid4())
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

        ref = AssetRef(
            asset_id=asset_id, kind=kind, mime=mime, caption=caption, meta=meta or {}, ts=time.time()
        )
        ops = [UiPatchOp(op="append", path="/assets", value=ref)]
        if mime.startswith("image/"):
            ops.extend(
                UiPatchOp(op="append", path=block_path(block_id, "asset_ids"), value=asset_id)
                for block_id, spec in self._block_specs.items()
                if spec.type == "gallery" and block_id in self.state.blocks
            )
        await self.patch(ops)
        return asset_id

    async def activity(self, event: ActivityEvent) -> None:
        """Record an `ActivityEvent`: broadcast on `lkap.ui.activity` *and* upsert it
        into `state.activity` (same `id` replaces an earlier entry; last 30 kept) so
        late joiners/reconnects see it via a snapshot too (docs/CONTRACTS.md §10).
        """
        await self._room.local_participant.send_text(event.model_dump_json(), topic=TOPIC_UI_ACTIVITY)
        await self.patch([UiPatchOp(op="upsert", path="/activity", value=event, key=event.id)])

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
        absolute = [op.model_copy(update={"path": block_path(block_id, op.path)}) for op in ops]
        block_type = self._block_type(block_id)
        if block_type is not None:
            trial = UiState(blocks={block_id: copy.deepcopy(self.state.blocks.get(block_id, {}))})
            for op in absolute:
                apply_patch_op(trial, _normalize_block_op(op))
            validate_block_state(block_type, trial.blocks.get(block_id) or {})
        await self.patch(absolute)
        self._record("block_update", {"block_id": block_id, "block_type": block_type, "op": "patch"})

    async def request_form(
        self,
        block_id: str,
        schema: dict[str, Any],
        prefill: dict[str, Any] | None = None,
        timeout_s: float = 120,
    ) -> dict[str, Any] | None:
        """Show a JSON-schema form in block `block_id` and wait for the user.

        1. `set /blocks/<id>` = `FormBlockState{schema, values: prefill,
           status: "requested", submitted_at: null}`.
        2. RPC `lkap.ui.request {method: "form", payload: {block_id, schema,
           prefill}}` with a short response timeout. An answer carrying
           `values` resolves the form; `{cancelled: true}` returns `None`; any
           other answer or an RPC error is logged and the wait continues (the
           form is already visible from the block state).
        3. `lkap.agent.action {action: "form_submit", payload: {block_id,
           values}}` resolves the form (see `_accept_form`).

        A second `request_form` on the same block releases the first with
        `None`. On timeout the block keeps `status: "requested"`, so a late
        submission still lands in state and reaches the agent through the
        unsolicited-form callback.

        Returns:
            The submitted values, or `None` on cancel, timeout or session close.
        """
        if self._closed:
            return None
        previous = self._pending_forms.pop(block_id, None)
        if previous is not None and not previous.done():
            previous.set_result(None)
        future: asyncio.Future[dict[str, Any] | None] = asyncio.get_running_loop().create_future()
        self._pending_forms[block_id] = future

        prefill_values = dict(prefill or {})
        state = FormBlockState.model_validate(
            {"schema": schema, "values": prefill_values, "status": "requested"}
        ).model_dump(mode="json", by_alias=True)
        try:
            await self.patch([UiPatchOp(op="set", path=block_path(block_id), value=state)])
            self._record(
                "block_update",
                {"block_id": block_id, "block_type": "form", "op": "form_requested"},
            )
            self._spawn(self._send_form_request(block_id, schema, prefill_values, future))
            return await asyncio.wait_for(future, timeout=timeout_s)
        except TimeoutError:
            self._log.debug("form_request_timed_out", block_id=block_id, timeout_s=timeout_s)
            return None
        finally:
            if self._pending_forms.get(block_id) is future:
                del self._pending_forms[block_id]

    async def cite(self, block_id: str, hits: list[KbHit]) -> None:
        """Replace `/blocks/<block_id>/items` with `hits` as `KbCitation`s."""
        items = [
            KbCitation(chunk_id=h.chunk_id, filename=h.filename, score=h.score, text=h.text) for h in hits
        ]
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

        if action.action == "block_action":
            block_id = str(action.payload.get("block_id", ""))
            if not block_id:
                return AgentActionResult(ok=False, error="block_action needs a block_id")
            name = str(action.payload.get("name", ""))
            data = dict(action.payload.get("data") or {})
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
            await self._cancel_form(block_id)
            return AgentActionResult(ok=True)
        values = payload.get("values")
        if not isinstance(values, dict):
            return AgentActionResult(ok=False, error="form_submit needs a values object")
        await self._accept_form(block_id, values)
        return AgentActionResult(ok=True)

    async def _accept_form(self, block_id: str, values: dict[str, Any]) -> None:
        """Store submitted `values`, record `form_submitted`, and hand them to the waiter.

        One patch, three ops: `set .../values`, `set .../status = "submitted"`,
        `set .../submitted_at`. With nobody waiting (timed out, cancelled
        tool), the values go to the unsolicited-form callback instead.
        """
        values = jsonable(values)
        await self.patch(
            [
                UiPatchOp(op="set", path=block_path(block_id, "values"), value=values),
                UiPatchOp(op="set", path=block_path(block_id, "status"), value="submitted"),
                UiPatchOp(op="set", path=block_path(block_id, "submitted_at"), value=time.time()),
            ]
        )
        self._record("form_submitted", {"block_id": block_id, "values": values})
        future = self._pending_forms.get(block_id)
        if future is not None and not future.done():
            future.set_result(values)
            return
        if self._on_unsolicited_form is not None:
            try:
                await self._on_unsolicited_form(block_id, values)
            except Exception:  # noqa: BLE001 - the submission is already stored and recorded
                self._log.warning("unsolicited form handler failed", block_id=block_id, exc_info=True)

    async def _cancel_form(self, block_id: str) -> None:
        """The user dismissed the form: release the waiter with `None` and reset the status."""
        future = self._pending_forms.get(block_id)
        if future is not None and not future.done():
            future.set_result(None)
        await self.patch([UiPatchOp(op="set", path=block_path(block_id, "status"), value="idle")])
        self._record("block_update", {"block_id": block_id, "block_type": "form", "op": "form_cancelled"})

    async def _send_form_request(
        self,
        block_id: str,
        schema: dict[str, Any],
        prefill: dict[str, Any],
        future: asyncio.Future[dict[str, Any] | None],
    ) -> None:
        try:
            result = await self.request_ui(
                "form",
                {"block_id": block_id, "schema": schema, "prefill": prefill},
                response_timeout=FORM_REQUEST_ACK_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 - the form is visible from state; keep waiting
            self._log.debug("form_request_rpc_failed", block_id=block_id, error=str(exc))
            return
        if future.done():
            return
        if result.get("cancelled") is True:
            await self._cancel_form(block_id)
        elif isinstance(result.get("values"), dict):
            await self._accept_form(block_id, result["values"])

    # --- helpers ----------------------------------------------------------------

    def _block_type(self, block_id: str) -> Any:
        spec = self._block_specs.get(block_id)
        return spec.type if spec is not None else None

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


def _normalize_block_op(op: UiPatchOp) -> UiPatchOp:
    """Make a `/blocks/...` op's value plain JSON, so it goes on the wire by alias."""
    if _segments(op.path)[:1] != ["blocks"]:
        return op
    return op.model_copy(update={"value": jsonable(op.value)})
