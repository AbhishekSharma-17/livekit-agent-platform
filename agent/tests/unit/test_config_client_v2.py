"""`ConfigClient` v2 calls (sessions/start, recording, metrics, qa) and the latency collector."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from fakes.fake_api import resolved_config
from lkap_contracts.api_models import SessionLatency, SessionMetricsIn, SessionRecordingIn, SessionStartIn

from lkap_agent.config_client import (
    ConfigClient,
    ConfigUnavailableError,
    RecordingUnavailableError,
    SessionEndedError,
    SessionNotFoundError,
)
from lkap_agent.observability import LatencyCollector, percentile
from lkap_agent.qa import SessionQaIn

API = "http://api.test"
START = SessionStartIn(agent_id="a1", room_name="room-1", channel="sip_in")


@respx.mock
async def test_start_session_posts_the_request_and_returns_the_resolved_config() -> None:
    route = respx.post(f"{API}/internal/v1/sessions/start").mock(
        return_value=httpx.Response(201, content=resolved_config(session_id="new-1").model_dump_json())
    )
    client = ConfigClient(API, "svc")

    resolved = await client.start_session(START)

    assert resolved.session_id == "new-1"
    request = route.calls[0].request
    assert request.headers["X-Service-Token"] == "svc"
    assert json.loads(request.content)["channel"] == "sip_in"
    await client.aclose()


@respx.mock
@pytest.mark.parametrize(
    ("status", "error"),
    [(404, SessionNotFoundError), (409, SessionEndedError), (500, ConfigUnavailableError)],
)
async def test_start_session_maps_error_statuses(status: int, error: type[Exception]) -> None:
    respx.post(f"{API}/internal/v1/sessions/start").mock(return_value=httpx.Response(status))
    client = ConfigClient(API, "svc")

    with pytest.raises(error):
        await client.start_session(START)
    await client.aclose()


@respx.mock
async def test_start_recording_returns_the_egress_id_and_raises_on_501() -> None:
    respx.post(f"{API}/internal/v1/sessions/s1/recording/start").mock(
        return_value=httpx.Response(200, json={"egress_id": "EG_9"})
    )
    respx.post(f"{API}/internal/v1/sessions/s2/recording/start").mock(return_value=httpx.Response(501))
    client = ConfigClient(API, "svc")

    assert await client.start_recording("s1") == "EG_9"
    with pytest.raises(RecordingUnavailableError, match="501"):
        await client.start_recording("s2")
    await client.aclose()


@respx.mock
async def test_best_effort_posts_swallow_a_missing_endpoint() -> None:
    """`metrics`, `recording` and `qa` routes may not exist on the api yet; the call must not fail."""
    metrics = respx.post(f"{API}/internal/v1/sessions/s1/metrics").mock(return_value=httpx.Response(404))
    recording = respx.post(f"{API}/internal/v1/sessions/s1/recording").mock(return_value=httpx.Response(202))
    qa = respx.put(f"{API}/internal/v1/sessions/s1/qa").mock(return_value=httpx.Response(405))
    client = ConfigClient(API, "svc")

    await client.post_metrics("s1", SessionMetricsIn(latency=SessionLatency(turns=1)))
    await client.post_recording("s1", SessionRecordingIn(egress_id="EG", status="ready", duration_s=3.0))
    await client.put_qa("s1", SessionQaIn(status="skipped"))

    assert metrics.called and recording.called and qa.called
    assert json.loads(qa.calls[0].request.content)["status"] == "skipped"
    await client.aclose()


def test_percentile_interpolates_linearly() -> None:
    assert percentile([], 0.5) is None
    assert percentile([5.0], 0.95) == 5.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)
    assert percentile([100.0, 200.0], 0.95) == pytest.approx(195.0)


def test_latency_collector_converts_seconds_to_ms_and_skips_turns_without_metrics() -> None:
    collector = LatencyCollector()
    collector.add({"e2e_latency": 0.5, "llm_node_ttft": 0.2})
    collector.add({})
    collector.add({"e2e_latency": 1.5, "tts_node_ttfb": 0.1})

    latency = collector.summary()

    assert latency.turns == 2
    assert latency.eou_to_first_audio_ms_p50 == pytest.approx(1000.0)
    assert latency.llm_ttft_ms_p50 == pytest.approx(200.0)
    assert latency.tts_ttfb_ms_p95 == pytest.approx(100.0)
