"""V6-06: panel validation for caller edits and pictures (D-V6-19)."""

from __future__ import annotations

import pytest
from conftest import inference_config
from lkap_contracts.agent_config import CapabilitiesConfig, PanelLayout, PipelineConfig, ToolsConfig
from lkap_contracts.common import ProviderRef
from lkap_contracts.ui_protocol import BlockSpec

from lkap_api.config_service import ValidationContext, validate
from lkap_api.panels import CALLER_EDIT_ON_PHONE_MESSAGE, PICTURES_NEED_A_GALLERY_MESSAGE

IMAGE_GEN = ProviderRef(provider_id="google-image-gen", model="gemini-3.1-flash-image")


def _issues(panel: PanelLayout, **kwargs: object) -> list[tuple[str, str, str]]:
    config = inference_config(panel=panel, **kwargs)
    result = validate(ValidationContext(config=config))
    return [
        (i.path, i.severity, i.message)
        for i in result.issues
        # The picture slot's own key check ("requires a credential") is not this package's.
        if i.path.startswith("panel.") or (i.path == "pipeline.image_gen" and i.severity == "warning")
    ]


def _with_image_gen() -> PipelineConfig:
    return inference_config().pipeline.model_copy(update={"image_gen": IMAGE_GEN})


@pytest.mark.parametrize("block_type", ["details", "checklist"])
def test_caller_can_edit_is_accepted_on_the_editable_blocks(block_type: str) -> None:
    block = BlockSpec(id="b", type=block_type, config={"caller_can_edit": True})  # type: ignore[arg-type]
    assert _issues(PanelLayout(blocks=[block])) == []


@pytest.mark.parametrize("block_type", ["table", "markdown", "notes", "consent"])
def test_caller_can_edit_is_an_error_on_other_blocks(block_type: str) -> None:
    block = BlockSpec(id="b", type=block_type, config={"caller_can_edit": True})  # type: ignore[arg-type]
    assert [(p, sev) for p, sev, _ in _issues(PanelLayout(blocks=[block]))] == [
        ("panel.blocks[0].config.caller_can_edit", "error")
    ]


def test_an_editable_block_on_a_phone_agent_gets_a_tip() -> None:
    panel = PanelLayout(
        blocks=[
            BlockSpec(id="n", type="notes"),
            BlockSpec(id="d", type="details", config={"caller_can_edit": True}),
            BlockSpec(id="c", type="checklist"),
        ]
    )
    phone = _issues(panel, capabilities=CapabilitiesConfig(dtmf=True))
    assert phone == [("panel.blocks[1].config.caller_can_edit", "warning", CALLER_EDIT_ON_PHONE_MESSAGE)]
    assert _issues(panel) == []
    assert CALLER_EDIT_ON_PHONE_MESSAGE.startswith("Tip:")


def test_a_picture_model_without_a_gallery_gets_a_tip() -> None:
    no_gallery = PanelLayout(blocks=[BlockSpec(id="n", type="notes")])
    assert _issues(no_gallery, pipeline=_with_image_gen()) == [
        ("pipeline.image_gen", "warning", PICTURES_NEED_A_GALLERY_MESSAGE)
    ]
    with_gallery = PanelLayout(blocks=[BlockSpec(id="n", type="notes"), BlockSpec(id="g", type="gallery")])
    assert _issues(with_gallery, pipeline=_with_image_gen()) == []
    switched_off = ToolsConfig(builtin_disabled=["generate_image"])
    assert _issues(no_gallery, pipeline=_with_image_gen(), tools=switched_off) == []


def test_a_pack_panel_with_a_picture_model_gets_no_tip() -> None:
    """The insurance pack's own panel draws its sketches itself (until V6-22)."""
    insurance = PanelLayout(panel_id="insurance_notebook", blocks=[])
    assert _issues(insurance, pipeline=_with_image_gen()) == []
    assert _issues(PanelLayout(), pipeline=_with_image_gen()) == []


def test_agents_without_the_new_settings_validate_as_before() -> None:
    """Compatibility: no new issue for a panel that uses none of V6-06."""
    panel = PanelLayout(
        blocks=[
            BlockSpec(id="d", type="details"),
            BlockSpec(id="c", type="checklist"),
            BlockSpec(id="t", type="table"),
        ]
    )
    assert _issues(panel, capabilities=CapabilitiesConfig(dtmf=True)) == []
