"""Live end-to-end tests for the insurance pack (LIVE_TEST_PLAN Stage 9; W3-E2E-INSURANCE).

Two layers, cheapest first (LIVE_TEST_PLAN Part E.2):

1. **Text mode** (`test_text_mode_*`): no room, no STT/TTS, no api. A real
   `PlatformAgent` + the real `InsuranceClaimPack` tools + the real
   `BackgroundToolRunner` + `PromptJsonStructuredLLM`, on a LiveKit Inference
   LLM, with `FakeUiChannel`/`FakeFrameBuffer`/`FakeKbClient`. Turns are
   driven through `platform_text_input_cb` — the production typed-chat path
   (DECISIONS-W2 §D-W2-9p) — because `AgentSession.run()` uses
   `generate_reply(user_input=...)`, which skips `on_user_turn_completed`, and
   the pack records the claimant transcript the workflow reads in that hook.
   Needs only `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`.
2. **Room level** (`test_room_*`): like `test_e2e_generic.py`, a scripted
   `rtc.Room` participant against the running api and local `lkap-agent`
   worker: create an agent from the `insurance_claim` pack, typed flood turn,
   synthetic camera track + "pin what you see" -> `pin_evidence_photo` asset,
   hangup -> summary -> exactly one session row. Needs the api and worker up
   (LIVE_TEST_PLAN §A1) plus `LKAP_LIVE_API_BASE_URL`/`LKAP_LIVE_ADMIN_TOKEN`.

Both use `google/gemini-3.5-flash` — the model the insurance manifest seeds
(DECISIONS-W2 §D-W2-10) — and keep each scenario to two or three turns.

Run: `uv run pytest -m live -v tests/live/test_e2e_insurance.py`.
Only `lkap-agent` is ever dispatched; `other-project-agent` is never touched.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fakes.fake_ctx import FakeFrameBuffer, FakeKbClient, FakeUiChannel
from fakes.fake_room import FakeRoom
from livekit import rtc
from livekit.agents import AgentSession, inference, llm
from lkap_contracts.agent_config import AgentConfig, VoiceConfig
from lkap_contracts.api_models import ConnectRequest, ConnectResponse
from lkap_contracts.ui_protocol import TOPIC_UI_STATE, UiPatch, UiSnapshot
from packs.insurance_claim.manifest import MANIFEST
from packs.insurance_claim.pack import PACK

from lkap_agent.platform_agent import PlatformAgent, SessionContext, platform_text_input_cb
from lkap_agent.tools.background import BackgroundToolRunner
from lkap_agent.workflow_llm import PromptJsonStructuredLLM

_HAS_LK = bool(os.environ.get("LIVEKIT_API_KEY") and os.environ.get("LIVEKIT_API_SECRET"))
_HAS_ROOM = _HAS_LK and all(
    os.environ.get(v) for v in ("LIVEKIT_URL", "LKAP_LIVE_API_BASE_URL", "LKAP_LIVE_ADMIN_TOKEN")
)

pytestmark = [pytest.mark.live]

logger = logging.getLogger("lkap.e2e.insurance")

needs_lk = pytest.mark.skipif(not _HAS_LK, reason="needs LIVEKIT_API_KEY/LIVEKIT_API_SECRET")
needs_room = pytest.mark.skipif(
    not _HAS_ROOM,
    reason="needs LIVEKIT_*, LKAP_LIVE_API_BASE_URL, LKAP_LIVE_ADMIN_TOKEN and a running api + worker",
)

#: The insurance manifest's LLM slot (the registry's first `supports_video` model).
MODEL = MANIFEST.recommended_pipeline.llm.model if MANIFEST.recommended_pipeline.llm else None

TURN_TIMEOUT_S = 45.0
WORKFLOW_TIMEOUT_S = 60.0

FLOOD = "Policy H0-44721. My basement flooded yesterday in Denver after heavy rain. Nobody was hurt."
PIN_REQUEST = "Please pin what you see on my camera as evidence, it's the water damage."
INJURY = (
    "Policy AUTO-11111. I was rear-ended on I-25 in Denver an hour ago and my passenger's neck hurts. "
    "Please sync the claim packet now."
)


# ------------------------------------------------------------------ text mode


class _TextHarness:
    """A roomless insurance session driven through the production typed-chat callback."""

    def __init__(self) -> None:
        assert MODEL is not None
        config = AgentConfig(
            instructions=MANIFEST.default_instructions,
            pipeline=MANIFEST.recommended_pipeline,
            capabilities=MANIFEST.capabilities,
            voice=VoiceConfig(greeting=""),  # no greeting turn: one LLM call saved
        )
        self.session: AgentSession[Any] = AgentSession(
            llm=inference.LLM(MODEL), stt=None, tts=None, vad=None, max_tool_steps=3
        )
        self.ui = FakeUiChannel()
        self.tool_calls: list[str] = []
        labels = {m.name: m.activity_label for m in PACK.tool_meta() if m.activity_label}
        self.ctx = SessionContext(
            session_id=f"text-{uuid.uuid4().hex[:8]}",
            agent_id="text-mode",
            pipeline_mode="cascaded",
            config=config,
            session=self.session,
            room=cast(rtc.Room, FakeRoom()),
            ui=self.ui,
            frames=FakeFrameBuffer(),
            kb=FakeKbClient(),
            workflow_llm=PromptJsonStructuredLLM(inference.LLM(MODEL)),
            background=BackgroundToolRunner(self.ui, self.session, labels=labels),
            log=None,
        )
        self.agent = PlatformAgent(ctx=self.ctx, pack=PACK, tools=list(PACK.tools(self.ctx)), has_tts=False)

        def _on_tools(ev: Any) -> None:
            self.tool_calls.extend(call.name for call in ev.function_calls)

        self.session.on("function_tools_executed", _on_tools)

    async def __aenter__(self) -> _TextHarness:
        await self.session.start(self.agent)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.session.aclose()

    def assistant_texts(self) -> list[str]:
        return [
            i.text_content or ""
            for i in self.session.history.items
            if isinstance(i, llm.ChatMessage) and i.role == "assistant" and i.text_content
        ]

    async def say(self, text: str) -> str:
        """One typed turn; returns the assistant text produced for it."""
        before = len(self.assistant_texts())
        await platform_text_input_cb(self.session, SimpleNamespace(text=text))
        deadline = time.monotonic() + TURN_TIMEOUT_S
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            if len(self.assistant_texts()) > before and self.session.agent_state == "listening":
                break
        new = self.assistant_texts()[before:]
        assert new, f"no assistant reply to {text!r} within {TURN_TIMEOUT_S}s"
        return "\n".join(new)

    async def wait_for(self, predicate: Any, within: float = WORKFLOW_TIMEOUT_S) -> bool:
        deadline = time.monotonic() + within
        while time.monotonic() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.5)
        return bool(predicate())

    def route(self) -> str:
        return str(self.ui.state.custom.get("route", ""))

    def done_count(self, source: str) -> int:
        """Number of completed background jobs from `source` seen so far.

        The pack also runs a passive, debounced `sync_claim_packet` workflow
        pass on `on_user_turn_completed` (mapping #14), independent of the
        model calling the tool. Snapshotting this count before a turn and
        waiting for it to grow after is how a test knows a render reflects
        that turn's facts, rather than reading a stale earlier render.
        """
        return sum(1 for a in self.ui.state.activity if a.source == source and a.phase == "done")


@needs_lk
async def test_text_mode_flood_claim_verifies_the_policy_and_builds_the_packet() -> None:
    async with _TextHarness() as h:
        # The model is free to ask for the policy number before or after asking
        # about the loss -- both are valid claim-intake orderings, and asserting
        # on the wording of this one turn is what made the test flaky (it once
        # asked about the loss first, which is not a regression). Either way the
        # claimant's next turn (FLOOD) carries the policy number, so drive that
        # turn regardless and assert on the outcomes the test name promises:
        # the policy gets verified and the claim packet gets built.
        await h.say("Hi, everyone is safe. I need to report water damage at my house.")
        syncs_before = h.done_count("sync_claim_packet")
        await h.say(FLOOD)
        assert "lookup_policy" in h.tool_calls, h.tool_calls

        policy = h.ui.state.custom.get("policy") or {}
        assert policy.get("policy_number") == "H0-44721", policy
        assert policy.get("status") == "active", f"lookup_policy should verify the policy as active: {policy}"

        if "sync_claim_packet" not in h.tool_calls:
            await h.say("That's everything for now, please put the claim packet together.")
        assert "sync_claim_packet" in h.tool_calls, h.tool_calls
        # Wait for a workflow render that completed *after* the flood facts and
        # policy number were given, so the route/packet we check below aren't a
        # stale snapshot from an earlier passive run (mapping #14).
        assert await h.wait_for(lambda: h.done_count("sync_claim_packet") > syncs_before), h.ui.state.activity
        assert h.ui.state.custom.get("packet_markdown"), "no packet rendered"
        assert h.route() == "needs_docs", h.route()
        assert h.ui.state.status is not None, "the route stamp should be set"
        logger.info(
            "flood evidence tools=%s route=%s status=%s replies=%s",
            h.tool_calls,
            h.route(),
            h.ui.state.status,
            h.assistant_texts(),
        )


@needs_lk
async def test_text_mode_injury_claim_escalates_with_an_urgent_reply() -> None:
    async with _TextHarness() as h:
        await h.say(INJURY)
        assert "lookup_policy" in h.tool_calls, h.tool_calls
        if "sync_claim_packet" not in h.tool_calls:
            await h.say("Yes, please sync the claim packet now.")
        assert "sync_claim_packet" in h.tool_calls, h.tool_calls

        replies_after_turn = len(h.assistant_texts())
        assert await h.wait_for(lambda: h.route() == "emergency_escalation"), h.route()
        # The urgent path: the background runner marks the job urgent and interrupts with
        # generate_reply(instructions=...) — a *new* assistant message (mapping #8).
        assert await h.wait_for(
            lambda: any(
                a.source == "sync_claim_packet" and a.phase == "done" and a.urgent
                for a in h.ui.state.activity
            )
        ), h.ui.state.activity
        assert await h.wait_for(
            lambda: any("emergenc" in t.lower() for t in h.assistant_texts()[replies_after_turn:])
        ), h.assistant_texts()
        assert h.ui.state.status is not None, "the route stamp should be set"
        logger.info(
            "injury evidence tools=%s route=%s status=%s replies=%s",
            h.tool_calls,
            h.route(),
            h.ui.state.status,
            h.assistant_texts(),
        )


# ------------------------------------------------------------------ room level

TOPIC_TRANSCRIPTION = "lk.transcription"
TOPIC_ASSET = "lkap.ui.asset"


class _RoomObserver:
    def __init__(self, local_identity: str) -> None:
        self.local_identity = local_identity
        self.snapshots: list[UiSnapshot] = []
        self.patches: list[UiPatch] = []
        self.assistant: list[str] = []
        self.assets: list[dict[str, str]] = []
        self._tasks: set[asyncio.Task[None]] = set()

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def on_state(self, reader: rtc.TextStreamReader, _who: str) -> None:
        async def _read() -> None:
            raw = json.loads(await reader.read_all())
            if raw.get("type") == "snapshot":
                self.snapshots.append(UiSnapshot.model_validate(raw))
            else:
                self.patches.append(UiPatch.model_validate(raw))

        self._spawn(_read())

    def on_transcription(self, reader: rtc.TextStreamReader, who: str) -> None:
        if who == self.local_identity:
            return

        async def _read() -> None:
            text = await reader.read_all()
            if text.strip():
                self.assistant.append(text)

        self._spawn(_read())

    def on_asset(self, reader: rtc.ByteStreamReader, _who: str) -> None:
        async def _read() -> None:
            async for _chunk in reader:
                pass
            self.assets.append(dict(reader.info.attributes or {}))

        self._spawn(_read())


def _red_frame(w: int = 640, h: int = 480) -> rtc.VideoFrame:
    return rtc.VideoFrame(w, h, rtc.VideoBufferType.RGBA, bytes([200, 30, 30, 255]) * (w * h))


async def _wait(predicate: Any, within: float) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.5)
    return bool(predicate())


@needs_room
async def test_room_insurance_call_verifies_policy_pins_camera_and_stores_one_session() -> None:
    api_base = os.environ["LKAP_LIVE_API_BASE_URL"]
    headers = {"X-Admin-Token": os.environ["LKAP_LIVE_ADMIN_TOKEN"]}
    run_id = uuid.uuid4().hex[:6]
    async with httpx.AsyncClient(base_url=api_base, headers=headers, timeout=30) as api:
        created = await api.post(
            "/v1/agents", json={"name": f"E2E Insurance {run_id}", "pack_id": "insurance_claim"}
        )
        created.raise_for_status()
        agent = created.json()
        assert agent["config"]["pipeline"]["llm"]["model"] == MODEL
        (await api.put(f"/v1/agents/{agent['id']}", json={"published": True})).raise_for_status()

        conn_raw = await api.post(f"/v1/agents/{agent['slug']}/connect", json=ConnectRequest().model_dump())
        conn_raw.raise_for_status()
        conn = ConnectResponse.model_validate(conn_raw.json())

        room = rtc.Room()
        obs = _RoomObserver(local_identity="")
        room.register_text_stream_handler(TOPIC_UI_STATE, obs.on_state)
        room.register_text_stream_handler(TOPIC_TRANSCRIPTION, obs.on_transcription)
        room.register_byte_stream_handler(TOPIC_ASSET, obs.on_asset)
        await room.connect(conn.serverUrl, conn.participantToken)
        obs.local_identity = room.local_participant.identity
        pump: asyncio.Task[None] | None = None
        try:
            assert await _wait(lambda: any(s.session_id == conn.sessionId for s in obs.snapshots), 20)
            assert obs.snapshots[-1].state.custom.get("route") is not None, "insurance notebook snapshot"

            patches_before = len(obs.patches)
            await room.local_participant.send_text(FLOOD, topic="lk.chat")
            assert await _wait(lambda: len(obs.patches) > patches_before, 45), "lookup_policy should patch"
            assert await _wait(lambda: bool(obs.assistant), 45)

            source = rtc.VideoSource(640, 480)
            track = rtc.LocalVideoTrack.create_video_track("camera", source)
            await room.local_participant.publish_track(
                track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)
            )
            frame = _red_frame()

            async def _pump() -> None:
                while True:
                    source.capture_frame(frame)
                    await asyncio.sleep(0.2)

            pump = asyncio.create_task(_pump())
            await asyncio.sleep(3)
            for _attempt in range(2):  # flaky-tolerant: the model may ask first
                await room.local_participant.send_text(
                    "Please pin what you see on my camera as evidence, it's the water damage.",
                    topic="lk.chat",
                )
                if await _wait(lambda: bool(obs.assets), 45):
                    break
            assert obs.assets, "pin_evidence_photo should stream an asset"
        finally:
            if pump is not None:
                pump.cancel()
            await room.disconnect()

        status = ""
        detail: dict[str, Any] = {}
        for _ in range(30):
            detail = (await api.get(f"/v1/sessions/{conn.sessionId}")).json()
            status = detail["status"]
            if status in ("ended", "failed"):
                break
            await asyncio.sleep(0.5)
        assert status == "ended", detail
        events = (await api.get(f"/v1/sessions/{conn.sessionId}/events", params={"limit": 1000})).json()[
            "items"
        ]
        tools = {e["payload"].get("tool") for e in events if e["type"] == "tool_call_started"}
        assert {"lookup_policy", "pin_evidence_photo"} <= tools, tools
        rows = (await api.get("/v1/sessions", params={"agent_id": agent["id"]})).json()
        assert rows["total"] == 1
