"""Fakes of the LiveKit SIP surface a warm transfer and a voicemail touch (V5-32).

No trunk and no LiveKit server: these duck-type exactly what livekit-agents 1.8.3's
beta `WarmTransferTask` and `lkap_agent.telephony` call, and record it in one shared
`log` so a test can assert the order of the steps:

* `FakeSipRoom` — the caller's room (a `FakeRoom` with the SIP leg in it).
* `FakeSipApi` — `api.sip.create_sip_participant` (the consult dial; can be told to
  fail like an unanswered phone) and `api.room.move_participant` / `delete_room`.
* `FakeSipJobContext` — what `get_job_context()` returns inside the task: `room`,
  `api`, `_info.url`, `delete_room()`, `shutdown()`.
* `consult_fakes(log)` — stand-ins for the task's private `rtc.Room()` and the
  `AgentSession` that briefs the person (patched into the SDK module by the
  tests, the LiveKit boundary).
* `FakeHoldPlayer` — the task's `BackgroundAudioPlayer` (the hold music).
* `FakeVoiceSession` / `FakeActivity` — the caller-side `AgentSession` slice
  (`say`, `input`/`output` toggles, `llm`) and the activity an `AgentTask` runs in.
"""

from __future__ import annotations

import asyncio
from typing import Any

from livekit import rtc

from fakes.fake_room import FakeRemoteParticipant, FakeRoom

SIP_KIND = rtc.ParticipantKind.PARTICIPANT_KIND_SIP


def caller_leg(identity: str = "sip-call1", status: str = "active") -> FakeRemoteParticipant:
    """The caller's SIP participant."""
    return FakeRemoteParticipant(
        identity,
        attributes={
            "sip.callStatus": status,
            "sip.phoneNumber": "+15557654321",
            "sip.trunkPhoneNumber": "+15551230000",
        },
        kind=SIP_KIND,
    )


class FakeSipRoom(FakeRoom):
    """The caller's room: a `FakeRoom` holding the SIP leg, always connected."""

    def __init__(self, name: str = "lkap-call-abc", *, leg: FakeRemoteParticipant | None = None) -> None:
        super().__init__(name)
        self.add_remote_participant(leg or caller_leg())

    def isconnected(self) -> bool:
        return True


class _Sip:
    def __init__(self, owner: FakeSipApi) -> None:
        self._owner = owner

    async def create_sip_participant(self, request: Any, **_kwargs: Any) -> Any:
        self._owner.log.append("sip:dial")
        self._owner.dials.append(request)
        if self._owner.dial_error is not None:
            raise self._owner.dial_error
        return object()


class _RoomService:
    def __init__(self, owner: FakeSipApi) -> None:
        self._owner = owner

    async def move_participant(self, request: Any) -> Any:
        self._owner.log.append("room:move")
        self._owner.moves.append(request)
        return object()

    async def delete_room(self, request: Any) -> Any:
        self._owner.log.append("room:delete")
        self._owner.deleted.append(request)
        return object()


class FakeSipApi:
    """`JobContext.api`'s `sip` and `room` services, recorded."""

    def __init__(self, log: list[str], *, dial_error: Exception | None = None) -> None:
        self.log = log
        self.dial_error = dial_error
        self.dials: list[Any] = []
        self.moves: list[Any] = []
        self.deleted: list[Any] = []
        self.sip = _Sip(self)
        self.room = _RoomService(self)


class _Info:
    url = "wss://livekit.example.com"


class FakeSipJobContext:
    """`get_job_context()` inside a warm transfer, and the job context `telephony.hang_up` ends."""

    def __init__(self, room: FakeSipRoom, log: list[str] | None = None, **api_kwargs: Any) -> None:
        self.log: list[str] = log if log is not None else []
        self.room = room
        self.api = FakeSipApi(self.log, **api_kwargs)
        self._info = _Info()
        self.shutdown_reasons: list[str] = []

    def delete_room(self, room_name: str | None = None) -> asyncio.Future[None]:
        self.log.append(f"job:delete_room:{room_name or self.room.name}")
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        future.set_result(None)
        return future

    def shutdown(self, reason: str = "") -> None:
        self.log.append("job:shutdown")
        self.shutdown_reasons.append(reason)


class _RoomIO:
    def __init__(self, room: Any) -> None:
        self.room = room


def consult_fakes(log: list[str]) -> tuple[type[Any], type[Any]]:
    """`(Room, AgentSession)` stand-ins for the task's private consult room, bound to `log`.

    The task builds `rtc.Room()` and an `AgentSession(...)` itself; the tests patch
    these two names in the SDK module (the LiveKit boundary). `sessions` on the
    returned session class lists every briefing session started.
    """

    class FakeConsultRoom(rtc.EventEmitter[str]):
        def __init__(self) -> None:
            super().__init__()
            self.name = ""

        async def connect(self, url: str, token: str, *_args: Any, **_kwargs: Any) -> None:
            log.append("consult:connect")

    class FakeHumanSession:
        sessions: list[Any] = []

        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.agent: Any = None
            self.room_io: _RoomIO | None = None
            self.closed = False

        async def start(self, *, agent: Any, room: Any, room_options: Any = None, **_kwargs: Any) -> None:
            log.append("consult:brief")
            self.agent = agent
            self.room_options = room_options
            room.name = room.name or "consult"
            self.room_io = _RoomIO(room)
            FakeHumanSession.sessions.append(self)

        def shutdown(self, *_args: Any, **_kwargs: Any) -> None:
            self.closed = True

    return FakeConsultRoom, FakeHumanSession


class _PlayHandle:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    def stop(self) -> None:
        self._log.append("hold:stop")


class FakeHoldPlayer:
    """`BackgroundAudioPlayer`: `start(room=)` and a looped `play(...)` of the hold music."""

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.played: list[Any] = []

    async def start(self, *, room: Any, **_kwargs: Any) -> None:
        self.log.append("hold:start")

    def play(self, audio: Any, *, loop: bool = False) -> _PlayHandle:
        self.log.append("hold:play")
        self.played.append((audio, loop))
        return _PlayHandle(self.log)


class _Toggle:
    """`session.input` / `session.output`: no tracks, so the task toggles nothing."""

    audio = None
    video = None
    transcription = None
    audio_enabled = True
    video_enabled = False
    transcription_enabled = True


class FakeSpeech:
    """A `SpeechHandle`: `wait_for_playout` is recorded."""

    def __init__(self, log: list[str], text: str) -> None:
        self._log = log
        self.text = text

    async def wait_for_playout(self) -> None:
        self._log.append(f"playout:{self.text}")


class FakeVoiceSession:
    """The caller-side `AgentSession` slice: `say`, the IO toggles, the model slots."""

    def __init__(self, log: list[str] | None = None, *, llm: Any = None) -> None:
        self.log: list[str] = log if log is not None else []
        self.said: list[str] = []
        self.llm = llm
        self.stt = None
        self.tts = None
        self.vad = None
        self.turn_detection = None
        self.input = _Toggle()
        self.output = _Toggle()

    def say(self, text: str, **_kwargs: Any) -> FakeSpeech:
        self.log.append(f"say:{text}")
        self.said.append(text)
        return FakeSpeech(self.log, text)


class FakeActivity:
    """The `AgentActivity` an `AgentTask` runs in: only `.session` is read."""

    def __init__(self, session: FakeVoiceSession) -> None:
        self.session = session
