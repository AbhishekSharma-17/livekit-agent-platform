"""`FakeRoomTransport` -- an offline stand-in for the MCP test chat's room connection (V3-02).

`lkap_mcp.chat.transport.RoomTransport` is the Protocol the MCP's chat manager
talks to; `LiveKitRoomTransport` implements it over `livekit.rtc`. This fake
implements the same Protocol **structurally** (it never imports `lkap_mcp`:
the MCP package dev-depends on this one, so the import would be circular), so
chat tests run with no LiveKit server, no worker and no network.

What it can script:

* ``greeting`` -- a reply emitted as soon as the agent "joins"
  (``wait_for_agent`` returns);
* ``turns`` -- one list of replies per ``send_text`` call, consumed in order
  (a plain ``str`` is a final reply from the agent; a :class:`FakeReply` sets
  ``final``/``participant`` explicitly, e.g. an interim ``final=False`` chunk or
  an echo from another participant). ``responder`` replaces ``turns`` with a
  function of the sent text;
* ``rpc_turns`` -- replies emitted after a successful ``lkap.agent.action``
  RPC (the regenerated reply of a ``rewind``/``inject_user_text``), one list
  per call; ``rpc_response`` is the JSON string the RPC returns
  (default ``{"ok": true, "payload": {}}``, an ``AgentActionResult``);
* ``join_delay_s`` -- how long the agent takes to join; ``never_joins=True``
  -- it never does (``wait_for_agent`` raises ``TimeoutError`` after
  ``timeout_s``);
* ``reply_delay_s`` -- a pause before each scripted reply is emitted.

What it records, for assertions: ``connected_with`` (``(server_url, token)``),
``sent`` (``(text, topic)``), ``rpc_calls`` (``(method, payload)``),
``closed``/``close_calls``. :meth:`drop` simulates the room going away (the
agent left or the connection dropped): ``replies()`` ends and ``connected``
turns false.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field

__all__ = ["FAKE_AGENT_IDENTITY", "FakeReply", "FakeRoomTransport", "ScriptedReply"]

FAKE_AGENT_IDENTITY = "agent-AJ_fake"
"""The identity the fake agent participant joins under."""


@dataclass(frozen=True, slots=True)
class FakeReply:
    """One `lk.transcription` stream as the transport reports it: ``{text, final, participant}``."""

    text: str
    final: bool = True
    participant: str = FAKE_AGENT_IDENTITY


ScriptedReply = str | FakeReply
"""A scripted reply: a bare string is a final reply from the fake agent."""

_END = object()


@dataclass
class FakeRoomTransport:
    """In-memory `RoomTransport` with scripted agent replies (see the module docstring)."""

    greeting: str | None = None
    turns: list[Sequence[ScriptedReply]] = field(default_factory=list)
    responder: Callable[[str], Sequence[ScriptedReply]] | None = None
    rpc_turns: list[Sequence[ScriptedReply]] = field(default_factory=list)
    rpc_response: str = '{"ok": true, "payload": {}}'
    join_delay_s: float = 0.0
    never_joins: bool = False
    reply_delay_s: float = 0.0
    agent_identity: str = FAKE_AGENT_IDENTITY
    connect_error: Exception | None = None

    connected_with: tuple[str, str] | None = field(default=None, init=False)
    sent: list[tuple[str, str]] = field(default_factory=list, init=False)
    rpc_calls: list[tuple[str, str]] = field(default_factory=list, init=False)
    closed: bool = field(default=False, init=False)
    close_calls: int = field(default=0, init=False)
    joined: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self._tasks: set[asyncio.Task[None]] = set()
        self._dropped = False

    # -- RoomTransport -------------------------------------------------------------
    @property
    def connected(self) -> bool:
        """Whether the fake room is connected (after ``connect``, before ``close``/``drop``)."""
        return self.connected_with is not None and not self.closed and not self._dropped

    async def connect(self, server_url: str, token: str) -> None:
        """Record the connection details; raise ``connect_error`` when one is configured."""
        if self.connect_error is not None:
            raise self.connect_error
        self.connected_with = (server_url, token)

    async def wait_for_agent(self, timeout_s: float) -> str:
        """Return the agent identity once it "joins", emitting the greeting.

        Raises:
            TimeoutError: ``never_joins``, or ``join_delay_s`` exceeds ``timeout_s``.
            RuntimeError: Called before ``connect``.
        """
        if self.connected_with is None:
            raise RuntimeError("wait_for_agent before connect")
        if self.never_joins or self.join_delay_s > timeout_s:
            await asyncio.sleep(timeout_s)
            raise TimeoutError(f"the agent did not join within {timeout_s}s")
        if self.join_delay_s:
            await asyncio.sleep(self.join_delay_s)
        self.joined = True
        if self.greeting is not None:
            self._emit([self.greeting])
        return self.agent_identity

    async def send_text(self, text: str, topic: str = "lk.chat") -> None:
        """Record ``text`` and emit the next scripted turn's replies.

        Raises:
            RuntimeError: The room is not connected.
        """
        if not self.connected:
            raise RuntimeError("the room is not connected")
        self.sent.append((text, topic))
        if self.responder is not None:
            self._emit(self.responder(text))
        elif self.turns:
            self._emit(self.turns.pop(0))

    def replies(self) -> AsyncIterator[FakeReply]:
        """Every reply emitted so far and from now on; ends on ``close``/``drop``."""
        return self._iterate()

    async def rpc(self, method: str, payload: str, *, timeout_s: float = 10.0) -> str:
        """Record the call, emit the next ``rpc_turns`` batch, return ``rpc_response``.

        Raises:
            RuntimeError: The room is not connected.
        """
        if not self.connected:
            raise RuntimeError("the room is not connected")
        self.rpc_calls.append((method, payload))
        if self.rpc_turns:
            self._emit(self.rpc_turns.pop(0))
        return self.rpc_response

    async def close(self) -> None:
        """Disconnect: cancel pending emissions and end ``replies()``. Idempotent."""
        self.close_calls += 1
        if self.closed:
            return
        self.closed = True
        await self._stop()

    # -- test controls -------------------------------------------------------------
    async def drop(self) -> None:
        """Simulate the room disconnecting under the client (agent gone, network lost)."""
        self._dropped = True
        await self._stop()

    def emit(self, *replies: ScriptedReply) -> None:
        """Push unsolicited replies (e.g. a late second segment) into ``replies()``."""
        self._emit(replies)

    # -- internals -----------------------------------------------------------------
    def _emit(self, replies: Sequence[ScriptedReply]) -> None:
        normalised = [
            r if isinstance(r, FakeReply) else FakeReply(text=r, participant=self.agent_identity)
            for r in replies
        ]
        if not normalised:
            return
        if not self.reply_delay_s:
            for reply in normalised:
                self._queue.put_nowait(reply)
            return
        task = asyncio.ensure_future(self._emit_later(normalised))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _emit_later(self, replies: list[FakeReply]) -> None:
        for reply in replies:
            await asyncio.sleep(self.reply_delay_s)
            self._queue.put_nowait(reply)

    async def _stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._queue.put_nowait(_END)

    async def _iterate(self) -> AsyncIterator[FakeReply]:
        while True:
            item = await self._queue.get()
            if item is _END:
                # Leave the sentinel for any other iterator and stop.
                self._queue.put_nowait(_END)
                return
            assert isinstance(item, FakeReply)  # noqa: S101 - the queue only holds these
            yield item
