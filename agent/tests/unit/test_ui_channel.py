"""Unit tests for `lkap_agent.ui.channel.UiChannel`.

Uses `fakes.fake_room.FakeRoom` (W0-SCAFFOLD) — a real `rtc.Room` connection
is FFI-backed and cannot be constructed in a unit test.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fakes.fake_room import FakeRemoteParticipant, FakeRoom
from livekit import rtc
from lkap_contracts.ui_protocol import (
    RPC_AGENT_ACTION,
    RPC_UI_REQUEST,
    TOPIC_UI_ACTIVITY,
    TOPIC_UI_ASSET,
    TOPIC_UI_STATE,
    ActivityEvent,
    AgentAction,
    AgentActionResult,
    BlockSpec,
    ChecklistItem,
    Note,
    UiPatch,
    UiPatchOp,
    UiRequest,
    UiSnapshot,
)
from pydantic import ValidationError

from lkap_agent.ui.channel import BARGE_IN, UiChannel


def _make_channel(room: FakeRoom | None = None, **kwargs: object) -> tuple[UiChannel, FakeRoom]:
    room = room or FakeRoom()
    channel = UiChannel(room, "sess-1", **kwargs)  # type: ignore[arg-type]
    channel.start()
    return channel, room


async def test_snapshot_sent_on_session_start_has_seq_one() -> None:
    channel, room = _make_channel()
    await channel.snapshot()
    assert channel.seq == 1
    sent = room.local_participant.sent_text[-1]
    assert sent.topic == TOPIC_UI_STATE
    snapshot = UiSnapshot.model_validate_json(sent.text)
    assert snapshot.seq == 1
    assert snapshot.session_id == "sess-1"


async def test_patch_bumps_seq_monotonically_and_validates() -> None:
    channel, room = _make_channel()
    await channel.snapshot()
    await channel.set_status("Reviewing", "info")
    await channel.add_note("Policy found", key="policy")

    seqs = []
    for sent in room.local_participant.sent_text:
        payload = json.loads(sent.text)
        seqs.append(payload["seq"])
    assert seqs == sorted(seqs)
    assert seqs == list(range(1, len(seqs) + 1))

    last_patch = UiPatch.model_validate_json(room.local_participant.sent_text[-1].text)
    assert last_patch.ops[0].path == "/notes"
    assert channel.state.notes[0].text == "Policy found"


async def test_snapshot_resent_every_50_patches() -> None:
    channel, room = _make_channel()
    await channel.snapshot()  # seq=1, 1 message so far

    for i in range(50):
        await channel.add_note(f"note-{i}")

    # 1 initial snapshot + 50 patches + 1 auto-resnapshot = 52 messages.
    assert len(room.local_participant.sent_text) == 52
    last = json.loads(room.local_participant.sent_text[-1].text)
    assert last["type"] == "snapshot"
    assert channel.seq == 51  # the auto-snapshot does not consume a seq number


async def test_set_checklist_replaces_list() -> None:
    channel, _room = _make_channel()
    items = [ChecklistItem(id="a", label="Policy number"), ChecklistItem(id="b", label="Photos", done=True)]
    await channel.set_checklist(items)
    assert [item.id for item in channel.state.checklist] == ["a", "b"]
    assert channel.state.checklist[1].done is True


async def test_push_asset_streams_bytes_with_caption_attribute_and_patches_assets() -> None:
    channel, room = _make_channel()
    asset_id = await channel.push_asset(b"\xff\xd8\xff\xd9", "image/jpeg", "photo", caption="evidence")

    stream = room.local_participant.byte_streams[-1]
    assert stream.topic == TOPIC_UI_ASSET
    assert stream.attributes["asset_id"] == asset_id
    assert stream.attributes["caption"] == "evidence"  # CONTRACTS §10 wins over ARCHITECTURE's caption_ref
    assert stream.attributes["mime"] == "image/jpeg"
    assert stream.chunks == [b"\xff\xd8\xff\xd9"]
    assert stream.closed is True

    assert channel.state.assets[0].asset_id == asset_id
    assert channel.state.assets[0].caption == "evidence"


async def test_push_asset_without_caption_omits_attribute() -> None:
    channel, room = _make_channel()
    await channel.push_asset(b"\x89PNG", "image/png", "sketch")
    stream = room.local_participant.byte_streams[-1]
    assert "caption" not in stream.attributes


async def test_activity_upserts_by_id_and_trims_ring_buffer() -> None:
    channel, _room = _make_channel()
    for i in range(35):
        event = ActivityEvent(
            id=f"job-{i}", ts=0.0, source="lookup_policy", label="Policy desk", phase="running", headline="x"
        )
        await channel.activity(event)
    assert len(channel.state.activity) == 30
    assert [e.id for e in channel.state.activity] == [f"job-{i}" for i in range(5, 35)]

    # same id replaces in place rather than appending
    await channel.activity(
        ActivityEvent(
            id="job-34", ts=1.0, source="lookup_policy", label="Policy desk", phase="done", headline="done"
        )
    )
    assert len(channel.state.activity) == 30
    assert channel.state.activity[-1].phase == "done"


async def test_activity_broadcasts_on_its_own_topic_too() -> None:
    channel, room = _make_channel()
    event = ActivityEvent(
        id="job-1", ts=0.0, source="lookup_policy", label="Policy desk", phase="running", headline="x"
    )
    await channel.activity(event)

    activity_messages = [m for m in room.local_participant.sent_text if m.topic == TOPIC_UI_ACTIVITY]
    assert len(activity_messages) == 1
    assert ActivityEvent.model_validate_json(activity_messages[0].text) == event
    # ...and it is still upserted into state (for late joiners via snapshot).
    assert channel.state.activity[0].id == "job-1"


async def test_patch_coerces_plain_dict_values_like_the_fake_ui_channel() -> None:
    """A pack/tool that patches `/notes` with a plain dict (as `fake_ctx.FakeUiChannel`
    accepts) must behave identically against the real `UiChannel`: the fake calls
    `Note.model_validate(op.value)`, so the real channel must too, or `_item_key`
    upsert/remove-by-key would silently break against a raw dict.
    """
    channel, _room = _make_channel()
    await channel.patch(
        [
            UiPatchOp(
                op="upsert",
                path="/notes",
                value={"id": "n1", "text": "first", "ts": 1.0, "key": "k"},
                key="k",
            )
        ]
    )
    assert isinstance(channel.state.notes[0], Note)
    assert channel.state.notes[0].text == "first"

    await channel.patch(
        [
            UiPatchOp(
                op="upsert",
                path="/notes",
                value={"id": "n2", "text": "second", "ts": 2.0, "key": "k"},
                key="k",
            )
        ]
    )
    assert len(channel.state.notes) == 1
    assert channel.state.notes[0].text == "second"


async def test_get_snapshot_action_triggers_snapshot_and_returns_ok() -> None:
    channel, room = _make_channel()
    payload = AgentAction(action="get_snapshot").model_dump_json()
    response = await room.local_participant.invoke_rpc(RPC_AGENT_ACTION, payload)
    result = AgentActionResult.model_validate_json(response)
    assert result.ok is True
    assert channel.seq == 1
    assert json.loads(room.local_participant.sent_text[-1].text)["type"] == "snapshot"


async def test_set_video_source_action_calls_callback() -> None:
    received: list[str] = []

    async def on_set_video_source(source: str) -> None:
        received.append(source)

    channel, room = _make_channel(on_set_video_source=on_set_video_source)
    payload = AgentAction(action="set_video_source", payload={"source": "screen"}).model_dump_json()
    response = await room.local_participant.invoke_rpc(RPC_AGENT_ACTION, payload)
    result = AgentActionResult.model_validate_json(response)
    assert result.ok is True
    assert received == ["screen"]


async def test_set_video_source_action_without_callback_returns_error() -> None:
    channel, room = _make_channel()
    payload = AgentAction(action="set_video_source", payload={"source": "camera"}).model_dump_json()
    response = await room.local_participant.invoke_rpc(RPC_AGENT_ACTION, payload)
    result = AgentActionResult.model_validate_json(response)
    assert result.ok is False
    assert result.error is not None


async def test_ui_action_dispatches_to_pack_callback_and_returns_payload() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def on_ui_action(name: str, data: dict[str, object]) -> dict[str, object]:
        calls.append((name, data))
        return {"confirmed": True}

    channel, room = _make_channel(on_ui_action=on_ui_action)
    action = AgentAction(action="ui_action", payload={"name": "confirm_sketch", "data": {"v": 2}})
    response = await room.local_participant.invoke_rpc(RPC_AGENT_ACTION, action.model_dump_json())
    result = AgentActionResult.model_validate_json(response)
    assert result.ok is True
    assert result.payload == {"confirmed": True}
    assert calls == [("confirm_sketch", {"v": 2})]


async def test_invalid_action_payload_returns_ok_false_not_an_exception() -> None:
    channel, room = _make_channel()
    response = await room.local_participant.invoke_rpc(RPC_AGENT_ACTION, "not json")
    result = AgentActionResult.model_validate_json(response)
    assert result.ok is False
    assert result.error


async def test_request_ui_performs_rpc_to_remote_participant() -> None:
    channel, room = _make_channel()
    room.add_remote_participant(FakeRemoteParticipant("web-ui"))
    room.local_participant.rpc_call_responses["lkap.ui.request"] = json.dumps(
        {"ok": True, "payload": {"opened": True}}
    )

    result = await channel.request_ui("open_dialog", {"dialog": "packet"})

    assert result == {"opened": True}
    call = room.local_participant.rpc_calls[-1]
    assert call.destination_identity == "web-ui"
    assert call.method == "lkap.ui.request"


async def test_request_ui_without_remote_participant_raises() -> None:
    channel, _room = _make_channel()
    with pytest.raises(RuntimeError):
        await channel.request_ui("toast", {})


async def test_request_ui_prefers_explicit_ui_identity_over_room_lookup() -> None:
    """An avatar's `lk.publish_on_behalf` participant also shows up in
    `room.remote_participants`; a session wired with `ui_identity=` must still
    RPC the browser, not whichever remote participant happens to be first.
    """
    channel, room = _make_channel(ui_identity="web-ui")
    room.add_remote_participant(FakeRemoteParticipant("avatar-bey"))
    room.local_participant.rpc_call_responses["lkap.ui.request"] = json.dumps({"ok": True, "payload": {}})

    await channel.request_ui("toast", {"text": "hi"})

    assert room.local_participant.rpc_calls[-1].destination_identity == "web-ui"


async def test_request_ui_fallback_skips_avatar_and_agent_participants() -> None:
    """D-W2-7: without `ui_identity`, the room lookup must never pick the avatar worker
    (`lk.publish_on_behalf`) or another agent participant, even when it joined first.
    """
    channel, room = _make_channel()
    room.add_remote_participant(
        FakeRemoteParticipant("avatar-bey", attributes={"lk.publish_on_behalf": "agent-xyz"})
    )
    room.add_remote_participant(
        FakeRemoteParticipant("other-agent", kind=rtc.ParticipantKind.PARTICIPANT_KIND_AGENT)
    )
    room.add_remote_participant(FakeRemoteParticipant("web-ui"))
    room.local_participant.rpc_call_responses["lkap.ui.request"] = json.dumps({"ok": True, "payload": {}})

    await channel.request_ui("toast", {"text": "hi"})

    assert room.local_participant.rpc_calls[-1].destination_identity == "web-ui"


async def test_request_ui_fallback_raises_when_only_an_avatar_is_present() -> None:
    channel, room = _make_channel()
    room.add_remote_participant(
        FakeRemoteParticipant("avatar-bey", attributes={"lk.publish_on_behalf": "agent-xyz"})
    )
    with pytest.raises(RuntimeError):
        await channel.request_ui("toast", {})


def test_close_does_not_raise_when_participant_lacks_unregister() -> None:
    # `FakeLocalParticipant` (W0-SCAFFOLD) implements `register_rpc_method` but not
    # `unregister_rpc_method`; `close()` must degrade gracefully against it.
    channel, _room = _make_channel()
    channel.close()


# ------------------------------------------------ V5-02: request_block

#: A pack-defined `custom` block (untyped state) stands in for the V5-08
#: requestable types; `intake` is a `form` block, `costs` is not requestable.
REQUEST_BLOCKS = [
    BlockSpec(id="pick", type="custom", config={"kind": "picker"}, order=0),
    BlockSpec(id="intake", type="form", order=1),
    BlockSpec(id="costs", type="table", order=2),
]
FORM_SCHEMA: dict[str, Any] = {"type": "object", "properties": {"name": {"type": "string"}}}


def _request_channel(
    **kwargs: Any,
) -> tuple[UiChannel, FakeRoom, list[tuple[str, dict[str, Any]]]]:
    room = FakeRoom()
    room.add_remote_participant(FakeRemoteParticipant("web-ui"))
    events: list[tuple[str, dict[str, Any]]] = []
    channel = UiChannel(room, "sess-1", record_event=lambda t, p: events.append((t, p)), **kwargs)  # type: ignore[arg-type]
    channel.start()
    channel.init_blocks(REQUEST_BLOCKS)
    _ack(room)
    return channel, room, events


def _ack(room: FakeRoom, payload: dict[str, Any] | None = None) -> None:
    room.local_participant.rpc_call_responses[RPC_UI_REQUEST] = json.dumps(
        {"ok": True, "payload": payload or {}}
    )


async def _action(room: FakeRoom, action: str, payload: dict[str, Any]) -> AgentActionResult:
    raw = AgentAction.model_validate({"action": action, "payload": payload}).model_dump_json()
    return AgentActionResult.model_validate_json(
        await room.local_participant.invoke_rpc(RPC_AGENT_ACTION, raw)
    )


async def _until(predicate: Any, *, tries: int = 50) -> None:
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never became true")


def _last_patch(room: FakeRoom) -> UiPatch:
    texts = [m.text for m in room.local_participant.sent_text if m.topic == TOPIC_UI_STATE]
    return UiPatch.model_validate_json(texts[-1])


async def test_request_block_resolves_with_the_submitted_values() -> None:
    channel, room, events = _request_channel()
    task = asyncio.create_task(
        channel.request_block("pick", timeout_s=5, payload={"schema": FORM_SCHEMA, "hint": "tap one"})
    )
    await _until(lambda: room.local_participant.rpc_calls)

    request = UiRequest.model_validate_json(room.local_participant.rpc_calls[-1].payload)
    assert request.method == "request"
    assert request.payload == {"block_id": "pick", "timeout_s": 5.0, "schema": FORM_SCHEMA, "hint": "tap one"}
    assert channel.pending_requests == {"pick": "request"}

    result = await _action(room, "block_submit", {"block_id": "pick", "values": {"choice": "b"}})
    assert result.ok is True
    assert await task == {"choice": "b"}
    assert channel.state.blocks["pick"]["status"] == "submitted"
    assert channel.state.blocks["pick"]["values"] == {"choice": "b"}
    assert isinstance(channel.state.blocks["pick"]["submitted_at"], float)
    assert events[-1] == (
        "block_update",
        {"block_id": "pick", "block_type": "custom", "op": "block_submitted", "values": {"choice": "b"}},
    )
    assert channel.pending_requests == {}


async def test_request_block_patches_only_the_status_and_keeps_the_block_content() -> None:
    channel, room, events = _request_channel()
    await channel.set_block("pick", {"options": ["a", "b"]})
    task = asyncio.create_task(channel.request_block("pick", timeout_s=5))
    await _until(lambda: room.local_participant.rpc_calls)

    request_patch = _last_patch(room)
    assert [(op.path, op.value) for op in request_patch.ops] == [
        ("/blocks/pick/status", "requested"),
        ("/blocks/pick/submitted_at", None),
    ]
    assert channel.state.blocks["pick"]["options"] == ["a", "b"]
    assert ("block_update", {"block_id": "pick", "block_type": "custom", "op": "block_requested"}) in events
    task.cancel()


async def test_request_block_snapshot_shows_requested_for_a_reconnecting_browser() -> None:
    channel, room, _ = _request_channel()
    task = asyncio.create_task(channel.request_block("intake", timeout_s=5))
    await _until(lambda: room.local_participant.rpc_calls)

    assert (await _action(room, "get_snapshot", {})).ok
    snapshot = UiSnapshot.model_validate_json(room.local_participant.sent_text[-1].text)
    assert snapshot.state.blocks["intake"]["status"] == "requested"
    assert snapshot.state.blocks["intake"]["submitted_at"] is None
    task.cancel()


async def test_block_submit_cancelled_resolves_none_and_marks_the_block_cancelled() -> None:
    channel, room, events = _request_channel()
    task = asyncio.create_task(channel.request_block("pick", timeout_s=5))
    await _until(lambda: room.local_participant.rpc_calls)

    assert (await _action(room, "block_submit", {"block_id": "pick", "cancelled": True})).ok
    assert await task is None
    assert [(op.path, op.value) for op in _last_patch(room).ops] == [("/blocks/pick/status", "cancelled")]
    assert channel.state.blocks["pick"]["status"] == "cancelled"
    assert events[-1] == (
        "block_update",
        {"block_id": "pick", "block_type": "custom", "op": "block_cancelled"},
    )


async def test_request_block_timeout_resolves_none_and_marks_the_block_cancelled() -> None:
    channel, _room, events = _request_channel()
    assert await channel.request_block("pick", timeout_s=0.01) is None
    assert channel.state.blocks["pick"]["status"] == "cancelled"
    assert events[-1][1] == {
        "block_id": "pick",
        "block_type": "custom",
        "op": "block_cancelled",
        "reason": "timeout",
    }
    assert channel.pending_requests == {}


async def test_second_request_on_a_block_releases_the_first_with_none() -> None:
    channel, room, _ = _request_channel()
    first = asyncio.create_task(channel.request_block("pick", timeout_s=5))
    await _until(lambda: room.local_participant.rpc_calls)
    second = asyncio.create_task(channel.request_block("pick", timeout_s=5))

    assert await asyncio.wait_for(first, 1) is None
    assert channel.state.blocks["pick"]["status"] == "requested"
    await _action(room, "block_submit", {"block_id": "pick", "values": {"n": 2}})
    assert await second == {"n": 2}


async def test_cancel_pending_releases_every_pending_request() -> None:
    channel, _room, events = _request_channel()
    generic = asyncio.create_task(channel.request_block("pick", timeout_s=5))
    form = asyncio.create_task(channel.request_form("intake", FORM_SCHEMA))
    await _until(lambda: len(channel.pending_requests) == 2)

    released = channel.cancel_pending(BARGE_IN)

    assert sorted(released) == ["intake", "pick"]
    assert await asyncio.wait_for(generic, 1) is None
    assert await asyncio.wait_for(form, 1) is None
    await _until(lambda: channel.state.blocks["pick"]["status"] == "cancelled")
    await _until(lambda: channel.state.blocks["intake"]["status"] == "idle")  # the legacy form status
    reasons = {
        p["block_id"]: p.get("reason")
        for _t, p in events
        if p.get("op") in ("block_cancelled", "form_cancelled")
    }
    assert reasons == {"pick": BARGE_IN, "intake": BARGE_IN}
    assert channel.pending_requests == {}
    assert channel.cancel_pending(BARGE_IN) == []


async def test_cancel_pending_can_spare_the_legacy_form() -> None:
    channel, room, _ = _request_channel()
    generic = asyncio.create_task(channel.request_block("pick", timeout_s=5))
    form = asyncio.create_task(channel.request_form("intake", FORM_SCHEMA))
    await _until(lambda: len(channel.pending_requests) == 2)

    assert channel.cancel_pending(BARGE_IN, methods=["request"]) == ["pick"]
    assert await asyncio.wait_for(generic, 1) is None
    assert channel.pending_requests == {"intake": "form"}
    await _action(room, "form_submit", {"block_id": "intake", "values": {"name": "Ada"}})
    assert await form == {"name": "Ada"}


async def test_cancel_pending_never_hides_a_newer_request_on_the_same_block() -> None:
    channel, _room, _ = _request_channel()
    first = asyncio.create_task(channel.request_block("pick", timeout_s=5))
    await _until(lambda: channel.pending_requests)
    channel.cancel_pending(BARGE_IN)
    second = asyncio.create_task(channel.request_block("pick", timeout_s=5))
    assert await asyncio.wait_for(first, 1) is None
    await _until(lambda: channel.pending_requests)
    for _ in range(10):
        await asyncio.sleep(0)
    assert channel.state.blocks["pick"]["status"] == "requested"
    second.cancel()


async def test_block_submit_answers_a_legacy_form_request() -> None:
    """V5-03 sends `block_submit` for every block, the form included."""
    channel, room, events = _request_channel()
    task = asyncio.create_task(channel.request_form("intake", FORM_SCHEMA))
    await _until(lambda: room.local_participant.rpc_calls)
    assert UiRequest.model_validate_json(room.local_participant.rpc_calls[-1].payload).method == "form"

    assert (await _action(room, "block_submit", {"block_id": "intake", "values": {"name": "Ada"}})).ok
    assert await task == {"name": "Ada"}
    assert events[-1] == ("form_submitted", {"block_id": "intake", "values": {"name": "Ada"}})


async def test_generic_request_on_a_form_block_stores_the_values() -> None:
    channel, room, _ = _request_channel()
    await channel.set_block("intake", {"schema": FORM_SCHEMA})
    task = asyncio.create_task(channel.request_block("intake", timeout_s=5, payload={"schema": FORM_SCHEMA}))
    await _until(lambda: room.local_participant.rpc_calls)

    assert (await _action(room, "form_submit", {"block_id": "intake", "values": {"name": "Ada"}})).ok
    assert await task == {"name": "Ada"}
    assert [op.path for op in _last_patch(room).ops] == [
        "/blocks/intake/values",
        "/blocks/intake/status",
        "/blocks/intake/submitted_at",
    ]
    assert channel.state.blocks["intake"]["schema"] == FORM_SCHEMA


async def test_request_block_accepts_an_inline_answer_in_the_rpc_result() -> None:
    channel, room, _ = _request_channel()
    _ack(room, {"values": {"choice": "a"}})
    assert await channel.request_block("pick", timeout_s=1) == {"choice": "a"}
    assert channel.state.blocks["pick"]["status"] == "submitted"

    _ack(room, {"cancelled": True})
    assert await channel.request_block("pick", timeout_s=1) is None
    assert channel.state.blocks["pick"]["status"] == "cancelled"


async def test_request_block_survives_a_failed_request_rpc() -> None:
    room = FakeRoom()  # no browser yet: the RPC fails, the state still shows the request
    channel = UiChannel(room, "sess-1")  # type: ignore[arg-type]
    channel.start()
    channel.init_blocks(REQUEST_BLOCKS)
    task = asyncio.create_task(channel.request_block("pick", timeout_s=5))
    await _until(lambda: channel.state.blocks["pick"].get("status") == "requested")
    await asyncio.sleep(0)
    assert not task.done()
    await _action(room, "block_submit", {"block_id": "pick", "values": {"x": 1}})
    assert await task == {"x": 1}


async def test_late_block_submit_goes_to_the_unsolicited_handler() -> None:
    late: list[tuple[str, dict[str, Any]]] = []

    async def on_late(block_id: str, values: dict[str, Any]) -> None:
        late.append((block_id, values))

    channel, room, _ = _request_channel()
    channel.bind(on_unsolicited_form=on_late)
    assert await channel.request_block("pick", timeout_s=0.01) is None
    await _action(room, "block_submit", {"block_id": "pick", "values": {"x": "late"}})
    assert late == [("pick", {"x": "late"})]
    assert channel.state.blocks["pick"]["status"] == "submitted"


async def test_request_block_is_released_on_session_close() -> None:
    channel, room, _ = _request_channel()
    task = asyncio.create_task(channel.request_block("pick", timeout_s=5))
    await _until(lambda: room.local_participant.rpc_calls)
    channel.close()
    assert await asyncio.wait_for(task, 1) is None
    assert await channel.request_block("pick", timeout_s=5) is None  # closed: no new waits


async def test_request_block_refuses_a_block_that_cannot_be_requested() -> None:
    channel, room, _ = _request_channel()
    with pytest.raises(ValueError, match="cannot be requested"):
        await channel.request_block("costs", timeout_s=5)
    with pytest.raises(ValidationError):
        await channel.request_block("pick", timeout_s=0)
    assert channel.pending_requests == {}
    assert room.local_participant.rpc_calls == []


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"block_id": "nope", "values": {}}, "unknown block"),
        ({"block_id": "costs", "values": {}}, "cannot be submitted"),
        ({"block_id": "pick"}, "values object or cancelled"),
        ({"block_id": "pick", "values": {}, "cancelled": True}, "values object or cancelled"),
        ({"block_id": "pick", "values": "x"}, "values object or cancelled"),
    ],
)
async def test_block_submit_rejects_bad_payloads(payload: dict[str, Any], error: str) -> None:
    _channel, room, _ = _request_channel()
    result = await _action(room, "block_submit", payload)
    assert result.ok is False
    assert result.error is not None and error in result.error
