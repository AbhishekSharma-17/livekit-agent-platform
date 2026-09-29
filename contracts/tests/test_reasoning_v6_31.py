"""V6-31: the reasoning part of the model capability view and the effort rule."""

import pytest

from lkap_contracts.providers import (
    OPTIONAL_REQUEST_PARAMETERS,
    REASONING_EFFORT_FIELD,
    REASONING_EFFORTS,
    REGISTRY,
    ModelCapabilities,
    accepts_parameter,
    get,
    lowest_reasoning_effort,
    reasoning_effort_to_send,
)

#: OpenRouter's record for `openai/gpt-6-luna` (2026-09-29), as the capability view reads it.
LUNA = ModelCapabilities(
    tools=True,
    reasoning=True,
    reasoning_efforts=["none", "low", "medium", "high", "xhigh", "max"],
    request_parameters=[
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
)
GEMINI_FLASH = ModelCapabilities(
    reasoning=True,
    reasoning_efforts=["minimal", "low", "medium", "high"],
    request_parameters=["reasoning", "reasoning_effort", "temperature", "tools", "tool_choice"],
)
PLAIN = ModelCapabilities(
    reasoning=False,
    reasoning_efforts=[],
    request_parameters=["temperature", "tools", "tool_choice", "top_p"],
)
NO_EFFORT_CONTROL = ModelCapabilities(
    reasoning=True, reasoning_efforts=[], request_parameters=["reasoning", "tools"]
)


@pytest.mark.parametrize(
    ("capabilities", "requested", "expected"),
    [
        (None, None, None),
        (None, "high", "high"),
        (ModelCapabilities(vision=True), "low", "low"),
        (LUNA, None, "none"),
        (LUNA, "", "none"),
        (LUNA, "low", "low"),
        (LUNA, "minimal", "low"),
        (GEMINI_FLASH, None, "minimal"),
        (GEMINI_FLASH, "none", "minimal"),
        (GEMINI_FLASH, "max", "high"),
        (PLAIN, None, None),
        (PLAIN, "low", None),
        (NO_EFFORT_CONTROL, None, None),
        (NO_EFFORT_CONTROL, "low", None),
        (
            ModelCapabilities(reasoning=True, reasoning_efforts=["low"], request_parameters=["tools"]),
            "low",
            None,
        ),
    ],
)
def test_reasoning_effort_to_send_follows_the_capability_view(
    capabilities: ModelCapabilities | None, requested: str | None, expected: str | None
) -> None:
    assert reasoning_effort_to_send(capabilities, requested) == expected


def test_accepts_parameter_is_unknown_without_a_parameter_list() -> None:
    assert accepts_parameter(None, "temperature") is None
    assert accepts_parameter(ModelCapabilities(reasoning=True), "temperature") is None
    assert accepts_parameter(LUNA, "temperature") is False
    assert accepts_parameter(LUNA, "parallel_tool_calls") is False
    assert accepts_parameter(PLAIN, "temperature") is True


def test_lowest_reasoning_effort_uses_the_canonical_order() -> None:
    assert lowest_reasoning_effort(["high", "low", "minimal"]) == "minimal"
    assert lowest_reasoning_effort([]) is None
    assert lowest_reasoning_effort(None) is None


def test_tools_are_never_an_optional_parameter() -> None:
    assert "tools" not in OPTIONAL_REQUEST_PARAMETERS
    assert {"temperature", "parallel_tool_calls", "reasoning_effort"} <= OPTIONAL_REQUEST_PARAMETERS


def test_the_effort_field_is_on_the_llms_whose_plugin_sends_it_and_has_no_default() -> None:
    with_field = sorted(
        spec.id for spec in REGISTRY if any(f.name == REASONING_EFFORT_FIELD for f in spec.fields)
    )
    assert with_field == ["livekit-inference-llm", "openai-llm", "openrouter-llm"]
    for provider_id in with_field:
        field = next(f for f in get(provider_id).fields if f.name == REASONING_EFFORT_FIELD)
        assert field.type == "enum"
        assert field.default is None
        assert field.recommended is None
        assert field.options == list(REASONING_EFFORTS)


def test_registry_reasoning_annotations_are_consistent() -> None:
    for spec in REGISTRY:
        for model in spec.models:
            if model.reasoning_efforts:
                assert model.reasoning is True, (spec.id, model.id)
                assert model.reasoning_efforts == [
                    e for e in REASONING_EFFORTS if e in model.reasoning_efforts
                ]
            if model.reasoning is not None:
                assert spec.kind == "llm", (spec.id, model.id)


def test_openrouter_suggestions_never_claim_reasoning_so_the_live_catalog_decides() -> None:
    assert all(model.reasoning is not True for model in get("openrouter-llm").models)
