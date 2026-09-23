"""`FakePackSessionContext` -- an in-memory implementation of every `packs.base`
Protocol, for offline pack/tool unit tests (no LiveKit connection, no vendor
keys, no network).

The one shared copy (REVIEW-FINAL F-18): `agent/tests/fakes/fake_ctx.py` and
`packs/tests/insurance_claim/fake_ctx.py` are thin shims over this module. It
imports `lkap_contracts` and `packs.base` only -- never `lkap_agent` or
`structlog` -- so a pack's own suite can use it (ARCHITECTURE §10). Each fake
stores its calls/results in plain lists/dicts so a test can assert on them
directly (`ctx.ui.state.notes`, `ctx.ui.state.blocks`, `ctx.workflow_llm.calls`,
`ctx.background.jobs`).

`apply_patch_op` follows docs/CONTRACTS.md §10 for the v1 envelope fields and
the worker's `lkap_agent.ui.channel._apply_tree_op` for `/blocks/...` (a plain
JSON tree: numeric segments index lists, `upsert`/keyed `remove` match items by
`key` or `id`, an unkeyed `remove` deletes the leaf). The v2 block helpers
(`set_block`, `patch_block`, `request_form`, `cite`) mirror the real
`UiChannel` minus state validation and the browser round trip (asks #67).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

from livekit.agents import AgentSession, ChatContext
from lkap_contracts.agent_config import AgentConfig, PipelineConfig, PipelineMode, ProviderRef
from lkap_contracts.api_models import KbHit
from lkap_contracts.ui_protocol import (
    ACTIVITY_RING_SIZE,
    ActivityEvent,
    AssetRef,
    ChecklistItem,
    KbCitation,
    Note,
    StatusStamp,
    Tone,
    UiPatchOp,
    UiState,
)
from packs.base import FrameSnapshot
from pydantic import BaseModel

__all__ = [
    "FakeBackgroundRunner",
    "FakeFrameBuffer",
    "FakeImageGen",
    "FakeKbClient",
    "FakeLogger",
    "FakePackSessionContext",
    "FakeStructuredLLM",
    "FakeUiChannel",
    "apply_patch_op",
    "block_path",
    "default_agent_config",
]


def default_agent_config(**overrides: Any) -> AgentConfig:
    """A minimal, valid `AgentConfig` (cascaded LiveKit Inference)."""
    defaults: dict[str, Any] = {
        "instructions": "You are a helpful test agent.",
        "pipeline": PipelineConfig(
            mode="cascaded",
            stt=ProviderRef(provider_id="livekit-inference-stt"),
            llm=ProviderRef(provider_id="livekit-inference-llm"),
            tts=ProviderRef(provider_id="livekit-inference-tts"),
        ),
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _upsert(items: list[Any], value: Any, key: str | None) -> None:
    """Match-by-`key`-or-`id` upsert semantics (docs/CONTRACTS.md §10)."""
    candidate_key = key if key is not None else (getattr(value, "key", None) or getattr(value, "id", None))
    for idx, existing in enumerate(items):
        existing_key = getattr(existing, "key", None) or getattr(existing, "id", None)
        if existing_key is not None and existing_key == candidate_key:
            items[idx] = value
            return
    items.append(value)


def _apply_list_op(items: list[Any], model_cls: type[BaseModel], op: UiPatchOp) -> None:
    if op.op == "append":
        items.append(model_cls.model_validate(op.value))
    elif op.op == "remove":
        items[:] = [
            i for i in items if not (getattr(i, "key", None) == op.key or getattr(i, "id", None) == op.key)
        ]
    elif op.op == "upsert":
        _upsert(items, model_cls.model_validate(op.value), op.key)
    elif op.op == "set":
        items[:] = [model_cls.model_validate(v) for v in (op.value or [])]
    else:  # pragma: no cover - defensive
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


def _jsonable(value: Any) -> Any:
    """Plain JSON data (models dumped by alias), as the worker stores block state."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


def block_path(block_id: str, relative: str = "") -> str:
    """`/blocks/<block_id>[/<relative>]`; `""` or `"/"` is the block itself."""
    if "/" in block_id or not block_id:
        raise ValueError(f"invalid block id: {block_id!r}")
    rest = relative.strip("/")
    return f"/blocks/{block_id}" + (f"/{rest}" if rest else "")


def _tree_item_matches(item: Any, key: str) -> bool:
    return isinstance(item, dict) and (item.get("key") == key or item.get("id") == key)


def _tree_list_op(items: list[Any], value: Any, op: UiPatchOp) -> None:
    if op.op == "append":
        items.append(value)
    elif op.op == "upsert":
        candidate = op.key
        if candidate is None and isinstance(value, dict):
            candidate = value.get("key") or value.get("id")
        if candidate is not None:
            for idx, existing in enumerate(items):
                if _tree_item_matches(existing, candidate):
                    items[idx] = value
                    return
        items.append(value)
    elif op.op == "remove" and op.key is not None:
        items[:] = [i for i in items if not _tree_item_matches(i, op.key)]
    else:  # pragma: no cover - defensive
        raise ValueError(f"unknown UiPatchOp.op: {op.op!r}")


def _tree_child(container: Any, segment: str) -> Any:
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
    """`/blocks/...` as a JSON tree, like `lkap_agent.ui.channel._apply_tree_op`."""
    container: Any = root
    for segment in segments[:-1]:
        container = _tree_child(container, segment)
        if container is None:
            return
    leaf = segments[-1]
    value = _jsonable(op.value)
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
            _tree_list_op(items, value, op)
            container[index] = items
        return
    if op.op == "set":
        container[leaf] = value
    elif op.op == "remove" and op.key is None:
        container.pop(leaf, None)
    else:
        current = container.get(leaf)
        items = current if isinstance(current, list) else []
        _tree_list_op(items, value, op)
        container[leaf] = items


def apply_patch_op(state: UiState, op: UiPatchOp) -> None:
    """Apply one `UiPatchOp` to `state` in place (docs/CONTRACTS.md §10 semantics)."""
    segments = [s for s in op.path.split("/") if s]
    if not segments:
        raise ValueError(f"empty UiPatchOp.path: {op.path!r}")
    top, rest = segments[0], segments[1:]

    if top == "blocks":
        if rest:
            _apply_tree_op(state.blocks, rest, op)
        elif op.op == "set":
            state.blocks = dict(_jsonable(op.value) or {})
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


class FakeLogger:
    """A trivial stand-in for a bound `structlog` logger: records, never raises."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str, dict[str, Any]]] = []

    def _record(self, level: str, event: str, **kwargs: Any) -> None:
        self.entries.append((level, event, kwargs))

    def debug(self, event: str, **kwargs: Any) -> None:
        self._record("debug", event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        self._record("info", event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._record("warning", event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._record("error", event, **kwargs)

    def bind(self, **kwargs: Any) -> FakeLogger:
        return self


class FakeUiChannel:
    """In-memory `packs.base.UiChannel`: mutates `.state` and records every op."""

    def __init__(self) -> None:
        self.seq = 0
        self.state = UiState()
        self.patches: list[list[UiPatchOp]] = []
        self.snapshots_sent = 0
        self.ui_requests: list[tuple[str, dict[str, Any]]] = []
        self.assets_pushed: list[tuple[bytes, str, str]] = []
        self.block_calls: list[tuple[str, str]] = []
        """`(method, block_id)` for every v2 block helper call."""
        self.form_requests: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        """`(block_id, schema, prefill)` for every `request_form`."""
        self.form_responses: dict[str, dict[str, Any] | None] = {}
        """What `request_form` returns per block id (default `None`: cancelled/timed out)."""

    async def patch(self, ops: list[UiPatchOp]) -> None:
        self.seq += 1
        self.patches.append(ops)
        for op in ops:
            apply_patch_op(self.state, op)

    async def snapshot(self) -> None:
        self.snapshots_sent += 1

    async def set_status(self, label: str, tone: Tone) -> None:
        await self.patch([UiPatchOp(op="set", path="/status", value=StatusStamp(label=label, tone=tone))])

    async def add_note(self, text: str, kind: str = "note", key: str | None = None) -> None:
        note = Note(id=str(uuid.uuid4()), text=text, kind=kind, ts=time.time(), key=key)
        op = "upsert" if key else "append"
        await self.patch([UiPatchOp(op=op, path="/notes", value=note, key=key)])

    async def set_checklist(self, items: list[ChecklistItem]) -> None:
        await self.patch([UiPatchOp(op="set", path="/checklist", value=items)])

    async def push_asset(
        self,
        data: bytes,
        mime: str,
        kind: str,
        caption: str | None = None,
        meta: dict[str, str] | None = None,
    ) -> str:
        asset_id = str(uuid.uuid4())
        self.assets_pushed.append((data, mime, kind))
        ref = AssetRef(
            asset_id=asset_id, kind=kind, mime=mime, caption=caption, meta=meta or {}, ts=time.time()
        )
        await self.patch([UiPatchOp(op="append", path="/assets", value=ref)])
        return asset_id

    async def activity(self, event: ActivityEvent) -> None:
        await self.patch([UiPatchOp(op="upsert", path="/activity", value=event, key=event.id)])

    async def request_ui(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.ui_requests.append((method, payload))
        return {"ok": True}

    # --- v2 panel blocks (CONTRACTS-V2 §4.4; asks #67) ---------------------------

    async def set_block(self, block_id: str, state: dict[str, Any]) -> None:
        self.block_calls.append(("set_block", block_id))
        await self.patch([UiPatchOp(op="set", path=block_path(block_id), value=_jsonable(state))])

    async def patch_block(self, block_id: str, ops: list[UiPatchOp]) -> None:
        self.block_calls.append(("patch_block", block_id))
        if ops:
            await self.patch([op.model_copy(update={"path": block_path(block_id, op.path)}) for op in ops])

    async def request_form(
        self,
        block_id: str,
        schema: dict[str, Any],
        prefill: dict[str, Any] | None = None,
        timeout_s: float = 120,
    ) -> dict[str, Any] | None:
        self.block_calls.append(("request_form", block_id))
        values = dict(prefill or {})
        self.form_requests.append((block_id, schema, values))
        state = {"schema": schema, "values": values, "status": "requested", "submitted_at": None}
        await self.patch([UiPatchOp(op="set", path=block_path(block_id), value=state)])
        return self.form_responses.get(block_id)

    async def cite(self, block_id: str, hits: list[KbHit]) -> None:
        self.block_calls.append(("cite", block_id))
        items = [
            KbCitation(chunk_id=h.chunk_id, filename=h.filename, score=h.score, text=h.text) for h in hits
        ]
        await self.patch([UiPatchOp(op="set", path=block_path(block_id, "items"), value=items)])


class FakeFrameBuffer:
    """In-memory `packs.base.FrameBufferProto`. Tests seed frames via `set_latest`."""

    def __init__(self) -> None:
        self._latest: dict[str, FrameSnapshot] = {}
        self._jpeg: dict[str, bytes] = {}

    def set_latest(self, snapshot: FrameSnapshot, jpeg_bytes: bytes = b"\xff\xd8\xff\xd9") -> None:
        self._latest[snapshot.source] = snapshot
        self._jpeg[snapshot.source] = jpeg_bytes

    def clear(self) -> None:
        self._latest.clear()
        self._jpeg.clear()

    def latest(self, max_age_s: float | None = None) -> FrameSnapshot | None:
        candidates = [s for s in self._latest.values() if max_age_s is None or s.age_s <= max_age_s]
        if not candidates:
            return None
        return min(candidates, key=lambda s: s.age_s)

    async def latest_jpeg(
        self, max_age_s: float | None = None, max_width: int = 1024
    ) -> tuple[bytes, FrameSnapshot] | None:
        snap = self.latest(max_age_s)
        if snap is None:
            return None
        return self._jpeg[snap.source], snap


class FakeKbClient:
    """In-memory `packs.base.KbClient`. Tests seed hits via `.hits`."""

    def __init__(self, hits: list[KbHit] | None = None) -> None:
        self.hits = hits or []
        self.queries: list[tuple[str, int, list[str] | None]] = []

    async def search(self, query: str, k: int = 4, kb_ids: list[str] | None = None) -> list[KbHit]:
        self.queries.append((query, k, kb_ids))
        return self.hits[:k]


class FakeStructuredLLM:
    """In-memory `packs.base.StructuredLLM`. Tests queue responses via `.responses`."""

    def __init__(self, responses: list[BaseModel] | None = None) -> None:
        self.responses: list[BaseModel] = list(responses or [])
        self.calls: list[tuple[str, str, type[BaseModel]]] = []

    async def extract(
        self, *, instructions: str, input_text: str, schema: type[BaseModel], timeout_s: float = 45
    ) -> BaseModel:
        self.calls.append((instructions, input_text, schema))
        if not self.responses:
            raise AssertionError("FakeStructuredLLM.extract called with no queued response")
        return self.responses.pop(0)


class FakeImageGen:
    """In-memory `packs.base.ImageGen`. Tests configure `.image_bytes`/`.mime`/`.error`."""

    def __init__(self, image_bytes: bytes = b"\x89PNG", mime: str = "image/png") -> None:
        self.image_bytes = image_bytes
        self.mime = mime
        self.prompts: list[str] = []
        self.error: Exception | None = None

    async def generate(self, prompt: str, *, timeout_s: float = 40) -> tuple[bytes, str]:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.image_bytes, self.mime


class FakeBackgroundRunner:
    """In-memory `packs.base.BackgroundRunner`.

    `submit()` schedules `coro` as a real `asyncio.Task` so tool code that
    fires-and-forgets background work behaves like production; tests
    `await wait_idle()` to let it settle, then assert on `.urgent_events` /
    `.routine_notes` / `.on_result_calls`. `.submitted` records `(job_id,
    name)` for every call -- `name` is what a real `BackgroundToolRunner`
    would use as both `ActivityEvent.source` and (via `_label()`) `.label`,
    since `submit()` has no separate `label` parameter.
    """

    def __init__(self) -> None:
        self.jobs: dict[str, asyncio.Task[Any]] = {}
        self.cancelled: list[str] = []
        self.submitted: list[tuple[str, str]] = []
        self.on_result_calls: list[tuple[str, Any]] = []
        self.urgent_events: list[tuple[str, Any, str | None]] = []
        self.routine_notes: list[tuple[str, str]] = []

    def submit(
        self,
        *,
        name: str,
        coro: Awaitable[Any],
        on_result: Callable[[Any], Awaitable[None]] | None = None,
        urgent: Callable[[Any], bool] | None = None,
        urgent_instructions: Callable[[Any], str] | None = None,
        routine_note: Callable[[Any], str | None] | None = None,
        call_id: str | None = None,
    ) -> str:
        job_id = call_id or str(uuid.uuid4())
        self.submitted.append((job_id, name))

        async def _run() -> None:
            result = await coro
            if on_result is not None:
                await on_result(result)
                self.on_result_calls.append((job_id, result))
            if urgent is not None and urgent(result):
                instructions = urgent_instructions(result) if urgent_instructions else None
                self.urgent_events.append((job_id, result, instructions))
            elif routine_note is not None:
                note = routine_note(result)
                if note is not None:
                    self.routine_notes.append((job_id, note))

        self.jobs[job_id] = asyncio.ensure_future(_run())
        return job_id

    def cancel(self, job_id: str) -> None:
        task = self.jobs.get(job_id)
        if task is not None and not task.done():
            task.cancel()
        self.cancelled.append(job_id)

    async def wait_idle(self) -> None:
        """Test helper: await every still-pending job."""
        pending = [t for t in self.jobs.values() if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


class _FakeAgentSession:
    """A minimal, non-Protocol stand-in for `AgentSession` covering the calls
    pack/tool code commonly makes.
    """

    def __init__(self) -> None:
        self.said: list[str] = []
        self.replies_generated: list[str] = []
        self.chat_ctx = ChatContext.empty()

    async def say(self, text: str, **kwargs: Any) -> None:
        self.said.append(text)

    async def generate_reply(self, *, instructions: str | None = None, **kwargs: Any) -> None:
        self.replies_generated.append(instructions or "")

    async def update_chat_ctx(self, chat_ctx: ChatContext) -> None:
        self.chat_ctx = chat_ctx


#: Distinguishes "caller omitted `image_gen`" (default to a `FakeImageGen()`)
#: from "caller explicitly passed `image_gen=None`" (no image-gen credential
#: configured, per `PackSessionContext.image_gen: ImageGen | None` -- a real,
#: reachable state a pack must degrade gracefully for). Plain `None` can't
#: serve as that default itself: it is the very value a test needs to pass
#: through unchanged.
_UNSET: Any = object()


class FakePackSessionContext:
    """In-memory `packs.base.PackSessionContext` composing every fake above.

    `room` defaults to `None` here (the agent shim substitutes a `FakeRoom`);
    `log` defaults to a recording :class:`FakeLogger`.
    """

    def __init__(
        self,
        *,
        session_id: str = "sess-test",
        agent_id: str = "agent-test",
        pipeline_mode: PipelineMode = "cascaded",
        config: AgentConfig | None = None,
        pack_settings: dict[str, Any] | None = None,
        session: AgentSession[Any] | None = None,
        room: Any | None = None,
        ui: FakeUiChannel | None = None,
        frames: FakeFrameBuffer | None = None,
        kb: FakeKbClient | None = None,
        workflow_llm: FakeStructuredLLM | None = None,
        image_gen: FakeImageGen | None = _UNSET,
        background: FakeBackgroundRunner | None = None,
        log: Any | None = None,
    ) -> None:
        self.session_id = session_id
        self.agent_id = agent_id
        self.pipeline_mode = pipeline_mode
        self.config = config or default_agent_config()
        self.pack_settings = pack_settings or {}
        self.session = session or cast(AgentSession[Any], _FakeAgentSession())
        self.room = room
        self.ui = ui or FakeUiChannel()
        self.frames = frames or FakeFrameBuffer()
        self.kb = kb or FakeKbClient()
        self.workflow_llm = workflow_llm or FakeStructuredLLM()
        self.image_gen: FakeImageGen | None = FakeImageGen() if image_gen is _UNSET else image_gen
        self.background = background or FakeBackgroundRunner()
        self.log: Any = log if log is not None else FakeLogger()
        self.userdata: dict[str, Any] = {}
        self.events: list[tuple[str, dict[str, Any]]] = []

    def record_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Capture a session event (D-W3-1) instead of posting it to the api."""
        self.events.append((event_type, payload))


if TYPE_CHECKING:  # mypy --strict proves every fake still satisfies its `packs.base` Protocol.
    from packs.base import (
        BackgroundRunner,
        FrameBufferProto,
        ImageGen,
        KbClient,
        StructuredLLM,
        UiChannel,
    )

    _ui: UiChannel = FakeUiChannel()
    _frames: FrameBufferProto = FakeFrameBuffer()
    _kb: KbClient = FakeKbClient()
    _llm: StructuredLLM = FakeStructuredLLM()
    _image: ImageGen = FakeImageGen()
    _background: BackgroundRunner = FakeBackgroundRunner()
    # `FakePackSessionContext` itself is not assignable to `PackSessionContext`:
    # its attributes are the concrete fakes, and Protocol attributes are invariant.
