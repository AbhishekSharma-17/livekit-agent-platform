"""A silent `tts.TTS` for offline tests.

Emits a short run of silence so `AgentSession.say()` completes without a vendor
connection. Its real purpose in this suite is to make `has_tts` true, which is
what decides whether the greeting uses `say()` or `generate_reply()`
(docs/ARCHITECTURE.md §15.9).
"""

from __future__ import annotations

from typing import Any

from livekit.agents import APIConnectOptions, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

__all__ = ["FakeTTS"]

_SAMPLE_RATE = 24000
_NUM_CHANNELS = 1
#: 100 ms of 16-bit silence.
_SILENCE = b"\x00\x00" * (_SAMPLE_RATE // 10)


class FakeTTS(tts.TTS[Any]):
    """Synthesises a fixed burst of silence and records every request."""

    def __init__(self) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=_SAMPLE_RATE,
            num_channels=_NUM_CHANNELS,
        )
        self.synthesized: list[str] = []

    @property
    def model(self) -> str:
        """A stable identifier for metrics and traces."""
        return "fake-tts"

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> _FakeChunkedStream:
        """Record `text` and return a stream of silence."""
        self.synthesized.append(text)
        return _FakeChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class _FakeChunkedStream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id="fake-tts",
            sample_rate=_SAMPLE_RATE,
            num_channels=_NUM_CHANNELS,
            mime_type="audio/pcm",
        )
        output_emitter.push(_SILENCE)
        output_emitter.flush()
