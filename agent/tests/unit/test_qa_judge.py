"""Worker-side post-call QA judge (PLAN-V2 §8 rulings R-V2-5, R-V2-6)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fakes.fake_api import FakeApi, resolved_config
from fakes.fake_llm import FakeLLM
from livekit.agents import inference, llm
from livekit.agents.voice.events import ConversationItemAddedEvent
from lkap_contracts.agent_config import QaConfig, ResolvedAgentConfig, ResolvedProvider
from lkap_contracts.api_models import TranscriptTurn
from test_main import FakeJobContext, RoomlessStarter, _deps, _metadata, _RecordingFactory

from lkap_agent.main import run_session
from lkap_agent.providers.factory import ProviderFactory
from lkap_agent.qa import DEFAULT_RUBRIC_PROMPT, build_judge, render_transcript, score_session
from lkap_agent.workflow_llm import PromptJsonStructuredLLM

VALID = '{"score": 8, "sentiment": "positive", "tags": ["DEAD_AIR"], "summary": "Resolved quickly."}'
TRANSCRIPT = [
    TranscriptTurn(role="user", text="My pipe burst.", ts=1.0),
    TranscriptTurn(role="assistant", text="I can help with that.", ts=2.0),
]


@pytest.fixture(autouse=True)
def _inference_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret")
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")


# ------------------------------------------------------------ score_session


async def test_score_session_malformed_then_valid_json_yields_done() -> None:
    """R-V2-5 named test: the one repair retry turns a bad first reply into a verdict."""
    judge_llm = FakeLLM(["not json at all", VALID])

    verdict = await score_session(
        qa=QaConfig(enabled=True),
        transcript=TRANSCRIPT,
        judge=PromptJsonStructuredLLM(judge_llm),
        model_label="livekit-inference-llm:google/gemma-4-31b-it",
    )

    assert verdict.status == "done"
    assert (verdict.score, verdict.sentiment, verdict.tags) == (8, "positive", ["DEAD_AIR"])
    assert verdict.summary == "Resolved quickly."
    assert verdict.raw == {
        "score": 8,
        "sentiment": "positive",
        "tags": ["DEAD_AIR"],
        "summary": "Resolved quickly.",
    }
    assert verdict.model == "livekit-inference-llm:google/gemma-4-31b-it"
    assert len(judge_llm.calls) == 2
    assert "User: My pipe burst." in judge_llm.calls[0].prompt
    assert DEFAULT_RUBRIC_PROMPT.splitlines()[0] in judge_llm.calls[0].prompt


async def test_score_session_still_malformed_after_the_repair_is_failed() -> None:
    verdict = await score_session(
        qa=QaConfig(enabled=True),
        transcript=TRANSCRIPT,
        judge=PromptJsonStructuredLLM(FakeLLM(["nope", "still nope"])),
        model_label="m",
    )

    assert verdict.status == "failed" and verdict.error and verdict.score is None


async def test_score_session_disabled_is_skipped_without_an_llm_call() -> None:
    judge_llm = FakeLLM([VALID])

    verdict = await score_session(
        qa=QaConfig(enabled=False),
        transcript=TRANSCRIPT,
        judge=PromptJsonStructuredLLM(judge_llm),
        model_label="m",
    )

    assert verdict.status == "skipped" and judge_llm.calls == []


async def test_score_session_uses_a_custom_rubric() -> None:
    judge_llm = FakeLLM([VALID])

    await score_session(
        qa=QaConfig(enabled=True, rubric_prompt="Grade politeness only."),
        transcript=TRANSCRIPT,
        judge=PromptJsonStructuredLLM(judge_llm),
        model_label="m",
    )

    prompt = judge_llm.calls[0].prompt
    assert "Grade politeness only." in prompt and DEFAULT_RUBRIC_PROMPT.splitlines()[0] not in prompt


async def test_score_session_times_out_as_failed() -> None:
    class _Slow:
        async def extract(self, **_: Any) -> Any:
            await asyncio.sleep(10)

    class _Timed:
        async def extract(self, **kwargs: Any) -> Any:
            return await asyncio.wait_for(_Slow().extract(**kwargs), kwargs["timeout_s"])

    verdict = await score_session(
        qa=QaConfig(enabled=True), transcript=TRANSCRIPT, judge=_Timed(), model_label="m", timeout_s=0.01
    )

    assert verdict.status == "failed"


async def test_score_session_without_a_judge_reports_why() -> None:
    verdict = await score_session(
        qa=QaConfig(enabled=True), transcript=TRANSCRIPT, judge=None, model_label="x", judge_error="no key"
    )

    assert (verdict.status, verdict.error) == ("failed", "no key")


async def test_score_session_with_an_empty_transcript_is_skipped() -> None:
    verdict = await score_session(qa=QaConfig(enabled=True), transcript=[], judge=None, model_label="m")

    assert verdict.status == "skipped"


def test_render_transcript_labels_speakers_and_interruptions() -> None:
    turns = [*TRANSCRIPT, TranscriptTurn(role="assistant", text="Let me", ts=3.0, interrupted=True)]

    assert render_transcript(turns) == (
        "User: My pipe burst.\nAgent: I can help with that.\nAgent: Let me [interrupted]"
    )


# --------------------------------------------------------------- judge choice (R-V2-6)
#
# The worker no longer walks `qa.model -> workflow_llm -> llm -> Inference`
# (that chain moved to the api's `config_service.resolve_providers`); it only
# builds whatever the api already resolved into `resolved["qa_llm"]`
# (`build_judge`). `_with_qa_llm` stands in for the api's resolve step.


def _with_qa_llm(
    config: ResolvedAgentConfig, qa: QaConfig, provider: ResolvedProvider | None
) -> ResolvedAgentConfig:
    resolved = dict(config.resolved)
    if provider is not None:
        resolved["qa_llm"] = provider
    return config.model_copy(
        update={"config": config.config.model_copy(update={"qa": qa}), "resolved": resolved}
    )


async def test_build_judge_uses_the_api_resolved_qa_llm_slot() -> None:
    """R-V2-6 named test: the worker never walks the chain, only builds `qa_llm`."""
    provider = ResolvedProvider(
        provider_id="livekit-inference-llm",
        python_class="livekit.agents.inference.LLM",
        model="google/gemma-4-31b-it",
        kwargs={},
    )
    config = _with_qa_llm(resolved_config(), QaConfig(enabled=True), provider)

    judge, label, error = build_judge(ProviderFactory(), config)

    assert error is None and judge is not None
    assert isinstance(judge._llm, inference.LLM)  # type: ignore[attr-defined]
    assert label == "livekit-inference-llm:google/gemma-4-31b-it"


async def test_build_judge_carries_a_vendor_key_from_the_resolved_slot() -> None:
    """R-V2-6 named test: a vendor-key judge carries the decrypted key."""
    provider = ResolvedProvider(
        provider_id="openai-llm",
        python_class="livekit.plugins.openai.LLM",
        model="gpt-4.1",
        kwargs={"api_key": "sk-decrypted-secret"},
    )
    config = _with_qa_llm(resolved_config(), QaConfig(enabled=True), provider)

    judge, label, error = build_judge(ProviderFactory(), config)

    assert error is None and judge is not None
    assert judge._llm._client.api_key == "sk-decrypted-secret"  # type: ignore[attr-defined]
    assert label == "openai-llm:gpt-4.1"


async def test_build_judge_without_a_resolved_slot_reports_why() -> None:
    """The api-side resolve step did not attach `qa_llm` — an honest failure, not a guess."""
    config = _with_qa_llm(resolved_config(), QaConfig(enabled=True), None)

    judge, label, error = build_judge(ProviderFactory(), config)

    assert judge is None and label is None and error == "qa_llm not resolved"


# ------------------------------------------------------------- end to end


async def _run_with_turns(config: ResolvedAgentConfig, judge_llm: FakeLLM) -> FakeApi:
    class _Factory(_RecordingFactory):
        def build(self, slot: Any, provider: Any, *, mode: Any = "cascaded") -> Any:
            # R-V2-6: `build_judge` asks the factory to build slot "qa_llm" from
            # whatever placeholder `ResolvedProvider` the test's config carries;
            # intercept it here rather than constructing a real plugin class.
            if slot == "qa_llm":
                return judge_llm
            return super().build(slot, provider, mode=mode)

    starter = RoomlessStarter()
    api = FakeApi(config)
    ctx = FakeJobContext(_metadata())
    await run_session(ctx, _deps(api, factory=_Factory(), session_starter=starter))
    assert starter.session is not None
    starter.session.history.add_message(role="user", content="My pipe burst.")
    starter.session.history.add_message(role="assistant", content="I can help.")
    starter.session.emit(
        "conversation_item_added",
        ConversationItemAddedEvent(item=llm.ChatMessage(role="assistant", content=["I can help."])),
    )
    await ctx.fire_shutdown("done")
    return api


async def test_summary_is_posted_before_qa() -> None:
    """R-V2-5 named test: the judge never delays the summary."""
    placeholder_qa_llm = ResolvedProvider(
        provider_id="livekit-inference-llm",
        python_class="livekit.agents.inference.LLM",
        model="google/gemma-4-31b-it",
        kwargs={},
    )
    api = await _run_with_turns(
        _with_qa_llm(resolved_config(greeting=""), QaConfig(enabled=True), placeholder_qa_llm),
        FakeLLM(["garbage", VALID]),
    )

    assert api.call_log.index("summary") < api.call_log.index("qa")
    (verdict,) = api.qa
    assert verdict.status == "done" and verdict.score == 8


async def test_a_disabled_qa_posts_skipped_after_the_summary() -> None:
    judge_llm = FakeLLM([VALID])
    api = await _run_with_turns(resolved_config(greeting=""), judge_llm)

    assert api.call_log[-1] == "qa"
    assert api.qa[0].status == "skipped" and judge_llm.calls == []
