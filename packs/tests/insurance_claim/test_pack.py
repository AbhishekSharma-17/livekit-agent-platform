"""Tests for `packs.insurance_claim.pack.InsuranceClaimPack` against
`FakePackSessionContext` (`tests/insurance_claim/fake_ctx.py`). No LiveKit
connection, no vendor keys, no network.
"""

from __future__ import annotations

import asyncio

import pytest
from livekit.agents import ChatContext, ChatMessage

from packs.insurance_claim import pack as pack_module
from packs.insurance_claim.pack import PACK
from packs.insurance_claim.schemas import ClaimClassification, ClaimNarrative, SketchState
from packs.insurance_claim.tools import get_intake
from tests.insurance_claim.conftest import load_fixture
from tests.insurance_claim.fake_ctx import FakePackSessionContext


def _user_message(text: str, msg_id: str = "m1") -> ChatMessage:
    return ChatMessage(id=msg_id, role="user", content=[text])


def _queue_scenario(ctx: FakePackSessionContext, fixture_name: str) -> dict[str, object]:
    scenario = load_fixture(fixture_name)
    workflow = scenario["workflow"]
    ctx.workflow_llm.responses.extend(
        [
            ClaimNarrative.model_validate(workflow["normalized_claim"]),
            ClaimClassification.model_validate(workflow["claim_classification"]),
        ]
    )
    return dict(workflow)


@pytest.fixture(autouse=True)
def _zero_debounce(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most tests don't care about the 2 s debounce window; zero it out."""
    monkeypatch.setattr(pack_module, "USER_TURN_DEBOUNCE_S", 0.0)


async def _settle() -> None:
    """Let scheduled debounce tasks and their background jobs run to completion."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# tools / tool_meta
# ---------------------------------------------------------------------------


def test_tools_returns_four_pack_tools() -> None:
    ctx = FakePackSessionContext()
    tools = PACK.tools(ctx)
    assert {t.info.name for t in tools} == {
        "lookup_policy",
        "sync_claim_packet",
        "pin_evidence_photo",
        "draw_incident_sketch",
    }


def test_tool_meta_independent_of_ctx() -> None:
    names = {meta.name for meta in PACK.tool_meta()}
    assert names == {
        "lookup_policy",
        "sync_claim_packet",
        "pin_evidence_photo",
        "draw_incident_sketch",
    }


# ---------------------------------------------------------------------------
# initial_state / on_session_start
# ---------------------------------------------------------------------------


def test_initial_state_is_blank_intake_custom() -> None:
    ctx = FakePackSessionContext()
    custom = PACK.initial_state(ctx)
    assert custom["route"] == "needs_docs"
    assert custom["policy"] is None
    assert custom["camera_notes"] == []
    assert custom["sketch"] is None


async def test_on_session_start_pushes_full_envelope_and_snapshot() -> None:
    ctx = FakePackSessionContext()
    await PACK.on_session_start(ctx)

    assert ctx.ui.snapshots_sent == 1
    assert ctx.ui.state.status is not None
    assert ctx.ui.state.custom["route"] == "needs_docs"
    assert get_intake(ctx).previous_route == "needs_docs"
    # the platform, not the pack, speaks the greeting (see pack.py module docstring).
    assert cast_said(ctx) == []


def cast_said(ctx: FakePackSessionContext) -> list[str]:
    return list(ctx.session.said)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# on_user_turn_completed (mapping #14)
# ---------------------------------------------------------------------------


async def test_on_user_turn_completed_records_every_turn_but_runs_every_second() -> None:
    ctx = FakePackSessionContext()
    _queue_scenario(ctx, "insurance_scenario_flood.json")
    turn_ctx = ChatContext.empty()

    await PACK.on_user_turn_completed(ctx, turn_ctx, _user_message("First fact.", "m1"))
    await _settle()
    assert get_intake(ctx).participant_turns == ["First fact."]
    assert not ctx.background.jobs  # odd turn: no passive run yet

    await PACK.on_user_turn_completed(ctx, turn_ctx, _user_message("Second fact.", "m2"))
    await _settle()
    assert get_intake(ctx).participant_turns == ["First fact.", "Second fact."]
    assert ctx.background.jobs  # even turn: one passive run submitted
    await ctx.background.wait_idle()


async def test_on_user_turn_completed_passive_run_never_urgent_even_for_emergency() -> None:
    ctx = FakePackSessionContext()
    _queue_scenario(ctx, "insurance_scenario_auto.json")
    turn_ctx = ChatContext.empty()

    await PACK.on_user_turn_completed(ctx, turn_ctx, _user_message("Injury on the highway.", "m1"))
    await _settle()
    await PACK.on_user_turn_completed(ctx, turn_ctx, _user_message("Police report filed.", "m2"))
    await _settle()
    await ctx.background.wait_idle()

    assert not ctx.background.urgent_events
    assert ctx.background.routine_notes
    assert ctx.ui.state.custom["route"] == "emergency_escalation"


async def test_on_user_turn_completed_skips_when_text_unchanged() -> None:
    ctx = FakePackSessionContext()
    _queue_scenario(ctx, "insurance_scenario_flood.json")
    turn_ctx = ChatContext.empty()

    await PACK.on_user_turn_completed(ctx, turn_ctx, _user_message("Fact one.", "m1"))
    await PACK.on_user_turn_completed(ctx, turn_ctx, _user_message("Fact two.", "m2"))
    await _settle()
    await ctx.background.wait_idle()
    first_job_count = len(ctx.background.jobs)
    assert first_job_count == 1

    # A third and fourth turn where the fourth carries no new text: participant_text()
    # is unchanged from the last passive run, so no second job is submitted.
    await PACK.on_user_turn_completed(ctx, turn_ctx, _user_message("", "m3"))
    await PACK.on_user_turn_completed(ctx, turn_ctx, _user_message("", "m4"))
    await _settle()
    await ctx.background.wait_idle()
    assert len(ctx.background.jobs) == first_job_count


async def test_on_user_turn_completed_debounce_cancels_superseded_run() -> None:
    """A rapid second even turn cancels the first debounce before it fires."""
    monkeypatch_target = pack_module
    monkeypatch_target.USER_TURN_DEBOUNCE_S = 0.05
    try:
        ctx = FakePackSessionContext()
        _queue_scenario(ctx, "insurance_scenario_flood.json")
        turn_ctx = ChatContext.empty()

        await PACK.on_user_turn_completed(ctx, turn_ctx, _user_message("Fact one.", "m1"))
        await PACK.on_user_turn_completed(ctx, turn_ctx, _user_message("Fact two.", "m2"))
        # Immediately, before the first debounce (turn 2) fires: turns 3 and 4.
        await PACK.on_user_turn_completed(ctx, turn_ctx, _user_message("Fact three.", "m3"))
        await PACK.on_user_turn_completed(ctx, turn_ctx, _user_message("Fact four.", "m4"))

        await asyncio.sleep(0.15)
        await ctx.background.wait_idle()
        assert len(ctx.background.jobs) == 1
    finally:
        monkeypatch_target.USER_TURN_DEBOUNCE_S = 0.0


# ---------------------------------------------------------------------------
# on_agent_turn_completed (no-op)
# ---------------------------------------------------------------------------


async def test_on_agent_turn_completed_is_noop() -> None:
    ctx = FakePackSessionContext()
    result = await PACK.on_agent_turn_completed(ctx, "some text", False)
    assert result is None


# ---------------------------------------------------------------------------
# on_ui_action (confirm_sketch, mapping #10)
# ---------------------------------------------------------------------------


async def test_on_ui_action_confirm_sketch_marks_confirmed() -> None:
    ctx = FakePackSessionContext()
    intake = get_intake(ctx)
    intake.sketch = SketchState(asset_id="asset-1", brief="basement flood", version=1, confirmed=False)

    result = await PACK.on_ui_action(ctx, "confirm_sketch", {})
    assert result == {"ok": True, "payload": {"asset_id": "asset-1", "confirmed": True}}
    assert intake.sketch.confirmed is True
    assert ctx.ui.state.custom["sketch"]["confirmed"] is True


async def test_on_ui_action_confirm_sketch_without_sketch_errors() -> None:
    ctx = FakePackSessionContext()
    result = await PACK.on_ui_action(ctx, "confirm_sketch", {})
    assert result["ok"] is False


async def test_on_ui_action_unknown_action_errors() -> None:
    ctx = FakePackSessionContext()
    result = await PACK.on_ui_action(ctx, "new_intake", {})
    assert result["ok"] is False
    assert "new_intake" in result["error"]


# ---------------------------------------------------------------------------
# on_session_end
# ---------------------------------------------------------------------------


async def test_on_session_end_noop_when_nothing_happened() -> None:
    ctx = FakePackSessionContext()
    await PACK.on_session_end(ctx, "client disconnected")
    assert ctx.ui.state.notes == []


async def test_on_session_end_leaves_final_note_when_workflow_ran() -> None:
    ctx = FakePackSessionContext()
    await PACK.on_session_start(ctx)  # runs the blank workflow, sets last_workflow
    await PACK.on_session_end(ctx, "client disconnected")
    assert any("Session ended" in note.text for note in ctx.ui.state.notes)
