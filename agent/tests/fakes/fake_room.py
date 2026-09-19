"""In-memory fakes of `rtc.Room` / `LocalParticipant` / `RemoteParticipant`.

Real `livekit.rtc` participant/room objects are FFI-backed and cannot be
constructed in a unit test, so these fakes duck-type the handful of methods
the platform calls (`send_text`, `stream_bytes`, `perform_rpc`,
`register_rpc_method`) and record every call for assertions. `FakeRoom`
subclasses the real, pure-Python `rtc.EventEmitter` so `room.on(...)` /
`room.emit(...)` behave exactly like the genuine `Room` for track-
subscription events (verified against livekit-agents==1.8.2 / livekit==1.1.18
in the probe venv: `Room.emit("track_subscribed", track, publication,
participant)`).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from livekit import rtc
from livekit.rtc.rpc import RpcInvocationData

RpcHandler = Callable[[RpcInvocationData], "str | Awaitable[str]"]
"""A registered RPC handler: sync (returns `str`) or async (returns `Awaitable[str]`)."""


@dataclass
class RecordedTextMessage:
    """One `send_text(...)` call."""

    text: str
    topic: str
    attributes: dict[str, str]
    destination_identities: list[str] | None
    reply_to_id: str | None


@dataclass
class RecordedByteStream:
    """One `stream_bytes(...)` call: the header options plus every chunk written."""

    name: str
    mime_type: str
    topic: str
    attributes: dict[str, str]
    chunks: list[bytes] = field(default_factory=list)
    closed: bool = False
    close_reason: str = ""


@dataclass
class RecordedRpcCall:
    """One `perform_rpc(...)` call."""

    destination_identity: str
    method: str
    payload: str


class FakeByteStreamWriter:
    """Duck-types `rtc.ByteStreamWriter`: `write(bytes)` / `aclose()` / `.info`."""

    def __init__(self, record: RecordedByteStream) -> None:
        self._record = record

    async def write(self, data: bytes) -> None:
        self._record.chunks.append(data)

    async def aclose(self, *, reason: str = "", attributes: dict[str, str] | None = None) -> None:
        self._record.closed = True
        self._record.close_reason = reason

    @property
    def info(self) -> RecordedByteStream:
        return self._record


class FakeLocalParticipant:
    """Records `send_text`/`stream_bytes`/`perform_rpc`/`register_rpc_method` calls."""

    def __init__(self, identity: str = "lkap-agent") -> None:
        self.identity = identity
        self.sent_text: list[RecordedTextMessage] = []
        self.byte_streams: list[RecordedByteStream] = []
        self.rpc_calls: list[RecordedRpcCall] = []
        self.rpc_call_responses: dict[str, str] = {}
        self._rpc_handlers: dict[str, RpcHandler] = {}

    async def send_text(
        self,
        text: str,
        *,
        destination_identities: list[str] | None = None,
        topic: str = "",
        attributes: dict[str, str] | None = None,
        reply_to_id: str | None = None,
        compress: bool = True,
    ) -> RecordedTextMessage:
        msg = RecordedTextMessage(
            text=text,
            topic=topic,
            attributes=dict(attributes or {}),
            destination_identities=destination_identities,
            reply_to_id=reply_to_id,
        )
        self.sent_text.append(msg)
        return msg

    async def stream_bytes(
        self,
        name: str,
        *,
        total_size: int | None = None,
        mime_type: str = "application/octet-stream",
        attributes: dict[str, str] | None = None,
        stream_id: str | None = None,
        destination_identities: list[str] | None = None,
        topic: str = "",
    ) -> FakeByteStreamWriter:
        record = RecordedByteStream(
            name=name, mime_type=mime_type, topic=topic, attributes=dict(attributes or {})
        )
        self.byte_streams.append(record)
        return FakeByteStreamWriter(record)

    async def perform_rpc(
        self,
        *,
        destination_identity: str,
        method: str,
        payload: str,
        response_timeout: float | None = None,
        max_round_trip_latency: float | None = None,
    ) -> str:
        self.rpc_calls.append(
            RecordedRpcCall(destination_identity=destination_identity, method=method, payload=payload)
        )
        return self.rpc_call_responses.get(method, "{}")

    def register_rpc_method(
        self,
        method_name: str,
        handler: RpcHandler | None = None,
    ) -> Any:
        if handler is not None:
            self._rpc_handlers[method_name] = handler
            return handler

        def decorator(fn: RpcHandler) -> RpcHandler:
            self._rpc_handlers[method_name] = fn
            return fn

        return decorator

    async def invoke_rpc(self, method_name: str, payload: str, *, caller_identity: str = "web-ui") -> str:
        """Test helper: drive a `register_rpc_method` handler as the UI would over RPC."""
        handler = self._rpc_handlers[method_name]
        data = RpcInvocationData(
            request_id=str(uuid.uuid4()),
            caller_identity=caller_identity,
            payload=payload,
            response_timeout=10.0,
            method=method_name,
        )
        result = handler(data)
        if isinstance(result, Awaitable):
            return await result
        return result


class FakeRemoteParticipant:
    """Minimal stand-in for `rtc.RemoteParticipant`."""

    def __init__(
        self,
        identity: str = "user-guest",
        *,
        attributes: dict[str, str] | None = None,
        kind: int = rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD,
    ) -> None:
        self.identity = identity
        self.attributes: dict[str, str] = dict(attributes or {})
        self.kind = kind


class FakeRoom(rtc.EventEmitter[str]):
    """Fakes `rtc.Room`: `.local_participant`, `.remote_participants`, and
    track-subscription events (via the real `EventEmitter.on/off/emit`).
    """

    def __init__(self, name: str = "test-room") -> None:
        super().__init__()
        self.name = name
        self.local_participant = FakeLocalParticipant()
        self.remote_participants: dict[str, FakeRemoteParticipant] = {}

    def add_remote_participant(self, participant: FakeRemoteParticipant) -> FakeRemoteParticipant:
        self.remote_participants[participant.identity] = participant
        return participant

    def fire_track_subscribed(self, track: Any, publication: Any, participant: FakeRemoteParticipant) -> None:
        """Emit `track_subscribed(track, publication, participant)`, matching the real Room."""
        self.emit("track_subscribed", track, publication, participant)

    def fire_track_unsubscribed(
        self, track: Any, publication: Any, participant: FakeRemoteParticipant
    ) -> None:
        """Emit `track_unsubscribed(track, publication, participant)`, matching the real Room."""
        self.emit("track_unsubscribed", track, publication, participant)
