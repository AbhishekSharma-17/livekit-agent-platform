"""A fake of livekit-agents 1.8.3 `AMD` (answering-machine detection), for V5-32's tests.

The real detector needs a running `AgentSession` with a room, an audio track and
an LLM call per greeting; this one duck-types the surface `lkap_agent.telephony.
AmdRunner` uses — the async context manager (`__aenter__` holds the agent's
speech, `__aexit__` releases it) and `execute()` returning an
`AMDPredictionEvent` — and records every call in `log`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from livekit.agents import AMDCategory, AMDPredictionEvent


class FakeAMD:
    """One detection with a canned verdict (or a failure), optionally gated on an event."""

    def __init__(
        self,
        verdict: str = "human",
        *,
        fail: bool = False,
        gate: asyncio.Event | None = None,
        log: list[str] | None = None,
    ) -> None:
        self.verdict = verdict
        self.fail = fail
        self.gate = gate
        self.log: list[str] = log if log is not None else []
        self.kwargs: dict[str, Any] = {}
        self.session: Any = None

    async def __aenter__(self) -> FakeAMD:
        self.log.append("amd:enter")
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.log.append("amd:exit")

    async def execute(self) -> AMDPredictionEvent:
        self.log.append("amd:execute")
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("amd closed before a result was available")
        return AMDPredictionEvent(
            category=AMDCategory(self.verdict),
            speech_duration=1.2,
            reason="fake",
            transcript="Hi, you've reached the voicemail of Sam. Leave a message after the tone.",
            delay=0.4,
        )


class FakeAMDFactory:
    """Stands in for `telephony.default_amd_factory`: records its arguments, returns one `FakeAMD`."""

    def __init__(self, detector: FakeAMD | None = None) -> None:
        self.detector = detector or FakeAMD()
        self.calls: list[dict[str, Any]] = []

    def __call__(self, session: Any, **kwargs: Any) -> FakeAMD:
        self.calls.append({"session": session, **kwargs})
        self.detector.session = session
        self.detector.kwargs = dict(kwargs)
        return self.detector
