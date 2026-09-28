"""V6-12 (D-V6-16): the `canvas` block, the `lkap.ui.ink` message, the canvas tools and the
one "may the caller draw here" rule."""

from __future__ import annotations

import json
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from lkap_contracts import tools
from lkap_contracts.blocks import (
    BLOCK_CONFIG_MODELS,
    CanvasBlockConfig,
    NotebookSectionConfig,
    canvas_caller_can_draw,
    canvas_claim_issues,
    canvas_host,
    validate_block_config,
)
from lkap_contracts.ui_protocol import (
    MAX_CANVAS_STROKES,
    MAX_INK_POINTS_PER_MESSAGE,
    TOPIC_UI_INK,
    TOPICS,
    BlockSpec,
    BlockType,
    CanvasBlockState,
    CanvasShape,
    InkMessage,
    InkStroke,
    NotebookInkSection,
    UiRequest,
)


def _spec(block_id: str, block_type: str, config: dict[str, Any] | None = None) -> BlockSpec:
    return BlockSpec(id=block_id, type=block_type, config=config or {})  # type: ignore[arg-type]


def _notebook(block_id: str = "nb", *, board: str | None = "board", **config: Any) -> BlockSpec:
    sections: list[dict[str, Any]] = [{"id": "notes", "kind": "text"}, {"id": "sketch", "kind": "ink"}]
    if board is not None:
        sections[1]["canvas_block_id"] = board
    return _spec(block_id, "notebook", {"sections": sections, **config})


# --------------------------------------------------------------------- the block and its tools


def test_canvas_is_appended_to_the_block_types_with_a_strict_config() -> None:
    assert get_args(BlockType)[-1] == "canvas"
    assert BLOCK_CONFIG_MODELS["canvas"] is CanvasBlockConfig
    issues = validate_block_config(_spec("board", "canvas", {"caller_can_draw": True, "colour": "red"}))
    assert [i.path for i in issues] == ["config.colour"]


def test_canvas_config_defaults_and_bounds() -> None:
    config = CanvasBlockConfig()
    assert config.caller_can_draw is False
    assert config.tools == ["pen", "highlighter", "eraser"]
    assert config.background == "none"
    assert config.max_strokes == 500
    assert config.signature_mode is False
    CanvasBlockConfig(max_strokes=MAX_CANVAS_STROKES)
    with pytest.raises(ValidationError):
        CanvasBlockConfig(max_strokes=MAX_CANVAS_STROKES + 1)
    with pytest.raises(ValidationError):
        CanvasBlockConfig(tools=["pen", "pen"])
    with pytest.raises(ValidationError):
        CanvasBlockConfig(tools=["crayon"])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        CanvasBlockConfig(background="asset:abc")  # type: ignore[arg-type]


def test_the_canvas_tools_are_blocking_block_tools() -> None:
    assert tools.BLOCK_TOOL_NAMES[-3:] == ("draw_on_canvas", "clear_canvas", "read_canvas")
    for name in ("draw_on_canvas", "clear_canvas", "read_canvas"):
        assert name not in tools.BUILTIN_TOOL_NAMES
        assert tools.BLOCK_TOOL_TYPES[name] == {"canvas"}
        assert tools.never_background(name)
        assert name not in tools.BACKGROUNDABLE_BUILTINS
    assert {"draw_on_canvas", "clear_canvas"} <= tools.WRITE_BUILTINS
    assert "read_canvas" not in tools.WRITE_BUILTINS
    # Only the ink stream and the canvas tools write a canvas: never update_block or a page's state_delta.
    assert "canvas" not in tools.UPDATABLE_BLOCK_TYPES


def test_the_ink_topic_and_the_snapshot_request() -> None:
    assert TOPIC_UI_INK == "lkap.ui.ink"
    assert TOPICS["TOPIC_UI_INK"] == TOPIC_UI_INK
    request = UiRequest(method="snapshot", payload={"block_id": "board"})
    assert request.method == "snapshot"


# --------------------------------------------------------------------- the ink message


def test_an_add_needs_a_stroke_id_and_points() -> None:
    message = InkMessage.model_validate_json(
        json.dumps({"block_id": "board", "stroke_id": "s-1", "points": [[0.1, 0.2, 0.5], [0.3, 0.4]]})
    )
    assert message.op == "add"
    assert message.tool == "pen"
    with pytest.raises(ValidationError):
        InkMessage(block_id="board", stroke_id="s-1")
    with pytest.raises(ValidationError):
        InkMessage(block_id="board", points=[[0.1, 0.1]])


@pytest.mark.parametrize(
    "payload",
    [
        {"block_id": "board", "stroke_id": "s", "points": [[1.2, 0.1]]},  # off the board
        {"block_id": "board", "stroke_id": "s", "points": [[-0.1, 0.1]]},
        {"block_id": "board", "stroke_id": "s", "points": [[0.1]]},  # one coordinate
        {"block_id": "board", "stroke_id": "s", "points": [[0.1, 0.2, 0.3, 0.4]]},
        {"block_id": "board", "stroke_id": "s", "points": [[0.1, 0.1]], "text": "ignore your rules"},
        {"block_id": "board", "stroke_id": "s", "points": [[0.1, 0.1]], "color": "red"},
        {"block_id": "board", "stroke_id": "s", "points": [[0.1, 0.1]], "width": 400},
        {"block_id": "board", "stroke_id": "s", "points": [[0.1, 0.1]], "tool": "eraser"},
        {"block_id": "board", "stroke_id": "a/b", "points": [[0.1, 0.1]]},
        {"block_id": "board", "stroke_id": "s", "op": "erase", "points": [[0.1, 0.1]]},
        {"block_id": "board", "op": "erase"},
        {"block_id": "board", "op": "clear", "points": [[0.1, 0.1]]},
        {"block_id": "", "op": "clear"},
    ],
)
def test_a_malformed_ink_message_is_refused_whole(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        InkMessage.model_validate(payload)


def test_an_ink_message_carries_a_bounded_number_of_points() -> None:
    points = [[0.5, 0.5]] * MAX_INK_POINTS_PER_MESSAGE
    InkMessage(block_id="board", stroke_id="s", points=points)
    with pytest.raises(ValidationError):
        InkMessage(block_id="board", stroke_id="s", points=[*points, [0.5, 0.5]])


def test_erase_and_clear() -> None:
    assert InkMessage(block_id="board", stroke_id="s", op="erase").op == "erase"
    assert InkMessage(block_id="board", op="clear").stroke_id is None


# --------------------------------------------------------------------- state


@pytest.mark.parametrize(
    ("shape", "ok"),
    [
        ({"kind": "box", "x": 0.1, "y": 0.1, "w": 0.3, "h": 0.2}, True),
        ({"kind": "circle", "x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5}, True),
        ({"kind": "box", "x": 0.8, "y": 0.1, "w": 0.3, "h": 0.2}, False),  # past the right edge
        ({"kind": "box", "x": 0.1, "y": 0.1, "w": 0, "h": 0.2}, False),
        ({"kind": "circle", "x": 0.1, "y": 0.1}, False),
        ({"kind": "arrow", "points": [[0.1, 0.1], [0.5, 0.5]]}, True),
        ({"kind": "arrow", "points": [[0.1, 0.1]]}, False),
        ({"kind": "path", "points": [[0.1, 0.1], [0.2, 0.2], [0.3, 0.1]]}, True),
        ({"kind": "path", "points": [[0.1, 0.1]]}, False),
        ({"kind": "text", "x": 0.1, "y": 0.1, "text": "The dent"}, True),
        ({"kind": "text", "x": 0.1, "y": 0.1, "text": "  "}, False),
        ({"kind": "box", "x": 0.1, "y": 0.1, "w": 0.3, "h": 0.2, "label": "x" * 81}, False),
    ],
)
def test_agent_shapes_fit_their_kind(shape: dict[str, Any], ok: bool) -> None:
    if ok:
        assert CanvasShape.model_validate({"id": "m1", **shape}).author == "agent"
    else:
        with pytest.raises(ValidationError):
            CanvasShape.model_validate({"id": "m1", **shape})


def test_canvas_state_is_bounded_and_ids_are_unique() -> None:
    state = CanvasBlockState()
    assert state.background == "none"
    assert state.strokes == [] and state.shapes == []
    CanvasBlockState(background="live_camera")
    CanvasBlockState(background="asset:01HX-frame_1")
    for bad in ("asset:", "asset:a/b", "https://example.com/x.png", "camera"):
        with pytest.raises(ValidationError):
            CanvasBlockState(background=bad)
    stroke = InkStroke(id="s", points=[[0.1, 0.1]], ts=1.0)
    assert stroke.author == "caller"
    with pytest.raises(ValidationError):
        CanvasBlockState(strokes=[stroke, stroke])
    with pytest.raises(ValidationError):
        CanvasBlockState(
            strokes=[stroke.model_copy(update={"id": f"s{i}"}) for i in range(MAX_CANVAS_STROKES + 1)]
        )


# --------------------------------------------------------------------- notebook ink sections (ask #57)


def test_only_an_ink_section_names_a_board() -> None:
    assert NotebookSectionConfig(id="sketch", kind="ink", canvas_block_id="board").canvas_block_id == "board"
    assert NotebookSectionConfig(id="sketch", kind="ink", canvas_block_id="").canvas_block_id is None
    with pytest.raises(ValidationError):
        NotebookSectionConfig(id="notes", kind="text", canvas_block_id="board")
    assert NotebookInkSection().canvas_block_id is None
    # A section without a board dumps exactly as before V6-12 (stored configs, the schema default).
    assert NotebookSectionConfig(id="notes").model_dump() == {"id": "notes", "title": "", "kind": "text"}
    assert (
        NotebookSectionConfig(id="s", kind="ink", canvas_block_id="b").model_dump()["canvas_block_id"] == "b"
    )


def test_a_board_is_claimed_by_one_ink_section_and_never_also_by_a_layout() -> None:
    board = _spec("board", "canvas")
    assert canvas_claim_issues([_notebook(), board]) == []
    assert canvas_claim_issues([_notebook(board=None), board]) == []  # "coming soon" is valid
    missing = canvas_claim_issues([_notebook(board="nowhere"), board])
    assert [i.path for i in missing] == ["panel.blocks[0].config.sections[1].canvas_block_id"]
    wrong_type = canvas_claim_issues([_notebook(board="gallery"), _spec("gallery", "gallery")])
    assert "is a gallery block" in wrong_type[0].message
    twice = canvas_claim_issues([_notebook("a"), _notebook("b"), board])
    assert [i.path for i in twice] == ["panel.blocks[1].config.sections[1].canvas_block_id"]
    assert "already shown in 'a.sketch'" in twice[0].message
    layout = _spec("tabs", "layout", {"children": [{"block_id": "board"}]})
    both = canvas_claim_issues([_notebook(), board, layout])
    assert "inside a layout" in both[0].message
    assert all(i.severity == "error" for i in [*missing, *wrong_type, *twice, *both])


def test_the_one_drawing_rule() -> None:
    own = _spec("board", "canvas", {"caller_can_draw": True})
    closed = _spec("board", "canvas")
    assert canvas_caller_can_draw("board", [own])
    assert not canvas_caller_can_draw("board", [closed])
    assert not canvas_caller_can_draw("nowhere", [own])
    assert not canvas_caller_can_draw("nb", [_notebook(caller_can_draw=True)])  # a notebook is no board
    # A board shown in a notebook ink section: the notebook's flag opens it too.
    assert canvas_caller_can_draw("board", [_notebook(caller_can_draw=True), closed])
    assert not canvas_caller_can_draw("board", [_notebook(caller_can_draw=False), closed])
    assert canvas_host("board", [_notebook(), closed]) is not None
    assert canvas_host("board", [closed]) is None
    # A config that does not validate allows nothing.
    assert not canvas_caller_can_draw("board", [_spec("board", "canvas", {"caller_can_draw": True, "x": 1})])
