"""The single `Agent` subclass the worker runs (docs/ARCHITECTURE.md §7–§10).

`PlatformAgent` is where platform behaviour and pack behaviour meet:

* composes the system prompt (agent instructions + pack mode addendum +
  platform pipeline notes),
* carries the merged tool list (built-in, declarative, pack),
* injects knowledge-base results and, in cascaded mode, one camera/screen frame
  per user turn before delegating to the pack hook,
* speaks the greeting on enter, choosing `say()` or `generate_reply()` by what
  the configured pipeline can actually do (ARCHITECTURE §15.9),
* cancels the model's tool reply for tools a pack marked `silent_reply`
  (and for the built-in `request_form` on realtime models, D-W2-9i),
* seeds the v2 panel blocks (`UiState.blocks`) from `AgentConfig.panel` and
  the pack's `default_panel`, and binds the block callbacks on the UI channel:
  `block_action` → the pack's optional `on_block_action`, a form submitted
  after its tool stopped waiting → a reply, and `block_update` /
  `form_submitted` session events (CONTRACTS-V2 §4.4).

`SessionContext` is the worker's concrete `packs.base.PackSessionContext`; it is
built here because everything a pack needs is already assembled at this point.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from livekit import rtc
from livekit.agents import (
    Agent,
    AgentSession,
    ChatContext,
    ChatMessage,
    ConversationItemAddedEvent,
    FunctionToolsExecutedEvent,
    StopResponse,
    ToolExecutionUpdatedEvent,
)
from livekit.agents import llm as lk_llm
from lkap_contracts.agent_config import AgentConfig, PipelineMode, ResolvedProvider
from lkap_contracts.api_models import KbHit
from lkap_contracts.common import SessionChannel
from lkap_contracts.packs import PackManifest
from lkap_contracts.providers import ModelCapabilities, vision_support
from lkap_contracts.ui_protocol import ActivityEvent
from packs.base import (
    BackgroundRunner,
    FrameBufferProto,
    ImageGen,
    KbClient,
    Pack,
    StructuredLLM,
    UiChannel,
)

from lkap_agent.logging import get_logger
from lkap_agent.tools.execution import (
    FLOW_BACKGROUND_MIN_SDK,
    ResolvedExecution,
    ToolActivityFeed,
    bind_agent_policy,
    flow_mode_of,
    policies_for,
    policy_of,
    register_policies,
    resolve_execution,
    wrap_tool,
)
from lkap_agent.ui.blocks import block_ids_of_type, initial_block_states, resolve_block_specs
from lkap_agent.vision import encode_jpeg_data_url

__all__ = [
    "GreetingMode",
    "PlatformAgent",
    "SessionContext",
    "compose_instructions",
    "model_vision_support",
    "platform_text_input_cb",
    "resolve_greeting_mode",
]

logger = get_logger(__name__)

GreetingMode = Literal["say", "generate"]

#: What "still in progress" means for a tool on the SDK's async-tool executor
#: (docs/v4/BACKGROUND-TOOLS.md §4.2, D-V4-33); appended to every pipeline note.
IN_PROGRESS_NOTE: Final[str] = (
    "Some tools report progress first and finish later; when a tool output says it is "
    "still in progress, tell the user it is on its way and never invent the result."
)

#: Appended to the system prompt so the model's tool etiquette matches what the
#: pipeline can express (docs/ARCHITECTURE.md §7.2).
PIPELINE_NOTES: Final[dict[PipelineMode, str]] = {
    "cascaded": (
        "Pipeline notes: you are speaking through a cascaded voice pipeline. "
        "Every tool result comes back to you and you will voice a reply, so keep "
        'tool acknowledgements to one short clause ("Checking that now.") and '
        f"never read raw tool output aloud. {IN_PROGRESS_NOTE}"
    ),
    "realtime": (
        "Pipeline notes: you are a realtime speech model. Some tools finish in the "
        "background and return nothing; stay silent after those and continue the "
        "conversation naturally. Results will appear in your context when ready. "
        f"{IN_PROGRESS_NOTE}"
    ),
    "half_cascade": (
        "Pipeline notes: you hear the user directly and your text replies are spoken "
        "by a separate voice, so write the way you would talk: no markdown, lists or "
        "symbols. Some tools finish in the background and return nothing; stay silent "
        "after those and continue the conversation naturally. Results will appear in "
        f"your context when ready. {IN_PROGRESS_NOTE}"
    ),
}

#: Pipeline modes whose conversational model is a `RealtimeModel`.
_REALTIME_MODEL_MODES: Final[frozenset[PipelineMode]] = frozenset({"realtime", "half_cascade"})

#: Longest side of an injected frame (DECISIONS-W2 D-W2-8 R3).
_VISION_MAX_PX: Final[int] = 512

#: Upper bound on the per-turn knowledge search; a slower api must not delay the reply.
_KB_INJECT_TIMEOUT_S: Final[float] = 3.0

#: How the retrieved knowledge is framed for the model.
_KB_PREFIX: Final[str] = "Relevant knowledge from the attached documents:"

#: Built-in tools whose reply realtime models skip (D-W2-9i): `request_form`
#: returns `None` and its result arrives later as a background result.
_REALTIME_SILENT_BUILTINS: Final[frozenset[str]] = frozenset({"request_form"})

#: `SessionContext.userdata` key: the flow-node downgrade was already recorded (R-V4-39).
_FLOW_DOWNGRADE_KEY: Final[str] = "_lkap_flow_background_downgrade_reported"


def compose_instructions(
    base: str,
    *,
    mode: PipelineMode,
    manifest: PackManifest | None = None,
) -> str:
    """Build the full system prompt for one session.

    Args:
        base: `AgentConfig.instructions`, authored in the console.
        mode: The pipeline mode, selecting the pack addendum and platform note.
        manifest: The pack manifest, for `instructions_by_mode`.

    Returns:
        The composed prompt: agent instructions, then the pack's mode addendum,
        then the platform pipeline note.
    """
    blocks = [base.strip()]
    if manifest is not None:
        addendum = manifest.instructions_by_mode.get(mode, "").strip()
        if addendum:
            blocks.append(addendum)
    blocks.append(PIPELINE_NOTES[mode])
    return "\n\n".join(b for b in blocks if b)


def resolve_greeting_mode(configured: GreetingMode, *, has_tts: bool) -> GreetingMode:
    """Pick the greeting mechanism the running pipeline actually supports.

    `AgentActivity.say()` raises `RuntimeError` when the session has no TTS
    unless the realtime model advertises `capabilities.supports_say`. Neither
    `livekit-plugins-google` nor `livekit-plugins-openai` 1.8.2 sets that flag,
    so a realtime session without a TTS must greet through `generate_reply`.

    Args:
        configured: `AgentConfig.voice.greeting_mode`.
        has_tts: Whether a TTS was resolved for this session.

    Returns:
        `"say"` only when a TTS exists; `"generate"` otherwise.
    """
    if configured == "say" and not has_tts:
        logger.info("greeting falls back to generate_reply: no TTS in this pipeline")
        return "generate"
    return configured


def _noop_record_event(event_type: str, payload: dict[str, Any]) -> None:
    """Default `SessionContext.record_event`: drops the event (no observer attached)."""
    del event_type, payload


@dataclass(slots=True)
class SessionContext:
    """The worker's `packs.base.PackSessionContext` for one job."""

    session_id: str
    agent_id: str
    pipeline_mode: PipelineMode
    config: AgentConfig
    session: AgentSession[Any]
    room: rtc.Room
    ui: UiChannel
    frames: FrameBufferProto
    kb: KbClient
    workflow_llm: StructuredLLM
    background: BackgroundRunner
    log: Any
    image_gen: ImageGen | None = None
    pack_settings: dict[str, Any] = field(default_factory=dict)
    userdata: dict[str, Any] = field(default_factory=dict)
    record_event: Callable[[str, dict[str, Any]], None] = field(default=_noop_record_event)
    #: The session's channel. Not part of `PackSessionContext`: built-in tools read
    #: it with `getattr(ctx, "channel", "web")` (e.g. `request_form` on `text`, asks #30).
    channel: SessionChannel = "web"
    #: Ends the job through the worker's own context (`end_call` prefers it over
    #: `get_job_context().shutdown`, so a text session posts its summary at once,
    #: asks #33). `None` outside a worker job.
    request_shutdown: Callable[[str], None] | None = None
    llm_capabilities: ModelCapabilities | None = None
    """What the api resolved about the cascaded LLM (``ResolvedProvider.capabilities``, V4-08)."""


class PlatformAgent(Agent):
    """The configured agent: instructions, tools, retrieval, vision and hooks."""

    def __init__(
        self,
        *,
        ctx: SessionContext,
        pack: Pack,
        tools: list[lk_llm.Tool | lk_llm.Toolset] | None = None,
        mcp_servers: list[Any] | None = None,
        mcp_toolsets: list[Any] | None = None,
        has_tts: bool,
        vision_max_frame_age_s: float = 8.0,
        record_event: Callable[[str, dict[str, Any]], None] | None = None,
        instructions: str | None = None,
        agent_options: dict[str, Any] | None = None,
    ) -> None:
        """Create the agent for one session.

        Args:
            ctx: The assembled pack session context.
            pack: The loaded pack (`NULL_PACK` when none is installed).
            tools: Built-in + declarative + pack tools, already merged.
            mcp_servers: Deprecated name for ``mcp_toolsets`` (the worker's builder,
                `declarative.build_mcp_servers`, now returns `MCPToolset`s).
            mcp_toolsets: `MCPToolset`s from `declarative.build_mcp_toolsets`; they go
                into `Agent(tools=...)` (livekit-agents 1.8.2 deprecates the `mcp_servers` argument
                and would build its toolsets without LKAP's per-tool options).
            has_tts: Whether the pipeline resolved a TTS (drives the greeting).
            vision_max_frame_age_s: Frames older than this are not injected.
            record_event: Records a session event (the worker passes
                `SessionObserver.record`); used for the one-off `info` event
                when per-turn vision is skipped on a text-only model.
            instructions: Hook point for flow nodes (V2-15): the fully composed
                system prompt, used instead of composing one from
                `AgentConfig.instructions`.
            agent_options: Hook point for flow nodes (V2-15): extra
                `livekit.agents.Agent` constructor kwargs (`id`, `chat_ctx`,
                `llm`, `tts`, `turn_handling`).
        """
        self._ctx = ctx
        self._pack = pack
        self._vision_max_frame_age_s = vision_max_frame_age_s
        self._last_turn_had_frame = False
        self._vision_disabled = False
        self._record_event = record_event
        self._vision_skip_reported = False
        # Strong refs for fire-and-forget `on_agent_turn_completed` tasks (asyncio keeps weak ones).
        self._hook_tasks: set[asyncio.Task[None]] = set()
        # D-W2-10 / D-V4-24: the api's resolved capabilities first, then the registry; None = unknown.
        self._model_vision = model_vision_support(
            ctx.config, capabilities=getattr(ctx, "llm_capabilities", None)
        )
        silent = {meta.name for meta in pack.tool_meta() if meta.silent_reply}
        if ctx.pipeline_mode in _REALTIME_MODEL_MODES and getattr(ctx, "channel", "web") != "text":
            # On the text channel `request_form` answers at once with a line the
            # model must act on (asks #30), so its reply is never suppressed there.
            silent |= _REALTIME_SILENT_BUILTINS
        self._silent_reply_tools = frozenset(silent)
        self._greeting_mode = resolve_greeting_mode(ctx.config.voice.greeting_mode, has_tts=has_tts)
        if instructions is None:
            instructions = compose_instructions(
                ctx.config.instructions,
                mode=ctx.pipeline_mode,
                manifest=pack.manifest,
            )
        toolsets = [*(mcp_toolsets or []), *(mcp_servers or [])]
        final_tools = self._apply_execution_policy(list(tools or []), pack)
        policies = register_policies(ctx.session, [*final_tools, *toolsets])
        self._report_flow_downgrade(policies)
        # Strong refs for the activity feed's UI sends, chained so rows arrive in order.
        self._activity_tail: asyncio.Task[None] | None = None
        self._activity_feed = ToolActivityFeed(
            emit=self._send_activity, policies=lambda: policies_for(self._ctx.session)
        )
        super().__init__(
            instructions=instructions,
            tools=[*final_tools, *toolsets],
            **(agent_options or {}),
        )
        self._init_blocks()

    # ------------------------------------------------------------- tool policy

    def _apply_execution_policy(
        self, tools: list[lk_llm.Tool | lk_llm.Toolset], pack: Pack
    ) -> list[lk_llm.Tool | lk_llm.Toolset]:
        """Bind declarative tools to this agent's read-tool default; wrap opted-in pack tools.

        Declarative HTTP tools are built before the agent's
        ``tools.execution_default`` and flow-ness reach the builder, so they are
        rebuilt here (`execution.bind_agent_policy`). A pack tool runs through
        `run_with_policy` only when its `ToolMeta.execution` is set; every other
        pack tool is passed on untouched (BACKGROUND-TOOLS.md §4.2).
        """
        config = self._ctx.config
        on_flow = flow_mode_of(config)
        bound = bind_agent_policy(tools, execution_default=config.tools.execution_default, flow_node=on_flow)
        opted = {meta.name: meta for meta in pack.tool_meta() if meta.execution is not None}
        if not opted:
            return bound
        out: list[lk_llm.Tool | lk_llm.Toolset] = []
        for tool in bound:
            name = getattr(getattr(tool, "info", None), "name", None)
            meta = opted.get(name) if isinstance(name, str) else None
            if meta is None or policy_of(tool) is not None:
                out.append(tool)
                continue
            resolved = resolve_execution(
                name=meta.name,
                kind="pack",
                is_read=False,
                declared=meta.execution,
                agent_default="blocking",
                flow_node=on_flow,
                label=meta.activity_label,
            )
            if meta.silent_reply and resolved.non_blocking:
                logger.warning(
                    "pack tool keeps blocking: silent_reply swallows its announcement", tool=meta.name
                )
                out.append(tool)
                continue
            out.append(wrap_tool(tool, resolved))
        return out

    def _report_flow_downgrade(self, policies: Mapping[str, ResolvedExecution]) -> None:
        """Record one `info` event per session when flow-node tools were kept blocking (R-V4-39)."""
        names = sorted(
            name for name, p in policies.items() if p.downgraded_from is not None and p.mode == "blocking"
        )
        if not names or not flow_mode_of(self._ctx.config):
            return
        userdata = getattr(self._ctx, "userdata", None)
        if not isinstance(userdata, dict) or userdata.get(_FLOW_DOWNGRADE_KEY):
            return
        userdata[_FLOW_DOWNGRADE_KEY] = True
        if self._record_event is not None:
            with contextlib.suppress(Exception):
                self._record_event(
                    "info",
                    {
                        "message": "background tools on flow nodes run blocking until the worker runs "
                        f"livekit-agents {FLOW_BACKGROUND_MIN_SDK}: {', '.join(names)}"
                    },
                )

    def on_tool_execution(self, ev: ToolExecutionUpdatedEvent) -> None:
        """Feed the activity block from the SDK's tool lifecycle (D-V4-38).

        Registered by the worker as a **synchronous** ``tool_execution_updated``
        handler; the UI send is scheduled as a task (like `on_conversation_item`),
        chained so the rows of one call arrive in order. Never raises.
        """
        try:
            self._activity_feed.handle(ev)
        except Exception:
            logger.debug("tool activity feed failed", exc_info=True)

    def _send_activity(self, event: ActivityEvent) -> None:
        previous = self._activity_tail

        async def _send() -> None:
            if previous is not None:
                with contextlib.suppress(BaseException):
                    await previous
            try:
                await self._ctx.ui.activity(event)
            except Exception:
                logger.debug("could not send a tool activity row", exc_info=True)

        task = asyncio.create_task(_send())
        self._activity_tail = task
        self._hook_tasks.add(task)
        task.add_done_callback(self._hook_tasks.discard)

    # ----------------------------------------------------------------- blocks

    def _init_blocks(self) -> None:
        """Seed `UiState.blocks` and bind the channel's block callbacks.

        Runs in the constructor, i.e. before `on_enter`, so both the pack's
        `on_session_start` and the seq-1 platform snapshot see the blocks
        (D-W2-9a keeps its order: nothing is sent here). A channel without
        v2 support (the worker's no-op channel, v1 test doubles) just gets
        `state.blocks` assigned.
        """
        specs = resolve_block_specs(self._ctx.config.panel, self._pack.manifest)
        ui = self._ctx.ui
        init_blocks = getattr(ui, "init_blocks", None)
        try:
            if callable(init_blocks):
                init_blocks(specs)
            elif not ui.state.blocks:
                ui.state.blocks = initial_block_states(specs)
        except Exception:
            logger.warning("panel blocks could not be initialised", exc_info=True)
        bind = getattr(ui, "bind", None)
        if callable(bind):
            bind(
                on_block_action=self._on_block_action,
                on_unsolicited_form=self._on_unsolicited_form,
                record_event=self._ctx.record_event,
            )
        logger.debug(
            "panel blocks initialised",
            panel_id=self._ctx.config.panel.panel_id,
            blocks=[f"{spec.id}:{spec.type}" for spec in specs],
        )

    async def _on_block_action(self, block_id: str, name: str, data: dict[str, Any]) -> dict[str, Any]:
        """`block_action` → the pack's optional `on_block_action` (default no-op).

        `on_block_action` is not part of the structural `Pack` Protocol (see
        `packs.base.BlockActionPack`), so it is looked up here.
        """
        handler = getattr(self._pack, "on_block_action", None)
        if not callable(handler):
            return {}
        result = await handler(self._ctx, block_id, name, data)
        return dict(result or {})

    async def _on_unsolicited_form(self, block_id: str, values: dict[str, Any]) -> None:
        """A form arrived after its tool stopped waiting: let the model react to it."""
        instructions = (
            f"The user just submitted the {block_id} form with these values: {json.dumps(values)}. "
            "Acknowledge them briefly and continue."
        )
        try:
            self.session.generate_reply(instructions=instructions)
        except Exception:
            logger.warning("could not reply to a late form submission", block_id=block_id, exc_info=True)

    async def _cite(self, hits: list[KbHit]) -> None:
        """Implicit `cite_sources` for auto-injected knowledge (best effort)."""
        cite = getattr(self._ctx.ui, "cite", None)
        specs = getattr(self._ctx.ui, "block_specs", None)
        if not callable(cite) or not isinstance(specs, dict):
            return
        for block_id in block_ids_of_type(specs.values(), "kb_citations"):
            try:
                await cite(block_id, hits)
            except Exception:
                logger.debug("could not cite knowledge", block_id=block_id, exc_info=True)

    # ------------------------------------------------------------------ hooks

    async def on_enter(self) -> None:
        """Speak the greeting, then publish the initial UI state (DECISIONS-W2 D-W2-9a).

        Order: `UiState.custom = pack.initial_state(ctx)` -> `pack.on_session_start`
        -> platform `ui.snapshot()`. The platform snapshot goes last because the
        browser drops a snapshot whose `seq` is not newer than the last one it
        applied: if the pack already snapshotted, this one repeats the full state
        at the same seq (harmless); if it sent nothing, this one is seq 1.
        """
        self._speak_greeting()
        await self._publish_initial_ui()

    def _greeting_text(self) -> str:
        """The greeting to speak on enter (hook point: flow nodes render variables into it)."""
        return self._ctx.config.voice.greeting.strip()

    def _speak_greeting(self) -> None:
        """Speak the configured greeting, if any, with the pipeline's greeting mechanism."""
        greeting = self._greeting_text()
        # CONTRACTS-V2 §4.3: `first_speaker="user"` waits for the caller to speak first.
        if greeting and self._ctx.config.voice.first_speaker == "agent":
            if self._greeting_mode == "say":
                self.session.say(greeting)
            else:
                self.session.generate_reply(
                    instructions=f"Greet the user. Say exactly this and nothing more: {greeting}"
                )

    async def _publish_initial_ui(self) -> None:
        """Seed `UiState.custom`, run the pack's start hook, then snapshot (D-W2-9a order)."""
        try:
            self._ctx.ui.state.custom = dict(self._pack.initial_state(self._ctx))
        except Exception:
            logger.warning("pack initial_state failed", pack_id=self._pack.manifest.id, exc_info=True)
        try:
            await self._pack.on_session_start(self._ctx)
        except Exception:
            logger.warning("pack on_session_start failed", pack_id=self._pack.manifest.id, exc_info=True)
        try:
            await self._ctx.ui.snapshot()
        except Exception:
            logger.warning("initial ui snapshot failed", exc_info=True)

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        """Augment the turn with retrieval and vision, then run the pack hook.

        Order matters: knowledge lands in `turn_ctx` as an assistant note (the
        pattern LiveKit recommends for RAG), the frame is attached to
        `new_message` so the model sees it alongside what the user just said,
        and the pack hook runs last with both already in place.
        """
        await self._inject_knowledge(turn_ctx, new_message)
        await self._inject_vision(turn_ctx, new_message)
        try:
            await self._pack.on_user_turn_completed(self._ctx, turn_ctx, new_message)
        except Exception:
            logger.warning(
                "pack on_user_turn_completed failed", pack_id=self._pack.manifest.id, exc_info=True
            )

    def on_conversation_item(self, ev: ConversationItemAddedEvent) -> None:
        """Run the pack's `on_agent_turn_completed` hook for each assistant message.

        Registered by the worker as a **synchronous** `conversation_item_added`
        handler (livekit-agents 1.8.2 emits it once per message committed to the
        chat history, interrupted replies included). The hook is scheduled as a
        task so a slow pack never blocks the SDK's emit; user messages, handoffs
        and empty (e.g. tool-only) assistant messages are ignored.
        """
        item = ev.item
        if not isinstance(item, ChatMessage) or item.role != "assistant":
            return
        text = item.text_content
        if not text:
            return
        task = asyncio.create_task(self._run_agent_turn_hook(text, bool(item.interrupted)))
        self._hook_tasks.add(task)
        task.add_done_callback(self._hook_tasks.discard)

    async def _run_agent_turn_hook(self, text: str, interrupted: bool) -> None:
        try:
            await self._pack.on_agent_turn_completed(self._ctx, text, interrupted)
        except Exception:
            logger.warning(
                "pack on_agent_turn_completed failed", pack_id=self._pack.manifest.id, exc_info=True
            )

    # --------------------------------------------------------------- internals

    def _auto_inject_kb_ids(self) -> list[str]:
        """The knowledge bases auto-inject searches (hook point: a flow node's KB subset)."""
        return list(self._ctx.config.knowledge.kb_ids)

    async def _inject_knowledge(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        knowledge = self._ctx.config.knowledge
        if not knowledge.auto_inject or not self._auto_inject_kb_ids():
            return
        query = new_message.text_content
        if not query:
            return
        try:
            hits = await asyncio.wait_for(
                self._ctx.kb.search(query, k=knowledge.top_k), timeout=_KB_INJECT_TIMEOUT_S
            )
        except TimeoutError:
            logger.warning("knowledge auto-inject timed out", timeout_s=_KB_INJECT_TIMEOUT_S)
            return
        except Exception:
            logger.warning("knowledge auto-inject failed", exc_info=True)
            return
        if not hits:
            return
        body = "\n\n".join(f"[{hit.filename}] {hit.text}" for hit in hits)
        turn_ctx.add_message(role="assistant", content=f"{_KB_PREFIX}\n{body}")
        # Info, counts only (asks #36): the one KB signal visible on an INFO worker.
        logger.info("injected knowledge", hits=len(hits), top_k=knowledge.top_k)
        await self._cite(hits)

    async def _inject_vision(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        """Attach one recent frame to the user's message in cascaded mode (D-W2-8).

        R1: cascaded only, with a camera/screen capability and
        `vision_inject_per_turn`; realtime models already receive frames through
        `RoomOptions.video_input`. R2: at most one frame per turn, honouring the
        frame buffer's source preference. R3: encoded to a <= 512 px JPEG data
        URL off the event loop, `inference_detail="low"`. R4: earlier images are
        stripped from `turn_ctx` (by replacing those items with copies, since the
        per-turn context shares message objects with the persisted history) so
        each LLM call carries at most one image. Never raises: an exception here
        would make the SDK skip the reply for this turn.
        """
        self._last_turn_had_frame = False
        caps = self._ctx.config.capabilities
        if self._ctx.pipeline_mode != "cascaded" or self._vision_disabled:
            return
        if not caps.vision_inject_per_turn or not (caps.camera or caps.screen_share):
            return
        if self._model_vision is False:
            self._report_vision_skipped()
            return
        snapshot = self._ctx.frames.latest(self._vision_max_frame_age_s)
        if snapshot is None:
            return
        try:
            data_url = await asyncio.to_thread(encode_jpeg_data_url, snapshot.frame, _VISION_MAX_PX)
        except Exception:
            logger.warning("frame encoding failed; turn continues without an image", exc_info=True)
            return
        stripped = _strip_images(turn_ctx)
        new_message.content.append(lk_llm.ImageContent(image=data_url, inference_detail="low"))
        self._last_turn_had_frame = True
        logger.debug(
            "injected frame",
            source=snapshot.source,
            age_s=round(snapshot.age_s, 2),
            bytes=len(data_url),
            stripped_earlier_images=stripped,
            images_in_ctx=1,
        )

    def _report_vision_skipped(self) -> None:
        """Log and record, once per session, that a text-only model gets no frames (D-W2-10)."""
        if self._vision_skip_reported:
            return
        self._vision_skip_reported = True
        llm_ref = self._ctx.config.pipeline.llm
        model = llm_ref.model if llm_ref is not None else None
        provider_id = llm_ref.provider_id if llm_ref is not None else None
        logger.info("per-turn vision skipped: model is text-only", provider_id=provider_id, model=model)
        if self._record_event is not None:
            try:
                self._record_event("info", {"message": f"vision injection skipped: {model} is text-only"})
            except Exception:
                logger.debug("could not record the vision-skipped event", exc_info=True)

    def on_session_error(self, ev: Any) -> str | None:
        """Disable per-turn vision after an LLM error on a turn that carried a frame (D-W2-8 R5).

        Registered as a **synchronous** `error` handler by the worker.

        Returns:
            The message to record as an `error` session event when vision was
            just disabled; `None` otherwise.
        """
        if self._vision_disabled or not self._last_turn_had_frame:
            return None
        source = getattr(ev, "source", None)
        error = getattr(ev, "error", None)
        if not (isinstance(source, lk_llm.LLM) or isinstance(error, lk_llm.LLMError)):
            return None
        detail = getattr(error, "error", error)
        self._vision_disabled = True
        model = getattr(source, "model", None)
        logger.warning("vision injection disabled", model=model, error=str(detail))
        return f"vision injection disabled: {detail}"

    @property
    def vision_disabled(self) -> bool:
        """Whether per-turn frame injection was switched off after an LLM error."""
        return self._vision_disabled

    # ------------------------------------------------------------ reply control

    def on_function_tools_executed(self, ev: FunctionToolsExecutedEvent) -> None:
        """Keep the model silent after a `silent_reply` tool.

        Registered on the session as a **synchronous** handler: livekit reads
        `has_tool_reply` immediately after emitting the event, so a coroutine
        handler would run after the decision has already been made.

        `reply_required` is only honoured by realtime models — which both the
        `realtime` and the `half_cascade` pipeline run; cascaded LLMs always
        answer a tool output, which is why the cascaded pipeline note tells the
        model to keep acknowledgements short instead.
        """
        if self._ctx.pipeline_mode not in _REALTIME_MODEL_MODES or not self._silent_reply_tools:
            return
        names = {call.name for call in ev.function_calls}
        if names and names <= self._silent_reply_tools:
            ev.cancel_tool_reply()
            logger.debug("cancelled tool reply", tools=sorted(names))

    async def on_pack_session_end(self, reason: str) -> None:
        """Run the pack's teardown hook, never raising into the shutdown path."""
        try:
            await self._pack.on_session_end(self._ctx, reason)
        except Exception:
            logger.warning("pack on_session_end failed", pack_id=self._pack.manifest.id, exc_info=True)

    @property
    def pack(self) -> Pack:
        """The pack this agent delegates to."""
        return self._pack

    @property
    def context(self) -> SessionContext:
        """The pack session context handed to every hook and tool."""
        return self._ctx

    @property
    def greeting_mode(self) -> GreetingMode:
        """The greeting mechanism resolved for this pipeline."""
        return self._greeting_mode


def model_vision_support(
    config: AgentConfig,
    resolved: Mapping[str, ResolvedProvider] | None = None,
    *,
    capabilities: ModelCapabilities | None = None,
) -> bool | None:
    """Whether the cascaded LLM slot is known to accept images (DECISIONS-W2 §D-W2-10, D-V4-24).

    The api's resolved capabilities win (``capabilities``, else
    ``resolved["llm"].capabilities``): an admin's declaration, a "Test model"
    probe or the live catalog can say a custom id is text-only. Without them
    (an older api, or an unknown answer) the registry decides.

    Returns:
        `True`/`False` when known, `None` for an unknown free-text model id, an
        unknown provider, or a pipeline without an LLM slot.
    """
    llm_ref = config.pipeline.llm
    if llm_ref is None:
        return None
    if capabilities is None and resolved is not None and "llm" in resolved:
        capabilities = resolved["llm"].capabilities
    if capabilities is not None and capabilities.vision is not None:
        return capabilities.vision
    return vision_support(llm_ref.provider_id, llm_ref.model)


def _strip_images(turn_ctx: ChatContext) -> int:
    """Replace every `ChatMessage` in `turn_ctx` that holds images with an image-free copy.

    `ChatContext.copy()` is shallow, so mutating the messages in place would
    also strip the persisted history; swapping in copies keeps it intact.

    Returns:
        How many images were removed.
    """
    removed = 0
    items = turn_ctx.items
    for index, item in enumerate(items):
        if not isinstance(item, ChatMessage):
            continue
        kept = [part for part in item.content if not isinstance(part, lk_llm.ImageContent)]
        dropped = len(item.content) - len(kept)
        if dropped:
            items[index] = item.model_copy(update={"content": kept})
            removed += dropped
    return removed


async def platform_text_input_cb(sess: AgentSession[Any], ev: Any) -> None:
    """Route a typed `lk.chat` turn through `Agent.on_user_turn_completed`.

    livekit-agents 1.8.2's default `text_input_cb` calls
    `session.generate_reply(user_input=text)` directly, which **skips**
    `on_user_turn_completed` — so typed turns would get no knowledge
    auto-inject, no per-turn frame (D-W2-8) and no pack hook, unlike spoken
    turns. This mirrors the SDK's audio path instead: a per-turn copy of the
    agent's chat context goes to the hook, then the reply is generated from it.

    `AgentSession._claim_user_turn` is SDK-private but documented as the hook
    for custom text-input callbacks; it is accepted under DECISIONS-W2
    §D-W2-9p with an exact SDK pin, an offline tripwire test and this runtime
    fallback: if a future SDK drops it, typed turns still run, only without
    the user-state pin, and one warning is logged per process.

    Args:
        sess: The running session.
        ev: The SDK's `TextInputEvent` (only `.text` is read).
    """
    claim = getattr(sess, "_claim_user_turn", None)
    if claim is None:
        _warn_missing_claim_once()
    async with claim() if claim is not None else contextlib.nullcontext():
        try:
            await sess.interrupt()
        except RuntimeError:
            logger.info("skipping typed turn: current speech cannot be interrupted")
            return
        agent = sess.current_agent
        message = ChatMessage(role="user", content=[ev.text])
        turn_ctx = agent.chat_ctx.copy()
        try:
            await agent.on_user_turn_completed(turn_ctx, new_message=message)
        except StopResponse:
            return
        except Exception:
            logger.warning("on_user_turn_completed failed for a typed turn", exc_info=True)
            turn_ctx = agent.chat_ctx.copy()
        sess.generate_reply(user_input=message, chat_ctx=turn_ctx)


_claim_warning_logged = False


def _warn_missing_claim_once() -> None:
    global _claim_warning_logged  # noqa: PLW0603 - one warning per process by design
    if _claim_warning_logged:
        return
    _claim_warning_logged = True
    logger.warning(
        "AgentSession._claim_user_turn is missing; typed turns run without the user-state pin (SDK upgrade?)"
    )
