"""V6-30 (F-3): `search_knowledge` is registered only when the agent has a knowledge base.

In the V6 demos run the model called it again and again on agents with no knowledge base
("No relevant knowledge found"), spending its tool steps. The session's knowledge client
searches `knowledge.kb_ids` only (`ApiKbClient` answers nothing without one), and a flow
node's knowledge bases are limited to those, so with none the tool can never find anything.
"""

from __future__ import annotations

import pytest
from fakes.fake_ctx import FakePackSessionContext, default_agent_config
from lkap_contracts.agent_config import KnowledgeConfig

from lkap_agent.tools.builtin import build_builtin_tools


def _names(kb_ids: list[str], disabled: list[str] | None = None) -> set[str]:
    config = default_agent_config(knowledge=KnowledgeConfig(kb_ids=kb_ids))
    ctx = FakePackSessionContext(config=config)
    return {tool.info.name for tool in build_builtin_tools(ctx, disabled=disabled or [], http_enabled=False)}


def test_search_knowledge_absent_without_a_knowledge_base() -> None:
    assert "search_knowledge" not in _names([])


@pytest.mark.parametrize("kb_ids", [["kb-1"], ["kb-1", "kb-2"]])
def test_search_knowledge_present_with_a_knowledge_base(kb_ids: list[str]) -> None:
    assert "search_knowledge" in _names(kb_ids)


def test_search_knowledge_still_honours_builtin_disabled() -> None:
    assert "search_knowledge" not in _names(["kb-1"], disabled=["search_knowledge"])


def test_other_builtins_unchanged_without_a_knowledge_base() -> None:
    assert _names([]) == _names(["kb-1"]) - {"search_knowledge"}
