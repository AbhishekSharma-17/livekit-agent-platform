"""Unit tests for `lkap_agent.ui.channel.UiChannel`.

Uses `fakes.fake_room.FakeRoom` (W0-SCAFFOLD) — a real `rtc.Room` connection
is FFI-backed and cannot be constructed in a unit test.
"""

from __future__ import annotations

import json

import pytest
from fakes.fake_room import FakeRemoteParticipant, FakeRoom
from livekit import rtc
from lkap_contracts.ui_protocol import (
    RPC_AGENT_ACTION,
    TOPIC_UI_ACTIVITY,
    TOPIC_UI_ASSET,
    TOPIC_UI_STATE,
    ActivityEvent,
    AgentAction,
    AgentActionResult,
    ChecklistItem,
    Note,
    UiPatch,
    UiPatchOp,
    UiSnapshot,
)

from lkap_agent.ui.channel import UiChannel


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
