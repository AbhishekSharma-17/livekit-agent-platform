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
from lkap_contracts.ui_protocol import RPC_AGENT_ACTION, RPC_UI_REQUEST, AgentAction, BlockSpec, UiRequest

from lkap_agent.tools.builtin import BLOCK_TOOL_NAMES, BUILTIN_TOOL_NAMES, build_builtin_tools
from lkap_agent.tools.builtin.request_form import FormField, build_request_form_tool, fields_to_schema
from lkap_agent.tools.builtin.search_knowledge import build_search_knowledge_tool
from lkap_agent.tools.builtin.show_document import build_show_document_tool
from lkap_agent.tools.builtin.table_append import build_table_append_tool
from lkap_agent.tools.builtin.update_block import build_update_block_tool
from lkap_agent.ui.channel import UiChannel

BLOCKS = [
    BlockSpec(id="status", type="status", order=0),
    BlockSpec(id="intake", type="form", order=1),
    BlockSpec(id="costs", type="table", order=2),
    BlockSpec(id="photos", type="gallery", order=3),
    BlockSpec(id="doc", type="document", order=4),
    BlockSpec(id="sources", type="kb_citations", order=5),
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


def test_block_tool_schemas_have_no_property_less_objects_for_gemini() -> None:
    """A free-form `dict` parameter becomes an OBJECT with no properties, which Gemini rejects."""
    ctx, _ch, _room = _ctx()
    tools = [
        t
        for t in build_builtin_tools(ctx, disabled=[], http_enabled=False)
        if t.info.name in BLOCK_TOOL_NAMES
    ]
    declarations = google_format.to_fnc_ctx(ToolContext(tools))

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
        walk(declaration["parameters"])


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
