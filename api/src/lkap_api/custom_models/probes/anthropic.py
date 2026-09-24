"""Anthropic Messages probe: ``POST /v1/messages`` (D-V4-26)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from lkap_api.custom_models.probes.base import (
    LLM_PROMPT,
    MAX_TOKENS,
    PING_TOOL_DESCRIPTION,
    PING_TOOL_NAME,
    PNG_1X1_BASE64,
    ChatAnswer,
    LlmProbe,
    ProbeContext,
    elapsed_ms,
    positive_int,
)

MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicMessagesProbe(LlmProbe):
    """``max_tokens`` 4, ``temperature`` 0; ``tools`` forces ``{"type": "tool", "name": "ping"}``."""

    name = "anthropic_messages"

    def _body(self, ctx: ProbeContext, variant: str) -> dict[str, Any]:
        content: Any = LLM_PROMPT
        if variant == "vision":
            content = [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": PNG_1X1_BASE64},
                },
                {"type": "text", "text": LLM_PROMPT},
            ]
        body: dict[str, Any] = {
            "model": ctx.model,
            "max_tokens": MAX_TOKENS,
            "temperature": 0,
            "messages": [{"role": "user", "content": content}],
        }
        if variant == "tools":
            body["tools"] = [
                {
                    "name": PING_TOOL_NAME,
                    "description": PING_TOOL_DESCRIPTION,
                    "input_schema": {"type": "object", "properties": {}},
                }
            ]
            body["tool_choice"] = {"type": "tool", "name": PING_TOOL_NAME}
        return body

    async def call(self, ctx: ProbeContext, variant: str) -> tuple[httpx.Response, int]:
        headers = {"x-api-key": ctx.api_key, "anthropic-version": ANTHROPIC_VERSION}
        start = time.perf_counter()
        response = await ctx.client.post(MESSAGES_URL, headers=headers, json=self._body(ctx, variant))
        return response, elapsed_ms(start)

    def parse(self, body: Any) -> ChatAnswer:
        content = body.get("content") if isinstance(body, dict) else None
        if not isinstance(content, list):
            return ChatAnswer(well_formed=False)
        text = "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return ChatAnswer(
            well_formed=True,
            text=text or None,
            tool_called=any(isinstance(block, dict) and block.get("type") == "tool_use" for block in content),
            truncated=body.get("stop_reason") == "max_tokens",
            tokens_in=positive_int(usage.get("input_tokens")),
            tokens_out=positive_int(usage.get("output_tokens")),
        )


__all__ = ["ANTHROPIC_VERSION", "MESSAGES_URL", "AnthropicMessagesProbe"]
