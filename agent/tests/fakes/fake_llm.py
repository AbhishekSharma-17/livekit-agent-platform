"""A scripted `llm.LLM` for offline tests.

Subclasses the real `livekit.agents.llm.LLM` / `LLMStream` so anything the
framework does with a model — `AgentSession`, `PlatformAgent`,
`PromptJsonStructuredLLM` — behaves exactly as in production, minus the network.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from livekit.agents import APIConnectOptions, llm
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr

__all__ = ["FakeLLM", "FakeLLMStream", "RecordedChatCall"]


class RecordedChatCall:
    """One `chat()` invocation, for assertions."""

    def __init__(self, chat_ctx: llm.ChatContext, tools: list[llm.Tool]) -> None:
        self.chat_ctx = chat_ctx
        self.tools = tools

    @property
    def prompt(self) -> str:
        """Every text part of the context, newline-joined (what the model saw)."""
        parts: list[str] = []
        for item in self.chat_ctx.items:
            if isinstance(item, llm.ChatMessage):
                parts.append(item.text_content or "")
        return "\n".join(parts)

    @property
    def image_count(self) -> int:
        """How many `ImageContent` parts the context carried."""
        total = 0
        for item in self.chat_ctx.items:
            if isinstance(item, llm.ChatMessage):
                total += sum(1 for part in item.content if isinstance(part, llm.ImageContent))
        return total


class FakeLLMStream(llm.LLMStream):
    """Emits one scripted reply as a single content chunk."""

    def __init__(
        self,
        parent: FakeLLM,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        conn_options: APIConnectOptions,
        reply: str,
    ) -> None:
        self._reply = reply
        super().__init__(parent, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)

    async def _run(self) -> None:
        self._event_ch.send_nowait(
            llm.ChatChunk(id="fake-chunk", delta=llm.ChoiceDelta(role="assistant", content=self._reply))
        )


class FakeLLM(llm.LLM[Any]):
    """An `llm.LLM` that returns queued replies, recording every call.

    Args:
        replies: Replies returned in order; the last one repeats once exhausted.
    """

    def __init__(self, replies: Sequence[str] | None = None) -> None:
        super().__init__()
        self.replies: list[str] = list(replies or ["Sure."])
        self.calls: list[RecordedChatCall] = []

    @property
    def model(self) -> str:
        """A stable identifier for metrics and traces."""
        return "fake-llm"

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[llm.ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> FakeLLMStream:
        """Record the call and stream back the next queued reply."""
        self.calls.append(RecordedChatCall(chat_ctx.copy(), list(tools or [])))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return FakeLLMStream(
            self,
            chat_ctx=chat_ctx,
            tools=list(tools or []),
            conn_options=conn_options,
            reply=reply,
        )
