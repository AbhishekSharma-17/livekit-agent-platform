"""Gemini API probes: ``:generateContent`` and ``:embedContent`` (D-V4-26).

The key rides in the ``x-goog-api-key`` header, never in the URL. The model id
(already checked by the model-id rule) is one quoted path segment.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import httpx

from lkap_api.custom_models.probes.base import (
    EMBED_INPUT,
    LLM_PROMPT,
    PING_TOOL_DESCRIPTION,
    PING_TOOL_NAME,
    PNG_1X1_BASE64,
    ChatAnswer,
    LlmProbe,
    ProbeContext,
    ProbeOutcome,
    as_dict,
    as_list,
    elapsed_ms,
    failure,
    json_body,
    positive_int,
    token_budget,
)
from lkap_api.custom_models.probes.openai_like import embedding_outcome

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


def model_path(model: str) -> str:
    """``models/<id>`` with the id quoted as one segment (a leading ``models/`` is accepted)."""
    return "models/" + quote(model.removeprefix("models/"), safe="")


class GeminiGenerateProbe(LlmProbe):
    """``maxOutputTokens`` 4 (16 on ``tools``), ``temperature`` 0.

    ``tools`` forces ``functionCallingConfig.mode=ANY``.
    """

    name = "gemini_generate"

    def _body(self, variant: str) -> dict[str, Any]:
        parts: list[dict[str, Any]] = [{"text": LLM_PROMPT}]
        if variant == "vision":
            parts.insert(0, {"inlineData": {"mimeType": "image/png", "data": PNG_1X1_BASE64}})
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"maxOutputTokens": token_budget(variant), "temperature": 0},
        }
        if variant == "tools":
            body["tools"] = [
                {"functionDeclarations": [{"name": PING_TOOL_NAME, "description": PING_TOOL_DESCRIPTION}]}
            ]
            body["toolConfig"] = {"functionCallingConfig": {"mode": "ANY"}}
        return body

    async def call(self, ctx: ProbeContext, variant: str) -> tuple[httpx.Response, int]:
        url = f"{GEMINI_BASE}/{model_path(ctx.model)}:generateContent"
        start = time.perf_counter()
        response = await ctx.client.post(
            url, headers={"x-goog-api-key": ctx.api_key}, json=self._body(variant)
        )
        return response, elapsed_ms(start)

    def parse(self, body: Any) -> ChatAnswer:
        candidates = as_list(as_dict(body).get("candidates"))
        if not candidates or not isinstance(candidates[0], dict):
            return ChatAnswer(well_formed=False)
        parts = as_list(as_dict(candidates[0].get("content")).get("parts"))
        text = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
        usage = as_dict(as_dict(body).get("usageMetadata"))
        return ChatAnswer(
            well_formed=True,
            text=text or None,
            tool_called=any(isinstance(part, dict) and "functionCall" in part for part in parts),
            truncated=candidates[0].get("finishReason") == "MAX_TOKENS",
            tokens_in=positive_int(usage.get("promptTokenCount")),
            tokens_out=positive_int(usage.get("candidatesTokenCount")),
        )


class GeminiEmbedProbe:
    """``POST .../models/{m}:embedContent`` with ``"ping"`` (no registry entry wires it yet)."""

    name = "gemini_embed"

    async def run(self, ctx: ProbeContext) -> ProbeOutcome:
        """Embed "ping"; pass on 2xx with one non-empty vector."""
        url = f"{GEMINI_BASE}/{model_path(ctx.model)}:embedContent"
        start = time.perf_counter()
        response = await ctx.client.post(
            url, headers={"x-goog-api-key": ctx.api_key}, json={"content": {"parts": [{"text": EMBED_INPUT}]}}
        )
        latency = elapsed_ms(start)
        if not response.is_success:
            return ProbeOutcome(ok=False, results=[failure("basic", response, latency)])
        body = json_body(response)
        embedding = body.get("embedding") if isinstance(body, dict) else None
        return embedding_outcome(embedding.get("values") if isinstance(embedding, dict) else None, latency)


__all__ = ["GEMINI_BASE", "GeminiEmbedProbe", "GeminiGenerateProbe", "model_path"]
