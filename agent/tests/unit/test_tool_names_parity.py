"""The worker's built-in tool names equal the shared `lkap_contracts.tools` lists (asks V2-16-3).

`lkap_contracts.tools` is the single source the api (flow validation) and the web
(`generated/builtin_tools.json`) read; until `tools/builtin/__init__.py` imports
the constants from there, this test keeps the worker's own copies equal to them.
"""

from __future__ import annotations

import importlib
import inspect

from fakes.fake_ctx import FakePackSessionContext, default_agent_config
from lkap_contracts import tools as shared
from lkap_contracts.agent_config import PanelLayout
from lkap_contracts.ui_protocol import BlockSpec

from lkap_agent.tools.builtin import BLOCK_TOOL_NAMES, BUILTIN_TOOL_NAMES, build_builtin_tools
from lkap_agent.tools.builtin.update_block import UPDATABLE_BLOCK_TYPES
from lkap_agent.tools.untrusted import FENCED_SITES


def test_builtin_tool_names_match_the_contract() -> None:
    assert tuple(BUILTIN_TOOL_NAMES) == shared.BUILTIN_TOOL_NAMES


def test_block_tool_names_match_the_contract() -> None:
    assert tuple(BLOCK_TOOL_NAMES) == shared.BLOCK_TOOL_NAMES
    assert set(shared.BLOCK_TOOL_TYPES) == set(BLOCK_TOOL_NAMES)


def test_updatable_block_types_match_the_contract() -> None:
    assert frozenset(UPDATABLE_BLOCK_TYPES) == shared.UPDATABLE_BLOCK_TYPES
    assert shared.BLOCK_TOOL_TYPES["update_block"] == shared.UPDATABLE_BLOCK_TYPES


def _registered_block_tools(types: set[str]) -> set[str]:
    blocks = [BlockSpec(id=f"b_{t}", type=t) for t in sorted(types)]  # type: ignore[arg-type]
    ctx = FakePackSessionContext(config=default_agent_config(panel=PanelLayout(blocks=blocks)))
    names = {t.info.name for t in build_builtin_tools(ctx, disabled=[], http_enabled=False)}
    return names & set(shared.BLOCK_TOOL_NAMES)


def test_the_worker_registers_exactly_the_contract_block_tools() -> None:
    """V5-08: with a block of every type the worker registers every contract block tool."""
    every_type = set().union(*shared.BLOCK_TOOL_TYPES.values())
    assert _registered_block_tools(every_type) == set(shared.BLOCK_TOOL_NAMES)


#: R-V5-15: built-in tools that hand third-party text to the model; each fences it.
FENCED_BUILTIN_TOOLS: frozenset[str] = frozenset(
    {"search_knowledge", "http_request", "web_search", "fetch_url"}
)
#: Built-in tools whose result is the platform's own words, the caller's own input or a
#: vendor receipt, never third-party content. `describe_asset` frames an uploaded file's
#: description with its own `UNTRUSTED_NOTE`.
UNFENCED_BUILTIN_TOOLS: frozenset[str] = frozenset(
    {
        "end_call",
        "describe_current_frame",
        "pin_frame",
        "push_note",
        "set_status",
        "escalate_to_human",
        "current_time",
        "convert_time",
        "describe_asset",
        "calculate",
        "spell_back",
        "send_sms",
        "notify_team",
        # V5-31: answers with a fixed sentence of the platform's own.
        "switch_language",
    }
)


def test_every_builtin_tool_is_classified_as_fenced_or_not() -> None:
    """R-V5-15: a new built-in tool cannot be added without deciding whether its result is fenced."""
    assert not FENCED_BUILTIN_TOOLS & UNFENCED_BUILTIN_TOOLS
    assert set(BUILTIN_TOOL_NAMES) == FENCED_BUILTIN_TOOLS | UNFENCED_BUILTIN_TOOLS


def test_every_fenced_site_calls_the_fence() -> None:
    """R-V5-15: every module listed as a content site calls `fence`, fenced built-ins included."""
    assert {f"lkap_agent.tools.builtin.{name}" for name in FENCED_BUILTIN_TOOLS} <= set(FENCED_SITES)
    for module_name in FENCED_SITES:
        source = inspect.getsource(importlib.import_module(module_name))
        assert "fence(" in source and "lkap_agent.tools.untrusted import" in source, module_name


def test_each_block_tool_registers_for_exactly_its_contract_block_types() -> None:
    every_type = set().union(*shared.BLOCK_TOOL_TYPES.values())
    for name, types in shared.BLOCK_TOOL_TYPES.items():
        for block_type in sorted(every_type):
            registered = name in _registered_block_tools({block_type})
            assert registered is (block_type in types), (name, block_type)
