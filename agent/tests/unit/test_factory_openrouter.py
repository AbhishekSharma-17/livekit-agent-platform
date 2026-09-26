"""V4-03: `ProviderFactory` builds the OpenRouter entries with the real 1.8.2 plugin classes.

Nothing here opens a socket: the plugin constructors only build an `openai.AsyncClient`,
and the one network call (`OpenRouterImageGen.generate`) is mocked with `respx`.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
import respx
from livekit.plugins import openai
from lkap_contracts.agent_config import ProviderSlot, ResolvedProvider
from lkap_contracts.providers import get as get_spec

from lkap_agent.providers.factory import ProviderBuildError, ProviderFactory
from lkap_agent.providers.image_gen import OpenRouterImageGen
from lkap_agent.providers.openrouter import OpenRouterTTS

API_KEY = "sk-or-v1-test-not-real"


def _resolved(provider_id: str, *, model: str | None = None, **fields: Any) -> ResolvedProvider:
    """Mirror the api's `resolve_provider_ref`: field defaults, then admin fields, then the secret."""
    spec = get_spec(provider_id)
    kwargs: dict[str, Any] = {f.name: f.default for f in spec.fields if f.default is not None}
    kwargs.update(fields)
    kwargs["api_key"] = API_KEY
    return ResolvedProvider(
        provider_id=spec.id,
        python_class=spec.python_class,
        model=model or spec.default_model,
        kwargs=kwargs,
    )


def _build(slot: ProviderSlot, provider: ResolvedProvider) -> Any:
    return ProviderFactory().build(slot, provider)


# ---------------------------------------------------------------------------------- llm
def test_llm_defaults_route_to_openrouter_with_tool_safe_provider_preferences() -> None:
    llm = _build("llm", _resolved("openrouter-llm"))

    assert isinstance(llm, openai.LLM)
    assert llm._client.base_url.host == "openrouter.ai"
    assert llm.model == "openai/gpt-4.1-mini"
    assert llm._opts.extra_body == {"provider": {"require_parameters": True}}
    assert llm._opts.extra_headers == {"X-Title": "LKAP"}
    assert "HTTP-Referer" not in llm._opts.extra_headers


def test_llm_provider_preferences_and_fallbacks_come_from_the_json_fields() -> None:
    llm = _build(
        "llm",
        _resolved(
            "openrouter-llm",
            provider='{"require_parameters": false, "sort": "latency"}',
            fallback_models='["openai/gpt-4o-mini", "google/gemini-3.5-flash"]',
            site_url="https://example.com",
        ),
    )

    assert llm._opts.extra_body["provider"] == {"require_parameters": False, "sort": "latency"}
    assert llm._opts.extra_body["models"] == [
        "openai/gpt-4.1-mini",
        "openai/gpt-4o-mini",
        "google/gemini-3.5-flash",
    ]
    assert llm._opts.extra_headers == {"HTTP-Referer": "https://example.com", "X-Title": "LKAP"}


def test_llm_workflow_slot_builds_the_same_way() -> None:
    llm = _build("workflow_llm", _resolved("openrouter-llm", model="openai/gpt-4o-mini", provider=""))

    assert llm.model == "openai/gpt-4o-mini"
    assert llm._opts.extra_body == {"provider": {"require_parameters": True}}


@pytest.mark.parametrize(
    ("field", "value"),
    [("provider", "{not json"), ("provider", '["a"]'), ("fallback_models", '{"a": 1}')],
)
def test_llm_rejects_malformed_json_fields(field: str, value: str) -> None:
    with pytest.raises(ProviderBuildError, match=field):
        _build("llm", _resolved("openrouter-llm", **{field: value}))


# ------------------------------------------------------------------------------ stt / tts
def test_stt_is_the_batch_openai_transcriber_pointed_at_openrouter() -> None:
    stt = _build("stt", _resolved("openrouter-stt"))

    assert isinstance(stt, openai.STT)
    assert stt.capabilities.streaming is False
    assert stt.capabilities.interim_results is False
    assert stt._client.base_url.host == "openrouter.ai"
    assert stt.model == "openai/gpt-4o-mini-transcribe"


def test_tts_is_the_openrouter_adapter_with_pcm_and_the_configured_voice() -> None:
    tts = _build("tts", _resolved("openrouter-tts", voice="Puck"))

    assert isinstance(tts, OpenRouterTTS)
    assert tts.capabilities.streaming is False
    # Gemini TTS on OpenRouter rejects mp3 (the stock plugin's default) with a 400.
    assert tts._opts.response_format == "pcm"
    assert tts._opts.voice == "Puck"
    assert tts._opts.model == "google/gemini-3.8-flash-tts"
    assert tts._client.base_url.host == "openrouter.ai"


def test_tts_voxtral_is_asked_for_mp3() -> None:
    tts = _build("tts", _resolved("openrouter-tts", model="mistralai/voxtral-mini-tts-2603", voice="x"))

    assert tts._opts.response_format == "mp3"


def test_tts_default_voice_is_kore() -> None:
    tts = _build("tts", _resolved("openrouter-tts"))

    assert tts._opts.voice == "Kore"


def test_tts_uses_the_worker_adapter_when_the_api_resolved_the_old_plugin_class() -> None:
    """Version skew: an api on the previous registry still names `livekit.plugins.openai.TTS`."""
    stale = _resolved("openrouter-tts").model_copy(update={"python_class": "livekit.plugins.openai.TTS"})

    assert isinstance(_build("tts", stale), OpenRouterTTS)


@pytest.mark.parametrize(
    ("language", "expected_languages", "detects"),
    [
        ("multi", [], True),
        ("auto", [], True),
        ("", [], True),
        ("en", ["en"], False),
        ("en-US", ["en"], False),
        ("pt_BR", ["pt"], False),
        ("HI", ["hi"], False),
    ],
)
def test_stt_language_becomes_an_iso_639_1_code_or_auto_detect(
    language: str, expected_languages: list[str], detects: bool
) -> None:
    """`multi` (Deepgram's code) made every OpenRouter transcription a 400 in real sessions."""
    stt = _build("stt", _resolved("openrouter-stt", model="microsoft/mai-transcribe-2", language=language))

    assert stt._opts.detect_language is detects
    assert stt._opts.languages == expected_languages


# ------------------------------------------------------------------------------- image_gen
async def test_image_gen_posts_to_openrouter_images_and_decodes_the_result() -> None:
    image_gen = _build("image_gen", _resolved("openrouter-image-gen", resolution="2K", aspect_ratio="16:9"))
    assert isinstance(image_gen, OpenRouterImageGen)
    raw = b"\x89PNG-bytes"

    with respx.mock:
        route = respx.post("https://openrouter.ai/api/v1/images").mock(
            return_value=httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(raw).decode(), "media_type": "image/webp"}]},
            )
        )
        image, mime = await image_gen.generate("a lighthouse at dusk")

    assert (image, mime) == (raw, "image/webp")
    request = route.calls.last.request
    assert json.loads(request.content) == {
        "model": "openai/gpt-image-1",
        "prompt": "a lighthouse at dusk",
        "resolution": "2K",
        "aspect_ratio": "16:9",
        "n": 1,
    }
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.headers["X-Title"] == "LKAP"
