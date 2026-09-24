"""ElevenLabs probes: ``/v1/speech-to-text`` and ``/v1/text-to-speech/{voice_id}`` (D-V4-26).

The TTS probe needs the slot's ``voice_id`` (the voice is part of the URL);
the service has already checked it with the id rule.
"""

from __future__ import annotations

import time
from urllib.parse import quote

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

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
#: The cheapest documented mp3 format.
OUTPUT_FORMAT = "mp3_22050_32"


def _auth(ctx: ProbeContext) -> dict[str, str]:
    return {"xi-api-key": ctx.api_key}


class ElevenLabsSttProbe:
    """``POST /v1/speech-to-text`` (multipart: ``model_id``, ``file``)."""

    name = "elevenlabs_stt"

    async def run(self, ctx: ProbeContext) -> ProbeOutcome:
        """Transcribe the clip; pass on 2xx with a ``text`` field."""
        start = time.perf_counter()
        response = await ctx.client.post(
            STT_URL,
            headers=_auth(ctx),
            data={"model_id": ctx.model},
            files={"file": ("ok_1s_16k.wav", stt_clip(), "audio/wav")},
        )
        latency = elapsed_ms(start)
        body = json_body(response) if response.is_success else None
        text = body.get("text") if isinstance(body, dict) and isinstance(body.get("text"), str) else None
        return transcript_outcome(response, latency, text)


class ElevenLabsTtsProbe:
    """``POST /v1/text-to-speech/{voice_id}?output_format=mp3_22050_32`` with ``model_id``."""

    name = "elevenlabs_tts"

    async def run(self, ctx: ProbeContext) -> ProbeOutcome:
        """Synthesise "Hello." with the slot's voice; pass on 2xx audio of at least 1 KB."""
        voice_id = ctx.field_str("voice_id")
        if voice_id is None:
            raise ProbeInputError(f"the '{ctx.spec.id}' probe needs the slot's 'voice_id' field")
        return await audio_probe(
            ctx,
            TTS_URL.format(voice_id=quote(voice_id, safe="")),
            headers=_auth(ctx),
            params={"output_format": OUTPUT_FORMAT},
            json={"text": TTS_INPUT, "model_id": ctx.model},
        )


__all__ = ["OUTPUT_FORMAT", "STT_URL", "TTS_URL", "ElevenLabsSttProbe", "ElevenLabsTtsProbe"]
