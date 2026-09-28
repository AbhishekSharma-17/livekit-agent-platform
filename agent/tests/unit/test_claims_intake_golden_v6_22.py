"""V6-22 (D-V6-30): the FNOL golden chat on the worker's real engines, from the claims starter's config.

``fixtures/claims_intake_config.json`` is the configuration the ``claims_intake`` starter seeds
(ids normalised; ``api/tests/test_claims_intake_v6_22.py::test_the_worker_fixture_is_the_seeded_config``
keeps it equal to what the api creates). Nothing here is insurance code: the caller's turns go
through the real live extraction (a scripted structured model stands in for the workflow LLM,
the one external boundary), the real rules engine, the real dataset lookup tool (the kit's
fixture row answers, as a V5-29 test case's mock does) and the real built-in tools with fake
media. Each D-V6-30 behaviour is asserted on what the caller would see (the panel state), the
session events, the note the model gets for its next reply, or the tool call.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import httpx
from fakes.fake_ctx import FakeImageGen, FakePackSessionContext, FakeRunContext
from livekit import rtc
from livekit.agents import RunContext
from livekit.agents import llm as lk_llm
from livekit.agents.voice.events import ConversationItemAddedEvent, FunctionToolsExecutedEvent
from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.tools import DatasetToolDefinition
from packs.base import FrameSnapshot
from pydantic import BaseModel

from lkap_agent.extraction.session import LiveStructure, live_structure
from lkap_agent.tools.builtin import build_builtin_tools
from lkap_agent.tools.context import ToolCallContext
from lkap_agent.tools.dataset import build_dataset_tool

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "claims_intake_config.json"
NOTEBOOK = "claim_notebook"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


# --------------------------------------------------------------------------- the one external boundary
class ScriptedExtraction:
    """The workflow model: answers each extraction call with the next scripted object."""

    def __init__(self, *answers: dict[str, Any]) -> None:
        self.answers = list(answers)
        self.calls: list[str] = []

    async def extract(
        self, *, instructions: str, input_text: str, schema: type[BaseModel], timeout_s: float = 45
    ) -> BaseModel:
        self.calls.append(input_text)
        return schema.model_validate(self.answers.pop(0) if self.answers else {})


class FakeSession:
    """An ``AgentSession`` stand-in with a history and ``on()``."""

    def __init__(self) -> None:
        self.history = lk_llm.ChatContext.empty()
        self.handlers: dict[str, list[Any]] = {}

    def on(self, event: str, handler: Any) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def say_user(self, text: str) -> None:
        message = self.history.add_message(role="user", content=text)
        for handler in self.handlers.get("conversation_item_added", []):
            handler(ConversationItemAddedEvent(item=message))

    def tool_ran(self, name: str) -> None:
        call = lk_llm.FunctionCall(call_id=f"c-{name}", name=name, arguments="{}")
        output = lk_llm.FunctionCallOutput(call_id=f"c-{name}", name=name, output="x", is_error=False)
        event = FunctionToolsExecutedEvent(function_calls=[call], function_call_outputs=[output])
        for handler in self.handlers.get("function_tools_executed", []):
            handler(event)


# --------------------------------------------------------------------------- the starter's config
def _fixture() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return loaded


def _session(*answers: dict[str, Any]) -> tuple[FakePackSessionContext, FakeSession, ScriptedExtraction]:
    config = AgentConfig.model_validate(_fixture()["config"])
    session = FakeSession()
    llm = ScriptedExtraction(*answers)
    ctx = FakePackSessionContext(
        config=config,
        session=cast(Any, session),
        workflow_llm=cast(Any, llm),
        image_gen=FakeImageGen(image_bytes=PNG),
    )
    return ctx, session, llm


def _structure(ctx: FakePackSessionContext) -> LiveStructure:
    structure = live_structure(ctx)
    assert structure is not None
    return structure


def _next_reply_note(structure: LiveStructure) -> str:
    """What the rules tell the model for its next reply (``instruct``), as one text."""
    turn = lk_llm.ChatContext.empty()
    structure.add_instructions(turn)
    return "\n".join(item.text_content or "" for item in turn.items if isinstance(item, lk_llm.ChatMessage))


def _summary(ctx: FakePackSessionContext) -> dict[str, str]:
    """The notebook's summary card, as the caller sees it: row key → value."""
    section = ctx.ui.state.blocks[NOTEBOOK]["sections"]["summary"]
    return {str(row["key"]): str(row["value"]) for row in section["items"]}


def _checklist(ctx: FakePackSessionContext) -> dict[str, bool]:
    return {item.id: item.done for item in ctx.ui.state.checklist}


def _events(ctx: FakePackSessionContext, kind: str) -> list[dict[str, Any]]:
    return [payload for event, payload in ctx.events if event == kind]


def _run(name: str) -> RunContext[Any]:
    return cast(RunContext[Any], FakeRunContext(name=name))


TURN_1 = "Yes, everyone's safe. A pipe burst under my kitchen sink last night and soaked the floor."
LOSS = {
    "reason": "A pipe burst under the kitchen sink",
    "request_details": "A pipe under the kitchen sink burst last night and soaked the kitchen floor.",
    "claim_type": "home_water_damage",
    "date_of_loss": "last night",
    "safety_concern": False,
}


# --------------------------------------------------------------------------- the golden chat
async def test_the_greeting_asks_about_safety_before_anything_else() -> None:
    config = AgentConfig.model_validate(_fixture()["config"])

    assert config.voice.greeting.endswith("are you and everyone else in a safe place?")
    assert config.voice.greeting_mode == "say"


async def test_the_narrative_and_claim_type_reach_the_notebook_summary_on_the_turn_they_are_said() -> None:
    ctx, session, llm = _session(LOSS)
    structure = _structure(ctx)

    session.say_user(TURN_1)
    await structure.wait_idle()

    assert len(llm.calls) == 1  # this turn, not a later one
    summary = _summary(ctx)
    assert summary["request_details"].startswith("A pipe under the kitchen sink burst")
    assert summary["claim_type"] == "home_water_damage"
    # Still needed: the required facts the caller has not given yet.
    checklist = _checklist(ctx)
    assert {"need_caller_name", "need_policy_number", "need_callback_number", "need_loss_location"} <= set(
        checklist
    )
    assert checklist["need_request_details"] is True


async def test_the_claim_type_drives_the_document_checklist_and_the_coverage_caveat() -> None:
    ctx, session, _ = _session(LOSS)
    structure = _structure(ctx)

    session.say_user(TURN_1)
    await structure.wait_idle()

    labels = {item.id: item.label for item in ctx.ui.state.checklist}
    assert [labels[f"doc_water_{n}"] for n in range(1, 5)] == [
        "Photos or video of damaged areas before cleanup",
        "Mitigation or drying invoice",
        "Repair estimate or contractor assessment",
        "Receipts for damaged personal property",
    ]
    assert not any(item_id.startswith("doc_auto_") for item_id in labels)
    note = _next_reply_note(structure)
    assert "water damage claim" in note and "I can't confirm coverage" in note
    assert "documents_water" in [event["rule_id"] for event in _events(ctx, "rule_fired")]


async def test_a_coverage_question_gets_the_caveat_every_time_it_is_asked() -> None:
    asked = {"asked_about_coverage": True}
    ctx, session, _ = _session(LOSS, asked, {"asked_about_coverage": False}, asked)
    structure = _structure(ctx)

    session.say_user(TURN_1)
    await structure.wait_idle()
    _next_reply_note(structure)
    notes: list[str] = []
    for text in ("Is this covered?", "Okay, what else do you need?", "But will you pay for it?"):
        session.say_user(text)
        await structure.wait_idle()
        notes.append(_next_reply_note(structure))

    assert ["I can't confirm coverage" in note for note in notes] == [True, False, True]
    assert all("promise" not in note.lower() or "no coverage" in note.lower() for note in notes if note)


async def test_a_policy_is_found_in_the_lookup_table_by_number_or_by_name_and_shown_on_the_panel() -> None:
    fixture = _fixture()
    definition = DatasetToolDefinition.model_validate(fixture["tools"][0])
    golden = next(case for case in fixture["config"]["tests"] if case["id"] == "fnol-golden")
    safety = next(case for case in fixture["config"]["tests"] if case["id"] == "fnol-safety")
    ctx, session, _ = _session()
    structure = _structure(ctx)
    requests: list[httpx.Request] = []

    def factory() -> httpx.AsyncClient:
        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(500)

        return httpx.AsyncClient(base_url="http://api.test", transport=httpx.MockTransport(handle))

    by_number = build_dataset_tool(
        definition,
        context=ToolCallContext(ctx),
        client_factory=factory,
        mock=golden["mocks"]["policy_lookup"],
    )
    by_name = build_dataset_tool(
        definition,
        context=ToolCallContext(ctx),
        client_factory=factory,
        mock=safety["mocks"]["policy_lookup"],
    )

    found = await by_number(raw_arguments={"policy_number": "H0-44721"}, context=_run("policy_lookup"))
    named = await by_name(raw_arguments={"policyholder_name": "Jordan Lee"}, context=_run("policy_lookup"))
    session.tool_ran("policy_lookup")
    await structure.wait_idle()

    assert definition.name == "policy_lookup"
    assert definition.key_columns == ["policy_number", "policyholder_name"]
    assert found.startswith('<untrusted source="dataset:policy_lookup">')
    assert "Maya Singh" in found and "Homeowners (HO-3)" in found
    assert "Jordan Lee" in named and "Personal auto" in named
    assert requests == []  # the test case's fixture answers; nothing calls out
    rows = ctx.ui.state.blocks["policy_results"]["rows"]
    assert any("Jordan Lee" in json.dumps(row) for row in rows)
    assert ctx.ui.state.status is not None and ctx.ui.state.status.label == "Record checked"


async def test_a_safety_concern_escalates_to_a_person_with_the_emergency_line() -> None:
    hurt = {"safety_concern": True, "safety_details": "the neighbour slipped and hurt her wrist"}
    ctx, session, _ = _session(LOSS, hurt)
    structure = _structure(ctx)

    session.say_user(TURN_1)
    await structure.wait_idle()
    assert _events(ctx, "escalation") == []  # "everyone's safe" does not escalate
    _next_reply_note(structure)
    session.say_user("Actually my neighbour slipped on the wet floor and hurt her wrist.")
    await structure.wait_idle()

    assert _events(ctx, "escalation") == [
        {"reason": "The claimant reported an injury or an unsafe home or scene.", "urgency": "high"}
    ]
    assert ctx.ui.state.status is not None
    assert (ctx.ui.state.status.label, ctx.ui.state.status.tone) == ("Safety review", "danger")
    assert "contact emergency services" in _next_reply_note(structure)


async def test_the_hand_off_summary_lands_in_the_notebook_once_the_core_facts_are_in() -> None:
    who = {"caller_name": "Maya Singh", "policy_number": "H0-44721"}
    where = {"loss_location": "12 Elm Street, the kitchen", "callback_number": "555 0100"}
    ctx, session, _ = _session(LOSS, who, where)
    structure = _structure(ctx)

    for text in (TURN_1, "I'm Maya Singh, policy H0-44721.", "It's 12 Elm Street, call me on 555 0100."):
        session.say_user(text)
        await structure.wait_idle()
        note = _next_reply_note(structure)

    [hand_off] = [n for n in ctx.ui.state.notes if n.text.startswith("Hand-off:")]
    assert hand_off.block_id == NOTEBOOK  # in the notebook's margin
    assert "Maya Singh, policy H0-44721" in hand_off.text and "12 Elm Street" in hand_off.text
    assert "notebook_write" in note and "two sentences" in note
    assert "packet_ready" in [event["rule_id"] for event in _events(ctx, "rule_fired")]


async def test_the_sketch_and_the_evidence_photo_are_the_built_in_tools_the_panel_shows() -> None:
    ctx, _session_, _ = _session()
    ctx.frames.set_latest(
        FrameSnapshot(
            frame=rtc.VideoFrame(width=2, height=2, type=rtc.VideoBufferType.RGBA, data=bytes(16)),
            source="camera",
            age_s=1.0,
        ),
        jpeg_bytes=b"\xff\xd8\xff\xd9",
    )
    tools = {tool.info.name: tool for tool in build_builtin_tools(ctx, disabled=[], http_enabled=False)}

    assert {"generate_image", "pin_frame", "notebook_write", "escalate_to_human"} <= set(tools)
    await tools["generate_image"](
        _run("generate_image"),
        prompt="A quick hand-drawn pen sketch of a kitchen with a burst pipe under the sink",
    )
    pinned = await tools["pin_frame"](
        _run("pin_frame"), caption="Swollen floorboards by the sink", confirmed=True
    )
    await ctx.background.wait_idle()  # the sketch is drawn in the background

    assert ctx.image_gen is not None and "pen sketch" in ctx.image_gen.prompts[0]  # type: ignore[attr-defined]
    assert '"pinned": true' in pinned
    photo = next(asset for asset in ctx.ui.state.assets if asset.caption == "Swollen floorboards by the sink")
    assert photo.meta == {"source": "camera", "confirmed": "true"}
    sketch = [asset for asset in ctx.ui.state.assets if asset.asset_id != photo.asset_id]
    assert len(sketch) == 1
    assert sketch[0].asset_id in ctx.ui.state.blocks["gallery"]["asset_ids"]  # the Pictures gallery
