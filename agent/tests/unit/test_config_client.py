"""`ConfigClient` maps the api's internal surface onto typed results and errors."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
import respx
from fakes.fake_api import resolved_config
from lkap_contracts.api_models import KbHit, SessionEventIn, SessionSummaryIn, TranscriptTurn

from lkap_agent.config_client import (
    ApiKbClient,
    ConfigClient,
    ConfigUnavailableError,
    SessionEndedError,
    SessionNotFoundError,
)

BASE_URL = "http://api.test"
SECRET = "sk-SECRET123"


def _client() -> ConfigClient:
    return ConfigClient(BASE_URL, "service-token-abc")


@respx.mock
async def test_resolve_returns_a_validated_config_and_sends_the_service_token() -> None:
    """The worker authenticates with `X-Service-Token` and parses the payload."""
    config = resolved_config(session_id="sess-9")
    route = respx.get(f"{BASE_URL}/internal/v1/sessions/sess-9/resolved").mock(
        return_value=httpx.Response(200, content=config.model_dump_json())
    )

    async with _client() as client:
        result = await client.resolve("sess-9")

    assert result.session_id == "sess-9"
    assert route.calls.last.request.headers["X-Service-Token"] == "service-token-abc"


@respx.mock
@pytest.mark.parametrize(
    ("status", "expected"),
    [(404, SessionNotFoundError), (409, SessionEndedError), (500, ConfigUnavailableError)],
)
async def test_resolve_maps_http_errors_to_typed_exceptions(status: int, expected: type[Exception]) -> None:
    """404/409 are distinguishable so the failure path can explain itself."""
    respx.get(f"{BASE_URL}/internal/v1/sessions/s/resolved").mock(return_value=httpx.Response(status))

    async with _client() as client:
        with pytest.raises(expected):
            await client.resolve("s")


@respx.mock
async def test_resolve_raises_when_the_api_is_unreachable() -> None:
    """A transport failure is a `ConfigUnavailableError`, not a raw httpx error."""
    respx.get(f"{BASE_URL}/internal/v1/sessions/s/resolved").mock(side_effect=httpx.ConnectError("refused"))

    async with _client() as client:
        with pytest.raises(ConfigUnavailableError, match="api unreachable"):
            await client.resolve("s")


@respx.mock
async def test_resolve_never_logs_or_reports_the_response_body(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """A malformed payload holds decrypted credentials, so only a message escapes.

    structlog writes to stdout until `configure_logging()` routes it through
    stdlib, so `capsys` is checked alongside `caplog`.
    """
    body = f'{{"v": 1, "session_id": "s", "api_key": "{SECRET}"}}'
    respx.get(f"{BASE_URL}/internal/v1/sessions/s/resolved").mock(
        return_value=httpx.Response(200, content=body)
    )

    with caplog.at_level(logging.DEBUG):
        async with _client() as client:
            with pytest.raises(ConfigUnavailableError) as exc_info:
                await client.resolve("s")

    captured = capsys.readouterr()
    assert SECRET not in str(exc_info.value)
    assert SECRET not in caplog.text
    assert SECRET not in captured.out


@respx.mock
async def test_post_events_sends_the_batch() -> None:
    """Events are posted as one `SessionEventsIn` body."""
    route = respx.post(f"{BASE_URL}/internal/v1/sessions/s/events").mock(return_value=httpx.Response(202))

    async with _client() as client:
        await client.post_events("s", [SessionEventIn(ts=1.0, type="session_started", payload={})])

    assert route.called
    assert b"session_started" in route.calls.last.request.content


@respx.mock
async def test_post_events_skips_the_call_when_there_is_nothing_to_send() -> None:
    """An empty batch must not cost a round trip."""
    route = respx.post(f"{BASE_URL}/internal/v1/sessions/s/events")

    async with _client() as client:
        await client.post_events("s", [])

    assert not route.called


@respx.mock
async def test_post_events_swallows_api_failures() -> None:
    """Telemetry must never take down a live conversation."""
    respx.post(f"{BASE_URL}/internal/v1/sessions/s/events").mock(return_value=httpx.Response(500))

    async with _client() as client:
        await client.post_events("s", [SessionEventIn(ts=1.0, type="error", payload={})])


@respx.mock
async def test_put_summary_sends_the_transcript_and_swallows_failures() -> None:
    """The summary is best effort; a 500 is logged, not raised."""
    route = respx.put(f"{BASE_URL}/internal/v1/sessions/s/summary").mock(return_value=httpx.Response(204))
    summary = SessionSummaryIn(
        status="ended",
        usage={"llm_prompt_tokens": 12},
        transcript=[TranscriptTurn(role="user", text="hi", ts=1.0)],
    )

    async with _client() as client:
        await client.put_summary("s", summary)

    assert b'"role":"user"' in route.calls.last.request.content

    respx.put(f"{BASE_URL}/internal/v1/sessions/s2/summary").mock(return_value=httpx.Response(500))
    async with _client() as client:
        await client.put_summary("s2", summary)


@respx.mock
async def test_kb_search_returns_hits_and_degrades_to_empty_on_failure() -> None:
    """Retrieval is an enhancement: a failing KB returns no hits, not an error."""
    hit = KbHit(chunk_id="c1", document_id="d1", filename="policy.md", score=0.9, text="covered")
    respx.post(f"{BASE_URL}/internal/v1/kb/search").mock(
        return_value=httpx.Response(200, json={"hits": [hit.model_dump()]})
    )

    async with _client() as client:
        assert await client.kb_search(["kb-1"], "is it covered") == [hit]

    respx.post(f"{BASE_URL}/internal/v1/kb/search").mock(return_value=httpx.Response(503))
    async with _client() as client:
        assert await client.kb_search(["kb-1"], "is it covered") == []


@respx.mock
async def test_kb_search_short_circuits_without_kbs_or_a_query() -> None:
    """No attached KB and no query means no request at all."""
    route = respx.post(f"{BASE_URL}/internal/v1/kb/search")

    async with _client() as client:
        assert await client.kb_search([], "q") == []
        assert await client.kb_search(["kb-1"], "   ") == []

    assert not route.called


async def test_api_kb_client_binds_the_agents_knowledge_bases() -> None:
    """`ApiKbClient` supplies the configured kb_ids so tools need not know them."""
    calls: list[tuple[list[str], str, int]] = []

    class _Recorder:
        async def kb_search(self, kb_ids: list[str], query: str, k: int = 4) -> list[KbHit]:
            calls.append((kb_ids, query, k))
            return []

        def __getattr__(self, name: str) -> Any:  # pragma: no cover - protocol filler
            raise AttributeError(name)

    kb = ApiKbClient(_Recorder(), ["kb-a", "kb-b"])  # type: ignore[arg-type]
    await kb.search("question")
    await kb.search("question", kb_ids=["kb-c"])

    assert calls[0][0] == ["kb-a", "kb-b"]
    assert calls[1][0] == ["kb-c"]


@pytest.mark.parametrize(("k", "expected"), [(None, 4), (1, 1), (7, 7), (20, 20)])
async def test_api_kb_client_search_passes_k_through_unchanged(k: int | None, expected: int) -> None:
    """D-W2-5: an explicit `k` wins, otherwise the contract default 4 — no hidden fallback."""
    calls: list[int] = []

    class _Recorder:
        async def kb_search(self, kb_ids: list[str], query: str, k: int = 4) -> list[KbHit]:
            calls.append(k)
            return []

        def __getattr__(self, name: str) -> Any:  # pragma: no cover - protocol filler
            raise AttributeError(name)

    kb = ApiKbClient(_Recorder(), ["kb-a"])  # type: ignore[arg-type]
    if k is None:
        await kb.search("question")
    else:
        await kb.search("question", k=k)

    assert calls == [expected]
