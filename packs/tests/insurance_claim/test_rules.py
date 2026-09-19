"""Parity tests for packs.insurance_claim.rules (ported from test_policies.py).

Ported verbatim from the original offline characterization suite
(commit c4472b0, ``tests/test_policies.py``) with only the import paths
changed: ``policies`` -> ``packs.insurance_claim.rules``, ``schemas`` ->
``packs.insurance_claim.schemas``. These pin the exact behavior of
``validate_required_claim_fields``, ``apply_coverage_and_evidence_rules``,
``generate_document_checklist``, ``fraud_signal_and_safety_gate``, and
``build_claim_intake_packet`` — the deterministic core the whole pack's
behaviour parity rests on. Where the behavior is a known bug, the test stays
marked ``xfail(strict=True)`` with the original reason: the parity bar is
"same claims produce the same output", not "the output is correct", and
fixing these would be a behaviour change out of scope for this port.
"""

from __future__ import annotations

import pytest

from packs.insurance_claim.rules import (
    apply_coverage_and_evidence_rules,
    build_claim_intake_packet,
    fraud_signal_and_safety_gate,
    generate_document_checklist,
    validate_required_claim_fields,
)
from packs.insurance_claim.schemas import ClaimNarrative

# ---------------------------------------------------------------------------
# validate_required_claim_fields
# ---------------------------------------------------------------------------


def test_validate_required_claim_fields_complete_claim_valid(claim_factory):
    claim = claim_factory(estimated_loss_usd=5000)
    result = validate_required_claim_fields(claim)
    assert result["intake_status"] == "valid"
    assert result["missing_fields"] == []
    assert result["ready_for_policy_review"] is True
    assert result["warnings"] == []


def test_validate_required_claim_fields_blank_claim_all_missing():
    blank = ClaimNarrative(
        policyholder_name="not specified",
        policy_number="",
        contact_method="unknown",
        date_of_loss="n/a",
        reported_date="not specified",
        loss_location="unspecified",
        loss_description="none",
        raw_narrative_summary="not specified",
    )
    result = validate_required_claim_fields(blank)
    assert result["intake_status"] == "missing_info"
    assert result["ready_for_policy_review"] is False
    assert set(result["missing_fields"]) == {
        "policyholder_name",
        "policy_number",
        "contact_method",
        "date_of_loss",
        "loss_location",
        "loss_description",
    }


@pytest.mark.parametrize("blank_value", ["", "unknown", "Not Specified", "N/A", "none", "not provided"])
def test_validate_required_claim_fields_blank_variants_missing(claim_factory, blank_value):
    claim = claim_factory(policyholder_name=blank_value)
    result = validate_required_claim_fields(claim)
    assert "policyholder_name" in result["missing_fields"]


def test_validate_required_claim_fields_no_estimated_loss_warns(claim_factory):
    claim = claim_factory(estimated_loss_usd=None)
    result = validate_required_claim_fields(claim)
    assert "Estimated loss amount was not supplied." in result["warnings"]


def test_validate_required_claim_fields_uncertain_facts_added_to_missing_and_deduped(claim_factory):
    claim = claim_factory(
        estimated_loss_usd=100,
        missing_or_uncertain_facts=["exact repair cost", "exact repair cost", "  "],
    )
    result = validate_required_claim_fields(claim)
    assert result["missing_fields"] == ["exact repair cost"]
    assert result["intake_status"] == "missing_info"


def test_validate_required_claim_fields_accepts_dict_and_json_string(claim_factory):
    claim = claim_factory(estimated_loss_usd=100)
    from_model = validate_required_claim_fields(claim)
    from_dict = validate_required_claim_fields(claim.model_dump())
    from_json = validate_required_claim_fields(claim.model_dump_json())
    assert from_model == from_dict == from_json


# ---------------------------------------------------------------------------
# apply_coverage_and_evidence_rules — routing decisions
# ---------------------------------------------------------------------------


def test_apply_coverage_rules_missing_fields_routes_needs_docs(claim_factory, classification_factory):
    claim = claim_factory(policyholder_name="")
    classification = classification_factory()
    validation = validate_required_claim_fields(claim)
    decision = apply_coverage_and_evidence_rules(claim, validation, classification)
    assert decision["routing_decision"] == "needs_docs"
    assert any(f["rule_id"] == "INTAKE-001" for f in decision["findings"])


def test_apply_coverage_rules_missing_documents_routes_needs_docs(claim_factory, classification_factory):
    claim = claim_factory(estimated_loss_usd=1000)  # no evidence_available/documents_mentioned
    classification = classification_factory(claim_type="auto_collision")
    validation = validate_required_claim_fields(claim)
    decision = apply_coverage_and_evidence_rules(claim, validation, classification)
    assert decision["routing_decision"] == "needs_docs"
    doc_findings = [f for f in decision["findings"] if f["rule_id"] == "DOC-001"]
    assert len(doc_findings) == 5  # every auto_collision required doc is unconfirmed


def test_apply_coverage_rules_travel_claim_with_all_evidence_ready_for_adjuster(
    claim_factory, classification_factory
):
    claim = claim_factory(
        policyholder_name="Alex Chen",
        policy_number="TRV-7711",
        contact_method="646-555-0112",
        date_of_loss="2026-01-14",
        reported_date="2026-01-15",
        loss_location="JFK to Reykjavik",
        loss_description=(
            "Flight cancelled due to winter storm, missed prepaid glacier tour and hotel nights."
        ),
        estimated_loss_usd=3200,
        evidence_available=[
            "airline emails",
            "hotel receipts",
            "tour confirmation",
            "credit card statements",
        ],
        documents_mentioned=["airline cancellation notice", "original itinerary", "refund voucher"],
        raw_narrative_summary="Travel cancellation due to storm",
    )
    classification = classification_factory(
        claim_type="travel_delay_cancellation", likely_policy_line="Single trip travel"
    )
    validation = validate_required_claim_fields(claim)
    decision = apply_coverage_and_evidence_rules(claim, validation, classification)
    assert decision["findings"] == []
    assert decision["routing_decision"] == "ready_for_adjuster"


def test_apply_coverage_rules_auto_collision_injury_escalates(claim_factory, classification_factory):
    claim = claim_factory(
        injuries_or_safety_concerns=["passenger has neck pain and went to urgent care"],
        evidence_available=["photos", "police report number PDX-24-8811", "tow receipt"],
    )
    classification = classification_factory(claim_type="auto_collision", severity="urgent")
    validation = validate_required_claim_fields(claim)
    decision = apply_coverage_and_evidence_rules(claim, validation, classification)
    assert decision["routing_decision"] == "emergency_escalation"
    assert any(f["rule_id"] == "SAFE-002" for f in decision["findings"])


def test_apply_coverage_rules_home_water_damage_unsafe_condition_escalates(
    claim_factory, classification_factory
):
    claim = claim_factory(
        loss_description=(
            "Basement flooded and now there is visible mold and an electrical hazard near the panel."
        ),
        raw_narrative_summary="Basement flood with mold and electrical hazard",
    )
    classification = classification_factory(claim_type="home_water_damage", severity="urgent")
    validation = validate_required_claim_fields(claim)
    decision = apply_coverage_and_evidence_rules(claim, validation, classification)
    assert decision["routing_decision"] == "emergency_escalation"
    assert any(f["rule_id"] == "SAFE-001" for f in decision["findings"])


def test_apply_coverage_rules_theft_without_police_report_flagged(claim_factory, classification_factory):
    claim = claim_factory(
        loss_description="Laptop taken from a backpack at a coffee shop.",
        raw_narrative_summary="Stolen laptop.",
        evidence_available=["purchase receipt", "serial number"],
    )
    classification = classification_factory(claim_type="theft_property_loss")
    validation = validate_required_claim_fields(claim)
    decision = apply_coverage_and_evidence_rules(claim, validation, classification)
    assert any(f["rule_id"] == "THEFT-001" for f in decision["findings"])
    assert decision["routing_decision"] == "needs_docs"


def test_apply_coverage_rules_theft_with_police_report_no_theft_finding(
    claim_factory, classification_factory
):
    claim = claim_factory(
        loss_description="Laptop taken from a backpack at a coffee shop.",
        raw_narrative_summary="Stolen laptop, police report number PDX-1122 was filed.",
        evidence_available=["purchase receipt", "serial number", "police report number PDX-1122"],
    )
    classification = classification_factory(claim_type="theft_property_loss")
    validation = validate_required_claim_fields(claim)
    decision = apply_coverage_and_evidence_rules(claim, validation, classification)
    assert not any(f["rule_id"] == "THEFT-001" for f in decision["findings"])


def test_apply_coverage_rules_high_loss_adds_finding_but_not_route_by_itself(
    claim_factory, classification_factory
):
    claim = claim_factory(
        estimated_loss_usd=30000,
        evidence_available=["photos", "receipts", "estimate", "contractor", "mitigation invoice"],
        loss_description="desc of the loss with photos receipts estimate contractor",
        raw_narrative_summary=(
            "full narrative with photos, receipts, estimate, contractor, mitigation invoice"
        ),
    )
    classification = classification_factory(claim_type="home_water_damage", severity="high")
    validation = validate_required_claim_fields(claim)
    decision = apply_coverage_and_evidence_rules(claim, validation, classification)
    assert any(f["rule_id"] == "LOSS-001" for f in decision["findings"])
    # LOSS-001 alone (no missing fields/docs/injury) does not force needs_docs/escalation.
    assert decision["routing_decision"] == "ready_for_adjuster"


def test_apply_coverage_rules_unclear_claim_type_notes_human_triage(claim_factory, classification_factory):
    claim = claim_factory()
    classification = classification_factory(claim_type="other")
    validation = validate_required_claim_fields(claim)
    decision = apply_coverage_and_evidence_rules(claim, validation, classification)
    assert any("human triage" in note for note in decision["provisional_coverage_considerations"])


@pytest.mark.parametrize(
    "phrase",
    ["no one was hurt", "no one was injured", "not injured"],
)
def test_apply_coverage_rules_negated_injury_language_does_not_escalate(
    claim_factory, classification_factory, phrase
):
    claim = claim_factory(
        loss_description=f"Car accident, {phrase} in the crash.",
        raw_narrative_summary=f"Fender bender, {phrase}.",
    )
    classification = classification_factory(claim_type="auto_collision")
    validation = validate_required_claim_fields(claim)
    decision = apply_coverage_and_evidence_rules(claim, validation, classification)
    assert not any(f["rule_id"] == "SAFE-002" for f in decision["findings"])
    assert decision["routing_decision"] != "emergency_escalation"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG: _without_negated_safety_mentions in policies.py only strips 'no' as a separate "
        "word followed by an optional 'was' before the injury term. It does not match the "
        "contraction 'nobody', nor present-tense 'is'/'are'. As a result 'nobody is hurt' and "
        "'no one is hurt' are NOT recognized as negated and still trip SAFE-002, incorrectly "
        "escalating a claim where nobody was hurt. This test documents the intended safe "
        "behavior (no escalation); it currently fails against the real bug."
    ),
)
@pytest.mark.parametrize("phrase", ["nobody is hurt", "no one is hurt", "nobody was hurt"])
def test_apply_coverage_rules_negated_injury_contraction_should_not_escalate_but_does(
    claim_factory, classification_factory, phrase
):
    claim = claim_factory(
        loss_description=f"Car accident, {phrase} in the crash.",
        raw_narrative_summary=f"Fender bender, {phrase}.",
    )
    classification = classification_factory(claim_type="auto_collision")
    validation = validate_required_claim_fields(claim)
    decision = apply_coverage_and_evidence_rules(claim, validation, classification)
    assert decision["routing_decision"] != "emergency_escalation"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG: the home_water_damage SAFE-001 check (policies.py apply_coverage_and_evidence_rules) "
        "runs its unsafe/mold/sewage regex directly against the raw evidence text with no negation "
        "stripping at all (unlike SAFE-002's _has_positive_safety_language). 'no sewage and no mold' "
        "still matches \\bsewage\\b and \\bmold\\b and incorrectly triggers emergency_escalation."
    ),
)
def test_apply_coverage_rules_home_water_negated_hazard_should_not_escalate_but_does(
    claim_factory, classification_factory
):
    claim = claim_factory(
        loss_description="Basement flooded but there is no sewage and no mold, drywall is wet.",
        raw_narrative_summary="Basement flood, no sewage, no mold.",
    )
    classification = classification_factory(claim_type="home_water_damage")
    validation = validate_required_claim_fields(claim)
    decision = apply_coverage_and_evidence_rules(claim, validation, classification)
    assert decision["routing_decision"] != "emergency_escalation"


# ---------------------------------------------------------------------------
# generate_document_checklist
# ---------------------------------------------------------------------------


def test_generate_document_checklist_marks_required_vs_recommended(claim_factory, classification_factory):
    claim = claim_factory(estimated_loss_usd=1000)
    classification = classification_factory(claim_type="auto_collision")
    validation = validate_required_claim_fields(claim)
    decision = apply_coverage_and_evidence_rules(claim, validation, classification)
    checklist = generate_document_checklist(claim, classification, decision)
    required_items = {item["item"] for item in checklist["items"] if item["priority"] == "required"}
    assert required_items == set(decision["required_documents"])
    assert all(item["already_provided"] is False for item in checklist["items"])


def test_generate_document_checklist_auto_collision_injury_adds_injured_people_item(
    claim_factory, classification_factory
):
    claim = claim_factory(injuries_or_safety_concerns=["passenger has neck pain and went to urgent care"])
    classification = classification_factory(claim_type="auto_collision")
    validation = validate_required_claim_fields(claim)
    decision = apply_coverage_and_evidence_rules(claim, validation, classification)
    checklist = generate_document_checklist(claim, classification, decision)
    injury_items = [
        i for i in checklist["items"] if i["item"] == "Names of injured people and treatment locations"
    ]
    assert len(injury_items) == 1
    assert injury_items[0]["priority"] == "required"
    # NOTE: _all_evidence_text() (policies.py) does not include
    # injuries_or_safety_concerns, so "urgent care" mentioned only there is not
    # detected here even though it drove the SAFE-002 escalation upstream.
    assert injury_items[0]["already_provided"] is False


def test_generate_document_checklist_has_claimant_tip(claim_factory, classification_factory):
    claim = claim_factory()
    classification = classification_factory()
    validation = validate_required_claim_fields(claim)
    decision = apply_coverage_and_evidence_rules(claim, validation, classification)
    checklist = generate_document_checklist(claim, classification, decision)
    assert checklist["claimant_tip"]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG: _document_provided's fallthrough (policies.py) checks the document title's first "
        "three words against the evidence text when no keyword group matches. For "
        "'Other driver and witness information' those words are 'other', 'driver', 'and' — the "
        "common word 'and' appears in almost any narrative, so the item is wrongly marked as "
        "already provided even when no other-driver information was ever given."
    ),
)
def test_generate_document_checklist_fallthrough_word_match_false_positive(
    claim_factory, classification_factory
):
    claim = claim_factory(
        loss_description="Something happened and it was bad.",
        raw_narrative_summary="A short narrative and nothing else.",
        evidence_available=[],
        documents_mentioned=[],
    )
    classification = classification_factory(claim_type="auto_collision")
    validation = validate_required_claim_fields(claim)
    decision = apply_coverage_and_evidence_rules(claim, validation, classification)
    checklist = generate_document_checklist(claim, classification, decision)
    other_driver_item = next(
        i for i in checklist["items"] if i["item"] == "Other driver and witness information"
    )
    assert other_driver_item["already_provided"] is False


# ---------------------------------------------------------------------------
# fraud_signal_and_safety_gate
# ---------------------------------------------------------------------------


def _run_gate(claim, classification, claim_kwargs=None):
    validation = validate_required_claim_fields(claim)
    decision = apply_coverage_and_evidence_rules(claim, validation, classification)
    return fraud_signal_and_safety_gate(claim, validation, classification, decision)


def test_fraud_gate_report_before_loss_date_routes_siu(claim_factory, classification_factory):
    claim = claim_factory(date_of_loss="2026-04-02", reported_date="2026-03-01")
    classification = classification_factory()
    gate = _run_gate(claim, classification)
    assert any(s["signal_id"] == "TIMING-001" for s in gate["signals"])
    assert gate["final_routing_decision"] == "special_investigation"


def test_fraud_gate_reported_over_90_days_late_routes_siu(claim_factory, classification_factory):
    claim = claim_factory(date_of_loss="2026-01-01", reported_date="2026-06-01")
    classification = classification_factory()
    gate = _run_gate(claim, classification)
    assert any(s["signal_id"] == "TIMING-002" for s in gate["signals"])
    assert gate["final_routing_decision"] == "special_investigation"


def test_fraud_gate_timely_report_no_timing_signal(claim_factory, classification_factory):
    claim = claim_factory(date_of_loss="2026-04-02", reported_date="2026-04-03")
    classification = classification_factory()
    gate = _run_gate(claim, classification)
    assert not any(s["signal_id"].startswith("TIMING") for s in gate["signals"])


def test_fraud_gate_vague_facts_signal_does_not_force_siu(claim_factory, classification_factory):
    claim = claim_factory(loss_description="I'm not sure exactly what happened, maybe a break-in.")
    classification = classification_factory()
    gate = _run_gate(claim, classification)
    facts_signals = [s for s in gate["signals"] if s["signal_id"] == "FACTS-001"]
    assert len(facts_signals) == 1
    assert facts_signals[0]["route_to_siu"] is False


def test_fraud_gate_high_loss_without_evidence_routes_siu(claim_factory, classification_factory):
    claim = claim_factory(estimated_loss_usd=15000, evidence_available=[], documents_mentioned=[])
    classification = classification_factory(claim_type="other")
    gate = _run_gate(claim, classification)
    assert any(s["signal_id"] == "EVID-001" for s in gate["signals"])
    assert gate["final_routing_decision"] == "special_investigation"


def test_fraud_gate_high_loss_with_evidence_no_evid_signal(claim_factory, classification_factory):
    claim = claim_factory(estimated_loss_usd=15000, evidence_available=["photos"])
    classification = classification_factory(claim_type="other")
    gate = _run_gate(claim, classification)
    assert not any(s["signal_id"] == "EVID-001" for s in gate["signals"])


def test_fraud_gate_theft_no_police_filed_signal_does_not_force_siu(claim_factory, classification_factory):
    claim = claim_factory(
        loss_description="Bike stolen, I have not filed a police report yet.",
        raw_narrative_summary="Stolen bike, no police report filed yet.",
    )
    classification = classification_factory(claim_type="theft_property_loss")
    gate = _run_gate(claim, classification)
    theft_signals = [s for s in gate["signals"] if s["signal_id"] == "THEFT-002"]
    assert len(theft_signals) == 1
    assert theft_signals[0]["route_to_siu"] is False


def test_fraud_gate_emergency_route_carries_through_from_coverage_decision(
    claim_factory, classification_factory
):
    claim = claim_factory(
        injuries_or_safety_concerns=["broken arm, taken to hospital"],
        loss_description="Collision, driver has a broken arm and was taken to the hospital.",
    )
    classification = classification_factory(claim_type="auto_collision", severity="urgent")
    gate = _run_gate(claim, classification)
    assert gate["final_routing_decision"] == "emergency_escalation"
    assert any(s["signal_id"] == "SAFETY-001" and s["route_to_emergency"] for s in gate["signals"])


def test_fraud_gate_missing_core_facts_signal(claim_factory, classification_factory):
    claim = claim_factory(date_of_loss="", loss_location="", loss_description="")
    classification = classification_factory()
    gate = _run_gate(claim, classification)
    assert any(s["signal_id"] == "INTAKE-002" for s in gate["signals"])


def test_fraud_gate_emergency_outranks_siu_when_both_present(claim_factory, classification_factory):
    """Timing (SIU) and injury (emergency) both fire; emergency must win."""

    claim = claim_factory(
        date_of_loss="2026-04-02",
        reported_date="2026-01-01",  # before loss date -> TIMING-001 (SIU)
        injuries_or_safety_concerns=["broken arm, taken to hospital"],
        loss_description="Collision, driver has a broken arm and was taken to the hospital.",
    )
    classification = classification_factory(claim_type="auto_collision", severity="urgent")
    gate = _run_gate(claim, classification)
    signal_ids = {s["signal_id"] for s in gate["signals"]}
    assert {"TIMING-001", "SAFETY-001"} <= signal_ids
    assert gate["final_routing_decision"] == "emergency_escalation"


# ---------------------------------------------------------------------------
# build_claim_intake_packet
# ---------------------------------------------------------------------------


def _full_pipeline(claim, classification):
    validation = validate_required_claim_fields(claim)
    decision = apply_coverage_and_evidence_rules(claim, validation, classification)
    checklist = generate_document_checklist(claim, classification, decision)
    gate = fraud_signal_and_safety_gate(claim, validation, classification, decision)
    packet = build_claim_intake_packet(claim, validation, classification, decision, checklist, gate)
    return validation, decision, checklist, gate, packet


def test_build_claim_intake_packet_markdown_has_all_sections(claim_factory, classification_factory):
    claim = claim_factory(estimated_loss_usd=1000)
    classification = classification_factory()
    _, _, _, _, packet = _full_pipeline(claim, classification)
    markdown = packet["markdown"]
    for heading in [
        "# Insurance Claim Intake Packet",
        "## Missing Information",
        "## Required Documents Checklist",
        "## Coverage Considerations and Disclaimer",
        "## Adjuster Handoff Summary",
        "## Claimant-Friendly Next Message",
        "## Deterministic Findings",
        "## Fraud, Timing, and Safety Signals",
        "## Audit Trail",
    ]:
        assert heading in markdown
    assert "does not confirm coverage, benefits, liability, payment, or legal rights" in markdown


def test_build_claim_intake_packet_routing_decision_label_title_cased(claim_factory, classification_factory):
    claim = claim_factory(policyholder_name="")
    classification = classification_factory()
    _, _, _, _, packet = _full_pipeline(claim, classification)
    assert "**Routing decision:** Needs Docs" in packet["markdown"]
    assert packet["routing_decision"] == "needs_docs"


def test_build_claim_intake_packet_next_message_emergency_escalation(claim_factory, classification_factory):
    claim = claim_factory(
        injuries_or_safety_concerns=["broken arm, taken to hospital"],
        loss_description="Collision, driver has a broken arm and was taken to the hospital.",
    )
    classification = classification_factory(claim_type="auto_collision", severity="urgent")
    _, _, _, _, packet = _full_pipeline(claim, classification)
    assert "human representative" in packet["claimant_next_message"]
    assert "emergency services" in packet["claimant_next_message"]


def test_build_claim_intake_packet_next_message_asks_for_blocking_field(
    claim_factory, classification_factory
):
    claim = claim_factory(policyholder_name="")
    classification = classification_factory()
    _, _, _, _, packet = _full_pipeline(claim, classification)
    assert packet["claimant_next_message"] == "What is your full name as it appears on the policy?"


def test_build_claim_intake_packet_next_message_asks_for_uncertain_fact(
    claim_factory, classification_factory
):
    claim = claim_factory(estimated_loss_usd=100, missing_or_uncertain_facts=["exact repair cost"])
    classification = classification_factory(claim_type="other")
    _, _, _, _, packet = _full_pipeline(claim, classification)
    assert "exact repair cost" in packet["claimant_next_message"]


def test_build_claim_intake_packet_next_message_asks_for_document(claim_factory, classification_factory):
    claim = claim_factory(
        policyholder_name="Priya Shah",
        policy_number="RNT-3008",
        contact_method="priya@example.com",
        date_of_loss="2026-02-09",
        reported_date="2026-02-09",
        loss_location="Austin coffee shop",
        loss_description="Laptop stolen from backpack.",
        estimated_loss_usd=2400,
        evidence_available=["purchase receipt", "serial number"],
        raw_narrative_summary="Stolen laptop, no police report yet",
    )
    classification = classification_factory(claim_type="theft_property_loss")
    _, decision, _, _, packet = _full_pipeline(claim, classification)
    assert decision["required_documents"]
    assert packet["claimant_next_message"].startswith("Do you have this document or evidence available now:")


def test_build_claim_intake_packet_next_message_default_ready_message(claim_factory, classification_factory):
    claim = claim_factory(
        policyholder_name="Alex Chen",
        policy_number="TRV-7711",
        contact_method="646-555-0112",
        date_of_loss="2026-01-14",
        reported_date="2026-01-15",
        loss_location="JFK to Reykjavik",
        loss_description=(
            "Flight cancelled due to winter storm, missed prepaid glacier tour and hotel nights."
        ),
        estimated_loss_usd=3200,
        evidence_available=[
            "airline emails",
            "hotel receipts",
            "tour confirmation",
            "credit card statements",
        ],
        documents_mentioned=["airline cancellation notice", "original itinerary", "refund voucher"],
        raw_narrative_summary="Travel cancellation due to storm",
    )
    classification = classification_factory(
        claim_type="travel_delay_cancellation", likely_policy_line="Single trip travel"
    )
    _, decision, _, gate, packet = _full_pipeline(claim, classification)
    assert decision["routing_decision"] == "ready_for_adjuster"
    assert gate["final_routing_decision"] == "ready_for_adjuster"
    assert "core information needed for adjuster assignment" in packet["claimant_next_message"]


def test_build_claim_intake_packet_accepts_dict_inputs(claim_factory, classification_factory):
    """agent.py passes dicts (from ADK session state), not model instances."""

    claim = claim_factory(estimated_loss_usd=1000)
    classification = classification_factory()
    validation = validate_required_claim_fields(claim.model_dump())
    decision = apply_coverage_and_evidence_rules(claim.model_dump(), validation, classification.model_dump())
    checklist = generate_document_checklist(claim.model_dump(), classification.model_dump(), decision)
    gate = fraud_signal_and_safety_gate(claim.model_dump(), validation, classification.model_dump(), decision)
    packet = build_claim_intake_packet(
        claim.model_dump(), validation, classification.model_dump(), decision, checklist, gate
    )
    assert packet["markdown"].startswith("# Insurance Claim Intake Packet")
