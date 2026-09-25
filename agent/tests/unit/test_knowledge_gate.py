"""Knowledge auto-inject v2 (V5-06): the gate, the conversation query, dedupe, the budget, the pre-fetch."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fakes.fake_api import FakeApi, resolved_config
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
from lkap_contracts.agent_config import KnowledgeConfig, ResolvedAgentConfig
from lkap_contracts.api_models import KbHit, KbSearchOptions
from lkap_contracts.flow import FlowState

from lkap_agent.config_client import ApiKbClient
from lkap_agent.knowledge import (
    BACKCHANNEL_PHRASES,
    KNOWLEDGE_STATE_KEY,
    KnowledgePrefetch,
    KnowledgeState,
    RecentChunks,
    approx_tokens,
    build_query,
    compose_note,
    jaccard,
    knowledge_state,
    last_sentence,
    prefetch_listener,
    search_options,
    skip_reason,
)
from lkap_agent.packs.loader import NullPack
from lkap_agent.platform_agent import PlatformAgent, SessionContext

PREFIX = "Relevant knowledge from the attached documents:"


def _hit(
    chunk_id: str, text: str = "Flood damage is covered up to the limit.", filename: str = "policy.md"
) -> KbHit:
    return KbHit(chunk_id=chunk_id, document_id=f"doc-{chunk_id}", filename=filename, score=0.9, text=text)


def _with_knowledge(resolved: ResolvedAgentConfig, **update: Any) -> ResolvedAgentConfig:
    knowledge = resolved.config.knowledge.model_copy(update=update)
    return resolved.model_copy(update={"config": resolved.config.model_copy(update={"knowledge": knowledge})})


def _context(config: ResolvedAgentConfig, kb: Any, *, debounce_s: float = 0.0) -> SessionContext:
    ctx = SessionContext(
        session_id=config.session_id,
        agent_id=config.agent_id,
        pipeline_mode=config.config.pipeline.mode,
        config=config.config,
        session=cast(Any, object()),
        room=cast(rtc.Room, FakeRoom()),
        ui=FakeUiChannel(),
        frames=FakeFrameBuffer(),
        kb=kb,
        workflow_llm=FakeStructuredLLM(),
        background=FakeBackgroundRunner(),
        log=None,
    )
    ctx.userdata[KNOWLEDGE_STATE_KEY] = KnowledgeState(prefetch=KnowledgePrefetch(debounce_s=debounce_s))
    return ctx


class _ScopedAgent(PlatformAgent):
    """A flow node stand-in: its auto-inject scope is fixed at construction."""

    def __init__(self, *, scope: list[str], **kwargs: Any) -> None:
        self._scope = scope
        super().__init__(**kwargs)

    def _auto_inject_kb_ids(self) -> list[str]:
        return list(self._scope)


def _agent(
    ctx: SessionContext,
    *,
    events: list[tuple[str, dict[str, Any]]] | None = None,
    scope: list[str] | None = None,
) -> PlatformAgent:
    def record(event_type: str, payload: dict[str, Any]) -> None:
        if events is not None:
            events.append((event_type, payload))

    if scope is not None:
        return _ScopedAgent(scope=scope, ctx=ctx, pack=NullPack(), has_tts=True, record_event=record)
    return PlatformAgent(ctx=ctx, pack=NullPack(), has_tts=True, record_event=record)


async def _turn(agent: PlatformAgent, text: str, turn_ctx: ChatContext | None = None) -> ChatContext:
    turn_ctx = turn_ctx if turn_ctx is not None else ChatContext.empty()
    await agent.on_user_turn_completed(turn_ctx, llm.ChatMessage(role="user", content=[text]))
    return turn_ctx


def _notes(turn_ctx: ChatContext) -> list[str]:
    return [
        item.text_content or ""
        for item in turn_ctx.items
        if isinstance(item, llm.ChatMessage)
        and item.role == "assistant"
        and PREFIX in (item.text_content or "")
    ]


# --------------------------------------------------------------------------- the gate


@pytest.mark.parametrize(
    "text",
    [
        "yes",
        "Okay.",
        "okay thanks",
        "haan ji",
        "Theek hai",
        "accha",
        "uh huh",
        "1234",
        "4 5 6 7 8",
        "no problem",
        "",
    ],
)
def test_skip_reason_backchannels_digits_and_short_turns_skip(text: str) -> None:
    assert skip_reason(text) is not None


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", "empty"),
        ("yes yes okay", "backchannel"),
        ("haan haan theek hai", "backchannel"),
        ("1 2 3 4 5 6", "digits"),
        ("the deductible", "short"),
    ],
)
def test_skip_reason_names_why(text: str, reason: str) -> None:
    assert skip_reason(text) == reason


@pytest.mark.parametrize(
    "text", ["Is flood damage covered?", "and the deductible amount?", "my policy number is 42"]
)
def test_skip_reason_lets_a_real_question_search(text: str) -> None:
    assert skip_reason(text) is None


def test_skip_reason_with_skip_short_turns_off_only_skips_empty_turns() -> None:
    assert skip_reason("yes", skip_short_turns=False) is None
    assert skip_reason("  ", skip_short_turns=False) == "empty"


def test_backchannel_list_covers_hindi_transliterations() -> None:
    assert {"haan", "haan ji", "theek hai", "accha", "ji", "hmm"} <= BACKCHANNEL_PHRASES


def test_jaccard_is_word_overlap() -> None:
    assert jaccard("is flood covered", "Is flood covered?") == 1.0
    assert jaccard("is flood covered", "what about fire") == 0.0
    assert jaccard("", "") == 1.0


# --------------------------------------------------------------------------- the query


def test_build_query_conversation_concatenates_turn_assistant_sentence_and_variables() -> None:
    query = build_query(
        "and the deductible?",
        query_mode="conversation",
        previous_assistant="Thanks for waiting. Your home policy covers flood damage.",
        variables={"policy_type": "home", "verified": True, "claim_id": None, "count": 2},
    )

    assert query.splitlines() == ["and the deductible?", "Your home policy covers flood damage.", "home 2"]


def test_build_query_last_turn_is_the_user_words_only() -> None:
    query = build_query("and the deductible?", query_mode="last_turn", previous_assistant="Flood is covered.")
    assert query == "and the deductible?"


def test_last_sentence_without_a_break_is_the_whole_text() -> None:
    assert last_sentence("Flood is covered") == "Flood is covered"
    assert last_sentence("") == ""


# --------------------------------------------------------------------------- the note


def test_compose_note_never_exceeds_the_budget() -> None:
    hits = [_hit(f"c{i}", text="word " * 200) for i in range(4)]

    note, used = compose_note(hits, prefix=PREFIX, max_tokens=120)

    assert approx_tokens(note) <= 120
    assert used == hits[:1]
    assert note.endswith("…")


def test_compose_note_keeps_whole_hits_that_fit() -> None:
    hits = [_hit("c1", text="Flood is covered."), _hit("c2", text="Fire is covered.")]

    note, used = compose_note(hits, prefix=PREFIX, max_tokens=1200)

    assert used == hits
    assert note == f"{PREFIX}\n[policy.md] Flood is covered.\n\n[policy.md] Fire is covered."


def test_compose_note_with_no_room_is_empty() -> None:
    assert compose_note([_hit("c1")], prefix=PREFIX, max_tokens=5) == ("", [])


def test_recent_chunks_forgets_after_three_turns() -> None:
    recent = RecentChunks(turns=3)
    recent.push(["c1"])
    recent.push([])
    assert recent.seen("c1")
    recent.push([])
    assert recent.seen("c1")
    recent.push([])
    assert not recent.seen("c1")


# --------------------------------------------------------------------------- the agent


@pytest.mark.parametrize("text", ["yes", "okay", "haan ji", "12345", "the deductible"])
async def test_gated_turns_do_not_search(text: str) -> None:
    kb = FakeKbClient([_hit("c1")])
    agent = _agent(_context(resolved_config(kb_ids=["kb-1"]), kb))

    turn_ctx = await _turn(agent, text)

    assert kb.queries == []
    assert _notes(turn_ctx) == []


async def test_a_normal_turn_searches_once() -> None:
    kb = FakeKbClient([_hit("c1")])
    agent = _agent(_context(resolved_config(kb_ids=["kb-1"]), kb))

    turn_ctx = await _turn(agent, "Is flood damage covered?")

    assert len(kb.queries) == 1
    assert len(_notes(turn_ctx)) == 1


async def test_skip_short_turns_off_searches_a_backchannel() -> None:
    kb = FakeKbClient([_hit("c1")])
    agent = _agent(_context(_with_knowledge(resolved_config(kb_ids=["kb-1"]), skip_short_turns=False), kb))

    await _turn(agent, "okay")

    assert len(kb.queries) == 1


async def test_a_chunk_injected_two_turns_ago_is_not_reinjected() -> None:
    kb = FakeKbClient([_hit("c1", text="Flood is covered.")])
    agent = _agent(_context(resolved_config(kb_ids=["kb-1"]), kb))

    first = await _turn(agent, "Is flood damage covered?")
    await _turn(agent, "yes")
    kb.hits = [_hit("c1", text="Flood is covered."), _hit("c2", text="Fire is covered.")]
    third = await _turn(agent, "What about fire damage then?")

    assert "Flood is covered." in _notes(first)[0]
    (note,) = _notes(third)
    assert "Fire is covered." in note
    assert "Flood is covered." not in note


async def test_a_chunk_is_injected_again_after_the_dedupe_window() -> None:
    kb = FakeKbClient([_hit("c1")])
    agent = _agent(_context(resolved_config(kb_ids=["kb-1"]), kb))

    await _turn(agent, "Is flood damage covered?")
    for _ in range(3):
        await _turn(agent, "okay")
    again = await _turn(agent, "Is flood damage covered again?")

    assert len(_notes(again)) == 1


async def test_the_injected_note_respects_max_inject_tokens() -> None:
    kb = FakeKbClient([_hit(f"c{i}", text="coverage " * 300) for i in range(4)])
    agent = _agent(_context(_with_knowledge(resolved_config(kb_ids=["kb-1"]), max_inject_tokens=200), kb))

    turn_ctx = await _turn(agent, "Is flood damage covered?")

    (note,) = _notes(turn_ctx)
    assert approx_tokens(note) <= 200


async def test_conversation_query_uses_the_previous_assistant_sentence_and_flow_variables() -> None:
    kb = FakeKbClient([_hit("c1")])
    ctx = _context(resolved_config(kb_ids=["kb-1"]), kb)
    ctx.userdata["flow"] = FlowState(current_node="n1", path=["n1"], variables={"policy_type": "home"})
    agent = _agent(ctx)
    turn_ctx = ChatContext.empty()
    turn_ctx.add_message(role="assistant", content="Let me check. Your home policy covers flood damage.")

    await _turn(agent, "and what is the deductible?", turn_ctx)

    assert kb.queries[0][0] == "and what is the deductible?\nYour home policy covers flood damage.\nhome"


async def test_last_turn_query_mode_searches_the_user_words_only() -> None:
    kb = FakeKbClient([_hit("c1")])
    agent = _agent(_context(_with_knowledge(resolved_config(kb_ids=["kb-1"]), query_mode="last_turn"), kb))
    turn_ctx = ChatContext.empty()
    turn_ctx.add_message(role="assistant", content="Your home policy covers flood damage.")

    await _turn(agent, "and what is the deductible?", turn_ctx)

    assert kb.queries[0][0] == "and what is the deductible?"


async def test_the_knowledge_event_carries_counts_and_prefetch_hit() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    kb = FakeKbClient([_hit("c1")])
    agent = _agent(_context(resolved_config(kb_ids=["kb-1"]), kb), events=events)

    await _turn(agent, "Is flood damage covered?")

    assert events == [
        (
            "knowledge",
            {
                "hits": 1,
                "deduped": 0,
                "prefetch_hit": False,
                "prefetch_ready_before_final": None,
                "tokens": events[0][1]["tokens"],
                "chunk_ids": ["c1"],
            },
        )
    ]


# --------------------------------------------------------------------------- pre-fetch


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_prefetch_matching_final_awaits_the_finished_task_and_records_prefetch_hit() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    kb = FakeKbClient([_hit("c1")])
    agent = _agent(_context(resolved_config(kb_ids=["kb-1"]), kb), events=events)

    agent.prefetch_knowledge("Is flood damage", False)
    agent.prefetch_knowledge("Is flood damage covered", False)
    await _settle()
    assert len(kb.queries) == 1  # the debounce kept only the latest transcript
    turn_ctx = await _turn(agent, "Is flood damage covered?")

    assert len(kb.queries) == 1  # the hook reused the pre-fetch
    assert len(_notes(turn_ctx)) == 1
    assert events[0][1]["prefetch_hit"] is True


async def test_prefetch_with_a_different_final_searches_again() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    kb = FakeKbClient([_hit("c1")])
    agent = _agent(_context(resolved_config(kb_ids=["kb-1"]), kb), events=events)

    agent.prefetch_knowledge("Is flood damage covered", False)
    await _settle()
    await _turn(agent, "Actually tell me about my car insurance renewal")

    assert [q[0] for q in kb.queries] == [
        "Is flood damage covered",
        "Actually tell me about my car insurance renewal",
    ]
    assert events[0][1]["prefetch_hit"] is False


async def test_prefetch_still_debouncing_is_cancelled_and_the_hook_searches_now() -> None:
    kb = FakeKbClient([_hit("c1")])
    agent = _agent(_context(resolved_config(kb_ids=["kb-1"]), kb, debounce_s=10.0))

    agent.prefetch_knowledge("Is flood damage covered", False)
    await _settle()
    await asyncio.wait_for(_turn(agent, "Is flood damage covered?"), timeout=1)

    assert [q[0] for q in kb.queries] == ["Is flood damage covered?"]


async def test_prefetch_joins_final_segments_with_the_interim_one() -> None:
    kb = FakeKbClient([_hit("c1")])
    agent = _agent(_context(resolved_config(kb_ids=["kb-1"]), kb))

    agent.prefetch_knowledge("Is flood damage", True)
    agent.prefetch_knowledge("covered by my policy", False)
    await _settle()

    assert kb.queries[-1][0] == "Is flood damage covered by my policy"


async def test_prefetch_skips_gated_transcripts_and_disabled_prefetch() -> None:
    kb = FakeKbClient([_hit("c1")])
    agent = _agent(_context(resolved_config(kb_ids=["kb-1"]), kb))
    agent.prefetch_knowledge("okay", False)
    await _settle()

    off = _agent(_context(_with_knowledge(resolved_config(kb_ids=["kb-1"]), prefetch=False), kb))
    off.prefetch_knowledge("Is flood damage covered", False)
    await _settle()

    assert kb.queries == []


async def test_a_flow_handoff_between_interim_and_final_uses_the_new_nodes_scope() -> None:
    kb = FakeKbClient([_hit("c1")])
    ctx = _context(resolved_config(kb_ids=["kb-1", "kb-2"]), kb)
    intake = _agent(ctx, scope=["kb-1"])
    claims = _agent(ctx, scope=["kb-2"])
    session = SimpleNamespace(current_agent=intake)
    listener = prefetch_listener(session)

    listener(SimpleNamespace(transcript="Is flood damage covered", is_final=False))
    await _settle()
    session.current_agent = claims  # the handoff lands before the final transcript
    await _turn(claims, "Is flood damage covered?")

    assert len(kb.queries) == 2  # the pre-fetch (old scope) was not reused


async def test_prefetch_listener_ignores_agents_without_the_hook() -> None:
    listener = prefetch_listener(SimpleNamespace(current_agent=object()))
    listener(SimpleNamespace(transcript="Is flood damage covered", is_final=False))


def test_knowledge_state_is_session_scoped_in_userdata() -> None:
    userdata: dict[str, Any] = {}
    assert knowledge_state(userdata) is knowledge_state(userdata)


# --------------------------------------------------------------------------- search options


def test_search_options_follow_the_knowledge_config() -> None:
    assert search_options(KnowledgeConfig()) == KbSearchOptions(mode="hybrid", rerank="none", min_score=None)
    assert search_options(KnowledgeConfig(mode="vector", rerank="local", min_score=0.5)) == KbSearchOptions(
        mode="vector", rerank="local", min_score=0.5
    )


def test_search_options_ignore_values_the_platform_cannot_honour() -> None:
    options = search_options(KnowledgeConfig(rerank="connection:abc", min_score=1.5))
    assert (options.rerank, options.min_score) == ("none", None)


async def test_api_kb_client_sends_the_bound_options() -> None:
    client = FakeApi()
    kb = ApiKbClient(client, ["kb-1"], options=KbSearchOptions(mode="hybrid", rerank="local", min_score=0.3))

    await kb.search("Is flood damage covered?", k=3)

    assert client.kb_options == [KbSearchOptions(mode="hybrid", rerank="local", min_score=0.3)]


async def test_prefetch_ready_before_final_compares_the_answer_with_the_last_final_segment() -> None:
    """The spike's live measurement: did the pre-fetch answer before the SDK's snapshot moment?"""
    events: list[tuple[str, dict[str, Any]]] = []
    kb = FakeKbClient([_hit("c1")])
    agent = _agent(_context(resolved_config(kb_ids=["kb-1"]), kb), events=events)

    # Answered before the final segment: it could have been in the preemptive snapshot.
    agent.prefetch_knowledge("Is flood damage covered", False)
    await _settle()
    agent.prefetch_knowledge("Is flood damage covered", True)
    await _turn(agent, "Is flood damage covered?")
    # The final segment came first and the search answered after it: too late for the snapshot.
    kb.hits = [_hit("c2", text="Fire is covered.")]
    agent.prefetch_knowledge("What about fire damage then", True)
    await _settle()
    await _turn(agent, "What about fire damage then?")

    assert [e[1]["prefetch_hit"] for e in events] == [True, True]
    assert [e[1]["prefetch_ready_before_final"] for e in events] == [True, False]
