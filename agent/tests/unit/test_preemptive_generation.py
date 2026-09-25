"""Knowledge auto-inject turns preemptive generation off (research-v4 knowledge-and-memory P0-0).

Auto-inject edits the chat context in `on_user_turn_completed`, which makes
livekit-agents 1.8.2 discard its preemptive reply on every turn with a hit, so
the builder switches preemptive generation off for such a session unless the
agent's `pipeline.turn_handling` sets it explicitly.

V5-06 adds the pre-fetch spike's tests at the end: the SDK predicate that
decides whether a preemptive reply survives, and the behaviour that shipped
(the rule stands; `docs/v5/_briefs/v5-06-spike.md`).
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from fakes.fake_api import resolved_config
from fakes.fake_ctx import (
    FakeBackgroundRunner,
    FakeFrameBuffer,
    FakeKbClient,
    FakeStructuredLLM,
    FakeUiChannel,
)
from fakes.fake_room import FakeRoom
from livekit import rtc
from livekit.agents import ChatContext, llm
from lkap_contracts.agent_config import ResolvedAgentConfig
from lkap_contracts.api_models import KbHit

from lkap_agent.knowledge import KNOWLEDGE_STATE_KEY, KnowledgePrefetch, KnowledgeState
from lkap_agent.packs.loader import NullPack
from lkap_agent.platform_agent import PlatformAgent, SessionContext
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


# --------------------------------------------------------------------------- V5-06 spike
# livekit-agents 1.8.3 keeps a preemptive reply only when the context the hook leaves
# equals the snapshot it took: `on_preemptive_generation` copies `Agent.chat_ctx` when an
# STT final segment arrives, `_user_turn_completed_task` copies it again for the hook, and
# the reply survives iff `snapshot.is_equivalent(hook_ctx)` (item ids and content). These
# tests replay that predicate on real `ChatContext`s.

_NOTE = "Relevant knowledge from the attached documents:\n[a.md] Flood is covered."


def _agent_ctx() -> ChatContext:
    ctx = ChatContext.empty()
    ctx.add_message(role="assistant", content="Hello, how can I help?")
    ctx.add_message(role="user", content="I have a question about my policy.")
    ctx.add_message(role="assistant", content="Sure, go ahead.")
    return ctx


def test_spike_a_note_added_in_the_hook_discards_the_preemptive_reply() -> None:
    """Today's (and the shipped) path: the note goes into the per-turn copy, so the reply is discarded."""
    agent_ctx = _agent_ctx()
    snapshot = agent_ctx.copy()  # on_preemptive_generation
    hook_ctx = agent_ctx.copy()  # _user_turn_completed_task
    hook_ctx.add_message(role="assistant", content=_NOTE)

    assert not snapshot.is_equivalent(hook_ctx)


def test_spike_a_note_committed_before_the_snapshot_keeps_the_preemptive_reply() -> None:
    """The mechanism the spike tried: `update_chat_ctx` during the interim phase, nothing in the hook."""
    agent_ctx = _agent_ctx()
    agent_ctx.add_message(role="assistant", content=_NOTE)
    snapshot = agent_ctx.copy()
    hook_ctx = agent_ctx.copy()

    assert snapshot.is_equivalent(hook_ctx)


def test_spike_a_note_committed_after_the_snapshot_still_discards_it() -> None:
    """The race the spike loses: the note lands after the final segment the snapshot was taken on."""
    agent_ctx = _agent_ctx()
    snapshot = agent_ctx.copy()
    agent_ctx.add_message(role="assistant", content=_NOTE)
    hook_ctx = agent_ctx.copy()

    assert not snapshot.is_equivalent(hook_ctx)


async def test_spike_verdict_prefetch_never_writes_the_agent_chat_context() -> None:
    """The shipped behaviour: the pre-fetch only fetches; the note joins the per-turn copy in the hook."""
    kb = FakeKbClient([KbHit(chunk_id="c1", document_id="d1", filename="a.md", score=0.9, text="Flood.")])
    resolved = resolved_config(auto_inject=True, kb_ids=["kb-1"])
    ctx = SessionContext(
        session_id=resolved.session_id,
        agent_id=resolved.agent_id,
        pipeline_mode=resolved.config.pipeline.mode,
        config=resolved.config,
        session=cast(Any, object()),
        room=cast(rtc.Room, FakeRoom()),
        ui=FakeUiChannel(),
        frames=FakeFrameBuffer(),
        kb=kb,
        workflow_llm=FakeStructuredLLM(),
        background=FakeBackgroundRunner(),
        log=None,
    )
    ctx.userdata[KNOWLEDGE_STATE_KEY] = KnowledgeState(prefetch=KnowledgePrefetch(debounce_s=0.0))
    agent = PlatformAgent(ctx=ctx, pack=NullPack(), has_tts=True)
    before = agent.chat_ctx.copy()

    agent.prefetch_knowledge("Is flood damage covered", False)
    for _ in range(5):
        await asyncio.sleep(0)
    snapshot = agent.chat_ctx.copy()
    hook_ctx = agent.chat_ctx.copy()
    message = llm.ChatMessage(role="user", content=["Is flood damage covered?"])
    await agent.on_user_turn_completed(hook_ctx, message)

    assert len(kb.queries) == 1  # the hook reused the pre-fetch
    assert agent.chat_ctx.is_equivalent(before)
    assert not snapshot.is_equivalent(hook_ctx)  # the note is in this turn's copy only


@pytest.mark.parametrize("prefetch", [True, False])
async def test_spike_verdict_auto_inject_keeps_preemptive_off_with_or_without_prefetch(
    prefetch: bool,
) -> None:
    """The P0-0 rule stands for `prefetch=True` agents too: the spike did not lift it."""
    resolved = resolved_config(auto_inject=True, kb_ids=["kb-1"])
    knowledge = resolved.config.knowledge.model_copy(update={"prefetch": prefetch})
    resolved = resolved.model_copy(
        update={"config": resolved.config.model_copy(update={"knowledge": knowledge})}
    )

    plan = SessionBuilder().build(resolved, _providers())

    assert plan.session.options.preemptive_generation["enabled"] is False
