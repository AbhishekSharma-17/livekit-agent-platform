"""Shared fixtures for the insurance_claim pack test suite.

``claim_factory``/``classification_factory`` are ported from the original
offline characterization suite's ``tests/conftest.py`` (commit ``c4472b0``):
same field defaults, so the ported ``test_rules.py`` cases produce identical
results. ``load_fixture`` reads the golden JSON/text fixtures copied from the
original ``tests/fixtures/`` into ``packs/tests/insurance_claim/fixtures/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from packs.insurance_claim.schemas import ClaimClassification, ClaimNarrative

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> Any:
    """Load a JSON fixture from ``packs/tests/insurance_claim/fixtures/`` by file name."""
    path = FIXTURES_DIR / name
    if path.suffix == ".json":
        return json.loads(path.read_text())
    return path.read_text()


def _make_claim(**overrides: Any) -> ClaimNarrative:
    defaults: dict[str, Any] = {
        "policyholder_name": "Jordan Lee",
        "policy_number": "AUTO-90210",
        "contact_method": "503-555-0199",
        "date_of_loss": "2026-04-02",
        "reported_date": "2026-04-02",
        "loss_location": "Portland",
        "loss_description": "Vehicle sustained damage in a parking lot incident.",
        "estimated_loss_usd": None,
        "injuries_or_safety_concerns": [],
        "parties_involved": [],
        "evidence_available": [],
        "documents_mentioned": [],
        "missing_or_uncertain_facts": [],
        "raw_narrative_summary": "Vehicle damage incident.",
        "assumptions": [],
    }
    defaults.update(overrides)
    return ClaimNarrative(**defaults)


def _make_classification(**overrides: Any) -> ClaimClassification:
    defaults: dict[str, Any] = {
        "claim_type": "auto_collision",
        "severity": "medium",
        "severity_rationale": "Moderate complexity claim.",
        "likely_policy_line": "Personal auto",
        "loss_drivers": [],
        "claimant_needs": [],
    }
    defaults.update(overrides)
    return ClaimClassification(**defaults)


@pytest.fixture
def claim_factory() -> Any:
    """Callable fixture: ``claim_factory(**overrides) -> ClaimNarrative``."""
    return _make_claim


@pytest.fixture
def classification_factory() -> Any:
    """Callable fixture: ``classification_factory(**overrides) -> ClaimClassification``."""
    return _make_classification
