"""Tests for `lkap_contracts.providers.vision_support` (DECISIONS-W2 §D-W2-10)."""

import pytest

from lkap_contracts.providers import get, vision_support


@pytest.mark.parametrize(
    ("provider_id", "model", "expected"),
    [
        ("livekit-inference-llm", "google/gemini-3.5-flash", True),
        ("livekit-inference-llm", "google/gemma-4-31b-it", False),
        ("livekit-inference-llm", None, False),  # default_model is gemma
        ("livekit-inference-llm", "anything/else", None),
        ("google-realtime", "gemini-3.8-live", True),
        ("no-such-provider", "google/gemini-3.5-flash", None),
    ],
)
def test_vision_support_known_unknown_and_default(
    provider_id: str, model: str | None, expected: bool | None
) -> None:
    assert vision_support(provider_id, model) is expected


def test_vision_support_provider_without_default_model_returns_none() -> None:
    assert vision_support("http-tool-secret", None) is None


def test_gemma_stays_the_text_default_with_a_note() -> None:
    spec = get("livekit-inference-llm")
    assert spec.default_model == "google/gemma-4-31b-it"
    gemma = next(m for m in spec.models if m.id == "google/gemma-4-31b-it")
    assert gemma.note is not None and "text-only" in gemma.note


def test_only_verified_inference_llm_models_are_flagged() -> None:
    spec = get("livekit-inference-llm")
    assert [m.id for m in spec.models if m.supports_video] == ["google/gemini-3.5-flash"]
