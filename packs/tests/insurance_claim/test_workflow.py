"""Parity tests for packs.insurance_claim.workflow.

Reproduces the original agent.py / server.py behaviour against the recorded
baseline fixtures (docs/INSURANCE_PACK_MAPPING.md #13, #15; ported
non-ADK-specific cases from the original ``tests/test_agent_graph.py``):

- an empty transcript reproduces ``insurance_initial_workflow.json`` exactly
  (was ``build_initial_workflow_state``/``test_build_initial_workflow_state_*``)
- the recorded ``flood``/``auto`` scenarios reproduce their fixture's
  ``workflow`` dict exactly when the fake LLM returns the fixture's own
  ``normalized_claim``/``claim_classification``
- the auto scenario reproduces the ``emergency_escalation`` routing and the
  lapsed-policy auto-attach (``attach_policy_from_claim``)
- re-running with unchanged intake text reuses the cached result and makes no
  further LLM calls (was ``_run_workflow_cached``)

The ADK graph-wiring tests from the original ``test_agent_graph.py``
(``root_agent`` sub-agent types/order, ``FunctionNode``/``FinalPacketNode``
event plumbing) are dropped: docs/ARCHITECTURE.md D9 removes ``google-adk``
entirely, and there is no ADK graph left to test the wiring of.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from packs.insurance_claim.instructions import SYSTEM_INSTRUCTION
from packs.insurance_claim.schemas import (
    ClaimClassification,
    ClaimIntakePacket,
    ClaimNarrative,
    CoverageEvidenceDecision,
    DocumentChecklist,
    FieldValidation,
    FraudSafetyGate,
    IntakeState,
)
from packs.insurance_claim.workflow import (
    ClaimWorkflow,
    WorkflowResult,
    attach_policy_from_claim,
    blank_claim,
    build_initial_workflow_state,
    initial_classification,
    voice_summary,
)
from tests.insurance_claim.conftest import load_fixture


class FakeStructuredLLM:
    """Dispatches on ``schema`` and returns pre-recorded fixture data.

    Mirrors ``packs.base.StructuredLLM`` without importing it (no livekit
    dependency needed for this fake), and counts calls so the caching
    behaviour of :meth:`ClaimWorkflow.run` can be asserted.
    """

    def __init__(self, *, claim: dict[str, Any], classification: dict[str, Any]) -> None:
        self._claim = claim
        self._classification = classification
        self.calls: list[type[BaseModel]] = []

    async def extract(
        self, *, instructions: str, input_text: str, schema: type[BaseModel], timeout_s: float = 45
    ) -> BaseModel:
        self.calls.append(schema)
        if schema is ClaimNarrative:
            return ClaimNarrative.model_validate(self._claim)
        if schema is ClaimClassification:
            return ClaimClassification.model_validate(self._classification)
        raise AssertionError(f"unexpected schema: {schema}")


def _intake_from_scenario(scenario: dict[str, Any]) -> IntakeState:
    turns = [turn["text"] for turn in scenario["transcript"] if turn["speaker"] == "Claimant"]
    return IntakeState(participant_turns=turns)


# ---------------------------------------------------------------------------
# instructions parity
# ---------------------------------------------------------------------------


def test_system_instruction_matches_fixture() -> None:
    fixture_text = load_fixture("insurance_system_instruction.txt").strip()
    assert SYSTEM_INSTRUCTION == fixture_text


# ---------------------------------------------------------------------------
# blank_claim / initial_classification / build_initial_workflow_state
# ---------------------------------------------------------------------------


def test_blank_claim_shape() -> None:
    claim = blank_claim()
    assert claim["policyholder_name"] == "not specified"
    assert claim["estimated_loss_usd"] is None
    assert claim["injuries_or_safety_concerns"] == []
    ClaimNarrative.model_validate(claim)


def test_initial_classification_shape() -> None:
    classification = initial_classification()
    assert classification["claim_type"] == "other"
    assert classification["severity"] == "medium"
    ClaimClassification.model_validate(classification)


def test_build_initial_workflow_state_validates_every_stage() -> None:
    result = build_initial_workflow_state()
    ClaimNarrative.model_validate(result.normalized_claim)
    FieldValidation.model_validate(result.field_validation)
    ClaimClassification.model_validate(result.claim_classification)
    CoverageEvidenceDecision.model_validate(result.coverage_evidence_decision)
    DocumentChecklist.model_validate(result.document_checklist)
    FraudSafetyGate.model_validate(result.fraud_safety_gate)
    ClaimIntakePacket.model_validate(result.claim_intake_packet)
    assert result.final_markdown == result.claim_intake_packet["markdown"]


def test_build_initial_workflow_state_matches_golden_fixture() -> None:
    result = build_initial_workflow_state()
    expected = load_fixture("insurance_initial_workflow.json")
    assert result.model_dump() == expected


def test_build_initial_workflow_state_blank_claim_missing_all_required_fields() -> None:
    result = build_initial_workflow_state()
    assert result.field_validation["intake_status"] == "missing_info"
    assert set(result.field_validation["missing_fields"]) == {
        "policyholder_name",
        "policy_number",
        "contact_method",
        "date_of_loss",
        "loss_location",
        "loss_description",
    }
    assert result.routing_decision == "needs_docs"


# ---------------------------------------------------------------------------
# ClaimWorkflow.run against the recorded scenarios
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario_name", ["flood", "auto"])
@pytest.mark.asyncio
async def test_run_matches_recorded_scenario_workflow(scenario_name: str) -> None:
    scenario = load_fixture(f"insurance_scenario_{scenario_name}.json")
    intake = _intake_from_scenario(scenario)
    llm = FakeStructuredLLM(
        claim=scenario["workflow"]["normalized_claim"],
        classification=scenario["workflow"]["claim_classification"],
    )
    result = await ClaimWorkflow(llm).run(intake)

    # The fixture's "normalized_claim" was hand-assembled as a plain dict by
    # tests/fixtures/record_baseline.py (it never passed through
    # `ClaimNarrative.model_dump(exclude_none=True)`, unlike the real
    # `run_claim_workflow`/`ClaimWorkflow._extract_and_apply_rules`), so it can
    # carry an explicit `"estimated_loss_usd": null` that the real code path
    # drops. Normalize both sides through the same exclude_none dump before
    # comparing so this is an apples-to-apples parity check.
    expected = dict(scenario["workflow"])
    expected["normalized_claim"] = ClaimNarrative.model_validate(expected["normalized_claim"]).model_dump(
        exclude_none=True
    )
    assert result.model_dump() == expected
    assert llm.calls == [ClaimNarrative, ClaimClassification]


@pytest.mark.asyncio
async def test_run_auto_scenario_routes_emergency_escalation() -> None:
    scenario = load_fixture("insurance_scenario_auto.json")
    intake = _intake_from_scenario(scenario)
    llm = FakeStructuredLLM(
        claim=scenario["workflow"]["normalized_claim"],
        classification=scenario["workflow"]["claim_classification"],
    )
    result = await ClaimWorkflow(llm).run(intake)
    assert result.routing_decision == "emergency_escalation"
    # docs/INSURANCE_PACK_MAPPING.md #8 spells the urgency predicate as `r.routing == ...`.
    assert result.routing == result.routing_decision == "emergency_escalation"


@pytest.mark.asyncio
async def test_run_attaches_lapsed_policy_from_extracted_policy_number() -> None:
    """The auto scenario's claimant never calls lookup_policy; the workflow attaches it."""
    scenario = load_fixture("insurance_scenario_auto.json")
    intake = _intake_from_scenario(scenario)
    assert intake.policy_record is None
    llm = FakeStructuredLLM(
        claim=scenario["workflow"]["normalized_claim"],
        classification=scenario["workflow"]["claim_classification"],
    )
    await ClaimWorkflow(llm).run(intake)
    assert intake.policy_record is not None
    assert intake.policy_record["found"] is True
    assert intake.policy_record["status"] == "lapsed"
    assert intake.policy_record["policyholder_name"] == "Chris Park"


@pytest.mark.asyncio
async def test_attach_policy_from_claim_does_not_override_an_already_found_record() -> None:
    intake = IntakeState(policy_record={"found": True, "policy_number": "H0-44721", "status": "active"})
    workflow = build_initial_workflow_state()
    workflow.normalized_claim["policy_number"] = "AUTO-90210"
    attach_policy_from_claim(intake, workflow)
    assert intake.policy_record is not None
    assert intake.policy_record["policy_number"] == "H0-44721"


@pytest.mark.asyncio
async def test_attach_policy_from_claim_skips_blank_policy_numbers() -> None:
    intake = IntakeState()
    workflow = build_initial_workflow_state()  # policy_number == "not specified"
    attach_policy_from_claim(intake, workflow)
    assert intake.policy_record is None


@pytest.mark.asyncio
async def test_run_empty_intake_text_returns_initial_workflow_without_calling_the_llm() -> None:
    intake = IntakeState()
    llm = FakeStructuredLLM(claim=blank_claim(), classification=initial_classification())
    result = await ClaimWorkflow(llm).run(intake)
    assert result.model_dump() == build_initial_workflow_state().model_dump()
    assert llm.calls == []


@pytest.mark.asyncio
async def test_run_caches_unchanged_intake_text_and_skips_the_llm() -> None:
    scenario = load_fixture("insurance_scenario_flood.json")
    intake = _intake_from_scenario(scenario)
    llm = FakeStructuredLLM(
        claim=scenario["workflow"]["normalized_claim"],
        classification=scenario["workflow"]["claim_classification"],
    )
    workflow = ClaimWorkflow(llm)

    first = await workflow.run(intake)
    assert len(llm.calls) == 2

    second = await workflow.run(intake)
    assert len(llm.calls) == 2  # no new calls: the cached result was reused
    assert second.model_dump() == first.model_dump()


@pytest.mark.asyncio
async def test_run_recomputes_when_intake_text_changes() -> None:
    scenario = load_fixture("insurance_scenario_flood.json")
    intake = _intake_from_scenario(scenario)
    llm = FakeStructuredLLM(
        claim=scenario["workflow"]["normalized_claim"],
        classification=scenario["workflow"]["claim_classification"],
    )
    workflow = ClaimWorkflow(llm)
    await workflow.run(intake)
    assert len(llm.calls) == 2

    intake.participant_turns.append("One more thing: the water heater also leaked.")
    await workflow.run(intake)
    assert len(llm.calls) == 4


# ---------------------------------------------------------------------------
# voice_summary (was live_tools.summarize_workflow_for_voice)
# ---------------------------------------------------------------------------


def test_voice_summary_flags_safety_escalation_and_trims_open_documents() -> None:
    scenario = load_fixture("insurance_scenario_auto.json")
    result = WorkflowResult.model_validate(scenario["workflow"])
    summary = voice_summary(result)
    assert summary["safety_escalation"] is True
    assert summary["routing_decision"] == "emergency_escalation"
    assert len(summary["open_documents"]) <= 3
    assert summary["guardrail"] == "Do not confirm coverage, payment, or liability."


def test_voice_summary_no_escalation_for_ready_route() -> None:
    result = build_initial_workflow_state()
    summary = voice_summary(result)
    assert summary["safety_escalation"] is False
