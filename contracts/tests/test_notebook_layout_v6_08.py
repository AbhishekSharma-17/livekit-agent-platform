"""V6-08 contracts: the ``notebook`` and ``layout`` blocks, their tools and the Notebook preset."""

from __future__ import annotations

from typing import Any, get_args

import pytest
from pydantic import ValidationError

from lkap_contracts import tools
from lkap_contracts.agent_config import (
    NOTEBOOK_PRESET,
    NOTEBOOK_PRESET_ID,
    PANEL_PRESETS,
    AgentConfig,
    PanelLayout,
    PanelPresetsResponse,
    panel_preset,
)
from lkap_contracts.blocks import (
    BLOCK_CONFIG_MODELS,
    MAX_LAYOUT_CHILDREN,
    CanvasBlockConfig,
    LayoutBlockConfig,
    NotebookBlockConfig,
    canvas_caller_can_draw,
    canvas_claim_issues,
    layout_issues,
    validate_block_config,
    validate_panel_block_configs,
)
from lkap_contracts.ui_protocol import (
    CALLER_EDIT_FLAGS,
    EDITABLE_BLOCK_TYPES,
    MAX_CALLER_EDIT_CHARS,
    MAX_NOTEBOOK_ENTRIES,
    MAX_NOTEBOOK_ENTRY_CHARS,
    MAX_NOTEBOOK_ITEMS,
    MAX_NOTEBOOK_SECTIONS,
    BlockSpec,
    BlockType,
    NotebookBlockState,
    NotebookEdit,
)


def _spec(block_id: str, block_type: str, config: dict[str, Any] | None = None) -> BlockSpec:
    return BlockSpec(id=block_id, type=block_type, config=config or {})  # type: ignore[arg-type]


# --------------------------------------------------------------------- block types and tools


def test_the_two_block_types_are_appended() -> None:
    # V6-12 appends `canvas` after them, V6-23 its five blocks.
    assert get_args(BlockType)[-8:-6] == ("notebook", "layout")
    assert BLOCK_CONFIG_MODELS["notebook"] is NotebookBlockConfig
    assert BLOCK_CONFIG_MODELS["layout"] is LayoutBlockConfig


def test_the_notebook_tools_are_block_writes_that_never_run_in_the_background() -> None:
    # V6-12 appends the three canvas tools after them, V6-23 its five tools.
    assert tools.BLOCK_TOOL_NAMES[-10:-8] == ("notebook_write", "notebook_check")
    for name in ("notebook_write", "notebook_check"):
        assert name not in tools.BUILTIN_TOOL_NAMES
        assert tools.BLOCK_TOOL_TYPES[name] == {"notebook"}
        assert name in tools.WRITE_BUILTINS
        assert tools.never_background(name)
        assert name not in tools.BACKGROUNDABLE_BUILTINS
    document = tools.builtin_tools_document()
    assert document["block_tool_types"]["notebook_write"] == ["notebook"]


def test_neither_block_is_written_by_update_block_or_a_state_delta() -> None:
    assert "notebook" not in tools.UPDATABLE_BLOCK_TYPES
    assert "layout" not in tools.UPDATABLE_BLOCK_TYPES
    # describe_panel reads every block, the two new ones included.
    assert {"notebook", "layout"} <= tools.BLOCK_TOOL_TYPES["describe_panel"]


# --------------------------------------------------------------------- notebook config


def test_a_notebook_with_no_config_gets_one_notes_section() -> None:
    config = NotebookBlockConfig()
    assert [(s.id, s.title, s.kind) for s in config.sections] == [("notes", "Notes", "text")]
    assert (config.paper, config.font, config.caller_can_write, config.caller_can_draw) == (
        "ruled",
        "print",
        False,
        False,
    )
    assert validate_block_config(_spec("nb", "notebook")) == []


@pytest.mark.parametrize(
    ("config", "where"),
    [
        ({"sections": []}, "sections"),
        ({"sections": [{"id": "a"}, {"id": "a", "kind": "checklist"}]}, "sections"),
        ({"sections": [{"id": "has space"}]}, "sections[0].id"),
        ({"sections": [{"id": "a", "kind": "table"}]}, "sections[0].kind"),
        ({"sections": [{"id": "a", "caller_can_edit": True}]}, "sections[0].caller_can_edit"),
        ({"sections": [{"id": f"s{i}"} for i in range(MAX_NOTEBOOK_SECTIONS + 1)]}, "sections"),
        ({"paper": "papyrus"}, "paper"),
        ({"font": "comic"}, "font"),
        ({"caller_can_edit": True}, "caller_can_edit"),
    ],
)
def test_a_bad_notebook_config_is_an_addressable_error(config: dict[str, Any], where: str) -> None:
    issues = validate_block_config(_spec("nb", "notebook", config), path="panel.blocks[0].config")
    assert issues, config
    assert issues[0].path == f"panel.blocks[0].config.{where}"


def test_the_notebook_uses_its_own_edit_flag() -> None:
    assert "notebook" in EDITABLE_BLOCK_TYPES
    assert CALLER_EDIT_FLAGS == {
        "details": "caller_can_edit",
        "checklist": "caller_can_edit",
        "notebook": "caller_can_write",
    }
    assert set(CALLER_EDIT_FLAGS) == EDITABLE_BLOCK_TYPES


# --------------------------------------------------------------------- notebook state


def _full_state() -> dict[str, Any]:
    return {
        "sections": {
            "notes": {
                "kind": "text",
                "entries": [
                    {"id": "e1", "text": "Water came through the ceiling", "ts": 1.0},
                    {"id": "e2", "text": "Mine too", "author": "caller", "key": "extra", "ts": 2.0},
                ],
            },
            "still_needed": {"kind": "checklist", "items": [{"id": "photo", "label": "A photo"}]},
            "summary": {"kind": "details", "items": [{"key": "claim_no", "label": "Claim", "value": "C-1"}]},
            "sketch": {"kind": "ink"},
        },
        "updated_at": 3.0,
    }


def test_a_notebook_state_round_trips() -> None:
    state = NotebookBlockState.model_validate(_full_state())
    dumped = state.model_dump(mode="json")
    assert list(dumped["sections"]) == ["notes", "still_needed", "summary", "sketch"]
    assert dumped["sections"]["sketch"] == {"kind": "ink", "canvas_block_id": None}
    assert dumped["sections"]["notes"]["entries"][0]["author"] == "agent"
    # The V6-06 keys stay off the wire while unset, inside the notebook too.
    assert "edited_by" not in dumped["sections"]["still_needed"]["items"][0]
    assert NotebookBlockState.model_validate(dumped) == state


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s["sections"]["notes"].update(kind="drawing"),
        lambda s: s["sections"]["notes"]["entries"].append({"id": "e1", "text": "dup", "ts": 4.0}),
        lambda s: s["sections"]["notes"]["entries"].append(
            {"id": "e3", "text": "y", "key": "extra", "ts": 6.0}
        ),
        lambda s: s["sections"]["notes"]["entries"][0].update(text="x" * (MAX_NOTEBOOK_ENTRY_CHARS + 1)),
        lambda s: s["sections"]["notes"]["entries"][0].update(author="page"),
        lambda s: s["sections"]["notes"].update(
            entries=[{"id": f"e{i}", "text": "x", "ts": 1.0} for i in range(MAX_NOTEBOOK_ENTRIES + 1)]
        ),
        lambda s: s["sections"]["still_needed"].update(
            items=[{"id": f"i{i}", "label": "x"} for i in range(MAX_NOTEBOOK_ITEMS + 1)]
        ),
        lambda s: s["sections"]["still_needed"]["items"].append({"id": "photo", "label": "again"}),
        lambda s: s["sections"]["summary"]["items"].append({"key": "claim_no", "label": "again"}),
        lambda s: s["sections"].update({f"x{i}": {"kind": "ink"} for i in range(MAX_NOTEBOOK_SECTIONS)}),
        lambda s: s["sections"].update({"bad id": {"kind": "ink"}}),
    ],
)
def test_a_notebook_state_that_breaks_a_bound_is_refused(mutate: Any) -> None:
    state = _full_state()
    mutate(state)
    with pytest.raises(ValidationError):
        NotebookBlockState.model_validate(state)


# --------------------------------------------------------------------- caller edits


@pytest.mark.parametrize(
    ("data", "change"),
    [
        ({"section_id": "notes", "text": "My own note"}, "note"),
        ({"section_id": "notes", "entry_id": "e1", "text": ""}, "note"),
        ({"section_id": "still_needed", "item_id": "photo", "done": True}, "tick"),
        ({"section_id": "summary", "key": "claim_no", "value": "C-2"}, "row"),
    ],
)
def test_a_notebook_edit_names_one_change(data: dict[str, Any], change: str) -> None:
    assert NotebookEdit.model_validate(data).change == change


@pytest.mark.parametrize(
    "data",
    [
        {"text": "no section"},
        {"section_id": "notes"},
        {"section_id": "notes", "text": "a", "item_id": "photo", "done": True},
        {"section_id": "notes", "text": "a", "value": "b"},
        {"section_id": "notes", "item_id": "photo"},
        {"section_id": "notes", "key": "claim_no"},
        {"section_id": "notes", "entry_id": "e1"},
        {"section_id": "notes", "text": "x" * (MAX_CALLER_EDIT_CHARS + 1)},
        {"section_id": "summary", "key": "claim_no", "value": "x" * (MAX_CALLER_EDIT_CHARS + 1)},
        {"section_id": "notes", "text": "a", "label": "extra keys are refused"},
        {"section_id": "../x", "text": "a"},
    ],
)
def test_a_malformed_notebook_edit_is_refused(data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        NotebookEdit.model_validate(data)


# --------------------------------------------------------------------- layout


def test_a_layout_config_is_strict() -> None:
    assert LayoutBlockConfig().model_dump() == {"kind": "tabs", "children": [], "columns": 2}
    children = [{"block_id": "a", "label": "A"}]
    ok = _spec("lay", "layout", {"kind": "columns", "columns": 3, "children": children})
    assert validate_block_config(ok) == []
    for bad in (
        {"kind": "grid"},
        {"columns": 4},
        {"children": [{"block_id": ""}]},
        {"children": [{"id": "a"}]},
        {"children": [{"block_id": f"b{i}"} for i in range(MAX_LAYOUT_CHILDREN + 1)]},
    ):
        assert validate_block_config(_spec("lay", "layout", bad)), bad


def _panel(*blocks: BlockSpec) -> list[BlockSpec]:
    return list(blocks)


def test_a_layout_over_existing_blocks_is_fine() -> None:
    blocks = _panel(
        _spec("notes", "notes"),
        _spec("card", "details"),
        _spec("tabs", "layout", {"children": [{"block_id": "notes"}, {"block_id": "card", "label": "Card"}]}),
    )
    assert layout_issues(blocks) == []


@pytest.mark.parametrize(
    ("children", "message"),
    [
        ([{"block_id": "missing"}], "there is no block 'missing' on this panel"),
        ([{"block_id": "tabs"}], "a layout cannot hold itself"),
        ([{"block_id": "other"}], "'other' is a layout. A layout cannot hold another layout"),
        ([{"block_id": "card"}, {"block_id": "card"}], "'card' is listed twice in this layout"),
    ],
)
def test_a_bad_layout_child_is_an_error_at_its_id(children: list[dict[str, Any]], message: str) -> None:
    blocks = _panel(
        _spec("card", "details"),
        _spec("tabs", "layout", {"children": children}),
        _spec("other", "layout", {"children": []}),
    )
    errors = [i for i in layout_issues(blocks) if i.severity == "error"]
    assert [(i.path, i.message) for i in errors][-1] == (
        f"panel.blocks[1].config.children[{len(children) - 1}].block_id",
        message,
    )


def test_a_child_claimed_by_two_layouts_is_an_error() -> None:
    blocks = _panel(
        _spec("card", "details"),
        _spec("first", "layout", {"children": [{"block_id": "card"}]}),
        _spec("second", "layout", {"kind": "columns", "children": [{"block_id": "card"}]}),
    )
    issues = layout_issues(blocks)
    assert [(i.path, i.message) for i in issues] == [
        ("panel.blocks[2].config.children[0].block_id", "'card' is already shown in 'first'")
    ]


def test_an_empty_layout_is_a_warning_and_a_broken_one_is_left_to_the_config_check() -> None:
    blocks = _panel(_spec("tabs", "layout"), _spec("broken", "layout", {"kind": "grid"}))
    issues = layout_issues(blocks)
    assert [(i.path, i.severity) for i in issues] == [("panel.blocks[0].config.children", "warning")]


# --------------------------------------------------------------------- the Notebook preset


def test_the_notebook_preset_is_a_valid_wide_panel() -> None:
    assert NOTEBOOK_PRESET.layout == "wide"
    assert NOTEBOOK_PRESET.panel_id == "composite"
    assert [b.type for b in NOTEBOOK_PRESET.blocks] == ["status", "notebook", "canvas", "gallery"]
    assert validate_panel_block_configs(NOTEBOOK_PRESET.blocks) == []
    assert layout_issues(NOTEBOOK_PRESET.blocks) == []
    notebook = NotebookBlockConfig.model_validate(NOTEBOOK_PRESET.blocks[1].config)
    assert [(s.id, s.kind) for s in notebook.sections] == [
        ("notes", "text"),
        ("still_needed", "checklist"),
        ("summary", "details"),
        ("sketch", "ink"),
    ]
    assert notebook.font == "handwritten"
    assert notebook.caller_can_write is True
    # V6-14, ask #94: the Sketch section's board — claimed once, and the caller may draw
    # on it even though the notebook's own `caller_can_write` governs only its other
    # sections.
    assert notebook.sections[3].canvas_block_id == "sketch_board"
    assert canvas_claim_issues(NOTEBOOK_PRESET.blocks) == []
    board = CanvasBlockConfig.model_validate(NOTEBOOK_PRESET.blocks[2].config)
    assert board.caller_can_draw is True
    assert canvas_caller_can_draw("sketch_board", NOTEBOOK_PRESET.blocks) is True
    # It survives a JSON round trip, as it travels in an agent's config.
    assert PanelLayout.model_validate(NOTEBOOK_PRESET.model_dump(mode="json")) == NOTEBOOK_PRESET
    assert AgentConfig.model_fields["panel"].annotation is PanelLayout


def test_panel_preset_hands_out_copies() -> None:
    first = panel_preset(NOTEBOOK_PRESET_ID)
    assert isinstance(first, PanelLayout)
    assert first == NOTEBOOK_PRESET
    first.blocks[1].config["caller_can_write"] = False
    assert NOTEBOOK_PRESET.blocks[1].config["caller_can_write"] is True
    assert panel_preset("nope") is None
    response = PanelPresetsResponse(items=list(PANEL_PRESETS))
    assert [p.id for p in response.items] == [NOTEBOOK_PRESET_ID]
