"""V5-39: the guardrails contracts (`GuardrailsConfig`, the rule union, the events, `ActivityEvent.kind`)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from lkap_contracts.agent_config import AgentConfig, ResolvedAgentConfig
from lkap_contracts.guardrails import (
    DEFAULT_GUARDRAIL_BUDGET_MS,
    DEFAULT_SAFE_REPLY,
    GUARDRAIL_EVENT,
    GUARDRAIL_TIMEOUT_EVENT,
    MAX_GUARDRAIL_RULES,
    MODERATION_CATEGORIES,
    ClassifierRule,
    GuardrailEvent,
    GuardrailsConfig,
    GuardrailTimeoutEvent,
    ProviderRule,
    RegexRule,
)
from lkap_contracts.ui_protocol import ActivityEvent

_MINIMAL: dict[str, Any] = {
    "instructions": "Be helpful.",
    "pipeline": {"mode": "realtime", "realtime": {"provider_id": "openai-realtime"}},
}


def test_agent_config_saved_before_guardrails_has_no_rules() -> None:
    config = AgentConfig.model_validate(_MINIMAL)

    assert config.guardrails == GuardrailsConfig()
    assert config.guardrails.active is False


def test_guardrails_defaults_match_the_card() -> None:
    config = GuardrailsConfig()

    assert (config.input, config.output, config.tool_output) == ([], [], [])
    assert config.on_trip == "interrupt"
    assert config.safe_reply == DEFAULT_SAFE_REPLY
    assert config.model is None
    assert config.budget_ms == DEFAULT_GUARDRAIL_BUDGET_MS == 300


def test_rules_are_told_apart_by_kind() -> None:
    config = GuardrailsConfig.model_validate(
        {
            "input": [{"kind": "regex", "name": "Card numbers", "pattern": r"\b(?:\d[ -]?){13,19}\b"}],
            "output": [
                {"kind": "classifier", "name": "No medical advice", "prompt": "Gives medical advice."}
            ],
            "tool_output": [{"kind": "provider", "name": "Harmful", "categories": ["hate", "violence"]}],
        }
    )

    assert isinstance(config.input[0], RegexRule)
    assert isinstance(config.output[0], ClassifierRule)
    assert isinstance(config.tool_output[0], ProviderRule)
    assert config.tool_output[0].provider == "openai_moderation"
    assert config.rules("output") == config.output
    assert config.active is True


def test_rule_names_are_cleaned_and_unique_per_stage() -> None:
    rule = RegexRule(name="  Card   numbers ", pattern="x")
    assert rule.name == "Card numbers"

    with pytest.raises(ValidationError, match="called"):
        GuardrailsConfig.model_validate(
            {
                "input": [
                    {"kind": "regex", "name": "Cards", "pattern": "a"},
                    {"kind": "classifier", "name": "cards", "prompt": "b"},
                ]
            }
        )
    # The same name on two stages is fine.
    GuardrailsConfig.model_validate(
        {
            "input": [{"kind": "regex", "name": "Cards", "pattern": "a"}],
            "output": [{"kind": "regex", "name": "Cards", "pattern": "a"}],
        }
    )


@pytest.mark.parametrize(
    "raw",
    [
        {"input": [{"kind": "regex", "name": "x", "pattern": ""}]},
        {"input": [{"kind": "regex", "name": "   ", "pattern": "a"}]},
        {"input": [{"kind": "regex", "name": "x", "pattern": "a" * 301}]},
        {"output": [{"kind": "classifier", "name": "x", "prompt": ""}]},
        {"output": [{"kind": "provider", "name": "x", "categories": ["rude"]}]},
        {"output": [{"kind": "provider", "name": "x", "provider": "azure"}]},
        {"output": [{"kind": "unknown", "name": "x"}]},
        {"on_trip": "hang_up"},
        {"safe_reply": "   "},
        {"safe_reply": "x" * 501},
        {"budget_ms": 10},
        {
            "input": [
                {"kind": "regex", "name": f"r{i}", "pattern": "a"} for i in range(MAX_GUARDRAIL_RULES + 1)
            ]
        },
    ],
)
def test_invalid_guardrails_are_refused(raw: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        GuardrailsConfig.model_validate(raw)


def test_provider_rule_dedupes_categories_and_knows_the_service_list() -> None:
    rule = ProviderRule(name="Harmful", categories=["hate", "hate", "violence"])

    assert rule.categories == ["hate", "violence"]
    assert len(MODERATION_CATEGORIES) == 13
    assert "self-harm/intent" in MODERATION_CATEGORIES


def test_events_have_the_card_fields() -> None:
    event = GuardrailEvent(
        stage="input", rule="Card numbers", kind="regex", action="interrupt", excerpt_hash="ab" * 8
    )
    dumped = event.model_dump(mode="json")

    assert GUARDRAIL_EVENT == "guardrail"
    assert {"stage", "rule", "action", "excerpt_hash"} <= dumped.keys()
    assert dumped["excerpt"] is None
    timeout = GuardrailTimeoutEvent(
        stage="output", rule="x", kind="classifier", reason="timeout", budget_ms=300
    )
    assert GUARDRAIL_TIMEOUT_EVENT == "guardrail_timeout"
    assert timeout.model_dump()["reason"] == "timeout"


def test_activity_event_kind_is_optional_and_defaults_to_none() -> None:
    row = ActivityEvent(id="a", ts=1.0, source="lookup", label="Lookup", phase="done", headline="ok")
    assert row.kind is None

    trip = ActivityEvent(
        id="g", ts=1.0, source="guardrail", label="Guardrail", phase="done", headline="x", kind="guardrail"
    )
    assert trip.model_dump()["kind"] == "guardrail"


def test_resolved_config_accepts_the_guardrail_builtin_slots() -> None:
    field = ResolvedAgentConfig.model_fields["builtin_providers"]
    adapter: TypeAdapter[Any] = TypeAdapter(field.annotation)
    raw = {
        "guardrails_llm": {
            "provider_id": "openai-llm",
            "python_class": "x",
            "model": "gpt-4.1-mini",
            "kwargs": {},
        },
        "guardrails_moderation": {
            "provider_id": "openai-llm",
            "python_class": "x",
            "model": None,
            "kwargs": {},
        },
    }

    assert set(adapter.validate_python(raw)) == {"guardrails_llm", "guardrails_moderation"}
