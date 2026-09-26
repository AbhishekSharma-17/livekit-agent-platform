"""Consent and disclosure contracts (V5-15, D-V5-22)."""

from __future__ import annotations

import hashlib
import json
import re

import pytest
from pydantic import ValidationError

import lkap_contracts
from lkap_contracts.agent_config import AgentConfig, DisclosureConfig, RecordingConfig, ResolvedAgentConfig
from lkap_contracts.blocks import BLOCK_CONFIG_MODELS, ConsentBlockConfig, validate_block_config
from lkap_contracts.compliance import (
    COMPLIANCE_PRESETS,
    DEFAULT_JURISDICTION,
    ComplianceSettings,
    ConsentEvent,
    ConsentState,
    ResolvedCompliance,
    consent_text_hash,
    resolve_compliance,
)
from lkap_contracts.export import EXPORTED_BLOCK_CONFIGS, EXPORTED_MODELS
from lkap_contracts.tools import BLOCK_TOOL_NAMES, BLOCK_TOOL_TYPES, NEVER_BACKGROUND_TOOLS
from lkap_contracts.ui_protocol import BlockSpec, ConsentBlockState, RequestableState

#: Words that must never reach a caller or a builder in the preset wording (§0.1 UI rules).
_JARGON = re.compile(r"\b(RRF|HMAC|PKCE|RFC|DCR|CIMD|egress|SIP|DPDP|GDPR|Art\.|Article)\b", re.IGNORECASE)


@pytest.mark.parametrize("jurisdiction", ["eu", "in", "us"])
def test_presets_read_plainly(jurisdiction: str) -> None:
    preset = COMPLIANCE_PRESETS[jurisdiction]  # type: ignore[index]
    assert preset.jurisdiction == jurisdiction
    for text in (preset.label, preset.disclosure_text, preset.recording_text, preset.counsel_note):
        assert text.strip() == text and text
        assert not _JARGON.search(text), text
    assert "AI" in preset.disclosure_text
    assert preset.recording_text.endswith("?")
    assert "counsel" in preset.counsel_note


def test_us_preset_names_two_party_consent_without_a_state_list() -> None:
    note = COMPLIANCE_PRESETS["us"].counsel_note
    assert "confirm with counsel for two-party-consent states" in note
    for state in ("California", "Florida", "Illinois", "Washington"):
        assert state not in note


def test_default_jurisdiction_is_india() -> None:
    assert DEFAULT_JURISDICTION == "in"
    assert ComplianceSettings().jurisdiction == "in"
    assert ResolvedCompliance() == resolve_compliance(None)


def test_resolve_compliance_uses_rewrites_then_the_preset() -> None:
    eu = COMPLIANCE_PRESETS["eu"]
    resolved = resolve_compliance({"jurisdiction": "eu", "recording_text": "May we record this call?"})
    assert resolved.jurisdiction == "eu"
    assert resolved.disclosure_text == eu.disclosure_text
    assert resolved.recording_text == "May we record this call?"


def test_blank_rewrites_fall_back_to_the_preset() -> None:
    settings = ComplianceSettings(jurisdiction="us", disclosure_text="   ", recording_text="")
    assert settings.disclosure_text is None and settings.recording_text is None
    assert resolve_compliance(settings).disclosure_text == COMPLIANCE_PRESETS["us"].disclosure_text


def test_invalid_stored_settings_resolve_to_the_default_preset() -> None:
    assert resolve_compliance({"jurisdiction": "mars"}) == ResolvedCompliance()


def test_compliance_settings_refuse_unknown_keys_and_long_text() -> None:
    with pytest.raises(ValidationError):
        ComplianceSettings.model_validate({"jurisdiction": "in", "states": ["CA"]})
    with pytest.raises(ValidationError):
        ComplianceSettings(disclosure_text="x" * 2001)


def test_text_hash_is_sha256_of_the_exact_utf8_text() -> None:
    text = "आप एक AI सहायक से बात कर रहे हैं। Is that okay?"
    assert consent_text_hash(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert consent_text_hash(text) != consent_text_hash(text + " ")
    assert re.fullmatch(r"[0-9a-f]{64}", consent_text_hash(""))


def test_consent_event_validates_the_hash() -> None:
    good = consent_text_hash("May we record?")
    event = ConsentEvent(kind="recording", accepted=True, method="voice", text_hash=good)
    assert event.block_id is None
    with pytest.raises(ValidationError):
        ConsentEvent(kind="recording", accepted=True, method="voice", text_hash="abc")
    with pytest.raises(ValidationError):
        ConsentEvent.model_validate(
            {"kind": "recording", "accepted": True, "method": "nod", "text_hash": good}
        )


def test_consent_state_keeps_the_latest_answer_per_kind() -> None:
    digest = consent_text_hash("t")
    state = ConsentState()
    assert state.answer("recording") is None
    state = state.with_event(
        ConsentEvent(kind="recording", accepted=False, method="tap", text_hash=digest), at=1.0
    )
    state = state.with_event(
        ConsentEvent(kind="ai_disclosure", accepted=True, method="tap", text_hash=digest), at=2.0
    )
    assert state.answer("recording") is False
    state = state.with_event(
        ConsentEvent(kind="recording", accepted=True, method="voice", text_hash=digest), at=3.0
    )
    assert state.answer("recording") is True
    assert state.latest["recording"].at == 3.0
    assert ConsentState.model_validate(json.loads(state.model_dump_json())) == state


def test_consent_block_config_defaults_and_strictness() -> None:
    assert BLOCK_CONFIG_MODELS["consent"] is ConsentBlockConfig
    config = ConsentBlockConfig()
    assert (config.kind, config.text, config.required, config.decline_action, config.show_banner) == (
        "recording",
        "",
        True,
        "continue",
        True,
    )
    issues = validate_block_config(
        BlockSpec(id="c", type="consent", config={"kind": "recording", "wording": "x"}),
        path="panel.blocks[0].config",
    )
    assert [i.path for i in issues] == ["panel.blocks[0].config.wording"]
    issues = validate_block_config(BlockSpec(id="c", type="consent", config={"decline_action": "hang_up"}))
    assert [i.path for i in issues] == ["config.decline_action"]


def test_consent_block_state_is_requestable() -> None:
    assert issubclass(ConsentBlockState, RequestableState)
    state = ConsentBlockState()
    assert (state.status, state.accepted, state.method, state.text_hash) == ("idle", None, None, None)
    answered = ConsentBlockState.model_validate(
        {"status": "submitted", "accepted": True, "method": "tap", "text_hash": consent_text_hash("x")}
    )
    assert answered.accepted is True
    with pytest.raises(ValidationError):
        ConsentBlockState.model_validate({"text_hash": "not-a-digest"})


def test_consent_tools_are_block_tools_that_never_run_in_the_background() -> None:
    assert BLOCK_TOOL_NAMES[-2:] == ("request_consent", "record_consent")
    assert BLOCK_TOOL_TYPES["request_consent"] == frozenset({"consent"})
    assert BLOCK_TOOL_TYPES["record_consent"] == frozenset({"consent"})
    assert {"request_consent", "record_consent"} <= NEVER_BACKGROUND_TOOLS


def test_disclosure_defaults_on_both_and_recording_consent_defaults_off() -> None:
    config = AgentConfig.model_validate({"instructions": "x", "pipeline": {}})
    assert config.disclosure == DisclosureConfig(enabled=True, text=None, position="both")
    assert config.recording.require_consent is False
    assert config.recording.consent_text is None
    with pytest.raises(ValidationError):
        DisclosureConfig.model_validate({"position": "footer"})
    with pytest.raises(ValidationError):
        RecordingConfig(consent_text="x" * 2001)


def test_resolved_config_compliance_is_optional_for_older_apis() -> None:
    fields = ResolvedAgentConfig.model_fields
    assert fields["compliance"].default is None


def test_an_agent_saved_before_v5_15_gains_the_defaults_and_round_trips() -> None:
    stored = {
        "v": 2,
        "instructions": "Help callers file a claim.",
        "pipeline": {"mode": "cascaded"},
        "voice": {"greeting": "Hello! How can I help?"},
        "recording": {"enabled": True, "audio_only": True},
    }
    config = AgentConfig.model_validate(stored)
    assert config.disclosure.enabled is True and config.disclosure.position == "both"
    assert config.recording.enabled is True and config.recording.require_consent is False
    assert config.voice.greeting == "Hello! How can I help?"
    assert AgentConfig.model_validate(config.model_dump(mode="json")) == config


def test_consent_models_are_exported() -> None:
    for name in (
        "ConsentBlockState",
        "ConsentEvent",
        "ConsentState",
        "ComplianceSettings",
        "CompliancePreset",
        "ResolvedCompliance",
        "ComplianceOut",
        "DisclosureConfig",
    ):
        assert name in EXPORTED_MODELS
        assert name in lkap_contracts.__all__
    assert "BlockConfig_consent" in EXPORTED_BLOCK_CONFIGS
    assert "ConsentBlockConfig" in lkap_contracts.__all__
