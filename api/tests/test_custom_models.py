"""Custom model ids: no echo, records, capabilities, the models routes (V4-07; D-V4-23, D-V4-24, R-V4-21).

Every "key" below is a placeholder shaped like a vendor key; none is real.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from auth_helpers import login, make_user
from conftest import inference_config
from fastapi import FastAPI
from lkap_contracts.agent_config import ProviderRef
from lkap_contracts.api_models import CatalogItem, ProviderModelOut
from lkap_contracts.providers import ModelCapabilities, get
from sqlalchemy import select

from lkap_api.custom_models import capabilities, records
from lkap_api.custom_models.ids import REDACTED, scrub
from lkap_api.custom_models.probes import Usage
from lkap_api.custom_models.service import estimate_cost
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import AuditLog
from lkap_api.db.session import Database

#: Shaped like an OpenRouter key; never a real one.
FAKE_OPENROUTER_KEY = "sk-or-v1-9f3c2a7d41e84b6fa0c5d2e19b7a6c83f4d0e1a2b3c4d5e6"


def _fragments(value: str, size: int = 6) -> list[str]:
    return [value[i : i + size] for i in range(len(value) - size + 1)]


def _assert_no_fragment(text: str, value: str) -> None:
    leaked = [fragment for fragment in _fragments(value) if fragment in text]
    assert not leaked, f"a fragment of the value leaked: {leaked[:3]}"


# ------------------------------------------------------------------------------- scrub
def test_scrub_removes_the_value_and_its_ends() -> None:
    secret = "sk-live-abcdef0123456789zyxwvu"
    text = f"401: invalid key {secret}; key starts {secret[:6]}… ends …{secret[-6:]}"

    out = scrub(text, [secret])

    assert secret not in out
    assert secret[:6] not in out and secret[-6:] not in out
    assert REDACTED in out


def test_scrub_leaves_short_secrets_fragments_alone_and_truncates() -> None:
    assert scrub("the word hello stays", ["hel"]) == "the word •••lo stays"
    assert len(scrub("x" * 900, [])) == 500


# ------------------------------------------------------------------------ no echo (R-V4-21)
def _openrouter_config(model: str, credential_id: str) -> dict[str, Any]:
    config = inference_config()
    config.pipeline.llm = ProviderRef(provider_id="openrouter-llm", credential_id=credential_id, model=model)
    dumped: dict[str, Any] = json.loads(config.model_dump_json())
    return dumped


async def _key(admin_client: httpx.AsyncClient, provider_id: str, api_key: str = "placeholder-key") -> str:
    created = await admin_client.post(
        "/v1/credentials", json={"provider_id": provider_id, "label": "k", "secrets": {"api_key": api_key}}
    )
    assert created.status_code == 201, created.text
    credential_id: str = created.json()["id"]
    return credential_id


async def test_a_pasted_key_as_the_model_is_one_error_that_never_echoes(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    credential_id = await _key(admin_client, "openrouter-llm", api_key="sk-or-v1-placeholder-credential")

    response = await admin_client.post(
        "/v1/agents", json={"name": "Leaky", "config": _openrouter_config(FAKE_OPENROUTER_KEY, credential_id)}
    )

    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    llm_errors = [i for i in details["issues"] if i["path"] == "pipeline.llm" and i["severity"] == "error"]
    assert len(llm_errors) == 1
    assert "looks like an API key" in llm_errors[0]["message"]
    assert not [w for w in details["warnings"] if w.startswith("pipeline.llm")], "no second finding for it"
    _assert_no_fragment(response.text, FAKE_OPENROUTER_KEY)
    _assert_no_fragment(json.dumps(details["errors"]), FAKE_OPENROUTER_KEY)
    async with database.session() as session:
        payloads = [json.dumps(row.payload) for row in (await session.execute(select(AuditLog))).scalars()]
    for payload in payloads:
        _assert_no_fragment(payload, FAKE_OPENROUTER_KEY)


async def test_a_pasted_key_in_an_enum_voice_field_is_an_error_with_no_fragment(
    admin_client: httpx.AsyncClient,
) -> None:
    credential_id = await _key(admin_client, "google-realtime", api_key="AIza-placeholder-google-key")
    config = inference_config()
    config.pipeline.mode = "realtime"
    config.pipeline.stt = config.pipeline.llm = config.pipeline.tts = None
    fake_google_key = "AIzaSyD4f8Qm2LxW7vT9cR1pN6kJ3hB0eZyUa5s"
    config.pipeline.realtime = ProviderRef(
        provider_id="google-realtime", credential_id=credential_id, fields={"voice": fake_google_key}
    )

    response = await admin_client.post(
        "/v1/agents", json={"name": "Leaky voice", "config": json.loads(config.model_dump_json())}
    )

    assert response.status_code == 422, response.text
    errors = response.json()["error"]["details"]["errors"]
    assert any("field 'voice' must be one of" in e for e in errors), "the enum error names the field"
    assert any("field 'voice': the value looks like an API key" in e for e in errors)
    _assert_no_fragment(response.text, fake_google_key)


async def test_an_unlisted_model_id_is_a_warning_without_the_value(admin_client: httpx.AsyncClient) -> None:
    credential_id = await _key(admin_client, "openrouter-llm", api_key="sk-or-v1-placeholder-credential")

    response = await admin_client.post(
        "/v1/agents",
        json={"name": "Custom", "config": _openrouter_config("vendor/brand-new-model-9", credential_id)},
    )
    assert response.status_code == 201, response.text
    validated = await admin_client.post(f"/v1/agents/{response.json()['id']}/validate")

    warnings = [w for w in validated.json()["warnings"] if w.startswith("pipeline.llm")]
    assert len(warnings) == 1
    assert "not in the suggestion list or the live catalog for 'openrouter-llm'" in warnings[0]
    assert "brand-new-model" not in warnings[0]


# ------------------------------------------------------------- validation from the database
async def test_the_database_context_knows_catalogs_records_and_key_rotation(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    credential_id = await _key(admin_client, "openai-llm", api_key="sk-placeholder-openai-one")
    config = inference_config()
    config.pipeline.llm = ProviderRef(
        provider_id="openai-llm", credential_id=credential_id, model="gpt-4.1-nano-2026"
    )
    created = await admin_client.post(
        "/v1/agents", json={"name": "N", "config": json.loads(config.model_dump_json())}
    )
    agent_id = created.json()["id"]

    async def llm_warnings() -> list[str]:
        result = (await admin_client.post(f"/v1/agents/{agent_id}/validate")).json()
        return [w for w in result["warnings"] if w.startswith("pipeline.llm")]

    assert len(await llm_warnings()) == 1

    # A passing test with the current key silences it...
    fingerprint = (await admin_client.get(f"/v1/credentials/{credential_id}")).json()["fingerprint"]
    async with database.session() as session:
        await records.upsert(
            session,
            workspace_id=DEFAULT_WORKSPACE_ID,
            spec=get("openai-llm"),
            model_id="gpt-4.1-nano-2026",
            last_test_at=dt.datetime.now(dt.UTC),
            last_test_ok=True,
            last_test_fingerprint=fingerprint,
            last_test_credential_id=credential_id,
        )
        await session.commit()
    assert await llm_warnings() == []

    # ...until the key is rotated: a new fingerprint no longer matches.
    rotated = await admin_client.put(
        f"/v1/credentials/{credential_id}", json={"secrets": {"api_key": "sk-placeholder-openai-two"}}
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["fingerprint"] != fingerprint
    assert len(await llm_warnings()) == 1


async def test_a_cached_live_catalog_listing_silences_the_warning(admin_client: httpx.AsyncClient) -> None:
    credential_id = await _key(admin_client, "openai-llm")
    await admin_client.put("/v1/providers/openai-llm/settings", json={"default_credential_id": credential_id})
    with respx.mock:
        respx.get("https://api.openai.com/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "gpt-4.1-nano-2026"}]})
        )
        await admin_client.get("/v1/providers/openai-llm/catalog")
    config = inference_config()
    config.pipeline.llm = ProviderRef(
        provider_id="openai-llm", credential_id=credential_id, model="gpt-4.1-nano-2026"
    )
    created = await admin_client.post(
        "/v1/agents", json={"name": "C", "config": json.loads(config.model_dump_json())}
    )

    validated = await admin_client.post(f"/v1/agents/{created.json()['id']}/validate")

    assert not [w for w in validated.json()["warnings"] if w.startswith("pipeline.llm")]


# ------------------------------------------------------------------------- capabilities
def _record(**values: Any) -> ProviderModelOut:
    now = dt.datetime.now(dt.UTC)
    base: dict[str, Any] = {
        "id": "r1",
        "provider_id": "openrouter-llm",
        "provider_home": "openrouter-llm",
        "kind": "llm",
        "model_id": "vendor/m",
        "created_at": now,
        "updated_at": now,
    }
    return ProviderModelOut.model_validate({**base, **values})


def test_resolve_capabilities_prefers_declared_then_detected_then_catalog_then_registry() -> None:
    spec = get("openrouter-llm")
    item = CatalogItem(
        id="vendor/m",
        label="M",
        meta={
            "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
            "supported_parameters": ["tools"],
            "context_length": 128000,
        },
    )
    record = _record(
        declared=ModelCapabilities(vision=False),
        detected=ModelCapabilities(vision=True, tools=False, streaming=True),
    )

    caps = capabilities.resolve_capabilities(spec, "vendor/m", record, item)

    assert caps.vision is False and caps.source == "declared"
    assert caps.tools is False, "detected beats the catalog for tools"
    assert caps.streaming is True
    assert caps.context_tokens == 128000, "only the catalog knows the context window"

    from_detected = capabilities.resolve_capabilities(
        spec, "vendor/m", _record(detected=ModelCapabilities(vision=True)), item
    )
    assert from_detected.vision is True and from_detected.source == "detected"

    from_catalog = capabilities.resolve_capabilities(spec, "vendor/m", None, item)
    assert from_catalog.vision is True and from_catalog.source == "catalog"
    assert from_catalog.tools is True

    listed = capabilities.resolve_capabilities(
        get("livekit-inference-llm"), "google/gemma-4-31b-it", None, None
    )
    assert listed.vision is False and listed.source == "registry"
    assert listed.tools is True

    unknown = capabilities.resolve_capabilities(spec, "vendor/unknown", None, None)
    assert unknown.vision is None and unknown.source is None


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        (
            {"capabilities": {"image_input": {"supported": True}}, "max_input_tokens": 200000},
            {"vision": True, "context_tokens": 200000},
        ),
        ({"capabilities": {"vision": False, "function_calling": True}}, {"vision": False, "tools": True}),
        (
            {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
            {"vision": True, "audio_in": False, "audio_out": False},
        ),
        (
            {"inputModalities": ["TEXT", "IMAGE"], "responseStreamingSupported": True},
            {"vision": True, "streaming": True},
        ),
        ({"supportedGenerationMethods": ["bidiGenerateContent"]}, {"audio_in": True, "audio_out": True}),
        ({"supportsTools": True, "supportsImageInput": False}, {"tools": True, "vision": False}),
        ({"can_do_text_to_speech": True}, {"audio_out": True}),
        ({"id": "gpt-4.1", "owned_by": "openai"}, {}),
    ],
)
def test_catalog_meta_readers(meta: dict[str, Any], expected: dict[str, Any]) -> None:
    caps = capabilities.catalog_capabilities(meta).model_dump(exclude_none=True)
    assert caps == expected


# ------------------------------------------------------------------------ models routes
async def test_models_list_is_newest_tested_first_and_custom_leaves_out_registry_ids(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    spec = get("openai-llm")
    now = dt.datetime.now(dt.UTC)
    async with database.session() as session:
        for model_id, tested in [
            ("ft:gpt-4.1:org::old", now - dt.timedelta(days=2)),
            ("gpt-4.1", now - dt.timedelta(hours=1)),
            ("ft:gpt-4.1:org::new", now),
            ("never-tested", None),
        ]:
            await records.upsert(
                session, workspace_id=DEFAULT_WORKSPACE_ID, spec=spec, model_id=model_id, last_test_at=tested
            )
        await session.commit()

    everything = await admin_client.get("/v1/providers/openai-llm/models")
    custom = await admin_client.get("/v1/providers/openai-llm/models", params={"custom": "true", "limit": 2})

    assert everything.status_code == 200, everything.text
    assert [r["model_id"] for r in everything.json()["items"]] == [
        "ft:gpt-4.1:org::new",
        "gpt-4.1",
        "ft:gpt-4.1:org::old",
        "never-tested",
    ]
    assert [r["model_id"] for r in custom.json()["items"]] == ["ft:gpt-4.1:org::new", "ft:gpt-4.1:org::old"]


async def test_declaring_capabilities_round_trips_an_id_with_slashes(admin_client: httpx.AsyncClient) -> None:
    put = await admin_client.put(
        "/v1/providers/openrouter-stt/models/vendor/speech-model:beta",
        json={"declared": {"vision": False, "audio_in": True}},
    )
    got = await admin_client.get("/v1/providers/openrouter-stt/models/vendor/speech-model:beta")

    assert put.status_code == 200, put.text
    body = got.json()
    assert body["model_id"] == "vendor/speech-model:beta"
    assert body["provider_home"] == "openrouter-llm", "an alias entry records under its credential home"
    assert body["kind"] == "stt"
    assert body["declared"] == {
        "vision": False,
        "tools": None,
        "audio_in": True,
        "audio_out": None,
        "streaming": None,
        "context_tokens": None,
        "source": "declared",
    }


async def test_an_unknown_record_is_a_404(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.get("/v1/providers/openai-llm/models/never-seen")
    assert response.status_code == 404


async def test_a_secret_looking_model_id_in_the_path_is_a_422_without_echo(
    admin_client: httpx.AsyncClient,
) -> None:
    put = await admin_client.put(
        f"/v1/providers/openrouter-llm/models/{FAKE_OPENROUTER_KEY}", json={"declared": {"vision": True}}
    )
    got = await admin_client.get(f"/v1/providers/openrouter-llm/models/{FAKE_OPENROUTER_KEY}")

    assert put.status_code == got.status_code == 422
    _assert_no_fragment(put.text + got.text, FAKE_OPENROUTER_KEY)


async def test_models_routes_refuse_kinds_that_take_no_model(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.get("/v1/providers/silero-vad/models")
    assert response.status_code in (404, 422)


async def test_declaring_needs_admin_while_a_builder_may_read(
    app: FastAPI, database: Database, settings: object
) -> None:
    await make_user(database, "builder@example.com", role="builder")
    async with await login(app, "builder@example.com") as builder:
        read = await builder.get("/v1/providers/openai-llm/models")
        write = await builder.put(
            "/v1/providers/openai-llm/models/custom-x", json={"declared": {"vision": True}}
        )

    assert read.status_code == 200, read.text
    assert write.status_code == 403


# ----------------------------------------------------- the severity split (R-V4-31, V4-08)
#: 32 hex characters: a Simli- or Tavus-style key *or* a vendor's real id.
HEX32 = "9f3c2a7d41e84b6fa0c5d2e19b7a6c83"


async def _simli_agent(admin_client: httpx.AsyncClient, face_id: str) -> httpx.Response:
    created = await admin_client.post(
        "/v1/credentials",
        json={
            "provider_id": "simli-avatar",
            "label": "simli",
            "secrets": {"simli_config.api_key": "placeholder-simli-key"},
        },
    )
    assert created.status_code == 201, created.text
    config = inference_config()
    config.pipeline.avatar = ProviderRef(
        provider_id="simli-avatar",
        credential_id=created.json()["id"],
        fields={"simli_config.face_id": face_id},
    )
    return await admin_client.post(
        "/v1/agents", json={"name": "Simli", "config": json.loads(config.model_dump_json())}
    )


async def test_a_32_hex_face_id_is_one_value_free_warning_and_the_agent_publishes(
    admin_client: httpx.AsyncClient,
) -> None:
    created = await _simli_agent(admin_client, HEX32)
    assert created.status_code == 201, created.text
    agent_id = created.json()["id"]

    validated = (await admin_client.post(f"/v1/agents/{agent_id}/validate")).json()
    published = await admin_client.put(f"/v1/agents/{agent_id}", json={"published": True})

    path = "pipeline.avatar.fields.simli_config.face_id"
    at_path = [i for i in validated["issues"] if i["path"] == path]
    assert len(at_path) == 1
    assert at_path[0]["severity"] == "warning"
    assert "looks like an API key; if it is the vendor's id, ignore this" in at_path[0]["message"]
    assert not [i for i in validated["issues"] if i["severity"] == "error"]
    _assert_no_fragment(json.dumps(validated), HEX32)
    assert published.status_code == 200, published.text
    assert published.json()["published"] is True


async def test_a_prefixed_key_in_face_id_is_still_an_error(admin_client: httpx.AsyncClient) -> None:
    fake_key = "sk_" + HEX32
    response = await _simli_agent(admin_client, fake_key)

    assert response.status_code == 422, response.text
    issues = response.json()["error"]["details"]["issues"]
    at_path = [i for i in issues if i["path"] == "pipeline.avatar.fields.simli_config.face_id"]
    assert [i["severity"] for i in at_path] == ["error"]
    _assert_no_fragment(response.text, HEX32)


async def test_a_32_hex_model_id_stays_an_error(admin_client: httpx.AsyncClient) -> None:
    credential_id = await _key(admin_client, "openrouter-llm", api_key="sk-or-v1-placeholder-credential")

    response = await admin_client.post(
        "/v1/agents", json={"name": "Hex model", "config": _openrouter_config(HEX32, credential_id)}
    )

    assert response.status_code == 422, response.text
    issues = response.json()["error"]["details"]["issues"]
    assert [i["severity"] for i in issues if i["path"] == "pipeline.llm"] == ["error"]
    _assert_no_fragment(response.text, HEX32)


def test_no_new_api_source_path_has_a_credentials_prefixed_segment() -> None:
    src = Path(__file__).resolve().parents[1] / "src"
    offenders = [
        path
        for path in src.rglob("*")
        if "__pycache__" not in path.parts
        and any(part.startswith("credentials") for part in path.relative_to(src).parts)
    ]
    assert offenders == []


# ------------------------------------------------------------ the cost estimate (ask #66)
@pytest.mark.parametrize(
    ("pricing", "usage", "cost", "note"),
    [
        # Deepgram Aura-2 on OpenRouter: $30/1M characters, billed per input character.
        ({"prompt": "0.00003", "completion": "0"}, Usage(chars=6), Decimal("0.00018"), "× 6 characters"),
        # Gemini TTS: the input side only; output audio tokens are unknowable from a speech answer.
        (
            {"prompt": "0.0000005", "completion": "0.000009"},
            Usage(chars=6),
            Decimal("0.0000030"),
            "output audio is priced separately and not counted",
        ),
        # An LLM keeps the per-token path.
        (
            {"prompt": "0.000001", "completion": "0.000002"},
            Usage(tokens_in=10, tokens_out=1),
            Decimal("0.000012"),
            "per-token",
        ),
    ],
    ids=["deepgram-aura-2", "gemini-tts", "llm-tokens"],
)
def test_estimate_cost_prices_what_the_probe_used(
    pricing: dict[str, str], usage: Usage, cost: Decimal, note: str
) -> None:
    item = CatalogItem(id="m", label="m", meta={"pricing": pricing})

    estimate, cost_note = estimate_cost(get("openrouter-tts"), "m", usage, item)

    assert estimate == cost
    assert note in cost_note


def test_estimate_cost_never_reads_catalog_pricing_as_a_zero_for_unpriced_units() -> None:
    item = CatalogItem(id="m", label="m", meta={"pricing": {"prompt": "0.000001", "completion": "0.000002"}})

    estimate, cost_note = estimate_cost(get("openrouter-stt"), "m", Usage(audio_s_in=1.0), item)

    assert estimate is None
    assert cost_note.startswith("no price on file for this model; the probe used 1 s of audio")
