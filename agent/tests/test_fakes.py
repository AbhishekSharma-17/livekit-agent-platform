"""Self-tests for the W0-SCAFFOLD fakes (`fakes.fake_room`, `fakes.fake_ctx`).

These exist so Wave-1/2 authors can trust the fakes' documented behaviour
before building on them.
"""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from fakes.fake_ctx import (
    FakeBackgroundRunner,
    FakeFrameBuffer,
    FakeImageGen,
    FakeKbClient,
    FakePackSessionContext,
    FakeStructuredLLM,
)
from fakes.fake_room import FakeRemoteParticipant, FakeRoom
from livekit import rtc
from lkap_contracts.api_models import KbHit
from lkap_contracts.ui_protocol import UiPatchOp
from packs.base import FrameSnapshot
from pydantic import BaseModel


class _Extracted(BaseModel):
    ok: bool = True


# --- fake_room ---------------------------------------------------------------


async def test_fake_room_send_text_records_message() -> None:
    room = FakeRoom()
    await room.local_participant.send_text("hello", topic="lkap.ui.state")
    assert room.local_participant.sent_text[0].text == "hello"
    assert room.local_participant.sent_text[0].topic == "lkap.ui.state"


async def test_fake_room_stream_bytes_records_chunks_and_close() -> None:
    room = FakeRoom()
    writer = await room.local_participant.stream_bytes("a1.jpg", mime_type="image/jpeg")
    await writer.write(b"\xff\xd8")
    await writer.write(b"\xff\xd9")
    await writer.aclose(reason="done")
    record = room.local_participant.byte_streams[0]
    assert record.chunks == [b"\xff\xd8", b"\xff\xd9"]
    assert record.closed is True
    assert record.mime_type == "image/jpeg"


async def test_fake_room_perform_rpc_records_call_and_returns_default() -> None:
    room = FakeRoom()
    result = await room.local_participant.perform_rpc(
        destination_identity="web-ui", method="lkap.ui.request", payload="{}"
    )
    assert result == "{}"
    assert room.local_participant.rpc_calls[0].method == "lkap.ui.request"


async def test_fake_room_register_rpc_method_round_trips_via_invoke_rpc() -> None:
    room = FakeRoom()

    def handler(data: object) -> str:
        return '{"ok": true}'

    room.local_participant.register_rpc_method("lkap.agent.action", handler)
    result = await room.local_participant.invoke_rpc("lkap.agent.action", "{}")
    assert result == '{"ok": true}'


def test_fake_room_emits_track_subscribed_like_the_real_room() -> None:
    room = FakeRoom()
    participant = room.add_remote_participant(FakeRemoteParticipant("user-1"))
    seen: list[tuple[object, object, object]] = []
    room.on("track_subscribed", lambda track, pub, p: seen.append((track, pub, p)))
    room.fire_track_subscribed("track", "publication", participant)
    assert seen == [("track", "publication", participant)]


# --- fake_ctx ------------------------------------------------------------------


async def test_ui_channel_add_note_and_set_status_mutate_state() -> None:
    ctx = FakePackSessionContext()
    await ctx.ui.set_status("Reviewing", "info")
    await ctx.ui.add_note("Policy found", key="policy")
    assert ctx.ui.state.status is not None
    assert ctx.ui.state.status.label == "Reviewing"
    assert ctx.ui.state.notes[0].text == "Policy found"
    assert ctx.ui.seq == 2


async def test_ui_channel_add_note_upsert_replaces_by_key() -> None:
    ctx = FakePackSessionContext()
    await ctx.ui.add_note("first", key="k")
    await ctx.ui.add_note("second", key="k")
    assert len(ctx.ui.state.notes) == 1
    assert ctx.ui.state.notes[0].text == "second"


async def test_ui_channel_push_asset_appends_asset_ref() -> None:
    ctx = FakePackSessionContext()
    asset_id = await ctx.ui.push_asset(b"\xff\xd8", "image/jpeg", "photo", caption="evidence")
    assert ctx.ui.state.assets[0].asset_id == asset_id
    assert ctx.ui.state.assets[0].caption == "evidence"


async def test_ui_channel_custom_nested_patch() -> None:
    ctx = FakePackSessionContext()
    await ctx.ui.patch([UiPatchOp(op="set", path="/custom/fields/policy", value="H0-44721")])
    assert ctx.ui.state.custom["fields"]["policy"] == "H0-44721"


def test_frame_buffer_latest_respects_max_age() -> None:
    buf = FakeFrameBuffer()
    snap = FrameSnapshot(frame=cast(rtc.VideoFrame, object()), source="camera", age_s=10.0)
    buf.set_latest(snap)
    assert buf.latest(max_age_s=1.0) is None
    assert buf.latest(max_age_s=20.0) is snap


async def test_frame_buffer_latest_jpeg_returns_configured_bytes() -> None:
    buf = FakeFrameBuffer()
    snap = FrameSnapshot(frame=cast(rtc.VideoFrame, object()), source="screen", age_s=0.1)
    buf.set_latest(snap, jpeg_bytes=b"jpeg-bytes")
    result = await buf.latest_jpeg()
    assert result is not None
    data, returned_snap = result
    assert data == b"jpeg-bytes"
    assert returned_snap is snap


async def test_kb_client_search_records_query_and_returns_hits() -> None:
    hit = KbHit(chunk_id="c1", document_id="d1", filename="f.txt", score=0.9, text="hello")
    kb = FakeKbClient(hits=[hit])
    results = await kb.search("hello", k=1)
    assert results == [hit]
    assert kb.queries == [("hello", 1, None)]


async def test_structured_llm_extract_returns_queued_response() -> None:
    llm = FakeStructuredLLM(responses=[_Extracted(ok=False)])
    result = await llm.extract(instructions="x", input_text="y", schema=_Extracted)
    assert isinstance(result, _Extracted)
    assert result.ok is False
    assert llm.calls[0][2] is _Extracted


async def test_structured_llm_extract_raises_when_no_response_queued() -> None:
    llm = FakeStructuredLLM()
    with pytest.raises(AssertionError):
        await llm.extract(instructions="x", input_text="y", schema=_Extracted)


async def test_image_gen_generate_returns_configured_bytes_and_records_prompt() -> None:
    gen = FakeImageGen(image_bytes=b"png-bytes", mime="image/png")
    data, mime = await gen.generate("draw a house")
    assert data == b"png-bytes"
    assert mime == "image/png"
    assert gen.prompts == ["draw a house"]


async def test_background_runner_routine_path_records_note() -> None:
    runner = FakeBackgroundRunner()

    async def work() -> str:
        return "done"

    runner.submit(name="job", coro=work(), routine_note=lambda r: f"finished: {r}")
    await runner.wait_idle()
    assert runner.routine_notes == [(list(runner.jobs)[0], "finished: done")]


async def test_background_runner_urgent_path_records_event() -> None:
    runner = FakeBackgroundRunner()

    async def work() -> str:
        return "injury"

    job_id = runner.submit(
        name="job",
        coro=work(),
        urgent=lambda r: r == "injury",
        urgent_instructions=lambda r: "escalate now",
        call_id="job-1",
    )
    await runner.wait_idle()
    assert job_id == "job-1"
    assert runner.urgent_events == [("job-1", "injury", "escalate now")]


async def test_background_runner_cancel_marks_cancelled() -> None:
    runner = FakeBackgroundRunner()

    async def work() -> str:
        await asyncio.sleep(10)
        return "unreachable"

    job_id = runner.submit(name="job", coro=work())
    await asyncio.sleep(0)  # let `_run()` actually start (and enter `await coro`) before cancelling
    runner.cancel(job_id)
    assert job_id in runner.cancelled
    await asyncio.sleep(0)  # let the cancellation land so the task doesn't outlive the test


def test_default_pack_session_context_satisfies_shape() -> None:
    ctx = FakePackSessionContext()
    assert ctx.session_id
    assert ctx.pipeline_mode == "cascaded"
    assert ctx.image_gen is not None
    assert ctx.userdata == {}
