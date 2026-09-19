"""The claim intake workflow: two prompt-for-JSON LLM calls plus the verbatim rules.

Replaces the ADK ``SequentialAgent`` graph in the original ``agent.py`` (commit
``c4472b0``) with plain Python, per docs/ARCHITECTURE.md §10.1 and
docs/INSURANCE_PACK_MAPPING.md #13: ``StructuredLLM.extract(ClaimNarrative)``
-> ``rules.validate_required_claim_fields`` -> ``StructuredLLM.extract(ClaimClassification)``
-> ``rules.apply_coverage_and_evidence_rules`` -> ``rules.generate_document_checklist``
-> ``rules.fraud_signal_and_safety_gate`` -> ``rules.build_claim_intake_packet``.

Deviation from the original ADK graph (documented, no behaviour change to the
deterministic rules): ADK's ``LlmAgent`` fills ``{normalized_claim}``/
``{field_validation}`` in the classifier's *instruction* from live session
state while re-using the same triggering user message as its *input*. LKAP's
:class:`~packs.base.StructuredLLM` is a single-shot ``(instructions,
input_text) -> schema`` call with no session state, so
:func:`prompts.classifier_prompt` inlines the normalized claim and validation
into the instructions string and the classifier call still receives the
original claimant transcript as ``input_text`` for conversational context.

Everything below is pure Python: no ``livekit`` import at module load time
(``StructuredLLM`` is imported only for type checking), so this module is
unit-testable with a fake LLM and no network, per the work package rules.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from packs.insurance_claim.policy_directory import lookup_policy
from packs.insurance_claim.prompts import (
    NORMALIZER_INSTRUCTION,
    classifier_prompt,
    normalizer_prompt,
)
from packs.insurance_claim.rules import (
    apply_coverage_and_evidence_rules,
    build_claim_intake_packet,
    fraud_signal_and_safety_gate,
    generate_document_checklist,
    validate_required_claim_fields,
)
from packs.insurance_claim.schemas import ClaimClassification, ClaimNarrative, IntakeState

if TYPE_CHECKING:
    from packs.base import StructuredLLM

__all__ = [
    "ClaimWorkflow",
    "WorkflowResult",
    "attach_policy_from_claim",
    "blank_claim",
    "build_initial_workflow_state",
    "initial_classification",
    "voice_summary",
]


def blank_claim() -> dict[str, Any]:
    """A ``ClaimNarrative`` with nothing extracted yet. Ported from ``agent.py``."""
    return {
        "policyholder_name": "not specified",
        "policy_number": "not specified",
        "contact_method": "not specified",
        "date_of_loss": "not specified",
        "reported_date": "not specified",
        "loss_location": "not specified",
        "loss_description": "not specified",
        "estimated_loss_usd": None,
        "injuries_or_safety_concerns": [],
        "parties_involved": [],
        "evidence_available": [],
        "documents_mentioned": [],
        "missing_or_uncertain_facts": [],
        "raw_narrative_summary": "not specified",
        "assumptions": [],
    }


def initial_classification() -> dict[str, Any]:
    """The classification used before the claimant has said anything. Ported from ``agent.py``."""
    return {
        "claim_type": "other",
        "severity": "medium",
        "severity_rationale": "Waiting for claimant facts.",
        "likely_policy_line": "unknown",
        "loss_drivers": [],
        "claimant_needs": ["Provide initial loss facts."],
    }


class WorkflowResult(BaseModel):
    """Everything the original ADK graph left in ``session.state``, plus ``final_markdown``.

    Every field is the ``model_dump(exclude_none=True)`` dict of the
    corresponding :mod:`packs.insurance_claim.schemas` model, matching the
    shape of the golden fixtures (``insurance_initial_workflow.json``,
    ``insurance_scenario_*.json["workflow"]``) exactly.
    """

    normalized_claim: dict[str, Any]
    field_validation: dict[str, Any]
    claim_classification: dict[str, Any]
    coverage_evidence_decision: dict[str, Any]
    document_checklist: dict[str, Any]
    fraud_safety_gate: dict[str, Any]
    claim_intake_packet: dict[str, Any]
    final_markdown: str

    @property
    def routing_decision(self) -> str:
        """The safety gate's final routing decision (was ``workflow["fraud_safety_gate"][...]``)."""
        return str(self.fraud_safety_gate["final_routing_decision"])

    @property
    def routing(self) -> str:
        """Alias of :attr:`routing_decision`, matching the predicate spelled out in
        docs/INSURANCE_PACK_MAPPING.md #8: ``urgent=lambda r: r.routing == "emergency_escalation"``.
        """
        return self.routing_decision


def build_initial_workflow_state() -> WorkflowResult:
    """The workflow result for an empty transcript. Ported from ``agent.py``."""
    claim = blank_claim()
    classification = initial_classification()
    validation = validate_required_claim_fields(claim)
    coverage = apply_coverage_and_evidence_rules(claim, validation, classification)
    checklist = generate_document_checklist(claim, classification, coverage)
    fraud_gate = fraud_signal_and_safety_gate(claim, validation, classification, coverage)
    packet = build_claim_intake_packet(claim, validation, classification, coverage, checklist, fraud_gate)
    return WorkflowResult(
        normalized_claim=claim,
        field_validation=validation,
        claim_classification=classification,
        coverage_evidence_decision=coverage,
        document_checklist=checklist,
        fraud_safety_gate=fraud_gate,
        claim_intake_packet=packet,
        final_markdown=str(packet["markdown"]),
    )


def attach_policy_from_claim(intake: IntakeState, workflow: WorkflowResult) -> None:
    """Verify an extracted policy number if the voice agent has not called ``lookup_policy`` yet.

    Ported from ``agent.py``'s (via ``server.py``) ``_attach_policy_from_claim``.
    """
    if intake.policy_record and intake.policy_record.get("found"):
        return
    number = str(workflow.normalized_claim.get("policy_number", "")).strip()
    if not number or number.lower() in {"not specified", "unknown"}:
        return
    record = lookup_policy(number)
    if record.get("found") or intake.policy_record is None:
        intake.policy_record = record


def voice_summary(workflow: WorkflowResult) -> dict[str, Any]:
    """Compact the workflow result into what the voice agent needs to hear.

    Ported verbatim from ``live_demo/live_tools.py``'s ``summarize_workflow_for_voice``.
    """
    packet = workflow.claim_intake_packet
    validation = workflow.field_validation
    fraud_gate = workflow.fraud_safety_gate
    classification = workflow.claim_classification
    checklist = workflow.document_checklist
    route = fraud_gate["final_routing_decision"]
    outstanding_docs = [
        item["item"] for item in checklist.get("items", []) if not item.get("already_provided")
    ]
    return {
        "routing_decision": route,
        "safety_escalation": route == "emergency_escalation",
        "claim_type": classification["claim_type"],
        "severity": classification["severity"],
        "open_items": validation.get("missing_fields", []),
        "open_documents": outstanding_docs[:3],
        "suggested_question_when_topic_is_closed": packet["claimant_next_message"],
        "how_to_use": (
            "Open items are a checklist for the packet. Finish the current topic first, then "
            "raise the item that fits the conversation. Do not read the list out."
        ),
        "handoff_summary": packet["adjuster_handoff_summary"],
        "guardrail": "Do not confirm coverage, payment, or liability.",
    }


def _cache_key(text: str) -> str:
    """A short, stable cache key for an intake text (avoids storing the raw text twice)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ClaimWorkflow:
    """The claim intake pipeline: two LLM extraction calls plus the deterministic rules.

    Args:
        structured_llm: The session's :class:`~packs.base.StructuredLLM`
            (``ctx.workflow_llm``); one JSON-extraction call per schema, with
            its own retry/timeout handling.
    """

    def __init__(self, structured_llm: StructuredLLM) -> None:
        self._llm = structured_llm

    async def run(self, intake: IntakeState) -> WorkflowResult:
        """Run (or reuse) the workflow for the current claimant transcript snapshot.

        Ported from ``server.py``'s ``_run_workflow_cached``: reuses the last
        result when the intake text (claimant transcript + camera notes) is
        unchanged, and only then attaches a policy record verified from the
        extracted policy number.
        """
        text = intake.intake_text()
        cache_key = _cache_key(text)
        if intake.last_workflow is not None and intake.last_workflow_key == cache_key:
            return WorkflowResult.model_validate(intake.last_workflow)

        result = build_initial_workflow_state() if not text else await self._extract_and_apply_rules(text)

        intake.last_workflow_key = cache_key
        intake.last_workflow = result.model_dump()
        attach_policy_from_claim(intake, result)
        return result

    async def _extract_and_apply_rules(self, text: str) -> WorkflowResult:
        raw_claim = await self._llm.extract(
            instructions=NORMALIZER_INSTRUCTION,
            input_text=normalizer_prompt(text),
            schema=ClaimNarrative,
        )
        claim = (
            raw_claim if isinstance(raw_claim, ClaimNarrative) else ClaimNarrative.model_validate(raw_claim)
        )
        claim_dict = claim.model_dump(exclude_none=True)

        validation = validate_required_claim_fields(claim_dict)

        raw_classification = await self._llm.extract(
            instructions=classifier_prompt(claim_dict, validation),
            input_text=text,
            schema=ClaimClassification,
        )
        classification = (
            raw_classification
            if isinstance(raw_classification, ClaimClassification)
            else ClaimClassification.model_validate(raw_classification)
        )
        classification_dict = classification.model_dump(exclude_none=True)

        coverage = apply_coverage_and_evidence_rules(claim_dict, validation, classification_dict)
        checklist = generate_document_checklist(claim_dict, classification_dict, coverage)
        fraud_gate = fraud_signal_and_safety_gate(claim_dict, validation, classification_dict, coverage)
        packet = build_claim_intake_packet(
            claim_dict, validation, classification_dict, coverage, checklist, fraud_gate
        )
        return WorkflowResult(
            normalized_claim=claim_dict,
            field_validation=validation,
            claim_classification=classification_dict,
            coverage_evidence_decision=coverage,
            document_checklist=checklist,
            fraud_safety_gate=fraud_gate,
            claim_intake_packet=packet,
            final_markdown=str(packet["markdown"]),
        )
