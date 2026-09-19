"""Parity tests for packs.insurance_claim.policy_directory (ported from test_policy_directory.py).

Ported verbatim from the original offline characterization suite
(commit c4472b0, ``tests/test_policy_directory.py``); only the import path changed.
"""

from __future__ import annotations

import pytest

from packs.insurance_claim.policy_directory import (
    lookup_policy,
    normalize_policy_number,
    policy_status_headline,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("H0-44721", "H044721"),
        ("h0 44721", "H044721"),
        ("HO44721", "H044721"),  # letter-O prefix voice transcript quirk
        ("AUTO 90210", "AUTO90210"),
        ("auto-90210", "AUTO90210"),
        ("AUTO-11111", "AUTO11111"),
        ("", ""),
    ],
)
def test_normalize_policy_number_variants(raw, expected):
    assert normalize_policy_number(raw) == expected


@pytest.mark.parametrize("raw", ["H0-44721", "h0 44721", "HO44721", " h0-44721 "])
def test_lookup_policy_found_active_homeowners(raw):
    record = lookup_policy(raw)
    assert record["found"] is True
    assert record["policy_number"] == "H0-44721"
    assert record["policyholder_name"] == "Maya Singh"
    assert record["status"] == "active"


@pytest.mark.parametrize("raw", ["AUTO 90210", "auto-90210", "AUTO90210"])
def test_lookup_policy_found_active_auto(raw):
    record = lookup_policy(raw)
    assert record["found"] is True
    assert record["policyholder_name"] == "Jordan Lee"
    assert record["status"] == "active"


def test_lookup_policy_lapsed_auto():
    record = lookup_policy("AUTO-11111")
    assert record["found"] is True
    assert record["status"] == "lapsed"
    assert record["policyholder_name"] == "Chris Park"


def test_lookup_policy_not_found():
    record = lookup_policy("ZZZ-000")
    assert record["found"] is False
    assert record["policy_number"] == "ZZZ-000"
    assert "No policy matched" in record["message"]


def test_lookup_policy_empty_input():
    record = lookup_policy("")
    assert record["found"] is False
    assert record["policy_number"] == ""
    assert "No policy number was provided" in record["message"]


def test_policy_status_headline_active():
    record = lookup_policy("H0-44721")
    assert policy_status_headline(record) == "Active"


def test_policy_status_headline_lapsed():
    record = lookup_policy("AUTO-11111")
    assert policy_status_headline(record) == "Lapsed - human review required"


def test_policy_status_headline_not_found():
    record = lookup_policy("ZZZ-000")
    assert policy_status_headline(record) == "Not found"


def test_policy_status_headline_unknown_status_title_cased():
    assert policy_status_headline({"found": True, "status": "pending"}) == "Pending"
