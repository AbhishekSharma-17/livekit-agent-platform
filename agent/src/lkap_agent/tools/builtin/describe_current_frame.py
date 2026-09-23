"""`describe_current_frame` built-in tool (docs/ARCHITECTURE.md §7.3, §8).

Capability-gated the same way as `pin_frame`.

- **Cascaded**: sends the latest fresh frame (+ optional question) to the
  session's configured LLM as an `ImageContent` and returns its text
  description (verified: `livekit.agents.llm.ImageContent` accepts an
  `rtc.VideoFrame` directly per its docstring in `llm/chat_context.py`;
  `LLM.chat(chat_ctx=...)` returns an async-iterable `LLMStream` of
  `ChatChunk`).
  On a model the registry knows is text-only (`vision_support(...) is False`,
  DECISIONS-W2 §D-W2-10) the tool refuses with a `ToolError` before touching
  a frame; unknown (free-text) model ids are tried.
- **Realtime and half-cascade**: Gemini Live / OpenAI Realtime already
  receive live frames via `RoomOptions(video_input=True)` (ARCHITECTURE §8;
  V2-07 enables it for `half_cascade` too), so this just confirms a frame
  exists and how old it is, rather than describing it a second time. In
  half-cascade the realtime model owns video input, exactly as in realtime
  (the architect's vision rule, asks #54); there is no cascaded LLM to ask.
"""

from __future__ import annotations

from typing import Any

from livekit.agents import ChatContext, FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.providers import vision_support
from packs.base import PackSessionContext

#: Matches `pin_frame`'s freshness window (ARCHITECTURE §7.3) for consistency.
DESCRIBE_FRAME_MAX_AGE_S = 12.0

_DEFAULT_QUESTION = "Describe what is visible in this image."

#: Pipeline modes whose realtime model sees the video itself (asks #54).
_REALTIME_VISION_MODES: frozenset[str] = frozenset({"realtime", "half_cascade"})

TEXT_ONLY_MODEL_ERROR = (
    "The configured language model cannot see images; ask an admin to switch it to a vision-capable model."
)


def build_describe_current_frame_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `describe_current_frame` tool bound to `ctx`."""

    @function_tool
    async def describe_current_frame(context: RunContext[Any], question: str | None = None) -> str:
        """Describe what the camera or screen share currently shows.

        Args:
            question: Optional specific question about the current frame.
        """
        if ctx.pipeline_mode in _REALTIME_VISION_MODES:
            snapshot = ctx.frames.latest()
            if snapshot is None:
                return "No camera or screen frame is currently available."
            return (
                f"A live {snapshot.source} frame is available (captured "
                f"{snapshot.age_s:.1f}s ago) — you can already see it directly."
            )

        llm_ref = ctx.config.pipeline.llm
        if llm_ref is not None and vision_support(llm_ref.provider_id, llm_ref.model) is False:
            raise ToolError(TEXT_ONLY_MODEL_ERROR)

        # `ImageContent(image=frame)` encodes the frame itself (per its docstring in
        # `llm/chat_context.py`) — no need to pre-encode a JPEG we'd only discard.
        snapshot = ctx.frames.latest(max_age_s=DESCRIBE_FRAME_MAX_AGE_S)
        if snapshot is None:
            return "No fresh camera or screen frame is available right now."

        model = ctx.session.llm
        if model is None or not hasattr(model, "chat"):
            raise ToolError("No cascaded LLM is configured to describe frames.")

        from livekit.agents.llm import ImageContent

        chat_ctx = ChatContext.empty()
        chat_ctx.add_message(
            role="user", content=[ImageContent(image=snapshot.frame), question or _DEFAULT_QUESTION]
        )

        text_parts: list[str] = []
        async with model.chat(chat_ctx=chat_ctx) as stream:
            async for chunk in stream:
                if chunk.delta and chunk.delta.content:
                    text_parts.append(chunk.delta.content)

        ctx.log.debug(
            "builtin_tool.describe_current_frame",
            call_id=context.function_call.call_id,
            source=snapshot.source,
        )
        description = "".join(text_parts).strip()
        return description or "I couldn't make out anything useful in the current frame."

    return describe_current_frame
