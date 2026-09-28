"""V6-23 (D-V6-20): validating the `signature`, `chart`, `timer`, `code` and `cart` blocks.

The validators run on in-memory configs. No network, no database.
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import inference_config
from lkap_contracts.agent_config import CapabilitiesConfig, PanelLayout
from lkap_contracts.ui_protocol import BlockSpec

from lkap_api.config_service import ValidationContext, validate
from lkap_api.panels import SIGNATURE_ON_PHONE_MESSAGE


def _issues(panel: PanelLayout, **kwargs: Any) -> list[tuple[str, str, str]]:
    result = validate(ValidationContext(config=inference_config(panel=panel, **kwargs)))
    return [(i.path, i.severity, i.message) for i in result.issues if i.path.startswith("panel.")]


def _block(block_type: str, config: dict[str, Any] | None = None, block_id: str = "b") -> BlockSpec:
    return BlockSpec(id=block_id, type=block_type, config=config or {})  # type: ignore[arg-type]


FULL = PanelLayout(
    blocks=[
        _block("signature", {"disclosure_text": "I accept the estimate.", "allow_decline": False}, "sign"),
        _block("chart", {"kind": "gauge", "show_table": True}, "chart"),
        _block("timer", {"mode": "elapsed", "max_seconds": 900}, "clock"),
        _block("code", {"max_chars": 2000, "wrap": True}, "snippet"),
        _block("cart", {"currency": "INR", "max_lines": 10}, "order"),
    ]
)


def test_the_five_blocks_validate_cleanly_on_a_web_agent() -> None:
    assert _issues(FULL) == []


@pytest.mark.parametrize(
    ("block_type", "config", "path"),
    [
        ("signature", {"text": "x"}, "panel.blocks[0].config.text"),
        ("signature", {"disclosure_text": "x" * 2001}, "panel.blocks[0].config.disclosure_text"),
        ("chart", {"kind": "scatter"}, "panel.blocks[0].config.kind"),
        ("chart", {"points": []}, "panel.blocks[0].config.points"),
        ("timer", {"max_seconds": 4 * 3600 + 1}, "panel.blocks[0].config.max_seconds"),
        ("timer", {"mode": "alarm"}, "panel.blocks[0].config.mode"),
        ("code", {"max_chars": 20_001}, "panel.blocks[0].config.max_chars"),
        ("code", {"language": "python"}, "panel.blocks[0].config.language"),
        ("cart", {"currency": "usd"}, "panel.blocks[0].config.currency"),
        ("cart", {"max_lines": 51}, "panel.blocks[0].config.max_lines"),
        ("cart", {"lines": []}, "panel.blocks[0].config.lines"),
    ],
)
def test_a_bad_config_is_an_error_at_its_key(block_type: str, config: dict[str, Any], path: str) -> None:
    issues = _issues(PanelLayout(blocks=[_block(block_type, config)]))
    assert [(p, sev) for p, sev, _ in issues][:1] == [(path, "error")]


def test_a_signature_on_an_agent_set_up_for_phone_calls_gets_the_tip() -> None:
    panel = PanelLayout(blocks=[_block("chart"), _block("signature", block_id="sign")])
    assert _issues(panel) == []
    phone = _issues(panel, capabilities=CapabilitiesConfig(dtmf=True))
    assert phone == [("panel.blocks[1]", "warning", SIGNATURE_ON_PHONE_MESSAGE)]
