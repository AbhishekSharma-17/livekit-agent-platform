"""The worker's built-in tool names equal the shared `lkap_contracts.tools` lists (asks V2-16-3).

`lkap_contracts.tools` is the single source the api (flow validation) and the web
(`generated/builtin_tools.json`) read; until `tools/builtin/__init__.py` imports
the constants from there, this test keeps the worker's own copies equal to them.
"""

from __future__ import annotations

from fakes.fake_ctx import FakePackSessionContext, default_agent_config
from lkap_contracts import tools as shared
from lkap_contracts.agent_config import PanelLayout
from lkap_contracts.ui_protocol import BlockSpec

from lkap_agent.tools.builtin import BLOCK_TOOL_NAMES, BUILTIN_TOOL_NAMES, build_builtin_tools
from lkap_agent.tools.builtin.update_block import UPDATABLE_BLOCK_TYPES


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


def test_each_block_tool_registers_for_exactly_its_contract_block_types() -> None:
    every_type = set().union(*shared.BLOCK_TOOL_TYPES.values())
    for name, types in shared.BLOCK_TOOL_TYPES.items():
        for block_type in sorted(every_type):
            registered = name in _registered_block_tools({block_type})
            assert registered is (block_type in types), (name, block_type)
