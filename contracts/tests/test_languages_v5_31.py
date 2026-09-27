"""V5-31: agent languages, per-language voices, language capabilities and the captions contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lkap_contracts.agent_config import (
    AgentConfig,
    ResolvedAgentConfig,
    VoiceConfig,
    effective_languages,
    voice_for_language,
)
from lkap_contracts.api_models import LanguageSwitchedEvent, TranscriptTurn
from lkap_contracts.blocks import CaptionsBlockConfig, validate_block_config
from lkap_contracts.common import ProviderRef
from lkap_contracts.providers import (
    REGISTRY,
    base_language,
    declares_language,
    get,
    language_name,
)
from lkap_contracts.tools import (
    BUILTIN_TOOL_NAMES,
    LANGUAGE_TOOL_NAMES,
    NEVER_BACKGROUND_TOOLS,
    builtin_tools_document,
)
from lkap_contracts.ui_protocol import TOPIC_UI_CAPTIONS, BlockSpec, CaptionsBlockState, CaptionSegment


def test_voice_config_defaults_keep_one_language() -> None:
    voice = VoiceConfig()
    assert voice.languages == []
    assert voice.auto_detect is False
    assert voice.voices_by_language == {}
    assert effective_languages(voice) == ["en"]


def test_an_agent_saved_before_v5_31_resolves_to_its_single_language() -> None:
    stored = {"instructions": "Help.", "pipeline": {}, "voice": {"greeting": "Hi", "language": "hi"}}
    config = AgentConfig.model_validate(stored)
    assert effective_languages(config.voice) == ["hi"]
    assert "languages" not in config.model_dump(exclude_defaults=True).get("voice", {})


def test_languages_first_is_default() -> None:
    voice = VoiceConfig(language="en", languages=["hi-IN", "en"])
    assert effective_languages(voice) == ["hi-IN", "en"]


@pytest.mark.parametrize("code", ["EN", "hindi", "", "en_US", "e"])
def test_languages_reject_codes_that_are_not_language_tags(code: str) -> None:
    with pytest.raises(ValidationError):
        VoiceConfig(languages=[code])


def test_languages_reject_duplicates() -> None:
    with pytest.raises(ValidationError, match="twice"):
        VoiceConfig(languages=["en", "hi", "en"])


def test_languages_are_capped() -> None:
    with pytest.raises(ValidationError):
        VoiceConfig(languages=[f"x{i:02d}"[:3] for i in range(11)])


def test_voice_for_language_matches_exact_key_then_base_code() -> None:
    hindi = ProviderRef(provider_id="sarvam-tts", fields={"speaker": "anushka"})
    english = ProviderRef(provider_id="livekit-inference-tts")
    voice = VoiceConfig(languages=["en", "hi"], voices_by_language={"hi-IN": hindi, "en": english})
    assert voice_for_language(voice, "en") is english
    assert voice_for_language(voice, "hi") is hindi
    assert voice_for_language(voice, "fr") is None


def test_voices_by_language_keys_are_language_tags() -> None:
    with pytest.raises(ValidationError):
        VoiceConfig(voices_by_language={"Hindi": ProviderRef(provider_id="sarvam-tts")})


def test_resolved_config_voices_default_empty() -> None:
    assert ResolvedAgentConfig.model_fields["voices_by_language"].default == {}


@pytest.mark.parametrize(
    ("code", "base"), [("hi-IN", "hi"), ("pt_BR", "pt"), ("EN", "en"), (" kok-IN ", "kok")]
)
def test_base_language(code: str, base: str) -> None:
    assert base_language(code) == base


def test_language_name_falls_back_to_the_code() -> None:
    assert language_name("hi-IN") == "Hindi"
    assert language_name("xx") == "xx"


def test_declares_language_is_unknown_for_an_empty_list() -> None:
    assert declares_language([], "hi") is None
    assert declares_language(["en", "hi"], "hi-IN") is True
    assert declares_language(["en"], "ta") is False


def test_deepgram_and_inference_detect_with_multi_over_ten_languages() -> None:
    for provider_id in ("livekit-inference-stt", "deepgram-stt"):
        caps = get(provider_id).capabilities
        assert caps.language_detection == "multi"
        assert caps.language_switch is True
        assert sorted(caps.detect_languages) == sorted(
            ["en", "es", "fr", "de", "hi", "ru", "pt", "ja", "it", "nl"]
        )
        assert "hi" in caps.languages
        assert "ta" in caps.languages
        assert "ta" not in caps.detect_languages


def test_openai_class_detects_and_switches() -> None:
    for provider_id in ("openai-stt", "openrouter-stt"):
        caps = get(provider_id).capabilities
        assert caps.language_detection == "multi"
        assert caps.language_switch is True
        assert len(caps.languages) == 57


def test_sarvam_detects_with_unknown_but_cannot_switch_mid_call() -> None:
    caps = get("sarvam-stt").capabilities
    assert caps.language_detection == "unknown"
    assert caps.language_switch is False
    assert {"hi", "ta", "en"} <= set(caps.languages)
    assert "hi" in get("sarvam-tts").capabilities.languages


@pytest.mark.parametrize("provider_id", ["google-stt", "gladia-stt", "assemblyai-stt", "elevenlabs-stt"])
def test_plugins_with_another_language_kwarg_do_not_claim_a_switch(provider_id: str) -> None:
    assert get(provider_id).capabilities.language_switch is False


def test_only_stt_entries_claim_detection_or_switching() -> None:
    for spec in REGISTRY:
        caps = spec.capabilities
        if caps.language_detection is not None or caps.language_switch or caps.detect_languages:
            assert spec.kind == "stt", spec.id


def test_detect_languages_are_within_languages() -> None:
    for spec in REGISTRY:
        caps = spec.capabilities
        assert set(caps.detect_languages) <= set(caps.languages), spec.id


def test_switch_language_is_a_builtin_that_never_runs_in_the_background() -> None:
    assert "switch_language" in BUILTIN_TOOL_NAMES
    assert LANGUAGE_TOOL_NAMES == frozenset({"switch_language"})
    assert "switch_language" in NEVER_BACKGROUND_TOOLS
    assert "switch_language" in builtin_tools_document()["builtin_tool_names"]


def test_captions_block_config_defaults_and_strictness() -> None:
    config = CaptionsBlockConfig()
    assert (config.show_user, config.show_agent, config.target_language, config.position) == (
        True,
        True,
        None,
        "block",
    )
    ok = BlockSpec(id="cap", type="captions", config={"position": "bottom", "target_language": "hi"})
    assert validate_block_config(ok) == []
    bad = BlockSpec(id="cap", type="captions", config={"font": "big"})
    assert [issue.path for issue in validate_block_config(bad)] == ["config.font"]
    wrong = BlockSpec(id="cap", type="captions", config={"position": "top"})
    assert validate_block_config(wrong)


def test_caption_segment_wire_shape() -> None:
    segment = CaptionSegment(id="u-1", speaker="user", text="namaste", final=False, language="hi", ts=1.0)
    assert segment.model_dump() == {
        "v": 1,
        "id": "u-1",
        "speaker": "user",
        "text": "namaste",
        "final": False,
        "language": "hi",
        "ts": 1.0,
    }
    assert TOPIC_UI_CAPTIONS == "lkap.captions"
    with pytest.raises(ValidationError):
        CaptionSegment(id="", speaker="user", text="x", final=True, ts=0)
    with pytest.raises(ValidationError):
        CaptionSegment.model_validate({"id": "a", "speaker": "robot", "text": "x", "final": True, "ts": 0})


def test_captions_block_state_defaults() -> None:
    assert CaptionsBlockState().model_dump() == {"language": None, "target_language": None}


def test_transcript_turn_language_is_optional() -> None:
    old = TranscriptTurn.model_validate({"role": "user", "text": "hi", "ts": 1.0})
    assert old.language is None
    assert TranscriptTurn(role="user", text="namaste", ts=1.0, language="hi").language == "hi"


def test_language_switched_event() -> None:
    event = LanguageSwitchedEvent(from_language="en", to_language="hi", source="tool", voice_switched=True)
    assert event.model_dump() == {
        "from_language": "en",
        "to_language": "hi",
        "source": "tool",
        "stt_switched": False,
        "voice_switched": True,
    }
