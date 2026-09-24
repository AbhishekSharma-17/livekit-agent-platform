"""Knowledge auto-inject turns preemptive generation off (research-v4 knowledge-and-memory P0-0).

Auto-inject edits the chat context in `on_user_turn_completed`, which makes
livekit-agents 1.8.2 discard its preemptive reply on every turn with a hit, so
the builder switches preemptive generation off for such a session unless the
agent's `pipeline.turn_handling` sets it explicitly.
"""

from __future__ import annotations

from typing import Any

import pytest
from fakes.fake_api import resolved_config
from lkap_contracts.agent_config import ResolvedAgentConfig

from lkap_agent.providers.factory import BuiltProviders
from lkap_agent.session_builder import SessionBuilder, auto_inject_active, build_turn_handling


def _providers() -> BuiltProviders:
    return BuiltProviders(stt=object(), llm=object(), tts=object())


def _with_turn_handling(resolved: ResolvedAgentConfig, turn_handling: dict[str, Any]) -> ResolvedAgentConfig:
    pipeline = resolved.config.pipeline.model_copy(update={"turn_handling": turn_handling})
    config = resolved.config.model_copy(update={"pipeline": pipeline})
    return resolved.model_copy(update={"config": config})


@pytest.mark.parametrize(
    ("auto_inject", "kb_ids", "expected_enabled"),
    [
        (True, ["kb-1"], False),
        (False, ["kb-1"], True),
        (True, [], True),
        (False, [], True),
    ],
)
async def test_build_auto_inject_with_a_knowledge_base_disables_preemptive_generation(
    auto_inject: bool, kb_ids: list[str], expected_enabled: bool
) -> None:
    resolved = resolved_config(auto_inject=auto_inject, kb_ids=kb_ids)

    plan = SessionBuilder().build(resolved, _providers())

    assert auto_inject_active(resolved.config) is (not expected_enabled)
    assert plan.session.options.preemptive_generation["enabled"] is expected_enabled


async def test_build_auto_inject_on_keeps_the_other_preemptive_defaults() -> None:
    plan = SessionBuilder().build(resolved_config(auto_inject=True, kb_ids=["kb-1"]), _providers())

    options = plan.session.options.preemptive_generation
    assert options["enabled"] is False
    # The SDK's own defaults still fill in the rest (`_resolve_preemptive_generation`).
    assert options["preemptive_tts"] is False
    assert options["max_retries"] == 3


async def test_build_auto_inject_on_respects_an_explicit_enabled_in_the_config() -> None:
    resolved = _with_turn_handling(
        resolved_config(auto_inject=True, kb_ids=["kb-1"]),
        {"preemptive_generation": {"enabled": True, "max_retries": 1}},
    )

    plan = SessionBuilder().build(resolved, _providers())

    options = plan.session.options.preemptive_generation
    assert options["enabled"] is True
    assert options["max_retries"] == 1


def test_build_turn_handling_disable_preemptive_keeps_other_configured_keys() -> None:
    options = build_turn_handling(
        {"preemptive_generation": {"preemptive_tts": True}},
        allow_interruptions=True,
        turn_detector=None,
        disable_preemptive=True,
    )

    assert options["preemptive_generation"] == {"preemptive_tts": True, "enabled": False}


def test_build_turn_handling_without_disable_preemptive_sets_nothing() -> None:
    options = build_turn_handling({}, allow_interruptions=True, turn_detector=None)

    assert "preemptive_generation" not in options
