"""Unit tests for `lkap_agent.vision.FrameBuffer`.

A real `rtc.VideoStream` is FFI-backed and cannot be constructed here, so
these tests inject a fake `video_stream_factory` and fake `rtc.VideoFrame`
stand-ins (plain objects with `.width`/`.height`); `TrackKind`/`TrackSource`
are pure protobuf-generated int enums and are safe to use directly, offline.
`encode()` (PIL/FFI-backed) is monkeypatched for the `latest_jpeg` tests.

Frame ages are exercised with an injected fake `monotonic` clock
(`FrameBuffer(..., monotonic=clock)`), never by monkeypatching the real
`time.monotonic` — doing that would also freeze asyncio's own scheduling
clock and hang every `await asyncio.sleep(...)` in the test.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from fakes.fake_room import FakeRemoteParticipant, FakeRoom
from livekit import rtc

from lkap_agent import vision
from lkap_agent.vision import FrameBuffer, _resize_options


@dataclass
class FakeTrack:
    sid: str


@dataclass
class FakePublication:
    kind: int
    source: int


class FakeClock:
    """A settable `time.monotonic`-shaped clock for deterministic age tests."""

    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeVideoStream:
    """Fakes `rtc.VideoStream`: push frames in, iterate/aclose out."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self.closed = False

    def push(self, frame: object) -> None:
        self._queue.put_nowait(SimpleNamespace(frame=frame))

    def stop(self) -> None:
        self._queue.put_nowait(None)

    def __aiter__(self) -> FakeVideoStream:
        return self

    async def __anext__(self) -> Any:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def aclose(self) -> None:
        self.closed = True


def _camera_publication() -> FakePublication:
    return FakePublication(kind=rtc.TrackKind.KIND_VIDEO, source=rtc.TrackSource.SOURCE_CAMERA)


def _screen_publication() -> FakePublication:
    return FakePublication(kind=rtc.TrackKind.KIND_VIDEO, source=rtc.TrackSource.SOURCE_SCREENSHARE)


async def test_latest_returns_freshest_frame_across_sources() -> None:
    room = FakeRoom()
    participant = room.add_remote_participant(FakeRemoteParticipant("user-1"))
    cam_stream, screen_stream = FakeVideoStream(), FakeVideoStream()
    streams = {"cam-1": cam_stream, "screen-1": screen_stream}

    buf = FrameBuffer(room, video_stream_factory=lambda track: streams[track.sid])
    buf.start()
    room.fire_track_subscribed(FakeTrack("cam-1"), _camera_publication(), participant)
    room.fire_track_subscribed(FakeTrack("screen-1"), _screen_publication(), participant)

    cam_frame, screen_frame = SimpleNamespace(width=640, height=480), SimpleNamespace(width=1920, height=1080)
    cam_stream.push(cam_frame)
    await asyncio.sleep(0.01)
    screen_stream.push(screen_frame)
    await asyncio.sleep(0.01)

    snap = buf.latest()
    assert snap is not None
    assert snap.source == "screen"
    assert snap.frame is screen_frame

    buf.stop()


async def test_latest_ignores_non_camera_screen_sources_and_audio() -> None:
    room = FakeRoom()
    participant = room.add_remote_participant(FakeRemoteParticipant("user-1"))
    buf = FrameBuffer(room, video_stream_factory=lambda track: FakeVideoStream())
    buf.start()

    mic_pub = FakePublication(kind=rtc.TrackKind.KIND_AUDIO, source=rtc.TrackSource.SOURCE_MICROPHONE)
    room.fire_track_subscribed(FakeTrack("mic-1"), mic_pub, participant)
    unknown_pub = FakePublication(kind=rtc.TrackKind.KIND_VIDEO, source=rtc.TrackSource.SOURCE_UNKNOWN)
    room.fire_track_subscribed(FakeTrack("unknown-1"), unknown_pub, participant)

    assert buf.latest() is None
    buf.stop()


async def test_latest_respects_max_age() -> None:
    room = FakeRoom()
    participant = room.add_remote_participant(FakeRemoteParticipant("user-1"))
    stream = FakeVideoStream()
    clock = FakeClock(100.0)
    buf = FrameBuffer(room, video_stream_factory=lambda track: stream, monotonic=clock)
    buf.start()

    room.fire_track_subscribed(FakeTrack("cam-1"), _camera_publication(), participant)
    stream.push(SimpleNamespace(width=100, height=100))
    await asyncio.sleep(0.01)

    clock.value = 110.0
    assert buf.latest(max_age_s=1.0) is None
    fresh = buf.latest(max_age_s=20.0)
    assert fresh is not None
    assert fresh.age_s == 10.0

    buf.stop()


async def test_track_unsubscribed_clears_stored_frame() -> None:
    room = FakeRoom()
    participant = room.add_remote_participant(FakeRemoteParticipant("user-1"))
    stream = FakeVideoStream()
    buf = FrameBuffer(room, video_stream_factory=lambda track: stream)
    buf.start()
    track, pub = FakeTrack("cam-1"), _camera_publication()
    room.fire_track_subscribed(track, pub, participant)
    stream.push(SimpleNamespace(width=100, height=100))
    await asyncio.sleep(0.01)
    assert buf.latest() is not None

    room.fire_track_unsubscribed(track, pub, participant)
    await asyncio.sleep(0.01)
    assert buf.latest() is None
    buf.stop()


async def test_stop_cancels_consumption_tasks() -> None:
    room = FakeRoom()
    participant = room.add_remote_participant(FakeRemoteParticipant("user-1"))
    stream = FakeVideoStream()
    buf = FrameBuffer(room, video_stream_factory=lambda track: stream)
    buf.start()
    room.fire_track_subscribed(FakeTrack("cam-1"), _camera_publication(), participant)
    await asyncio.sleep(0.01)

    buf.stop()
    await asyncio.sleep(0.01)
    assert buf._tasks == {}


async def test_latest_jpeg_returns_none_when_no_fresh_frame() -> None:
    room = FakeRoom()
    buf = FrameBuffer(room, video_stream_factory=lambda track: FakeVideoStream())
    assert await buf.latest_jpeg() is None


async def test_latest_jpeg_encodes_freshest_frame_and_resizes_when_wide(monkeypatch: Any) -> None:
    room = FakeRoom()
    participant = room.add_remote_participant(FakeRemoteParticipant("user-1"))
    stream = FakeVideoStream()
    buf = FrameBuffer(room, video_stream_factory=lambda track: stream)
    buf.start()
    room.fire_track_subscribed(FakeTrack("cam-1"), _camera_publication(), participant)
    frame = SimpleNamespace(width=2000, height=1000)
    stream.push(frame)
    await asyncio.sleep(0.01)

    captured: dict[str, Any] = {}

    def fake_encode(f: object, options: object) -> bytes:
        captured["frame"] = f
        captured["options"] = options
        return b"jpeg-bytes"

    monkeypatch.setattr(vision, "encode", fake_encode)

    result = await buf.latest_jpeg(max_width=1024)
    assert result is not None
    data, snap = result
    assert data == b"jpeg-bytes"
    assert snap.frame is frame
    assert captured["frame"] is frame
    resize = captured["options"].resize_options
    assert resize is not None
    assert (resize.width, resize.height, resize.strategy) == (1024, 1000, "scale_aspect_fit")

    buf.stop()


def test_resize_options_none_when_frame_already_narrow() -> None:
    frame = SimpleNamespace(width=800, height=600)
    assert _resize_options(frame, max_width=1024) is None  # type: ignore[arg-type]


def test_resize_options_scales_down_preserving_height_hint() -> None:
    frame = SimpleNamespace(width=4000, height=2000)
    options = _resize_options(frame, max_width=1024)  # type: ignore[arg-type]
    assert options is not None
    assert options.width == 1024
    assert options.height == 2000
    assert options.strategy == "scale_aspect_fit"


# --- D-W2-4: UI-selected source preference -----------------------------------------


async def _two_source_buffer(clock: FakeClock) -> tuple[FrameBuffer, FakeVideoStream, FakeVideoStream]:
    room = FakeRoom()
    participant = room.add_remote_participant(FakeRemoteParticipant("user-1"))
    cam_stream, screen_stream = FakeVideoStream(), FakeVideoStream()
    streams = {"cam-1": cam_stream, "screen-1": screen_stream}
    buf = FrameBuffer(room, video_stream_factory=lambda track: streams[track.sid], monotonic=clock)
    buf.start()
    room.fire_track_subscribed(FakeTrack("cam-1"), _camera_publication(), participant)
    room.fire_track_subscribed(FakeTrack("screen-1"), _screen_publication(), participant)
    return buf, cam_stream, screen_stream


async def test_latest_returns_the_preferred_source_when_it_is_fresh() -> None:
    clock = FakeClock(100.0)
    buf, cam_stream, screen_stream = await _two_source_buffer(clock)
    cam_stream.push(SimpleNamespace(width=640, height=480))
    await asyncio.sleep(0.01)
    clock.value = 101.0
    screen_stream.push(SimpleNamespace(width=1920, height=1080))  # fresher
    await asyncio.sleep(0.01)

    buf.set_preferred_source("camera")
    snap = buf.latest(max_age_s=8.0)

    assert snap is not None
    assert snap.source == "camera"
    buf.stop()


async def test_latest_falls_back_to_the_other_source_when_the_preferred_one_is_stale() -> None:
    clock = FakeClock(100.0)
    buf, cam_stream, screen_stream = await _two_source_buffer(clock)
    cam_stream.push(SimpleNamespace(width=640, height=480))
    await asyncio.sleep(0.01)
    clock.value = 120.0
    screen_stream.push(SimpleNamespace(width=1920, height=1080))
    await asyncio.sleep(0.01)

    buf.set_preferred_source("camera")  # camera frame is 20 s old
    snap = buf.latest(max_age_s=8.0)

    assert snap is not None
    assert snap.source == "screen"
    buf.stop()


async def test_latest_falls_back_when_the_preferred_source_has_no_frame() -> None:
    clock = FakeClock(100.0)
    buf, cam_stream, _screen_stream = await _two_source_buffer(clock)
    cam_stream.push(SimpleNamespace(width=640, height=480))
    await asyncio.sleep(0.01)

    buf.set_preferred_source("screen")
    snap = buf.latest(max_age_s=8.0)

    assert snap is not None
    assert snap.source == "camera"
    buf.stop()


async def test_clearing_the_preference_restores_freshest_wins() -> None:
    clock = FakeClock(100.0)
    buf, cam_stream, screen_stream = await _two_source_buffer(clock)
    cam_stream.push(SimpleNamespace(width=640, height=480))
    await asyncio.sleep(0.01)
    clock.value = 101.0
    screen_stream.push(SimpleNamespace(width=1920, height=1080))
    await asyncio.sleep(0.01)

    buf.set_preferred_source("camera")
    buf.set_preferred_source(None)
    snap = buf.latest(max_age_s=8.0)

    assert snap is not None
    assert snap.source == "screen"
    buf.stop()


# --- D-W2-8 R3: JPEG data URL encoding --------------------------------------------


def test_encode_jpeg_data_url_bounds_the_image_to_512_px() -> None:
    import base64
    import io

    from PIL import Image

    frame = rtc.VideoFrame(
        width=1280, height=720, type=rtc.VideoBufferType.RGBA, data=b"\x80" * (1280 * 720 * 4)
    )

    url = vision.encode_jpeg_data_url(frame)

    assert url.startswith("data:image/jpeg;base64,")
    image = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert image.format == "JPEG"
    assert max(image.size) <= 512
    assert image.size[0] == 512


def test_encode_jpeg_data_url_keeps_a_small_frame_at_its_size() -> None:
    import base64
    import io

    from PIL import Image

    frame = rtc.VideoFrame(width=64, height=48, type=rtc.VideoBufferType.RGBA, data=b"\x10" * (64 * 48 * 4))

    url = vision.encode_jpeg_data_url(frame)

    image = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert image.size == (64, 48)
