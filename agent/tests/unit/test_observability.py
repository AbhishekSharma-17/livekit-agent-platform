"""V4-17 (docs/v4/COSTS.md D-V4-45, §7 worker bullet): per-request ids under the opt-in."""

from __future__ import annotations

import json
import time
from typing import Any, cast

from fakes.fake_api import FakeApi
from livekit.agents import MetricsCollectedEvent
from livekit.agents.metrics import (
    LLMMetrics,
    RealtimeModelMetrics,
    STTMetrics,
    TTSMetrics,
    VADMetrics,
)
from livekit.agents.metrics.base import Metadata

from lkap_agent.observability import ProviderRequestCollector, SessionObserver

SECRET = "sk-or-SECRET-123"
PROMPT = "My policy number is 555-0101 and my dog ate the claim form."


class _SpySession:
    """Records which events an observer subscribes to (no SDK session needed)."""

    def __init__(self) -> None:
        self.subscribed: list[str] = []

    def on(self, event: str, callback: Any = None) -> Any:
        self.subscribed.append(event)
        return callback


def _llm(request_id: str, *, model: str = "google/gemma-4-31b-it") -> LLMMetrics:
    return LLMMetrics(
        label="livekit.plugins.openai.llm.LLM",
        request_id=request_id,
        timestamp=time.time(),
        duration=0.8,
        ttft=0.2,
        cancelled=False,
        completion_tokens=40,
        prompt_tokens=900,
        prompt_cached_tokens=0,
        total_tokens=940,
        tokens_per_second=50.0,
        metadata=Metadata(model_name=model, model_provider="openrouter.ai"),
    )


def _stt(request_id: str) -> STTMetrics:
    return STTMetrics(
        label="deepgram.STT",
        request_id=request_id,
        timestamp=time.time(),
        duration=0.0,
        audio_duration=3.2,
        streamed=True,
        metadata=Metadata(model_name="nova-3", model_provider="Deepgram"),
    )


def _tts(request_id: str) -> TTSMetrics:
    return TTSMetrics(
        label="cartesia.TTS",
        request_id=request_id,
        timestamp=time.time(),
        ttfb=0.1,
        duration=0.5,
        audio_duration=2.0,
        cancelled=False,
        characters_count=len(PROMPT),
        streamed=True,
    )


def _provider_requests(api: FakeApi) -> dict[str, Any]:
    events = [e for e in api.events_of("metrics") if e.payload.get("kind") == "provider_requests"]
    assert len(events) == 1
    data = events[0].payload["data"]
    assert isinstance(data, dict)
    return data


def test_attach_without_the_opt_in_never_subscribes_to_metrics_collected() -> None:
    session = _SpySession()
    observer = SessionObserver(session_id="sess-1", client=cast(Any, FakeApi()))

    observer.attach(cast(Any, session))

    assert "metrics_collected" not in session.subscribed
    assert "session_usage_updated" in session.subscribed


def test_attach_with_the_opt_in_subscribes_to_metrics_collected_once() -> None:
    session = _SpySession()
    observer = SessionObserver(
        session_id="sess-1", client=cast(Any, FakeApi()), cost_reconcile=["openrouter"]
    )

    observer.attach(cast(Any, session))

    assert session.subscribed.count("metrics_collected") == 1


async def test_shutdown_without_the_opt_in_posts_no_provider_requests_event() -> None:
    api = FakeApi()
    observer = SessionObserver(session_id="sess-1", client=cast(Any, api))

    await observer.shutdown(reason="done")

    assert [e for e in api.events_of("metrics") if e.payload.get("kind") == "provider_requests"] == []


async def test_shutdown_with_the_opt_in_posts_the_ids_before_the_summary() -> None:
    api = FakeApi()
    observer = SessionObserver(session_id="sess-1", client=cast(Any, api), cost_reconcile=["openrouter"])

    observer._on_metrics(MetricsCollectedEvent(metrics=_llm("gen-1")))
    observer._on_metrics(MetricsCollectedEvent(metrics=_llm("gen-2")))
    observer._on_metrics(MetricsCollectedEvent(metrics=_stt("dg-req-1")))
    observer._on_metrics(MetricsCollectedEvent(metrics=_tts("tts-1")))
    await observer.shutdown(reason="done")

    data = _provider_requests(api)
    assert data == {
        "llm": [
            {"request_id": "gen-1", "provider": "openrouter.ai", "model": "google/gemma-4-31b-it"},
            {"request_id": "gen-2", "provider": "openrouter.ai", "model": "google/gemma-4-31b-it"},
        ],
        "stt": [{"request_id": "dg-req-1", "provider": "Deepgram", "model": "nova-3"}],
        "tts": [{"request_id": "tts-1", "provider": None, "model": None}],
        "dropped": 0,
    }
    # The event lands before `session_ended`, in the flush that precedes the summary.
    types = api.event_types()
    assert types.index("metrics") < types.index("session_ended")
    assert len(api.summaries) == 1


async def test_the_event_never_carries_a_prompt_or_a_secret() -> None:
    api = FakeApi()
    observer = SessionObserver(session_id="sess-1", client=cast(Any, api), cost_reconcile=["openrouter"])

    observer._on_metrics(MetricsCollectedEvent(metrics=_llm("gen-1")))
    observer._on_metrics(MetricsCollectedEvent(metrics=_tts("tts-1")))
    await observer.shutdown(reason="done")

    dumped = json.dumps(_provider_requests(api))
    assert PROMPT not in dumped
    assert SECRET not in dumped
    for rows in (_provider_requests(api)[k] for k in ("llm", "stt", "tts")):
        for row in rows:
            assert set(row) == {"request_id", "provider", "model"}


def test_the_collector_counts_ids_beyond_the_limit_as_dropped() -> None:
    collector = ProviderRequestCollector(limit=3)

    for index in range(5):
        collector.add(_llm(f"gen-{index}"))
    collector.add(_stt("dg-overflow"))

    data = collector.data()
    assert [row["request_id"] for row in data["llm"]] == ["gen-0", "gen-1", "gen-2"]
    assert data["stt"] == []
    assert data["dropped"] == 3
    assert collector.kept == 3


def test_the_collector_keeps_a_repeated_id_once_and_ignores_other_metrics() -> None:
    collector = ProviderRequestCollector()

    collector.add(_stt("dg-1"))
    collector.add(_stt("dg-1"))
    collector.add(_llm(""))
    collector.add(
        VADMetrics(
            label="vad", timestamp=time.time(), idle_time=0.0, inference_duration_total=0.0, inference_count=1
        )
    )
    collector.add(
        RealtimeModelMetrics(
            request_id="resp-1",
            timestamp=time.time(),
            input_token_details=RealtimeModelMetrics.InputTokenDetails(),
            output_token_details=RealtimeModelMetrics.OutputTokenDetails(),
        )
    )

    data = collector.data()
    assert [row["request_id"] for row in data["stt"]] == ["dg-1"]
    assert data["llm"] == [] and data["tts"] == [] and data["dropped"] == 0
