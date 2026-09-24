"""Validation against connection flags and installed providers (R-V2-2, D-V2-4, D-V2-8)."""

from __future__ import annotations

import pytest
from conftest import GENERIC_MANIFEST, inference_config
from connection_fakes import connection_row
from lkap_contracts.agent_config import AgentConfig, PipelineConfig, ProviderRef, RecordingConfig
from lkap_contracts.connections import ConnectionCapabilities
from lkap_contracts.providers import REGISTRY, ProviderSpec, get

# Importing this registers V2-06's workspace-enablement validator into
# `config_service.VALIDATORS` — explicitly, so these tests don't depend on
# some other test file having already imported `routers.providers` (and so
# `lkap_api.catalogs`) first when this file runs in isolation.
import lkap_api.catalogs  # noqa: F401
from lkap_api.config_service import (
    ConnectionContext,
    installed_on,
    resolve_providers,
    seed_config_from_manifest,
    validate_agent_config,
    validate_in_db,
)
from lkap_api.connections.probe import static_capabilities
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import WorkspaceProvider
from lkap_api.db.session import Database
from lkap_api.settings import Settings
from lkap_api.vault import Vault

#: The flat `errors` text V2-06's registered validator produces for one disabled slot.
_DISABLED_TTS = (
    "pipeline.tts: provider 'livekit-inference-tts' is disabled for this workspace "
    "(providers.disabled) — an admin can re-enable it on the Providers page"
)


def _connection(
    deployment_type: str = "cloud",
    *,
    use_inference: bool = True,
    image: str = "slim",
    installed: frozenset[str] | None = None,
    **caps: object,
) -> ConnectionContext:
    capabilities = static_capabilities(deployment_type, use_inference).model_copy(update=caps)
    return ConnectionContext(
        connection_id="conn",
        name="self-a" if deployment_type == "self_hosted" else "cloud-a",
        deployment_type=deployment_type,  # type: ignore[arg-type]
        worker_image=image,  # type: ignore[arg-type]
        capabilities=capabilities,
        installed_provider_ids=installed,
    )


def _deepgram_config() -> AgentConfig:
    config = inference_config()
    config.pipeline.stt = ProviderRef(provider_id="deepgram-stt", credential_id="dg")
    return config


def test_validate_inference_llm_on_self_hosted_connection_is_an_issue_at_pipeline_llm() -> None:
    result = validate_agent_config(
        inference_config(), credential_providers={}, connection=_connection("self_hosted")
    )

    assert not result.ok
    llm_issues = [i for i in result.issues if i.path == "pipeline.llm"]
    assert len(llm_issues) == 1
    assert llm_issues[0].severity == "error"
    assert "LiveKit Inference" in llm_issues[0].message and "self-hosted" in llm_issues[0].message
    assert {i.path for i in result.issues if i.severity == "error"} == {
        "pipeline.stt",
        "pipeline.llm",
        "pipeline.tts",
    }
    assert any(e.startswith("pipeline.llm: ") for e in result.errors)


def test_validate_inference_on_cloud_connection_is_valid() -> None:
    result = validate_agent_config(inference_config(), credential_providers={}, connection=_connection())

    assert result.ok, result.errors
    assert result.issues == []


def test_validate_inference_on_cloud_connection_with_inference_off_is_rejected() -> None:
    result = validate_agent_config(
        inference_config(), credential_providers={}, connection=_connection(use_inference=False)
    )

    assert not result.ok
    assert all("setting is off" in i.message for i in result.issues if i.severity == "error")


def test_validate_without_connection_keeps_the_v1_rules() -> None:
    result = validate_agent_config(inference_config(), credential_providers={})

    assert result.ok and result.issues == []


def test_validate_deferred_provider_is_not_available_even_on_a_full_connection() -> None:
    deferred = next(p for p in REGISTRY if p.kind == "stt" and p.availability != "available")
    config = inference_config()
    config.pipeline.stt = ProviderRef(provider_id=deferred.id, credential_id="c")

    result = validate_agent_config(
        config, credential_providers={"c": deferred.id}, connection=_connection(image="full")
    )

    assert any(e.startswith("pipeline.stt: ") and "not available yet" in e for e in result.errors)


def test_validate_full_image_provider_needs_a_full_connection() -> None:
    full = next(
        p for p in REGISTRY if p.kind == "llm" and p.availability == "available" and p.worker_image == "full"
    )
    config = inference_config()
    config.pipeline.llm = ProviderRef(provider_id=full.id, credential_id="c")
    credentials = {"c": full.id}
    required = {f.name: "x" for f in full.fields if f.required and f.default is None}
    config.pipeline.llm.fields.update(required)

    on_slim = validate_agent_config(
        config, credential_providers=credentials, connection=_connection(image="slim")
    )
    on_full = validate_agent_config(
        config, credential_providers=credentials, connection=_connection(image="full")
    )
    without = validate_agent_config(config, credential_providers=credentials)

    assert [i.path for i in on_slim.issues if i.severity == "error"] == ["pipeline.llm"]
    assert "needs the 'full' worker image; connection 'cloud-a' runs 'slim'" in on_slim.errors[0]
    assert [i for i in on_full.issues if i.severity == "error"] == []
    assert any("not available yet" in e for e in without.errors)


@pytest.mark.parametrize(
    ("spec_image", "connection_image", "expected"),
    [("slim", "slim", True), ("full", "slim", False), ("full", "full", True), ("isolated", "full", False)],
)
def test_installed_on_falls_back_to_worker_image(
    spec_image: str, connection_image: str, expected: bool
) -> None:
    spec = get("deepgram-stt").model_copy(update={"worker_image": spec_image})

    assert installed_on(spec, _connection(image=connection_image)) is expected


def test_installed_on_prefers_registered_worker_reports() -> None:
    spec: ProviderSpec = get("deepgram-stt")

    assert (
        installed_on(spec, _connection(image="full", installed=frozenset({"livekit-inference-llm"}))) is False
    )
    assert installed_on(spec, _connection(image="slim", installed=frozenset({"deepgram-stt"}))) is True


def test_validate_provider_missing_from_the_pool_is_an_error() -> None:
    connection = _connection(
        installed=frozenset({"livekit-inference-stt", "livekit-inference-llm", "livekit-inference-tts"})
    )

    result = validate_agent_config(
        _deepgram_config(), credential_providers={"dg": "deepgram-stt"}, connection=connection
    )

    assert [i.path for i in result.issues if i.severity == "error"] == ["pipeline.stt"]
    assert "not installed on connection 'cloud-a'" in result.issues[0].message


def test_validate_half_cascade_needs_realtime_with_text_output_and_tts() -> None:
    config = AgentConfig(
        instructions="x",
        pipeline=PipelineConfig(
            mode="half_cascade", realtime=ProviderRef(provider_id="google-realtime", credential_id="g")
        ),
    )

    result = validate_agent_config(config, credential_providers={"g": "google-realtime"})

    paths = {i.path for i in result.issues if i.severity == "error"}
    assert "pipeline.tts" in paths
    assert "pipeline.tts is required when mode is 'half_cascade'" in result.errors
    text_modality = get("google-realtime").capabilities.text_modality
    assert ("pipeline.realtime" in paths) is (not text_modality)


def test_validate_dtmf_without_sip_is_an_error_and_recording_without_egress_warns() -> None:
    config = inference_config()
    config.capabilities.dtmf = True
    config.recording = RecordingConfig(enabled=True)

    result = validate_agent_config(
        config, credential_providers={}, connection=_connection(sip_enabled=False, egress_enabled=False)
    )

    by_path = {i.path: i.severity for i in result.issues}
    assert by_path == {"capabilities.dtmf": "error", "recording.enabled": "warning"}


def test_validate_realtime_on_connection_without_inference_warns_about_workflow_llm() -> None:
    config = AgentConfig(
        instructions="x",
        pipeline=PipelineConfig(
            mode="realtime", realtime=ProviderRef(provider_id="google-realtime", credential_id="g")
        ),
    )

    result = validate_agent_config(
        config, credential_providers={"g": "google-realtime"}, connection=_connection("self_hosted")
    )

    assert result.ok
    assert [(i.path, i.severity) for i in result.issues] == [("pipeline.workflow_llm", "warning")]


def test_validate_runs_registered_validators() -> None:
    """V2-06's `catalogs.validation.disabled_provider_issues` is registered on import."""
    result = validate_agent_config(
        inference_config(),
        credential_providers={},
        disabled_provider_ids=frozenset({"livekit-inference-tts"}),
    )

    assert not result.ok
    assert result.errors == [_DISABLED_TTS]


async def test_validate_in_db_uses_the_default_connection_and_disabled_providers(
    database: Database, settings: Settings
) -> None:
    vault = Vault(settings.master_key)
    async with database.session() as session:
        session.add(
            WorkspaceProvider(
                workspace_id=DEFAULT_WORKSPACE_ID, provider_id="livekit-inference-tts", enabled=False
            )
        )
        self_hosted = connection_row(
            vault, slug="self-a", url="ws://localhost:7880", deployment_type="self_hosted"
        )
        session.add(self_hosted)
        await session.flush()
        on_default = await validate_in_db(session, inference_config(), workspace_id=DEFAULT_WORKSPACE_ID)
        on_self_hosted = await validate_in_db(
            session, inference_config(), workspace_id=DEFAULT_WORKSPACE_ID, connection_id=self_hosted.id
        )

    assert on_default.errors == [_DISABLED_TTS]
    assert {i.path for i in on_self_hosted.issues if i.severity == "error"} == {
        "pipeline.stt",
        "pipeline.llm",
        "pipeline.tts",
    }


def test_seed_config_falls_back_to_inference_when_the_pool_lacks_the_provider() -> None:
    manifest = GENERIC_MANIFEST.model_copy(deep=True)
    manifest.recommended_pipeline.stt = ProviderRef(provider_id="deepgram-stt")
    credentials = {"deepgram-stt": ["dg"]}

    kept = seed_config_from_manifest(manifest, credentials_by_provider=credentials)
    replaced = seed_config_from_manifest(
        manifest,
        credentials_by_provider=credentials,
        connection=_connection(installed=frozenset({"livekit-inference-stt"})),
    )

    assert kept.pipeline.stt == ProviderRef(provider_id="deepgram-stt", credential_id="dg")
    assert replaced.pipeline.stt == ProviderRef(provider_id="livekit-inference-stt")


def test_seed_config_replaces_a_deferred_recommendation() -> None:
    manifest = GENERIC_MANIFEST.model_copy(deep=True)
    manifest.recommended_pipeline.llm = ProviderRef(provider_id="anthropic-llm")

    config = seed_config_from_manifest(manifest, credentials_by_provider={"anthropic-llm": ["a"]})

    assert config.pipeline.llm == ProviderRef(provider_id="livekit-inference-llm")


def test_resolve_providers_half_cascade_resolves_realtime_and_tts() -> None:
    config = AgentConfig(
        instructions="x",
        pipeline=PipelineConfig(
            mode="half_cascade",
            realtime=ProviderRef(provider_id="google-realtime", credential_id="g"),
            tts=ProviderRef(provider_id="livekit-inference-tts"),
            stt=ProviderRef(provider_id="livekit-inference-stt"),
        ),
    )

    resolved = resolve_providers(config, {"g": {"api_key": "k"}})

    assert set(resolved) == {"realtime", "tts", "workflow_llm"}
    assert resolved["workflow_llm"].provider_id == "livekit-inference-llm"


def test_connection_context_from_row_uses_effective_capabilities(settings: Settings) -> None:
    row = connection_row(Vault(settings.master_key), capabilities={"sip_enabled": False})

    context = ConnectionContext.from_row(row)

    assert context.capabilities == ConnectionCapabilities(
        inference_available=True,
        sip_enabled=False,
        egress_enabled=True,
        ingress_enabled=True,
        cloud_hosting=True,
        noise_cancellation_tier="krisp",
        observability_dashboard=True,
        turn_detector_mode="hosted",
    )
