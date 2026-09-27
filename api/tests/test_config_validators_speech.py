"""V6-02 speech validators: non-streaming tips, Flux under Deepgram, Fireworks withdrawn, deprecations."""

from __future__ import annotations

import pytest
from conftest import inference_config
from lkap_contracts.agent_config import AgentConfig, ProviderRef
from lkap_contracts.api_models import Issue
from lkap_contracts.connections import ConnectionCapabilities
from lkap_contracts.providers import get

from lkap_api.config_service import (
    DEEPGRAM_FLUX_MOVED_MESSAGE,
    ConnectionContext,
    ValidationContext,
    resolve_provider_ref,
    speech_latency_issues,
    validate_agent_config,
)

CREDENTIALS = {
    "cred-oa": "openai-llm",
    "cred-oa-stt": "openai-stt",
    "cred-rime": "rime-tts",
    "cred-dg": "deepgram-stt",
    "cred-fw": "fireworksai-stt",
    "cred-inworld": "inworld-tts",
    "cred-azure": "azure-tts",
    "cred-el": "elevenlabs-tts",
    "cred-cartesia": "cartesia-tts",
}


def _with(
    *, stt: ProviderRef | None = None, tts: ProviderRef | None = None, mode: str = "cascaded"
) -> AgentConfig:
    config = inference_config()
    if stt is not None:
        config.pipeline.stt = stt
    if tts is not None:
        config.pipeline.tts = tts
    config.pipeline.mode = mode  # type: ignore[assignment]
    return config


def _tips(config: AgentConfig) -> list[Issue]:
    return speech_latency_issues(ValidationContext(config=config, credential_providers=CREDENTIALS))


#: A LiveKit Cloud connection whose pool runs the full worker image (every entry here is on it).
FULL_CLOUD = ConnectionContext(
    connection_id="conn-full",
    worker_image="full",
    capabilities=ConnectionCapabilities(inference_available=True, turn_detector_mode="hosted"),
)


def _issues(config: AgentConfig) -> list[Issue]:
    return validate_agent_config(config, credential_providers=CREDENTIALS, connection=FULL_CLOUD).issues


# ------------------------------------------------------------------ not-streaming tips
def test_openai_stt_without_realtime_gets_a_tip_naming_the_switch() -> None:
    [tip] = _tips(_with(stt=ProviderRef(provider_id="openai-stt", credential_id="cred-oa-stt")))

    assert tip.path == "pipeline.stt"
    assert tip.severity == "warning"
    assert tip.message.startswith("Tip: ")
    assert "Streams while the caller speaks" in tip.message


def test_openai_stt_with_realtime_on_gets_no_tip() -> None:
    ref = ProviderRef(provider_id="openai-stt", credential_id="cred-oa-stt", fields={"use_realtime": True})

    assert _tips(_with(stt=ref)) == []


def test_rime_without_the_websocket_gets_a_tip_and_with_it_none() -> None:
    off = ProviderRef(provider_id="rime-tts", credential_id="cred-rime")
    on = ProviderRef(provider_id="rime-tts", credential_id="cred-rime", fields={"use_websocket": True})

    [tip] = _tips(_with(tts=off))
    assert tip.path == "pipeline.tts" and "Streams while it speaks" in tip.message
    assert _tips(_with(tts=on)) == []


@pytest.mark.parametrize(
    "provider_id", ["openai-tts", "azure-tts", "hume-tts", "speechmatics-tts", "groq-tts"]
)
def test_a_voice_that_waits_for_the_sentence_gets_a_tip(provider_id: str) -> None:
    [tip] = _tips(_with(tts=ProviderRef(provider_id=provider_id)))

    assert tip.path == "pipeline.tts"
    assert "waits for the whole sentence" in tip.message
    assert "Cartesia" in tip.message


@pytest.mark.parametrize("provider_id", ["groq-stt", "fal-wizper-stt"])
def test_a_batch_transcriber_gets_a_tip(provider_id: str) -> None:
    [tip] = _tips(_with(stt=ProviderRef(provider_id=provider_id)))

    assert tip.path == "pipeline.stt" and "Deepgram" in tip.message


def test_the_tips_never_use_jargon() -> None:
    configs = [
        _with(stt=ProviderRef(provider_id="openai-stt"), tts=ProviderRef(provider_id="rime-tts")),
        _with(stt=ProviderRef(provider_id="groq-stt"), tts=ProviderRef(provider_id="azure-tts")),
    ]
    for config in configs:
        for issue in _tips(config):
            for word in ("WS", "SSE", "PCM", "websocket", "WebSocket"):
                assert word not in issue.message


@pytest.mark.parametrize(
    ("stt", "tts"),
    [
        ("livekit-inference-stt", "livekit-inference-tts"),
        ("deepgram-stt", "cartesia-tts"),
        ("deepgram-flux-stt", "elevenlabs-tts"),
    ],
)
def test_streaming_stacks_get_no_tip(stt: str, tts: str) -> None:
    assert _tips(_with(stt=ProviderRef(provider_id=stt), tts=ProviderRef(provider_id=tts))) == []


def test_realtime_mode_ignores_speech_slots_that_do_not_run() -> None:
    config = _with(
        stt=ProviderRef(provider_id="groq-stt"), tts=ProviderRef(provider_id="openai-tts"), mode="realtime"
    )

    assert _tips(config) == []


def test_the_tips_do_not_fail_the_save() -> None:
    config = _with(stt=ProviderRef(provider_id="openai-stt", credential_id="cred-oa-stt"))

    assert validate_agent_config(config, credential_providers=CREDENTIALS, connection=FULL_CLOUD).ok is True


# ------------------------------------------------------------------ errors and warnings
def test_a_flux_model_under_deepgram_stt_is_an_error_that_names_the_flux_entry() -> None:
    config = _with(
        stt=ProviderRef(provider_id="deepgram-stt", credential_id="cred-dg", model="flux-general-en")
    )

    errors = [i for i in _issues(config) if i.severity == "error" and i.path == "pipeline.stt"]
    assert [i.message for i in errors] == [DEEPGRAM_FLUX_MOVED_MESSAGE]
    assert "Deepgram Flux" in DEEPGRAM_FLUX_MOVED_MESSAGE


def test_the_flux_entry_takes_the_deepgram_key() -> None:
    config = _with(stt=ProviderRef(provider_id="deepgram-flux-stt", credential_id="cred-dg"))

    assert not [i for i in _issues(config) if i.path == "pipeline.stt" and i.severity == "error"]


def test_fireworks_stt_is_an_error_with_the_vendor_reason() -> None:
    config = _with(stt=ProviderRef(provider_id="fireworksai-stt", credential_id="cred-fw"))

    errors = [i.message for i in _issues(config) if i.severity == "error" and i.path == "pipeline.stt"]
    assert errors == ["Fireworks stopped its speech service on 2026-06-10; pick another transcriber."]


def test_a_deprecated_model_is_a_warning_naming_the_new_default() -> None:
    config = _with(
        tts=ProviderRef(provider_id="inworld-tts", credential_id="cred-inworld", model="inworld-tts-1.5-max")
    )

    warnings = [i.message for i in _issues(config) if i.severity == "warning" and i.path == "pipeline.tts"]
    assert warnings == ["'inworld-tts-1.5-max' is deprecated by the vendor; pick 'inworld-tts-2'"]


def test_an_older_but_current_model_is_not_flagged() -> None:
    config = _with(
        tts=ProviderRef(provider_id="cartesia-tts", credential_id="cred-cartesia", model="sonic-3")
    )

    assert not [i for i in _issues(config) if i.path == "pipeline.tts"]


# ------------------------------------------------------------------ compatibility (D-V6-4c)
@pytest.mark.parametrize(
    ("provider_id", "model", "fields", "expected"),
    [
        # Refs as a pre-V6-02 console stored them: explicit model, no new field.
        ("openai-stt", None, {"model": "gpt-4o-mini-transcribe"}, {"model": "gpt-4o-mini-transcribe"}),
        ("rime-tts", "mistv3", {"speaker": "cove"}, {"speaker": "cove", "lang": "eng"}),
        ("elevenlabs-tts", "eleven_turbo_v2_5", {"voice_id": "v1"}, {"voice_id": "v1"}),
        ("cartesia-tts", "sonic-3", {"voice": "v1"}, {"voice": "v1", "language": "en"}),
    ],
)
def test_an_agent_saved_before_the_new_fields_resolves_to_the_same_kwargs(
    provider_id: str, model: str | None, fields: dict[str, str], expected: dict[str, str]
) -> None:
    """No new field has a default, so nothing new reaches the plugin; stored models are kept."""
    resolved = resolve_provider_ref(ProviderRef(provider_id=provider_id, model=model, fields=fields), {})

    assert resolved.kwargs == expected
    assert resolved.model == (model or get(provider_id).default_model)
