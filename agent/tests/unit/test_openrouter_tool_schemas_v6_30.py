"""V6-30 (F-2): OpenRouter requests carry tool schemas with nested objects written out.

OpenRouter's translation of an OpenAI tool schema for Gemini does not follow ``$ref``: in the
V6 demos run Gemini 3.5 Flash was shown ``items: {type: STRING}`` for ``notebook_write``'s
``fields`` and sent text where objects were expected. Nothing here opens a socket: the one
chat request is answered by ``respx``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from livekit.agents import APIConnectOptions, RunContext, function_tool, llm
from livekit.plugins import openai
from lkap_contracts.agent_config import ResolvedProvider
from lkap_contracts.providers import get as get_spec
from pydantic import BaseModel, Field

from lkap_agent.providers.factory import ProviderFactory
from lkap_agent.providers.openrouter_llm import InlineRefToolContext, OpenRouterLLM, inline_schema_refs

API_KEY = "sk-or-v1-test-not-real"


class PointIn(BaseModel):
    x: float = Field(description="Across, 0-1000.")
    y: float = Field(description="Down, 0-1000.")


class ShapeIn(BaseModel):
    kind: str = Field(description="line or rect.")
    points: list[PointIn] = Field(description="The corners.")


class Node(BaseModel):
    label: str
    children: list[Node] = []


async def draw(context: RunContext[Any], shapes: list[ShapeIn], note: PointIn | None = None) -> str:
    """Draw shapes.

    Args:
        shapes: What to draw.
        note: Where the note goes.
    """
    return "ok"


async def tree(context: RunContext[Any], root: Node) -> str:
    """Show a tree.

    Args:
        root: The top node.
    """
    return "ok"


def _refs(value: Any) -> list[str]:
    if isinstance(value, dict):
        found = [value["$ref"]] if isinstance(value.get("$ref"), str) else []
        return found + [ref for item in value.values() for ref in _refs(item)]
    if isinstance(value, list):
        return [ref for item in value for ref in _refs(item)]
    return []


def _openai_schemas(*tools: Any) -> list[dict[str, Any]]:
    context = InlineRefToolContext([function_tool(tool) for tool in tools])
    return context.parse_function_tools("openai", strict=True)


def test_stock_strict_schema_uses_ref_for_nested_models() -> None:
    """The shape OpenRouter mistranslates: the item schema only behind a `$ref`."""
    stock = llm.ToolContext([function_tool(draw)]).parse_function_tools("openai", strict=True)
    assert "#/$defs/ShapeIn" in _refs(stock)


def test_inline_ref_tool_context_writes_nested_models_out() -> None:
    (schema,) = _openai_schemas(draw)
    parameters = schema["function"]["parameters"]
    assert _refs(parameters) == []
    assert "$defs" not in parameters
    shapes = parameters["properties"]["shapes"]
    assert shapes["type"] == "array"
    item = shapes["items"]
    assert item["type"] == "object"
    assert item["additionalProperties"] is False
    assert item["required"] == ["kind", "points"]
    point = item["properties"]["points"]["items"]
    assert point["type"] == "object" and set(point["properties"]) == {"x", "y"}
    assert point["additionalProperties"] is False
    # An optional model stays nullable, the object written out beside `null`.
    options = parameters["properties"]["note"]["anyOf"]
    assert [option["type"] for option in options] == ["object", "null"]
    assert set(options[0]["properties"]) == {"x", "y"}
    assert schema["function"]["strict"] is True


def test_inline_ref_tool_context_keeps_a_recursive_model_as_ref() -> None:
    (schema,) = _openai_schemas(tree)
    parameters = schema["function"]["parameters"]
    root = parameters["properties"]["root"]
    assert root["type"] == "object"
    assert _refs(root) == ["#/$defs/Node"]  # the self-reference cannot be written out
    assert "Node" in parameters["$defs"]


def test_inline_ref_tool_context_leaves_other_formats_alone() -> None:
    tools = [function_tool(draw)]
    assert InlineRefToolContext(tools).parse_function_tools("anthropic") == llm.ToolContext(
        tools
    ).parse_function_tools("anthropic")


def test_inline_schema_refs_keeps_sibling_keywords_and_input() -> None:
    schema = {
        "$defs": {"A": {"type": "object", "properties": {"v": {"type": "string"}}, "description": "a"}},
        "type": "object",
        "properties": {"a": {"$ref": "#/$defs/A", "description": "the a"}},
    }
    inlined = inline_schema_refs(schema)
    assert inlined["properties"]["a"] == {
        "type": "object",
        "properties": {"v": {"type": "string"}},
        "description": "the a",
    }
    assert "$defs" in schema  # the input is untouched
    assert inline_schema_refs({"type": "object"}) == {"type": "object"}


def _resolved(**fields: Any) -> ResolvedProvider:
    spec = get_spec("openrouter-llm")
    kwargs: dict[str, Any] = {f.name: f.default for f in spec.fields if f.default is not None}
    kwargs.update(fields)
    kwargs["api_key"] = API_KEY
    return ResolvedProvider(
        provider_id=spec.id, python_class=spec.python_class, model="google/gemini-3.5-flash", kwargs=kwargs
    )


def test_factory_builds_openrouter_llm_with_inline_tool_schemas() -> None:
    built = ProviderFactory().build("llm", _resolved())
    assert isinstance(built, OpenRouterLLM)
    assert isinstance(built, openai.LLM)
    assert built._opts.extra_body == {"provider": {"require_parameters": True}}


@pytest.mark.parametrize("slot", ["workflow_llm", "llm"])
def test_factory_openrouter_slots_are_all_inlined(slot: str) -> None:
    assert isinstance(ProviderFactory().build(slot, _resolved()), OpenRouterLLM)  # type: ignore[arg-type]


@respx.mock
async def test_openrouter_chat_request_sends_tools_without_refs() -> None:
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": {"message": "stop here"}})
    )
    model = ProviderFactory().build("llm", _resolved())
    chat_ctx = llm.ChatContext.empty()
    chat_ctx.add_message(role="user", content="draw a box")
    stream = model.chat(
        chat_ctx=chat_ctx,
        tools=[function_tool(draw)],
        conn_options=APIConnectOptions(max_retry=0, timeout=5),
    )
    with pytest.raises(Exception):  # noqa: B017 - the mocked 400 ends the stream
        async with stream:
            async for _ in stream:
                pass
    assert route.called
    body = json.loads(route.calls.last.request.content)
    (tool,) = body["tools"]
    assert tool["function"]["name"] == "draw"
    assert _refs(tool) == []
    assert tool["function"]["parameters"]["properties"]["shapes"]["items"]["type"] == "object"
