"""Integrity tests for the provider registry (CONTRACTS §4)."""

from typing import get_args

import pytest

from lkap_contracts.providers import (
    REGISTRY,
    FieldSpec,
    FieldType,
    ProviderKind,
    ProviderSpec,
    by_kind,
    get,
    mvp_providers,
)

#: The MVP table of CONTRACTS §4 plus the §9 tool-secret bag.
EXPECTED_MVP_IDS = [
    "livekit-inference-stt",
    "livekit-inference-llm",
    "livekit-inference-tts",
    "google-realtime",
    "openai-realtime",
    "deepgram-stt",
    "openai-llm",
    "google-llm",
    "cartesia-tts",
    "elevenlabs-tts",
    "openai-tts",
    "bey-avatar",
    "tavus-avatar",
    "google-image-gen",
    "openai-image-gen",
    "fastembed-embedding",
    "openai-embedding",
    "http-tool-secret",
]

EXPECTED_DEFERRED_IDS = [
    "azure-openai-realtime",
    "xai-realtime",
    "assemblyai-stt",
    "google-stt",
    "openai-stt",
    "speechmatics-stt",
    "elevenlabs-stt",
    "cartesia-stt",
    "groq-stt",
    "azure-stt",
    "anthropic-llm",
    "groq-llm",
    "cerebras-llm",
    "openai-compatible-llm",
    "aws-bedrock-llm",
    "google-tts",
    "deepgram-tts",
    "rime-tts",
    "inworld-tts",
    "hume-tts",
    "azure-tts",
    "simli-avatar",
    "anam-avatar",
    "bithuman-avatar",
    "liveavatar-avatar",
]


def test_registry_ids_are_unique() -> None:
    ids = [spec.id for spec in REGISTRY]
    assert len(ids) == len(set(ids)), "duplicate provider ids in REGISTRY"


def test_registry_contains_expected_mvp_entries() -> None:
    assert [spec.id for spec in mvp_providers()] == EXPECTED_MVP_IDS


def test_registry_has_at_least_seventeen_mvp_providers() -> None:
    assert len(mvp_providers()) >= 17


def test_registry_contains_expected_deferred_entries() -> None:
    deferred = [spec.id for spec in REGISTRY if spec.status == "deferred"]
    assert deferred == EXPECTED_DEFERRED_IDS


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.id)
def test_kind_is_a_valid_provider_kind(spec: ProviderSpec) -> None:
    assert spec.kind in get_args(ProviderKind)


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.id)
def test_default_model_is_listed_or_none(spec: ProviderSpec) -> None:
    if spec.default_model is None:
        return
    assert spec.default_model in {model.id for model in spec.models}


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.id)
def test_secret_fields_are_typed_secret(spec: ProviderSpec) -> None:
    assert all(field.type == "secret" for field in spec.secret_fields)


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.id)
def test_non_secret_fields_are_never_typed_secret(spec: ProviderSpec) -> None:
    assert all(field.type != "secret" for field in spec.fields)


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.id)
def test_field_types_are_valid(spec: ProviderSpec) -> None:
    for field in [*spec.fields, *spec.secret_fields]:
        assert field.type in get_args(FieldType)


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.id)
def test_enum_fields_declare_options_containing_their_default(spec: ProviderSpec) -> None:
    for field in spec.fields:
        if field.type != "enum":
            continue
        assert field.options, f"{spec.id}.{field.name} is an enum without options"
        if field.default is not None:
            assert field.default in field.options


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.id)
def test_field_names_are_unique_within_a_provider(spec: ProviderSpec) -> None:
    names = [f.name for f in (*spec.fields, *spec.secret_fields)]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.id)
def test_model_ids_are_unique_within_a_provider(spec: ProviderSpec) -> None:
    ids = [model.id for model in spec.models]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("spec", mvp_providers(), ids=lambda s: s.id)
def test_mvp_constructible_providers_declare_a_python_class(spec: ProviderSpec) -> None:
    """Every MVP provider the factory builds names a dotted class path.

    ``secret_bag`` entries are pure credential holders with nothing to construct.
    """
    if spec.kind == "secret_bag":
        assert spec.python_class == ""
        return
    assert "." in spec.python_class
    assert spec.package


@pytest.mark.parametrize("spec", mvp_providers(), ids=lambda s: s.id)
def test_credentialled_providers_declare_secret_fields(spec: ProviderSpec) -> None:
    """Providers needing a credential list their secret fields.

    ``secret_bag`` is exempt: its secrets are free-form ``NAME=value`` pairs the
    admin types in the console, so it has no fixed field list.
    """
    if not spec.requires_credential or spec.kind == "secret_bag":
        return
    assert spec.secret_fields


def test_livekit_inference_providers_need_no_credential() -> None:
    for provider_id in ("livekit-inference-stt", "livekit-inference-llm", "livekit-inference-tts"):
        assert get(provider_id).requires_credential is False


def test_realtime_video_capability_matches_model_support() -> None:
    google = get("google-realtime")
    assert google.capabilities.video_input is True
    assert all(model.supports_video for model in google.models)
    assert get("openai-realtime").capabilities.video_input is False


def test_get_returns_the_matching_spec() -> None:
    assert get("google-realtime").kind == "realtime"


def test_get_raises_key_error_for_unknown_ids() -> None:
    with pytest.raises(KeyError):
        get("does-not-exist")


def test_by_kind_defaults_to_mvp_only() -> None:
    llms = by_kind("llm")
    assert {spec.id for spec in llms} == {"livekit-inference-llm", "openai-llm", "google-llm"}


def test_by_kind_can_include_deferred_entries() -> None:
    all_llms = {spec.id for spec in by_kind("llm", status=None)}
    assert "anthropic-llm" in all_llms
    assert "openai-llm" in all_llms


def test_every_pipeline_slot_kind_has_an_mvp_provider() -> None:
    for kind in ("realtime", "stt", "llm", "tts", "avatar", "image_gen", "embedding"):
        assert by_kind(kind), f"no mvp provider for slot kind {kind}"


def test_field_spec_rejects_unknown_field_type() -> None:
    with pytest.raises(ValueError, match="type"):
        FieldSpec(name="x", label="X", type="not-a-type")  # type: ignore[arg-type]
