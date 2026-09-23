"""The worker's built-in tool names equal the shared `lkap_contracts.tools` lists (asks V2-16-3).

`lkap_contracts.tools` is the single source the api (flow validation) and the web
(`generated/builtin_tools.json`) read; until `tools/builtin/__init__.py` imports
the constants from there, this test keeps the worker's own copies equal to them.
"""

from __future__ import annotations

from lkap_contracts import tools as shared

from lkap_agent.tools.builtin import BLOCK_TOOL_NAMES, BUILTIN_TOOL_NAMES
from lkap_agent.tools.builtin.update_block import UPDATABLE_BLOCK_TYPES


def test_builtin_tool_names_match_the_contract() -> None:
    assert tuple(BUILTIN_TOOL_NAMES) == shared.BUILTIN_TOOL_NAMES


def test_block_tool_names_match_the_contract() -> None:
    assert tuple(BLOCK_TOOL_NAMES) == shared.BLOCK_TOOL_NAMES
    assert set(shared.BLOCK_TOOL_TYPES) == set(BLOCK_TOOL_NAMES)


def test_updatable_block_types_match_the_contract() -> None:
    assert frozenset(UPDATABLE_BLOCK_TYPES) == shared.UPDATABLE_BLOCK_TYPES
    assert shared.BLOCK_TOOL_TYPES["update_block"] == shared.UPDATABLE_BLOCK_TYPES
