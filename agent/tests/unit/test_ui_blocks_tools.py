"""V2-10: the block-writing built-in tools and implicit citations.

The tools run against `FakePackSessionContext` with the **real** `UiChannel`
over `FakeRoom` (the v1 `FakeUiChannel` has no block methods).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from fakes.fake_ctx import FakeBackgroundRunner, FakeKbClient, FakePackSessionContext, default_agent_config
from fakes.fake_room import FakeRemoteParticipant, FakeRoom
from livekit.agents import RunContext, ToolError
from livekit.agents.llm import ToolContext
from livekit.agents.llm._provider_format import google as google_format
from livekit.agents.llm.utils import build_legacy_openai_schema
from lkap_contracts.agent_config import PanelLayout, ToolsConfig
from lkap_contracts.api_models import KbHit
from lkap_contracts.ui_protocol import (
    RPC_AGENT_ACTION,
    RPC_UI_REQUEST,
    AgentAction,
    BlockSpec,
    UiPatchOp,
    UiRequest,
)
from pydantic import ValidationError

from lkap_agent.tools.builtin import BLOCK_TOOL_NAMES, BUILTIN_TOOL_NAMES, build_builtin_tools
from lkap_agent.tools.builtin.request_choice import ChoiceOptionIn, build_request_choice_tool
from lkap_agent.tools.builtin.request_form import FormField, build_request_form_tool, fields_to_schema
from lkap_agent.tools.builtin.resolve_choice import build_resolve_choice_tool
from lkap_agent.tools.builtin.search_knowledge import build_search_knowledge_tool
from lkap_agent.tools.builtin.set_details import DetailIn, build_set_details_tool
from lkap_agent.tools.builtin.set_steps import StepIn, build_set_steps_tool
from lkap_agent.tools.builtin.show_document import build_show_document_tool
from lkap_agent.tools.builtin.show_text import build_show_text_tool, contains_html
from lkap_agent.tools.builtin.table_append import build_table_append_tool
from lkap_agent.tools.builtin.update_block import build_update_block_tool
from lkap_agent.ui.channel import BARGE_IN, UiChannel

BLOCKS = [
    BlockSpec(id="status", type="status", order=0),
    BlockSpec(id="intake", type="form", order=1),
    BlockSpec(id="costs", type="table", order=2),
    BlockSpec(id="photos", type="gallery", order=3),
    BlockSpec(id="doc", type="document", order=4),
    BlockSpec(id="sources", type="kb_citations", order=5),
    # V5-08: the block quartet.
    BlockSpec(id="pick", type="choices", order=6),
    BlockSpec(
        id="claim",
        type="details",
        config={"fields": [{"key": "amount", "label": "Estimated repair", "type": "money"}]},
        order=7,
    ),
    BlockSpec(id="recap", type="markdown", config={"max_chars": 300}, order=8),
    BlockSpec(id="progress", type="steps", config={"steps": [{"id": "intake", "label": "Intake"}]}, order=9),
    # V5-15: consent.
    BlockSpec(id="consent", type="consent", order=10),
]


@dataclass
class _Call:
    call_id: str = "call-1"


@dataclass
class _RunCtx:
    function_call: _Call = field(default_factory=_Call)


def _run_ctx() -> RunContext[Any]:
    return cast(RunContext[Any], _RunCtx())


def _ctx(
    blocks: list[BlockSpec] | None = None, *, mode: Any = "cascaded", **kwargs: Any
) -> tuple[FakePackSessionContext, UiChannel, FakeRoom]:
    room = FakeRoom()
    room.add_remote_participant(FakeRemoteParticipant("web-ui"))
    room.local_participant.rpc_call_responses[RPC_UI_REQUEST] = json.dumps({"ok": True, "payload": {}})
    channel = UiChannel(room, "sess-1")  # type: ignore[arg-type]
    channel.start()
    specs = BLOCKS if blocks is None else blocks
    config = default_agent_config(panel=PanelLayout(blocks=specs), **kwargs)
    channel.init_blocks(specs)
    ctx = FakePackSessionContext(
        pipeline_mode=mode, config=config, ui=cast(Any, channel), room=cast(Any, room)
    )
    return ctx, channel, room


async def _submit(room: FakeRoom, block_id: str, values: dict[str, Any]) -> None:
    raw = AgentAction(
        action="form_submit", payload={"block_id": block_id, "values": values}
    ).model_dump_json()
    await room.local_participant.invoke_rpc(RPC_AGENT_ACTION, raw)


async def _until(predicate: Any) -> None:
    for _ in range(50):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never became true")


# ----------------------------------------------------------- registration


def test_block_tools_registered_only_for_blocks_that_need_them() -> None:
    ctx, _ch, _room = _ctx()
    names = {t.info.name for t in build_builtin_tools(ctx, disabled=[], http_enabled=False)}
    assert set(BLOCK_TOOL_NAMES) <= names
    assert not set(BLOCK_TOOL_NAMES) & set(BUILTIN_TOOL_NAMES)


def test_default_composite_blocks_register_no_block_tools() -> None:
    envelope = [BlockSpec(id=t, type=t) for t in ("status", "notes", "checklist", "activity")]  # type: ignore[arg-type]
    ctx, _ch, _room = _ctx(envelope)
    names = {t.info.name for t in build_builtin_tools(ctx, disabled=[], http_enabled=False)}
    assert not names & set(BLOCK_TOOL_NAMES)


def test_insurance_style_custom_panel_registers_no_block_tools() -> None:
    ctx, _ch, _room = _ctx([])
    ctx.config.panel = PanelLayout(panel_id="insurance_notebook", blocks=[])
    names = {t.info.name for t in build_builtin_tools(ctx, disabled=[], http_enabled=False)}
    assert not names & set(BLOCK_TOOL_NAMES)


def test_builtin_disabled_switches_block_tools_off() -> None:
    ctx, _ch, _room = _ctx(tools=ToolsConfig(builtin_disabled=["request_form", "table_append"]))
    names = {
        t.info.name
        for t in build_builtin_tools(ctx, disabled=["request_form", "table_append"], http_enabled=False)
    }
    assert "request_form" not in names and "table_append" not in names
    assert {"update_block", "show_document"} <= names


@pytest.mark.parametrize("use_parameters_json_schema", [True, False])
def test_block_tool_schemas_have_no_property_less_objects_for_gemini(
    use_parameters_json_schema: bool,
) -> None:
    """A free-form `dict` parameter becomes an OBJECT with no properties, which Gemini rejects.

    Since livekit-agents 1.8.3 (#7312) the text API gets `parameters_json_schema` and Gemini
    Live (`use_parameters_json_schema=False`) the simplified `parameters`; both are walked.
    """
    ctx, _ch, _room = _ctx()
    tools = [
        t
        for t in build_builtin_tools(ctx, disabled=[], http_enabled=False)
        if t.info.name in BLOCK_TOOL_NAMES
    ]
    declarations = google_format.to_fnc_ctx(
        ToolContext(tools), use_parameters_json_schema=use_parameters_json_schema
    )

    def walk(schema: Any) -> None:
        if isinstance(schema, dict):
            if str(schema.get("type")).upper().endswith("OBJECT"):
                assert schema.get("properties"), schema
            for value in schema.values():
                walk(value)
        elif isinstance(schema, list):
            for value in schema:
                walk(value)

    for declaration in declarations:
        key = "parameters_json_schema" if use_parameters_json_schema else "parameters"
        assert key in declaration, declaration
        walk(declaration.get("parameters_json_schema") or declaration.get("parameters"))


def test_tool_descriptions_list_the_panel_blocks() -> None:
    ctx, _ch, _room = _ctx()
    schema = build_legacy_openai_schema(build_table_append_tool(ctx))
    assert "costs (table)" in schema["function"]["description"]
    assert "column keys" in schema["function"]["parameters"]["properties"]["row"]["description"]


# ------------------------------------------------------------ update_block


async def test_update_block_sets_each_field_on_the_block() -> None:
    ctx, channel, _room = _ctx()
    tool = build_update_block_tool(ctx)
    await channel.set_block("photos", {"asset_ids": ["a1", "a2"]})
    assert await tool(context=_run_ctx(), block_id="photos", patch='{"selected": "a2"}') == "Updated photos."
    assert channel.state.blocks["photos"] == {"asset_ids": ["a1", "a2"], "selected": "a2"}


@pytest.mark.parametrize(
    ("block_id", "patch", "message"),
    [
        ("nope", '{"a": 1}', "Unknown block"),
        ("status", '{"a": 1}', "set_status"),
        ("photos", "not json", "JSON object"),
        ("photos", "[1]", "JSON object"),
        ("photos", "{}", "empty"),
        ("photos", '{"asset_ids": 3}', "does not fit"),
        ("intake", '{"values": {"a": 1}}', "request_form"),
    ],
)
async def test_update_block_rejects_bad_calls(block_id: str, patch: str, message: str) -> None:
    ctx, _channel, _room = _ctx()
    tool = build_update_block_tool(ctx)
    with pytest.raises(ToolError, match=message):
        await tool(context=_run_ctx(), block_id=block_id, patch=patch)


# ------------------------------------------------------------ table_append


async def test_table_append_adds_row_with_id_and_new_columns() -> None:
    ctx, channel, _room = _ctx()
    tool = build_table_append_tool(ctx)
    await tool(context=_run_ctx(), block_id="costs", row='{"item": "Tyre", "cost": 120, "covered": true}')
    await tool(context=_run_ctx(), block_id="costs", row='{"id": "r2", "item": "Door", "note": "dent"}')

    state = channel.state.blocks["costs"]
    assert [c["key"] for c in state["columns"]] == ["item", "cost", "covered", "note"]
    assert [c["type"] for c in state["columns"]] == ["string", "number", "boolean", "string"]
    assert state["rows"][0]["id"] and state["rows"][0]["item"] == "Tyre"
    assert state["rows"][1] == {"id": "r2", "item": "Door", "note": "dent"}


async def test_table_append_falls_back_to_the_only_table() -> None:
    ctx, channel, _room = _ctx()
    await build_table_append_tool(ctx)(context=_run_ctx(), block_id="", row='{"a": 1}')
    assert len(channel.state.blocks["costs"]["rows"]) == 1


async def test_table_append_with_two_tables_needs_a_valid_id() -> None:
    ctx, _channel, _room = _ctx([BlockSpec(id="t1", type="table"), BlockSpec(id="t2", type="table")])
    with pytest.raises(ToolError, match="t1, t2"):
        await build_table_append_tool(ctx)(context=_run_ctx(), block_id="t3", row='{"a": 1}')


# ----------------------------------------------------------- show_document


async def test_show_document_sets_the_block_and_asks_to_show_it() -> None:
    ctx, channel, room = _ctx()
    tool = build_show_document_tool(ctx)
    result = await tool(context=_run_ctx(), url="https://example.com/policy.pdf", page=3, note="Deductible")
    assert "page 3" in result
    assert channel.state.blocks["doc"] == {
        "asset_id": None,
        "url": "https://example.com/policy.pdf",
        "page": 3,
        "highlights": [{"page": 3, "bbox": [0.0, 0.0, 1.0, 1.0], "note": "Deductible"}],
    }
    await _until(lambda: room.local_participant.rpc_calls)
    assert UiRequest.model_validate_json(room.local_participant.rpc_calls[-1].payload).method == "show_block"


async def test_show_document_accepts_a_shared_asset() -> None:
    ctx, channel, _room = _ctx()
    asset_id = await channel.push_asset(b"%PDF", "application/pdf", "document")
    await build_show_document_tool(ctx)(context=_run_ctx(), asset_id=asset_id)
    assert channel.state.blocks["doc"]["asset_id"] == asset_id


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({}, "exactly one"),
        ({"url": "https://a", "asset_id": "x"}, "exactly one"),
        ({"url": "http://example.com/a.pdf"}, "https"),
        ({"url": "javascript:alert(1)"}, "https"),
        ({"asset_id": "missing"}, "No shared file"),
    ],
)
async def test_show_document_rejects_bad_sources(kwargs: dict[str, Any], message: str) -> None:
    ctx, _channel, _room = _ctx()
    with pytest.raises(ToolError, match=message):
        await build_show_document_tool(ctx)(context=_run_ctx(), **kwargs)


# ------------------------------------------------------------ request_form


def test_fields_to_schema_maps_types_labels_and_required() -> None:
    schema = fields_to_schema(
        [
            FormField(name="policy", label="Policy number", required=True),
            FormField(name="date", label="Date of loss", type="date"),
            FormField(name="email", label="Email", type="email"),
            FormField(name="cost", label="Cost", type="number"),
            FormField(name="kind", label="Kind", type="select", options=["auto", "home"]),
            FormField(name="ok", label="Injured?", type="boolean"),
        ]
    )
    assert schema == {
        "type": "object",
        "properties": {
            "policy": {"title": "Policy number", "type": "string"},
            "date": {"title": "Date of loss", "type": "string", "format": "date"},
            "email": {"title": "Email", "type": "string", "format": "email"},
            "cost": {"title": "Cost", "type": "number"},
            "kind": {"title": "Kind", "type": "string", "enum": ["auto", "home"]},
            "ok": {"title": "Injured?", "type": "boolean"},
        },
        "required": ["policy"],
    }


async def test_request_form_cascaded_blocks_until_submitted() -> None:
    ctx, channel, room = _ctx()
    tool = build_request_form_tool(ctx)
    task = asyncio.create_task(
        tool(context=_run_ctx(), block_id="intake", fields=[FormField(name="policy", label="Policy")])
    )
    await _until(lambda: channel.state.blocks["intake"]["status"] == "requested")
    assert not task.done()
    await _submit(room, "intake", {"policy": "P-1"})
    assert json.loads(await task) == {"submitted": True, "values": {"policy": "P-1"}}


async def test_request_form_cascaded_reports_a_missed_form(monkeypatch: pytest.MonkeyPatch) -> None:
    from lkap_agent.tools.builtin import request_form as module  # noqa: PLC0415

    monkeypatch.setattr(module, "FORM_TIMEOUT_S", 0.01)
    ctx, _channel, _room = _ctx()
    result = await build_request_form_tool(ctx)(
        context=_run_ctx(), block_id="intake", fields=[FormField(name="p", label="P")]
    )
    assert "did not submit" in result


@pytest.mark.parametrize("mode", ["realtime", "half_cascade"])
async def test_request_form_realtime_returns_none_and_values_arrive_as_urgent_result(mode: Any) -> None:
    ctx, channel, room = _ctx(mode=mode)
    background = cast(FakeBackgroundRunner, ctx.background)
    tool = build_request_form_tool(ctx)

    result = await tool(
        context=_run_ctx(), block_id="intake", fields=[FormField(name="policy", label="Policy")]
    )
    assert result is None
    await _until(lambda: channel.state.blocks["intake"]["status"] == "requested")
    assert list(background.jobs) == ["call-1"]

    await _submit(room, "intake", {"policy": "P-9"})
    await background.wait_idle()
    ((job_id, values, instructions),) = background.urgent_events
    assert (job_id, values) == ("call-1", {"policy": "P-9"})
    assert instructions is not None and "P-9" in instructions


async def test_request_form_realtime_missed_form_is_a_routine_note(monkeypatch: pytest.MonkeyPatch) -> None:
    from lkap_agent.tools.builtin import request_form as module  # noqa: PLC0415

    monkeypatch.setattr(module, "FORM_TIMEOUT_S", 0.01)
    ctx, _channel, _room = _ctx(mode="realtime")
    background = cast(FakeBackgroundRunner, ctx.background)
    await build_request_form_tool(ctx)(
        context=_run_ctx(), block_id="intake", fields=[FormField(name="p", label="P")]
    )
    await background.wait_idle()
    assert background.urgent_events == []
    assert background.routine_notes == [("call-1", "The user did not submit the intake form.")]


@pytest.mark.parametrize("mode", ["cascaded", "realtime", "half_cascade"])
async def test_request_form_on_the_text_channel_answers_at_once_without_showing_a_form(mode: Any) -> None:
    """Asks #30 / B-5: a typed chat cannot submit a form, so the turn must not wait on one."""
    ctx, channel, room = _ctx(mode=mode)
    ctx.channel = "text"  # type: ignore[attr-defined]
    background = cast(FakeBackgroundRunner, ctx.background)
    rpcs_before = len(room.local_participant.rpc_calls)

    result = await asyncio.wait_for(
        build_request_form_tool(ctx)(
            context=_run_ctx(),
            block_id="intake",
            fields=[
                FormField(name="name", label="Full name", required=True),
                FormField(name="country", label="Country"),
            ],
        ),
        timeout=1,
    )

    assert isinstance(result, str)
    assert "text chat" in result and "Full name (required)" in result and "Country" in result
    assert channel.state.blocks["intake"].get("status") != "requested"
    assert list(background.jobs) == []
    assert len(room.local_participant.rpc_calls) == rpcs_before  # no form RPC to the browser


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ([], "at least one"),
        ([FormField(name="a", label="A"), FormField(name="a", label="B")], "unique"),
        ([FormField(name="k", label="K", type="select")], "options"),
    ],
)
async def test_request_form_rejects_bad_fields(fields: list[FormField], message: str) -> None:
    ctx, _channel, _room = _ctx()
    with pytest.raises(ToolError, match=message):
        await build_request_form_tool(ctx)(context=_run_ctx(), block_id="intake", fields=fields)


# --------------------------------------------------------- implicit citations


async def test_search_knowledge_cites_into_kb_citations_blocks() -> None:
    hit = KbHit(chunk_id="c1", document_id="d1", filename="policy.pdf", score=0.8, text="Deductible 500")
    ctx, channel, _room = _ctx()
    ctx.kb = FakeKbClient([hit])
    await build_search_knowledge_tool(ctx)(context=_run_ctx(), query="deductible")
    assert channel.state.blocks["sources"]["items"][0]["filename"] == "policy.pdf"


async def test_search_knowledge_without_citation_block_sends_nothing() -> None:
    hit = KbHit(chunk_id="c1", document_id="d1", filename="policy.pdf", score=0.8, text="x")
    ctx, _channel, room = _ctx([BlockSpec(id="notes", type="notes")])
    ctx.kb = FakeKbClient([hit])
    await build_search_knowledge_tool(ctx)(context=_run_ctx(), query="deductible")
    assert room.local_participant.sent_text == []


async def test_search_knowledge_with_a_v1_fake_channel_still_answers() -> None:
    hit = KbHit(chunk_id="c1", document_id="d1", filename="policy.pdf", score=0.8, text="x")
    ctx = FakePackSessionContext(
        config=default_agent_config(panel=PanelLayout(blocks=[BlockSpec(id="s", type="kb_citations")])),
        kb=FakeKbClient([hit]),
    )
    result = await build_search_knowledge_tool(ctx)(context=_run_ctx(), query="q")
    assert json.loads(result)[0]["source"] == "policy.pdf"


# ================================================================ V5-08: the block quartet

OPTIONS = [
    ChoiceOptionIn(id="no", label="No"),
    ChoiceOptionIn(id="minor", label="Yes, minor"),
    ChoiceOptionIn(id="serious", label="Yes, serious", hint="We call you back first"),
]


async def _block_submit(room: FakeRoom, block_id: str, values: dict[str, Any]) -> None:
    raw = AgentAction(
        action="block_submit", payload={"block_id": block_id, "values": values}
    ).model_dump_json()
    await room.local_participant.invoke_rpc(RPC_AGENT_ACTION, raw)


def _names(ctx: FakePackSessionContext) -> set[str]:
    return {t.info.name for t in build_builtin_tools(ctx, disabled=[], http_enabled=False)}


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        (BlockSpec(id="c", type="choices"), {"request_choice", "resolve_choice"}),
        (BlockSpec(id="d", type="details"), {"set_details", "update_block"}),
        (BlockSpec(id="m", type="markdown"), {"show_text", "update_block"}),
        (BlockSpec(id="s", type="steps"), {"set_steps", "update_block"}),
        (BlockSpec(id="s", type="steps", config={"source": "flow"}), {"update_block"}),
    ],
)
def test_quartet_tools_register_only_for_their_block(block: BlockSpec, expected: set[str]) -> None:
    ctx, _ch, _room = _ctx([block])
    assert _names(ctx) & set(BLOCK_TOOL_NAMES) == expected


def test_quartet_tools_honour_builtin_disabled() -> None:
    ctx, _ch, _room = _ctx()
    names = {
        t.info.name
        for t in build_builtin_tools(ctx, disabled=["request_choice", "show_text"], http_enabled=False)
    }
    assert "request_choice" not in names and "show_text" not in names
    assert {"resolve_choice", "set_details", "set_steps"} <= names


# ----------------------------------------------------------- request_choice


async def test_request_choice_writes_the_options_and_returns_the_tapped_option() -> None:
    ctx, channel, room = _ctx()
    task = asyncio.create_task(
        build_request_choice_tool(ctx)(context=_run_ctx(), prompt="Was anyone injured?", options=OPTIONS)
    )
    await _until(lambda: channel.state.blocks["pick"]["status"] == "requested")
    state = channel.state.blocks["pick"]
    assert state["prompt"] == "Was anyone injured?"
    assert [o["id"] for o in state["options"]] == ["no", "minor", "serious"]
    assert state["options"][2]["hint"] == "We call you back first"
    assert state["multi"] is False and state["selected"] == []
    await _until(lambda: room.local_participant.rpc_calls)
    request = UiRequest.model_validate_json(room.local_participant.rpc_calls[-1].payload)
    assert (request.method, request.payload["block_id"]) == ("request", "pick")

    await _block_submit(room, "pick", {"selected": ["minor"]})
    assert json.loads(await task) == {"selected": ["minor"], "labels": ["Yes, minor"]}
    assert channel.state.blocks["pick"]["status"] == "submitted"
    assert channel.state.blocks["pick"]["selected"] == ["minor"]


async def test_request_choice_times_out_to_a_not_picked_line(monkeypatch: pytest.MonkeyPatch) -> None:
    from lkap_agent.tools.builtin import request_choice as module  # noqa: PLC0415

    monkeypatch.setattr(module, "CHOICE_TIMEOUT_S", 0.01)
    ctx, channel, _room = _ctx()
    result = await build_request_choice_tool(ctx)(context=_run_ctx(), prompt="Injured?", options=OPTIONS)
    assert isinstance(result, str) and "did not pick" in result and "resolve_choice" in result
    assert channel.state.blocks["pick"]["status"] == "cancelled"


async def test_request_choice_is_cancelled_when_the_caller_barges_in() -> None:
    ctx, channel, _room = _ctx()
    task = asyncio.create_task(
        build_request_choice_tool(ctx)(context=_run_ctx(), prompt="Injured?", options=OPTIONS)
    )
    await _until(lambda: channel.pending_requests == {"pick": "request"})
    assert channel.cancel_pending(BARGE_IN, methods=["request"]) == ["pick"]
    result = await task
    assert isinstance(result, str) and "did not pick" in result
    await _until(lambda: channel.state.blocks["pick"]["status"] == "cancelled")


async def test_request_choice_rejects_an_answer_that_is_not_an_option() -> None:
    ctx, channel, room = _ctx()
    task = asyncio.create_task(
        build_request_choice_tool(ctx)(context=_run_ctx(), prompt="Injured?", options=OPTIONS)
    )
    await _until(lambda: channel.state.blocks["pick"]["status"] == "requested")
    await _block_submit(room, "pick", {"selected": ["no", "minor"]})  # two answers on a single choice
    result = await task
    assert isinstance(result, str) and "did not pick" in result
    assert channel.state.blocks["pick"]["selected"] == []


async def test_request_choice_multi_comes_from_the_call_or_the_block_config() -> None:
    blocks = [BlockSpec(id="pick", type="choices", config={"multi": True})]
    ctx, channel, room = _ctx(blocks)
    assert channel.state.blocks["pick"]["multi"] is True  # seeded from config
    task = asyncio.create_task(
        build_request_choice_tool(ctx)(context=_run_ctx(), prompt="Which rooms?", options=OPTIONS)
    )
    await _until(lambda: channel.state.blocks["pick"]["status"] == "requested")
    assert channel.state.blocks["pick"]["multi"] is True
    await _block_submit(room, "pick", {"selected": ["no", "minor"]})
    assert json.loads(await task)["selected"] == ["no", "minor"]


@pytest.mark.parametrize("mode", ["realtime", "half_cascade"])
async def test_request_choice_realtime_returns_none_and_a_tap_arrives_as_urgent_result(mode: Any) -> None:
    ctx, channel, room = _ctx(mode=mode)
    background = cast(FakeBackgroundRunner, ctx.background)
    result = await build_request_choice_tool(ctx)(context=_run_ctx(), prompt="Injured?", options=OPTIONS)
    assert result is None
    await _until(lambda: channel.state.blocks["pick"]["status"] == "requested")
    assert list(background.jobs) == ["call-1"]

    await _block_submit(room, "pick", {"selected": ["no"]})
    await background.wait_idle()
    ((job_id, answer, instructions),) = background.urgent_events
    assert (job_id, answer["selected"]) == ("call-1", ["no"])
    assert instructions is not None and "No" in instructions


async def test_request_choice_realtime_voice_answer_is_not_announced_back() -> None:
    ctx, channel, _room = _ctx(mode="realtime")
    background = cast(FakeBackgroundRunner, ctx.background)
    await build_request_choice_tool(ctx)(context=_run_ctx(), prompt="Injured?", options=OPTIONS)
    await _until(lambda: channel.pending_requests == {"pick": "request"})

    result = await build_resolve_choice_tool(ctx)(context=_run_ctx(), selected=["serious"])
    assert "Yes, serious" in result
    await background.wait_idle()
    assert background.urgent_events == []
    assert background.routine_notes == []
    assert channel.state.blocks["pick"]["status"] == "submitted"


async def test_request_choice_realtime_missed_pick_is_a_routine_note(monkeypatch: pytest.MonkeyPatch) -> None:
    from lkap_agent.tools.builtin import request_choice as module  # noqa: PLC0415

    monkeypatch.setattr(module, "CHOICE_TIMEOUT_S", 0.01)
    ctx, _channel, _room = _ctx(mode="realtime")
    background = cast(FakeBackgroundRunner, ctx.background)
    await build_request_choice_tool(ctx)(context=_run_ctx(), prompt="Injured?", options=OPTIONS)
    await background.wait_idle()
    assert background.urgent_events == []
    assert background.routine_notes == [
        ("call-1", "The caller did not pick an option in the pick block on screen.")
    ]


@pytest.mark.parametrize("channel_name", ["sip_in", "sip_out"])
async def test_request_choice_on_a_phone_call_is_voice_only(channel_name: str) -> None:
    ctx, channel, room = _ctx()
    ctx.channel = channel_name  # type: ignore[attr-defined]
    result = await build_request_choice_tool(ctx)(context=_run_ctx(), prompt="Injured?", options=OPTIONS)
    assert json.loads(cast(str, result)) == {"channel": "voice_only"}
    assert channel.state.blocks["pick"]["status"] == "idle"
    assert room.local_participant.rpc_calls == []


async def test_request_choice_on_the_text_channel_asks_in_the_conversation() -> None:
    ctx, channel, _room = _ctx(mode="realtime")
    ctx.channel = "text"  # type: ignore[attr-defined]
    result = await asyncio.wait_for(
        build_request_choice_tool(ctx)(context=_run_ctx(), prompt="Injured?", options=OPTIONS), timeout=1
    )
    assert isinstance(result, str) and "text chat" in result and "Yes, minor" in result
    assert channel.state.blocks["pick"]["status"] == "idle"


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (OPTIONS[:1], "at least two"),
        ([ChoiceOptionIn(id="a", label="A"), ChoiceOptionIn(id="a", label="B")], "unique"),
        ([ChoiceOptionIn(id="a", label="A"), ChoiceOptionIn(id="b", label=" ")], "label"),
        ([ChoiceOptionIn(id=f"o{i}", label=f"O{i}") for i in range(9)], "at most 8"),
    ],
)
async def test_request_choice_rejects_bad_options(options: list[ChoiceOptionIn], message: str) -> None:
    ctx, _channel, _room = _ctx()
    with pytest.raises(ToolError, match=message):
        await build_request_choice_tool(ctx)(context=_run_ctx(), prompt="Q?", options=options)


async def test_request_choice_honours_the_block_max_options() -> None:
    ctx, _channel, _room = _ctx([BlockSpec(id="pick", type="choices", config={"max_options": 2})])
    with pytest.raises(ToolError, match="at most 2"):
        await build_request_choice_tool(ctx)(context=_run_ctx(), prompt="Q?", options=OPTIONS)


async def test_late_choice_submit_reaches_the_unsolicited_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    from lkap_agent.tools.builtin import request_choice as module  # noqa: PLC0415

    monkeypatch.setattr(module, "CHOICE_TIMEOUT_S", 0.01)
    ctx, channel, room = _ctx()
    late: list[tuple[str, dict[str, Any]]] = []

    async def _late(block_id: str, values: dict[str, Any]) -> None:
        late.append((block_id, values))

    channel.bind(on_unsolicited_form=_late)
    await build_request_choice_tool(ctx)(context=_run_ctx(), prompt="Injured?", options=OPTIONS)
    await _block_submit(room, "pick", {"selected": ["no"]})
    assert late == [("pick", {"selected": ["no"]})]
    assert channel.state.blocks["pick"]["selected"] == ["no"]
    assert channel.state.blocks["pick"]["status"] == "submitted"


# ----------------------------------------------------------- resolve_choice


async def test_resolve_choice_flips_a_pending_request_to_the_spoken_answer() -> None:
    ctx, channel, _room = _ctx()
    task = asyncio.create_task(
        build_request_choice_tool(ctx)(context=_run_ctx(), prompt="Injured?", options=OPTIONS)
    )
    await _until(lambda: channel.pending_requests == {"pick": "request"})
    result = await build_resolve_choice_tool(ctx)(context=_run_ctx(), selected=["no"])
    assert result == "Recorded the caller's choice: No."
    assert json.loads(await task) == {"selected": ["no"], "labels": ["No"]}
    state = channel.state.blocks["pick"]
    assert (state["status"], state["selected"]) == ("submitted", ["no"])
    assert state["submitted_at"] is not None


async def test_resolve_choice_after_a_barge_in_still_records_the_answer() -> None:
    ctx, channel, _room = _ctx()
    late: list[str] = []

    async def _late(block_id: str, values: dict[str, Any]) -> None:
        late.append(block_id)

    channel.bind(on_unsolicited_form=_late)
    task = asyncio.create_task(
        build_request_choice_tool(ctx)(context=_run_ctx(), prompt="Injured?", options=OPTIONS)
    )
    await _until(lambda: channel.pending_requests == {"pick": "request"})
    channel.cancel_pending(BARGE_IN, methods=["request"])
    await task
    await build_resolve_choice_tool(ctx)(context=_run_ctx(), selected=["minor"])
    await _until(lambda: channel.state.blocks["pick"]["status"] == "submitted")
    assert channel.state.blocks["pick"]["selected"] == ["minor"]
    assert late == []  # the model gave the answer; it is not prompted about it again


@pytest.mark.parametrize(("selected", "message"), [(["maybe"], "unknown option"), ([], "at least one")])
async def test_resolve_choice_rejects_answers_that_are_not_options(selected: list[str], message: str) -> None:
    ctx, _channel, _room = _ctx()
    await _write_options(ctx)
    with pytest.raises(ToolError, match=message):
        await build_resolve_choice_tool(ctx)(context=_run_ctx(), selected=selected)


async def _write_options(ctx: FakePackSessionContext) -> None:
    await ctx.ui.patch_block(
        "pick",
        [
            UiPatchOp(op="set", path="/prompt", value="Injured?"),
            UiPatchOp(op="set", path="/options", value=[o.model_dump(exclude={"hint"}) for o in OPTIONS]),
        ],
    )


# ------------------------------------------------------------- set_details


async def test_set_details_upserts_by_key_keeping_the_configured_label_and_type() -> None:
    ctx, channel, _room = _ctx()
    assert channel.state.blocks["claim"]["items"] == [
        {
            "key": "amount",
            "label": "Estimated repair",
            "value": None,
            "type": "money",
            "tone": None,
            "updated_at": None,
        }
    ]
    tool = build_set_details_tool(ctx)
    await tool(
        context=_run_ctx(),
        items=[
            DetailIn(key="amount", value="4,200"),
            DetailIn(key="claim_no", value="CLM-1", label="Claim number"),
        ],
    )
    await tool(context=_run_ctx(), items=[DetailIn(key="claim_no", value="CLM-2")])
    items = {i["key"]: i for i in channel.state.blocks["claim"]["items"]}
    assert list(items) == ["amount", "claim_no"]
    assert (items["amount"]["label"], items["amount"]["type"], items["amount"]["value"]) == (
        "Estimated repair",
        "money",
        4200.0,
    )
    assert (items["claim_no"]["label"], items["claim_no"]["value"]) == ("Claim number", "CLM-2")
    assert items["claim_no"]["updated_at"] is not None


async def test_set_details_empty_value_clears_it_and_a_new_key_gets_a_label() -> None:
    ctx, channel, _room = _ctx()
    tool = build_set_details_tool(ctx)
    await tool(context=_run_ctx(), items=[DetailIn(key="date_of_loss", value="")])
    [row] = [i for i in channel.state.blocks["claim"]["items"] if i["key"] == "date_of_loss"]
    assert (row["label"], row["value"]) == ("Date of loss", None)


@pytest.mark.parametrize(
    ("items", "message"),
    [([], "at least one"), ([DetailIn(key="a", value="1"), DetailIn(key="a", value="2")], "unique")],
)
async def test_set_details_rejects_bad_items(items: list[DetailIn], message: str) -> None:
    ctx, _channel, _room = _ctx()
    with pytest.raises(ToolError, match=message):
        await build_set_details_tool(ctx)(context=_run_ctx(), items=items)


# ---------------------------------------------------------------- show_text


async def test_show_text_replaces_the_text_and_asks_to_show_the_block() -> None:
    ctx, channel, room = _ctx()
    result = await build_show_text_tool(ctx)(
        context=_run_ctx(), markdown="## Next\n\n1. **Photos**", title="Recap"
    )
    assert "one-sentence summary" in result
    state = channel.state.blocks["recap"]
    assert (state["markdown"], state["title"]) == ("## Next\n\n1. **Photos**", "Recap")
    assert state["updated_at"] is not None
    await _until(lambda: room.local_participant.rpc_calls)
    assert UiRequest.model_validate_json(room.local_participant.rpc_calls[-1].payload).method == "show_block"


@pytest.mark.parametrize(
    ("markdown", "message"),
    [
        ("<script>alert(1)</script>", "HTML"),
        ("Hello <b>there</b>", "HTML"),
        ('<img src="x" onerror="y">', "HTML"),
        ("a <!-- hidden --> b", "HTML"),
        ("x" * 301, "at most 300"),
        ("   ", "Pass the text"),
    ],
)
async def test_show_text_refuses_raw_html_and_over_long_text(markdown: str, message: str) -> None:
    ctx, channel, _room = _ctx()
    with pytest.raises(ToolError, match=message):
        await build_show_text_tool(ctx)(context=_run_ctx(), markdown=markdown)
    assert channel.state.blocks["recap"]["markdown"] == ""


@pytest.mark.parametrize(
    ("text", "html"),
    [
        ("See <https://example.com/policy>", False),
        ("if a < b and b > c", False),
        ("x < 3 > y", False),
        ("<br>", True),
        ("</div>", True),
    ],
)
def test_contains_html_ignores_autolinks_and_comparisons(text: str, html: bool) -> None:
    assert contains_html(text) is html


async def test_show_text_on_a_phone_call_is_not_visible() -> None:
    ctx, channel, _room = _ctx()
    ctx.channel = "sip_in"  # type: ignore[attr-defined]
    result = await build_show_text_tool(ctx)(context=_run_ctx(), markdown="Recap")
    assert json.loads(result) == {"visible": False}
    assert channel.state.blocks["recap"]["markdown"] == ""


# ---------------------------------------------------------------- set_steps


async def test_set_steps_merges_by_id_and_tracks_the_current_step() -> None:
    ctx, channel, _room = _ctx()
    assert channel.state.blocks["progress"] == {
        "steps": [{"id": "intake", "label": "Intake", "status": "pending", "note": None, "at": None}],
        "current": None,
    }
    tool = build_set_steps_tool(ctx)
    await tool(
        context=_run_ctx(),
        steps=[StepIn(id="intake", status="done", note="Policy found"), StepIn(id="photos", status="active")],
    )
    state = channel.state.blocks["progress"]
    assert [(s["id"], s["status"]) for s in state["steps"]] == [("intake", "done"), ("photos", "active")]
    assert state["steps"][0]["note"] == "Policy found" and state["steps"][0]["at"] is not None
    assert state["steps"][1]["label"] == "Photos"
    assert state["current"] == "photos"


async def test_set_steps_rejects_an_unknown_current_step() -> None:
    ctx, _channel, _room = _ctx()
    with pytest.raises(ToolError, match="current names no step"):
        await build_set_steps_tool(ctx)(context=_run_ctx(), steps=[StepIn(id="intake")], current="nope")


def test_set_steps_validates_statuses() -> None:
    with pytest.raises(ValidationError):
        StepIn.model_validate({"id": "a", "status": "later"})
    ctx, _channel, _room = _ctx()
    schema = build_legacy_openai_schema(build_set_steps_tool(ctx))
    step = schema["function"]["parameters"]["$defs"]["StepIn"]["properties"]["status"]
    assert step["enum"] == ["pending", "active", "done", "skipped", "failed"]


async def test_set_steps_refuses_a_block_that_follows_the_flow() -> None:
    blocks = [
        BlockSpec(id="flow_steps", type="steps", config={"source": "flow"}),
        BlockSpec(id="mine", type="steps"),
    ]
    ctx, channel, _room = _ctx(blocks)
    tool = build_set_steps_tool(ctx)
    await tool(context=_run_ctx(), steps=[StepIn(id="a", status="active")], block_id="flow_steps")
    assert channel.state.blocks["flow_steps"]["steps"] == []  # untouched: the only manual block got it
    assert channel.state.blocks["mine"]["current"] == "a"
