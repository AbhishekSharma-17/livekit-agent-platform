"""A silent `stt.STT` for offline tests.

The platform never calls STT directly — `AgentSession` does — but a cascaded
`SessionPlan` is only honest if the STT slot holds a real `stt.STT`. This one
returns a fixed transcript and never touches the network.
"""

from __future__ import annotations

from typing import Any

from livekit.agents import APIConnectOptions, stt
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer

__all__ = ["FakeSTT"]


class FakeSTT(stt.STT[Any]):
    """Recognises everything as one fixed final transcript."""

    def __init__(self, transcript: str = "hello") -> None:
        super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))
        self.transcript = transcript
        self.calls = 0

    @property
    def model(self) -> str:
        """A stable identifier for metrics and traces."""
        return "fake-stt"

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        """Return the fixed transcript as a final recognition event."""
        self.calls += 1
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(text=self.transcript, language="en")],
        )
