"""`FakeRoomTransport`: scripted replies, join delay, never-joins, RPC and disconnect (V3-02)."""

from __future__ import annotations

import asyncio

import pytest

from lkap_testing.fake_room import FAKE_AGENT_IDENTITY, FakeReply, FakeRoomTransport


async def _take(transport: FakeRoomTransport, n: int, wait_s: float = 1.0) -> list[FakeReply]:
    out: list[FakeReply] = []

    async def run() -> None:
        async for reply in transport.replies():
            out.append(reply)
            if len(out) == n:
                return

    await asyncio.wait_for(run(), wait_s)
    return out


async def test_fake_room_greeting_and_scripted_turns_are_emitted_in_order() -> None:
    room = FakeRoomTransport(greeting="Hello!", turns=[["one"], ["two", FakeReply("partial", final=False)]])
    await room.connect("wss://example.livekit.cloud", "tok")
    assert await room.wait_for_agent(1.0) == FAKE_AGENT_IDENTITY
    await room.send_text("first")
    await room.send_text("second", topic="lk.chat")

    replies = await _take(room, 4)

    assert [r.text for r in replies] == ["Hello!", "one", "two", "partial"]
    assert [r.final for r in replies] == [True, True, True, False]
    assert all(r.participant == FAKE_AGENT_IDENTITY for r in replies)
    assert room.sent == [("first", "lk.chat"), ("second", "lk.chat")]
    assert room.connected_with == ("wss://example.livekit.cloud", "tok")


async def test_fake_room_responder_replaces_the_script() -> None:
    room = FakeRoomTransport(responder=lambda text: [f"echo: {text}"])
    await room.connect("wss://x.test", "t")
    await room.wait_for_agent(1.0)
    await room.send_text("ping")

    assert [r.text for r in await _take(room, 1)] == ["echo: ping"]


async def test_fake_room_never_joins_times_out() -> None:
    room = FakeRoomTransport(never_joins=True)
    await room.connect("wss://x.test", "t")

    with pytest.raises(TimeoutError):
        await room.wait_for_agent(0.05)
    assert room.joined is False


async def test_fake_room_join_delay_longer_than_the_timeout_times_out_and_shorter_joins() -> None:
    slow = FakeRoomTransport(join_delay_s=0.2)
    await slow.connect("wss://x.test", "t")
    with pytest.raises(TimeoutError):
        await slow.wait_for_agent(0.05)

    quick = FakeRoomTransport(join_delay_s=0.01)
    await quick.connect("wss://x.test", "t")
    assert await quick.wait_for_agent(1.0) == FAKE_AGENT_IDENTITY
    assert quick.joined is True


async def test_fake_room_rpc_records_the_call_and_emits_the_regenerated_reply() -> None:
    room = FakeRoomTransport(rpc_turns=[["regenerated"]])
    await room.connect("wss://x.test", "t")
    await room.wait_for_agent(1.0)

    result = await room.rpc("lkap.agent.action", '{"action": "rewind"}')

    assert result == '{"ok": true, "payload": {}}'
    assert room.rpc_calls == [("lkap.agent.action", '{"action": "rewind"}')]
    assert [r.text for r in await _take(room, 1)] == ["regenerated"]


async def test_fake_room_close_ends_replies_and_blocks_sends() -> None:
    room = FakeRoomTransport(reply_delay_s=10.0, turns=[["never delivered"]])
    await room.connect("wss://x.test", "t")
    await room.wait_for_agent(1.0)
    await room.send_text("hi")

    await room.close()
    await room.close()

    assert [r async for r in room.replies()] == []
    assert room.closed is True and room.close_calls == 2 and room.connected is False
    with pytest.raises(RuntimeError):
        await room.send_text("after close")


async def test_fake_room_drop_simulates_a_disconnect() -> None:
    room = FakeRoomTransport()
    await room.connect("wss://x.test", "t")
    await room.wait_for_agent(1.0)
    room.emit("late")

    await room.drop()

    assert [r.text async for r in room.replies()] == ["late"]
    assert room.connected is False and room.closed is False


async def test_fake_room_connect_error_is_raised() -> None:
    room = FakeRoomTransport(connect_error=ConnectionError("refused"))

    with pytest.raises(ConnectionError):
        await room.connect("wss://x.test", "t")
    assert room.connected is False
