"""Self-tests for the shared fakes (REVIEW-FINAL F-18; asks #67 block helpers)."""

from __future__ import annotations

from lkap_contracts.api_models import KbHit
from lkap_contracts.ui_protocol import UiPatchOp, UiState

from lkap_testing import (
    FakeLogger,
    FakePackSessionContext,
    FakeUiChannel,
    apply_patch_op,
    default_agent_config,
)


def test_context_defaults_are_offline_fakes() -> None:
    ctx = FakePackSessionContext()
    assert ctx.room is None
    assert isinstance(ctx.log, FakeLogger)
    assert ctx.config == default_agent_config()
    assert ctx.image_gen is not None
    assert FakePackSessionContext(image_gen=None).image_gen is None


def test_context_takes_an_injected_logger_and_room() -> None:
    sentinel_log = FakeLogger()
    room = object()
    ctx = FakePackSessionContext(log=sentinel_log, room=room)
    assert ctx.log is sentinel_log
    assert ctx.room is room


def test_record_event_captures_session_events() -> None:
    ctx = FakePackSessionContext()
    ctx.record_event("custom", {"a": 1})
    assert ctx.events == [("custom", {"a": 1})]


async def test_set_block_and_patch_block_write_the_blocks_tree() -> None:
    ui = FakeUiChannel()
    await ui.set_block("items", {"columns": [], "rows": [], "selected_row": None})
    await ui.patch_block(
        "items",
        [
            UiPatchOp(op="append", path="/rows", value={"id": "r1", "name": "TV"}),
            UiPatchOp(op="upsert", path="rows", value={"id": "r1", "name": "Television"}),
            UiPatchOp(op="append", path="/rows", value={"id": "r2", "name": "Sofa"}),
            UiPatchOp(op="set", path="/rows/1/name", value="Couch"),
        ],
    )
    assert ui.state.blocks["items"]["rows"] == [
        {"id": "r1", "name": "Television"},
        {"id": "r2", "name": "Couch"},
    ]
    await ui.patch_block("items", [UiPatchOp(op="remove", path="/rows", key="r1")])
    assert [r["id"] for r in ui.state.blocks["items"]["rows"]] == ["r2"]
    assert ui.block_calls == [("set_block", "items"), ("patch_block", "items"), ("patch_block", "items")]


async def test_request_form_sets_the_requested_state_and_returns_the_configured_values() -> None:
    ui = FakeUiChannel()
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    assert await ui.request_form("details", schema, prefill={"name": "Jo"}) is None
    assert ui.state.blocks["details"] == {
        "schema": schema,
        "values": {"name": "Jo"},
        "status": "requested",
        "submitted_at": None,
    }
    ui.form_responses["details"] = {"name": "Jordan"}
    assert await ui.request_form("details", schema) == {"name": "Jordan"}
    assert [r[0] for r in ui.form_requests] == ["details", "details"]


async def test_cite_replaces_the_items_as_plain_json() -> None:
    ui = FakeUiChannel()
    hits = [KbHit(chunk_id="c1", document_id="d", filename="a.md", score=0.9, text="x")]
    await ui.cite("sources", hits)
    await ui.cite("sources", hits[:1])
    assert ui.state.blocks["sources"]["items"] == [
        {"chunk_id": "c1", "filename": "a.md", "score": 0.9, "text": "x"}
    ]


def test_apply_patch_op_envelope_fields_and_activity_ring() -> None:
    state = UiState()
    apply_patch_op(state, UiPatchOp(op="set", path="/status", value={"label": "OK", "tone": "info"}))
    apply_patch_op(state, UiPatchOp(op="set", path="/custom/a/b", value=1))
    apply_patch_op(state, UiPatchOp(op="set", path="/blocks", value={"x": {"k": 1}}))
    assert state.status is not None and state.status.label == "OK"
    assert state.custom == {"a": {"b": 1}}
    assert state.blocks == {"x": {"k": 1}}
    apply_patch_op(state, UiPatchOp(op="remove", path="/blocks/x/k"))
    assert state.blocks == {"x": {}}
