"""V6-06 contracts: the checklist and picture tools, per-block notes and caller edits (D-V6-19)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lkap_contracts import tools
from lkap_contracts.blocks import (
    BLOCK_CONFIG_MODELS,
    ChecklistBlockConfig,
    DetailsBlockConfig,
    validate_block_config,
)
from lkap_contracts.ui_protocol import (
    BLOCK_EDIT_ACTION,
    EDITABLE_BLOCK_TYPES,
    MAX_CALLER_EDIT_CHARS,
    BlockSpec,
    ChecklistEdit,
    ChecklistItem,
    DetailsEdit,
    DetailsItem,
    Note,
    UiState,
)


def test_the_checklist_tools_are_block_tools_for_a_checklist_block() -> None:
    for name in ("set_checklist", "check_item"):
        assert name in tools.BLOCK_TOOL_NAMES
        assert name not in tools.BUILTIN_TOOL_NAMES
        assert tools.BLOCK_TOOL_TYPES[name] == {"checklist"}
        assert name in tools.WRITE_BUILTINS
        assert tools.never_background(name)


def test_generate_image_is_a_write_built_in_that_never_joins_the_background_settings() -> None:
    assert "generate_image" in tools.BUILTIN_TOOL_NAMES
    assert "generate_image" in tools.WRITE_BUILTINS
    # It runs as the pack's sketch tool does (its own background job), so the agent's
    # background settings never name it.
    assert "generate_image" not in tools.BACKGROUNDABLE_BUILTINS
    assert "generate_image" not in tools.BUILTIN_DEFAULT_MODES
    assert tools.never_background("generate_image")
    document = tools.builtin_tools_document()
    assert "generate_image" in document["builtin_tool_names"]
    assert document["block_tool_types"]["set_checklist"] == ["checklist"]


def test_existing_tool_order_is_kept() -> None:
    """New names go at the end: the api and web read the lists in order."""
    # V6-13 appended `extract_now` after it.
    names = tools.BUILTIN_TOOL_NAMES
    assert names.index("generate_image") == names.index("switch_language") + 1
    assert tools.BLOCK_TOOL_NAMES[-2:] == ("set_checklist", "check_item")


@pytest.mark.parametrize("block_type", ["details", "checklist"])
def test_caller_can_edit_is_accepted_by_the_editable_block_configs(block_type: str) -> None:
    spec = BlockSpec(id="b1", type=block_type, config={"caller_can_edit": True})  # type: ignore[arg-type]
    assert validate_block_config(spec) == []
    assert BLOCK_CONFIG_MODELS[block_type].model_validate({}).caller_can_edit is False  # type: ignore[attr-defined]


@pytest.mark.parametrize("block_type", ["table", "markdown", "consent", "upload", "link", "notes", "gallery"])
def test_caller_can_edit_is_an_unknown_key_elsewhere(block_type: str) -> None:
    config: dict[str, object] = {"caller_can_edit": True}
    if block_type == "link":
        config["allowed_hosts"] = ["example.com"]
    spec = BlockSpec(id="b1", type=block_type, config=config)  # type: ignore[arg-type]
    issues = validate_block_config(spec, path="panel.blocks[0].config")
    assert [i.path for i in issues] == ["panel.blocks[0].config.caller_can_edit"]


def test_the_editable_types_have_editable_configs() -> None:
    assert EDITABLE_BLOCK_TYPES == {"details", "checklist"}
    assert BLOCK_CONFIG_MODELS["checklist"] is ChecklistBlockConfig
    assert BLOCK_CONFIG_MODELS["details"] is DetailsBlockConfig
    assert BLOCK_EDIT_ACTION == "edit"


def test_edit_payloads_are_strict_and_bounded() -> None:
    assert DetailsEdit.model_validate({"key": "claim_no", "value": ""}).value == ""
    assert ChecklistEdit.model_validate({"item_id": "photo", "done": True}).done is True
    with pytest.raises(ValidationError):
        DetailsEdit.model_validate({"key": "claim_no", "value": "x" * (MAX_CALLER_EDIT_CHARS + 1)})
    with pytest.raises(ValidationError):
        DetailsEdit.model_validate({"key": "claim_no", "value": "1", "label": "Changed"})
    with pytest.raises(ValidationError):
        DetailsEdit.model_validate({"key": "", "value": "1"})
    with pytest.raises(ValidationError):
        ChecklistEdit.model_validate({"item_id": "photo", "done": True, "label": "x"})
    with pytest.raises(ValidationError):
        ChecklistEdit.model_validate({"item_id": "photo"})


def test_new_state_fields_default_to_the_old_shape() -> None:
    """Additive: data written before V6-06 still validates and keeps its meaning."""
    note = Note.model_validate({"id": "n1", "text": "hi", "ts": 1.0})
    assert note.block_id is None
    assert ChecklistItem(id="a", label="A").edited_by is None
    assert DetailsItem(key="k", label="K").edited_by is None
    assert DetailsItem(key="k", label="K", edited_by="caller").edited_by == "caller"
    with pytest.raises(ValidationError):
        DetailsItem.model_validate({"key": "k", "label": "K", "edited_by": "someone"})


def test_unset_new_keys_stay_off_the_wire() -> None:
    """A panel that never uses them sends exactly what it sent before V6-06."""
    note = Note(id="n1", text="hi", ts=1.0)
    assert "block_id" not in note.model_dump(mode="json")
    assert Note(id="n1", text="hi", ts=1.0, block_id="claim").model_dump()["block_id"] == "claim"
    item = ChecklistItem(id="a", label="A")
    assert "edited_by" not in item.model_dump(mode="json") and "edited_by" not in item.model_dump_json()
    assert ChecklistItem(id="a", label="A", edited_by="caller").model_dump()["edited_by"] == "caller"
    assert "edited_by" not in DetailsItem(key="k", label="K").model_dump(mode="json", by_alias=True)
    state = UiState(notes=[note], checklist=[item])
    assert "block_id" not in state.model_dump_json() and "edited_by" not in state.model_dump_json()
