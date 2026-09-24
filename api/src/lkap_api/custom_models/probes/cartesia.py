"""Cartesia probes: ``/stt`` and ``/tts/bytes`` (D-V4-26).

Headers follow the catalog adapter (``X-API-Key`` + ``Cartesia-Version``);
which header style a real key needs is the open live-check ask (§1.4 item 1),
so both stay in one place: :func:`lkap_api.catalogs.adapters._cartesia_auth`'s
values are mirrored here rather than guessed anew.
"""

from __future__ import annotations

import time

from lkap_api.custom_models.probes.base import (
    TTS_INPUT,
    ProbeContext,
    ProbeInputError,
    ProbeOutcome,
    audio_probe,
    elapsed_ms,
    json_body,
    stt_clip,
    transcript_outcome,
)

STT_URL = "https://api.cartesia.ai/stt"
TTS_URL = "https://api.cartesia.ai/tts/bytes"
CARTESIA_VERSION = "2024-06-10"


def _auth(ctx: ProbeContext) -> dict[str, str]:
    return {"X-API-Key": ctx.api_key, "Cartesia-Version": CARTESIA_VERSION}


class CartesiaSttProbe:
    """``POST /stt`` (multipart: ``model``, ``file``)."""

    name = "cartesia_stt"

    async def run(self, ctx: ProbeContext) -> ProbeOutcome:
        """Transcribe the clip; pass on 2xx with a ``text`` field."""
        start = time.perf_counter()
        response = await ctx.client.post(
            STT_URL,
            headers=_auth(ctx),
            data={"model": ctx.model},
            files={"file": ("ok_1s_16k.wav", stt_clip(), "audio/wav")},
        )
        latency = elapsed_ms(start)
        body = json_body(response) if response.is_success else None
        text = body.get("text") if isinstance(body, dict) and isinstance(body.get("text"), str) else None
        return transcript_outcome(response, latency, text)


class CartesiaTtsProbe:
    """``POST /tts/bytes`` with the slot's ``voice`` id and an mp3 container."""

    name = "cartesia_tts"

    async def run(self, ctx: ProbeContext) -> ProbeOutcome:
        """Synthesise "Hello."; pass on 2xx audio of at least 1 KB."""
        voice = ctx.field_str("voice")
        if voice is None:
            raise ProbeInputError(f"the '{ctx.spec.id}' probe needs the slot's 'voice' field")
        return await audio_probe(
            ctx,
            TTS_URL,
            headers=_auth(ctx),
            json={
                "model_id": ctx.model,
                "transcript": TTS_INPUT,
                "voice": {"mode": "id", "id": voice},
                "output_format": {"container": "mp3", "sample_rate": 22050, "bit_rate": 64000},
            },
        )


__all__ = ["CARTESIA_VERSION", "STT_URL", "TTS_URL", "CartesiaSttProbe", "CartesiaTtsProbe"]
