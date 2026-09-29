"""V6-31: the reasoning part of the capability view, read from OpenRouter's catalog, and the
validator warnings that explain what the worker does with it."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from conftest import inference_config
from lkap_contracts.agent_config import ProviderRef
from lkap_contracts.api_models import CatalogItem
from lkap_contracts.common import Issue
from lkap_contracts.providers import ModelCapabilities, accepts_parameter, get, reasoning_effort_to_send

from lkap_api.catalogs.openrouter import OPENROUTER_ADAPTERS
from lkap_api.config_service import ValidationContext, validate
from lkap_api.custom_models.capabilities import (
    catalog_capabilities,
    registry_capabilities,
    resolve_capabilities,
)
from lkap_api.custom_models.reasoning_checks import reasoning_issues

#: OpenRouter's `/models` record for `openai/gpt-6-luna` (the fields the view reads, 2026-09-29).
LUNA_META: dict[str, Any] = {
    "architecture": {"input_modalities": ["file", "image", "text"], "output_modalities": ["text"]},
    "context_length": 1_050_000,
    "pricing": {"prompt": "0.0000001", "completion": "0.0000005"},
    "supported_parameters": [
        "include_reasoning",
        "max_completion_tokens",
        "max_tokens",
        "reasoning",
        "reasoning_effort",
        "response_format",
        "seed",
        "structured_outputs",
        "tool_choice",
        "tools",
    ],
    "reasoning": {
        "mandatory": False,
        "default_enabled": True,
        "supported_efforts": ["max", "xhigh", "high", "medium", "low", "none"],
        "default_effort": "medium",
    },
}
MINI_META: dict[str, Any] = {
    "architecture": {"input_modalities": ["image", "text"], "output_modalities": ["text"]},
    "pricing": {"prompt": "0.0000004", "completion": "0.0000016"},
    "supported_parameters": ["max_tokens", "temperature", "tool_choice", "tools", "top_p"],
    "reasoning": None,
}
R1_META: dict[str, Any] = {
    "supported_parameters": ["include_reasoning", "max_tokens", "reasoning", "temperature", "tools"],
    "reasoning": {"mandatory": True},
}


def test_catalog_view_of_luna_is_a_reasoning_model_without_temperature() -> None:
    caps = catalog_capabilities(LUNA_META)

    assert caps.reasoning is True
    assert caps.reasoning_efforts == ["none", "low", "medium", "high", "xhigh", "max"]
    assert caps.request_parameters is not None
    assert "temperature" not in caps.request_parameters
    assert caps.tools is True
    assert caps.vision is True


def test_catalog_view_of_a_non_reasoning_model() -> None:
    caps = catalog_capabilities(MINI_META)

    assert caps.reasoning is False
    assert caps.reasoning_efforts == []
    assert caps.request_parameters is not None and "temperature" in caps.request_parameters


def test_catalog_view_of_a_model_that_always_reasons_without_an_effort_setting() -> None:
    caps = catalog_capabilities(R1_META)

    assert caps.reasoning is True
    assert caps.reasoning_efforts == []


def test_catalog_view_of_other_vendors_says_nothing_about_reasoning() -> None:
    caps = catalog_capabilities({"capabilities": {"image_input": {"supported": True}}})

    assert caps.reasoning is None
    assert caps.reasoning_efforts is None
    assert caps.request_parameters is None


@pytest.mark.parametrize(
    ("model", "reasoning", "efforts"),
    [
        ("openai/gpt-5.5", True, ["none", "low", "medium", "high", "xhigh"]),
        ("openai/gpt-4.1", False, []),
        ("google/gemma-4-31b-it", None, None),
        ("google/gemini-3.5-flash", None, None),
        ("unlisted/model", None, None),
    ],
)
def test_registry_view_of_livekit_inference_models(
    model: str, reasoning: bool | None, efforts: list[str] | None
) -> None:
    caps = registry_capabilities(get("livekit-inference-llm"), model)

    assert caps.reasoning is reasoning
    assert caps.reasoning_efforts == efforts
    assert caps.request_parameters is None


def test_the_catalog_wins_over_the_registry_for_reasoning() -> None:
    spec = get("openrouter-llm")
    item = CatalogItem(id="openai/gpt-4.1-mini", label="GPT-4.1 mini", meta={**LUNA_META})

    caps = resolve_capabilities(spec, "openai/gpt-4.1-mini", None, item)

    assert caps.reasoning is True  # the (made-up) catalog record beats the registry's `False`
    assert resolve_capabilities(spec, "openai/gpt-4.1-mini", None, None).reasoning is False


def _ctx(
    model: str, meta: dict[str, Any] | None, *, slot: str = "llm", mode: str = "cascaded", **fields: Any
) -> ValidationContext:
    config = inference_config()
    ref = ProviderRef(provider_id="openrouter-llm", credential_id="cred-or", model=model, fields=fields)
    setattr(config.pipeline, slot, ref)
    config.pipeline.mode = mode  # type: ignore[assignment]
    catalog = {model: CatalogItem(id=model, label=model, meta=meta)} if meta is not None else {}
    return ValidationContext(
        config=config,
        credential_providers={"cred-or": "openrouter-llm"},
        catalog_items={"openrouter-llm": catalog},
    )


def _by_path(issues: list[Issue]) -> dict[str, str]:
    return {issue.path: issue.message for issue in issues}


def test_luna_with_a_stored_temperature_warns_that_it_is_left_out() -> None:
    issues = reasoning_issues(_ctx("openai/gpt-6-luna", LUNA_META, temperature=0.7))

    found = _by_path(issues)
    assert found == {
        "pipeline.llm.fields.temperature": "'openai/gpt-6-luna' does not accept 'Temperature', so the agent "
        "leaves it out of each request"
    }
    assert all(issue.severity == "warning" for issue in issues)


def test_luna_with_no_effort_on_a_voice_call_is_quiet_because_the_lowest_is_used() -> None:
    assert reasoning_issues(_ctx("openai/gpt-6-luna", LUNA_META)) == []


@pytest.mark.parametrize("effort", ["medium", "high", "max"])
def test_luna_at_medium_or_more_on_a_voice_call_warns_about_seconds_per_reply(effort: str) -> None:
    (issue,) = reasoning_issues(_ctx("openai/gpt-6-luna", LUNA_META, reasoning_effort=effort))

    assert issue.path == "pipeline.llm.fields.reasoning_effort"
    assert issue.severity == "warning"
    assert "adds several seconds to each reply on a voice call" in issue.message
    assert "'none'" in issue.message


def test_an_effort_the_model_does_not_offer_names_the_one_used() -> None:
    (issue,) = reasoning_issues(_ctx("openai/gpt-6-luna", LUNA_META, reasoning_effort="minimal"))

    assert (
        issue.message
        == "'openai/gpt-6-luna' does not offer 'minimal' reasoning effort; 'low' is used instead"
    )


def test_an_effort_on_a_non_reasoning_model_is_ignored_with_a_warning() -> None:
    (issue,) = reasoning_issues(_ctx("openai/gpt-4.1-mini", MINI_META, reasoning_effort="low"))

    assert issue.path == "pipeline.llm.fields.reasoning_effort"
    assert issue.message == "'openai/gpt-4.1-mini' does not reason, so Reasoning effort is ignored"


def test_a_non_reasoning_model_with_temperature_has_no_findings() -> None:
    assert reasoning_issues(_ctx("openai/gpt-4.1-mini", MINI_META, temperature=0.4)) == []


def test_an_uncached_catalog_gives_no_findings() -> None:
    assert reasoning_issues(_ctx("openai/gpt-6-luna", None, temperature=0.7)) == []


def test_a_reasoning_model_without_an_effort_setting_gets_a_tip() -> None:
    (issue,) = reasoning_issues(_ctx("deepseek/deepseek-r1", R1_META))

    assert issue.path == "pipeline.llm"
    assert issue.message.startswith(
        "Tip: 'deepseek/deepseek-r1' can think before it answers and has no effort setting"
    )


def test_the_background_model_gets_parameter_warnings_but_no_voice_warning() -> None:
    issues = reasoning_issues(
        _ctx("openai/gpt-6-luna", LUNA_META, slot="workflow_llm", temperature=0.2, reasoning_effort="high")
    )

    assert _by_path(issues).keys() == {"pipeline.workflow_llm.fields.temperature"}


def test_an_unlisted_model_id_is_never_quoted() -> None:
    ctx = _ctx("some-lab/unlisted-model-7b", None, reasoning_effort="high")

    (issue,) = reasoning_issues(ctx)

    assert "unlisted-model-7b" not in issue.message
    assert issue.message.startswith("this model thinks at 'high' effort")


def test_validate_reports_the_findings_as_warnings_only() -> None:
    result = validate(_ctx("openai/gpt-6-luna", LUNA_META, temperature=0.7, reasoning_effort="high"))

    paths = {issue.path for issue in result.issues if issue.path.startswith("pipeline.llm.fields")}
    assert paths == {"pipeline.llm.fields.temperature", "pipeline.llm.fields.reasoning_effort"}
    assert not any(issue.severity == "error" and issue.path in paths for issue in result.issues)


def test_capabilities_model_round_trips_the_new_fields() -> None:
    caps = ModelCapabilities(reasoning=True, reasoning_efforts=["low"], request_parameters=["tools"])
    assert ModelCapabilities.model_validate_json(caps.model_dump_json()) == caps


def test_a_catalog_cached_before_v6_31_still_drops_temperature_but_sends_no_effort() -> None:
    old_meta = {key: value for key, value in LUNA_META.items() if key != "reasoning"}

    caps = catalog_capabilities(old_meta)

    assert caps.reasoning is True
    assert caps.reasoning_efforts is None  # unknown until the catalog refreshes
    assert reasoning_effort_to_send(caps, None) is None
    assert accepts_parameter(caps, "temperature") is False
    assert reasoning_issues(_ctx("openai/gpt-6-luna", old_meta)) == []


async def test_the_openrouter_catalog_keeps_the_reasoning_record() -> None:
    adapter = OPENROUTER_ADAPTERS["openrouter_llm_models"]
    body = {"data": [{"id": "openai/gpt-6-luna", "name": "GPT-6 Luna", "description": "prose", **LUNA_META}]}
    with respx.mock:
        respx.get("https://openrouter.ai/api/v1/key").mock(
            return_value=httpx.Response(200, json={"data": {"label": "test key"}})
        )
        respx.get(url__startswith="https://openrouter.ai/api/v1/models").mock(
            return_value=httpx.Response(200, json=body)
        )
        async with httpx.AsyncClient() as client:
            (item,) = await adapter.fetch(
                client=client, secrets={"api_key": "sk-or-v1-placeholder"}, kind="models"
            )

    assert item.meta["reasoning"] == LUNA_META["reasoning"]
    assert "description" not in item.meta
    assert catalog_capabilities(item.meta).reasoning_efforts == [
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
