"""Compose the LKAP ``UiState`` envelope from a claim workflow result.

Ported from ``live_demo/server.py``'s ``_ui_state``/``_policy_fields``/``_events``
and ``web/js/app.js``'s ``buildNotes`` (commit ``c4472b0``), retargeted from the
old flat "notebook state" dict onto the platform envelope
(``lkap_contracts.ui_protocol.UiState``) per docs/INSURANCE_PACK_MAPPING.md #16:

- ``status``      <- the route stamp (``ROUTE_LABELS``/``ROUTE_TONES``)
- ``progress``    <- the 0-100 completeness percentage
- ``notes``       <- the composed handwritten notes (was ``app.js#buildNotes``)
- ``checklist``   <- missing blockers + the document checklist (platform shape)
- ``custom``      <- :class:`InsuranceState`: ``fields``, ``route``,
  ``missing_blockers``, ``documents`` (full fidelity, incl. ``reason``/
  ``priority``), ``handoff``, ``packet_markdown``, ``events``, ``policy``,
  ``camera_notes``, ``sketch``, ``severity``, ``claim_type``

``build_ui_state`` never touches ``assets``/``activity``: those are owned by
the live ``UiChannel`` (``push_asset``/``activity``), not recomputed here. See
the "Notes for W2" section of the work package report for how callers should
merge this envelope in.
"""

from __future__ import annotations

import re
from typing import Any, Literal, cast

from lkap_contracts.ui_protocol import ChecklistItem, Note, StatusStamp, Tone, UiState
from pydantic import BaseModel

from packs.insurance_claim.policy_directory import policy_status_headline
from packs.insurance_claim.schemas import (
    ClaimClassification,
    ClaimNarrative,
    IntakeState,
    RoutingDecision,
    Severity,
    SketchState,
)
from packs.insurance_claim.workflow import WorkflowResult

__all__ = [
    "DocumentEntry",
    "EventEntry",
    "FieldEntry",
    "InsuranceState",
    "build_ui_state_for_intake",
    "ROUTE_LABELS",
    "ROUTE_TONES",
    "build_ui_state",
    "camera_note",
    "compose_notes",
]

FieldStatus = Literal["missing", "complete", "urgent"]

#: Order of the notebook fields, mirrored 1:1 from ``server.py``'s ``_ui_state`` (19 fields).
FIELD_LABELS: dict[str, str] = {
    "claimant": "Claimant name",
    "policy": "Policy number",
    "contact": "Contact method",
    "type": "Claim type",
    "date": "Date of loss",
    "time": "Reported date",
    "location": "Location",
    "description": "Loss description",
    "injuries": "Injuries",
    "hazards": "Hazards present",
    "medical": "Medical attention",
    "police": "Report number",
    "photos": "Evidence available",
    "tow": "Tow info",
    "otherDriver": "Other driver info",
    "policyStatus": "Policy status",
    "policyLine": "Policy line",
    "deductible": "Deductibles",
    "coverages": "Coverages on file",
}

#: The route stamp's label and tone, was the notebook spec's ``RouteSpec`` table.
ROUTE_LABELS: dict[str, str] = {
    "needs_docs": "Needs docs",
    "ready_for_adjuster": "Ready for adjuster",
    "special_investigation": "SIU review",
    "emergency_escalation": "Escalate to human",
}
ROUTE_TONES: dict[str, Tone] = {
    "needs_docs": "warning",
    "ready_for_adjuster": "success",
    "special_investigation": "info",
    "emergency_escalation": "danger",
}

#: Short claimant-facing questions for notebook blanks, was ``app.js#blockerQuestions``.
#: Distinct from ``rules.BLOCKING_FIELD_QUESTIONS`` (the longer voice phrasing).
BLOCKER_QUESTIONS: dict[str, str] = {
    "policyholder_name": "Name?",
    "policy_number": "Policy number?",
    "contact_method": "Best contact?",
    "date_of_loss": "When did it happen?",
    "loss_location": "Where?",
    "loss_description": "What happened?",
}

_BLANK_VALUES = {"", "unknown", "not specified", "unspecified", "n/a", "none", "not provided"}
_FILLED = re.compile(r"^(missing:|not captured|not specified|unknown|none)", re.IGNORECASE)

SOURCE = "Gemini extraction"
POLICY_SOURCE = "Policy directory lookup"


class FieldEntry(BaseModel):
    """One notebook field: value, completeness status, and where it came from."""

    label: str
    value: str
    status: FieldStatus
    source: str


class DocumentEntry(BaseModel):
    """A document checklist entry with full fidelity (reason, priority)."""

    item: str
    reason: str
    priority: Literal["required", "recommended", "conditional"]
    already_provided: bool = False


class EventEntry(BaseModel):
    """One audit-log style event, was the old ``events`` list entries."""

    tone: Tone
    title: str
    detail: str
    rule: str


class InsuranceState(BaseModel):
    """The pack's ``UiState.custom`` shape; ``manifest.state_schema`` is this model's JSON Schema."""

    fields: dict[str, FieldEntry]
    route: RoutingDecision
    missing_blockers: list[str] = []
    documents: list[DocumentEntry] = []
    handoff: dict[str, str]
    packet_markdown: str
    events: list[EventEntry] = []
    policy: dict[str, Any] | None = None
    camera_notes: list[str] = []
    sketch: SketchState | None = None
    severity: Severity
    claim_type: str


def camera_note(observation: str, claimant_description: str, confirmed: bool) -> str:
    """The camera-note sentence appended to ``IntakeState.camera_notes`` by ``pin_evidence_photo``.

    Ported verbatim from ``server.py``'s ``_pin_evidence_photo``, exposed here
    so W2-PACK-INSURANCE-TOOLS's tool implementation produces byte-identical text.
    """
    note = f"Agent saw on camera: {observation}"
    if claimant_description:
        note += f" Claimant described it as: {claimant_description}."
        note += " Confirmed on camera." if confirmed else " Not confirmed on camera; needs a clearer photo."
    return note


def _status_of(value: Any, *, urgent: bool = False) -> FieldStatus:
    if urgent:
        return "urgent"
    text = str(value or "").strip().lower()
    if text in _BLANK_VALUES:
        return "missing"
    return "complete"


def _field(label: str, value: Any, *, source: str = SOURCE, urgent: bool = False) -> FieldEntry:
    status = _status_of(value, urgent=urgent)
    display = value if status != "missing" else f"Missing: {label.lower()}"
    return FieldEntry(
        label=label, value=str(display), status=status, source="-" if status == "missing" else source
    )


def _join(items: list[str], fallback: str) -> str:
    return ", ".join(items) if items else fallback


def _items_containing(items: list[str], needles: list[str], fallback: str = "Unknown") -> str:
    matches = [item for item in items if any(needle in item.lower() for needle in needles)]
    return _join(matches, fallback)


def _find_text(claim: ClaimNarrative, needles: list[str]) -> str:
    text = " | ".join(
        [
            claim.loss_description,
            *claim.evidence_available,
            *claim.documents_mentioned,
            *claim.parties_involved,
        ]
    )
    lower = text.lower()
    if any(needle in lower for needle in needles):
        return text
    return "not specified"


def _find_report(claim: ClaimNarrative) -> str:
    text = " | ".join([*claim.evidence_available, *claim.documents_mentioned, claim.loss_description])
    lower = text.lower()
    if any(term in lower for term in ["police", "report", "case number", "incident"]):
        return text
    return "not specified"


def _without_negated_safety_mentions(text: str) -> str:
    cleaned = str(text or "")
    for pattern in [
        r"\b(?:no|not|none|without|denies|denied)\s+(?:one\s+)?(?:was\s+)?"
        r"(?:injur\w*|hurt|pain|medical attention|ambulance|hospital|unsafe|hazard\w*|danger)\b",
        r"\b(?:injur\w*|hurt|pain|medical attention|ambulance|hospital|unsafe|hazard\w*|danger)"
        r"\s+(?:was|were|is|are)?\s*(?:reported\s+)?(?:no|none|not reported|denied)\b",
    ]:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    return cleaned


def _has_negated_safety_mention(text: str) -> bool:
    return _without_negated_safety_mentions(text) != str(text or "")


def _positive_safety_items(items: list[str]) -> list[str]:
    patterns = [
        r"\binjur",
        r"\bhurt\b",
        r"\bneck pain\b",
        r"\bhospital\b",
        r"\burgent care\b",
        r"\bambulance\b",
        r"\bunsafe\b",
        r"\bhazard",
        r"\bdanger\b",
    ]
    return [
        item
        for item in items
        if any(
            re.search(pattern, _without_negated_safety_mentions(item), flags=re.IGNORECASE)
            for pattern in patterns
        )
    ]


def _policy_fields(record: dict[str, Any] | None) -> dict[str, FieldEntry]:
    """Policy verification rows sourced from the background lookup, was ``_policy_fields``."""
    if not record:
        return {
            "policyStatus": _field("Policy status", ""),
            "policyLine": _field("Policy line", ""),
            "deductible": _field("Deductibles", ""),
            "coverages": _field("Coverages on file", ""),
        }
    if not record.get("found"):
        return {
            "policyStatus": _field(
                "Policy status", "Not found - confirm number", source=POLICY_SOURCE, urgent=True
            ),
            "policyLine": _field("Policy line", ""),
            "deductible": _field("Deductibles", ""),
            "coverages": _field("Coverages on file", ""),
        }
    deductibles = ", ".join(
        f"{name.replace('_', ' ')} ${int(amount):,}" for name, amount in record.get("deductibles", {}).items()
    )
    return {
        "policyStatus": _field(
            "Policy status",
            policy_status_headline(dict(record)),
            source=POLICY_SOURCE,
            urgent=str(record.get("status")) != "active",
        ),
        "policyLine": _field("Policy line", record.get("policy_line", ""), source=POLICY_SOURCE),
        "deductible": _field("Deductibles", deductibles or "None listed", source=POLICY_SOURCE),
        "coverages": _field(
            "Coverages on file", "; ".join(record.get("coverages", [])), source=POLICY_SOURCE
        ),
    }


def _build_fields(
    claim: ClaimNarrative,
    classification: ClaimClassification,
    policy_record: dict[str, Any] | None,
    participant_text: str,
) -> dict[str, FieldEntry]:
    """The 19 notebook fields, was ``_ui_state``'s ``fields`` dict."""
    positive_safety = _positive_safety_items(claim.injuries_or_safety_concerns)
    safety_text = " ".join(
        [
            claim.loss_description,
            claim.raw_narrative_summary,
            participant_text,
            *claim.injuries_or_safety_concerns,
        ]
    )
    injury_text = _join(claim.injuries_or_safety_concerns, "Unknown")
    if not positive_safety and _has_negated_safety_mention(safety_text):
        injury_text = "No injuries reported"
    evidence_text = _join(claim.evidence_available, "Not captured yet")

    return {
        "claimant": _field("Claimant name", claim.policyholder_name),
        "policy": _field("Policy number", claim.policy_number),
        "contact": _field("Contact method", claim.contact_method),
        "type": _field("Claim type", classification.claim_type.replace("_", " ")),
        "date": _field("Date of loss", claim.date_of_loss),
        "time": _field("Reported date", claim.reported_date),
        "location": _field("Location", claim.loss_location),
        "description": _field("Loss description", claim.loss_description),
        "injuries": _field(
            "Injuries", injury_text, source="Gemini extraction + safety gate", urgent=bool(positive_safety)
        ),
        "hazards": _field(
            "Hazards present", _items_containing(claim.injuries_or_safety_concerns, ["hazard", "unsafe"])
        ),
        "medical": _field(
            "Medical attention",
            _items_containing(claim.injuries_or_safety_concerns, ["medical", "care", "hospital"]),
        ),
        "police": _field("Report number", _find_report(claim)),
        "photos": _field("Evidence available", evidence_text),
        "tow": _field("Tow info", _find_text(claim, ["tow", "storage"])),
        "otherDriver": _field(
            "Other driver info", _find_text(claim, ["other driver", "driver", "plate", "witness"])
        ),
        **_policy_fields(policy_record),
    }


def _events(
    model_label: str,
    validation: dict[str, Any],
    coverage: dict[str, Any],
    fraud_gate: dict[str, Any],
    previous_route: str | None,
) -> list[EventEntry]:
    """Was ``_events`` in ``server.py``."""
    events: list[EventEntry] = [
        EventEntry(
            tone="success",
            title="Gemini extraction complete",
            detail=f"Updated structured claim facts using {model_label}.",
            rule="LLM-001",
        )
    ]
    if validation.get("missing_fields"):
        events.append(
            EventEntry(
                tone="warning",
                title="Missing intake facts",
                detail=", ".join(validation["missing_fields"]),
                rule="INTAKE-001",
            )
        )
    for finding in coverage.get("findings", []):
        tone: Tone = "danger" if finding["required_action"] == "emergency_escalation" else "warning"
        if finding["required_action"] == "adjuster_review":
            tone = "success"
        events.append(
            EventEntry(
                tone=tone,
                title=finding["message"],
                detail=f"Required action: {finding['required_action']}.",
                rule=finding["rule_id"],
            )
        )
    for signal in fraud_gate.get("signals", []):
        events.append(
            EventEntry(
                tone="danger" if signal.get("route_to_emergency") else "warning",
                title=signal["message"],
                detail="Deterministic fraud/safety gate signal.",
                rule=signal["signal_id"],
            )
        )
    route = fraud_gate.get("final_routing_decision", coverage.get("routing_decision"))
    previous = previous_route or "needs_docs"
    if route != previous:
        events.append(
            EventEntry(
                tone="danger" if route == "emergency_escalation" else "success",
                title="Routing changed",
                detail=f"{previous} -> {route}.",
                rule="ROUTE-001",
            )
        )
    return events


def is_filled(field: FieldEntry | None) -> bool:
    """``app.js#isFilled``: a field counts as written down unless missing or a placeholder."""
    if field is None or field.status == "missing":
        return False
    return not _FILLED.match(field.value.strip())


def short_blocker(blocker: str) -> str:
    """``app.js#shortBlocker``: the known question, or the blocker text trimmed to a short question."""
    if blocker in BLOCKER_QUESTIONS:
        return BLOCKER_QUESTIONS[blocker]
    text = re.sub(r"\s*\([^)]*\)", "", str(blocker)).strip()
    if len(text) > 42:
        text = f"{text[:40].strip()}..."
    return text if text.endswith("?") else f"{text}?"


def compose_notes(
    fields: dict[str, FieldEntry],
    record: dict[str, Any] | None,
    blockers: list[str],
    *,
    participant_has_spoken: bool,
    now: float,
) -> list[Note]:
    """Compose the handwriting on the page. Ported line-for-line from ``app.js#buildNotes``.

    ``id``/``ts`` are new (the old wire format had neither): ``id`` is derived
    from ``key`` so it is stable across rebuilds, and ``ts`` is the caller's
    ``now`` so tests stay deterministic.
    """
    notes: list[Note] = []

    def value_of(field_id: str) -> str:
        field = fields.get(field_id)
        return field.value if field is not None and is_filled(field) else ""

    def add(key: str, text: str, *, kind: str = "note", urgent: bool = False) -> None:
        notes.append(
            Note(
                id=f"note:{key}",
                text=text,
                kind=kind,
                tone="danger" if urgent else "neutral",
                ts=now,
                key=key,
            )
        )

    name = value_of("claimant")
    policy = value_of("policy")
    if name or policy:
        add(f"title:{name}|{policy}", "  ·  ".join(part for part in (name, policy) if part), kind="title")

    if record:
        if record.get("found"):
            active = record.get("status") == "active"
            extras = "".join(list(record.get("coverages", []))[:1])
            text = (
                f"{record.get('policy_line')}, active. {extras}"
                if active
                else (
                    f"{record.get('policy_line')}, {record.get('status')}. Human review before anything else."
                )
            )
            add(
                f"policy:{record.get('policy_number')}:{record.get('status')}",
                text,
                kind="check" if active else "flag",
                urgent=not active,
            )
        else:
            number = record.get("policy_number") or ""
            add(
                f"policy:notfound:{number}",
                f"Policy {number} not found, confirm the number.",
                kind="flag",
                urgent=True,
            )

    when = value_of("date")
    where = value_of("location")
    if when or where:
        add(f"whenwhere:{when}|{where}", ", ".join(part for part in (where, when) if part))

    if value_of("description"):
        add(f"desc:{value_of('description')}", value_of("description"))

    if value_of("injuries"):
        injuries = fields["injuries"]
        urgent = injuries.status == "urgent"
        add(
            f"inj:{injuries.value}",
            f"Injury: {injuries.value}" if urgent else injuries.value,
            kind="flag" if urgent else "note",
            urgent=urgent,
        )

    if value_of("contact"):
        add(f"contact:{value_of('contact')}", f"Reach at {value_of('contact')}", kind="aside")

    if value_of("photos"):
        add(f"ev:{value_of('photos')}", f"Has: {value_of('photos')}", kind="aside")

    if value_of("police"):
        add(f"rep:{value_of('police')}", f"Report: {value_of('police')}", kind="aside")

    if participant_has_spoken:
        seen: set[str] = set()
        for blocker in blockers:
            question = short_blocker(blocker)
            dedupe = re.sub(r"[^a-z]", "", question.lower())
            if dedupe in seen or len(seen) >= 3:
                continue
            seen.add(dedupe)
            add(f"blank:{blocker}", question, kind="blank")

    if not notes:
        add("empty", "Waiting for the claimant. Tap Talk, show the camera, or type below.", kind="aside")

    return notes


def _slug(text: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text.lower())).strip("_")[:48]


def _build_checklist(blockers: list[str], documents: list[dict[str, Any]]) -> list[ChecklistItem]:
    """Blocking intake items first, then the document checklist. Platform-shape view.

    Full document fidelity (``reason``, ``priority``, ``already_provided``)
    stays in ``InsuranceState.documents``; this is the simplified view the
    generic panel and ``StillNeeded.tsx`` render from.
    """
    items = [
        ChecklistItem(id=f"blocker:{blocker}", label=short_blocker(blocker).rstrip("?"), blocking=True)
        for blocker in blockers
    ]
    items.extend(
        ChecklistItem(
            id=f"doc:{_slug(str(document['item']))}",
            label=str(document["item"]),
            done=bool(document.get("already_provided")),
            blocking=document.get("priority") == "required",
            hint=str(document.get("reason", "")),
        )
        for document in documents
    )
    return items


def build_ui_state(
    intake_text_participant: str,
    workflow: WorkflowResult,
    *,
    policy_record: dict[str, Any] | None,
    camera_notes: list[str],
    sketch: SketchState | None,
    previous_route: str | None,
    now: float,
    model_label: str,
) -> UiState:
    """Compose the full ``UiState`` envelope for a workflow result.

    Ported from ``server.py``'s ``_state_from_workflow``/``_ui_state``. Callers
    (W2-PACK-INSURANCE-TOOLS) own ``PackSessionContext.userdata["intake"]``
    (:class:`~packs.insurance_claim.schemas.IntakeState`) and pass its pieces
    in explicitly so this function stays a pure, easily-tested transform.

    Args:
        intake_text_participant: The claimant-only transcript text (was
            ``session.participant_text()``), used for the safety-negation check.
        workflow: The :class:`~packs.insurance_claim.workflow.WorkflowResult`
            to render.
        policy_record: ``intake.policy_record`` (or ``None``).
        camera_notes: ``intake.camera_notes``.
        sketch: ``intake.sketch``.
        previous_route: The route stamped on the *previous* envelope (so the
            "Routing changed" event only fires when it actually moves);
            ``None`` is treated as ``"needs_docs"``, matching the original's
            default session route.
        now: Injected timestamp for ``Note.ts`` (deterministic in tests).
        model_label: The workflow LLM's display name for the ``LLM-001`` event
            and ``InsuranceState`` (no hardcoded model name; the caller reads
            it from ``ResolvedProvider.model``).

    Returns:
        A ``UiState`` with ``assets`` and ``activity`` left empty — the caller
        must merge those in from the live ``UiChannel`` state, never overwrite
        them from this function's return value.
    """
    claim = ClaimNarrative.model_validate(workflow.normalized_claim)
    classification = ClaimClassification.model_validate(workflow.claim_classification)
    validation = workflow.field_validation
    coverage = workflow.coverage_evidence_decision
    checklist = workflow.document_checklist
    fraud_gate = workflow.fraud_safety_gate
    packet = workflow.claim_intake_packet
    route = str(fraud_gate["final_routing_decision"])

    fields = _build_fields(claim, classification, policy_record, intake_text_participant)
    completed = sum(1 for f in fields.values() if f.status in {"complete", "urgent"})
    progress = max(12, round(completed / len(fields) * 100))

    blockers = list(validation.get("missing_fields", []))
    documents = list(checklist.get("items", []))
    evidence_text = _join(claim.evidence_available, "Not captured yet")
    required_doc_names = [str(item["item"]) for item in documents]

    notes = compose_notes(
        fields,
        policy_record,
        blockers,
        participant_has_spoken=bool(intake_text_participant.strip()),
        now=now,
    )

    custom = InsuranceState(
        fields=fields,
        route=cast(RoutingDecision, route),
        missing_blockers=blockers,
        documents=[DocumentEntry.model_validate(d) for d in documents],
        handoff={
            "Summary": str(packet["adjuster_handoff_summary"]),
            "Priority": f"{classification.severity.title()} - {classification.severity_rationale}",
            "Required actions": _join(
                required_doc_names, "No additional documents identified by current rules."
            ),
            "Attachments": evidence_text,
            "Next best action": str(packet["claimant_next_message"]),
        },
        packet_markdown=str(packet["markdown"]),
        events=_events(model_label, validation, coverage, fraud_gate, previous_route),
        policy=policy_record,
        camera_notes=list(camera_notes),
        sketch=sketch,
        severity=classification.severity,
        claim_type=classification.claim_type.replace("_", " "),
    )

    return UiState(
        status=StatusStamp(
            label=ROUTE_LABELS.get(route, route), tone=ROUTE_TONES.get(route, "neutral"), key=route
        ),
        progress=progress,
        notes=notes,
        checklist=_build_checklist(blockers, documents),
        assets=[],
        activity=[],
        custom=custom.model_dump(mode="json"),
    )


def build_ui_state_for_intake(
    intake: IntakeState,
    workflow: WorkflowResult,
    *,
    now: float,
    model_label: str,
) -> UiState:
    """``build_ui_state``, unpacking an :class:`~packs.insurance_claim.schemas.IntakeState`.

    This is the ``build_ui_state(intake) -> UiState`` signature named in
    docs/IMPLEMENTATION_PLAN.md; call this one from W2-PACK-INSURANCE-TOOLS's
    session hooks. ``build_ui_state`` itself stays a pure function of plain
    values (no ``IntakeState`` dependency) because that is what keeps its
    golden-fixture tests in ``test_ui_state.py`` simple to construct.

    After rendering, write the returned envelope's route back onto
    ``intake.previous_route`` (``result.custom["route"]``) so the next call's
    "Routing changed" event only fires when the route actually moves — this
    function does not mutate ``intake`` itself.
    """
    return build_ui_state(
        intake.participant_text(),
        workflow,
        policy_record=intake.policy_record,
        camera_notes=intake.camera_notes,
        sketch=intake.sketch,
        previous_route=intake.previous_route,
        now=now,
        model_label=model_label,
    )
