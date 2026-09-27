"""V5-30: `PrivacyConfig`, `QaConfig.fields` and the shared post-call fields model."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from lkap_contracts import providers
from lkap_contracts.agent_config import AgentConfig, PrivacyConfig, QaConfig
from lkap_contracts.api_models import QaOut, SessionDetailOut
from lkap_contracts.qa import QaField, qa_field_guide, qa_fields_model


def _config(**extra: Any) -> AgentConfig:
    return AgentConfig.model_validate({"instructions": "Help.", "pipeline": {}, **extra})


def test_privacy_defaults_keep_todays_behaviour() -> None:
    """Compatibility: an agent saved before `privacy` masks nothing and keeps everything."""
    config = _config()
    assert config.privacy == PrivacyConfig()
    assert config.privacy.stt_redact == []
    assert config.privacy.storage_tier == "full"
    assert config.privacy.telemetry_pii is True
    assert config.privacy.scrub_model is None
    assert config.qa.fields == []


def test_stt_redact_is_deduplicated_and_closed() -> None:
    privacy = PrivacyConfig.model_validate({"stt_redact": ["pci", "pii", "pci"]})
    assert privacy.stt_redact == ["pci", "pii"]
    with pytest.raises(ValidationError):
        PrivacyConfig.model_validate({"stt_redact": ["ssn"]})
    with pytest.raises(ValidationError):
        PrivacyConfig.model_validate({"storage_tier": "none"})


def test_the_telemetry_flag_says_cloud_insights_is_not_affected() -> None:
    """The card: the console note about LiveKit Cloud Insights lives in the contract description."""
    description = PrivacyConfig.model_fields["telemetry_pii"].description or ""
    assert "LiveKit Cloud Insights" in description


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ({"name": "claim_type", "type": "select"}, "needs at least one option"),
        ({"name": "injury", "type": "boolean", "options": ["yes"]}, "only a select field"),
        ({"name": "Claim Type"}, "pattern"),
        ({"name": "claim", "type": "select", "options": ["a", "a"]}, "unique"),
        ({"name": "claim", "type": "select", "options": [" "]}, "1-100 characters"),
    ],
)
def test_a_malformed_field_is_refused(field: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        QaField.model_validate(field)


def test_field_names_are_unique_per_agent() -> None:
    with pytest.raises(ValidationError, match="unique: injury"):
        QaConfig.model_validate({"fields": [{"name": "injury"}, {"name": "injury", "type": "boolean"}]})


def test_at_most_twenty_fields() -> None:
    with pytest.raises(ValidationError):
        QaConfig.model_validate({"fields": [{"name": f"f{i}"} for i in range(21)]})


def test_the_fields_model_types_each_field_and_defaults_to_null() -> None:
    fields = [
        QaField(name="claim_type", type="select", options=["auto", "home"], description="Kind of claim"),
        QaField(name="injury", type="boolean"),
        QaField(name="amount", type="number"),
        QaField(name="notes"),
        # A name that is also a `BaseModel` attribute must not break the model.
        QaField(name="json"),
    ]
    model = qa_fields_model(fields)
    schema = model.model_json_schema()
    assert set(schema["properties"]) == {"claim_type", "injury", "amount", "notes", "json"}
    assert schema["properties"]["claim_type"]["anyOf"][0]["enum"] == ["auto", "home"]

    values = model.model_validate_json('{"claim_type": "home", "injury": false, "amount": 12.5, "other": 1}')
    assert values.model_dump(by_alias=True) == {
        "claim_type": "home",
        "injury": False,
        "amount": 12.5,
        "notes": None,
        "json": None,
    }
    with pytest.raises(ValidationError):
        model.model_validate_json('{"claim_type": "boat"}')


def test_the_field_guide_lists_choices_and_descriptions() -> None:
    guide = qa_field_guide(
        [
            QaField(name="claim_type", type="select", options=["auto", "home"], description="Kind of claim"),
            QaField(name="injury", type="boolean"),
        ]
    )
    assert guide == "- claim_type (one of: 'auto', 'home') - Kind of claim\n- injury (boolean)"


def test_deepgram_is_the_one_stt_entry_that_can_redact() -> None:
    """The card: `capabilities.redaction` on the STT entries that support it (Deepgram Nova)."""
    redacting = {spec.id for spec in providers.REGISTRY if spec.capabilities.redaction}
    assert redacting == {"deepgram-stt"}
    assert providers.get("deepgram-stt").capabilities.redaction == ["pci", "pii", "phi", "numbers"]
    # LiveKit Inference is not confirmed to pass `redact` through (STT redaction deferred).
    assert providers.get("livekit-inference-stt").capabilities.redaction == []


def test_qa_out_and_session_detail_carry_the_new_fields_with_empty_defaults() -> None:
    assert QaOut().fields == {}
    assert "scrubbed_at" in SessionDetailOut.model_fields
