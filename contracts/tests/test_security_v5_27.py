"""V5-27 contract fixes from the V5-26 security review (docs/v5/SECURITY-REVIEW-V5.md)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from lkap_contracts.api_models import (
    MAX_KB_QUERY_CHARS,
    CostEstimateRequest,
    InternalKbSearchRequest,
    KbCreate,
    KbSearchRequest,
)
from lkap_contracts.compliance import ConsentEvent, consent_text_hash
from lkap_contracts.packs import KbSeed
from lkap_contracts.tool_providers import action_risk
from lkap_contracts.tools import McpServerDefinition, McpToolSnapshot


# ---------------------------------------------------------------- S5-9 (R-V5-16)
@pytest.mark.parametrize(
    ("slug", "tags", "risk"),
    [
        # The review's list: each was a "write" before V5-27.
        ("STRIPE_CREATE_PAYOUT", [], "destructive"),
        ("WISE_CREATE_TRANSFER", [], "destructive"),
        ("GOOGLEDRIVE_TRASH_FILE", [], "destructive"),
        ("NOTION_ARCHIVE_PAGE", [], "destructive"),
        ("SHOPIFY_CANCEL_ORDER", [], "destructive"),
        ("GITHUB_FORCE_PUSH", [], "destructive"),
        ("SLACK_KICK_USER", [], "destructive"),
        ("TWILIO_TERMINATE_CALL", [], "destructive"),
        ("ACME_WIPE_DISK", [], "destructive"),
        ("DB_DROP_TABLE", [], "destructive"),
        ("OKTA_REVOKE_SESSIONS", [], "destructive"),
        ("STRIPE_CREATE_CHARGE", [], "destructive"),
        ("DISCORD_BAN_MEMBER", [], "destructive"),
        ("AUTH_RESET_PASSWORD", [], "destructive"),
        ("OKTA_DEACTIVATE_USER", [], "destructive"),
        ("MAILCHIMP_UNSUBSCRIBE_MEMBER", [], "destructive"),
        ("DB_TRUNCATE_TABLE", [], "destructive"),
        ("BANK_SEND_PAYMENT", [], "destructive"),
        ("WALLET_MOVE_MONEY", [], "destructive"),
        ("INVOICE_PAY", [], "destructive"),
        # A vendor destructive tag wins over the slug and over a read-only tag.
        ("ACME_UPDATE_RECORD", ["destructiveHint"], "destructive"),
        ("ACME_LIST_THINGS", ["Destructive"], "destructive"),
        ("ACME_GET_THING", ["readOnlyHint", "destructiveHint"], "destructive"),
        # Whole words only for plain markers: PAYPAL is not PAY, PAYMENTS is not PAY.
        ("PAYPAL_LIST_PAYMENTS", [], "read"),
        ("PAYPAL_CREATE_INVOICE", [], "write"),
        # The pre-V5-27 fixture keeps its answers.
        ("GOOGLECALENDAR_FIND_FREE_SLOTS", None, "read"),
        ("GMAIL_FETCH_EMAILS", [], "read"),
        ("GMAIL_SEND_EMAIL", [], "write"),
        ("GITHUB_DELETE_REPO", [], "destructive"),
        ("PAYPAL_SEND_MONEY", [], "destructive"),
        ("SHOP_PURGE_CACHE", ["readOnlyHint"], "destructive"),
        ("CRM_UPDATE_DEAL", ["readOnlyHint"], "read"),
    ],
)
def test_action_risk_honours_destructive_hints_and_the_wider_marker_list(
    slug: str, tags: list[str] | None, risk: str
) -> None:
    assert action_risk(slug, tags) == risk


# ---------------------------------------------------------------- S5-4
def test_consent_event_turn_id_is_additive_and_bounded() -> None:
    digest = consent_text_hash("May we record?")
    legacy = ConsentEvent.model_validate(
        {"kind": "recording", "accepted": True, "method": "voice", "text_hash": digest}
    )
    assert legacy.turn_id is None
    event = ConsentEvent(kind="recording", accepted=True, method="voice", text_hash=digest, turn_id="t1")
    assert event.model_dump()["turn_id"] == "t1"
    with pytest.raises(ValidationError):
        ConsentEvent(kind="recording", accepted=True, method="voice", text_hash=digest, turn_id="x" * 65)


# ---------------------------------------------------------------- S5-13, S5-29
@pytest.mark.parametrize("model", [KbSearchRequest, InternalKbSearchRequest])
def test_search_query_is_bounded(model: Any) -> None:
    extra = {"kb_ids": ["k"]} if model is InternalKbSearchRequest else {}
    model(query="q" * MAX_KB_QUERY_CHARS, **extra)
    with pytest.raises(ValidationError):
        model(query="q" * (MAX_KB_QUERY_CHARS + 1), **extra)
    with pytest.raises(ValidationError):
        model(query="", **extra)


def test_internal_search_session_id_is_optional() -> None:
    assert InternalKbSearchRequest(kb_ids=["k"], query="q").session_id is None
    assert InternalKbSearchRequest(kb_ids=["k"], query="q", session_id="s1").session_id == "s1"


# ---------------------------------------------------------------- S5-42
def _mcp(**extra: Any) -> dict[str, Any]:
    return {"type": "mcp", "name": "m", "url": "https://x.example/mcp", **extra}


def test_cached_tools_and_snapshots_are_capped() -> None:
    McpToolSnapshot(name="t", description="d" * 1000, input_schema={"type": "object"})
    with pytest.raises(ValidationError):
        McpToolSnapshot(name="t", description="d" * 1001)
    with pytest.raises(ValidationError):
        McpToolSnapshot(name="t", input_schema={"description": "x" * 16_001})
    many = [{"name": f"t{i}"} for i in range(201)]
    with pytest.raises(ValidationError):
        McpServerDefinition.model_validate(_mcp(cached_tools=many))
    McpServerDefinition.model_validate(_mcp(cached_tools=many[:200]))


def test_cost_estimate_assumptions_are_capped() -> None:
    CostEstimateRequest(assumptions={f"k{i}": 1.0 for i in range(50)})
    with pytest.raises(ValidationError):
        CostEstimateRequest(assumptions={f"k{i}": 1.0 for i in range(51)})


# ---------------------------------------------------------------- S5-38
@pytest.mark.parametrize("name", ["../x.md", "/etc/passwd", "a/../../b.md", "C:\\x.md", "..\\x.md", ""])
def test_kb_seed_files_refuse_paths_outside_the_pack(name: str) -> None:
    with pytest.raises(ValidationError):
        KbSeed(kb_name="k", files=[name])


def test_kb_seed_files_keep_pack_relative_paths() -> None:
    assert KbSeed(kb_name="k", files=["policy_lines.md", "kb/intake.md"]).files == [
        "policy_lines.md",
        "kb/intake.md",
    ]


def test_kb_create_name_and_description_are_bounded() -> None:
    KbCreate(name="n" * 200, description="d" * 4000)
    with pytest.raises(ValidationError):
        KbCreate(name="n" * 201)
    with pytest.raises(ValidationError):
        KbCreate(name="n", description="d" * 4001)
