"""Agent CRUD, pack seeding and registry validation."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from conftest import create_agent, inference_config
from lkap_contracts.agent_config import AgentConfig, PipelineConfig, ProviderRef
from lkap_contracts.packs import PackManifest

from lkap_api.config_service import seed_config_from_manifest, validate_agent_config
from lkap_api.routers.agents import slugify


async def _credential(admin_client: httpx.AsyncClient, provider_id: str, key: str = "k-1234") -> str:
    response = await admin_client.post(
        "/v1/credentials",
        json={"provider_id": provider_id, "label": provider_id, "secrets": {"api_key": key}},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


# ------------------------------------------------------------------------------ slugs
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Insurance Claim", "insurance-claim"),
        ("  Héllo Wörld!  ", "hello-world"),
        ("***", "agent"),
    ],
)
def test_slugify_produces_url_safe_slugs(name: str, expected: str) -> None:
    assert slugify(name) == expected


async def test_duplicate_names_get_distinct_slugs(admin_client: httpx.AsyncClient) -> None:
    first = await create_agent(admin_client, name="Claims desk", published=False)
    second = await create_agent(admin_client, name="Claims desk", published=False)

    assert first["slug"] == "claims-desk"
    assert second["slug"] == "claims-desk-2"


# -------------------------------------------------------------------------- validation
def test_valid_inference_config_has_no_errors() -> None:
    result = validate_agent_config(inference_config(), credential_providers={})

    assert result.ok is True
    assert result.errors == []


def test_missing_cascaded_slots_are_errors() -> None:
    config = AgentConfig(instructions="x", pipeline=PipelineConfig(mode="cascaded"))

    result = validate_agent_config(config, credential_providers={})

    assert result.ok is False
    assert len(result.errors) == 3


def test_realtime_mode_requires_a_realtime_provider() -> None:
    config = AgentConfig(instructions="x", pipeline=PipelineConfig(mode="realtime"))

    result = validate_agent_config(config, credential_providers={})

    assert "pipeline.realtime is required when mode is 'realtime'" in result.errors


def test_a_provider_of_the_wrong_kind_is_rejected() -> None:
    config = inference_config()
    config.pipeline.llm = ProviderRef(provider_id="livekit-inference-tts")

    result = validate_agent_config(config, credential_providers={})

    assert any("expected llm" in error for error in result.errors)


def test_a_missing_credential_is_an_error() -> None:
    config = inference_config()
    config.pipeline.llm = ProviderRef(provider_id="openai-llm")

    result = validate_agent_config(config, credential_providers={})

    assert any("requires a credential" in error for error in result.errors)


def test_a_credential_from_another_provider_is_an_error() -> None:
    config = inference_config()
    config.pipeline.llm = ProviderRef(provider_id="openai-llm", credential_id="cred-1")

    result = validate_agent_config(config, credential_providers={"cred-1": "google-llm"})

    assert any("belongs to provider" in error for error in result.errors)


def test_unknown_and_deferred_providers_are_errors() -> None:
    unknown = inference_config()
    unknown.pipeline.llm = ProviderRef(provider_id="made-up")
    deferred = inference_config()
    deferred.pipeline.llm = ProviderRef(provider_id="anthropic-llm", credential_id="c")

    assert any(
        "unknown provider" in e for e in validate_agent_config(unknown, credential_providers={}).errors
    )
    assert any(
        "not available yet" in e
        for e in validate_agent_config(deferred, credential_providers={"c": "anthropic-llm"}).errors
    )


def test_enum_fields_are_constrained() -> None:
    config = AgentConfig(
        instructions="x",
        pipeline=PipelineConfig(
            mode="realtime",
            realtime=ProviderRef(
                provider_id="google-realtime", credential_id="c", fields={"voice": "Not-A-Voice"}
            ),
        ),
    )

    result = validate_agent_config(config, credential_providers={"c": "google-realtime"})

    assert any("must be one of" in error for error in result.errors)


def test_overriding_turn_detection_is_rejected() -> None:
    config = inference_config()
    config.pipeline.turn_handling = {"turn_detection": "vad", "endpointing": {"min_delay": 0.3}}

    result = validate_agent_config(config, credential_providers={})

    assert any("turn_detection" in error for error in result.errors)


def test_unknown_turn_handling_keys_only_warn() -> None:
    config = inference_config()
    config.pipeline.turn_handling = {"nonsense": True}

    result = validate_agent_config(config, credential_providers={})

    assert result.ok is True
    assert any("nonsense" in warning for warning in result.warnings)


def test_free_text_models_warn_but_do_not_fail() -> None:
    config = inference_config()
    config.pipeline.llm = ProviderRef(provider_id="livekit-inference-llm", model="vendor/brand-new")

    result = validate_agent_config(config, credential_providers={})

    assert result.ok is True
    assert any("suggestion list" in warning for warning in result.warnings)


def test_camera_with_a_blind_realtime_model_warns() -> None:
    config = AgentConfig(
        instructions="x",
        pipeline=PipelineConfig(
            mode="realtime", realtime=ProviderRef(provider_id="openai-realtime", credential_id="c")
        ),
    )
    config.capabilities.camera = True

    result = validate_agent_config(config, credential_providers={"c": "openai-realtime"})

    assert result.ok is True
    assert any("cannot see video frames" in warning for warning in result.warnings)


def test_unknown_tool_and_kb_references_are_errors() -> None:
    config = inference_config()
    config.tools.tool_ids = ["tool-x"]
    config.knowledge.kb_ids = ["kb-x"]

    result = validate_agent_config(config, credential_providers={}, known_tool_ids=set(), known_kb_ids=set())

    assert len(result.errors) == 2


# ----------------------------------------------------------------------------- seeding
def test_seeding_a_cascaded_pack_keeps_the_inference_stack(
    fake_packs: dict[str, PackManifest],
) -> None:
    config = seed_config_from_manifest(fake_packs["generic"], credentials_by_provider={})

    assert config.pipeline.mode == "cascaded"
    assert config.pipeline.llm is not None
    assert config.pipeline.llm.provider_id == "livekit-inference-llm"
    assert validate_agent_config(config, credential_providers={}).ok is True


def test_seeding_the_real_insurance_pack_keeps_its_vision_llm() -> None:
    """DECISIONS-W2 §D-W2-10 step 4: the camera-on insurance agent seeds a vision model."""
    from packs.insurance_claim.manifest import MANIFEST as INSURANCE_MANIFEST  # noqa: PLC0415

    config = seed_config_from_manifest(INSURANCE_MANIFEST, credentials_by_provider={})

    assert config.pipeline.mode == "cascaded"
    assert config.pipeline.llm is not None
    assert config.pipeline.llm.provider_id == "livekit-inference-llm"
    assert config.pipeline.llm.model == "google/gemini-3.5-flash"
    assert config.capabilities.camera is True
    assert validate_agent_config(config, credential_providers={}).ok is True


def test_seeding_a_realtime_pack_without_a_credential_falls_back_to_cascaded(
    fake_packs: dict[str, PackManifest],
) -> None:
    config = seed_config_from_manifest(fake_packs["insurance_claim"], credentials_by_provider={})

    assert config.pipeline.mode == "cascaded"
    assert config.pipeline.realtime is None
    assert config.pipeline.image_gen is None
    assert config.pipeline.stt is not None and config.pipeline.stt.provider_id == "livekit-inference-stt"
    assert config.pipeline.tts is not None
    assert config.pipeline.tts.fields["voice"] == "Ashley"
    assert config.tools.builtin_disabled == ["push_note", "set_status"]
    assert validate_agent_config(config, credential_providers={}).ok is True


def test_seeding_a_realtime_pack_with_one_credential_keeps_realtime(
    fake_packs: dict[str, PackManifest],
) -> None:
    config = seed_config_from_manifest(
        fake_packs["insurance_claim"],
        credentials_by_provider={"google-realtime": ["cred-rt"], "google-image-gen": ["cred-img"]},
    )

    assert config.pipeline.mode == "realtime"
    assert config.pipeline.realtime is not None
    assert config.pipeline.realtime.credential_id == "cred-rt"
    assert config.pipeline.realtime.fields["voice"] == "Kore"
    assert config.pipeline.image_gen is not None
    assert config.pipeline.image_gen.credential_id == "cred-img"
    assert (
        validate_agent_config(
            config, credential_providers={"cred-rt": "google-realtime", "cred-img": "google-image-gen"}
        ).ok
        is True
    )


def test_ambiguous_credentials_fall_back_instead_of_guessing(
    fake_packs: dict[str, PackManifest],
) -> None:
    config = seed_config_from_manifest(
        fake_packs["insurance_claim"],
        credentials_by_provider={"google-realtime": ["cred-a", "cred-b"]},
    )

    assert config.pipeline.mode == "cascaded"


async def test_create_without_a_config_seeds_from_the_pack(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post("/v1/agents", json={"name": "Claims", "pack_id": "insurance_claim"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["ui_panel_id"] == "insurance_notebook"
    assert body["config"]["instructions"] == "You take first-notice-of-loss calls."
    assert body["config"]["voice"]["greeting"] == "Hi, I can start your claim."
    assert body["config"]["capabilities"]["camera"] is True


async def test_create_without_a_config_seeds_realtime_when_a_key_exists(
    admin_client: httpx.AsyncClient,
) -> None:
    await _credential(admin_client, "google-realtime")

    response = await admin_client.post(
        "/v1/agents", json={"name": "Claims live", "pack_id": "insurance_claim"}
    )

    pipeline = response.json()["config"]["pipeline"]
    assert pipeline["mode"] == "realtime"
    assert pipeline["realtime"]["fields"]["voice"] == "Kore"


async def test_create_with_an_unknown_pack_is_rejected(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post("/v1/agents", json={"name": "x", "pack_id": "nope"})

    assert response.status_code == 422
    assert "not installed" in response.json()["error"]["message"]


async def test_create_rejects_an_invalid_config(admin_client: httpx.AsyncClient) -> None:
    config = json.loads(inference_config().model_dump_json())
    config["pipeline"]["llm"] = {"provider_id": "openai-llm"}

    response = await admin_client.post("/v1/agents", json={"name": "Broken", "config": config})

    assert response.status_code == 422
    details: dict[str, Any] = response.json()["error"]["details"]
    assert any("requires a credential" in error for error in details["errors"])


# ------------------------------------------------------------------------------- CRUD
async def test_config_version_bumps_only_on_a_real_change(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client, published=False)
    config = json.loads(inference_config().model_dump_json())

    same = await admin_client.put(f"/v1/agents/{agent['id']}", json={"config": config})
    renamed = await admin_client.put(f"/v1/agents/{agent['id']}", json={"name": "New name"})
    config["instructions"] = "Changed."
    changed = await admin_client.put(f"/v1/agents/{agent['id']}", json={"config": config})

    assert same.json()["config_version"] == 1
    assert renamed.json()["config_version"] == 1
    assert changed.json()["config_version"] == 2


async def test_publish_toggle_round_trips(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client, published=False)

    published = await admin_client.put(f"/v1/agents/{agent['id']}", json={"published": True})
    unpublished = await admin_client.put(f"/v1/agents/{agent['id']}", json={"published": False})

    assert published.json()["published"] is True
    assert unpublished.json()["published"] is False


async def test_public_get_returns_the_public_projection(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient
) -> None:
    agent = await create_agent(admin_client, name="Public agent")

    response = await client.get(f"/v1/agents/{agent['slug']}")

    body = response.json()
    assert response.status_code == 200
    assert set(body) == {
        "id",
        "slug",
        "name",
        "description",
        "ui_panel_id",
        "capabilities",
        "pipeline_mode",
    }
    assert "config" not in body


async def test_public_get_of_an_unpublished_agent_is_forbidden(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient
) -> None:
    agent = await create_agent(admin_client, published=False)

    assert (await client.get(f"/v1/agents/{agent['slug']}")).status_code == 403
    assert (await admin_client.get(f"/v1/agents/{agent['slug']}")).status_code == 200


async def test_list_requires_admin(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/agents")).status_code == 401


async def test_validate_endpoint_reports_warnings(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client, published=False)

    response = await admin_client.post(f"/v1/agents/{agent['id']}/validate")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "errors": [], "warnings": [], "issues": []}


async def test_delete_removes_the_agent(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client, published=False)

    assert (await admin_client.delete(f"/v1/agents/{agent['id']}")).status_code == 204
    assert (await admin_client.get(f"/v1/agents/{agent['id']}")).status_code == 404


async def test_delete_is_refused_while_sessions_exist(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client)
    await admin_client.post(f"/v1/agents/{agent['id']}/connect", json={})

    response = await admin_client.delete(f"/v1/agents/{agent['id']}")

    assert response.status_code == 409
