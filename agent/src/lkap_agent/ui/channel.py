"""`UiChannel` — the agent's handle to the `lkap.ui.*` topics and RPCs.

Implements `packs.base.UiChannel` (docs/CONTRACTS.md §10) over a real
`livekit.rtc.Room`. State mutations are applied to a local copy of
`UiState` and mirrored to the browser as `UiSnapshot`/`UiPatch` JSON on the
`lkap.ui.state` text stream; images go out on the `lkap.ui.asset` byte
stream; `lkap.agent.action` (UI -> agent) is dispatched here to optional
platform/pack callbacks, and `lkap.ui.request` (agent -> UI) is a thin
`perform_rpc` wrapper.

Note on attribute naming: the byte-stream attribute and `AssetRef` field
carrying the caption are named `caption` (docs/CONTRACTS.md §10 wins over
docs/ARCHITECTURE.md §9's `caption_ref`, which is stale).
"""

from __future__ import annotations

import mimetypes
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from livekit import rtc
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
    ChecklistItem,
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

__all__ = ["UiChannel"]

#: `Pack.on_ui_action(ctx, action, payload) -> payload` shape, bound by the caller.
OnUiAction = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
#: Attribute an avatar worker carries (`livekit.agents.types.ATTRIBUTE_PUBLISH_ON_BEHALF`).
_ATTRIBUTE_PUBLISH_ON_BEHALF = "lk.publish_on_behalf"
#: `set_video_source` payload handler: receives `"camera" | "screen" | "none"`.
OnSetVideoSource = Callable[[str], Awaitable[None]]


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


def apply_patch_op(state: UiState, op: UiPatchOp) -> None:
    """Apply one `UiPatchOp` to `state` in place (docs/CONTRACTS.md §10 semantics).

    `/activity` is additionally trimmed to the last `ACTIVITY_RING_SIZE` entries
    after every mutation, per docs/ARCHITECTURE.md §9.
    """
    segments = _segments(op.path)
    if not segments:
        raise ValueError(f"empty UiPatchOp.path: {op.path!r}")
    top, rest = segments[0], segments[1:]

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
    """

    def __init__(
        self,
        room: rtc.Room,
        session_id: str,
        *,
        ui_identity: str | None = None,
        on_set_video_source: OnSetVideoSource | None = None,
        on_ui_action: OnUiAction | None = None,
        log: Any = None,
    ) -> None:
        self.seq = 0
        self.state = UiState()
        self._room = room
        self._session_id = session_id
        self._ui_identity = ui_identity
        self._on_set_video_source = on_set_video_source
        self._on_ui_action = on_ui_action
        self._patches_since_snapshot = 0
        self._log = log or get_logger(__name__).bind(session_id=session_id)

    def start(self) -> None:
        """Register the `lkap.agent.action` RPC handler on the local participant."""
        self._room.local_participant.register_rpc_method(RPC_AGENT_ACTION, self._handle_agent_action)

    def close(self) -> None:
        """Unregister the `lkap.agent.action` RPC handler, if the participant supports it.

        Guarded because some minimal test doubles (e.g. `fakes.fake_room.FakeRoom`)
        implement `register_rpc_method` but not the (rarely needed, per-session-room)
        `unregister_rpc_method`.
        """
        unregister = getattr(self._room.local_participant, "unregister_rpc_method", None)
        if unregister is not None:
            unregister(RPC_AGENT_ACTION)

    # --- packs.base.UiChannel -------------------------------------------------

    async def patch(self, ops: list[UiPatchOp]) -> None:
        """Send a `UiPatch` with `ops`, applying them to `state` and bumping `seq`."""
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
        await self.patch([UiPatchOp(op="append", path="/assets", value=ref)])
        return asset_id

    async def activity(self, event: ActivityEvent) -> None:
        """Record an `ActivityEvent`: broadcast on `lkap.ui.activity` *and* upsert it
        into `state.activity` (same `id` replaces an earlier entry; last 30 kept) so
        late joiners/reconnects see it via a snapshot too (docs/CONTRACTS.md §10).
        """
        await self._room.local_participant.send_text(event.model_dump_json(), topic=TOPIC_UI_ACTIVITY)
        await self.patch([UiPatchOp(op="upsert", path="/activity", value=event, key=event.id)])

    async def request_ui(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        """RPC `lkap.ui.request` (agent -> UI); returns the UI's response payload."""
        request = UiRequest(method=method, payload=payload)
        identity = self._remote_identity()
        response_payload = await self._room.local_participant.perform_rpc(
            destination_identity=identity, method=RPC_UI_REQUEST, payload=request.model_dump_json()
        )
        result = UiRequestResult.model_validate_json(response_payload)
        return result.payload

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

        return AgentActionResult(ok=False, error=f"unknown action: {action.action!r}")  # pragma: no cover

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
