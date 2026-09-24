"""Integrity tests for the provider registry (CONTRACTS §4)."""

from typing import get_args

import pytest

from lkap_contracts.providers import (
    MODEL_KINDS,
    PUBLIC_CATALOG_ADAPTERS,
    REGISTRY,
    TTL_OPENROUTER_S,
    TTL_PUBLIC_LIST_S,
    CatalogFilter,
    FieldSpec,
    FieldType,
    ProviderKind,
    ProviderSpec,
    available_providers,
    by_kind,
    credential_home,
    get,
    mvp_providers,
)

#: The MVP table of CONTRACTS §4 plus the §9 tool-secret bag, plus the slim
#: entries added since (``simli-avatar`` in v4, the five OpenRouter entries).
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
    "simli-avatar",  # moved to the slim image in v4 (the user's avatar choice)
    "google-image-gen",
    "openai-image-gen",
    "fastembed-embedding",
    "openai-embedding",
    "http-tool-secret",
    # V4-03: OpenRouter, shipped on the slim image (OPENROUTER.md D-V4-9).
    "openrouter-llm",
    "openrouter-stt",
    "openrouter-tts",
    "openrouter-embedding",
    "openrouter-image-gen",
]

OPENROUTER_IDS = [
    "openrouter-llm",
    "openrouter-stt",
    "openrouter-tts",
    "openrouter-embedding",
    "openrouter-image-gen",
]

#: V2-05 (PLAN-V2 §4 card, ruling R-V2-1): every entry it adds or promotes
#: carries ``worker_image="full"``, so the ``status`` alias is ``"deferred"``
#: for all of them regardless of ``availability`` — the alias is now a
#: slim-vs-full signal, not an availability signal, until V2-03/V2-13 migrate
#: their consumers off it. `test_registry_contains_expected_mvp_entries`
#: (byte-identical to the v1 set) is the test that actually guards R-V2-1;
#: this file instead asserts each of the four `Availability` outcomes by id,
#: which is the meaningful post-V2-05 invariant.
EXPECTED_DEFERRED_AVAILABILITY_IDS = [
    "krisp-noise-cancellation",  # asks #56: no livekit-plugins-krisp==1.8.2 release
    "playai-tts",
    "nvidia-personaplex-realtime",
    "legacy-noise-cancellation",
    "ai-coustics-noise-cancellation",
    "rtzr-stt",
    "spitch-stt",
    "spitch-tts",
]

EXPECTED_INCOMPATIBLE_IDS = ["minimax-tts"]

EXPECTED_REMOVED_IDS: list[str] = []


def test_registry_ids_are_unique() -> None:
    ids = [spec.id for spec in REGISTRY]
    assert len(ids) == len(set(ids)), "duplicate provider ids in REGISTRY"


def test_registry_contains_expected_mvp_entries() -> None:
    assert [spec.id for spec in mvp_providers()] == EXPECTED_MVP_IDS


def test_registry_has_at_least_seventeen_mvp_providers() -> None:
    assert len(mvp_providers()) >= 17


def test_registry_has_at_least_eighty_five_available_providers() -> None:
    """V2-05 card acceptance: "≥ 85 `available` entries"."""
    assert len(available_providers()) >= 85


def test_status_alias_is_deferred_for_every_full_image_entry() -> None:
    """R-V2-1: `status` derives from availability + image, not from being offered.

    Every entry V2-05 added or promoted is `worker_image="full"`, so it must
    report `status == "deferred"` no matter its `availability` — `"mvp"` is
    reserved for the slim set (the v1 set plus the v4 slim additions).
    """
    for spec in REGISTRY:
        if spec.id in EXPECTED_MVP_IDS:
            continue
        assert spec.status == "deferred", f"{spec.id} should still alias to 'deferred' (worker_image=full)"


def test_registry_contains_expected_deferred_availability_entries() -> None:
    deferred = [spec.id for spec in REGISTRY if spec.availability == "deferred"]
    assert deferred == EXPECTED_DEFERRED_AVAILABILITY_IDS


def test_registry_contains_expected_incompatible_entries() -> None:
    incompatible = [spec.id for spec in REGISTRY if spec.availability == "incompatible"]
    assert incompatible == EXPECTED_INCOMPATIBLE_IDS


def test_registry_contains_expected_removed_entries() -> None:
    removed = [spec.id for spec in REGISTRY if spec.availability == "removed"]
    assert removed == EXPECTED_REMOVED_IDS


def test_hedra_is_not_registered() -> None:
    """Vendor-disabled plugin (its AvatarSession.__init__ unconditionally raises): never selectable."""
    assert all("hedra" not in spec.id for spec in REGISTRY)


def test_minimax_is_marked_incompatible_not_merely_deferred() -> None:
    assert get("minimax-tts").availability == "incompatible"


def test_every_new_full_image_entry_carries_worker_image_full() -> None:
    for spec in REGISTRY:
        if spec.id not in EXPECTED_MVP_IDS:
            assert spec.worker_image == "full", spec.id


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
    assert {spec.id for spec in llms} == {
        "livekit-inference-llm",
        "openai-llm",
        "google-llm",
        "openrouter-llm",
    }


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


def test_simli_avatar_ships_on_the_slim_image() -> None:
    """The user's avatar choice (Beyond Presence + Simli): both build on the slim worker."""
    for provider_id in ("bey-avatar", "simli-avatar"):
        spec = get(provider_id)
        assert spec.worker_image == "slim"
        assert spec.status == "mvp"


# --------------------------------------------------------------------------- credential home (R-V4-7)


@pytest.mark.parametrize(
    "spec", [s for s in REGISTRY if s.credential_provider is not None], ids=lambda s: s.id
)
def test_credential_provider_names_a_home_with_no_home_and_the_same_secret_fields(spec: ProviderSpec) -> None:
    assert spec.credential_provider is not None
    home = get(spec.credential_provider)
    assert home.credential_provider is None, f"{spec.id}: home {home.id} has a home of its own"
    assert [f.name for f in home.secret_fields] == [f.name for f in spec.secret_fields]


def test_credential_home_resolves_aliases_and_passes_other_ids_through() -> None:
    assert credential_home("openrouter-tts") == "openrouter-llm"
    assert credential_home(get("openrouter-image-gen")) == "openrouter-llm"
    assert credential_home("openrouter-llm") == "openrouter-llm"
    assert credential_home("openai-llm") == "openai-llm"
    assert credential_home("not-a-provider") == "not-a-provider"


def test_only_the_four_non_llm_openrouter_entries_have_a_home() -> None:
    aliased = {s.id for s in REGISTRY if s.credential_provider is not None}
    assert aliased == {"openrouter-stt", "openrouter-tts", "openrouter-embedding", "openrouter-image-gen"}


# --------------------------------------------------------------------------- OpenRouter (D-V4-9, R-V4-8)


@pytest.mark.parametrize("provider_id", OPENROUTER_IDS)
def test_openrouter_default_model_is_listed(provider_id: str) -> None:
    spec = get(provider_id)
    assert spec.default_model is not None
    assert spec.default_model in {m.id for m in spec.models}


def test_no_openrouter_entry_fills_realtime() -> None:
    assert all(s.kind != "realtime" for s in REGISTRY if s.vendor == "OpenRouter")
    assert {s.id for s in REGISTRY if s.vendor == "OpenRouter"} == set(OPENROUTER_IDS)


@pytest.mark.parametrize("provider_id", OPENROUTER_IDS)
def test_openrouter_entries_ship_on_slim_and_are_catalogued(provider_id: str) -> None:
    spec = get(provider_id)
    assert spec.worker_image == "slim"
    assert spec.availability == "available"
    assert spec.catalog is not None
    assert spec.test == spec.catalog.adapter


def test_openrouter_llm_default_is_tool_capable_not_auto() -> None:
    spec = get("openrouter-llm")
    assert spec.default_model == "openai/gpt-4.1-mini"
    assert all(not m.id.startswith("openrouter/") for m in spec.models)
    assert spec.capabilities.tool_calling is True


# ------------------------------------------------ custom model ids, live catalogs (V4-07)
#: The 26 string-typed ``model`` fields retyped ``type="model"`` (R-V4-22). bitHuman's
#: ``model`` is a genuine two-value enum (its runtime mode), not a vendor model id, and
#: stays ``type="enum"``.
EXPECTED_MODEL_FIELD_COUNT = 26


def test_every_string_model_field_is_typed_model() -> None:
    model_fields = [(s.id, f) for s in REGISTRY for f in s.fields if f.name == "model"]
    typed_model = [pid for pid, f in model_fields if f.type == "model"]
    assert len(typed_model) == EXPECTED_MODEL_FIELD_COUNT
    assert not [pid for pid, f in model_fields if f.type == "string"]
    assert [pid for pid, f in model_fields if f.type == "enum"] == ["bithuman-avatar"]


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.id)
def test_no_entry_has_both_a_model_field_and_a_models_list(spec: ProviderSpec) -> None:
    has_model_field = any(f.name == "model" and f.type == "model" for f in spec.fields)
    assert not (has_model_field and spec.models), "one place per entry says which model it runs"


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.id)
def test_model_typed_fields_never_carry_options_contradicting_the_default(spec: ProviderSpec) -> None:
    for field in spec.fields:
        if field.type == "model" and field.options:
            assert field.default is None or field.default in field.options, field.name
            assert spec.default_model is None or spec.default_model in field.options, field.name


@pytest.mark.parametrize("spec", [s for s in REGISTRY if s.catalog and s.catalog.filter], ids=lambda s: s.id)
def test_every_listed_model_and_default_passes_its_own_catalog_filter(spec: ProviderSpec) -> None:
    # Only the id conditions can be checked here: a registry id has no vendor metadata,
    # so a `meta_path`/`meta_contains` condition (google-realtime's bidiGenerateContent)
    # is exercised by the api's fixture tests instead.
    assert spec.catalog is not None and spec.catalog.filter is not None
    ids = {m.id for m in spec.models}
    ids.update(f.default for f in spec.fields if f.type == "model" and isinstance(f.default, str))
    if spec.default_model:
        ids.add(spec.default_model)
    assert ids, "a filtered entry names at least one model"
    for model_id in sorted(ids):
        assert spec.catalog.filter.matches_id(model_id), model_id


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.id)
def test_probe_is_never_set_on_non_model_kinds(spec: ProviderSpec) -> None:
    if spec.kind in ("vad", "turn_detection", "noise_cancellation"):
        assert spec.probe is None


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.id)
def test_test_is_never_a_public_list_adapter(spec: ProviderSpec) -> None:
    # R-V4-9: a list that answers a bogus key cannot test a key.
    assert spec.test not in PUBLIC_CATALOG_ADAPTERS


@pytest.mark.parametrize("spec", [s for s in REGISTRY if s.test], ids=lambda s: s.id)
def test_the_credential_test_and_the_catalog_name_the_same_adapter(spec: ProviderSpec) -> None:
    assert spec.catalog is not None
    assert spec.test == spec.catalog.adapter


@pytest.mark.parametrize(
    ("provider_id", "adapter"),
    [
        ("google-llm", "gemini_models"),
        ("google-realtime", "gemini_models"),
        ("google-image-gen", "gemini_models"),
        ("deepgram-stt", "deepgram_stt_models"),
        ("deepgram-tts", "deepgram_tts_models"),
        ("openai-tts", "openai_models"),
        ("openai-realtime", "openai_models"),
        ("openai-embedding", "openai_models"),
        ("openai-image-gen", "openai_models"),
        ("xai-llm", "xai_models"),
        ("cerebras-llm", "cerebras_models"),
        ("inworld-tts", "inworld_voices"),
        ("rime-tts", "rime_voices"),
        ("mistral-llm", "mistral_models"),
    ],
)
def test_the_d_v4_25_entries_are_wired_to_a_live_catalog(provider_id: str, adapter: str) -> None:
    spec = get(provider_id)
    assert spec.catalog is not None
    assert spec.catalog.adapter == adapter


def test_public_lists_are_catalogs_with_a_day_long_ttl_and_no_credential_test() -> None:
    public = [s for s in REGISTRY if s.catalog and s.catalog.adapter in PUBLIC_CATALOG_ADAPTERS]
    assert {s.id for s in public} == {"deepgram-stt", "deepgram-tts", "rime-tts"}
    for spec in public:
        assert spec.test is None
        assert spec.catalog is not None and spec.catalog.ttl_s == TTL_PUBLIC_LIST_S


@pytest.mark.parametrize("provider_id", OPENROUTER_IDS)
def test_openrouter_catalogs_cache_for_six_hours(provider_id: str) -> None:
    spec = get(provider_id)
    assert spec.catalog is not None and spec.catalog.ttl_s == TTL_OPENROUTER_S


def test_azure_voices_have_no_adapter_the_region_is_a_slot_field() -> None:
    # CUSTOM-MODELS.md §1.4 item 7: the provider-level catalog route cannot carry `speech_region`.
    assert get("azure-tts").catalog is None
    assert get("azure-stt").catalog is None


@pytest.mark.parametrize(
    ("filter_", "item_id", "meta", "expected"),
    [
        (CatalogFilter(id_include="^gpt-"), "gpt-4.1", {}, True),
        (CatalogFilter(id_include="^gpt-"), "tts-1", {}, False),
        (CatalogFilter(id_exclude="whisper"), "WHISPER-1", {}, False),
        (CatalogFilter(meta_path="a.b", meta_contains="x"), "m", {"a": {"b": ["x", "y"]}}, True),
        (CatalogFilter(meta_path="a.b", meta_contains="x"), "m", {"a": {"b": ["y"]}}, False),
        (CatalogFilter(meta_path="a.b", meta_contains="x"), "m", {"a": "not-a-dict"}, False),
        (CatalogFilter(), "anything", {}, True),
    ],
)
def test_catalog_filter_matches(
    filter_: CatalogFilter, item_id: str, meta: dict[str, object], expected: bool
) -> None:
    assert filter_.matches(item_id, meta) is expected


def test_catalog_filter_rejects_a_bad_regex_and_half_a_meta_condition() -> None:
    with pytest.raises(ValueError):
        CatalogFilter(id_include="(unclosed")
    with pytest.raises(ValueError):
        CatalogFilter(meta_path="supportedGenerationMethods")


def test_model_kinds_cover_every_kind_that_takes_a_model() -> None:
    assert frozenset({"realtime", "stt", "llm", "tts", "image_gen", "embedding"}) == MODEL_KINDS
