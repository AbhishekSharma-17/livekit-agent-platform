"""``POST /v1/providers/{provider_id}/test-model`` (docs/v4/CUSTOM-MODELS.md D-V4-26, R-V4-25, R-V4-26).

Offline: vendors are an ``httpx.MockTransport`` behind ``get_http_client`` and
the realtime socket is a fake behind ``get_ws_connector``. Every "key" below is
a placeholder shaped like a vendor key; none is real.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
import pytest
from auth_helpers import key_client, login, make_api_key, make_user, make_workspace
from conftest import REQUIRED_ENV, captured_text, inference_config
from fastapi import FastAPI
from lkap_contracts.agent_config import ProviderRef
from sqlalchemy import select

from lkap_api.custom_models.router import get_in_flight, get_probe_timeouts, get_ws_connector
from lkap_api.custom_models.service import InFlight, Timeouts
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import AuditLog, ProviderModel
from lkap_api.db.session import Database

#: Shaped like an OpenAI project key; never a real one.
FAKE_OPENAI_KEY = "sk-proj-Qm7Xv2Lp9Rt4Wz8Nk3Hb6Jd1Fs5Gc0Ya2Ue4Io7"
#: Shaped like an OpenRouter key; never a real one.
FAKE_OPENROUTER_KEY = "sk-or-v1-7c1e2a9d41e84b6fa0c5d2e19b7a6c83f4d0e1a2b3c4d5e6"


def _fragments(value: str, size: int = 6) -> list[str]:
    return [value[i : i + size] for i in range(len(value) - size + 1)]


def _assert_no_fragment(text: str, value: str) -> None:
    leaked = [fragment for fragment in _fragments(value) if fragment in text]
    assert not leaked, f"a fragment of the value leaked: {leaked[:3]}"


@dataclass
class Vendor:
    """The recorded fake vendor behind the api's outbound client."""

    requests: list[httpx.Request] = field(default_factory=list)
    handler: Callable[[httpx.Request], httpx.Response] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.handler is not None:
            return self.handler(request)
        return _chat_ok(request)


def _chat_ok(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content) if request.content else {}
    message: dict[str, Any] = {"role": "assistant", "content": "ok"}
    if body.get("tools"):
        message = {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "type": "function"}]}
    return httpx.Response(
        200,
        json={
            "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 1},
        },
    )


@pytest.fixture
def vendor(app: FastAPI) -> Iterator[Vendor]:
    from lkap_api.deps import get_http_client

    fake = Vendor()

    async def override() -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as client:
            yield client

    app.dependency_overrides[get_http_client] = override
    yield fake
    app.dependency_overrides.pop(get_http_client, None)


class FakeWs:
    """A realtime vendor that answers the handshake (or stays silent)."""

    def __init__(self, replies: list[dict[str, Any]], *, silent: bool = False) -> None:
        self.replies = replies
        self.silent = silent
        self.opened: list[str] = []

    @asynccontextmanager
    async def connect(self, url: str, headers: Mapping[str, str]) -> AsyncIterator[FakeWs]:
        self.opened.append(url)
        yield self

    async def send_json(self, data: Mapping[str, Any]) -> None:
        return None

    async def receive_json(self) -> dict[str, Any]:
        if self.silent:
            import asyncio

            await asyncio.sleep(3600)
        if not self.replies:
            raise ConnectionError("closed")
        return self.replies.pop(0)


async def _key(
    admin_client: httpx.AsyncClient, provider_id: str, api_key: str = FAKE_OPENAI_KEY
) -> dict[str, Any]:
    created = await admin_client.post(
        "/v1/credentials", json={"provider_id": provider_id, "label": "k", "secrets": {"api_key": api_key}}
    )
    assert created.status_code == 201, created.text
    body: dict[str, Any] = created.json()
    return body


def _test(provider_id: str) -> str:
    return f"/v1/providers/{provider_id}/test-model"


# ---------------------------------------------------------------------------- happy path
async def test_a_passing_llm_test_is_recorded_with_detected_tools(
    admin_client: httpx.AsyncClient, vendor: Vendor
) -> None:
    key = await _key(admin_client, "openai-llm")

    response = await admin_client.post(_test("openai-llm"), json={"model": "gpt-4.1-nano-2026"})

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ok"] is True and result["cached"] is False
    assert result["detected"]["tools"] is True and result["detected"]["source"] == "detected"
    assert result["latency_ms"] is not None and result["record_id"]
    assert [p["name"] for p in result["probes"]] == ["basic", "tools"]
    assert result["sample"] == "ok"
    assert result["cost_estimate_usd"] is None
    assert result["cost_note"].startswith("no price on file for this model; the probe used 22 tokens")
    assert len(vendor.requests) == 2
    assert vendor.requests[0].headers["authorization"] == f"Bearer {FAKE_OPENAI_KEY}"

    record = (await admin_client.get("/v1/providers/openai-llm/models/gpt-4.1-nano-2026")).json()
    assert record["last_test_ok"] is True
    assert record["last_test_fingerprint"] == key["fingerprint"]
    assert record["last_test_credential_id"] == key["id"]
    assert record["detected"]["tools"] is True


async def test_a_priced_model_gets_a_cost_estimate(admin_client: httpx.AsyncClient, vendor: Vendor) -> None:
    await _key(admin_client, "openai-llm")

    result = (
        await admin_client.post(_test("openai-llm"), json={"model": "gpt-4.1", "probes": ["basic"]})
    ).json()

    assert result["cost_estimate_usd"] is not None and float(result["cost_estimate_usd"]) > 0
    assert result["cost_note"] == "from the platform's price table"


async def test_openrouter_pricing_comes_from_the_cached_catalog(
    admin_client: httpx.AsyncClient, vendor: Vendor, database: Database
) -> None:
    from lkap_api.db.models import ProviderCatalogCache, new_id, utcnow

    key = await _key(admin_client, "openrouter-llm", FAKE_OPENROUTER_KEY)
    async with database.session() as session:
        session.add(
            ProviderCatalogCache(
                id=new_id(),
                provider_id="openrouter-llm",
                credential_id=key["id"],
                kind="models",
                items=[
                    {
                        "id": "acme/tiny-1",
                        "label": "Tiny",
                        "meta": {"pricing": {"prompt": "0.000001", "completion": "0.000002"}},
                    }
                ],
                fetched_at=utcnow(),
            )
        )

    result = (
        await admin_client.post(_test("openrouter-llm"), json={"model": "acme/tiny-1", "probes": ["basic"]})
    ).json()

    assert result["cost_note"] == "from the vendor catalog's per-token pricing"
    assert float(result["cost_estimate_usd"]) == pytest.approx(10 * 0.000001 + 1 * 0.000002)


async def test_openrouter_tts_cost_counts_the_characters_at_the_catalog_price(
    admin_client: httpx.AsyncClient, vendor: Vendor, database: Database
) -> None:
    """Ask #66: `deepgram/aura-2` read "0.00000"; 6 characters x $0.00003 is $0.00018."""
    from lkap_api.db.models import ProviderCatalogCache, new_id, utcnow

    key = await _key(admin_client, "openrouter-llm", FAKE_OPENROUTER_KEY)
    async with database.session() as session:
        session.add(
            ProviderCatalogCache(
                id=new_id(),
                provider_id="openrouter-tts",
                credential_id=key["id"],
                kind="models",
                items=[
                    {
                        "id": "deepgram/aura-2",
                        "label": "Aura 2",
                        "meta": {"pricing": {"prompt": "0.00003", "completion": "0"}},
                    }
                ],
                fetched_at=utcnow(),
            )
        )
    vendor.handler = lambda request: httpx.Response(
        200, headers={"content-type": "audio/mpeg"}, content=b"\xff\xfb" * 2304
    )

    result = (
        await admin_client.post(
            _test("openrouter-tts"),
            json={"model": "deepgram/aura-2", "fields": {"voice": "aura-2-thalia-en"}},
        )
    ).json()

    assert result["ok"] is True
    assert float(result["cost_estimate_usd"]) == pytest.approx(0.00018)
    assert result["cost_note"] == "from the vendor catalog's input pricing × 6 characters"


# ------------------------------------------------------------------------------- scrub
async def test_a_404_that_echoes_the_key_is_scrubbed_everywhere(
    admin_client: httpx.AsyncClient,
    vendor: Vendor,
    database: Database,
    log_capture: pytest.LogCaptureFixture,
) -> None:
    await _key(admin_client, "openai-llm")
    vendor.handler = lambda request: httpx.Response(
        404,
        json={
            "error": {
                "message": f"The model does not exist or key {request.headers['authorization']} "
                f"(starts {FAKE_OPENAI_KEY[:8]}) cannot use it"
            }
        },
    )

    response = await admin_client.post(_test("openai-llm"), json={"model": "gpt-nonexistent"})

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ok"] is False
    assert "•••" in result["message"]
    assert "does not exist" in result["message"]
    _assert_no_fragment(response.text, FAKE_OPENAI_KEY)
    async with database.session() as session:
        row = (await session.execute(select(ProviderModel))).scalar_one()
    assert row.last_test_ok is False
    assert row.last_test_message is not None and "•••" in row.last_test_message
    _assert_no_fragment(row.last_test_message, FAKE_OPENAI_KEY)
    _assert_no_fragment(captured_text(log_capture), FAKE_OPENAI_KEY)


async def test_a_secret_shaped_model_is_422_without_echo(
    admin_client: httpx.AsyncClient, vendor: Vendor
) -> None:
    await _key(admin_client, "openai-llm")

    response = await admin_client.post(_test("openai-llm"), json={"model": FAKE_OPENROUTER_KEY})

    assert response.status_code == 422
    assert "looks like an API key" in response.json()["error"]["message"]
    _assert_no_fragment(response.text, FAKE_OPENROUTER_KEY)
    assert vendor.requests == []


async def test_a_key_pasted_into_a_voice_field_is_422_without_echo(
    admin_client: httpx.AsyncClient, vendor: Vendor
) -> None:
    await _key(admin_client, "elevenlabs-tts")

    response = await admin_client.post(
        _test("elevenlabs-tts"),
        json={"model": "eleven_turbo_v2_5", "fields": {"voice_id": "sk_" + "a1b2" * 10}},
    )

    assert response.status_code == 422
    _assert_no_fragment(response.text, "a1b2" * 10)
    assert vendor.requests == []


async def test_a_metadata_base_url_is_refused_before_any_call(
    admin_client: httpx.AsyncClient, vendor: Vendor
) -> None:
    await _key(admin_client, "openai-compatible-llm")

    response = await admin_client.post(
        _test("openai-compatible-llm"),
        json={"model": "local-model", "fields": {"base_url": "http://169.254.169.254/v1"}},
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "blocked_destination"
    assert vendor.requests == []


async def test_no_key_is_a_422(admin_client: httpx.AsyncClient, vendor: Vendor) -> None:
    response = await admin_client.post(_test("anthropic-llm"), json={"model": "claude-sonnet-4-6"})

    assert response.status_code == 422
    assert "no key" in response.json()["error"]["message"]
    assert vendor.requests == []


# -------------------------------------------------------------------------- cache + caps
async def test_a_repeat_within_ten_minutes_is_cached_and_force_reruns(
    admin_client: httpx.AsyncClient, vendor: Vendor
) -> None:
    await _key(admin_client, "openai-llm")
    body = {"model": "gpt-4.1-nano-2026", "probes": ["basic"]}

    first = (await admin_client.post(_test("openai-llm"), json=body)).json()
    cached = (await admin_client.post(_test("openai-llm"), json=body)).json()
    calls_after_cache = len(vendor.requests)
    forced = (await admin_client.post(_test("openai-llm"), json={**body, "force": True})).json()

    assert first["cached"] is False
    assert cached["cached"] is True and cached["ok"] is True and cached["probes"] == []
    assert cached["record_id"] == first["record_id"]
    assert calls_after_cache == 1, "a cached answer sends nothing to the vendor"
    assert forced["cached"] is False
    assert len(vendor.requests) == 2


async def test_a_rotated_key_runs_a_fresh_probe(admin_client: httpx.AsyncClient, vendor: Vendor) -> None:
    key = await _key(admin_client, "openai-llm")
    body = {"model": "gpt-4.1-nano-2026", "probes": ["basic"]}
    await admin_client.post(_test("openai-llm"), json=body)

    rotated = await admin_client.put(
        f"/v1/credentials/{key['id']}", json={"secrets": {"api_key": "sk-proj-rotated-" + "Z9" * 16}}
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["fingerprint"] != key["fingerprint"]
    again = (await admin_client.post(_test("openai-llm"), json=body)).json()

    assert again["cached"] is False
    assert len(vendor.requests) == 2


async def test_the_eleventh_test_in_a_minute_is_429_with_retry_after(
    admin_client: httpx.AsyncClient, vendor: Vendor
) -> None:
    await _key(admin_client, "openai-llm")
    body = {"model": "gpt-4.1-nano-2026", "probes": ["basic"], "force": True}

    statuses = [(await admin_client.post(_test("openai-llm"), json=body)).status_code for _ in range(10)]
    eleventh = await admin_client.post(_test("openai-llm"), json=body)

    assert statuses == [200] * 10
    assert eleventh.status_code == 429
    error = eleventh.json()["error"]
    assert error["code"] == "rate_limited"
    assert error["details"]["retry_after"] > 0
    assert len(vendor.requests) == 10


async def test_a_third_concurrent_test_is_429(
    app: FastAPI, admin_client: httpx.AsyncClient, vendor: Vendor
) -> None:
    await _key(admin_client, "openai-llm")
    counter = InFlight()
    app.dependency_overrides[get_in_flight] = lambda: counter
    try:
        with ExitStack() as held:
            held.enter_context(counter.slot(DEFAULT_WORKSPACE_ID))
            held.enter_context(counter.slot(DEFAULT_WORKSPACE_ID))
            third = await admin_client.post(
                _test("openai-llm"), json={"model": "gpt-4.1-nano", "force": True}
            )
        after = await admin_client.post(_test("openai-llm"), json={"model": "gpt-4.1-nano", "force": True})
    finally:
        app.dependency_overrides.pop(get_in_flight, None)

    assert third.status_code == 429
    assert third.json()["error"]["details"]["retry_after"] > 0
    assert after.status_code == 200, "released slots are usable again"
    assert counter.running(DEFAULT_WORKSPACE_ID) == 0


# ---------------------------------------------------------------------- LiveKit Inference
async def test_livekit_inference_posts_to_the_gateway_with_a_signed_jwt(
    admin_client: httpx.AsyncClient, vendor: Vendor
) -> None:
    response = await admin_client.post(
        _test("livekit-inference-llm"), json={"model": "openai/gpt-4o-mini", "probes": ["basic"]}
    )

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    request = vendor.requests[0]
    assert str(request.url) == "https://agent-gateway.livekit.cloud/v1/chat/completions"
    token = request.headers["authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, REQUIRED_ENV["LIVEKIT_API_SECRET"], algorithms=["HS256"])
    assert claims["sub"] == "agent"
    assert claims["inference"] == {"perform": True}
    assert claims["iss"] == REQUIRED_ENV["LIVEKIT_API_KEY"]
    assert claims["exp"] - claims["nbf"] <= 600
    assert token not in response.text


async def test_livekit_inference_without_a_connection_is_422(
    app: FastAPI, database: Database, vendor: Vendor
) -> None:
    workspace_id = await make_workspace(database, "no-connection")
    _, raw = await make_api_key(database, ["*"], workspace_id=workspace_id)

    async with key_client(app, raw) as client:
        response = await client.post(_test("livekit-inference-llm"), json={"model": "openai/gpt-4o-mini"})

    assert response.status_code == 422
    assert "no LiveKit connection" in response.json()["error"]["message"]
    assert vendor.requests == []


# ------------------------------------------------------------------- no probe / images
async def test_an_image_model_is_never_probed(admin_client: httpx.AsyncClient, vendor: Vendor) -> None:
    await _key(admin_client, "openrouter-llm", FAKE_OPENROUTER_KEY)

    response = await admin_client.post(_test("openrouter-image-gen"), json={"model": "openai/gpt-image-1"})

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ok"] is None
    assert "images cost money" in result["message"]
    assert vendor.requests == []


async def test_an_entry_without_a_probe_answers_none(admin_client: httpx.AsyncClient, vendor: Vendor) -> None:
    response = await admin_client.post(_test("assemblyai-stt"), json={"model": "universal-3-5-pro"})

    assert response.status_code == 200
    assert response.json()["ok"] is None
    assert (
        response.json()["message"] == "no test for this provider; the model is checked on the first session"
    )
    assert vendor.requests == []


async def test_a_vad_provider_has_no_model_to_test(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post(_test("silero-vad"), json={"model": "anything"})

    assert response.status_code in (404, 422)


async def test_bey_gets_the_avatar_and_never_posts_a_session(
    admin_client: httpx.AsyncClient, vendor: Vendor
) -> None:
    await _key(admin_client, "bey-avatar", "bey-placeholder-key-000000")
    vendor.handler = lambda request: httpx.Response(200, json={"id": "b9be11b8-89fb-4227-8f86-4a881393cbdb"})

    response = await admin_client.post(
        _test("bey-avatar"), json={"model": "b9be11b8-89fb-4227-8f86-4a881393cbdb"}
    )

    assert response.json()["ok"] is True
    assert [r.method for r in vendor.requests] == ["GET"]


# ---------------------------------------------------------------------------- realtime
async def test_a_realtime_handshake_passes_on_session_created(
    app: FastAPI, admin_client: httpx.AsyncClient, vendor: Vendor
) -> None:
    await _key(admin_client, "openai-realtime")
    ws = FakeWs([{"type": "session.created"}])
    app.dependency_overrides[get_ws_connector] = lambda: ws
    try:
        response = await admin_client.post(_test("openai-realtime"), json={"model": "gpt-realtime"})
    finally:
        app.dependency_overrides.pop(get_ws_connector, None)

    assert response.json()["ok"] is True
    assert ws.opened == ["wss://api.openai.com/v1/realtime?model=gpt-realtime"]
    assert vendor.requests == []


async def test_a_silent_realtime_vendor_fails_at_the_budget(
    app: FastAPI, admin_client: httpx.AsyncClient, vendor: Vendor
) -> None:
    await _key(admin_client, "google-realtime", "AIza-placeholder-google-key-000000")
    app.dependency_overrides[get_ws_connector] = lambda: FakeWs([], silent=True)
    app.dependency_overrides[get_probe_timeouts] = lambda: Timeouts(realtime=0.05)
    try:
        response = await admin_client.post(_test("google-realtime"), json={"model": "gemini-3.8-live"})
    finally:
        app.dependency_overrides.pop(get_ws_connector, None)
        app.dependency_overrides.pop(get_probe_timeouts, None)

    result = response.json()
    assert result["ok"] is False
    assert "no answer within 0.05 s" in result["message"]


# -------------------------------------------------------------------------- gating + audit
async def test_a_builder_key_may_test_a_viewer_key_may_not(
    app: FastAPI, database: Database, admin_client: httpx.AsyncClient, vendor: Vendor
) -> None:
    await _key(admin_client, "openai-llm")
    _, builder = await make_api_key(database, ["agents:write", "providers:read", "sessions:write"])
    _, viewer = await make_api_key(database, ["agents:read", "providers:read"])
    body = {"model": "gpt-4.1-nano", "probes": ["basic"], "force": True}

    async with key_client(app, builder) as client:
        tested = await client.post(_test("openai-llm"), json=body)
        declared = await client.put(
            "/v1/providers/openai-llm/models/gpt-4.1-nano", json={"declared": {"vision": False}}
        )
    async with key_client(app, viewer) as client:
        refused = await client.post(_test("openai-llm"), json=body)

    assert tested.status_code == 200, tested.text
    assert declared.status_code == 403, "declaring capabilities stays admin + providers:write"
    assert refused.status_code == 403


async def test_a_builder_member_may_test(
    app: FastAPI, database: Database, admin_client: httpx.AsyncClient, vendor: Vendor
) -> None:
    await _key(admin_client, "openai-llm")
    await make_user(database, "builder-tm@example.com", role="builder")

    async with await login(app, "builder-tm@example.com") as builder:
        response = await builder.post(
            _test("openai-llm"), json={"model": "gpt-4.1-nano", "probes": ["basic"]}
        )

    assert response.status_code == 200, response.text


async def test_the_audit_row_carries_only_provider_model_ok_and_latency(
    admin_client: httpx.AsyncClient, vendor: Vendor, database: Database
) -> None:
    await _key(admin_client, "openai-llm")

    await admin_client.post(_test("openai-llm"), json={"model": "gpt-4.1-nano", "probes": ["basic"]})

    async with database.session() as session:
        rows = (
            (await session.execute(select(AuditLog).where(AuditLog.action.contains("test")))).scalars().all()
        )
    assert [row.action for row in rows] == ["provider.test_model"], "no second, generic route row"
    assert set(rows[0].payload) == {"provider_id", "model", "ok", "latency_ms"}
    assert rows[0].payload["provider_id"] == "openai-llm" and rows[0].payload["ok"] is True


# ------------------------------------------------------------- the validation loop (D-V4-26)
async def test_a_passing_test_clears_the_untested_warning(
    admin_client: httpx.AsyncClient, vendor: Vendor
) -> None:
    """An unlisted id warns until Test model passes with the slot's current key (live check §4 item 7)."""
    key = await _key(admin_client, "openai-llm")
    config = inference_config()
    config.pipeline.llm = ProviderRef(
        provider_id="openai-llm", credential_id=key["id"], model="gpt-4.1-nano-2026"
    )
    created = await admin_client.post(
        "/v1/agents", json={"name": "Custom", "config": json.loads(config.model_dump_json())}
    )
    agent_id = created.json()["id"]

    async def llm_warnings() -> list[str]:
        result = (await admin_client.post(f"/v1/agents/{agent_id}/validate")).json()
        return [w for w in result["warnings"] if w.startswith("pipeline.llm")]

    before = await llm_warnings()
    tested = await admin_client.post(
        _test("openai-llm"), json={"model": "gpt-4.1-nano-2026", "probes": ["basic"]}
    )
    after = await llm_warnings()

    assert len(before) == 1 and "run Test model" in before[0]
    assert tested.json()["ok"] is True
    assert after == []


async def test_a_keyless_entry_that_is_not_inference_never_gets_a_livekit_token(
    admin_client: httpx.AsyncClient, vendor: Vendor, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lkap_contracts.providers import get

    import lkap_api.custom_models.router as router_module

    bedrock = get("aws-bedrock-llm").model_copy(update={"probe": "openai_chat"})
    monkeypatch.setattr(router_module, "get", lambda provider_id: bedrock)

    response = await admin_client.post(_test("aws-bedrock-llm"), json={"model": "amazon.nova-2-lite-v1:0"})

    assert response.status_code == 422
    assert "no key the api can test with" in response.json()["error"]["message"]
    assert vendor.requests == []
