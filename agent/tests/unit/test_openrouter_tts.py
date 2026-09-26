"""`OpenRouterTTS`: the format it asks for and the rate it labels audio with.

OpenRouter's `/audio/speech` is mocked with `respx` (the external boundary); the
LiveKit TTS machinery (`ChunkedStream`, `AudioEmitter`) runs for real.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from livekit.agents import APIConnectOptions, APIStatusError

from lkap_agent.providers.openrouter import (
    AudioFormat,
    OpenRouterTTS,
    parse_audio_content_type,
    response_format_for,
)

SPEECH_URL = "https://openrouter.ai/api/v1/audio/speech"
NO_RETRY = APIConnectOptions(max_retry=0, timeout=5.0)


def _tts(model: str = "google/gemini-3.8-flash-tts") -> OpenRouterTTS:
    return OpenRouterTTS(
        model=model, voice="Kore", base_url="https://openrouter.ai/api/v1", api_key="sk-or-v1-test-not-real"
    )


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("google/gemini-3.8-flash-tts", "pcm"),
        ("google/gemini-3.8-flash-lite-tts", "pcm"),
        ("deepgram/aura-2", "pcm"),
        ("x-ai/grok-voice-tts-1.0", "pcm"),
        ("mistralai/voxtral-mini-tts-2603", "mp3"),
    ],
)
def test_response_format_for_picks_pcm_except_for_mp3_only_models(model: str, expected: str) -> None:
    assert response_format_for(model) == expected


@pytest.mark.parametrize(
    ("content_type", "requested", "expected"),
    [
        ("audio/pcm;rate=24000;channels=1", "pcm", AudioFormat("audio/pcm", 24_000, 1)),
        ("audio/pcm; rate=16000; channels=1", "pcm", AudioFormat("audio/pcm", 16_000, 1)),
        ("audio/pcm;rate=48000;channels=2", "pcm", AudioFormat("audio/pcm", 48_000, 2)),
        ('audio/pcm;rate="22050"', "pcm", AudioFormat("audio/pcm", 22_050, 1)),
        ("audio/pcm", "pcm", AudioFormat("audio/pcm", 24_000, 1)),
        ("audio/pcm;rate=abc", "pcm", AudioFormat("audio/pcm", 24_000, 1)),
        (None, "pcm", AudioFormat("audio/pcm", 24_000, 1)),
        ("application/octet-stream", "pcm", AudioFormat("audio/pcm", 24_000, 1)),
        ("audio/mpeg", "mp3", AudioFormat("audio/mpeg", 24_000, 1)),
        (None, "mp3", AudioFormat("audio/mpeg", 24_000, 1)),
    ],
)
def test_parse_audio_content_type(content_type: str | None, requested: str, expected: AudioFormat) -> None:
    assert parse_audio_content_type(content_type, requested_format=requested) == expected


async def _synthesize(tts: OpenRouterTTS) -> tuple[int, float, set[int]]:
    """Return (frame count, total seconds, sample rates seen)."""
    frames = 0
    seconds = 0.0
    rates: set[int] = set()
    async with tts.synthesize("Hello there.", conn_options=NO_RETRY) as stream:
        async for event in stream:
            frames += 1
            seconds += event.frame.duration
            rates.add(event.frame.sample_rate)
    return frames, seconds, rates


async def test_pcm_is_labelled_with_the_rate_openrouter_declares() -> None:
    """16 kHz PCM labelled as 24 kHz would play 1.5x too fast; half a second must stay half a second."""
    tts = _tts()
    half_second_16k = b"\x01\x00" * 8_000

    with respx.mock:
        route = respx.post(SPEECH_URL).mock(
            return_value=httpx.Response(
                200,
                content=half_second_16k,
                headers={"content-type": "audio/pcm;rate=16000;channels=1", "x-generation-id": "gen-123"},
            )
        )
        frames, seconds, rates = await _synthesize(tts)

    assert frames > 0
    assert rates == {16_000}
    assert seconds == pytest.approx(0.5, abs=0.03)  # the emitter may close with a short silence tail
    body = json.loads(route.calls.last.request.content)
    assert body["response_format"] == "pcm"
    assert body["model"] == "google/gemini-3.8-flash-tts"
    assert body["voice"] == "Kore"
    assert "stream_format" not in body
    await tts.aclose()


async def test_pcm_without_a_rate_parameter_is_taken_as_24k() -> None:
    tts = _tts()
    one_second_24k = b"\x00\x00" * 24_000

    with respx.mock:
        respx.post(SPEECH_URL).mock(
            return_value=httpx.Response(200, content=one_second_24k, headers={"content-type": "audio/pcm"})
        )
        _, seconds, rates = await _synthesize(tts)

    assert rates == {24_000}
    assert seconds == pytest.approx(1.0, abs=0.03)  # the emitter may close with a short silence tail
    await tts.aclose()


async def test_a_vendor_400_surfaces_as_a_non_retryable_status_error() -> None:
    tts = _tts()

    with respx.mock:
        respx.post(SPEECH_URL).mock(
            return_value=httpx.Response(400, json={"error": {"message": "bad voice", "code": 400}})
        )
        with pytest.raises(APIStatusError) as caught:
            await _synthesize(tts)

    assert caught.value.status_code == 400
    await tts.aclose()
