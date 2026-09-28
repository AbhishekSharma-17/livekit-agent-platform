"""V6-02 speech registry truth: streaming, the new fields, deprecated defaults, Flux, Fireworks, Inference."""

from __future__ import annotations

import pytest

from lkap_contracts.providers import (
    DEPRECATED_BY_VENDOR,
    REGISTRY,
    ProviderSpec,
    available_providers,
    get,
    speech_streams,
)

_SPEECH = [spec for spec in available_providers() if spec.kind in ("stt", "tts")]

#: D-V6-2's list of non-streaming entries, plus the ones the 1.8.3 source adds (V6-02 asks).
NOT_STREAMING = {
    "openrouter-stt",
    "openrouter-tts",
    "openai-tts",
    "azure-tts",
    "speechmatics-tts",
    "hume-tts",
    "groq-stt",
    "groq-tts",
    "fal-wizper-stt",
    # beyond D-V6-2, read from each plugin's `streaming=False`:
    "aws-polly-tts",
    "lmnt-tts",
    "cambai-tts",
    "clova-stt",
    "simplismart-tts",
    "simplismart-stt",
    "mistral-stt",
    "elevenlabs-stt",
    # off by default, on through a field:
    "openai-stt",
    "rime-tts",
}


@pytest.mark.parametrize("spec", _SPEECH, ids=lambda s: s.id)
def test_every_available_speech_entry_records_streaming_or_says_why(spec: ProviderSpec) -> None:
    capabilities = spec.capabilities
    assert capabilities.streaming is not None or capabilities.streaming_note, spec.id


def test_the_non_streaming_set_is_exactly_what_the_plugins_say() -> None:
    assert {spec.id for spec in _SPEECH if spec.capabilities.streaming is False} == NOT_STREAMING


def test_streaming_is_only_recorded_on_speech_entries() -> None:
    for spec in REGISTRY:
        if spec.kind not in ("stt", "tts"):
            assert spec.capabilities.streaming is None, spec.id
            assert spec.capabilities.streaming_field is None, spec.id


@pytest.mark.parametrize("spec", _SPEECH, ids=lambda s: s.id)
def test_a_streaming_field_is_a_boolean_field_of_the_entry(spec: ProviderSpec) -> None:
    field = spec.capabilities.streaming_field
    if field is None:
        return
    match = [f for f in spec.fields if f.name == field]
    assert match and match[0].type == "boolean", spec.id
    assert spec.capabilities.streaming is False, "the field turns on what is off by default"


@pytest.mark.parametrize(
    ("provider_id", "fields", "expected"),
    [
        ("openai-stt", {}, False),
        ("openai-stt", {"use_realtime": False}, False),
        ("openai-stt", {"use_realtime": True}, True),
        ("rime-tts", {"use_websocket": True}, True),
        ("rime-tts", {}, False),
        ("openrouter-tts", {}, False),
        ("livekit-inference-stt", {}, True),
        ("deepgram-flux-stt", {}, True),
        ("baseten-tts", {}, None),
        ("openai-llm", {}, None),
    ],
)
def test_speech_streams_reads_the_default_and_the_streaming_field(
    provider_id: str, fields: dict[str, bool], expected: bool | None
) -> None:
    assert speech_streams(get(provider_id), fields) is expected


@pytest.mark.parametrize(
    ("provider_id", "field", "recommended"),
    [
        ("openai-stt", "use_realtime", True),
        ("rime-tts", "use_websocket", True),
        ("elevenlabs-tts", "encoding", "pcm_24000"),
        ("minimax-tts", "audio_format", "pcm"),
    ],
)
def test_the_new_behaviour_fields_default_to_the_plugin_and_recommend_streaming(
    provider_id: str, field: str, recommended: str | bool
) -> None:
    """D-V6-4c: no default (a stored ref resolves to the same kwargs); the console pre-selects it."""
    spec = get(provider_id)
    by_name = {f.name: f for f in spec.fields}
    assert by_name[field].default is None
    assert by_name[field].recommended == recommended
    if by_name[field].type == "enum":
        assert by_name[field].options is not None and recommended in by_name[field].options


@pytest.mark.parametrize(
    ("provider_id", "new_default", "old_id", "old_deprecated"),
    [
        ("inworld-tts", "inworld-tts-2", "inworld-tts-1.5-max", True),
        ("minimax-tts", "speech-2.8-turbo", "speech-02-turbo", True),
        ("cartesia-tts", "sonic-3.6", "sonic-3", False),
        ("elevenlabs-tts", "eleven_flash_v2_5", "eleven_turbo_v2_5", False),
    ],
)
def test_default_models_moved_and_the_old_ids_still_resolve(
    provider_id: str, new_default: str, old_id: str, old_deprecated: bool
) -> None:
    spec = get(provider_id)
    models = {m.id: m for m in spec.models}
    assert spec.default_model == new_default
    assert new_default in models and not models[new_default].deprecated
    assert old_id in models
    assert models[old_id].deprecated is old_deprecated
    if old_deprecated:
        assert models[old_id].note == DEPRECATED_BY_VENDOR


def test_minimax_uses_the_1_8_package() -> None:
    spec = get("minimax-tts")
    assert spec.availability == "available"
    assert spec.package == "livekit-plugins-minimax-ai"
    assert spec.python_class == "livekit.plugins.minimax.TTS"


def test_deepgram_flux_has_its_own_entry_with_end_of_turn() -> None:
    flux = get("deepgram-flux-stt")
    assert flux.python_class == "livekit.plugins.deepgram.STTv2"
    assert [m.id for m in flux.models] == ["flux-general-en", "flux-general-multi"]
    assert flux.capabilities.end_of_turn is True
    assert flux.capabilities.redaction == []  # STTv2's `redact` is one string, not a list
    assert flux.credential_provider == "deepgram-stt"
    assert flux.price_ref == "deepgram-stt"
    assert all(f.name != "language" for f in flux.fields)
    assert "flux-general-en" not in {m.id for m in get("deepgram-stt").models}
    assert all(not spec.capabilities.end_of_turn for spec in REGISTRY if spec.id != flux.id)


def test_fireworks_stt_is_withdrawn_with_a_plain_reason() -> None:
    spec = get("fireworksai-stt")
    assert spec.availability == "removed"
    assert spec.notes is not None and "2026-06-10" in spec.notes


def test_groq_tts_notes_its_character_cap() -> None:
    notes = get("groq-tts").notes
    assert notes is not None and "200 characters" in notes


#: D-V6-8 additions, each checked against docs.livekit.io/agents/models/inference (rendered
#: 2026-09-27T22:10Z). `inworld/inworld-tts-2-flash` waits for its own price row.
INFERENCE_ADDED = {
    "livekit-inference-stt": {
        "deepgram/flux-general-multi",
        "deepgram/nova-3-medical",
        "assemblyai/universal-3-6-pro",
        "cartesia/ink-2",
        "speechmatics/linden-1",
        "xai/stt-2",
    },
    "livekit-inference-llm": {
        "google/gemini-3.8-flash",
        "google/gemini-3.1-flash-lite",
        "openai/gpt-5.5",
        "openai/gpt-5.4-mini",
        "openai/gpt-5.6-luna",
        "xai/grok-4.7",
        "deepseek-ai/deepseek-v4.1-flash",
    },
    "livekit-inference-tts": {
        "cartesia/sonic-3.6",
        "deepgram/flux-tts",
        "fishaudio/s2.1-pro",
        "gradium/default",
        "rime/coda",
        "xai/tts-1",
    },
}


@pytest.mark.parametrize("provider_id", sorted(INFERENCE_ADDED))
def test_the_inference_lists_carry_the_current_catalogue(provider_id: str) -> None:
    spec = get(provider_id)
    ids = {m.id for m in spec.models}
    assert INFERENCE_ADDED[provider_id] <= ids
    assert spec.default_model in ids
    assert not any("elevenlabs" in model_id for model_id in ids)
    added = [m for m in spec.models if m.id in INFERENCE_ADDED[provider_id]]
    assert not any(m.supports_video for m in added), "vision is flagged only after verification"


def test_every_inference_speech_model_streams() -> None:
    assert get("livekit-inference-stt").capabilities.streaming is True
    assert get("livekit-inference-tts").capabilities.streaming is True
