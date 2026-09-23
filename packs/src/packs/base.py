"""Pack interface and runtime Protocols — docs/CONTRACTS.md §8, verbatim.

Packs import only `lkap_contracts`, `livekit.agents`/`livekit.rtc` types and
`packs.base`. The worker (`lkap_agent`) implements these Protocols;
`agent -> packs -> contracts` is the only allowed dependency direction —
packs never import `lkap_agent`.

- `packs/<id>/manifest.py` exposes `MANIFEST: PackManifest` (pure Pydantic,
  importable by `lkap_api` without a `livekit` import).
- `packs/<id>/pack.py` exposes `PACK: Pack` (imported only by the worker);
  `PACK.manifest is MANIFEST`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from livekit import rtc
from livekit.agents import AgentSession, ChatContext, ChatMessage, llm
from lkap_contracts.agent_config import AgentConfig, PipelineMode
from lkap_contracts.api_models import KbHit
from lkap_contracts.packs import PackManifest, ToolMeta
from lkap_contracts.ui_protocol import ActivityEvent, ChecklistItem, Tone, UiPatchOp, UiState
from pydantic import BaseModel

__all__ = [
    "BackgroundRunner",
    "BlockActionPack",
    "DtmfPack",
    "FrameBufferProto",
    "FrameSnapshot",
    "ImageGen",
    "KbClient",
    "Pack",
    "PackSessionContext",
    "StructuredLLM",
    "ToolMeta",
    "UiChannel",
]

FrameSource = Literal["camera", "screen"]


@dataclass
class FrameSnapshot:
    """The freshest decoded video frame for one track source."""

    frame: rtc.VideoFrame
    source: FrameSource
    age_s: float


class UiChannel(Protocol):
    """Agent-side handle to the `lkap.ui.*` topics/RPCs (docs/CONTRACTS.md §10)."""

    seq: int
    state: UiState  # current envelope (mutable copy)

    async def patch(self, ops: list[UiPatchOp]) -> None:
        """Send a `UiPatch` with `ops`, applying them to `state` and bumping `seq`."""
        ...

    async def snapshot(self) -> None:
        """Send a full `UiSnapshot` of the current `state`."""
        ...

    async def set_status(self, label: str, tone: Tone) -> None:
        """Patch `/status` to `StatusStamp(label=label, tone=tone)`."""
        ...

    async def add_note(self, text: str, kind: str = "note", key: str | None = None) -> None:
        """Append (or, with `key`, upsert) a `Note` onto `/notes`."""
        ...

    async def set_checklist(self, items: list[ChecklistItem]) -> None:
        """Replace `/checklist`."""
        ...

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
        ...

    async def activity(self, event: ActivityEvent) -> None:
        """Record an `ActivityEvent` (same `id` replaces an earlier entry; last 30 kept)."""
        ...

    async def request_ui(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        """RPC `lkap.ui.request` (agent -> UI); returns the UI's response payload."""
        ...

    # --- v2 panel blocks (CONTRACTS-V2 §4.4) ------------------------------------

    async def set_block(self, block_id: str, state: dict[str, Any]) -> None:
        """Replace the state of block `block_id` (`set /blocks/<block_id>`).

        `state` is validated against the block type's state model when the block
        is part of the session's panel layout.
        """
        ...

    async def patch_block(self, block_id: str, ops: list[UiPatchOp]) -> None:
        """Apply `ops` to block `block_id` in one `UiPatch`.

        Each op's `path` is relative to the block (`"/rows"` becomes
        `"/blocks/<block_id>/rows"`; `""` or `"/"` is the block itself).
        """
        ...

    async def request_form(
        self,
        block_id: str,
        schema: dict[str, Any],
        prefill: dict[str, Any] | None = None,
        timeout_s: float = 120,
    ) -> dict[str, Any] | None:
        """Show a JSON-schema form in block `block_id` and wait for the user.

        Returns the submitted values, or `None` when the user cancels, the
        timeout expires, or the session closes first.
        """
        ...

    async def cite(self, block_id: str, hits: list[KbHit]) -> None:
        """Replace the citations shown by `kb_citations` block `block_id`."""
        ...


class FrameBufferProto(Protocol):
    """Latest-frame-per-source buffer used for pinning and cascaded-mode vision inject."""

    def latest(self, max_age_s: float | None = None) -> FrameSnapshot | None:
        """The freshest `FrameSnapshot` across sources, or `None` if none is fresh enough."""
        ...

    async def latest_jpeg(
        self, max_age_s: float | None = None, max_width: int = 1024
    ) -> tuple[bytes, FrameSnapshot] | None:
        """JPEG-encode the freshest frame, or `None` if none is fresh enough."""
        ...


class KbClient(Protocol):
    """Agent-side handle to `/internal/v1/kb/search`."""

    async def search(self, query: str, k: int = 4, kb_ids: list[str] | None = None) -> list[KbHit]:
        """Search the agent's attached knowledge bases (or `kb_ids` if given)."""
        ...


class StructuredLLM(Protocol):
    """One JSON-extraction LLM call: prompt-for-JSON + Pydantic parse + one repair retry."""

    async def extract(
        self, *, instructions: str, input_text: str, schema: type[BaseModel], timeout_s: float = 45
    ) -> BaseModel:
        """Return a validated instance of `schema` extracted from `input_text`."""
        ...


class BackgroundRunner(Protocol):
    """Runs "non-blocking" tool work in a session-scoped background task.

    Result delivery: the UI topic always gets an `ActivityEvent`; the
    conversation gets `reply_required` (realtime, via `urgent`/
    `urgent_instructions`) or `update_chat_ctx`/`generate_reply` (cascaded,
    via `routine_note`) — see docs/ARCHITECTURE.md D10.
    """

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
        """Schedule `coro` and return a job id."""
        ...

    def cancel(self, job_id: str) -> None:
        """Cancel a still-running job; a no-op if it already finished."""
        ...


class ImageGen(Protocol):
    """Image-generation provider (Google/OpenAI), behind one async call."""

    async def generate(self, prompt: str, *, timeout_s: float = 40) -> tuple[bytes, str]:
        """Return `(image_bytes, mime_type)`."""
        ...


class PackSessionContext(Protocol):
    """Everything a `Pack` needs for one session; implemented by the worker."""

    session_id: str
    agent_id: str
    pipeline_mode: PipelineMode
    config: AgentConfig
    pack_settings: dict[str, Any]
    session: AgentSession[Any]
    room: rtc.Room
    ui: UiChannel
    frames: FrameBufferProto
    kb: KbClient
    workflow_llm: StructuredLLM
    image_gen: ImageGen | None
    background: BackgroundRunner
    log: Any  # structlog bound logger
    userdata: dict[str, Any]  # pack-private per-session store

    def record_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Records a session event on the api timeline (best-effort, never raises).

        Packs may emit `escalation` and `info`, or pack-prefixed custom types;
        platform types are reserved (CONTRACTS §7).
        """
        ...


class Pack(Protocol):
    """A use-case pack: tools, hooks, initial UI state, and its `PackManifest`."""

    manifest: PackManifest

    def tools(self, ctx: PackSessionContext) -> list[llm.FunctionTool[..., Any]]:
        """Ordinary `@function_tool` closures over `ctx`."""
        ...

    def tool_meta(self) -> list[ToolMeta]:
        """Metadata for every tool `tools()` can return, independent of `ctx`."""
        ...

    def initial_state(self, ctx: PackSessionContext) -> dict[str, Any]:
        """The initial value of `UiState.custom`."""
        ...

    async def on_session_start(self, ctx: PackSessionContext) -> None:
        """Called once the session is ready to push its first UI snapshot."""
        ...

    async def on_user_turn_completed(
        self, ctx: PackSessionContext, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        """Called after each completed user turn (KB/vision injection already applied)."""
        ...

    async def on_agent_turn_completed(self, ctx: PackSessionContext, text: str, interrupted: bool) -> None:
        """Called after each completed assistant turn."""
        ...

    async def on_ui_action(
        self, ctx: PackSessionContext, action: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle `AgentAction(action="ui_action")` from the UI; returns the RPC payload."""
        ...

    async def on_session_end(self, ctx: PackSessionContext, reason: str) -> None:
        """Called once, right before the session tears down."""
        ...


class BlockActionPack(Protocol):
    """The optional `on_block_action` hook a `Pack` may add (CONTRACTS-V2 §4.4).

    Kept out of `Pack` itself so every existing pack still satisfies `Pack`
    structurally: the worker looks the method up with `getattr`, and a pack
    that does not define it gets the default no-op (the action is acknowledged
    with an empty payload).
    """

    async def on_block_action(
        self, ctx: PackSessionContext, block_id: str, name: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle `AgentAction(action="block_action", payload={block_id, name, data})`.

        Returns the RPC result payload for the browser.
        """
        ...


class DtmfPack(Protocol):
    """The optional ``on_dtmf`` hook a `Pack` may add (V2-17; moved here by R-V2-25).

    Like `BlockActionPack`, it is kept out of `Pack` so every existing pack
    still satisfies `Pack` structurally; the worker looks the method up with
    `getattr`. It runs on phone calls only, once per keypad entry (digits
    buffered until ``#`` or a 2.5 s pause), before the entry reaches the model.
    """

    async def on_dtmf(self, ctx: PackSessionContext, digits: str) -> bool:
        """Handle a keypad entry; return ``True`` to consume it (the model never sees it)."""
        ...
