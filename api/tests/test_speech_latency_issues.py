"""`speech_latency_issues`: batch-only OpenRouter speech and language codes the transcriber rejects."""

from __future__ import annotations

import pytest
from conftest import inference_config
from lkap_contracts.agent_config import AgentConfig, ProviderRef
from lkap_contracts.api_models import Issue

from lkap_api.config_service import ValidationContext, speech_latency_issues, validate_agent_config

CREDENTIALS = {"cred-or": "openrouter-llm"}


def _openrouter_speech(*, language: str | None = "en", mode: str = "cascaded") -> AgentConfig:
    config = inference_config()
    fields = {} if language is None else {"language": language}
    config.pipeline.stt = ProviderRef(provider_id="openrouter-stt", credential_id="cred-or", fields=fields)
    config.pipeline.tts = ProviderRef(provider_id="openrouter-tts", credential_id="cred-or")
    config.pipeline.mode = mode  # type: ignore[assignment]
    return config


def speech_latency_issues_for(config: AgentConfig) -> list[Issue]:
    return speech_latency_issues(ValidationContext(config=config, credential_providers=CREDENTIALS))


def _paths(config: AgentConfig) -> dict[str, str]:
    result = validate_agent_config(config, credential_providers=CREDENTIALS)
    return {issue.path: issue.message for issue in result.issues if issue.severity == "warning"}


def test_openrouter_stt_and_tts_each_warn_about_batch_latency() -> None:
    warnings = _paths(_openrouter_speech())

    assert "one request after you stop speaking" in warnings["pipeline.stt"]
    assert "LiveKit Inference" in warnings["pipeline.stt"]
    assert "not streamed" in warnings["pipeline.tts"]
    assert "pipeline.stt.fields.language" not in warnings


def test_the_warnings_are_not_errors() -> None:
    result = validate_agent_config(_openrouter_speech(language="multi"), credential_providers=CREDENTIALS)

    assert result.ok is True


def test_streaming_inference_speech_gets_no_latency_warning() -> None:
    config = inference_config()

    assert speech_latency_issues_for(config) == []


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("multi", "auto-detected"),
        ("auto", "auto-detected"),
        ("en-US", "'en' will be sent instead"),
        ("pt_BR", "'pt' will be sent instead"),
    ],
)
def test_a_non_iso_639_1_language_on_an_openai_style_transcriber_warns(language: str, expected: str) -> None:
    message = _paths(_openrouter_speech(language=language))["pipeline.stt.fields.language"]

    assert f"'{language}'" in message
    assert expected in message


def test_multi_on_a_deepgram_style_stt_is_not_flagged() -> None:
    config = inference_config()
    config.pipeline.stt = ProviderRef(provider_id="livekit-inference-stt", fields={"language": "multi"})

    assert speech_latency_issues_for(config) == []


def test_realtime_mode_ignores_the_unused_speech_slots() -> None:
    config = _openrouter_speech(language="multi", mode="realtime")

    assert speech_latency_issues_for(config) == []


def test_half_cascade_checks_only_the_tts() -> None:
    config = _openrouter_speech(language="multi", mode="half_cascade")

    assert [issue.path for issue in speech_latency_issues_for(config)] == ["pipeline.tts"]
