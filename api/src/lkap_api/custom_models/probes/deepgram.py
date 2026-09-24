"""Deepgram probes: ``/v1/listen`` (STT, raw audio) and ``/v1/speak`` (TTS) (D-V4-26)."""

from __future__ import annotations

import time
from typing import Any

from lkap_api.custom_models.probes.base import (
    TTS_INPUT,
    ProbeContext,
    ProbeOutcome,
    audio_probe,
    elapsed_ms,
    json_body,
    stt_clip,
    transcript_outcome,
)

LISTEN_URL = "https://api.deepgram.com/v1/listen"
SPEAK_URL = "https://api.deepgram.com/v1/speak"


def _auth(ctx: ProbeContext) -> dict[str, str]:
    return {"Authorization": f"Token {ctx.api_key}"}


def _transcript(body: Any) -> str | None:
    try:
        value = body["results"]["channels"][0]["alternatives"][0]["transcript"]
    except (KeyError, IndexError, TypeError):
        return None
    return value if isinstance(value, str) else None


class DeepgramListenProbe:
    """``POST /v1/listen?model=<id>`` with the bundled clip as ``audio/wav``."""

    name = "deepgram_listen"

    async def run(self, ctx: ProbeContext) -> ProbeOutcome:
        """Transcribe the clip; pass on 2xx with a transcript field."""
        start = time.perf_counter()
        response = await ctx.client.post(
            LISTEN_URL,
            params={"model": ctx.model},
            headers={**_auth(ctx), "Content-Type": "audio/wav"},
            content=stt_clip(),
        )
        latency = elapsed_ms(start)
        text = _transcript(json_body(response)) if response.is_success else None
        return transcript_outcome(response, latency, text)


class DeepgramSpeakProbe:
    """``POST /v1/speak?model=<id>`` with ``{"text": "Hello."}`` (the model id names the voice)."""

    name = "deepgram_speak"

    async def run(self, ctx: ProbeContext) -> ProbeOutcome:
        """Synthesise "Hello."; pass on 2xx audio of at least 1 KB."""
        return await audio_probe(
            ctx, SPEAK_URL, headers=_auth(ctx), params={"model": ctx.model}, json={"text": TTS_INPUT}
        )


__all__ = ["LISTEN_URL", "SPEAK_URL", "DeepgramListenProbe", "DeepgramSpeakProbe"]
