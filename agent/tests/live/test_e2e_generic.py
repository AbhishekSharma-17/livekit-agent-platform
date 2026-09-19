"""Room-level live test for the generic pack (LIVE_TEST_PLAN §A4, Stages 1, 3, 4, 5).

Needs the api **and** a local `lkap-agent` worker already running against the
same LiveKit project (LIVE_TEST_PLAN §A1), plus:

    LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET
    LKAP_LIVE_API_BASE_URL   e.g. http://127.0.0.1:8080
    LKAP_LIVE_ADMIN_TOKEN    the api's LKAP_ADMIN_TOKEN

Run with `uv run pytest -m live -v tests/live/test_e2e_generic.py`.

The test joins as a plain participant, publishes **no audio** (no STT cost),
drives one typed turn over `lk.chat`, and checks the whole loop: dispatch ->
resolve -> initial `UiSnapshot` (D-W2-9a) -> tool call -> `UiPatch` on
`/notes` -> assistant transcription -> session events -> hangup -> job
shutdown -> summary (D-W2-9e) -> exactly one session row (D-W2-2). The TTS
still speaks the greeting and one reply (~2 short utterances).

Only `lkap-agent` is ever dispatched (by the api's `RoomAgentDispatch`); the
unrelated `other-project-agent` agent in the same project is never touched.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any

import httpx
import pytest
from livekit import rtc
from lkap_contracts.api_models import ConnectRequest, ConnectResponse
from lkap_contracts.ui_protocol import TOPIC_UI_STATE, UiPatch, UiSnapshot

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not all(
            os.environ.get(var)
            for var in (
                "LIVEKIT_URL",
                "LIVEKIT_API_KEY",
                "LIVEKIT_API_SECRET",
                "LKAP_LIVE_API_BASE_URL",
                "LKAP_LIVE_ADMIN_TOKEN",
            )
        ),
        reason="needs LIVEKIT_*, LKAP_LIVE_API_BASE_URL, LKAP_LIVE_ADMIN_TOKEN and a running api + worker",
    ),
]

TOPIC_TRANSCRIPTION = "lk.transcription"
TOPIC_CHAT = "lk.chat"

SNAPSHOT_TIMEOUT_S = 20.0
TOOL_TIMEOUT_S = 30.0
SUMMARY_TIMEOUT_S = 15.0


class _RoomObserver:
    """Collects `lkap.ui.state` messages and assistant transcriptions from the room."""

    def __init__(self, local_identity: str) -> None:
        self.local_identity = local_identity
        self.snapshots: list[UiSnapshot] = []
        self.patches: list[UiPatch] = []
        self.assistant_segments: list[str] = []
        self._tasks: set[asyncio.Task[None]] = set()
        self.changed = asyncio.Event()

    def on_ui_state(self, reader: rtc.TextStreamReader, participant_identity: str) -> None:
        self._spawn(self._read_ui_state(reader))

    def on_transcription(self, reader: rtc.TextStreamReader, participant_identity: str) -> None:
        if participant_identity == self.local_identity:
            return
        self._spawn(self._read_transcription(reader))

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _read_ui_state(self, reader: rtc.TextStreamReader) -> None:
        raw = json.loads(await reader.read_all())
        if raw.get("type") == "snapshot":
            self.snapshots.append(UiSnapshot.model_validate(raw))
        elif raw.get("type") == "patch":
            self.patches.append(UiPatch.model_validate(raw))
        self.changed.set()

    async def _read_transcription(self, reader: rtc.TextStreamReader) -> None:
        text = await reader.read_all()
        if text.strip():
            self.assistant_segments.append(text)
            self.changed.set()

    async def wait_for(self, predicate: Any, timeout_s: float, what: str) -> None:
        deadline = time.monotonic() + timeout_s
        while not predicate():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"timed out after {timeout_s:.0f}s waiting for {what}")
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), timeout=min(remaining, 1.0))
            except TimeoutError:
                continue


def _touches_notes(patch: UiPatch) -> bool:
    return any(op.path.rstrip("/") == "/notes" for op in patch.ops)


async def _create_published_agent(api: httpx.AsyncClient, run_id: str) -> dict[str, Any]:
    created = await api.post("/v1/agents", json={"name": f"E2E Generic {run_id}", "pack_id": "generic"})
    created.raise_for_status()
    agent: dict[str, Any] = created.json()
    published = await api.put(f"/v1/agents/{agent['id']}", json={"published": True})
    published.raise_for_status()
    result: dict[str, Any] = published.json()
    return result


async def _poll_session(
    api: httpx.AsyncClient, session_id: str, timeout_s: float
) -> tuple[dict[str, Any], float]:
    started = time.monotonic()
    detail: dict[str, Any] = {}
    while time.monotonic() - started < timeout_s:
        response = await api.get(f"/v1/sessions/{session_id}")
        response.raise_for_status()
        detail = response.json()
        if detail["status"] in ("ended", "failed"):
            return detail, time.monotonic() - started
        await asyncio.sleep(0.5)
    raise AssertionError(f"session {session_id} still {detail.get('status')!r} after {timeout_s:.0f}s")


async def test_generic_agent_round_trip_over_a_real_room() -> None:
    """Dispatch, snapshot, typed turn, tool -> panel, events, hangup -> one ended row."""
    run_id = uuid.uuid4().hex[:8]
    async with httpx.AsyncClient(
        base_url=os.environ["LKAP_LIVE_API_BASE_URL"],
        headers={"X-Admin-Token": os.environ["LKAP_LIVE_ADMIN_TOKEN"]},
        timeout=20.0,
    ) as api:
        # 1. A fresh, published generic agent (tagged with the run id; the row stays
        #    because deletion is refused while sessions exist).
        agent = await _create_published_agent(api, run_id)

        # 2. Connect -> dispatch of `lkap-agent` by the api.
        response = await api.post(
            f"/v1/agents/{agent['slug']}/connect",
            json=ConnectRequest(participant_name=f"e2e-{run_id}").model_dump(),
        )
        response.raise_for_status()
        connect = ConnectResponse.model_validate(response.json())

        # 3. Join the room with the handlers registered before connecting.
        room = rtc.Room()
        observer = _RoomObserver(local_identity="")
        room.register_text_stream_handler(TOPIC_UI_STATE, observer.on_ui_state)
        room.register_text_stream_handler(TOPIC_TRANSCRIPTION, observer.on_transcription)
        await room.connect(connect.serverUrl, connect.participantToken)
        observer.local_identity = room.local_participant.identity
        try:
            # 4. The platform's initial snapshot (D-W2-9a).
            await observer.wait_for(
                lambda: any(s.session_id == connect.sessionId for s in observer.snapshots),
                SNAPSHOT_TIMEOUT_S,
                "the initial UiSnapshot",
            )
            first = next(s for s in observer.snapshots if s.session_id == connect.sessionId)
            assert first.seq == 1, "the generic pack sends nothing itself, so the platform snapshot is seq 1"

            # 5. Typed chat -> push_note -> UiPatch on /notes + an assistant transcription.
            segments_before = len(observer.assistant_segments)
            await room.local_participant.send_text(
                "Please add a note that says hello world.", topic=TOPIC_CHAT
            )
            await observer.wait_for(
                lambda: any(_touches_notes(p) for p in observer.patches),
                TOOL_TIMEOUT_S,
                "a UiPatch touching /notes",
            )
            await observer.wait_for(
                lambda: len(observer.assistant_segments) > segments_before,
                TOOL_TIMEOUT_S,
                "an assistant lk.transcription segment after the typed turn",
            )
            assert all(p.session_id == connect.sessionId for p in observer.patches)
        finally:
            # 7. Hang up.
            await room.disconnect()
        hangup_at = time.monotonic()

        # 7. The job ends and posts the summary (D-W2-9e).
        detail, waited_s = await _poll_session(api, connect.sessionId, SUMMARY_TIMEOUT_S)
        assert detail["status"] == "ended", detail.get("error")
        assert len(detail["transcript"] or []) >= 2
        assert detail["usage"], "usage must be stored with the summary"
        assert time.monotonic() - hangup_at <= SUMMARY_TIMEOUT_S + 1
        assert waited_s <= SUMMARY_TIMEOUT_S

        # 6. The event timeline (checked after the final flush on shutdown).
        events_response = await api.get(f"/v1/sessions/{connect.sessionId}/events", params={"limit": 1000})
        events_response.raise_for_status()
        events = events_response.json()["items"]
        types = [e["type"] for e in events]
        assert "session_started" in types
        assert "user_turn" in types
        tools_started = [e["payload"].get("tool") for e in events if e["type"] == "tool_call_started"]
        assert "push_note" in tools_started, types
        assert "tool_call_ended" in types

        # One session row for this run (D-W2-2).
        rows_response = await api.get("/v1/sessions", params={"agent_id": agent["id"]})
        rows_response.raise_for_status()
        rows = rows_response.json()["items"]
        assert [r["id"] for r in rows] == [connect.sessionId]
