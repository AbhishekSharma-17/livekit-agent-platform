"""Telephony contracts (R-V2-21 transfer targets, R-V2-22 variables, R-V2-25 promoted models)."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

import lkap_contracts
from lkap_contracts import api_models
from lkap_contracts.agent_config import AgentConfig, PipelineConfig, ResolvedAgentConfig
from lkap_contracts.export import EXPORTED_MODELS
from lkap_contracts.telephony import (
    DTMF_PATTERN,
    E164_PATTERN,
    TRANSFER_TARGET_PATTERN,
    TelephonyConfig,
    TransferTarget,
)


@pytest.mark.parametrize(
    "to", ["+15551234567", "tel:+15551234567", "sip:desk@pbx.example.com", "sips:+15551230000@carrier"]
)
def test_transfer_target_accepts_numbers_and_uris(to: str) -> None:
    assert TransferTarget(label="Desk", to=to).to == to


@pytest.mark.parametrize("to", ["call mom", "15551234567", "+0123456789", "sip:nohost", ""])
def test_transfer_target_rejects_free_text(to: str) -> None:
    with pytest.raises(ValidationError):
        TransferTarget(label="Desk", to=to)


@pytest.mark.parametrize("label", ["", "x" * 65])
def test_transfer_target_label_is_1_to_64_characters(label: str) -> None:
    with pytest.raises(ValidationError):
        TransferTarget(label=label, to="+15551234567")


def test_agent_config_telephony_is_additive_with_an_empty_default() -> None:
    config = AgentConfig(instructions="x", pipeline=PipelineConfig())
    assert config.telephony == TelephonyConfig(transfer_targets=[])
    stored = config.model_dump(mode="json")
    stored.pop("telephony")
    assert AgentConfig.model_validate(stored).telephony.transfer_targets == []


def test_resolved_agent_config_variables_default_to_empty() -> None:
    field = ResolvedAgentConfig.model_fields["variables"]
    assert field.default == {}


def test_patterns_are_valid_regexes() -> None:
    assert re.match(E164_PATTERN, "+15551234567")
    assert re.match(DTMF_PATTERN, "12*#AD")
    assert not re.match(TRANSFER_TARGET_PATTERN, "reception")


def test_promoted_models_are_exported_to_schemas_and_the_package() -> None:
    promoted = [
        "TrunkCreate",
        "TrunkUpdate",
        "TrunkOut",
        "TrunkPage",
        "DispatchRuleCreate",
        "DispatchRuleOut",
        "DispatchRulePage",
        "PhoneNumberCreate",
        "PhoneNumberUpdate",
        "PhoneNumberOut",
        "PhoneNumberPage",
        "CallTransferIn",
        "CallDtmfIn",
        "CallDtmfOut",
        "CallReportIn",
        "InternalTransferIn",
        "InternalTransferOut",
        "TransferTarget",
        "TelephonyConfig",
    ]
    for name in promoted:
        assert name in EXPORTED_MODELS, name
        assert name in lkap_contracts.__all__, name


def test_call_transfer_in_strips_and_checks_the_target() -> None:
    assert api_models.CallTransferIn(to=" +15551234567 ").to == "+15551234567"
    with pytest.raises(ValidationError):
        api_models.InternalTransferIn(to="call mom")
    with pytest.raises(ValidationError):
        api_models.CallDtmfIn(digits="12x")
