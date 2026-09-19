"""`FrameBuffer` — latest-frame-per-source buffer for camera/screen vision.

Implements `packs.base.FrameBufferProto` (docs/CONTRACTS.md §8;
docs/ARCHITECTURE.md §8). Subscribes to `track_subscribed`/`track_unsubscribed`
on the session's `rtc.Room` for the linked participant's `SOURCE_CAMERA` and
`SOURCE_SCREENSHARE` video tracks, and keeps the freshest decoded
`rtc.VideoFrame` per source. Active in both realtime and cascaded pipeline
modes (realtime additionally wires `RoomOptions(video_input=True)` at the
`SessionBuilder`, W1-AGENT-CORE; this buffer independently backs
`pin_frame`/`describe_current_frame` and cascaded-mode vision injection).

The concrete `rtc.VideoStream` is FFI-backed and cannot be constructed in a
unit test, so track-to-stream construction is injected via
`video_stream_factory` (default `rtc.VideoStream.from_track`) — tests pass a
fake async-iterable of frame events instead. Frame ages are computed from an
injectable `monotonic` clock (default `time.monotonic`) rather than the
module-level `time.monotonic` directly, since monkeypatching the real
`time.monotonic` in a test would also freeze asyncio's own scheduling clock.
"""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from livekit import rtc
from livekit.agents.utils.images import EncodeOptions, ResizeOptions, encode
from packs.base import FrameSnapshot, FrameSource

from lkap_agent.logging import get_logger

__all__ = ["FrameBuffer", "encode_jpeg_data_url"]

_SOURCE_BY_TRACK_SOURCE: dict[Any, FrameSource] = {
    rtc.TrackSource.SOURCE_CAMERA: "camera",
    rtc.TrackSource.SOURCE_SCREENSHARE: "screen",
}


class _VideoFrameEventLike(Protocol):
    frame: rtc.VideoFrame


class _VideoStreamLike(Protocol):
    def __aiter__(self) -> AsyncIterator[_VideoFrameEventLike]: ...
    async def aclose(self) -> None: ...


VideoStreamFactory = Callable[[rtc.Track], _VideoStreamLike]
#: A `time.monotonic`-shaped clock, injectable so tests never have to patch the
#: real stdlib `time.monotonic` (which would also freeze asyncio's own scheduling).
Clock = Callable[[], float]


def _default_video_stream_factory(track: rtc.Track) -> _VideoStreamLike:
    return rtc.VideoStream.from_track(track=track)


@dataclass
class _Entry:
    frame: rtc.VideoFrame
    monotonic_ts: float


class FrameBuffer:
    """Keeps the freshest video frame per source for one session's room."""

    def __init__(
        self,
        room: rtc.Room,
        *,
        participant_identity: str | None = None,
        video_stream_factory: VideoStreamFactory = _default_video_stream_factory,
        monotonic: Clock = time.monotonic,
        log: Any = None,
    ) -> None:
        self._room = room
        self._participant_identity = participant_identity
        self._video_stream_factory = video_stream_factory
        self._monotonic = monotonic
        self._latest: dict[FrameSource, _Entry] = {}
        self._sid_to_source: dict[str, FrameSource] = {}
        self._streams: dict[str, _VideoStreamLike] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._preferred: FrameSource | None = None
        self._log = log or get_logger(__name__)

    def start(self) -> None:
        """Subscribe to the room's video track-subscription events."""
        self._room.on("track_subscribed", self._on_track_subscribed)
        self._room.on("track_unsubscribed", self._on_track_unsubscribed)

    def stop(self) -> None:
        """Unsubscribe and cancel every in-flight frame-consumption task."""
        self._room.off("track_subscribed", self._on_track_subscribed)
        self._room.off("track_unsubscribed", self._on_track_unsubscribed)
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        self._streams.clear()
        self._sid_to_source.clear()

    def set_preferred_source(self, source: FrameSource | None) -> None:
        """Prefer `source` in :meth:`latest` while it has a fresh frame (DECISIONS-W2 D-W2-4).

        Set from the UI's `set_video_source` action; `None` restores
        freshest-wins across sources.
        """
        self._preferred = source
        self._log.debug("frame_buffer_preferred_source", source=source)

    # --- packs.base.FrameBufferProto -------------------------------------------

    def latest(self, max_age_s: float | None = None) -> FrameSnapshot | None:
        """The preferred source's fresh frame, else the freshest across sources.

        Returns `None` when no source has a frame within `max_age_s`.
        """
        now = self._monotonic()
        candidates: list[FrameSnapshot] = []
        for source, entry in self._latest.items():
            age_s = now - entry.monotonic_ts
            if max_age_s is None or age_s <= max_age_s:
                candidates.append(FrameSnapshot(frame=entry.frame, source=source, age_s=age_s))
        if not candidates:
            return None
        if self._preferred is not None:
            for snap in candidates:
                if snap.source == self._preferred:
                    return snap
        return min(candidates, key=lambda snap: snap.age_s)

    async def latest_jpeg(
        self, max_age_s: float | None = None, max_width: int = 1024
    ) -> tuple[bytes, FrameSnapshot] | None:
        """JPEG-encode the freshest frame, or `None` if none is fresh enough."""
        snapshot = self.latest(max_age_s)
        if snapshot is None:
            return None
        options = EncodeOptions(format="JPEG", resize_options=_resize_options(snapshot.frame, max_width))
        data = await asyncio.to_thread(encode, snapshot.frame, options)
        return data, snapshot

    # --- track subscription handling --------------------------------------------

    def _on_track_subscribed(
        self, track: rtc.Track, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant
    ) -> None:
        if self._participant_identity is not None and participant.identity != self._participant_identity:
            return
        if publication.kind != rtc.TrackKind.KIND_VIDEO:
            return
        source = _SOURCE_BY_TRACK_SOURCE.get(publication.source)
        if source is None:
            return

        stream = self._video_stream_factory(track)
        self._streams[track.sid] = stream
        self._sid_to_source[track.sid] = source
        self._tasks[track.sid] = asyncio.create_task(self._consume(stream, source, track.sid))
        self._log.debug("frame_buffer_track_subscribed", source=source, track_sid=track.sid)

    def _on_track_unsubscribed(
        self, track: rtc.Track, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant
    ) -> None:
        sid = track.sid
        task = self._tasks.pop(sid, None)
        if task is not None:
            task.cancel()
        self._streams.pop(sid, None)
        source = self._sid_to_source.pop(sid, None)
        if source is not None:
            self._latest.pop(source, None)
            self._log.debug("frame_buffer_track_unsubscribed", source=source, track_sid=sid)

    async def _consume(self, stream: _VideoStreamLike, source: FrameSource, sid: str) -> None:
        try:
            async for event in stream:
                self._latest[source] = _Entry(frame=event.frame, monotonic_ts=self._monotonic())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a broken video stream must not crash the session
            self._log.exception("frame_buffer_stream_error", source=source, track_sid=sid)
        finally:
            await stream.aclose()


def _resize_options(frame: rtc.VideoFrame, max_width: int) -> ResizeOptions | None:
    """Downscale to `max_width`, preserving aspect ratio; `None` if already narrow enough."""
    if frame.width <= max_width:
        return None
    return ResizeOptions(width=max_width, height=frame.height, strategy="scale_aspect_fit")


def encode_jpeg_data_url(frame: rtc.VideoFrame, max_px: int = 512) -> str:
    """Encode `frame` as a `data:image/jpeg;base64,...` URL no larger than `max_px`.

    Used for cascaded per-turn vision injection (DECISIONS-W2 D-W2-8 R3): the
    chat history then holds a ~20-40 KB string instead of a raw frame buffer.
    CPU-bound; call it through `asyncio.to_thread`.

    Args:
        frame: The decoded video frame.
        max_px: Bounding box for both sides; aspect ratio is preserved.

    Returns:
        The JPEG as a base64 data URL.
    """
    resize = (
        ResizeOptions(width=max_px, height=max_px, strategy="scale_aspect_fit")
        if frame.width > max_px or frame.height > max_px
        else None
    )
    data = encode(frame, EncodeOptions(format="JPEG", resize_options=resize))
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
