"""V5-29: `AgentConfig.tests`, `publish_gate`, `ResolvedAgentConfig.tool_mocks` and the run models."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from lkap_contracts.agent_config import AgentConfig, PipelineConfig, ResolvedAgentConfig
from lkap_contracts.agent_tests import (
    AGENT_TEST_JUDGES,
    AgentTest,
    AgentTestRun,
    PublishGate,
    PublishGateRefusal,
)


def _case(**overrides: Any) -> dict[str, Any]:
    case: dict[str, Any] = {
        "id": "booking",
        "name": "Books a table",
        "persona_instructions": "You are a polite caller who wants a table for two.",
        "scenario": "Book dinner for two on Friday at 19:00.",
        "expectations": ["The agent confirms the date and time back."],
    }
    case.update(overrides)
    return case


def _config(**kwargs: Any) -> AgentConfig:
    return AgentConfig(instructions="Be brief.", pipeline=PipelineConfig(), **kwargs)


def test_agent_config_defaults_have_no_tests_and_no_gate() -> None:
    config = _config()

    assert config.tests == []
    assert config.publish_gate == PublishGate(require_tests=False, min_pass_ratio=1.0)


def test_agent_config_saved_before_v5_29_validates_unchanged() -> None:
    """Compatibility: a stored config without the fields dumps with the defaults."""
    stored = {"instructions": "Hi", "pipeline": {"mode": "cascaded"}}

    config = AgentConfig.model_validate(stored)

    assert config.tests == []
    assert config.publish_gate.require_tests is False


def test_agent_test_defaults() -> None:
    case = AgentTest.model_validate(_case())

    assert case.max_turns == 12
    assert case.mocks == {}


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"id": "has space"}, "id"),
        ({"persona_instructions": ""}, "persona_instructions"),
        ({"max_turns": 0}, "max_turns"),
        ({"max_turns": 41}, "max_turns"),
        ({"expectations": ["  "]}, "cannot be empty"),
        ({"expectations": ["x" * 501]}, "500"),
        ({"mocks": {"not a name": {}}}, "not a tool name"),
    ],
)
def test_agent_test_rejects_bad_fields(overrides: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        AgentTest.model_validate(_case(**overrides))

    assert fragment in str(excinfo.value)


def test_agent_config_refuses_duplicate_test_ids() -> None:
    with pytest.raises(ValidationError, match="test ids must be unique: booking"):
        _config(tests=[_case(), _case(name="Another")])


@pytest.mark.parametrize("ratio", [-0.1, 1.5])
def test_publish_gate_ratio_is_bounded(ratio: float) -> None:
    with pytest.raises(ValidationError):
        PublishGate(require_tests=True, min_pass_ratio=ratio)


def test_mocks_keep_any_json_fixture() -> None:
    case = AgentTest.model_validate(_case(mocks={"check_slots": {"slots": ["19:00"]}, "ping": "pong"}))

    assert case.mocks == {"check_slots": {"slots": ["19:00"]}, "ping": "pong"}


def test_resolved_config_tool_mocks_default_empty() -> None:
    resolved = ResolvedAgentConfig(
        session_id="s",
        agent_id="a",
        agent_slug="a",
        config_version=1,
        pack_id="generic",
        ui_panel_id="composite",
        config=_config(),
        resolved={},
        tools=[],
        kb_ids=[],
        participant_identity="p",
    )

    assert resolved.tool_mocks == {}


def test_five_judges_in_order() -> None:
    assert AGENT_TEST_JUDGES == ("task_completion", "tool_use", "safety", "relevancy", "accuracy")


def test_run_and_refusal_models_round_trip() -> None:
    run = AgentTestRun.model_validate(
        {
            "id": "r1",
            "agent_id": "a1",
            "config_version": 3,
            "status": "error",
            "created_at": "2026-09-27T10:00:00Z",
            "error": "no worker is running for this agent's connection",
        }
    )
    refusal = PublishGateRefusal(
        reason="error", config_version=3, min_pass_ratio=1.0, run_id=run.id, error=run.error
    )

    assert run.verdicts == []
    assert refusal.model_dump()["reason"] == "error"
