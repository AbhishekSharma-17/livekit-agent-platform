"""V6-02 on the worker: the new speech fields reach the plugins, Flux ends turns, the PCM clamp.

Plugins are constructed offline with placeholder keys (never connected).
"""

from __future__ import annotations

from typing import Any

import pytest
from fakes.fake_api import resolved_config
from livekit.plugins import deepgram, openai
from lkap_contracts import providers as provider_registry
from lkap_contracts.agent_config import ResolvedAgentConfig, ResolvedProvider

from lkap_agent.providers.factory import BuiltProviders, ProviderFactory, deepgram_flux_kwargs
from lkap_agent.providers.openrouter import (
    MAX_PCM_CHANNELS,
    MAX_PCM_SAMPLE_RATE,
    MIN_PCM_SAMPLE_RATE,
    AudioFormat,
    parse_audio_content_type,
)
from lkap_agent.session_builder import STT_TURN_DETECTION, SessionBuilder, stt_decides_turns

KEY = "dg-placeholder-key"


@pytest.fixture(autouse=True)
def _inference_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret")
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")


def _resolved(provider_id: str, *, model: str | None = None, **kwargs: Any) -> ResolvedProvider:
    spec = provider_registry.get(provider_id)
    return ResolvedProvider(
        provider_id=provider_id, python_class=spec.python_class, model=model, kwargs=kwargs
    )


def _with_stt(config: ResolvedAgentConfig, provider: ResolvedProvider) -> ResolvedAgentConfig:
    return config.model_copy(update={"resolved": {**config.resolved, "stt": provider}})


# ------------------------------------------------------------------ fields reach the plugin
def test_openai_stt_use_realtime_reaches_the_plugin_and_turns_streaming_on() -> None:
    stt = ProviderFactory().build(
        "stt",
        _resolved("openai-stt", api_key="sk-placeholder", model="gpt-4o-mini-transcribe", use_realtime=True),
    )

    assert isinstance(stt, openai.STT)
    assert stt.capabilities.streaming is True


def test_openai_stt_without_the_field_stays_batch() -> None:
    stt = ProviderFactory().build(
        "stt", _resolved("openai-stt", api_key="sk-placeholder", model="gpt-4o-mini-transcribe")
    )

    assert stt.capabilities.streaming is False


def test_the_registry_streaming_flag_matches_the_installed_plugins() -> None:
    """D-V6-2 against the real 1.8.3 plugins this venv carries (the rest come from the source)."""
    cases = {
        "openai-stt": ProviderFactory().build(
            "stt", _resolved("openai-stt", api_key="sk-placeholder", model="gpt-4o-mini-transcribe")
        ),
        "deepgram-stt": ProviderFactory().build("stt", _resolved("deepgram-stt", api_key=KEY)),
        "deepgram-flux-stt": ProviderFactory().build("stt", _resolved("deepgram-flux-stt", api_key=KEY)),
        "openai-tts": ProviderFactory().build("tts", _resolved("openai-tts", api_key="sk-placeholder")),
    }
    for provider_id, built in cases.items():
        assert built.capabilities.streaming is provider_registry.get(provider_id).capabilities.streaming


def test_deepgram_flux_builds_stt_v2_with_its_end_of_turn_settings() -> None:
    stt = ProviderFactory().build(
        "stt", _resolved("deepgram-flux-stt", api_key=KEY, eot_threshold=0.8, eot_timeout_ms=2500.0)
    )

    assert isinstance(stt, deepgram.STTv2)
    assert stt.model == "flux-general-en"
    assert stt._opts.eot_threshold == 0.8
    assert stt._opts.eot_timeout_ms == 2500 and isinstance(stt._opts.eot_timeout_ms, int)


@pytest.mark.parametrize(
    ("given", "expected"),
    [(3000.0, 3000), (3000, 3000), (2999.5, 2999.5), (None, None)],
)
def test_deepgram_flux_kwargs_makes_the_timeout_a_whole_number(
    given: float | None, expected: float | None
) -> None:
    kwargs = {"model": "flux-general-en"} if given is None else {"eot_timeout_ms": given}

    assert deepgram_flux_kwargs(kwargs).get("eot_timeout_ms") == expected


def test_flux_ignores_stt_redaction_rather_than_passing_a_list() -> None:
    """STTv2's `redact` is one string; the entry declares no redaction classes, so none is sent."""
    stt = ProviderFactory().build("stt", _resolved("deepgram-flux-stt", api_key=KEY), stt_redact=["pii"])

    assert isinstance(stt, deepgram.STTv2)


@pytest.mark.parametrize(
    ("provider_id", "model", "kwargs"),
    [
        ("openai-stt", None, {"api_key": "sk-placeholder", "model": "gpt-4o-mini-transcribe"}),
        ("elevenlabs-tts", "eleven_turbo_v2_5", {"api_key": "sk-placeholder", "voice_id": "v1"}),
        ("cartesia-tts", "sonic-3", {"api_key": "sk-placeholder", "voice": "v1", "language": "en"}),
    ],
)
def test_a_ref_saved_before_v6_02_builds_with_the_same_kwargs(
    provider_id: str, model: str | None, kwargs: dict[str, Any]
) -> None:
    """Compatibility (D-V6-4c): no new kwarg appears; an explicitly stored model is kept."""
    built = ProviderFactory()._constructor_kwargs(
        provider_registry.get(provider_id),
        ResolvedProvider(
            provider_id=provider_id,
            python_class=provider_registry.get(provider_id).python_class,
            model=model,
            kwargs=kwargs,
        ),
        "cascaded",
    )

    expected = dict(kwargs)
    if model is not None:
        expected["model"] = model
    assert built == expected


# ------------------------------------------------------------------ turn-taking by the transcriber
async def test_flux_makes_the_session_use_stt_turn_detection() -> None:
    config = _with_stt(resolved_config(), _resolved("deepgram-flux-stt", api_key=KEY))
    providers = BuiltProviders(llm=object(), stt=object(), tts=object())

    plan = SessionBuilder().build(config, providers, vad=object(), turn_detector=object())

    assert stt_decides_turns(config) is True
    assert plan.session.turn_detection == STT_TURN_DETECTION


async def test_an_explicit_turn_detection_slot_still_wins_over_flux() -> None:
    config = _with_stt(resolved_config(), _resolved("deepgram-flux-stt", api_key=KEY))
    slot_detector = object()
    providers = BuiltProviders(llm=object(), stt=object(), tts=object(), turn_detection=slot_detector)

    plan = SessionBuilder().build(config, providers, vad=object(), turn_detector=object())

    assert plan.session.turn_detection is slot_detector


async def test_a_transcriber_without_end_of_turn_keeps_the_default_detector() -> None:
    config = _with_stt(resolved_config(), _resolved("deepgram-stt", api_key=KEY))
    default_detector = object()
    providers = BuiltProviders(llm=object(), stt=object(), tts=object())

    plan = SessionBuilder().build(config, providers, vad=object(), turn_detector=default_detector)

    assert stt_decides_turns(config) is False
    assert plan.session.turn_detection is default_detector


# ------------------------------------------------------------------ the PCM clamp (#331)
@pytest.mark.parametrize(
    ("header", "rate", "channels"),
    [
        ("audio/pcm;rate=16000;channels=1", 16_000, 1),
        ("audio/pcm;rate=4000;channels=1", MIN_PCM_SAMPLE_RATE, 1),
        ("audio/pcm;rate=192000;channels=2", MAX_PCM_SAMPLE_RATE, 2),
        ("audio/pcm;rate=24000;channels=64", 24_000, MAX_PCM_CHANNELS),
        ("audio/pcm;rate=8000;channels=8", 8_000, 8),
        ("audio/pcm;rate=48000", 48_000, 1),
    ],
)
def test_pcm_rate_and_channels_are_clamped(header: str, rate: int, channels: int) -> None:
    assert parse_audio_content_type(header, requested_format="pcm") == AudioFormat(
        mime_type="audio/pcm", sample_rate=rate, num_channels=channels
    )
