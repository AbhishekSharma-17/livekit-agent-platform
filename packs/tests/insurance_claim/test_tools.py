"""Tests for `packs.insurance_claim.tools` against `FakePackSessionContext`
(`tests/insurance_claim/fake_ctx.py`). No LiveKit connection, no vendor keys,
no network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, cast

from livekit.agents.llm import ToolFlag
from livekit.agents.llm.utils import build_legacy_openai_schema

from packs.base import FrameSnapshot
from packs.insurance_claim.schemas import ClaimClassification, ClaimNarrative
from packs.insurance_claim.tools import (
    INSURANCE_TOOL_META,
    PIN_EVIDENCE_MAX_AGE_S,
    build_draw_incident_sketch_tool,
    build_insurance_tools,
    build_lookup_policy_tool,
    build_pin_evidence_photo_tool,
    build_sync_claim_packet_tool,
    get_intake,
    model_label,
    submit_workflow_run,
)
from packs.insurance_claim.ui_state import camera_note
from tests.insurance_claim.conftest import load_fixture
from tests.insurance_claim.fake_ctx import FakePackSessionContext, default_agent_config


@dataclass
class _FakeFunctionCall:
    call_id: str = "call-1"


@dataclass
class _FakeRunContext:
    function_call: _FakeFunctionCall = field(default_factory=_FakeFunctionCall)


def _run_ctx(call_id: str = "call-1") -> Any:
    return cast(Any, _FakeRunContext(_FakeFunctionCall(call_id)))


def _video_frame_snapshot(source: str = "camera", age_s: float = 1.0) -> FrameSnapshot:
    return cast(
        FrameSnapshot, FrameSnapshot(frame=cast(Any, object()), source=cast(Any, source), age_s=age_s)
    )


def _queue_scenario(ctx: FakePackSessionContext, fixture_name: str) -> dict[str, Any]:
    """Queue the fixture's recorded narrative/classification onto `ctx.workflow_llm`."""
    scenario = load_fixture(fixture_name)
    workflow = scenario["workflow"]
    ctx.workflow_llm.responses.extend(
        [
            ClaimNarrative.model_validate(workflow["normalized_claim"]),
            ClaimClassification.model_validate(workflow["claim_classification"]),
        ]
    )
    turns = [turn["text"] for turn in scenario["transcript"] if turn["speaker"] == "Claimant"]
    get_intake(ctx).participant_turns.extend(turns)
    return dict(workflow)


# ---------------------------------------------------------------------------
# lookup_policy (mapping #7)
# ---------------------------------------------------------------------------


async def test_lookup_policy_active_records_and_renders() -> None:
    ctx = FakePackSessionContext()
    tool = build_lookup_policy_tool(ctx)
    result = await tool(context=_run_ctx(), policy_number="H0-44721")

    record = json.loads(result)
    assert record["found"] is True
    assert record["status"] == "active"
    assert get_intake(ctx).policy_record == record
    assert ctx.ui.state.custom["policy"] == record
    # active policy: route-derived stamp is not overridden to danger.
    assert ctx.ui.state.status is not None
    assert ctx.ui.state.status.tone != "danger"

    activity = ctx.ui.state.activity[-1]
    assert activity.source == "lookup_policy"
    assert activity.label == "Policy desk"
    assert activity.phase == "done"
    assert activity.urgent is False


async def test_lookup_policy_lapsed_sets_danger_status_and_urgent_activity() -> None:
    ctx = FakePackSessionContext()
    tool = build_lookup_policy_tool(ctx)
    result = await tool(context=_run_ctx(), policy_number="AUTO-11111")

    record = json.loads(result)
    assert record["status"] == "lapsed"
    assert ctx.ui.state.status is not None
    assert ctx.ui.state.status.label == "Policy needs review"
    assert ctx.ui.state.status.tone == "danger"
    assert ctx.ui.state.activity[-1].urgent is True


async def test_lookup_policy_not_found_is_urgent() -> None:
    ctx = FakePackSessionContext()
    tool = build_lookup_policy_tool(ctx)
    result = await tool(context=_run_ctx(), policy_number="ZZZ-0000")

    record = json.loads(result)
    assert record["found"] is False
    assert ctx.ui.state.status is not None
    assert ctx.ui.state.status.tone == "danger"
    assert ctx.ui.state.activity[-1].urgent is True
    assert "not found" in ctx.ui.state.activity[-1].headline.lower()


# ---------------------------------------------------------------------------
# sync_claim_packet (mapping #8)
# ---------------------------------------------------------------------------


async def test_sync_claim_packet_realtime_silent_and_urgent_for_emergency() -> None:
    ctx = FakePackSessionContext(pipeline_mode="realtime")
    _queue_scenario(ctx, "insurance_scenario_auto.json")
    tool = build_sync_claim_packet_tool(ctx)

    result = await tool(context=_run_ctx(), reason="injury mentioned")
    assert result is None
    # the web notebook panel keys the team feed off `ActivityEvent.source ==
    # <tool name>`, and `BackgroundRunner.submit(name=...)` is what a real
    # `BackgroundToolRunner` turns into that `source`.
    assert ctx.background.submitted == [(ctx.background.submitted[0][0], "sync_claim_packet")]

    await ctx.background.wait_idle()
    assert len(ctx.background.urgent_events) == 1
    job_id, wf_result, instructions = ctx.background.urgent_events[0]
    assert wf_result.routing == "emergency_escalation"
    assert instructions is not None and "emergency services" in instructions
    assert ctx.ui.state.custom["route"] == "emergency_escalation"


async def test_sync_claim_packet_cascaded_acknowledges_and_routine_note() -> None:
    ctx = FakePackSessionContext(pipeline_mode="cascaded")
    _queue_scenario(ctx, "insurance_scenario_flood.json")
    tool = build_sync_claim_packet_tool(ctx)

    result = await tool(context=_run_ctx(), reason="new loss facts")
    assert result == "Got it, updating the claim notes."

    await ctx.background.wait_idle()
    assert not ctx.background.urgent_events
    assert len(ctx.background.routine_notes) == 1
    _, note = ctx.background.routine_notes[0]
    payload = json.loads(note)
    assert payload["routing_decision"] == "needs_docs"
    assert ctx.ui.state.custom["route"] == "needs_docs"


async def test_submit_workflow_run_passive_never_urgent_for_emergency() -> None:
    """mapping #14: a passive run (`urgent_when_emergency=False`) never urgent-speaks."""
    ctx = FakePackSessionContext()
    _queue_scenario(ctx, "insurance_scenario_auto.json")

    submit_workflow_run(ctx, urgent_when_emergency=False)
    await ctx.background.wait_idle()

    assert not ctx.background.urgent_events
    assert len(ctx.background.routine_notes) == 1
    assert ctx.ui.state.custom["route"] == "emergency_escalation"


# ---------------------------------------------------------------------------
# escalation session event (DECISIONS-W2 D-W3-1)
# ---------------------------------------------------------------------------

_EMERGENCY_ESCALATION_EVENT = (
    "escalation",
    {"reason": "claim routed to emergency_escalation", "urgency": "high", "route": "emergency_escalation"},
)


def _queue_second_run(ctx: FakePackSessionContext, fixture_name: str) -> None:
    """Queue another run's narrative/classification without re-adding the transcript."""
    workflow = load_fixture(fixture_name)["workflow"]
    ctx.workflow_llm.responses.extend(
        [
            ClaimNarrative.model_validate(workflow["normalized_claim"]),
            ClaimClassification.model_validate(workflow["claim_classification"]),
        ]
    )


async def test_sync_claim_packet_injury_records_one_escalation_event_across_two_runs() -> None:
    ctx = FakePackSessionContext(pipeline_mode="realtime")
    _queue_scenario(ctx, "insurance_scenario_auto.json")
    _queue_second_run(ctx, "insurance_scenario_auto.json")
    tool = build_sync_claim_packet_tool(ctx)

    await tool(context=_run_ctx("call-1"), reason="injury mentioned")
    await ctx.background.wait_idle()
    await tool(context=_run_ctx("call-2"), reason="more detail")
    await ctx.background.wait_idle()

    assert len(ctx.background.on_result_calls) == 2
    assert ctx.ui.state.custom["route"] == "emergency_escalation"
    assert ctx.events == [_EMERGENCY_ESCALATION_EVENT]


async def test_passive_workflow_run_records_the_escalation_event() -> None:
    ctx = FakePackSessionContext()
    _queue_scenario(ctx, "insurance_scenario_auto.json")

    submit_workflow_run(ctx, urgent_when_emergency=False)
    await ctx.background.wait_idle()

    assert not ctx.background.urgent_events
    assert ctx.events == [_EMERGENCY_ESCALATION_EVENT]


async def test_non_escalating_route_records_no_event() -> None:
    ctx = FakePackSessionContext(pipeline_mode="cascaded")
    _queue_scenario(ctx, "insurance_scenario_flood.json")
    tool = build_sync_claim_packet_tool(ctx)

    await tool(context=_run_ctx(), reason="new loss facts")
    await ctx.background.wait_idle()

    assert ctx.ui.state.custom["route"] == "needs_docs"
    assert ctx.events == []


# ---------------------------------------------------------------------------
# pin_evidence_photo (mapping #9)
# ---------------------------------------------------------------------------


async def test_pin_evidence_photo_with_fresh_frame() -> None:
    ctx = FakePackSessionContext()
    ctx.frames.set_latest(_video_frame_snapshot(), jpeg_bytes=b"\xff\xd8fake-jpeg")
    tool = build_pin_evidence_photo_tool(ctx)

    result = await tool(
        context=_run_ctx("call-42"),
        observation="Water line about two inches up the drywall next to the stairs.",
        confirmed=True,
        claimant_description="the flooded corner",
        evidence_type="damage",
    )
    payload = json.loads(result)
    assert payload["pinned"] is True
    assert payload["confirmed"] is True

    asset = ctx.ui.state.assets[-1]
    assert asset.kind == "evidence"
    assert asset.meta["source"] == "camera"
    assert asset.meta["confirmed"] == "true"
    assert asset.meta["claimant_description"] == "the flooded corner"
    assert asset.meta["evidence_type"] == "damage"

    intake = get_intake(ctx)
    assert intake.camera_notes == [
        camera_note(
            "Water line about two inches up the drywall next to the stairs.",
            "the flooded corner",
            True,
        )
    ]
    activity = ctx.ui.state.activity[-1]
    assert activity.source == "pin_evidence_photo"
    assert activity.label == "Evidence"
    assert activity.detail is not None and activity.detail["asset_id"] == asset.asset_id


async def test_pin_evidence_photo_unconfirmed_caption_meta() -> None:
    ctx = FakePackSessionContext()
    ctx.frames.set_latest(_video_frame_snapshot())
    tool = build_pin_evidence_photo_tool(ctx)

    result = await tool(
        context=_run_ctx(),
        observation="I can see a dark mark but I can't make out a crack from here.",
        confirmed=False,
    )
    payload = json.loads(result)
    assert payload["confirmed"] is False
    assert ctx.ui.state.assets[-1].meta["confirmed"] == "false"
    # defaults: claimant_description="" and evidence_type="scene" (documented default).
    assert ctx.ui.state.assets[-1].meta["claimant_description"] == ""
    assert ctx.ui.state.assets[-1].meta["evidence_type"] == "scene"


async def test_pin_evidence_photo_no_fresh_frame() -> None:
    ctx = FakePackSessionContext()  # no frame seeded
    tool = build_pin_evidence_photo_tool(ctx)

    result = await tool(context=_run_ctx(), observation="anything", confirmed=False)
    assert "no fresh" in result.lower()
    assert ctx.ui.state.assets == []
    assert get_intake(ctx).camera_notes == []


def test_pin_evidence_max_age_matches_mapping() -> None:
    assert PIN_EVIDENCE_MAX_AGE_S == 12.0


# ---------------------------------------------------------------------------
# draw_incident_sketch (mapping #10)
# ---------------------------------------------------------------------------


async def test_draw_incident_sketch_no_image_gen_returns_apology() -> None:
    ctx = FakePackSessionContext(image_gen=None)
    tool = build_draw_incident_sketch_tool(ctx)

    result = await tool(context=_run_ctx(), scene_description="basement flood")
    assert result is not None
    assert "isn't available" in result
    assert not ctx.background.jobs


async def test_draw_incident_sketch_versions_increment_and_patches_custom_sketch() -> None:
    ctx = FakePackSessionContext()
    tool = build_draw_incident_sketch_tool(ctx)

    first = await tool(context=_run_ctx("call-1"), scene_description="basement flood, sump pump")
    assert first == "Sketching that now."
    assert ("call-1", "draw_incident_sketch") in ctx.background.submitted
    await ctx.background.wait_idle()

    intake = get_intake(ctx)
    assert intake.sketch is not None
    assert intake.sketch.version == 1
    assert intake.sketch.confirmed is False
    assert ctx.ui.state.custom["sketch"]["version"] == 1
    assert ctx.image_gen is not None and ctx.image_gen.prompts[-1].startswith("A quick hand-drawn pen sketch")

    await tool(context=_run_ctx("call-2"), scene_description="basement flood, corrected layout")
    await ctx.background.wait_idle()
    assert intake.sketch.version == 2
    assert ctx.ui.state.custom["sketch"]["version"] == 2


async def test_draw_incident_sketch_realtime_returns_none() -> None:
    ctx = FakePackSessionContext(pipeline_mode="realtime")
    tool = build_draw_incident_sketch_tool(ctx)
    result = await tool(context=_run_ctx(), scene_description="basement flood")
    assert result is None
    await ctx.background.wait_idle()
    assert get_intake(ctx).sketch is not None


async def test_draw_incident_sketch_provider_failure_degrades_gracefully() -> None:
    ctx = FakePackSessionContext()
    assert ctx.image_gen is not None
    ctx.image_gen.error = RuntimeError("boom")
    tool = build_draw_incident_sketch_tool(ctx)

    result = await tool(context=_run_ctx(), scene_description="basement flood")
    assert result == "Sketching that now."
    await ctx.background.wait_idle()

    assert get_intake(ctx).sketch is None
    assert not ctx.ui.state.assets
    assert len(ctx.background.routine_notes) == 1
    _, note = ctx.background.routine_notes[0]
    assert "didn't come through" in note


# ---------------------------------------------------------------------------
# schema / metadata parity
# ---------------------------------------------------------------------------


_GEMINI_TYPE_TO_JSON = {
    "STRING": "string",
    "BOOLEAN": "boolean",
    "OBJECT": "object",
    "NUMBER": "number",
    "INTEGER": "integer",
}


def _collapse(text: str) -> str:
    return " ".join(text.split())


def test_tool_schema_matches_declarations_fixture() -> None:
    fixture = load_fixture("insurance_declarations.json")
    by_name = {decl["name"]: decl for decl in fixture["function_declarations"]}

    ctx = FakePackSessionContext()
    tools = {tool.info.name: tool for tool in build_insurance_tools(ctx)}
    assert set(tools) == set(by_name)

    for name, tool in tools.items():
        declaration = by_name[name]
        schema = build_legacy_openai_schema(tool)["function"]
        assert _collapse(schema["description"]) == _collapse(declaration["description"])

        params = schema["parameters"]
        expected_props = declaration["parameters"]["properties"]
        assert set(params["properties"]) == set(expected_props)
        assert set(params.get("required", [])) == set(declaration["parameters"].get("required", []))

        for pname, expected in expected_props.items():
            actual = params["properties"][pname]
            assert actual["type"] == _GEMINI_TYPE_TO_JSON[expected["type"]]
            assert _collapse(actual["description"]) == _collapse(expected["description"])


def test_all_tools_are_cancellable() -> None:
    ctx = FakePackSessionContext()
    for tool in build_insurance_tools(ctx):
        assert ToolFlag.CANCELLABLE in tool.info.flags


def test_insurance_tool_meta_names_and_labels() -> None:
    by_name = {meta.name: meta for meta in INSURANCE_TOOL_META}
    assert set(by_name) == {
        "lookup_policy",
        "sync_claim_packet",
        "pin_evidence_photo",
        "draw_incident_sketch",
    }
    assert by_name["lookup_policy"].activity_label == "Policy desk"
    assert by_name["sync_claim_packet"].activity_label == "Claim writer"
    assert by_name["pin_evidence_photo"].activity_label == "Evidence"
    assert by_name["draw_incident_sketch"].activity_label == "Sketch artist"


def test_model_label_falls_back_through_pipeline_slots() -> None:
    ctx = FakePackSessionContext(config=default_agent_config())
    assert model_label(ctx) == ctx.config.pipeline.llm.model or model_label(ctx) == "livekit-inference-llm"
