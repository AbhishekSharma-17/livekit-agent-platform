"""The test chat's room connection: a `RoomTransport` Protocol and its `livekit.rtc` implementation.

The MCP process joins the text session's room as the "user" participant,
exactly as ``scripts/smoke_v2.sh`` step 7 and the console Test chat do
(AGENT-ACCESS D-V3-3, R-V3-4):

* turns go **out** on the ``lk.chat`` text-stream topic
  (``LocalParticipant.send_text``), which the worker's ``RoomIO`` text input
  turns into a user turn;
* replies come **in** on ``lk.transcription``: livekit-agents 1.8.2
  (``voice/room_io/_output.py``) publishes each agent reply segment as **one
  delta text stream** that opens with ``lk.transcription_final: "false"`` and
  closes with ``aclose(attributes={"lk.transcription_final": "true"})``. The
  closing attributes arrive with the stream's end-of-stream frame, which
  ``TextStreamReader.__anext__`` merges into ``reader.info.attributes`` only
  once iteration ends. So a reply is the concatenation of one stream's chunks,
  and it is final when the reader ends with that attribute ``"true"`` (or with
  no transcription attribute at all). User-side transcriptions (non-delta, one
  stream per interim update) are published under the *user's* identity and
  are filtered out by the chat manager, which keeps only the agent's replies;
* rewind/edit go over the existing ``lkap.agent.action`` RPC
  (``LocalParticipant.perform_rpc`` to the agent participant).

``livekit.rtc`` is imported lazily (checked in ``LiveKitRoomTransport()``) so a
machine where the native wheel does not load still starts the MCP server; the
chat tools then answer ``code="chat_unavailable"`` (PLAN-V3 §4 risk table).

Verified against ``livekit`` 1.1.18 (``mcp/.venv``): ``Room.register_text_stream_handler``
(one handler per topic, sync ``(reader, participant_identity)`` callback),
``TextStreamReader`` (async iterator of ``str`` chunks, ``info.attributes``),
``LocalParticipant.send_text(text, topic=...)``,
``LocalParticipant.perform_rpc(destination_identity=, method=, payload=, response_timeout=)``,
``Participant.kind`` (``ParticipantKind.PARTICIPANT_KIND_AGENT``), and the
``participant_connected`` / ``participant_disconnected`` / ``disconnected``
room events.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from livekit import rtc

__all__ = [
    "ATTRIBUTE_TRANSCRIPTION_FINAL",
    "TOPIC_CHAT",
    "TOPIC_TRANSCRIPTION",
    "LiveKitRoomTransport",
    "Reply",
    "RoomReply",
    "RoomTransport",
    "TextStreamReaderLike",
    "TransportFactory",
    "TransportUnavailableError",
    "read_transcription_stream",
]

logger = logging.getLogger(__name__)

TOPIC_CHAT = "lk.chat"
TOPIC_TRANSCRIPTION = "lk.transcription"
ATTRIBUTE_TRANSCRIPTION_FINAL = "lk.transcription_final"


class TransportUnavailableError(RuntimeError):
    """The ``livekit`` rtc SDK cannot be loaded in this process (``chat_unavailable``)."""


class RoomReply(Protocol):
    """One ``lk.transcription`` stream, read to its end: ``{text, final, participant}``."""

    @property
    def text(self) -> str: ...

    @property
    def final(self) -> bool: ...

    @property
    def participant(self) -> str: ...


@dataclass(frozen=True, slots=True)
class Reply:
    """The concrete :class:`RoomReply` :class:`LiveKitRoomTransport` yields."""

    text: str
    final: bool
    participant: str


class RoomTransport(Protocol):
    """What the chat manager needs from a room connection (real: :class:`LiveKitRoomTransport`).

    ``lkap_testing.fake_room.FakeRoomTransport`` implements it structurally for
    offline tests. ``timeout_s`` (not ``timeout``) because ruff's ASYNC109 bans
    a ``timeout`` parameter on coroutines.
    """

    @property
    def connected(self) -> bool:
        """Whether the room is connected (false after ``close`` or a drop)."""
        ...

    async def connect(self, server_url: str, token: str) -> None:
        """Join the room with the participant token ``text-sessions`` minted."""
        ...

    async def wait_for_agent(self, timeout_s: float) -> str:
        """Return the agent participant's identity once it is in the room.

        Raises:
            TimeoutError: It did not join within ``timeout_s``.
        """
        ...

    async def send_text(self, text: str, topic: str = TOPIC_CHAT) -> None:
        """Send one user turn on ``topic`` (``lk.chat``)."""
        ...

    def replies(self) -> AsyncIterator[RoomReply]:
        """Every ``lk.transcription`` stream, read to its end; ends when the room goes away."""
        ...

    async def rpc(self, method: str, payload: str, *, timeout_s: float = 10.0) -> str:
        """Call ``method`` on the agent participant and return its response payload."""
        ...

    async def close(self) -> None:
        """Leave the room. Idempotent."""
        ...


TransportFactory = Callable[[], RoomTransport]
"""Builds one fresh transport per chat (tests pass a ``FakeRoomTransport`` factory)."""


class _StreamInfoLike(Protocol):
    @property
    def attributes(self) -> dict[str, str] | None: ...


class TextStreamReaderLike(Protocol):
    """The slice of ``rtc.TextStreamReader`` the transcription handler reads."""

    @property
    def info(self) -> _StreamInfoLike: ...

    def __aiter__(self) -> AsyncIterator[str]: ...


async def read_transcription_stream(reader: TextStreamReaderLike, participant: str) -> Reply:
    """Read one ``lk.transcription`` stream to its end and return it as a :class:`Reply`.

    Chunks are concatenated in order. ``final`` is decided after the reader
    ends, from the end-of-stream attributes (see the module docstring); a
    stream the sender aborted (``StreamError`` from the reader) is returned
    with what arrived and ``final=False``.
    """
    chunks: list[str] = []
    aborted = False
    try:
        async for chunk in reader:
            chunks.append(chunk)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - rtc.StreamError and friends: keep what arrived
        logger.debug("transcription stream ended abnormally: %s", type(exc).__name__)
        aborted = True
    attributes = reader.info.attributes or {}
    final = not aborted and attributes.get(ATTRIBUTE_TRANSCRIPTION_FINAL, "true") != "false"
    return Reply(text="".join(chunks), final=final, participant=participant)


_END: Any = object()


class LiveKitRoomTransport:
    """:class:`RoomTransport` over ``livekit.rtc`` (one ``Room`` per chat)."""

    def __init__(self) -> None:
        """Check that ``livekit.rtc`` loads, so a chat fails before a session is minted.

        Raises:
            TransportUnavailableError: The rtc SDK (a native wheel) cannot be imported.
        """
        try:
            from livekit import rtc  # noqa: F401
        except Exception as exc:  # noqa: BLE001 - a broken native wheel raises OSError/ImportError
            raise TransportUnavailableError(f"the livekit rtc SDK is unavailable: {exc}") from exc
        self._room: rtc.Room | None = None
        self._queue: asyncio.Queue[Reply | object] = asyncio.Queue()
        self._readers: set[asyncio.Task[None]] = set()
        self._agent_identity: str | None = None
        self._closed = False
        self._ended = False

    # -- the transcription handler (unit-tested with a fake reader) -----------------
    def on_transcription(self, reader: TextStreamReaderLike, participant_identity: str) -> None:
        """``register_text_stream_handler`` callback: read the stream in a task, queue the reply."""
        task = asyncio.ensure_future(self._read(reader, participant_identity))
        self._readers.add(task)
        task.add_done_callback(self._readers.discard)

    async def _read(self, reader: TextStreamReaderLike, participant_identity: str) -> None:
        reply = await read_transcription_stream(reader, participant_identity)
        if self._local_identity() == participant_identity:
            return
        if reply.text.strip():
            self._queue.put_nowait(reply)

    # -- RoomTransport ---------------------------------------------------------------
    @property
    def connected(self) -> bool:
        """Whether the room is connected and not closed or dropped."""
        return self._room is not None and not self._closed and not self._ended and self._room.isconnected()

    async def connect(self, server_url: str, token: str) -> None:
        """Create the ``Room``, register the ``lk.transcription`` handler and connect.

        Raises:
            ConnectionError: The room refused the connection.
        """
        from livekit import rtc

        room = rtc.Room()
        room.register_text_stream_handler(TOPIC_TRANSCRIPTION, self.on_transcription)
        room.on("disconnected", self._on_disconnected)
        room.on("participant_disconnected", self._on_participant_disconnected)
        self._room = room
        try:
            await room.connect(server_url, token)
        except rtc.ConnectError as exc:
            self._end()
            raise ConnectionError(f"could not join the room: {exc.message}") from exc

    async def wait_for_agent(self, timeout_s: float) -> str:
        """Return the agent's identity (``PARTICIPANT_KIND_AGENT`` first, any remote otherwise).

        Raises:
            TimeoutError: No agent joined within ``timeout_s``.
            RuntimeError: Called before ``connect``.
        """
        room = self._require_room()
        from livekit import rtc

        loop = asyncio.get_running_loop()
        joined: asyncio.Future[str] = loop.create_future()

        def pick() -> str | None:
            remotes = list(room.remote_participants.values())
            for p in remotes:
                if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
                    return p.identity
            return remotes[0].identity if remotes else None

        def on_connected(_participant: rtc.RemoteParticipant) -> None:
            identity = pick()
            if identity is not None and not joined.done():
                joined.set_result(identity)

        room.on("participant_connected", on_connected)
        try:
            identity = pick()
            if identity is None:
                identity = await asyncio.wait_for(joined, timeout_s)
        finally:
            room.off("participant_connected", on_connected)
        self._agent_identity = identity
        return identity

    async def send_text(self, text: str, topic: str = TOPIC_CHAT) -> None:
        """Send ``text`` on ``topic`` from the local participant."""
        room = self._require_room()
        await room.local_participant.send_text(text, topic=topic)

    def replies(self) -> AsyncIterator[Reply]:
        """Agent replies as they complete; ends when the room disconnects or the agent leaves."""
        return self._iterate()

    async def rpc(self, method: str, payload: str, *, timeout_s: float = 10.0) -> str:
        """``perform_rpc`` to the agent participant found by :meth:`wait_for_agent`.

        Raises:
            RuntimeError: No agent identity yet.
        """
        room = self._require_room()
        if self._agent_identity is None:
            raise RuntimeError("rpc before the agent joined")
        return await room.local_participant.perform_rpc(
            destination_identity=self._agent_identity,
            method=method,
            payload=payload,
            response_timeout=timeout_s,
        )

    async def close(self) -> None:
        """Unregister the handler, cancel pending reads and disconnect. Idempotent."""
        if self._closed:
            return
        self._closed = True
        for task in list(self._readers):
            task.cancel()
        for task in list(self._readers):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        room = self._room
        if room is not None:
            room.unregister_text_stream_handler(TOPIC_TRANSCRIPTION)
            with contextlib.suppress(Exception):
                await room.disconnect()
        self._end()

    # -- internals -----------------------------------------------------------------
    def _require_room(self) -> rtc.Room:
        if self._room is None or self._closed:
            raise RuntimeError("the room is not connected")
        return self._room

    def _local_identity(self) -> str | None:
        room = self._room
        if room is None:
            return None
        try:
            return room.local_participant.identity
        except Exception:  # noqa: BLE001 - before connect the SDK raises a bare Exception
            return None

    def _on_disconnected(self, *_args: object) -> None:
        self._end()

    def _on_participant_disconnected(self, participant: rtc.RemoteParticipant) -> None:
        if self._agent_identity is not None and participant.identity == self._agent_identity:
            self._end()

    def _end(self) -> None:
        if not self._ended:
            self._ended = True
            self._queue.put_nowait(_END)

    async def _iterate(self) -> AsyncIterator[Reply]:
        while True:
            item = await self._queue.get()
            if item is _END:
                self._queue.put_nowait(_END)
                return
            assert isinstance(item, Reply)  # noqa: S101 - the queue only holds replies
            yield item
