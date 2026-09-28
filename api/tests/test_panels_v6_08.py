"""V6-08: validating the notebook and layout blocks, and the ready-made panels (D-V6-15, D-V6-18)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from conftest import inference_config
from lkap_contracts.agent_config import NOTEBOOK_PRESET, CapabilitiesConfig, PanelLayout
from lkap_contracts.ui_protocol import BlockSpec

from lkap_api.config_service import ValidationContext, validate
from lkap_api.panels import CALLER_EDIT_ON_PHONE_MESSAGE


def _issues(panel: PanelLayout, **kwargs: object) -> list[tuple[str, str, str]]:
    config = inference_config(panel=panel, **kwargs)
    result = validate(ValidationContext(config=config))
    return [(i.path, i.severity, i.message) for i in result.issues if i.path.startswith("panel.")]


def _block(block_id: str, block_type: str, config: dict[str, Any] | None = None) -> BlockSpec:
    return BlockSpec(id=block_id, type=block_type, config=config or {})  # type: ignore[arg-type]


def test_the_notebook_preset_validates_without_an_issue() -> None:
    # V6-14, ask #94: the preset's Sketch section now claims a real `canvas` block with
    # `caller_can_draw` on. On the Cloud default LLM (`google/gemma-4-31b-it`, text-only)
    # that correctly gets `canvas_vision_issues`' warning — the agent cannot read what the
    # caller draws there until the agent picks a vision-capable model.
    assert _issues(NOTEBOOK_PRESET) == [
        (
            "panel.blocks[2]",
            "warning",
            "'google/gemma-4-31b-it' cannot see pictures, so the agent cannot read what the "
            "caller draws on this board — pick a model marked 'supports video' (e.g. "
            "google/gemini-3.5-flash)",
        )
    ]


@pytest.mark.parametrize(
    ("config", "path"),
    [
        ({"sections": [{"id": "a"}, {"id": "a"}]}, "panel.blocks[0].config.sections"),
        ({"sections": [{"id": "a", "kind": "video"}]}, "panel.blocks[0].config.sections[0].kind"),
        ({"caller_can_edit": True}, "panel.blocks[0].config.caller_can_edit"),
        ({"paper": "papyrus"}, "panel.blocks[0].config.paper"),
    ],
)
def test_a_bad_notebook_config_is_an_error_at_its_key(config: dict[str, Any], path: str) -> None:
    issues = _issues(PanelLayout(blocks=[_block("nb", "notebook", config)]))
    assert [(p, sev) for p, sev, _ in issues][:1] == [(path, "error")]


def test_a_writable_notebook_on_a_phone_agent_gets_the_tip_at_its_own_flag() -> None:
    panel = PanelLayout(blocks=[_block("nb", "notebook", {"caller_can_write": True})])
    phone = _issues(panel, capabilities=CapabilitiesConfig(dtmf=True))
    assert phone == [("panel.blocks[0].config.caller_can_write", "warning", CALLER_EDIT_ON_PHONE_MESSAGE)]
    assert _issues(panel) == []


def test_a_layout_over_existing_blocks_is_fine() -> None:
    panel = PanelLayout(
        blocks=[
            _block("card", "details"),
            _block("recap", "markdown"),
            _block(
                "tabs",
                "layout",
                {"children": [{"block_id": "card"}, {"block_id": "recap", "label": "Recap"}]},
            ),
        ]
    )
    assert _issues(panel) == []


@pytest.mark.parametrize(
    ("children", "message"),
    [
        ([{"block_id": "missing"}], "there is no block 'missing' on this panel"),
        ([{"block_id": "card"}, {"block_id": "card"}], "'card' is listed twice in this layout"),
        ([{"block_id": "other"}], "'other' is a layout; a layout cannot hold another layout"),
        ([{"block_id": "tabs"}], "a layout cannot hold itself"),
    ],
)
def test_a_layout_claiming_a_missing_or_duplicate_child_fails_validation(
    children: list[dict[str, str]], message: str
) -> None:
    panel = PanelLayout(
        blocks=[
            _block("card", "details"),
            _block("tabs", "layout", {"children": children}),
            _block("other", "layout", {"children": [{"block_id": "card"}]}),
        ]
    )
    errors = [(p, m) for p, sev, m in _issues(panel) if sev == "error"]
    assert (f"panel.blocks[1].config.children[{len(children) - 1}].block_id", message) in errors
    result = validate(ValidationContext(config=inference_config(panel=panel)))
    assert not result.ok


def test_a_child_claimed_by_two_layouts_fails_validation() -> None:
    panel = PanelLayout(
        blocks=[
            _block("card", "details"),
            _block("first", "layout", {"children": [{"block_id": "card"}]}),
            _block("second", "layout", {"kind": "columns", "children": [{"block_id": "card"}]}),
        ]
    )
    assert _issues(panel) == [
        ("panel.blocks[2].config.children[0].block_id", "error", "'card' is already shown in 'first'")
    ]


def test_panels_without_the_new_blocks_validate_as_before() -> None:
    """Compatibility: no new issue for a panel that uses neither block."""
    panel = PanelLayout(
        blocks=[
            _block("n", "notes"),
            _block("d", "details", {"caller_can_edit": True}),
            _block("c", "checklist"),
            _block("g", "gallery"),
        ]
    )
    assert _issues(panel) == []


async def test_the_presets_route_lists_the_notebook(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.get("/v1/panels/presets")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["id"] for item in items] == ["notebook"]
    notebook = items[0]
    assert notebook["name"] == "Notebook"
    panel = PanelLayout.model_validate(notebook["panel"])
    assert panel == NOTEBOOK_PRESET and panel.layout == "wide"


async def test_the_presets_route_needs_a_signed_in_caller(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/panels/presets")).status_code == 401


async def test_an_agent_saved_with_the_preset_resolves_it_for_the_session(
    admin_client: httpx.AsyncClient,
) -> None:
    created = await admin_client.post("/v1/agents", json={"name": "Demo — Notebook", "pack_id": "generic"})
    assert created.status_code in (200, 201), created.text
    agent = created.json()
    config = {**agent["config"], "panel": NOTEBOOK_PRESET.model_dump(mode="json")}
    saved = await admin_client.put(f"/v1/agents/{agent['id']}", json={"config": config})
    assert saved.status_code == 200, saved.text
    assert PanelLayout.model_validate(saved.json()["config"]["panel"]) == NOTEBOOK_PRESET
