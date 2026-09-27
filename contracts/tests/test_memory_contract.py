"""V5-40: the caller-memory contracts (`MemoryConfig`, the recall and console models)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from lkap_contracts.agent_config import MAX_MEMORY_RETENTION_DAYS, AgentConfig, MemoryConfig
from lkap_contracts.api_models import (
    MEMORY_FORGOTTEN_EVENT,
    MEMORY_RECALLED_EVENT,
    MEMORY_STORED_EVENT,
    MemoryPurgeIn,
    MemoryRecalledEvent,
    MemoryRecallIn,
    MemoryRecallOut,
    MemoryStoredEvent,
    SessionMemoryOut,
)

_MINIMAL: dict[str, Any] = {
    "instructions": "Be helpful.",
    "pipeline": {"mode": "realtime", "realtime": {"provider_id": "openai-realtime"}},
}


def test_agent_config_without_memory_keeps_memory_off() -> None:
    config = AgentConfig.model_validate(_MINIMAL)

    assert config.memory == MemoryConfig()
    assert config.memory.enabled is False


def test_memory_config_defaults_match_the_plan() -> None:
    memory = MemoryConfig()

    assert (memory.enabled, memory.scope, memory.retention_days) == (False, "agent", 90)
    assert (memory.consent_line, memory.max_recall_tokens, memory.verbatim) == (None, 400, False)


@pytest.mark.parametrize(
    "update",
    [
        {"retention_days": 0},
        {"retention_days": MAX_MEMORY_RETENTION_DAYS + 1},
        {"max_recall_tokens": 10},
        {"max_recall_tokens": 5000},
        {"scope": "caller"},
        {"consent_line": "x" * 501},
    ],
)
def test_memory_config_out_of_range_is_rejected(update: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        MemoryConfig.model_validate(update)


@pytest.mark.parametrize(
    ("raw", "expected"), [("   ", None), ("  We   remember calls. ", "We remember calls.")]
)
def test_memory_config_consent_line_is_normalised(raw: str, expected: str | None) -> None:
    assert MemoryConfig(consent_line=raw).consent_line == expected


def test_agent_config_round_trips_memory() -> None:
    config = AgentConfig.model_validate(
        {**_MINIMAL, "memory": {"enabled": True, "scope": "workspace", "retention_days": 30}}
    )

    again = AgentConfig.model_validate(config.model_dump(mode="json"))

    assert again.memory.enabled is True
    assert again.memory.scope == "workspace"
    assert again.memory.retention_days == 30


@pytest.mark.parametrize("number", ["+15551234567", "+442071838750"])
def test_memory_recall_in_accepts_e164(number: str) -> None:
    assert MemoryRecallIn(session_id="s1", caller_e164=number).caller_e164 == number


@pytest.mark.parametrize("number", ["5551234567", "+0123456789", "+1 555 123 4567", "tel:+15551234567"])
def test_memory_recall_in_rejects_a_non_e164_number(number: str) -> None:
    with pytest.raises(ValidationError):
        MemoryRecallIn(session_id="s1", caller_e164=number)


def test_memory_texts_are_capped() -> None:
    with pytest.raises(ValidationError):
        MemoryRecallOut(status="recalled", memories=["x" * 501])


def test_memory_event_names_are_stable() -> None:
    assert (MEMORY_RECALLED_EVENT, MEMORY_STORED_EVENT, MEMORY_FORGOTTEN_EVENT) == (
        "memory_recalled",
        "memory_stored",
        "memory_forgotten",
    )


def test_memory_event_payloads_default_to_not_forgotten() -> None:
    recalled = MemoryRecalledEvent(status="recalled", count=1, memories=["Prefers email."])
    stored = MemoryStoredEvent(status="skipped", reason="no caller identity")

    assert recalled.forgotten is False
    assert stored.forgotten is False
    assert stored.memories == []


def test_session_memory_out_minimal() -> None:
    out = SessionMemoryOut(enabled=False)

    assert out.subject_id is None
    assert out.recalled == []
    assert out.stored == []


def test_memory_purge_in_defaults_to_unconfirmed() -> None:
    assert MemoryPurgeIn().confirm is False
