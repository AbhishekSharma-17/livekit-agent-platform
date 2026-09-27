"""The AG-UI state adapter (V5-43): LKAP ops ⇄ RFC 6902 ``STATE_DELTA`` operations."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from lkap_contracts.ui_agui import (
    AguiJsonPatchOp,
    AguiPatchError,
    AguiStateDeltaEvent,
    agui_delta_to_patch,
    apply_json_patch,
    apply_ui_ops,
    decode_pointer,
    encode_pointer,
    patch_to_agui,
    patch_to_agui_delta,
    snapshot_to_agui,
)
from lkap_contracts.ui_protocol import ACTIVITY_RING_SIZE, UiPatch, UiPatchOp, UiSnapshot, UiState

FIXTURES = Path(__file__).resolve().parents[2] / "web" / "src" / "panels" / "blocks" / "__fixtures__"


def _fixture_state() -> dict[str, Any]:
    """The web fixture layout's blocks (every block type) in a plain-JSON `UiState`."""
    layout = json.loads((FIXTURES / "layout.json").read_text())
    by_type: dict[str, str] = {}
    for path in FIXTURES.glob("*.json"):
        if path.stem not in ("layout", "form.submitted"):
            by_type[path.stem.split(".")[0]] = path.name
    blocks: dict[str, Any] = {}
    for spec in layout["blocks"]:
        name = by_type.get(spec["type"])
        blocks[spec["id"]] = json.loads((FIXTURES / name).read_text()) if name else {}
    state = UiState(blocks=blocks, notes=[{"id": "n1", "text": "Called in", "ts": 1.0}])
    return state.model_dump(mode="json", by_alias=True)


def _op(op: str, path: str, value: Any = None, key: str | None = None) -> UiPatchOp:
    return UiPatchOp(op=op, path=path, value=value, key=key)  # type: ignore[arg-type]


#: LKAP ops on the fixture layout, one per row of the documented mapping.
LKAP_OPS: list[list[UiPatchOp]] = [
    [_op("set", "/blocks/recap/markdown", "**Done.**")],
    [_op("set", "/blocks/plans/cards/0/title", "Silver plus")],
    [_op("set", "/blocks/plans/selected", "silver")],
    [_op("set", "/blocks/pack/new/deep", 3)],
    [_op("append", "/blocks/items/rows", {"id": "r9", "item": "Kettle"})],
    [_op("append", "/blocks/pack/list", 1)],
    [_op("remove", "/blocks/plans/selected")],
    [_op("remove", "/blocks/plans/nothing_here")],
    [_op("remove", "/blocks/plans/cards", key="silver")],
    [
        _op(
            "upsert",
            "/blocks/claim/items",
            {"key": "policy", "label": "Policy", "value": "H0-1"},
            key="policy",
        )
    ],
    [_op("upsert", "/blocks/claim/items", {"key": "brand_new", "label": "New", "value": "x"})],
    [_op("upsert", "/notes", {"id": "n1", "text": "Called in again", "ts": 2.0})],
    [_op("set", "/status", {"label": "Open", "tone": "info", "key": None})],
    [
        _op("set", "/blocks/recap/markdown", "one"),
        _op("append", "/blocks/items/rows", {"id": "r10"}),
        _op("remove", "/blocks/items/rows", key="r10"),
    ],
]


@pytest.mark.parametrize("ops", LKAP_OPS)
def test_patch_to_agui_delta_matches_the_lkap_reducer(ops: list[UiPatchOp]) -> None:
    state = _fixture_state()
    delta = patch_to_agui_delta(ops, state)
    assert apply_json_patch(state, delta) == apply_ui_ops(state, ops)


@pytest.mark.parametrize("ops", LKAP_OPS)
def test_lkap_ops_round_trip_through_agui(ops: list[UiPatchOp]) -> None:
    """LKAP → STATE_DELTA → LKAP gives the same state (when every path is under /blocks)."""
    state = _fixture_state()
    if not all(op.path.startswith("/blocks/") for op in ops):
        pytest.skip("envelope paths are outside the inbound /blocks prefix")
    delta = patch_to_agui_delta(ops, state)
    back = agui_delta_to_patch([d.wire() for d in delta], state)
    assert apply_ui_ops(state, back) == apply_ui_ops(state, ops)


def test_the_documented_mapping_rows() -> None:
    state = _fixture_state()
    rows = {
        "set member": patch_to_agui_delta([_op("set", "/blocks/recap/markdown", "x")], state),
        "set index": patch_to_agui_delta([_op("set", "/blocks/items/rows/0", {"id": "r0"})], state),
        "append": patch_to_agui_delta([_op("append", "/blocks/items/rows", {"id": "z"})], state),
        "keyed remove": patch_to_agui_delta([_op("remove", "/blocks/plans/cards", key="gold")], state),
        "upsert hit": patch_to_agui_delta(
            [_op("upsert", "/blocks/plans/cards", {"id": "gold", "title": "G"})], state
        ),
    }
    assert [d.wire() for d in rows["set member"]] == [
        {"op": "add", "path": "/blocks/recap/markdown", "value": "x"}
    ]
    assert rows["set index"][0].op == "replace"
    assert rows["append"][0].path == "/blocks/items/rows/-"
    assert [d.wire() for d in rows["keyed remove"]] == [{"op": "remove", "path": "/blocks/plans/cards/1"}]
    assert rows["upsert hit"][0].wire()["path"] == "/blocks/plans/cards/1"


def test_a_missing_list_is_added_whole() -> None:
    delta = patch_to_agui_delta([_op("append", "/custom/log", "a")], UiState())
    assert [d.wire() for d in delta] == [{"op": "add", "path": "/custom/log", "value": ["a"]}]


def test_activity_overflow_is_spelled_out() -> None:
    rows = [
        {
            "v": 1,
            "id": f"a{i}",
            "ts": float(i),
            "source": "tool",
            "label": "x",
            "phase": "done",
            "headline": "x",
        }
        for i in range(ACTIVITY_RING_SIZE)
    ]
    state = UiState.model_validate({"activity": rows})
    op = _op("append", "/activity", {**rows[0], "id": "new"})
    delta = patch_to_agui_delta([op], state)
    assert delta[-1].wire() == {"op": "remove", "path": "/activity/0"}
    dumped = state.model_dump(mode="json", by_alias=True)
    assert apply_json_patch(dumped, delta) == apply_ui_ops(dumped, [op])


#: AG-UI deltas on the fixture layout, including the operations LKAP has no op for.
AGUI_DELTAS: list[list[dict[str, Any]]] = [
    [{"op": "replace", "path": "/blocks/recap/markdown", "value": "hi"}],
    [{"op": "add", "path": "/blocks/recap/title", "value": "Recap"}],
    [{"op": "add", "path": "/blocks/items/rows/-", "value": {"id": "r3"}}],
    [{"op": "add", "path": "/blocks/items/rows/0", "value": {"id": "first"}}],
    [{"op": "remove", "path": "/blocks/plans/cards/0"}],
    [{"op": "replace", "path": "/blocks/plans/cards/0/title", "value": "Bronze"}],
    [{"op": "move", "from": "/blocks/plans/cards/1", "path": "/blocks/plans/cards/0"}],
    [{"op": "copy", "from": "/blocks/recap/markdown", "path": "/blocks/claim/note"}],
    [
        {"op": "test", "path": "/blocks/plans/selected", "value": "gold"},
        {"op": "remove", "path": "/blocks/plans/selected"},
    ],
    [{"op": "add", "path": "/blocks/pack/tilde~0", "value": 1}],
]


@pytest.mark.parametrize("delta", AGUI_DELTAS)
def test_agui_delta_to_patch_matches_rfc_6902(delta: list[dict[str, Any]]) -> None:
    state = _fixture_state()
    ops = agui_delta_to_patch(delta, state)
    assert apply_ui_ops(state, ops) == apply_json_patch(state, delta)


@pytest.mark.parametrize("delta", AGUI_DELTAS)
def test_a_state_delta_round_trips_to_ui_patch_ops_and_back(delta: list[dict[str, Any]]) -> None:
    """The card's acceptance: STATE_DELTA → UiPatch ops → STATE_DELTA gives the same state."""
    state = _fixture_state()
    ops = agui_delta_to_patch(delta, state)
    UiPatch(seq=2, session_id="s", ops=ops)  # the ops are a valid patch
    again = patch_to_agui_delta(ops, state)
    assert apply_json_patch(state, again) == apply_json_patch(state, delta)


@pytest.mark.parametrize(
    ("delta", "message"),
    [
        ([{"op": "test", "path": "/blocks/plans/selected", "value": "silver"}], "test failed"),
        ([{"op": "replace", "path": "/blocks/recap/missing", "value": 1}], "does not exist"),
        ([{"op": "remove", "path": "/blocks/items/rows/9"}], "past the end"),
        ([{"op": "add", "path": "/status", "value": {"label": "x"}}], "outside /blocks/<id>"),
        ([{"op": "replace", "path": "/blocks", "value": {}}], "outside /blocks/<id>"),
        ([{"op": "copy", "from": "/custom", "path": "/blocks/x"}], "outside /blocks/<id>"),
        ([{"op": "add", "path": "/blocks/pack/a~1b", "value": 1}], "cannot be written"),
        ([{"op": "add", "path": "blocks/x", "value": 1}], "not a JSON Pointer"),
        ([{"op": "add", "path": "/blocks/x~2", "value": 1}], "invalid '~' escape"),
        ([{"op": "shout", "path": "/blocks/x"}], "not a JSON Patch operation"),
    ],
)
def test_a_bad_delta_is_refused_whole(delta: list[dict[str, Any]], message: str) -> None:
    state = _fixture_state()
    before = copy.deepcopy(state)
    with pytest.raises(AguiPatchError, match=message):
        agui_delta_to_patch(
            [{"op": "replace", "path": "/blocks/recap/markdown", "value": "x"}, *delta], state
        )
    assert state == before


def test_pointer_escapes() -> None:
    assert encode_pointer(["blocks", "a/b", "c~d"]) == "/blocks/a~1b/c~0d"
    assert decode_pointer("/blocks/a~1b/c~0d") == ["blocks", "a/b", "c~d"]
    assert decode_pointer("") == []


def test_events_carry_the_agui_wire_types() -> None:
    state = UiState(blocks={"recap": {"markdown": "a"}})
    snapshot = snapshot_to_agui(UiSnapshot(seq=1, session_id="s", state=state))
    assert snapshot.model_dump()["type"] == "STATE_SNAPSHOT"
    assert snapshot.snapshot["blocks"] == {"recap": {"markdown": "a"}}
    event = patch_to_agui(
        UiPatch(seq=2, session_id="s", ops=[_op("set", "/blocks/recap/markdown", "b")]), state
    )
    assert event.type == "STATE_DELTA"
    parsed = AguiStateDeltaEvent.model_validate(
        {"type": "STATE_DELTA", "delta": [{"op": "move", "from": "/a", "path": "/b"}]}
    )
    assert parsed.delta[0].wire() == {"op": "move", "path": "/b", "from": "/a"}
    assert AguiJsonPatchOp(op="remove", path="/x").wire() == {"op": "remove", "path": "/x"}
