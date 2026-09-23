"""V2-10: the additive `packs.base` Protocol changes (CONTRACTS-V2 §4.4).

* `UiChannel` gains `set_block`, `patch_block`, `request_form`, `cite`.
* `on_block_action` lives on the separate `BlockActionPack` Protocol, so every
  existing pack still satisfies `Pack` structurally without defining it.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from packs.base import BlockActionPack, Pack, UiChannel
from packs.generic.pack import PACK as GENERIC_PACK
from packs.insurance_claim.pack import PACK as INSURANCE_PACK

V1_UI_METHODS = (
    "patch",
    "snapshot",
    "set_status",
    "add_note",
    "set_checklist",
    "push_asset",
    "activity",
    "request_ui",
)
V2_UI_METHODS = ("set_block", "patch_block", "request_form", "cite")


def _pack_members() -> set[str]:
    return {name for name in dir(Pack) if not name.startswith("_")}


@pytest.mark.parametrize("name", V1_UI_METHODS + V2_UI_METHODS)
def test_ui_channel_declares_every_method_as_a_coroutine(name: str) -> None:
    assert inspect.iscoroutinefunction(getattr(UiChannel, name))


def test_ui_channel_request_form_signature() -> None:
    params = inspect.signature(UiChannel.request_form).parameters
    assert list(params) == ["self", "block_id", "schema", "prefill", "timeout_s"]
    assert params["prefill"].default is None
    assert params["timeout_s"].default == 120


def test_ui_channel_patch_block_and_cite_signatures() -> None:
    assert list(inspect.signature(UiChannel.patch_block).parameters) == ["self", "block_id", "ops"]
    assert list(inspect.signature(UiChannel.cite).parameters) == ["self", "block_id", "hits"]
    assert list(inspect.signature(UiChannel.set_block).parameters) == ["self", "block_id", "state"]


def test_on_block_action_is_not_required_by_pack() -> None:
    assert "on_block_action" not in _pack_members()
    assert getattr(BlockActionPack, "_is_protocol", False) is True
    params = list(inspect.signature(BlockActionPack.on_block_action).parameters)
    assert params == ["self", "ctx", "block_id", "name", "data"]


@pytest.mark.parametrize("pack", [GENERIC_PACK, INSURANCE_PACK])
def test_shipped_packs_still_implement_every_pack_member(pack: Any) -> None:
    for member in _pack_members():
        assert hasattr(pack, member), member


def test_insurance_pack_declares_no_blocks_for_its_custom_panel() -> None:
    manifest = INSURANCE_PACK.manifest
    assert manifest.default_panel is not None
    assert manifest.default_panel.panel_id == "insurance_notebook"
    assert manifest.default_panel.blocks == []
    assert manifest.blocks == []


def test_generic_pack_default_panel_is_the_composite_with_four_blocks() -> None:
    panel = GENERIC_PACK.manifest.default_panel
    assert panel is not None and panel.panel_id == "composite"
    assert [(b.id, b.type) for b in panel.blocks] == [
        ("status", "status"),
        ("notes", "notes"),
        ("checklist", "checklist"),
        ("activity", "activity"),
    ]
