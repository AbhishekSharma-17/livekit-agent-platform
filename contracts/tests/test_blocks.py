"""Per-block config schemas (R-V2-17, ``lkap_contracts.blocks``)."""

from typing import Any, get_args

import pytest

from lkap_contracts.blocks import (
    BLOCK_CONFIG_MODELS,
    validate_block_config,
    validate_panel_block_configs,
)
from lkap_contracts.migrate import DEFAULT_COMPOSITE_BLOCKS
from lkap_contracts.ui_protocol import BlockSpec, BlockType


def _spec(block_type: BlockType, config: dict[str, Any]) -> BlockSpec:
    return BlockSpec(id="b", type=block_type, config=config)


def test_every_block_type_has_a_config_model() -> None:
    assert set(BLOCK_CONFIG_MODELS) == set(get_args(BlockType))


@pytest.mark.parametrize(
    ("block_type", "config"),
    [
        ("status", {}),
        ("form", {}),
        ("table", {"columns": [{"key": "item", "label": "Item", "type": "string"}]}),
        ("table", {"columns": []}),
        ("document", {"url": "https://example.com/policy.pdf", "page": 2}),
        ("document", {"url": "", "page": 1}),
        ("document", {"url": None}),
        ("transcript", {"show_tools": True}),
        ("video", {"source": "user_screen", "muted": True}),
        ("video", {"source": "track:TR_abc123"}),
        ("custom", {}),
        ("custom", {"kind": "flow_progress", "anything": {"pack": ["declared"]}}),
    ],
)
def test_valid_configs_have_no_issues(block_type: BlockType, config: dict[str, Any]) -> None:
    assert validate_block_config(_spec(block_type, config)) == []


@pytest.mark.parametrize(
    ("block_type", "config", "path"),
    [
        ("table", {"foo": 1}, "config.foo"),
        ("status", {"label": "x"}, "config.label"),
        ("table", {"columns": [{"key": "", "label": "x"}]}, "config.columns[0].key"),
        ("table", {"columns": [{"key": "a", "label": "A", "width": 3}]}, "config.columns[0].width"),
        ("document", {"url": "http://insecure.example"}, "config.url"),
        ("document", {"page": 0}, "config.page"),
        ("video", {"source": "somewhere"}, "config.source"),
        ("transcript", {"show_tools": "sometimes"}, "config.show_tools"),
    ],
)
def test_invalid_configs_are_errors_at_the_offending_key(
    block_type: BlockType, config: dict[str, Any], path: str
) -> None:
    issues = validate_block_config(_spec(block_type, config))
    assert [i.path for i in issues] == [path]
    assert all(i.severity == "error" for i in issues)


def test_unknown_key_message_names_the_block_type() -> None:
    [issue] = validate_block_config(_spec("table", {"foo": 1}))
    assert issue.message == "unknown config key for a table block"


def test_panel_paths_address_each_block_by_index() -> None:
    blocks = [_spec("notes", {}), _spec("table", {"foo": 1})]
    issues = validate_panel_block_configs(blocks)
    assert [i.path for i in issues] == ["panel.blocks[1].config.foo"]


def test_the_default_composite_blocks_validate() -> None:
    blocks = [BlockSpec.model_validate(block) for block in DEFAULT_COMPOSITE_BLOCKS]
    assert validate_panel_block_configs(blocks) == []


# ------------------------------------------------------------- V5-08: the block quartet


@pytest.mark.parametrize(
    ("block_type", "config"),
    [
        ("choices", {}),
        ("choices", {"multi": True, "layout": "chips", "max_options": 4}),
        ("details", {"columns": 2, "fields": [{"key": "claim_no", "label": "Claim no."}]}),
        ("details", {"fields": [{"key": "amount", "label": "Amount", "type": "money"}]}),
        ("markdown", {"max_chars": 2000, "allow_links": True}),
        ("steps", {"steps": [{"id": "intake", "label": "Intake"}], "source": "flow"}),
        ("steps", {"show_notes": False}),
    ],
)
def test_quartet_valid_configs_have_no_issues(block_type: BlockType, config: dict[str, Any]) -> None:
    assert validate_block_config(_spec(block_type, config)) == []


@pytest.mark.parametrize(
    ("block_type", "config", "path"),
    [
        ("choices", {"layout": "grid"}, "config.layout"),
        ("choices", {"max_options": 1}, "config.max_options"),
        ("choices", {"options": []}, "config.options"),
        ("details", {"columns": 3}, "config.columns"),
        ("details", {"fields": [{"key": "a", "label": "A", "type": "colour"}]}, "config.fields[0].type"),
        ("details", {"fields": [{"key": "a", "label": "A"}, {"key": "a", "label": "B"}]}, "config.fields"),
        ("markdown", {"max_chars": 50}, "config.max_chars"),
        ("markdown", {"html": True}, "config.html"),
        ("steps", {"source": "pack"}, "config.source"),
        ("steps", {"steps": [{"id": "", "label": "x"}]}, "config.steps[0].id"),
        ("steps", {"steps": [{"id": "a", "label": "A"}, {"id": "a", "label": "B"}]}, "config.steps"),
    ],
)
def test_quartet_invalid_configs_are_errors_at_the_offending_key(
    block_type: BlockType, config: dict[str, Any], path: str
) -> None:
    issues = validate_block_config(_spec(block_type, config))
    assert [i.path for i in issues] == [path]
    assert all(i.severity == "error" for i in issues)
