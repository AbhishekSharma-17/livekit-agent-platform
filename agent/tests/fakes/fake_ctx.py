"""`FakePackSessionContext` for the worker's tests -- a shim over `lkap_testing` (REVIEW-FINAL F-18).

The fakes themselves (`FakeUiChannel`, `FakeFrameBuffer`, `FakeKbClient`,
`FakeStructuredLLM`, `FakeImageGen`, `FakeBackgroundRunner`) live once, in
`testing/src/lkap_testing/fake_ctx.py`, shared with `packs/tests`. The only
agent-specific part is this subclass's defaults: a `fakes.fake_room.FakeRoom`
for `room` and a bound `structlog` logger for `log`, as the real
`SessionContext` has.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any, cast

import structlog
from livekit import rtc
from lkap_testing import fake_ctx as _shared
from lkap_testing.fake_ctx import (
    FakeBackgroundRunner,
    FakeFrameBuffer,
    FakeImageGen,
    FakeKbClient,
    FakeLogger,
    FakeStructuredLLM,
    FakeUiChannel,
    apply_patch_op,
    default_agent_config,
)

__all__ = [
    "FakeBackgroundRunner",
    "FakeFrameBuffer",
    "FakeImageGen",
    "FakeKbClient",
    "FakeLogger",
    "FakeFunctionCall",
    "FakePackSessionContext",
    "FakeRunContext",
    "FakeStructuredLLM",
    "FakeToolSession",
    "FakeUiChannel",
    "apply_patch_op",
    "default_agent_config",
]


class FakePackSessionContext(_shared.FakePackSessionContext):
    """The shared fake context with a `FakeRoom` and a `structlog` logger by default."""

    def __init__(self, *, room: rtc.Room | None = None, log: Any | None = None, **kwargs: Any) -> None:
        session_id = kwargs.get("session_id", "sess-test")
        agent_id = kwargs.get("agent_id", "agent-test")
        super().__init__(
            room=room or cast(rtc.Room, _empty_fake_room()),
            log=log or structlog.get_logger("test").bind(session_id=session_id, agent_id=agent_id),
            **kwargs,
        )


class FakeToolSession:
    """The slice of `AgentSession` `tools.execution.run_with_policy` reads (`tts`)."""

    def __init__(self, *, tts: Any | None = None) -> None:
        self.tts = tts


class FakeFunctionCall:
    """A `FunctionCall` stand-in: only `call_id` and `name` are read."""

    def __init__(self, call_id: str = "call-1", name: str = "lookup_item") -> None:
        self.call_id = call_id
        self.name = name


class FakeRunContext:
    """A `RunContext` stand-in for `run_with_policy` (BACKGROUND-TOOLS.md §8).

    Records every `update()` message and every `with_filler()` entry, in order,
    in :attr:`log` (``("update", message)`` / ``("filler", kwargs)``).
    """

    def __init__(self, *, tts: Any | None = None, call_id: str = "call-1", name: str = "lookup_item") -> None:
        self.session = FakeToolSession(tts=tts)
        self.function_call = FakeFunctionCall(call_id, name)
        self.updates: list[str] = []
        self.fillers: list[dict[str, Any]] = []
        self.log: list[tuple[str, Any]] = []

    async def update(self, message: str) -> None:
        self.updates.append(message)
        self.log.append(("update", message))

    @contextlib.asynccontextmanager
    async def with_filler(self, source: Any, **kwargs: Any) -> AsyncIterator[None]:
        entry = {"source": source, **kwargs}
        self.fillers.append(entry)
        self.log.append(("filler", entry))
        yield


def _empty_fake_room() -> Any:
    """Lazily import `FakeRoom` (avoids a hard import cycle at module load).

    `agent/tests` has no `__init__.py`, so pytest's rootdir insertion makes
    `agent/tests/fakes` importable as the top-level package `fakes` (its
    `conftest.py` triggers the `sys.path` insertion before any test runs).
    """
    from fakes.fake_room import FakeRoom  # noqa: PLC0415

    return FakeRoom()
