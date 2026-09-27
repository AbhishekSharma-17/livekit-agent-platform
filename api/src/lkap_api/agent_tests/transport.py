"""The simulated caller's room connection (V5-29): a `RoomTransport` Protocol and its rtc implementation.

The runner joins a test case's text session exactly as the console's Test chat
and the lkap-mcp ``chat_*`` tools do (the protocol notes below are the ones
``mcp/src/lkap_mcp/chat/transport.py`` verified against ``livekit`` 1.1.18):

* turns go **out** on the ``lk.chat`` text-stream topic
  (``LocalParticipant.send_text``), which the worker's ``RoomIO`` text input
  turns into a user turn;
* replies come **in** on ``lk.transcription``: livekit-agents publishes each
  agent reply segment as one delta text stream that closes with
  ``lk.transcription_final: "true"`` in the end-of-stream attributes, which
  ``TextStreamReader`` merges into ``reader.info.attributes`` once iteration
  ends. A reply is the concatenation of one stream's chunks. Transcriptions
  published under the local (caller) identity are dropped.

``livekit.rtc`` is imported lazily. It reaches the api's environment through
``lkap-packs`` → ``livekit-agents`` today (``api/uv.lock``); ``docs/v5/_asks.md``
asks for it to be declared. When it cannot be loaded, constructing
:class:`LiveKitRoomTransport` raises :class:`TransportUnavailableError` and the
run ends ``error`` with that reason instead of pretending the tests failed.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from lkap_api.logging import get_logger

if TYPE_CHECKING:
    from livekit import rtc

__all__ = [
    "TOPIC_CHAT",
    "TOPIC_TRANSCRIPTION",
    "LiveKitRoomTransport",
    "Reply",
    "RoomTransport",
    "TransportFactory",
    "TransportUnavailableError",
]

log = get_logger(__name__)

TOPIC_CHAT = "lk.chat"
TOPIC_TRANSCRIPTION = "lk.transcription"
ATTRIBUTE_TRANSCRIPTION_FINAL = "lk.transcription_final"


class TransportUnavailableError(RuntimeError):
    """The ``livekit`` rtc SDK cannot be loaded in this process."""


@dataclass(frozen=True, slots=True)
class Reply:
    """One ``lk.transcription`` stream, read to its end."""

    text: str
    final: bool
    participant: str


class RoomTransport(Protocol):
    """What the runner needs from a room connection (real: :class:`LiveKitRoomTransport`)."""

    async def connect(self, server_url: str, token: str) -> None:
        """Join the room with the participant token the runner minted."""
        ...

    async def wait_for_agent(self, timeout_s: float) -> str:
        """Return the agent participant's identity once it is in the room.

        Raises:
            TimeoutError: It did not join within ``timeout_s``.
        """
        ...

    async def send_text(self, text: str) -> None:
        """Send one caller turn on ``lk.chat``."""
        ...

    def replies(self) -> AsyncIterator[Reply]:
        """Every transcription stream from others, read to its end; ends when the room goes away."""
        ...

    async def close(self) -> None:
        """Leave the room. Idempotent."""
        ...


TransportFactory = Callable[[], RoomTransport]


class _StreamInfoLike(Protocol):
    @property
    def attributes(self) -> dict[str, str] | None: ...


class _TextStreamReaderLike(Protocol):
    @property
    def info(self) -> _StreamInfoLike: ...

    def __aiter__(self) -> AsyncIterator[str]: ...


async def _read_stream(reader: _TextStreamReaderLike, participant: str) -> Reply:
    chunks: list[str] = []
    aborted = False
    try:
        async for chunk in reader:
            chunks.append(chunk)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - rtc.StreamError and friends: keep what arrived
        aborted = True
    attributes = reader.info.attributes or {}
    final = not aborted and attributes.get(ATTRIBUTE_TRANSCRIPTION_FINAL, "true") != "false"
    return Reply(text="".join(chunks), final=final, participant=participant)


_END: Any = object()


class LiveKitRoomTransport:
    """:class:`RoomTransport` over ``livekit.rtc`` (one ``Room`` per case)."""

    def __init__(self) -> None:
        """Check that ``livekit.rtc`` loads, before a session is minted.

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

    def _on_transcription(self, reader: _TextStreamReaderLike, participant_identity: str) -> None:
        task = asyncio.ensure_future(self._read(reader, participant_identity))
        self._readers.add(task)
        task.add_done_callback(self._readers.discard)

    async def _read(self, reader: _TextStreamReaderLike, participant_identity: str) -> None:
        reply = await _read_stream(reader, participant_identity)
        if self._local_identity() == participant_identity:
            return
        if reply.text.strip():
            self._queue.put_nowait(reply)

    async def connect(self, server_url: str, token: str) -> None:
        """Create the ``Room``, register the ``lk.transcription`` handler and connect.

        Raises:
            ConnectionError: The room refused the connection.
        """
        from livekit import rtc

        room = rtc.Room()
        room.register_text_stream_handler(TOPIC_TRANSCRIPTION, self._on_transcription)
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
        """
        room = self._require_room()
        from livekit import rtc

        loop = asyncio.get_running_loop()
        joined: asyncio.Future[str] = loop.create_future()

        def pick() -> str | None:
            remotes = list(room.remote_participants.values())
            for participant in remotes:
                if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
                    return participant.identity
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

    async def send_text(self, text: str) -> None:
        """Send ``text`` on ``lk.chat`` from the local participant."""
        room = self._require_room()
        await room.local_participant.send_text(text, topic=TOPIC_CHAT)

    def replies(self) -> AsyncIterator[Reply]:
        """Replies as they complete; ends when the room disconnects or the agent leaves."""
        return self._iterate()

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

    def _require_room(self) -> rtc.Room:
        if self._room is None or self._closed:
            raise RuntimeError("the room is not connected")
        return self._room

    def _local_identity(self) -> str | None:
        room = self._room
        if room is None:
            return None
        try:
            identity: str = room.local_participant.identity
        except Exception:  # noqa: BLE001 - before connect the SDK raises a bare Exception
            return None
        return identity

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
            if isinstance(item, Reply):
                yield item
