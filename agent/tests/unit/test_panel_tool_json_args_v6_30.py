"""V6-30 (F-2): panel tools read an object or a list of objects sent as JSON text.

Gemini through OpenRouter often sent a JSON string where a tool parameter is a list of objects
(``draw_on_canvas`` shapes, ``notebook_write`` items and fields, ``cart_set`` lines,
``show_chart`` points, ``set_details`` items); each refusal cost a tool step. The text is now
read with strict ``json.loads`` (at most 32 000 characters) and validated exactly as the object
would be; malformed or oversized text is refused with the expected shape in the message.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import pytest
from fakes.fake_ctx import FakePackSessionContext, FakeRunContext, default_agent_config
from livekit.agents import FunctionTool, RunContext
from livekit.agents.llm.utils import build_strict_openai_schema, validated_arguments
from lkap_contracts.agent_config import PanelLayout
from lkap_contracts.ui_protocol import BlockSpec
from pydantic import ValidationError

from lkap_agent.tools.builtin.cart_set import build_cart_set_tool
from lkap_agent.tools.builtin.describe_asset import build_describe_asset_tool
from lkap_agent.tools.builtin.draw_on_canvas import build_draw_on_canvas_tool
from lkap_agent.tools.builtin.notebook_write import build_notebook_write_tool
from lkap_agent.tools.builtin.request_choice import build_request_choice_tool
from lkap_agent.tools.builtin.request_form import build_request_form_tool
from lkap_agent.tools.builtin.request_slot import build_request_slot_tool
from lkap_agent.tools.builtin.set_checklist import build_set_checklist_tool
from lkap_agent.tools.builtin.set_details import build_set_details_tool
from lkap_agent.tools.builtin.set_steps import build_set_steps_tool
from lkap_agent.tools.builtin.show_cards import build_show_cards_tool
from lkap_agent.tools.builtin.show_chart import build_show_chart_tool
from lkap_agent.tools.json_args import MAX_JSON_ARGUMENT_CHARS

_SECTIONS = [
    {"id": "notes", "title": "Notes", "kind": "text"},
    {"id": "todo", "title": "Still needed", "kind": "checklist"},
    {"id": "summary", "title": "Summary", "kind": "details"},
]
_PANEL = PanelLayout(
    blocks=[
        BlockSpec(id="card", type="details"),
        BlockSpec(id="book", type="notebook", config={"sections": _SECTIONS}),
        BlockSpec(id="order", type="cart", config={"currency": "USD"}),
        BlockSpec(id="revenue", type="chart", config={"kind": "bar"}),
        BlockSpec(id="board", type="canvas"),
        BlockSpec(id="checklist", type="checklist"),
    ]
)

Builder = Callable[[Any], FunctionTool[..., Any]]

#: (builder, the list parameter, one valid value, the other arguments the call needs)
CASES: list[tuple[str, Builder, str, list[dict[str, Any]], dict[str, Any]]] = [
    ("set_details", build_set_details_tool, "items", [{"key": "claim_no", "value": "CL-1042"}], {}),
    (
        "notebook_write.items",
        build_notebook_write_tool,
        "items",
        [{"id": "photos", "label": "Photos of the damage"}],
        {"section_id": "todo"},
    ),
    (
        "notebook_write.fields",
        build_notebook_write_tool,
        "fields",
        [{"key": "policy", "value": "H0-44721", "label": "Policy"}],
        {"section_id": "summary"},
    ),
    (
        "cart_set.lines",
        build_cart_set_tool,
        "lines",
        [{"id": "filter", "name": "Water filter", "quantity": 2, "unit_price": 24.5}],
        {},
    ),
    (
        "cart_set.adjustments",
        build_cart_set_tool,
        "adjustments",
        [{"label": "Discount", "amount": -5}],
        {"lines": [{"id": "a", "name": "A", "unit_price": 1}]},
    ),
    ("show_chart", build_show_chart_tool, "points", [{"label": "Q1", "value": 405000}], {}),
    (
        "draw_on_canvas",
        build_draw_on_canvas_tool,
        "shapes",
        [{"kind": "arrow", "points": [{"x": 0.1, "y": 0.1}, {"x": 0.5, "y": 0.5}], "label": "Leak"}],
        {},
    ),
    ("set_checklist", build_set_checklist_tool, "items", [{"id": "photos", "label": "Photos"}], {}),
    ("set_steps", build_set_steps_tool, "steps", [{"id": "photos", "status": "done"}], {}),
    (
        "show_cards",
        build_show_cards_tool,
        "cards",
        [{"id": "gold", "title": "Gold cover", "facts": [{"label": "Excess", "value": "100 GBP"}]}],
        {},
    ),
    (
        "request_form",
        build_request_form_tool,
        "fields",
        [{"name": "policy_number", "label": "Policy number", "required": True}],
        {"block_id": "form"},
    ),
    (
        "request_choice",
        build_request_choice_tool,
        "options",
        [{"id": "minor", "label": "Yes, minor injuries"}],
        {"prompt": "Anyone hurt?"},
    ),
    (
        "request_slot",
        build_request_slot_tool,
        "slots",
        [{"id": "mon", "start": "2026-10-05T09:00:00+01:00", "end": "2026-10-05T10:00:00+01:00"}],
        {"prompt": "When suits you?"},
    ),
    (
        "describe_asset",
        build_describe_asset_tool,
        "fields",
        [{"name": "policy_number", "description": "the policy number"}],
        {"asset_id": "a1"},
    ),
]
_IDS = [case[0] for case in CASES]


def _ctx() -> FakePackSessionContext:
    return FakePackSessionContext(config=default_agent_config(panel=_PANEL))


def _run() -> RunContext[Any]:
    return cast(RunContext[Any], FakeRunContext())


@pytest.mark.parametrize(("name", "builder", "param", "value", "others"), CASES, ids=_IDS)
def test_tool_json_string_argument_equals_object_argument(
    name: str, builder: Builder, param: str, value: list[dict[str, Any]], others: dict[str, Any]
) -> None:
    tool = builder(_ctx())
    as_objects = validated_arguments(tool, {**others, param: value})
    as_text = validated_arguments(tool, {**others, param: json.dumps(value)})
    as_item_text = validated_arguments(tool, {**others, param: [json.dumps(item) for item in value]})
    assert as_text == as_objects
    assert as_item_text == as_objects


@pytest.mark.parametrize(("name", "builder", "param", "value", "others"), CASES, ids=_IDS)
@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[{"key": "a", "value": ', "not valid JSON"),
        ("Policyholder: Maya Singh", "pass a list like"),
        ('{"key": "a"}', "pass a list like"),
        ("[" + " " * MAX_JSON_ARGUMENT_CHARS + "]", "too long"),
        ('["Policyholder: Maya Singh"]', "pass an object like"),
        ("[1, 2]", "Input should be"),
    ],
    ids=["malformed", "plain_text", "one_object", "oversized", "text_item", "not_objects"],
)
def test_tool_bad_json_string_argument_is_refused_with_the_shape(
    name: str,
    builder: Builder,
    param: str,
    value: list[dict[str, Any]],
    others: dict[str, Any],
    text: str,
    message: str,
) -> None:
    tool = builder(_ctx())
    with pytest.raises(ValidationError) as caught:
        validated_arguments(tool, {**others, param: text})
    assert message in str(caught.value)


@pytest.mark.parametrize(("name", "builder", "param", "value", "others"), CASES, ids=_IDS)
def test_tool_schema_still_shows_a_list_of_objects(
    name: str, builder: Builder, param: str, value: list[dict[str, Any]], others: dict[str, Any]
) -> None:
    """The annotation changes validation only: the model is still shown an array of objects."""
    schema = build_strict_openai_schema(builder(_ctx()))["function"]["parameters"]
    prop = schema["properties"][param]
    types = prop["type"] if isinstance(prop["type"], list) else [prop["type"]]
    assert "array" in types and "string" not in types
    assert prop["items"] == {"$ref": prop["items"]["$ref"]}


def test_json_text_validation_keeps_the_bounds() -> None:
    """Nothing is loosened: a point off the board is refused as text exactly as as an object."""
    tool = build_draw_on_canvas_tool(_ctx())
    shapes = [{"kind": "arrow", "points": [{"x": 2, "y": 0.1}, {"x": 0.5, "y": 0.5}]}]
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        validated_arguments(tool, {"shapes": json.dumps(shapes)})
    nested = [{"kind": "arrow", "points": json.dumps([{"x": 0.1, "y": 0.1}, {"x": 0.5, "y": 0.5}])}]
    assert validated_arguments(tool, {"shapes": nested}) == validated_arguments(
        tool, {"shapes": [{"kind": "arrow", "points": [{"x": 0.1, "y": 0.1}, {"x": 0.5, "y": 0.5}]}]}
    )


def test_json_text_null_means_the_default() -> None:
    """A strict schema tells the model to send null for a field it leaves out."""
    tool = build_set_details_tool(_ctx())
    text = json.dumps([{"key": "claim_no", "value": "CL-1042", "label": None}])
    assert validated_arguments(tool, {"items": text}) == validated_arguments(
        tool, {"items": [{"key": "claim_no", "value": "CL-1042"}]}
    )


def test_blank_string_for_an_optional_list_means_not_given() -> None:
    tool = build_notebook_write_tool(_ctx())
    assert validated_arguments(tool, {"section_id": "notes", "text": "hi", "items": " "})["items"] is None


async def test_set_details_json_string_items_fill_the_card() -> None:
    ctx = _ctx()
    tool = build_set_details_tool(ctx)
    text = '[{"key": "policy", "value": "H0-44721", "label": "Policy"}]'
    kwargs = validated_arguments(tool, {"items": text})
    await tool(context=_run(), **kwargs)
    rows = ctx.ui.state.blocks["card"]["items"]
    assert [(row["key"], row["label"], row["value"]) for row in rows] == [("policy", "Policy", "H0-44721")]


async def test_notebook_write_json_string_fields_fill_the_summary() -> None:
    ctx = _ctx()
    tool = build_notebook_write_tool(ctx)
    kwargs = validated_arguments(
        tool, {"section_id": "summary", "fields": '[{"key": "policy", "value": "H0-44721"}]'}
    )
    await tool(context=_run(), **kwargs)
    items = ctx.ui.state.blocks["book"]["sections"]["summary"]["items"]
    assert [(row["key"], row["value"]) for row in items] == [("policy", "H0-44721")]


@pytest.mark.parametrize(
    ("builder", "example"),
    [
        (build_set_details_tool, '"key": "claim_no"'),
        (build_notebook_write_tool, 'items=[{"id": "photos"'),
        (build_cart_set_tool, 'lines=[{"id": "filter"'),
        (build_show_chart_tool, 'points=[{"label": "Q1"'),
        (build_draw_on_canvas_tool, 'shapes=[{"kind": "box"'),
    ],
)
def test_panel_tool_description_shows_an_example_call(builder: Builder, example: str) -> None:
    assert example in builder(_ctx()).info.description
