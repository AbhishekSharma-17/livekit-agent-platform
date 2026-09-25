"""Tests for `lkap_api.panels.effective_layout` (R-V2-7, CONTRACTS-V2 §4.4 "Layout delivery")."""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import create_agent, inference_config
from lkap_contracts.agent_config import CapabilitiesConfig, PanelLayout
from lkap_contracts.flow import FlowSpec
from lkap_contracts.ui_protocol import BlockSpec

from lkap_api.config_service import CHOICES_ON_PHONE_MESSAGE, ValidationContext, validate
from lkap_api.db.models import Agent
from lkap_api.packs import get_manifest
from lkap_api.panels import effective_layout
from lkap_api.settings import Settings


def _agent_row(config_json: dict[str, object], *, pack_id: str = "generic") -> Agent:
    return Agent(
        id="p" * 32,
        slug="panel-test",
        name="Panel test",
        pack_id=pack_id,
        ui_panel_id="composite",
        published=True,
        config=config_json,
        config_version=1,
    )


def test_effective_layout_falls_through_to_the_pack_default_when_never_customized(
    settings: Settings,
) -> None:
    """`seed_config_from_manifest` never writes `panel`, so a freshly seeded agent's
    `config.panel` still holds the bare Pydantic default — the generic pack's
    `default_panel` (four blocks) must be what actually renders.
    """
    row = _agent_row(inference_config().model_dump(mode="json"))
    pack = get_manifest(settings.packs_list, "generic")
    assert pack is not None

    layout = effective_layout(row, pack)

    assert layout.panel_id == "composite"
    assert [block.type for block in layout.blocks] == ["status", "notes", "checklist", "activity"]


def test_effective_layout_returns_an_explicit_config_panel_verbatim(settings: Settings) -> None:
    custom = PanelLayout(
        panel_id="composite", layout="wide", blocks=[BlockSpec(id="notes", type="notes", order=0)]
    )
    config = inference_config(panel=custom).model_dump(mode="json")
    row = _agent_row(config)
    pack = get_manifest(settings.packs_list, "generic")

    layout = effective_layout(row, pack)

    assert layout == custom


def test_effective_layout_falls_back_to_the_bare_default_with_no_pack(settings: Settings) -> None:
    row = _agent_row(inference_config().model_dump(mode="json"), pack_id="not-installed")

    layout = effective_layout(row, None)

    assert layout == PanelLayout()


async def test_connect_returns_the_four_default_blocks_for_a_generic_pack_agent(
    admin_client: httpx.AsyncClient,
) -> None:
    agent = await create_agent(admin_client, name="Generic panel agent")

    response = await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["agent"]["panel"]["panel_id"] == "composite"
    assert [b["type"] for b in body["agent"]["panel"]["blocks"]] == [
        "status",
        "notes",
        "checklist",
        "activity",
    ]
    assert body["uiPanelId"] == body["agent"]["panel"]["panel_id"]


async def test_connect_returns_an_explicit_panel_verbatim(admin_client: httpx.AsyncClient) -> None:
    custom = {"panel_id": "composite", "layout": "wide", "blocks": []}
    config = json.loads(inference_config().model_dump_json())
    config["panel"] = custom
    agent = await create_agent(admin_client, name="Custom panel agent", config=config)

    response = await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})

    assert response.status_code == 200, response.text
    assert response.json()["agent"]["panel"] == custom


async def test_resolve_and_connect_layouts_are_byte_identical(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    agent = await create_agent(admin_client, name="Consistency check")

    connect_response = await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})
    session_id = connect_response.json()["sessionId"]

    resolved_response = await service_client.get(f"/internal/v1/sessions/{session_id}/resolved")

    assert connect_response.json()["agent"]["panel"] == resolved_response.json()["panel"]


# ================================================================ V5-08: the block quartet

FLOW = FlowSpec.model_validate(
    {
        "nodes": [
            {"id": "start", "kind": "start"},
            {"id": "collect", "kind": "agent", "label": "Collect", "instructions": "Ask for the details."},
        ],
        "edges": [{"id": "e1", "source": "start", "target": "collect"}],
    }
)


def _issues(blocks: list[BlockSpec], **kwargs: object) -> list[tuple[str, str, str]]:
    config = inference_config(panel=PanelLayout(blocks=blocks), **kwargs)
    result = validate(ValidationContext(config=config))
    return [(i.path, i.severity, i.message) for i in result.issues if i.path.startswith("panel.")]


@pytest.mark.parametrize(
    ("block", "path"),
    [
        (BlockSpec(id="c", type="choices", config={"layout": "grid"}), "panel.blocks[0].config.layout"),
        (BlockSpec(id="d", type="details", config={"columns": 3}), "panel.blocks[0].config.columns"),
        (BlockSpec(id="m", type="markdown", config={"html": True}), "panel.blocks[0].config.html"),
        (BlockSpec(id="s", type="steps", config={"source": "pack"}), "panel.blocks[0].config.source"),
    ],
)
def test_quartet_block_configs_are_checked_on_save(block: BlockSpec, path: str) -> None:
    assert [(p, sev) for p, sev, _ in _issues([block])] == [(path, "error")]


def test_valid_quartet_blocks_have_no_panel_issues() -> None:
    blocks = [
        BlockSpec(id="c", type="choices", config={"multi": True}),
        BlockSpec(id="d", type="details", config={"fields": [{"key": "claim_no", "label": "Claim"}]}),
        BlockSpec(id="m", type="markdown"),
        BlockSpec(
            id="s", type="steps", config={"steps": [{"id": "collect", "label": "Collect"}], "source": "flow"}
        ),
    ]
    assert _issues(blocks, flow=FLOW) == []


def test_a_flow_steps_block_on_an_agent_without_a_flow_is_a_warning() -> None:
    [(path, severity, message)] = _issues([BlockSpec(id="s", type="steps", config={"source": "flow"})])
    assert (path, severity) == ("panel.blocks[0].config.source", "warning")
    assert "no flow" in message


def test_a_flow_steps_block_naming_an_unknown_step_is_a_warning() -> None:
    block = BlockSpec(
        id="s",
        type="steps",
        config={"source": "flow", "steps": [{"id": "collect", "label": "A"}, {"id": "ghost", "label": "B"}]},
    )
    [(path, severity, message)] = _issues([block], flow=FLOW)
    assert (path, severity) == ("panel.blocks[0].config.steps[1].id", "warning")
    assert "'ghost'" in message


def test_a_choices_block_on_a_phone_agent_is_a_warning() -> None:
    blocks = [BlockSpec(id="n", type="notes"), BlockSpec(id="c", type="choices")]
    phone = _issues(blocks, capabilities=CapabilitiesConfig(dtmf=True))
    assert phone == [("panel.blocks[1]", "warning", CHOICES_ON_PHONE_MESSAGE)]
    assert _issues(blocks) == []


def test_agents_without_the_new_blocks_validate_as_before() -> None:
    """Compatibility: no new issue for a panel that has none of the four blocks."""
    blocks = [BlockSpec(id="t", type="table"), BlockSpec(id="k", type="kb_citations")]
    assert _issues(blocks, capabilities=CapabilitiesConfig(dtmf=True)) == []
