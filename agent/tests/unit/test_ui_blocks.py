"""V2-10: panel blocks in `UiChannel` and the pure helpers in `lkap_agent.ui.blocks`.

Runs the real `UiChannel` over `fakes.fake_room.FakeRoom`; the form RPC is
driven the way the browser does it: `lkap.ui.request {method: "form"}` answered
through `rpc_call_responses`, then `lkap.agent.action {action: "form_submit"}`
through `invoke_rpc`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fakes.fake_room import FakeRemoteParticipant, FakeRoom
from lkap_contracts.agent_config import PanelLayout
from lkap_contracts.api_models import KbHit
from lkap_contracts.migrate import DEFAULT_COMPOSITE_BLOCKS
from lkap_contracts.packs import PackManifest
from lkap_contracts.ui_protocol import (
    RPC_AGENT_ACTION,
    RPC_UI_REQUEST,
    TOPIC_UI_STATE,
    AgentAction,
    AgentActionResult,
    BlockSpec,
    UiPatch,
    UiPatchOp,
    UiRequest,
    UiSnapshot,
)
from pydantic import ValidationError

from lkap_agent.packs.loader import null_manifest
from lkap_agent.ui.blocks import (
    block_path,
    flow_steps_state,
    initial_block_state,
    initial_block_states,
    pick_block,
    resolve_block_specs,
    session_block_specs,
    validate_block_state,
)
from lkap_agent.ui.channel import UiChannel

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"policy_number": {"type": "string", "title": "Policy number"}},
    "required": ["policy_number"],
}

L8_BLOCKS = [
    BlockSpec(id="status", type="status", order=0),
    BlockSpec(id="intake", type="form", title="Intake", order=1),
    BlockSpec(id="costs", type="table", config={"columns": [{"key": "item", "label": "Item"}]}, order=2),
    BlockSpec(id="photos", type="gallery", order=3),
    BlockSpec(id="doc", type="document", order=4),
    BlockSpec(id="sources", type="kb_citations", order=5),
]


def _channel(
    specs: list[BlockSpec] | None = None, **kwargs: Any
) -> tuple[UiChannel, FakeRoom, list[tuple[str, dict[str, Any]]]]:
    room = FakeRoom()
    room.add_remote_participant(FakeRemoteParticipant("web-ui"))
    events: list[tuple[str, dict[str, Any]]] = []
    channel = UiChannel(room, "sess-1", record_event=lambda t, p: events.append((t, p)), **kwargs)  # type: ignore[arg-type]
    channel.start()
    channel.init_blocks(specs if specs is not None else L8_BLOCKS)
    return channel, room, events


def _ack(room: FakeRoom, payload: dict[str, Any] | None = None) -> None:
    room.local_participant.rpc_call_responses[RPC_UI_REQUEST] = json.dumps(
        {"ok": True, "payload": payload or {}}
    )


def _messages(room: FakeRoom) -> list[dict[str, Any]]:
    return [json.loads(m.text) for m in room.local_participant.sent_text if m.topic == TOPIC_UI_STATE]


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


# ------------------------------------------------------------------ helpers


def test_resolve_block_specs_prefers_config_blocks() -> None:
    panel = PanelLayout(blocks=[BlockSpec(id="t", type="table")])
    assert [s.id for s in resolve_block_specs(panel, null_manifest())] == ["t"]


def test_resolve_block_specs_empty_composite_falls_back_to_pack_default_panel() -> None:
    manifest = null_manifest().model_copy(
        update={"default_panel": PanelLayout.model_validate({"blocks": DEFAULT_COMPOSITE_BLOCKS})}
    )
    specs = resolve_block_specs(PanelLayout(), manifest)
    assert [s.id for s in specs] == ["status", "notes", "checklist", "activity"]


def test_resolve_block_specs_custom_panel_without_blocks_is_empty() -> None:
    from packs.insurance_claim.manifest import MANIFEST  # noqa: PLC0415

    panel = PanelLayout(panel_id="insurance_notebook", blocks=[])
    assert resolve_block_specs(panel, MANIFEST) == []


def test_resolve_block_specs_custom_panel_uses_pack_blocks() -> None:
    manifest: PackManifest = null_manifest().model_copy(
        update={"blocks": [BlockSpec(id="evidence", type="gallery")]}
    )
    specs = resolve_block_specs(PanelLayout(panel_id="my_panel"), manifest)
    assert [s.id for s in specs] == ["evidence"]


def test_resolve_block_specs_sorts_by_order_and_drops_duplicates() -> None:
    panel = PanelLayout(
        blocks=[
            BlockSpec(id="b", type="notes", order=2),
            BlockSpec(id="a", type="status", order=1),
            BlockSpec(id="a", type="table", order=3),
        ]
    )
    assert [(s.id, s.type) for s in resolve_block_specs(panel)] == [("a", "status"), ("b", "notes")]


@pytest.mark.parametrize("block_type", ["status", "notes", "checklist", "activity", "custom"])
def test_initial_block_state_envelope_types_hold_empty_dict(block_type: Any) -> None:
    assert initial_block_state(BlockSpec(id="x", type=block_type)) == {}


def test_initial_block_state_form_dumps_schema_by_alias() -> None:
    state = initial_block_state(BlockSpec(id="f", type="form"))
    assert state == {"schema": {}, "values": {}, "status": "idle", "submitted_at": None}


def test_initial_block_state_seeds_model_fields_from_config() -> None:
    spec = BlockSpec(id="t", type="table", config={"columns": [{"key": "a", "label": "A"}], "zebra": True})
    state = initial_block_state(spec)
    assert state["columns"] == [{"key": "a", "label": "A", "type": "string"}]
    assert "zebra" not in state


def test_initial_block_state_ignores_an_invalid_config() -> None:
    state = initial_block_state(BlockSpec(id="t", type="table", config={"columns": "nope"}))
    assert state == {"columns": [], "rows": [], "selected_row": None}


def test_validate_block_state_rejects_a_state_that_does_not_fit() -> None:
    with pytest.raises(ValidationError):
        validate_block_state("gallery", {"asset_ids": "not-a-list"})


def test_block_path_rejects_a_slash_in_the_id() -> None:
    assert block_path("a", "/rows") == "/blocks/a/rows"
    assert block_path("a", "/") == "/blocks/a"
    with pytest.raises(ValueError):
        block_path("a/b")


def test_pick_block_uses_the_only_block_of_a_type() -> None:
    assert pick_block(L8_BLOCKS, "form", "wrong") == "intake"
    with pytest.raises(ValueError, match="no video block"):
        pick_block(L8_BLOCKS, "video", None)


def test_session_block_specs_falls_back_to_config_for_a_plain_channel() -> None:
    class _Plain:
        pass

    panel = PanelLayout(blocks=[BlockSpec(id="t", type="table")])
    assert [s.id for s in session_block_specs(_Plain(), panel)] == ["t"]


# ---------------------------------------------------------------- channel


async def test_init_blocks_seeds_state_before_the_first_snapshot() -> None:
    channel, room, _ = _channel()
    await channel.snapshot()
    snapshot = UiSnapshot.model_validate_json(room.local_participant.sent_text[-1].text)
    assert snapshot.seq == 1
    assert set(snapshot.state.blocks) == {s.id for s in L8_BLOCKS}
    assert snapshot.state.blocks["intake"]["status"] == "idle"
    assert "schema" in _messages(room)[0]["state"]["blocks"]["intake"]


async def test_init_blocks_twice_keeps_live_state() -> None:
    channel, _room, _ = _channel()
    await channel.set_block("photos", {"asset_ids": ["a1"]})
    channel.init_blocks([*L8_BLOCKS, BlockSpec(id="extra", type="notes")])
    assert channel.state.blocks["photos"]["asset_ids"] == ["a1"]
    assert channel.state.blocks["extra"] == {}


async def test_set_block_emits_set_on_the_block_path() -> None:
    channel, room, events = _channel()
    await channel.set_block("photos", {"asset_ids": ["a1"], "selected": "a1"})

    patch = UiPatch.model_validate_json(room.local_participant.sent_text[-1].text)
    assert [(op.op, op.path) for op in patch.ops] == [("set", "/blocks/photos")]
    assert patch.ops[0].value == {"asset_ids": ["a1"], "selected": "a1"}
    assert channel.state.blocks["photos"]["selected"] == "a1"
    assert events == [("block_update", {"block_id": "photos", "block_type": "gallery", "op": "set"})]


async def test_set_block_validates_against_the_block_type() -> None:
    channel, room, _ = _channel()
    with pytest.raises(ValidationError):
        await channel.set_block("photos", {"asset_ids": 3})
    assert room.local_participant.sent_text == []


async def test_set_block_on_an_unknown_block_passes_state_through() -> None:
    channel, _room, _ = _channel()
    await channel.set_block("pack_owned", {"anything": [1, 2]})
    assert channel.state.blocks["pack_owned"] == {"anything": [1, 2]}


async def test_patch_block_rewrites_relative_paths_into_one_patch() -> None:
    channel, room, events = _channel()
    await channel.patch_block(
        "costs",
        [
            UiPatchOp(op="append", path="/rows", value={"id": "r1", "item": "Tyre"}),
            UiPatchOp(op="set", path="/selected_row", value="r1"),
        ],
    )
    patch = UiPatch.model_validate_json(room.local_participant.sent_text[-1].text)
    assert [op.path for op in patch.ops] == ["/blocks/costs/rows", "/blocks/costs/selected_row"]
    assert channel.state.blocks["costs"]["rows"] == [{"id": "r1", "item": "Tyre"}]
    assert channel.state.blocks["costs"]["selected_row"] == "r1"
    assert events[-1][1]["op"] == "patch"


async def test_patch_block_that_breaks_the_type_sends_nothing() -> None:
    channel, room, _ = _channel()
    with pytest.raises(ValidationError):
        await channel.patch_block("costs", [UiPatchOp(op="set", path="/rows", value="nope")])
    assert room.local_participant.sent_text == []
    assert channel.state.blocks["costs"]["rows"] == []


async def test_block_tree_ops_upsert_and_remove_by_id_like_the_browser() -> None:
    channel, _room, _ = _channel()
    rows = "/blocks/costs/rows"
    await channel.patch([UiPatchOp(op="append", path=rows, value={"id": "r1", "v": 1})])
    await channel.patch([UiPatchOp(op="upsert", path=rows, value={"id": "r1", "v": 2})])
    await channel.patch([UiPatchOp(op="upsert", path=rows, value={"id": "r2", "v": 3})])
    await channel.patch([UiPatchOp(op="set", path="/blocks/costs/rows/1/v", value=4)])
    await channel.patch([UiPatchOp(op="remove", path=rows, key="r1")])
    assert channel.state.blocks["costs"]["rows"] == [{"id": "r2", "v": 4}]
    await channel.patch([UiPatchOp(op="remove", path="/blocks/costs/selected_row")])
    assert "selected_row" not in channel.state.blocks["costs"]
    await channel.patch([UiPatchOp(op="set", path="/blocks/costs/rows/9/v", value=1)])  # out of range: no-op
    assert channel.state.blocks["costs"]["rows"] == [{"id": "r2", "v": 4}]


async def test_patch_dumps_block_models_by_alias_on_the_wire() -> None:
    from lkap_contracts.ui_protocol import FormBlockState  # noqa: PLC0415

    channel, room, _ = _channel()
    await channel.patch([UiPatchOp(op="set", path="/blocks/intake", value=FormBlockState(schema=SCHEMA))])
    wire = json.loads(room.local_participant.sent_text[-1].text)
    assert wire["ops"][0]["value"]["schema"] == SCHEMA
    assert "schema_" not in wire["ops"][0]["value"]
    assert channel.state.blocks["intake"]["schema"] == SCHEMA


async def test_resnapshot_every_50_patches_includes_blocks() -> None:
    channel, room, _ = _channel()
    await channel.snapshot()
    for i in range(50):
        await channel.patch_block(
            "costs", [UiPatchOp(op="append", path="/rows", value={"id": f"r{i}", "item": str(i)})]
        )
    last = _messages(room)[-1]
    assert last["type"] == "snapshot"
    assert len(last["state"]["blocks"]["costs"]["rows"]) == 50
    assert set(last["state"]["blocks"]) == {s.id for s in L8_BLOCKS}


async def test_seq_stays_contiguous_across_block_and_envelope_writes() -> None:
    channel, room, _ = _channel()
    await channel.snapshot()
    await channel.set_status("Reviewing", "info")
    await channel.set_block("doc", {"url": "https://example.com/a.pdf", "page": 2})
    await channel.add_note("hi")
    assert [m["seq"] for m in _messages(room)] == [1, 2, 3, 4]


async def test_push_asset_image_appends_to_gallery_blocks_in_the_same_patch() -> None:
    channel, room, _ = _channel()
    asset_id = await channel.push_asset(b"\xff\xd8", "image/jpeg", "photo", caption="dent")
    patch = UiPatch.model_validate_json(room.local_participant.sent_text[-1].text)
    assert [op.path for op in patch.ops] == ["/assets", "/blocks/photos/asset_ids"]
    assert channel.state.blocks["photos"]["asset_ids"] == [asset_id]
    assert room.local_participant.byte_streams[-1].attributes["caption"] == "dent"


async def test_push_asset_non_image_leaves_galleries_alone() -> None:
    channel, _room, _ = _channel()
    await channel.push_asset(b"%PDF", "application/pdf", "document")
    assert channel.state.blocks["photos"]["asset_ids"] == []


async def test_cite_sets_items_from_kb_hits() -> None:
    channel, room, events = _channel()
    hit = KbHit(chunk_id="c1", document_id="d1", filename="policy.pdf", score=0.91, text="Deductible is 500.")
    await channel.cite("sources", [hit])
    patch = UiPatch.model_validate_json(room.local_participant.sent_text[-1].text)
    assert [(op.op, op.path) for op in patch.ops] == [("set", "/blocks/sources/items")]
    # V5-08: the citation carries the hit's document id (the locators only when the hit has them).
    assert channel.state.blocks["sources"]["items"] == [
        {
            "chunk_id": "c1",
            "filename": "policy.pdf",
            "score": 0.91,
            "text": "Deductible is 500.",
            "document_id": "d1",
        }
    ]
    assert events[-1] == ("block_update", {"block_id": "sources", "block_type": "kb_citations", "op": "cite"})


# ------------------------------------------------------------------- forms


async def test_request_form_resolves_with_values_on_form_submit() -> None:
    channel, room, events = _channel()
    _ack(room)
    task = asyncio.create_task(channel.request_form("intake", SCHEMA, prefill={"policy_number": "P-"}))
    await _until(lambda: room.local_participant.rpc_calls)

    request = UiRequest.model_validate_json(room.local_participant.rpc_calls[-1].payload)
    assert request.method == "form"
    assert request.payload == {"block_id": "intake", "schema": SCHEMA, "prefill": {"policy_number": "P-"}}
    assert channel.state.blocks["intake"]["status"] == "requested"
    assert channel.state.blocks["intake"]["values"] == {"policy_number": "P-"}

    result = await _action(room, "form_submit", {"block_id": "intake", "values": {"policy_number": "P-42"}})
    assert result.ok is True
    assert await task == {"policy_number": "P-42"}

    submit = UiPatch.model_validate_json(room.local_participant.sent_text[-1].text)
    assert [op.path for op in submit.ops] == [
        "/blocks/intake/values",
        "/blocks/intake/status",
        "/blocks/intake/submitted_at",
    ]
    assert channel.state.blocks["intake"]["status"] == "submitted"
    assert isinstance(channel.state.blocks["intake"]["submitted_at"], float)
    assert ("form_submitted", {"block_id": "intake", "values": {"policy_number": "P-42"}}) in events


async def test_request_form_returns_none_after_timeout_and_keeps_requested() -> None:
    channel, room, _ = _channel()
    _ack(room)
    assert await channel.request_form("intake", SCHEMA, timeout_s=0.01) is None
    assert channel.state.blocks["intake"]["status"] == "requested"


async def test_request_form_is_cancelled_on_session_close() -> None:
    channel, room, _ = _channel()
    _ack(room)
    task = asyncio.create_task(channel.request_form("intake", SCHEMA))
    await _until(lambda: room.local_participant.rpc_calls)
    channel.close()
    assert await asyncio.wait_for(task, 1) is None
    assert await channel.request_form("intake", SCHEMA) is None  # closed: no new waits


async def test_request_form_accepts_values_in_the_rpc_result() -> None:
    channel, room, _ = _channel()
    _ack(room, {"values": {"policy_number": "P-7"}})
    assert await channel.request_form("intake", SCHEMA, timeout_s=1) == {"policy_number": "P-7"}
    assert channel.state.blocks["intake"]["status"] == "submitted"


async def test_request_form_cancelled_rpc_result_returns_none() -> None:
    channel, room, events = _channel()
    _ack(room, {"cancelled": True})
    assert await channel.request_form("intake", SCHEMA, timeout_s=1) is None
    assert channel.state.blocks["intake"]["status"] == "idle"
    assert events[-1][1]["op"] == "form_cancelled"


async def test_form_submit_cancelled_flag_releases_the_waiter() -> None:
    channel, room, _ = _channel()
    _ack(room)
    task = asyncio.create_task(channel.request_form("intake", SCHEMA))
    await _until(lambda: room.local_participant.rpc_calls)
    assert (await _action(room, "form_submit", {"block_id": "intake", "cancelled": True})).ok
    assert await task is None


async def test_request_form_survives_a_failed_form_rpc() -> None:
    """A browser without a `form` handler (or no browser yet): the block state still shows the form."""
    room = FakeRoom()  # no remote participant: request_ui raises
    channel = UiChannel(room, "sess-1")  # type: ignore[arg-type]
    channel.start()
    channel.init_blocks(L8_BLOCKS)
    task = asyncio.create_task(channel.request_form("intake", SCHEMA))
    await _until(lambda: channel.state.blocks["intake"]["status"] == "requested")
    await asyncio.sleep(0)
    assert not task.done()
    await _action(room, "form_submit", {"block_id": "intake", "values": {"policy_number": "X"}})
    assert await task == {"policy_number": "X"}


async def test_second_request_form_on_a_block_releases_the_first() -> None:
    channel, room, _ = _channel()
    _ack(room)
    first = asyncio.create_task(channel.request_form("intake", SCHEMA))
    await _until(lambda: room.local_participant.rpc_calls)
    second = asyncio.create_task(channel.request_form("intake", SCHEMA))
    assert await asyncio.wait_for(first, 1) is None
    await _action(room, "form_submit", {"block_id": "intake", "values": {"policy_number": "2"}})
    assert await second == {"policy_number": "2"}


async def test_late_form_submit_goes_to_the_unsolicited_handler() -> None:
    channel, room, events = _channel()
    late: list[tuple[str, dict[str, Any]]] = []

    async def on_late(block_id: str, values: dict[str, Any]) -> None:
        late.append((block_id, values))

    channel.bind(on_unsolicited_form=on_late)
    _ack(room)
    assert await channel.request_form("intake", SCHEMA, timeout_s=0.01) is None
    await _action(room, "form_submit", {"block_id": "intake", "values": {"policy_number": "late"}})
    assert late == [("intake", {"policy_number": "late"})]
    assert channel.state.blocks["intake"]["values"] == {"policy_number": "late"}
    assert events[-1][0] == "form_submitted"


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"block_id": "nope", "values": {}}, "unknown form block"),
        ({"block_id": "costs", "values": {}}, "is not a form"),
        ({"block_id": "intake", "values": "x"}, "values object"),
    ],
)
async def test_form_submit_rejects_bad_payloads(payload: dict[str, Any], error: str) -> None:
    _channel_, room, _ = _channel()
    result = await _action(room, "form_submit", payload)
    assert result.ok is False
    assert result.error is not None and error in result.error


# ----------------------------------------------------------- block actions


async def test_block_action_dispatches_to_the_bound_handler() -> None:
    channel, room, _ = _channel()
    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def handler(block_id: str, name: str, data: dict[str, Any]) -> dict[str, Any]:
        calls.append((block_id, name, data))
        return {"ack": name}

    channel.bind(on_block_action=handler)
    result = await _action(
        room, "block_action", {"block_id": "costs", "name": "select_row", "data": {"id": "r1"}}
    )
    assert result.ok is True
    assert result.payload == {"ack": "select_row"}
    assert calls == [("costs", "select_row", {"id": "r1"})]


async def test_block_action_without_handler_is_a_default_no_op() -> None:
    _ch, room, _ = _channel()
    result = await _action(room, "block_action", {"block_id": "costs", "name": "x"})
    assert result.ok is True
    assert result.payload == {}


async def test_block_action_needs_a_block_id() -> None:
    _ch, room, _ = _channel()
    assert (await _action(room, "block_action", {"name": "x"})).ok is False


@pytest.mark.parametrize("action", ["rewind", "inject_user_text"])
async def test_v2_18_actions_without_a_bound_handler_are_reported_unsupported(action: str) -> None:
    """V2-18 wires `rewind`/`inject_user_text` through `UiChannel.bind(on_text_action=...)`
    (only for `channel="text"` sessions, from `main.py`); with nothing bound — every other
    channel, and a text channel before `main.py`'s hook runs — both still fail cleanly.
    """
    _ch, room, _ = _channel()
    result = await _action(room, action, {})
    assert result.ok is False
    assert result.error is not None and "is not supported" in result.error


async def test_show_block_sends_a_show_block_request() -> None:
    channel, room, _ = _channel()
    _ack(room)
    channel.show_block("doc")
    await _until(lambda: room.local_participant.rpc_calls)
    request = UiRequest.model_validate_json(room.local_participant.rpc_calls[-1].payload)
    assert (request.method, request.payload) == ("show_block", {"block_id": "doc"})


def test_initial_block_states_is_keyed_by_block_id() -> None:
    assert list(initial_block_states(L8_BLOCKS)) == [s.id for s in L8_BLOCKS]


# ================================================================ V5-08: the block quartet


def test_quartet_initial_states_seed_from_config() -> None:
    choices = initial_block_state(
        BlockSpec(id="c", type="choices", config={"multi": True, "layout": "chips"})
    )
    assert choices == {
        "status": "idle",
        "submitted_at": None,
        "prompt": "",
        "options": [],
        "multi": True,
        "selected": [],
        "reveal": None,
    }
    details = initial_block_state(
        BlockSpec(
            id="d", type="details", config={"columns": 2, "fields": [{"key": "claim_no", "label": "Claim"}]}
        )
    )
    assert details == {
        "items": [
            {
                "key": "claim_no",
                "label": "Claim",
                "value": None,
                "type": "string",
                "tone": None,
                "updated_at": None,
            }
        ]
    }
    assert initial_block_state(BlockSpec(id="m", type="markdown", config={"max_chars": 500})) == {
        "markdown": "",
        "title": None,
        "updated_at": None,
    }
    steps = initial_block_state(
        BlockSpec(id="s", type="steps", config={"steps": [{"id": "a", "label": "A"}], "source": "flow"})
    )
    assert steps == {
        "steps": [{"id": "a", "label": "A", "status": "pending", "note": None, "at": None}],
        "current": None,
    }


def test_choices_state_is_validated_on_set_block() -> None:
    with pytest.raises(ValidationError):
        validate_block_state("choices", {"selected": "no"})
    with pytest.raises(ValidationError):
        validate_block_state("steps", {"steps": [{"id": "a", "label": "A", "status": "later"}]})


NODES = [("collect", "Collect"), ("photos", "Photos"), ("confirm", "Confirm")]


@pytest.mark.parametrize(
    ("path", "current", "finished", "statuses", "active"),
    [
        (["start", "collect"], "collect", False, ["active", "pending", "pending"], "collect"),
        (["start", "collect", "photos"], "photos", False, ["done", "active", "pending"], "photos"),
        (["start", "confirm"], "confirm", False, ["skipped", "skipped", "active"], "confirm"),
        (["start", "collect", "confirm", "done"], "done", True, ["done", "skipped", "done"], None),
        (["start"], "start", False, ["pending", "pending", "pending"], None),
    ],
)
def test_flow_steps_state_follows_the_path(
    path: list[str], current: str, finished: bool, statuses: list[str], active: str | None
) -> None:
    state = flow_steps_state({}, nodes=NODES, path=path, current=current, finished=finished)
    assert [s["status"] for s in state["steps"]] == statuses
    assert [s["label"] for s in state["steps"]] == ["Collect", "Photos", "Confirm"]
    assert state["current"] == active


def test_flow_steps_state_prefers_the_configured_steps() -> None:
    config = {"steps": [{"id": "confirm", "label": "Check it"}, {"id": "ghost", "label": "Not a node"}]}
    state = flow_steps_state(
        config, nodes=NODES, path=["start", "confirm"], current="confirm", finished=False
    )
    assert [(s["id"], s["label"], s["status"]) for s in state["steps"]] == [
        ("confirm", "Check it", "active"),
        ("ghost", "Not a node", "pending"),
    ]


# ------------------------------------------------------------- open_citation (E1)

CITATION_BLOCKS = [
    BlockSpec(id="sources", type="kb_citations", order=0),
    BlockSpec(id="doc", type="document", order=1),
]


def _hit(**meta: Any) -> KbHit:
    hit = KbHit(chunk_id="c1", document_id="d1", filename="policy.pdf", score=0.9, text="Fire is covered.")
    if meta:
        object.__setattr__(hit, "meta", meta)  # V5-04 adds `KbHit.meta`; until then a stand-in
    return hit


async def test_cite_carries_the_hit_locators() -> None:
    channel, _room, _events = _channel(CITATION_BLOCKS)
    await channel.cite("sources", [_hit(page=3, heading_path=["Cover", "Fire"], char_start=10, char_end=40)])
    assert channel.state.blocks["sources"]["items"] == [
        {
            "chunk_id": "c1",
            "filename": "policy.pdf",
            "score": 0.9,
            "text": "Fire is covered.",
            "document_id": "d1",
            "page": 3,
            "heading_path": ["Cover", "Fire"],
            "char_start": 10,
            "char_end": 40,
        }
    ]


async def test_open_citation_shows_the_cited_page_in_the_document_block() -> None:
    pack_actions: list[str] = []

    async def _pack(block_id: str, name: str, data: dict[str, Any]) -> dict[str, Any]:
        pack_actions.append(name)
        return {}

    channel, room, _events = _channel(CITATION_BLOCKS, on_block_action=_pack)
    _ack(room)
    await channel.cite("sources", [_hit(page=3, heading_path=["Cover", "Fire"])])
    asset_id = await channel.push_asset(b"%PDF", "application/pdf", "document", meta={"document_id": "d1"})

    result = await _action(
        room, "block_action", {"block_id": "sources", "name": "open_citation", "data": {"chunk_id": "c1"}}
    )
    assert result.ok and result.payload == {"opened": "document", "block_id": "doc", "page": 3}
    assert channel.state.blocks["doc"] == {
        "asset_id": asset_id,
        "url": None,
        "page": 3,
        "highlights": [{"page": 3, "bbox": [0.0, 0.0, 1.0, 1.0], "note": "Cover › Fire"}],
    }
    await _until(
        lambda: any(
            UiRequest.model_validate_json(c.payload).method == "show_block"
            for c in room.local_participant.rpc_calls
        )
    )
    assert pack_actions == []  # the platform handles it; packs never see open_citation


async def test_open_citation_without_a_source_returns_the_citation_for_a_preview() -> None:
    channel, room, _events = _channel(CITATION_BLOCKS)
    await channel.cite("sources", [_hit(page=2)])
    result = await _action(
        room, "block_action", {"block_id": "sources", "name": "open_citation", "data": {"index": 0}}
    )
    assert result.payload["opened"] is False and result.payload["reason"] == "no_source"
    assert result.payload["citation"]["page"] == 2
    assert channel.state.blocks["doc"]["asset_id"] is None


async def test_open_citation_without_a_document_block_or_citation() -> None:
    channel, room, _events = _channel([BlockSpec(id="sources", type="kb_citations")])
    await channel.cite("sources", [_hit()])
    no_doc = await _action(
        room, "block_action", {"block_id": "sources", "name": "open_citation", "data": {"chunk_id": "c1"}}
    )
    assert no_doc.payload["reason"] == "no_document_block"
    unknown = await _action(
        room, "block_action", {"block_id": "sources", "name": "open_citation", "data": {"chunk_id": "zz"}}
    )
    assert unknown.payload == {"opened": False, "reason": "unknown_citation"}


# ---------------------------------------------------------------- submit_block


async def test_submit_block_refuses_unknown_and_non_requestable_blocks() -> None:
    channel, _room, _events = _channel()
    with pytest.raises(ValueError, match="unknown block"):
        await channel.submit_block("nope", {})
    with pytest.raises(ValueError, match="cannot be submitted"):
        await channel.submit_block("costs", {})


async def test_a_choices_answer_that_is_not_an_option_is_not_stored() -> None:
    channel, room, _events = _channel([BlockSpec(id="pick", type="choices")])
    await channel.patch_block(
        "pick", [UiPatchOp(op="set", path="/options", value=[{"id": "no", "label": "No"}])]
    )
    result = await _action(room, "block_submit", {"block_id": "pick", "values": {"selected": ["maybe"]}})
    assert result.ok
    assert channel.state.blocks["pick"]["selected"] == []
    assert channel.state.blocks["pick"]["status"] == "submitted"
