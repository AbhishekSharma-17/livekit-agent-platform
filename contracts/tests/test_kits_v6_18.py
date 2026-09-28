"""V6-18 (D-V6-26): the tool kit contract — variants, sources, snippets, markers."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from lkap_contracts.kits import (
    KIT_FLOW_ANCHOR,
    KitFlowFragment,
    KitTool,
    KitVariant,
    ToolKit,
    ToolKitInstantiate,
    kit_markers,
    snippet_problems,
)

HTTP_TOOL: dict[str, Any] = {
    "key": "lookup",
    "label": "Look a record up",
    "definition": {
        "kind": "http",
        "name": "record_lookup",
        "description": "Find a record.",
        "parameters": {"type": "object", "properties": {"record_id": {"type": "string"}}},
        "method": "GET",
        "url": "https://records.example.com/api/{{ record_id }}",
    },
    "fake": {"id": "Demo-1"},
}


def _kit(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "record_lookup",
        "name": "Look up a record",
        "summary": "Finds a record by its reference.",
        "default_prefix": "record",
        "default_variant": "rest",
        "variants": [
            {
                "id": "rest",
                "label": "Your API",
                "summary": "An HTTP API.",
                "source": "http",
                "tools": [HTTP_TOOL],
            }
        ],
        "instructions_snippet": "Ask for the reference first, then look it up.",
    }
    base.update(overrides)
    return base


def test_a_kit_parses_and_finds_its_default_variant() -> None:
    kit = ToolKit.model_validate(_kit())
    variant = kit.variant(None)
    assert variant is not None and variant.id == "rest"
    assert kit.variant("nope") is None


def test_the_default_variant_must_exist() -> None:
    with pytest.raises(ValidationError, match="default_variant"):
        ToolKit.model_validate(_kit(default_variant="dataset"))


@pytest.mark.parametrize(
    ("source", "tools", "apps", "ok"),
    [
        ("http", [HTTP_TOOL], [], True),
        ("http", [], [], False),
        ("none", [], [], True),
        ("none", [HTTP_TOOL], [], False),
        (
            "composio_action",
            [],
            [
                {
                    "toolkit": "zendesk",
                    "label": "Zendesk",
                    "actions": [{"slug": "ZENDESK_X", "key": "create", "label": "Open"}],
                }
            ],
            True,
        ),
        ("composio_action", [HTTP_TOOL], [], False),
        ("mcp_preset", [HTTP_TOOL], [], False),
        ("dataset", [HTTP_TOOL], [], False),
    ],
)
def test_a_variants_tools_must_fit_its_source(
    source: str, tools: list[dict[str, Any]], apps: list[dict[str, Any]], ok: bool
) -> None:
    payload = {"id": "v", "label": "V", "summary": "S", "source": source, "tools": tools, "apps": apps}
    if ok:
        KitVariant.model_validate(payload)
    else:
        with pytest.raises(ValidationError, match="do not fit"):
            KitVariant.model_validate(payload)


def test_a_kit_tool_is_a_definition_or_a_template_never_both() -> None:
    KitTool.model_validate({"key": "book", "label": "Book", "template": "cal_com.booking_create"})
    with pytest.raises(ValidationError, match="exactly one"):
        KitTool.model_validate({**HTTP_TOOL, "template": "cal_com.booking_create"})
    with pytest.raises(ValidationError, match="exactly one"):
        KitTool.model_validate({"key": "book", "label": "Book"})


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("Ask for the reference, then call record_lookup.", None),
        ("Open https://records.example.com to see it.", "link"),
        ("Go to www.example.org first.", "link"),
        ("Use the API_KEY from settings.", "key"),
        ("Never read the api key aloud.", "key"),
        ("Close the marker --> now.", "comment"),
        ("Call {{ kit.tool.lookup }}.", "placeholder"),
        ("", "empty"),
    ],
)
def test_snippet_problems_refuse_links_keys_markers_and_placeholders(text: str, problem: str | None) -> None:
    problems = snippet_problems(text)
    if problem is None:
        assert problems == []
    else:
        assert any(problem in p for p in problems), problems


def test_markers_name_the_kit_and_the_prefix() -> None:
    assert kit_markers("record_lookup", "policy") == (
        "<!-- kit:record_lookup:policy -->",
        "<!-- /kit:record_lookup:policy -->",
    )


def test_a_flow_fragment_may_name_the_anchor_in_its_edges() -> None:
    fragment = KitFlowFragment.model_validate(
        {
            "nodes": [
                {"id": "record_step", "kind": "tool", "tool": "record_lookup", "on": {"ok": "record_back"}}
            ],
            "edges": [
                {"id": "record_in", "source": KIT_FLOW_ANCHOR, "target": "record_step"},
                {"id": "record_back", "source": "record_step", "target": KIT_FLOW_ANCHOR},
            ],
        }
    )
    assert fragment.edges[0].source == "@anchor"


def test_the_instantiate_request_checks_the_prefix_and_the_actions() -> None:
    ToolKitInstantiate(agent_id="a", block_prefix="policy")
    with pytest.raises(ValidationError):
        ToolKitInstantiate(agent_id="a", block_prefix="Policy Lookup")
    with pytest.raises(ValidationError, match="1 to 20"):
        ToolKitInstantiate(agent_id="a", actions=[])
