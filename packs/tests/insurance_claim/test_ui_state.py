"""Parity tests for packs.insurance_claim.ui_state against the recorded baseline.

The fixtures were recorded from the baseline ``live_demo/server.py::_ui_state``
and the baseline ``app.js#buildNotes`` (``tests/fixtures/record_baseline.py``
and ``record_notes.mjs``, commit ``c4472b0``) before that code was deleted,
for three scenarios: a blank session, a basement flood with the policy found
and one pinned photo, and an auto collision with an injury and a lapsed
policy.

``to_legacy`` maps the new ``UiState`` envelope back onto the old flat
``_ui_state`` dict shape (docs/INSURANCE_PACK_MAPPING.md #16 table) so the
comparison is a direct parity check field-for-field, with an explicit,
documented exclusion list for the pieces the new architecture deliberately
moved elsewhere (``transcript``/``tool_activity``/``live_model`` are platform-
or W2-owned; ``evidence_photos``/``sketch`` become byte-stream assets, out of
this pure builder's scope; ``model`` is parameterized instead of hardcoded).
"""

from __future__ import annotations

from typing import Any

import pytest

from packs.insurance_claim.policy_directory import lookup_policy
from packs.insurance_claim.schemas import IntakeState
from packs.insurance_claim.ui_state import (
    ROUTE_LABELS,
    ROUTE_TONES,
    InsuranceState,
    build_ui_state,
    build_ui_state_for_intake,
    camera_note,
)
from packs.insurance_claim.workflow import WorkflowResult
from tests.insurance_claim.conftest import load_fixture

SCENARIOS = ["blank", "flood", "auto"]
NOW = 1_777_000_000.0
MODEL_LABEL = "gemini-3.8-flash"  # matches the fixtures' recorded MODEL env default


def _participant_text(scenario: dict[str, Any]) -> str:
    return "\n".join(turn["text"] for turn in scenario["transcript"] if turn["speaker"] == "Claimant")


def _camera_notes(scenario: dict[str, Any]) -> list[str]:
    pin_photo = scenario["pin_photo"]
    if not pin_photo:
        return []
    return [
        camera_note(
            pin_photo["observation"],
            pin_photo.get("claimant_description", ""),
            bool(pin_photo.get("confirmed", False)),
        )
    ]


def _policy_record(scenario: dict[str, Any]) -> dict[str, Any] | None:
    looked_up = scenario["policy_number_looked_up"]
    return lookup_policy(looked_up) if looked_up else None


def build(scenario_name: str) -> tuple[dict[str, Any], dict[str, Any], Any]:
    """Build the new UiState for a recorded scenario, next to the recorded baseline state."""
    scenario = load_fixture(f"insurance_scenario_{scenario_name}.json")
    baseline = load_fixture(f"insurance_ui_state_{scenario_name}.json")
    state = build_ui_state(
        _participant_text(scenario),
        WorkflowResult.model_validate(scenario["workflow"]),
        policy_record=_policy_record(scenario),
        camera_notes=_camera_notes(scenario),
        sketch=None,
        previous_route=None,
        now=NOW,
        model_label=MODEL_LABEL,
    )
    return scenario, baseline, state


def to_legacy(state: Any) -> dict[str, Any]:
    """Map the new UiState envelope back onto the baseline ``_ui_state`` shape."""
    custom = state.custom
    return {
        "route": custom["route"],
        "progress": state.progress,
        "fields": {
            key: {"label": f["label"], "value": f["value"], "status": f["status"], "source": f["source"]}
            for key, f in custom["fields"].items()
        },
        "events": [
            {"tone": e["tone"], "title": e["title"], "detail": e["detail"], "rule": e["rule"]}
            for e in custom["events"]
        ],
        "policy": custom["policy"],
        "missing_blockers": custom["missing_blockers"],
        "documents": custom["documents"],
        "camera_notes": custom["camera_notes"],
        "sketch": custom["sketch"],
        "severity": custom["severity"],
        "claim_type": custom["claim_type"],
        "handoff": custom["handoff"],
        "packet_markdown": custom["packet_markdown"],
    }


def test_route_labels_and_tones_cover_every_routing_decision() -> None:
    from packs.insurance_claim.schemas import RoutingDecision

    routes = set(RoutingDecision.__args__)  # type: ignore[attr-defined]
    assert routes == set(ROUTE_LABELS) == set(ROUTE_TONES)


@pytest.mark.parametrize("scenario_name", SCENARIOS)
def test_build_ui_state_matches_recorded_baseline(scenario_name: str) -> None:
    _scenario, baseline, state = build(scenario_name)
    legacy = to_legacy(state)
    assert legacy["route"] == baseline["route"]
    assert legacy["progress"] == baseline["progress"]
    assert legacy["fields"] == baseline["fields"]
    assert legacy["missing_blockers"] == baseline["missing_blockers"]
    assert legacy["documents"] == baseline["documents"]
    assert legacy["policy"] == baseline["policy"]
    assert legacy["camera_notes"] == baseline["camera_notes"]
    assert legacy["sketch"] == baseline["sketch"]
    assert legacy["severity"] == baseline["severity"]
    assert legacy["claim_type"] == baseline["claim_type"]
    assert legacy["handoff"] == baseline["handoff"]
    assert legacy["packet_markdown"] == baseline["packet_markdown"]
    assert [e["rule"] for e in legacy["events"]] == [e["rule"] for e in baseline["events"]]
    assert legacy["events"] == baseline["events"]


def test_status_stamp_reflects_the_route() -> None:
    _scenario, baseline, state = build("auto")
    assert state.status is not None
    assert state.status.label == ROUTE_LABELS[baseline["route"]]
    assert state.status.tone == ROUTE_TONES[baseline["route"]]
    assert state.status.key == baseline["route"]


def test_assets_and_activity_are_left_for_the_caller_to_own() -> None:
    _scenario, _baseline, state = build("flood")
    assert state.assets == []
    assert state.activity == []


def test_checklist_has_blockers_then_documents_with_platform_shape() -> None:
    _scenario, baseline, state = build("flood")
    blocker_ids = {f"blocker:{b}" for b in baseline["missing_blockers"]}
    doc_count = len(baseline["documents"])
    checklist_ids = [item.id for item in state.checklist]
    assert checklist_ids[: len(blocker_ids)] == [f"blocker:{b}" for b in baseline["missing_blockers"]]
    assert len(state.checklist) == len(blocker_ids) + doc_count
    for item in state.checklist:
        if item.id.startswith("blocker:"):
            assert item.blocking is True
            assert item.done is False


#: ``Note.kind`` -> the old ``buildNotes`` CSS class (the platform ``Note`` model
#: has no "line" kind; the default plain line uses the base ``kind="note"``).
_CSS_CLASS_BY_KIND = {
    "note": "",
    "title": "title",
    "check": "check",
    "flag": "flag",
    "aside": "aside",
    "blank": "blank",
}


@pytest.mark.parametrize("scenario_name", SCENARIOS)
def test_notes_match_the_recorded_buildnotes_output(scenario_name: str) -> None:
    _scenario, _baseline, state = build(scenario_name)
    recorded = load_fixture(f"insurance_notes_{scenario_name}.json")
    produced = [
        {
            "key": note.key,
            "cls": (
                f"{_CSS_CLASS_BY_KIND[note.kind]} urgent".strip()
                if note.tone == "danger"
                else _CSS_CLASS_BY_KIND[note.kind]
            ),
            "text": note.text,
            "blank": note.kind == "blank",
        }
        for note in state.notes
    ]
    assert produced == recorded


def test_route_change_event_only_fires_when_previous_route_differs() -> None:
    scenario = load_fixture("insurance_scenario_auto.json")
    workflow = WorkflowResult.model_validate(scenario["workflow"])
    state_first = build_ui_state(
        _participant_text(scenario),
        workflow,
        policy_record=_policy_record(scenario),
        camera_notes=_camera_notes(scenario),
        sketch=None,
        previous_route=None,
        now=NOW,
        model_label=MODEL_LABEL,
    )
    assert any(e["rule"] == "ROUTE-001" for e in state_first.custom["events"])

    state_second = build_ui_state(
        _participant_text(scenario),
        workflow,
        policy_record=_policy_record(scenario),
        camera_notes=_camera_notes(scenario),
        sketch=None,
        previous_route=state_first.custom["route"],
        now=NOW,
        model_label=MODEL_LABEL,
    )
    assert not any(e["rule"] == "ROUTE-001" for e in state_second.custom["events"])


def test_llm001_event_uses_the_injected_model_label_not_a_hardcoded_name() -> None:
    _scenario, _baseline, state = build("blank")
    llm_event = next(e for e in state.custom["events"] if e["rule"] == "LLM-001")
    assert MODEL_LABEL in llm_event["detail"]


@pytest.mark.parametrize("scenario_name", SCENARIOS)
def test_golden_lkap_ui_state_matches_committed_fixture(scenario_name: str) -> None:
    """Regression guard: the new-envelope golden fixtures must not silently drift.

    ``insurance_ui_state_lkap_{blank,auto,flood}.json`` are the LKAP ``UiState``
    goldens (docs/IMPLEMENTATION_PLAN.md's W1-PACK-INSURANCE-CORE acceptance
    criterion), generated once from this same ``build_ui_state`` call and
    committed next to the baseline fixtures. If this test fails after a
    deliberate change to ``ui_state.py``, regenerate them and review the diff
    before committing new goldens.
    """
    _scenario, _baseline, state = build(scenario_name)
    golden = load_fixture(f"insurance_ui_state_lkap_{scenario_name}.json")
    assert state.model_dump(mode="json") == golden


@pytest.mark.parametrize("scenario_name", SCENARIOS)
def test_custom_validates_against_the_manifest_state_schema(scenario_name: str) -> None:
    """``PackManifest.state_schema`` will be ``InsuranceState.model_json_schema()`` (W2); confirm
    every scenario's rendered ``custom`` actually validates against that model, both ways.
    """
    _scenario, _baseline, state = build(scenario_name)
    InsuranceState.model_validate(state.custom)


def test_insurance_state_json_schema_builds_without_error() -> None:
    schema = InsuranceState.model_json_schema()
    assert schema["type"] == "object"
    assert "fields" in schema["properties"]


def test_build_ui_state_for_intake_matches_the_manual_call() -> None:
    scenario = load_fixture("insurance_scenario_flood.json")
    workflow = WorkflowResult.model_validate(scenario["workflow"])
    intake = IntakeState(
        participant_turns=[t["text"] for t in scenario["transcript"] if t["speaker"] == "Claimant"],
        policy_record=_policy_record(scenario),
        camera_notes=_camera_notes(scenario),
        sketch=None,
        previous_route=None,
    )
    via_intake = build_ui_state_for_intake(intake, workflow, now=NOW, model_label=MODEL_LABEL)
    via_manual = build_ui_state(
        intake.participant_text(),
        workflow,
        policy_record=intake.policy_record,
        camera_notes=intake.camera_notes,
        sketch=intake.sketch,
        previous_route=intake.previous_route,
        now=NOW,
        model_label=MODEL_LABEL,
    )
    assert via_intake.model_dump(mode="json") == via_manual.model_dump(mode="json")
