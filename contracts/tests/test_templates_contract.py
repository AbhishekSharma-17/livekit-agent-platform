"""Starter template contracts (docs/v4/TEMPLATES.md §2): round-trip, id pattern, validators, export."""

import re
from typing import Any

import pytest
from pydantic import ValidationError

from lkap_contracts import api_models
from lkap_contracts.export import EXPORTED_MODELS, build_combined_schema
from lkap_contracts.packs import PackManifest
from lkap_contracts.templates import TEMPLATE_ID_PATTERN, StarterTemplate


def _template(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "receptionist",
        "name": "Receptionist",
        "tagline": "Books appointments",
        "description": "Greets callers and books appointments.",
        "category": "scheduling",
        "chips": ["flow", "http_tools"],
        "requires": {"provider_keys": [{"provider_id": "google-image-gen", "optional": True}]},
        "greeting": "Hi!",
        "voice": {"user_away_timeout_s": 20},
        "knowledge": {"top_k": 4},
        "tool_seeds": [
            {
                "definition": {
                    "name": "check_availability",
                    "description": "Check free slots",
                    "parameters": {"type": "object", "properties": {}},
                    "method": "GET",
                    "url": "https://example.com/slots",
                    "allowed_hosts": ["example.com"],
                }
            }
        ],
        "kb_seeds": [{"kb_name": "Receptionist · Practice info", "files": ["practice_info.md"]}],
        "next_steps": [{"label": "Review the flow", "section": "flow"}],
    }
    base.update(overrides)
    return base


def test_starter_template_round_trips() -> None:
    template = StarterTemplate.model_validate(_template())

    again = StarterTemplate.model_validate_json(template.model_dump_json())

    assert again == template
    assert again.pack_id == "generic" and again.order == 100
    assert again.tool_seeds[0].definition.kind == "http"


@pytest.mark.parametrize(
    "template_id", ["blank", "knowledge_assistant", "pack:generic", "pack:insurance_claim"]
)
def test_template_id_pattern_accepts_slugs_and_derived_ids(template_id: str) -> None:
    assert re.match(TEMPLATE_ID_PATTERN, template_id)
    assert StarterTemplate.model_validate(_template(id=template_id)).id == template_id


@pytest.mark.parametrize("template_id", ["", "Blank", "1st", "pack:", "packs:generic", "has-dash", "a" * 33])
def test_template_id_pattern_rejects_other_shapes(template_id: str) -> None:
    with pytest.raises(ValidationError):
        StarterTemplate.model_validate(_template(id=template_id))


def test_a_template_voice_may_not_set_the_greeting() -> None:
    with pytest.raises(ValidationError, match=r"voice\.greeting"):
        StarterTemplate.model_validate(_template(voice={"greeting": "Hello"}))


def test_a_template_knowledge_may_not_set_kb_ids() -> None:
    with pytest.raises(ValidationError, match=r"knowledge\.kb_ids"):
        StarterTemplate.model_validate(_template(knowledge={"kb_ids": ["kb1"]}))


def test_unknown_chip_and_section_are_rejected() -> None:
    with pytest.raises(ValidationError):
        StarterTemplate.model_validate(_template(chips=["teleport"]))
    with pytest.raises(ValidationError):
        StarterTemplate.model_validate(_template(next_steps=[{"label": "x", "section": "nowhere"}]))


def test_agent_create_accepts_a_template_id() -> None:
    body = api_models.AgentCreate.model_validate({"name": "Front desk", "template_id": "receptionist"})

    assert body.template_id == "receptionist"
    assert body.pack_id == "generic"
    assert api_models.AgentCreate.model_validate({"name": "x"}).template_id is None


def test_templates_response_round_trips() -> None:
    manifest = PackManifest(
        id="generic",
        version="0.1.0",
        name="Generic",
        description="d",
        ui_panel_id="generic",
        default_instructions="i",
        default_greeting="g",
        recommended_pipeline={"mode": "cascaded"},
        capabilities={},
        tool_names=[],
        state_schema={"type": "object"},
    )
    response = api_models.TemplatesResponse(
        items=[api_models.TemplateOut(template=StarterTemplate.model_validate(_template()), pack=manifest)]
    )

    again = api_models.TemplatesResponse.model_validate_json(response.model_dump_json())

    assert again == response
    assert again.items[0].derived is False


@pytest.mark.parametrize("name", ["StarterTemplate", "TemplateOut", "TemplatesResponse"])
def test_template_models_are_exported(name: str) -> None:
    assert name in EXPORTED_MODELS
    assert name in build_combined_schema()["properties"]


def test_a_dump_keeps_only_the_set_voice_and_knowledge_fields() -> None:
    dumped = StarterTemplate.model_validate(_template()).model_dump(mode="json")

    assert dumped["voice"] == {"user_away_timeout_s": 20}
    assert dumped["knowledge"] == {"top_k": 4}
