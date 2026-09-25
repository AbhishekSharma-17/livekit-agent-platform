"""OpenAI-shaped probes: chat completions, transcriptions, speech, embeddings (D-V4-26).

One adapter per wire shape, parameterised by ``base_url``: OpenAI, OpenRouter,
Groq, Cerebras, xAI, Mistral, ``openai-compatible-llm`` (its ``base_url``
field is required) and LiveKit Inference (the agent gateway, with a bearer
JWT the service mints from a connection's key and secret, R-V4-25).
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx
from lkap_contracts.api_models import ProbeResult

from lkap_api.custom_models.probes.base import (
    EMBED_INPUT,
    LLM_PROMPT,
    PING_TOOL_DESCRIPTION,
    PING_TOOL_NAME,
    PNG_1X1_BASE64,
    RAW_PCM_TYPES,
    TTS_INPUT,
    ChatAnswer,
    LlmProbe,
    ProbeContext,
    ProbeInputError,
    ProbeOutcome,
    Usage,
    as_dict,
    as_list,
    audio_probe,
    base_url,
    elapsed_ms,
    failure,
    json_body,
    positive_int,
    stt_clip,
    token_budget,
    transcript_outcome,
)

#: The vendor base URL per registry entry (a slot's ``base_url`` field overrides it).
BASE_URLS: dict[str, str] = {
    "openai-llm": "https://api.openai.com/v1",
    "openai-responses-llm": "https://api.openai.com/v1",
    "openai-stt": "https://api.openai.com/v1",
    "openai-tts": "https://api.openai.com/v1",
    "openai-embedding": "https://api.openai.com/v1",
    "openrouter-llm": "https://openrouter.ai/api/v1",
    "openrouter-stt": "https://openrouter.ai/api/v1",
    "openrouter-tts": "https://openrouter.ai/api/v1",
    "openrouter-embedding": "https://openrouter.ai/api/v1",
    "groq-llm": "https://api.groq.com/openai/v1",
    "groq-stt": "https://api.groq.com/openai/v1",
    "cerebras-llm": "https://api.cerebras.ai/v1",
    "xai-llm": "https://api.x.ai/v1",
    "mistral-llm": "https://api.mistral.ai/v1",
    # installed livekit-agents `inference/_utils.py::DEFAULT_INFERENCE_URL`
    "livekit-inference-llm": "https://agent-gateway.livekit.cloud/v1",
}

#: The voice an OpenAI speech probe uses when the slot names none.
OPENAI_DEFAULT_VOICE = "alloy"

#: Phrases in a 400 that name a parameter the model refuses (reasoning models).
_REFUSED_PARAMS = {
    "max_tokens": ("max_tokens", "max_completion_tokens"),
    "temperature": ("temperature",),
}


def _bearer(ctx: ProbeContext) -> dict[str, str]:
    return {"Authorization": f"Bearer {ctx.api_key}"}


class OpenAiChatProbe(LlmProbe):
    """``POST {base}/chat/completions``: ``max_tokens`` 4 (16 on ``tools``), ``temperature`` 0, no stream.

    A 400 that names ``max_tokens`` or ``temperature`` (reasoning models refuse
    them) is retried once with ``max_completion_tokens`` at the same budget and
    no temperature; the rest of the run keeps that shape.
    """

    name = "openai_chat"

    def _body(self, ctx: ProbeContext, variant: str) -> dict[str, Any]:
        content: Any = LLM_PROMPT
        if variant == "vision":
            content = [
                {"type": "text", "text": LLM_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_1X1_BASE64}"}},
            ]
        body: dict[str, Any] = {
            "model": ctx.model,
            "messages": [{"role": "user", "content": content}],
            "stream": False,
        }
        if ctx.state.get("reasoning_shape"):
            body["max_completion_tokens"] = token_budget(variant)
        else:
            body["max_tokens"] = token_budget(variant)
            body["temperature"] = 0
        if variant == "tools":
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": PING_TOOL_NAME,
                        "description": PING_TOOL_DESCRIPTION,
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]
            body["tool_choice"] = "required"
        return body

    async def call(self, ctx: ProbeContext, variant: str) -> tuple[httpx.Response, int]:
        url = f"{base_url(ctx, BASE_URLS.get(ctx.spec.id))}/chat/completions"
        start = time.perf_counter()
        response = await ctx.client.post(url, headers=_bearer(ctx), json=self._body(ctx, variant))
        if response.status_code == 400 and not ctx.state.get("reasoning_shape") and _refuses_params(response):
            ctx.state["reasoning_shape"] = True
            start = time.perf_counter()
            response = await ctx.client.post(url, headers=_bearer(ctx), json=self._body(ctx, variant))
        return response, elapsed_ms(start)

    def parse(self, body: Any) -> ChatAnswer:
        choices = as_list(as_dict(body).get("choices"))
        if not choices or not isinstance(choices[0], dict):
            return ChatAnswer(well_formed=False)
        message = as_dict(choices[0].get("message"))
        content = message.get("content")
        usage = as_dict(as_dict(body).get("usage"))
        return ChatAnswer(
            well_formed=True,
            text=content if isinstance(content, str) else None,
            tool_called=bool(message.get("tool_calls")),
            truncated=choices[0].get("finish_reason") == "length",
            tokens_in=positive_int(usage.get("prompt_tokens")),
            tokens_out=positive_int(usage.get("completion_tokens")),
        )


def _refuses_params(response: httpx.Response) -> bool:
    text = response.text.lower()
    return "unsupported" in text and any(word in text for words in _REFUSED_PARAMS.values() for word in words)


class OpenAiTranscriptionsProbe:
    """``POST {base}/audio/transcriptions`` (multipart) with the bundled 1 s clip."""

    name = "openai_transcriptions"

    async def run(self, ctx: ProbeContext) -> ProbeOutcome:
        """Transcribe the clip; pass on 2xx with a ``text`` field."""
        url = f"{base_url(ctx, BASE_URLS.get(ctx.spec.id))}/audio/transcriptions"
        start = time.perf_counter()
        response = await ctx.client.post(
            url,
            headers=_bearer(ctx),
            data={"model": ctx.model, "response_format": "json"},
            files={"file": ("ok_1s_16k.wav", stt_clip(), "audio/wav")},
        )
        latency = elapsed_ms(start)
        body = json_body(response) if response.is_success else None
        text = body.get("text") if isinstance(body, dict) and isinstance(body.get("text"), str) else None
        return transcript_outcome(response, latency, text)


class OpenAiSpeechProbe:
    """``POST {base}/audio/speech``: ``input="Hello."``, the slot's voice, mp3 (pcm on a format refusal).

    The first request asks for ``mp3`` (the cheapest to carry). Some models take
    only raw PCM (OpenRouter's Gemini TTS answers ``400: Gemini TTS only supports
    response_format="pcm"``, ask #64), and no vendor list says which formats a
    model takes (OpenRouter's speech models carry no format field), so a 400/415/422
    that names the format is retried **once** with ``pcm``, and PCM bytes count
    as audio. A refused request is not billed; the input stays ``"Hello."``.
    """

    name = "openai_speech"

    async def run(self, ctx: ProbeContext) -> ProbeOutcome:
        """Synthesise "Hello."; pass on 2xx audio of at least 1 KB."""
        voice = ctx.field_str("voice") or (
            OPENAI_DEFAULT_VOICE if ctx.spec.id.startswith("openai-") else None
        )
        if voice is None:
            raise ProbeInputError(f"the '{ctx.spec.id}' probe needs the slot's 'voice' field")
        url = f"{base_url(ctx, BASE_URLS.get(ctx.spec.id))}/audio/speech"
        body = {"model": ctx.model, "input": TTS_INPUT, "voice": voice, "response_format": "mp3"}
        outcome = await audio_probe(ctx, url, headers=_bearer(ctx), json=body)
        if not _refuses_format(outcome):
            return outcome
        ctx.state["tts_format"] = "pcm"
        retried = await audio_probe(
            ctx,
            url,
            headers=_bearer(ctx),
            json={**body, "response_format": "pcm"},
            raw_audio_types=RAW_PCM_TYPES,
        )
        note = "retried with pcm after the vendor refused mp3"
        retried.message = f"{retried.message} ({note})" if retried.message else note
        retried.results = [
            r.model_copy(update={"message": f"{r.message} ({note})" if r.message else note})
            for r in retried.results
        ]
        return retried


#: A 4xx that names the audio format (``response_format``, ``pcm``): worth one pcm retry.
_FORMAT_REFUSAL = re.compile(r"response_format|\bpcm\b", re.I)


def _refuses_format(outcome: ProbeOutcome) -> bool:
    """Whether a failed speech request was a 400/415/422 naming the response format."""
    if outcome.ok is not False or not outcome.results:
        return False
    message = outcome.results[0].message or ""
    return bool(re.match(r"HTTP (400|415|422)\b", message)) and bool(_FORMAT_REFUSAL.search(message))


class OpenAiEmbeddingsProbe:
    """``POST {base}/embeddings`` with ``input="ping"``; reports the vector's dimension."""

    name = "openai_embeddings"

    async def run(self, ctx: ProbeContext) -> ProbeOutcome:
        """Embed "ping"; pass on 2xx with one non-empty vector."""
        url = f"{base_url(ctx, BASE_URLS.get(ctx.spec.id))}/embeddings"
        start = time.perf_counter()
        response = await ctx.client.post(
            url, headers=_bearer(ctx), json={"model": ctx.model, "input": EMBED_INPUT}
        )
        latency = elapsed_ms(start)
        if not response.is_success:
            return ProbeOutcome(ok=False, results=[failure("basic", response, latency)])
        body = as_dict(json_body(response))
        data = as_list(body.get("data"))
        vector = as_dict(data[0]).get("embedding") if data else None
        usage = as_dict(body.get("usage"))
        return embedding_outcome(vector, latency, tokens_in=positive_int(usage.get("prompt_tokens")))


def embedding_outcome(vector: Any, latency_ms: int, *, tokens_in: int = 0) -> ProbeOutcome:
    """Pass when ``vector`` is a non-empty list; the message names its dimension."""
    if not isinstance(vector, list) or not vector:
        message = "the answer carried no embedding vector"
        return ProbeOutcome(
            ok=False, results=[ProbeResult(name="basic", ok=False, latency_ms=latency_ms, message=message)]
        )
    message = f"one vector of dimension {len(vector)}"
    return ProbeOutcome(
        ok=True,
        results=[ProbeResult(name="basic", ok=True, latency_ms=latency_ms, message=message)],
        message=message,
        usage=Usage(tokens_in=tokens_in),
    )


__all__ = [
    "BASE_URLS",
    "OPENAI_DEFAULT_VOICE",
    "OpenAiChatProbe",
    "OpenAiEmbeddingsProbe",
    "OpenAiSpeechProbe",
    "OpenAiTranscriptionsProbe",
    "embedding_outcome",
]
