"""Parity tests for packs.insurance_claim.schemas (ported from test_schemas.py).

Ported verbatim from the original offline characterization suite
(commit c4472b0, ``tests/test_schemas.py``); only the import path changed.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from packs.insurance_claim.schemas import (
    ClaimClassification,
    ClaimIntakePacket,
    ClaimNarrative,
    CoverageEvidenceDecision,
    DocumentChecklist,
    DocumentChecklistItem,
    EvidenceRuleFinding,
    FieldValidation,
    FraudSafetyGate,
    FraudSafetySignal,
)


def _minimal_claim(**overrides):
    defaults = dict(
        policyholder_name="A",
        policy_number="B",
        contact_method="c",
        date_of_loss="2026-01-01",
        reported_date="2026-01-01",
        loss_location="home",
        loss_description="desc",
        raw_narrative_summary="summary",
    )
    defaults.update(overrides)
    return defaults


def test_claim_narrative_valid_minimal_data():
    claim = ClaimNarrative(**_minimal_claim())
    assert claim.estimated_loss_usd is None
    assert claim.injuries_or_safety_concerns == []


def test_claim_narrative_missing_required_fields_raises():
    with pytest.raises(ValidationError) as exc_info:
        ClaimNarrative()
    errors = exc_info.value.errors()
    missing_fields = {e["loc"][0] for e in errors if e["type"] == "missing"}
    assert missing_fields == {
        "policyholder_name",
        "policy_number",
        "contact_method",
        "date_of_loss",
        "reported_date",
        "loss_location",
        "loss_description",
        "raw_narrative_summary",
    }


def test_claim_narrative_estimated_loss_usd_numeric_string_coerced():
    claim = ClaimNarrative(**_minimal_claim(estimated_loss_usd="1234.5"))
    assert claim.estimated_loss_usd == 1234.5
    assert isinstance(claim.estimated_loss_usd, float)


def test_claim_narrative_estimated_loss_usd_invalid_string_raises():
    with pytest.raises(ValidationError):
        ClaimNarrative(**_minimal_claim(estimated_loss_usd="not-a-number"))


def test_field_validation_valid_data():
    fv = FieldValidation(intake_status="valid", ready_for_policy_review=True)
    assert fv.missing_fields == []
    assert fv.warnings == []


def test_field_validation_bad_intake_status_raises():
    with pytest.raises(ValidationError):
        FieldValidation(intake_status="bogus", ready_for_policy_review=True)


@pytest.mark.parametrize(
    "claim_type",
    [
        "home_water_damage",
        "auto_collision",
        "theft_property_loss",
        "health_medical_reimbursement",
        "travel_delay_cancellation",
        "other",
    ],
)
def test_claim_classification_valid_claim_types(claim_type):
    classification = ClaimClassification(
        claim_type=claim_type,
        severity="medium",
        severity_rationale="x",
        likely_policy_line="x",
    )
    assert classification.claim_type == claim_type


def test_claim_classification_bad_claim_type_raises():
    with pytest.raises(ValidationError) as exc_info:
        ClaimClassification(
            claim_type="not_a_real_type",
            severity="low",
            severity_rationale="x",
            likely_policy_line="x",
        )
    assert exc_info.value.errors()[0]["type"] == "literal_error"


@pytest.mark.parametrize("severity", ["low", "medium", "high", "urgent"])
def test_claim_classification_valid_severities(severity):
    classification = ClaimClassification(
        claim_type="other", severity=severity, severity_rationale="x", likely_policy_line="x"
    )
    assert classification.severity == severity


def test_claim_classification_bad_severity_raises():
    with pytest.raises(ValidationError):
        ClaimClassification(
            claim_type="other", severity="catastrophic", severity_rationale="x", likely_policy_line="x"
        )


@pytest.mark.parametrize(
    "required_action",
    ["collect_info", "collect_document", "adjuster_review", "siu_review", "emergency_escalation"],
)
def test_evidence_rule_finding_valid_required_actions(required_action):
    finding = EvidenceRuleFinding(
        rule_id="X", severity="medium", message="m", required_action=required_action
    )
    assert finding.required_action == required_action


def test_evidence_rule_finding_bad_required_action_raises():
    with pytest.raises(ValidationError):
        EvidenceRuleFinding(rule_id="X", severity="medium", message="m", required_action="escalate_now")


def test_coverage_evidence_decision_bad_routing_decision_raises():
    with pytest.raises(ValidationError):
        CoverageEvidenceDecision(routing_decision="not_a_route")


@pytest.mark.parametrize("priority", ["required", "recommended", "conditional"])
def test_document_checklist_item_valid_priorities(priority):
    item = DocumentChecklistItem(item="Photo", reason="why", priority=priority)
    assert item.already_provided is False


def test_document_checklist_item_bad_priority_raises():
    with pytest.raises(ValidationError):
        DocumentChecklistItem(item="Photo", reason="why", priority="urgent")


def test_document_checklist_valid_data():
    checklist = DocumentChecklist(
        items=[DocumentChecklistItem(item="Photo", reason="why", priority="required")],
        claimant_tip="tip",
    )
    assert len(checklist.items) == 1


def test_fraud_safety_signal_defaults_false():
    signal = FraudSafetySignal(signal_id="X", severity="low", message="m")
    assert signal.route_to_siu is False
    assert signal.route_to_emergency is False


@pytest.mark.parametrize(
    "route", ["ready_for_adjuster", "needs_docs", "special_investigation", "emergency_escalation"]
)
def test_fraud_safety_gate_valid_routes(route):
    gate = FraudSafetyGate(final_routing_decision=route)
    assert gate.final_routing_decision == route


def test_fraud_safety_gate_bad_route_raises():
    with pytest.raises(ValidationError):
        FraudSafetyGate(final_routing_decision="escalate")


def test_claim_intake_packet_valid_data():
    packet = ClaimIntakePacket(
        claim_type="other",
        intake_status="valid",
        severity="low",
        routing_decision="ready_for_adjuster",
        adjuster_handoff_summary="summary",
        claimant_next_message="message",
        markdown="# Packet",
    )
    assert packet.missing_information == []
    assert packet.required_documents == []


def test_claim_intake_packet_missing_required_field_raises():
    with pytest.raises(ValidationError):
        ClaimIntakePacket(
            claim_type="other",
            intake_status="valid",
            severity="low",
            routing_decision="ready_for_adjuster",
            adjuster_handoff_summary="summary",
            claimant_next_message="message",
            # markdown intentionally omitted
        )
