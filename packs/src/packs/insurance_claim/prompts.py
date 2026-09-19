"""Extraction, classification, and sketch prompts for the insurance claim workflow.

``NORMALIZER_INSTRUCTION`` and ``CLASSIFIER_INSTRUCTION`` are ported verbatim
from the ADK ``LlmAgent`` instructions in the original ``agent.py`` (commit
``c4472b0``); ``WORKFLOW_USER_PREFIX`` is the message text the original
``run_claim_workflow`` prepended to the claimant transcript; ``sketch_prompt``
is ported verbatim from ``live_demo/live_tools.py``.
"""

from __future__ import annotations

NORMALIZER_INSTRUCTION = (
    """
You are the intake specialist for an AI Insurance Claim Intake Agent.

Read the user's messy insurance claim narrative and produce a structured
ClaimNarrative. Preserve facts exactly when possible. Do not invent policy
numbers, contacts, dates, locations, evidence, or dollar amounts.

Extraction rules:
- policyholder_name: claimant or policyholder name, otherwise "not specified".
- policy_number: policy/member number, otherwise "not specified".
- contact_method: phone, email, mailing address, or preferred channel, otherwise "not specified".
- date_of_loss: date or date range of the loss, otherwise "not specified".
- reported_date: date the user says they are reporting the claim, otherwise "not specified".
- loss_location: address, city, intersection, provider, or travel route, otherwise "not specified".
- loss_description: concise factual description of what happened.
- estimated_loss_usd: numeric USD estimate only if supplied.
"""
    "- injuries_or_safety_concerns: include injuries, urgent medical care, unsafe housing, electrical "
    "hazards, sewage, mold, or no place to live.\n"
    "- evidence_available: photos, video, receipts, report numbers, estimates, bills, carrier notices, "
    "EOBs, proof of payment, serial numbers, or similar evidence already mentioned.\n"
    """- documents_mentioned: specific documents mentioned whether available or missing.
- missing_or_uncertain_facts: key facts the narrative says are unknown, vague, or incomplete.

This is an intake normalization step only. Do not confirm coverage or payment.
"""
)

CLASSIFIER_INSTRUCTION = """
Classify this normalized claim for insurance intake routing.

Normalized claim:
{normalized_claim}

Validation:
{field_validation}

Supported claim types:
- home_water_damage
- auto_collision
- theft_property_loss
- health_medical_reimbursement
- travel_delay_cancellation
- other

Severity rubric:
- low: complete, low-dollar, no injury/safety issue, routine documentation.
- medium: missing documents or moderate complexity.
- high: high estimated loss, unclear liability, missing core facts, or specialized handling likely.
- urgent: injury, unsafe living condition, emergency medical/safety concern, or time-sensitive mitigation.

Return only the structured ClaimClassification. This is classification, not a
coverage decision.
"""

#: Prepended to the claimant transcript before the normalizer call, verbatim
#: from ``agent.py``'s ``run_claim_workflow``.
WORKFLOW_USER_PREFIX = (
    "Use this full claimant transcript as the source of truth for "
    "the insurance intake workflow. Do not invent missing facts.\n\n"
)

#: The adjuster's pen style, shared by every sketch so the notebook stays consistent.
SKETCH_STYLE_PREFIX = (
    "A quick hand-drawn pen sketch on cream notebook paper, the kind an insurance "
    "adjuster draws in a field notebook. Black ink line drawing, loose confident "
    "strokes, small handwritten labels in the same ink, a light blue wash only where "
    "water is present and a light red wash only where impact damage is. Top-down or "
    "simple perspective, no photorealism, no shading gradients, no people. Write no names, "
    "dates, addresses, or claim numbers anywhere; the only text is short labels for the "
    "objects in the scene. Scene to draw: "
)


def normalizer_prompt(claimant_transcript: str) -> str:
    """The user message sent to the normalizer LLM call, was ``run_claim_workflow``'s message."""
    return f"{WORKFLOW_USER_PREFIX}{claimant_transcript}"


def classifier_prompt(normalized_claim: dict[str, object], field_validation: dict[str, object]) -> str:
    """Fill the classifier instruction's ``{normalized_claim}``/``{field_validation}`` slots.

    Uses ``str.replace`` rather than ``str.format`` because the surrounding
    instruction text contains literal ``{``/``}`` free-form JSON-ish content
    that must not be treated as format fields.
    """
    return CLASSIFIER_INSTRUCTION.replace("{normalized_claim}", str(normalized_claim)).replace(
        "{field_validation}", str(field_validation)
    )


def sketch_prompt(scene_description: str) -> str:
    """Prompt for the image model so every sketch looks like the same adjuster's pen.

    Ported verbatim from ``live_demo/live_tools.py``.
    """
    return f"{SKETCH_STYLE_PREFIX}{scene_description.strip()}"


__all__ = [
    "CLASSIFIER_INSTRUCTION",
    "NORMALIZER_INSTRUCTION",
    "SKETCH_STYLE_PREFIX",
    "WORKFLOW_USER_PREFIX",
    "classifier_prompt",
    "normalizer_prompt",
    "sketch_prompt",
]
