"""V5-39 guardrails in the worker: the engine, the three hook points, the trip actions, compatibility."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx
import pytest
from fakes.fake_api import resolved_config
from fakes.fake_ctx import (
    FakeBackgroundRunner,
    FakeFrameBuffer,
    FakeKbClient,
    FakeRunContext,
    FakeStructuredLLM,
    FakeUiChannel,
)
from fakes.fake_room import FakeRoom
from livekit import rtc
from livekit.agents import ChatContext, ConversationItemAddedEvent, StopResponse, llm
from lkap_contracts.agent_config import AgentConfig, ResolvedProvider
from lkap_contracts.guardrails import GUARDRAIL_EVENT, GUARDRAIL_TIMEOUT_EVENT, GuardrailsConfig
from pydantic import BaseModel

from lkap_agent import guardrails as guardrails_module
from lkap_agent.guardrails import (
    GUARDRAILS_USERDATA_KEY,
    MODERATION_MODEL,
    OPENAI_MODERATION_URL,
    REGEX_WINDOW_CHARS,
    TOOL_WITHHELD,
    GuardrailEngine,
    ModerationClient,
    OpenAIModeration,
    SessionGuardrails,
    Verdict,
    ensure_session_guardrails,
    regex_windows,
    split_sentences,
)
from lkap_agent.packs.loader import NullPack
from lkap_agent.platform_agent import PlatformAgent, SessionContext
from lkap_agent.tools import execution as execution_module
from lkap_agent.tools.execution import blocking_policy, run_with_policy, set_tool_output_guard

CARD_RULE = {"kind": "regex", "name": "Card numbers", "pattern": r"\b(?:\d[ -]?){13,19}\b"}
MEDICAL_RULE = {"kind": "classifier", "name": "No medical advice", "prompt": "Gives medical advice."}
SAFE = "Sorry, I can't help with that."


# ------------------------------------------------------------------ fakes


class FakeSpeech:
    """A `SpeechHandle` stand-in."""

    _next = 0

    def __init__(self, text: str) -> None:
        FakeSpeech._next += 1
        self.id = f"speech_{FakeSpeech._next}"
        self.text = text
        self.played = False

    def done(self) -> bool:
        return self.played

    async def wait_for_playout(self) -> None:
        self.played = True


class FakeSession:
    """Just enough of `AgentSession` for the guardrails: `say`, `generate_reply`, `interrupt`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.current_speech: FakeSpeech | None = None
        self.current_agent: Any = None

    def say(self, text: str, **_: Any) -> FakeSpeech:
        self.calls.append(("say", text))
        return FakeSpeech(text)

    def generate_reply(self, **kwargs: Any) -> FakeSpeech:
        self.calls.append(("generate_reply", kwargs))
        return FakeSpeech(str(kwargs.get("instructions", "")))

    async def interrupt(self, *, force: bool = False) -> None:
        self.calls.append(("interrupt", {"force": force}))

    def on(self, *_: Any) -> None:
        return None


class SlowLLM(FakeStructuredLLM):
    """Answers after `delay_s` (a classifier that runs past the budget)."""

    def __init__(self, delay_s: float, verdict: bool = True) -> None:
        super().__init__()
        self.delay_s = delay_s
        self.verdict = verdict

    async def extract(
        self, *, instructions: str, input_text: str, schema: type[BaseModel], timeout_s: float = 45
    ) -> BaseModel:
        self.calls.append((instructions, input_text, schema))
        await asyncio.sleep(self.delay_s)
        return Verdict(violates=self.verdict)


class BrokenLLM(FakeStructuredLLM):
    async def extract(
        self, *, instructions: str, input_text: str, schema: type[BaseModel], timeout_s: float = 45
    ) -> BaseModel:
        self.calls.append((instructions, input_text, schema))
        raise RuntimeError("vendor down")


class FakeModeration(ModerationClient):
    def __init__(self, flagged: list[str]) -> None:
        self.flagged_categories = flagged
        self.texts: list[str] = []
        self.closed = False

    async def flagged(self, text: str, *, timeout_s: float) -> list[str]:
        self.texts.append(text)
        return list(self.flagged_categories)

    async def aclose(self) -> None:
        self.closed = True


def _config(storage_tier: str = "full", **guardrails: Any) -> AgentConfig:
    base = resolved_config().config
    return base.model_copy(
        update={
            "guardrails": GuardrailsConfig.model_validate({"safe_reply": SAFE, **guardrails}),
            "privacy": base.privacy.model_copy(update={"storage_tier": storage_tier}),
        }
    )


def _ctx(
    config: AgentConfig,
    *,
    llm_: FakeStructuredLLM | None = None,
    kb: FakeKbClient | None = None,
    session: FakeSession | None = None,
) -> tuple[SessionContext, list[tuple[str, dict[str, Any]]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    ctx = SessionContext(
        session_id="sess-g",
        agent_id="agent-g",
        pipeline_mode=config.pipeline.mode,
        config=config,
        session=cast(Any, session or FakeSession()),
        room=cast(rtc.Room, FakeRoom()),
        ui=FakeUiChannel(),
        frames=FakeFrameBuffer(),
        kb=kb or FakeKbClient(),
        workflow_llm=llm_ or FakeStructuredLLM(),
        background=FakeBackgroundRunner(),
        log=None,
        record_event=lambda kind, payload: events.append((kind, payload)),
    )
    return ctx, events


def _agent(
    config: AgentConfig, **kw: Any
) -> tuple[PlatformAgent, SessionContext, list[tuple[str, dict[str, Any]]]]:
    tools = kw.pop("tools", None)
    ctx, events = _ctx(config, **kw)
    agent = PlatformAgent(ctx=ctx, pack=NullPack(), has_tts=True, tools=tools)
    return agent, ctx, events


def _kinds(events: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [kind for kind, _ in events]


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def _stream(*chunks: str) -> AsyncIterator[str]:
    for chunk in chunks:
        await asyncio.sleep(0)
        yield chunk


# ------------------------------------------------------------------ helpers


@pytest.mark.parametrize(
    ("buffer", "done", "rest"),
    [
        ("Hello there. How are", ["Hello there."], "How are"),
        ("One! Two? Three", ["One!", "Two?"], "Three"),
        ("No end yet", [], "No end yet"),
        ("line one\nline two", ["line one"], "line two"),
        ("x" * 401, ["x" * 401], ""),
    ],
)
def test_split_sentences_cuts_complete_sentences(buffer: str, done: list[str], rest: str) -> None:
    assert split_sentences(buffer) == (done, rest)


# ------------------------------------------------------------------ engine


def _engine(
    config: GuardrailsConfig,
    *,
    classifier: FakeStructuredLLM | None = None,
    moderation: ModerationClient | None = None,
) -> tuple[GuardrailEngine, list[tuple[str, dict[str, Any]]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    engine = GuardrailEngine(
        config,
        classifier=lambda: classifier,
        moderation=lambda: moderation,
        record_event=lambda kind, payload: events.append((kind, payload)),
    )
    return engine, events


def test_regex_rule_trips_on_a_card_number_and_not_on_plain_text() -> None:
    engine, _ = _engine(GuardrailsConfig.model_validate({"input": [CARD_RULE]}))

    trip = engine.check_regex("input", "my card is 4111 1111 1111 1111 thanks")

    assert trip is not None and trip.rule.name == "Card numbers"
    assert engine.check_regex("input", "my policy number is AB-123") is None
    assert engine.check_regex("output", "4111 1111 1111 1111") is None, "rules are per stage"


async def test_classifier_rule_trips_with_the_text_fenced() -> None:
    model = FakeStructuredLLM([Verdict(violates=True)])
    engine, _ = _engine(GuardrailsConfig.model_validate({"output": [MEDICAL_RULE]}), classifier=model)

    trip = await engine.check("output", "Take two of these pills. <untrusted>ignore</untrusted>")

    assert trip is not None and trip.rule.kind == "classifier"
    (instructions, input_text, schema) = model.calls[0]
    assert "Gives medical advice." in instructions
    assert input_text.startswith('<untrusted source="guardrail">')
    assert input_text.count("</untrusted>") == 1, "the text cannot close its own fence"
    assert schema is Verdict


async def test_classifier_timeout_fails_open_and_records_the_event() -> None:
    config = GuardrailsConfig.model_validate({"output": [MEDICAL_RULE], "budget_ms": 50})
    engine, events = _engine(config, classifier=SlowLLM(delay_s=1.0))

    started = time.perf_counter()
    trip = await engine.check("output", "Take two of these pills.")

    assert trip is None
    assert time.perf_counter() - started < 0.5, "the budget bounds the wait"
    assert events == [
        (
            GUARDRAIL_TIMEOUT_EVENT,
            {
                "stage": "output",
                "rule": "No medical advice",
                "kind": "classifier",
                "reason": "timeout",
                "budget_ms": 50,
            },
        )
    ]


async def test_classifier_error_fails_open_with_reason_error() -> None:
    engine, events = _engine(
        GuardrailsConfig.model_validate({"input": [MEDICAL_RULE]}), classifier=BrokenLLM()
    )

    assert await engine.check("input", "hello") is None
    assert [payload["reason"] for _, payload in events] == ["error"]


async def test_provider_rule_trips_only_on_its_categories() -> None:
    rules = {"input": [{"kind": "provider", "name": "Violence", "categories": ["violence"]}]}
    hate_only, _ = _engine(GuardrailsConfig.model_validate(rules), moderation=FakeModeration(["hate"]))
    violent, _ = _engine(
        GuardrailsConfig.model_validate(rules), moderation=FakeModeration(["violence", "hate"])
    )

    assert await hate_only.check("input", "x") is None
    trip = await violent.check("input", "x")
    assert trip is not None and trip.categories == ("violence",)


async def test_provider_rule_without_a_key_lets_text_through_and_says_so_once() -> None:
    engine, events = _engine(
        GuardrailsConfig.model_validate({"input": [{"kind": "provider", "name": "Harmful"}]}), moderation=None
    )

    assert await engine.check("input", "one") is None
    assert await engine.check("input", "two") is None
    assert [(kind, payload["reason"]) for kind, payload in events] == [
        (GUARDRAIL_TIMEOUT_EVENT, "unavailable")
    ]


async def test_openai_moderation_posts_through_the_given_transport() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = {"results": [{"flagged": True, "categories": {"hate": True, "violence": False}}]}
        return httpx.Response(200, json=body)

    client = OpenAIModeration("sk-test", transport=httpx.MockTransport(handler))
    try:
        flagged = await client.flagged("some text", timeout_s=1.0)
    finally:
        await client.aclose()

    assert flagged == ["hate"]
    (request,) = seen
    assert str(request.url) == OPENAI_MODERATION_URL
    assert request.headers["Authorization"] == "Bearer sk-test"
    assert json.loads(request.content) == {"model": MODERATION_MODEL, "input": "some text"}


@pytest.mark.parametrize("base_url", ["http://api.example.com/v1", "https://127.0.0.1/v1"])
async def test_openai_moderation_refuses_a_non_public_or_plain_http_url(base_url: str) -> None:
    client = OpenAIModeration(
        "sk-test", base_url=base_url, transport=httpx.MockTransport(lambda r: httpx.Response(200))
    )

    with pytest.raises(RuntimeError):
        await client.flagged("x", timeout_s=1.0)


async def test_openai_moderation_error_status_raises_without_the_key() -> None:
    client = OpenAIModeration("sk-secret", transport=httpx.MockTransport(lambda r: httpx.Response(500)))

    with pytest.raises(RuntimeError) as info:
        await client.flagged("x", timeout_s=1.0)
    assert "sk-secret" not in str(info.value)


# ------------------------------------------------------------------ compatibility


async def test_no_rules_means_no_guardrails_and_the_default_transcription_stream() -> None:
    model = FakeStructuredLLM()
    agent, ctx, events = _agent(resolved_config().config, llm_=model)

    assert agent.guardrails is None
    assert GUARDRAILS_USERDATA_KEY not in ctx.userdata
    out = [chunk async for chunk in agent.transcription_node(_stream("Take ", "pills."), cast(Any, None))]
    assert out == ["Take ", "pills."]
    await agent.on_user_turn_completed(
        ChatContext(), llm.ChatMessage(role="user", content=["4111 1111 1111 1111"])
    )
    assert model.calls == [], "no model call without rules"
    assert GUARDRAIL_EVENT not in _kinds(events)


async def test_no_tool_output_rules_leave_run_with_policy_untouched() -> None:
    context = FakeRunContext()

    result = await run_with_policy(cast(Any, context), blocking_policy("t"), _work)

    assert result == "4111 1111 1111 1111"


async def _work() -> str:
    return "4111 1111 1111 1111"


# ------------------------------------------------------------------ input


async def test_regex_input_rule_stops_the_response_and_speaks_the_safe_reply() -> None:
    kb = FakeKbClient()
    config = _config(input=[CARD_RULE])
    config = config.model_copy(update={"knowledge": config.knowledge.model_copy(update={"kb_ids": ["kb1"]})})
    agent, ctx, events = _agent(config, kb=kb)
    session = cast(FakeSession, ctx.session)

    with pytest.raises(StopResponse):
        await agent.on_user_turn_completed(
            ChatContext(), llm.ChatMessage(role="user", content=["my card is 4111 1111 1111 1111"])
        )

    assert session.calls == [("say", SAFE)]
    assert kb.queries == [], "a tripped turn is never searched"
    ((kind, payload),) = [e for e in events if e[0] == GUARDRAIL_EVENT]
    assert payload["stage"] == "input" and payload["rule"] == "Card numbers"
    assert payload["action"] == "interrupt" and payload["kind"] == "regex"
    assert len(payload["excerpt_hash"]) == 32
    assert payload["excerpt"] == "my card is 4111 1111 1111 1111"
    await _settle()
    (row,) = cast(FakeUiChannel, ctx.ui).state.activity
    assert row.kind == "guardrail" and row.detail == {
        "stage": "input",
        "rule": "Card numbers",
        "action": "interrupt",
    }


async def test_the_excerpt_is_kept_only_on_the_full_storage_tier() -> None:
    agent, _, events = _agent(_config(storage_tier="redacted", input=[CARD_RULE]))

    with pytest.raises(StopResponse):
        await agent.on_user_turn_completed(
            ChatContext(), llm.ChatMessage(role="user", content=["4111111111111111"])
        )

    (payload,) = [p for kind, p in events if kind == GUARDRAIL_EVENT]
    assert payload["excerpt"] is None
    assert "4111" not in json.dumps(payload)


async def test_input_classifier_trip_stops_the_turn_after_the_injection_overlaps() -> None:
    model = FakeStructuredLLM([Verdict(violates=True)])
    agent, ctx, events = _agent(_config(input=[MEDICAL_RULE]), llm_=model)

    with pytest.raises(StopResponse):
        await agent.on_user_turn_completed(
            ChatContext(), llm.ChatMessage(role="user", content=["what dose should I take?"])
        )

    assert cast(FakeSession, ctx.session).calls == [("say", SAFE)]
    assert GUARDRAIL_EVENT in _kinds(events)


async def test_a_clean_turn_goes_through_unchanged() -> None:
    model = FakeStructuredLLM([Verdict(violates=False)])
    agent, ctx, events = _agent(_config(input=[CARD_RULE, MEDICAL_RULE]), llm_=model)

    await agent.on_user_turn_completed(
        ChatContext(), llm.ChatMessage(role="user", content=["is flood covered?"])
    )

    assert cast(FakeSession, ctx.session).calls == []
    assert GUARDRAIL_EVENT not in _kinds(events)


async def test_realtime_committed_input_is_checked_in_parallel_and_interrupts() -> None:
    agent, ctx, events = _agent(_config(input=[CARD_RULE]))
    message = llm.ChatMessage(role="user", content=["card 4111 1111 1111 1111"])

    agent.on_conversation_item(ConversationItemAddedEvent(item=message))
    await _settle()

    assert cast(FakeSession, ctx.session).calls == [("interrupt", {"force": True}), ("say", SAFE)]
    assert _kinds(events).count(GUARDRAIL_EVENT) == 1


async def test_a_message_the_hook_checked_is_not_checked_again_when_committed() -> None:
    agent, ctx, events = _agent(_config(input=[CARD_RULE]))
    message = llm.ChatMessage(role="user", content=["hello there"])

    await agent.on_user_turn_completed(ChatContext(), message)
    agent.on_conversation_item(ConversationItemAddedEvent(item=message))
    await _settle()

    guard = cast(SessionGuardrails, agent.guardrails)
    assert guard.was_checked(message.id)
    assert cast(FakeSession, ctx.session).calls == []


# ------------------------------------------------------------------ output


async def test_output_classifier_trip_interrupts_mid_reply_and_speaks_the_safe_reply() -> None:
    model = FakeStructuredLLM([Verdict(violates=False), Verdict(violates=True), Verdict(violates=False)])
    agent, ctx, events = _agent(_config(output=[MEDICAL_RULE]), llm_=model)
    chunks = ("Sure. ", "Take two ", "pills every ", "four hours. ", "And rest")

    out = [chunk async for chunk in agent.transcription_node(_stream(*chunks), cast(Any, None))]
    await _settle()

    assert out == list(chunks), "the text passes through unchanged"
    calls = cast(FakeSession, ctx.session).calls
    assert calls[:2] == [("interrupt", {"force": True}), ("say", SAFE)]
    (payload,) = [p for kind, p in events if kind == GUARDRAIL_EVENT]
    assert payload["stage"] == "output" and payload["excerpt"] == "Take two pills every four hours."
    assert [text.count("Take two") for _, text, _ in model.calls] == [0, 1, 0]


async def test_output_regex_trip_is_immediate() -> None:
    agent, ctx, _ = _agent(_config(output=[CARD_RULE]))

    _ = [
        c
        async for c in agent.transcription_node(
            _stream("Your card 4111 1111 ", "1111 1111. Bye."), cast(Any, None)
        )
    ]
    await _settle()

    assert cast(FakeSession, ctx.session).calls == [("interrupt", {"force": True}), ("say", SAFE)]


async def test_the_safe_reply_is_never_checked_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, ctx, events = _agent(_config(output=[{"kind": "regex", "name": "Sorry", "pattern": "sorry"}]))
    guard = cast(SessionGuardrails, agent.guardrails)
    session = cast(FakeSession, ctx.session)
    session.current_speech = guard.speak_safe_reply(agent)
    # `Agent.session` needs a running activity; the speech the SDK plays is read through this helper.
    monkeypatch.setattr(guardrails_module, "_current_speech", lambda _agent: session.current_speech)

    _ = [c async for c in agent.transcription_node(_stream(SAFE), cast(Any, None))]
    await _settle()

    assert GUARDRAIL_EVENT not in _kinds(events)


async def test_a_late_trip_does_not_cut_the_next_reply() -> None:
    session = FakeSession()
    first = FakeSpeech("first")
    agent, ctx, events = _agent(_config(output=[MEDICAL_RULE]), llm_=SlowLLM(delay_s=0.05), session=session)
    guard = cast(SessionGuardrails, agent.guardrails)
    trip = guardrails_module.Trip(stage="output", rule=guard.config.output[0], text="x", latency_ms=1)
    session.current_speech = FakeSpeech("the next reply")

    await guard.respond(agent, trip, interrupt=True, handle=first)

    assert ("interrupt", {"force": True}) not in session.calls
    assert ("say", SAFE) in session.calls
    assert GUARDRAIL_EVENT in _kinds(events)


# ------------------------------------------------------------------ on_trip


async def test_end_call_ends_the_job_after_the_safe_reply() -> None:
    reasons: list[str] = []
    agent, ctx, _ = _agent(_config(input=[CARD_RULE], on_trip="end_call"))
    ctx.request_shutdown = reasons.append

    with pytest.raises(StopResponse):
        await agent.on_user_turn_completed(
            ChatContext(), llm.ChatMessage(role="user", content=["4111111111111111"])
        )
    await _settle()

    assert reasons == ["guardrail"]


async def test_escalate_calls_escalate_to_human() -> None:
    from livekit.agents import RunContext, function_tool

    calls: list[dict[str, Any]] = []

    @function_tool
    async def escalate_to_human(context: RunContext[Any], reason: str, urgency: str = "normal") -> str:
        """Escalate."""
        calls.append({"call_id": context.function_call.call_id, "reason": reason, "urgency": urgency})
        return "Escalation logged."

    agent, _, events = _agent(_config(input=[CARD_RULE], on_trip="escalate"), tools=[escalate_to_human])

    with pytest.raises(StopResponse):
        await agent.on_user_turn_completed(
            ChatContext(), llm.ChatMessage(role="user", content=["4111111111111111"])
        )
    await _settle()

    (call,) = calls
    assert call["urgency"] == "high" and "Card numbers" in call["reason"]
    assert "4111" not in call["reason"], "the caller's words never reach the escalation"
    (payload,) = [p for kind, p in events if kind == GUARDRAIL_EVENT]
    assert payload["action"] == "escalate"


# ------------------------------------------------------------------ tool output


async def test_tool_output_trip_replaces_the_result_with_the_safe_text() -> None:
    session = FakeSession()
    agent, _, events = _agent(_config(tool_output=[CARD_RULE]), session=session)
    context = FakeRunContext(name="lookup_customer")
    context.session = cast(Any, session)
    try:
        result = await run_with_policy(cast(Any, context), blocking_policy("lookup_customer"), _work)
    finally:
        set_tool_output_guard(session, None)

    assert result == TOOL_WITHHELD.format(safe_reply=SAFE)
    (payload,) = [p for kind, p in events if kind == GUARDRAIL_EVENT]
    assert (payload["stage"], payload["action"], payload["tool"]) == (
        "tool_output",
        "replaced",
        "lookup_customer",
    )
    assert agent.guardrails is not None


async def test_tool_output_guard_passes_a_clean_result_and_dict_results_are_checked() -> None:
    session = FakeSession()
    _agent(_config(tool_output=[CARD_RULE]), session=session)
    context = FakeRunContext()
    context.session = cast(Any, session)
    try:
        clean = await execution_module.guard_tool_output(cast(Any, context), "all good")
        flagged = await execution_module.guard_tool_output(
            cast(Any, context), {"card": "4111 1111 1111 1111"}
        )
    finally:
        set_tool_output_guard(session, None)

    assert clean == "all good"
    assert flagged == TOOL_WITHHELD.format(safe_reply=SAFE)


# ------------------------------------------------------------------ wiring


def test_the_guardrails_model_is_built_from_the_resolved_builtin_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[tuple[str, str]] = []

    class _Factory:
        def build(self, slot: str, provider: ResolvedProvider, **_: Any) -> Any:
            built.append((slot, provider.model or ""))
            return object()

    import lkap_agent.providers.factory as factory_module

    monkeypatch.setattr(factory_module, "ProviderFactory", _Factory)
    ctx, _ = _ctx(_config(output=[MEDICAL_RULE]))
    ctx.userdata["lkap.builtin_providers"] = {
        "guardrails_llm": ResolvedProvider(
            provider_id="openai-llm", python_class="x", model="gpt-4.1-mini", kwargs={}
        )
    }

    model = guardrails_module.default_classifier(ctx)

    assert built == [("workflow_llm", "gpt-4.1-mini")]
    assert model is not ctx.workflow_llm


def test_without_a_guardrails_model_the_workflow_model_judges() -> None:
    ctx, _ = _ctx(_config(output=[MEDICAL_RULE]))

    assert guardrails_module.default_classifier(ctx) is ctx.workflow_llm
    assert guardrails_module.default_moderation(ctx) is None


def test_moderation_key_comes_from_the_resolved_builtin_slot() -> None:
    ctx, _ = _ctx(_config(output=[{"kind": "provider", "name": "Harmful"}]))
    ctx.userdata["lkap.builtin_providers"] = {
        "guardrails_moderation": ResolvedProvider(
            provider_id="openai-llm", python_class="x", model=None, kwargs={"api_key": "sk-x"}
        )
    }

    assert isinstance(guardrails_module.default_moderation(ctx), OpenAIModeration)


def test_guardrails_are_created_once_per_session() -> None:
    ctx, _ = _ctx(_config(input=[CARD_RULE]))

    first = ensure_session_guardrails(ctx)
    assert first is not None and ensure_session_guardrails(ctx) is first


async def test_session_end_closes_the_moderation_client() -> None:
    moderation = FakeModeration([])
    ctx, _ = _ctx(_config(input=[{"kind": "provider", "name": "Harmful"}]))
    guard = ensure_session_guardrails(ctx, moderation_factory=lambda _ctx: moderation)
    assert guard is not None
    await guard.check_input("hello")

    await guard.aclose()

    assert moderation.closed and moderation.texts == ["hello"]


# ------------------------------------------------------------------ latency


def test_regex_checks_cost_microseconds_per_turn() -> None:
    """The hot-path cost of regex rules (reported in the V5-39 hand-off): well under 1 ms a turn."""
    engine, _ = _engine(
        GuardrailsConfig.model_validate(
            {"input": [CARD_RULE, {"kind": "regex", "name": "SSN", "pattern": r"\b\d{3}-\d{2}-\d{4}\b"}]}
        )
    )
    turn = "I would like to know whether my policy covers water damage in the basement. " * 7
    runs = 1000
    started = time.perf_counter()
    for _ in range(runs):
        assert engine.check_regex("input", turn) is None
    per_turn_us = (time.perf_counter() - started) / runs * 1e6

    assert per_turn_us < 1000


async def test_the_output_watch_adds_little_per_reply() -> None:
    agent, _, _ = _agent(_config(output=[CARD_RULE]))
    chunks = [f"word{i} " + ("and more. " if i % 10 == 9 else "") for i in range(100)]

    async def _plain() -> AsyncIterator[str]:
        for chunk in chunks:
            yield chunk

    started = time.perf_counter()
    out = [c async for c in agent.transcription_node(_plain(), cast(Any, None))]
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert out == chunks
    assert elapsed_ms < 50


def test_regex_windows_overlap_and_stop_at_the_cap() -> None:
    assert regex_windows("short") == ["short"]
    text = "a" * 5000
    windows = regex_windows(text)
    assert all(len(window) <= REGEX_WINDOW_CHARS for window in windows)
    assert windows[0] == text[:REGEX_WINDOW_CHARS]
    assert text.endswith(windows[-1])
    assert sum(len(w) for w in regex_windows("b" * 50_000)) < 50_000 * 1.2, "only the first 20 000 characters"


async def test_a_card_number_across_a_window_edge_in_a_long_tool_result_trips() -> None:
    engine, _ = _engine(GuardrailsConfig.model_validate({"tool_output": [CARD_RULE]}))
    edge = REGEX_WINDOW_CHARS - 8
    text = "x " * (edge // 2) + "4111 1111 1111 1111" + " y" * 3000

    trip = await engine.check("tool_output", text)

    assert trip is not None and trip.rule.name == "Card numbers"


async def test_a_caller_repeating_the_words_on_a_realtime_agent_trips_again() -> None:
    agent, ctx, events = _agent(_config(input=[CARD_RULE]))

    for _ in range(2):
        message = llm.ChatMessage(role="user", content=["card 4111 1111 1111 1111"])
        agent.on_conversation_item(ConversationItemAddedEvent(item=message))
        await _settle()

    calls = cast(FakeSession, ctx.session).calls
    assert calls.count(("interrupt", {"force": True})) == 2
    assert _kinds(events).count(GUARDRAIL_EVENT) == 2


async def test_the_realtime_transcript_of_a_turn_the_hook_checked_is_skipped_once() -> None:
    agent, ctx, events = _agent(_config(input=[CARD_RULE]))
    await agent.on_user_turn_completed(ChatContext(), llm.ChatMessage(role="user", content=["hello there"]))

    # The realtime model's own transcript of the same turn: a new message, same words.
    agent.on_conversation_item(
        ConversationItemAddedEvent(item=llm.ChatMessage(role="user", content=["hello there"]))
    )
    await _settle()
    agent.on_conversation_item(
        ConversationItemAddedEvent(item=llm.ChatMessage(role="user", content=["card 4111 1111 1111 1111"]))
    )
    await _settle()

    assert cast(FakeSession, ctx.session).calls == [("interrupt", {"force": True}), ("say", SAFE)]
