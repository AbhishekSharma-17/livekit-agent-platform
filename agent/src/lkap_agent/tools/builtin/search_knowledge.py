"""`search_knowledge` built-in tool (docs/ARCHITECTURE.md §7.3, §7.4).

The same `PackSessionContext.kb` (`KbClient` over
`POST /internal/v1/kb/search`) also backs RAG auto-injection in
`PlatformAgent.on_user_turn_completed` (W1-AGENT-CORE, gated by
`AgentConfig.knowledge.auto_inject`); this module is only the explicit
tool-call surface.

V5-06: the search runs with the agent's `KnowledgeConfig.mode`, `rerank` and
`min_score`, bound on the session's `ApiKbClient`
(:func:`lkap_agent.knowledge.search_options`), so the tool and auto-inject
retrieve alike. The tool path skips the auto-inject gate, dedupe and budget:
the model asked for this search. Hits carry their locators (`KbHit.meta`) to
`ctx.ui.cite(...)`, which maps them onto `KbCitation` (V5-08).

v2 (CONTRACTS-V2 §4.4, implicit `cite_sources`): when the panel has a
`kb_citations` block, every non-empty result also replaces that block's
citations, so the user sees what the answer is based on. Citing is best
effort and never changes what the model receives.
"""

from __future__ import annotations

import json
from typing import Any

from livekit.agents import FunctionTool, RunContext, function_tool
from lkap_contracts.api_models import KbHit
from packs.base import PackSessionContext

from lkap_agent.tools.execution import (
    ResolvedExecution,
    ToolPolicy,
    attach_policy,
    blocking_policy,
    run_with_policy,
    tool_flags,
)
from lkap_agent.ui.blocks import block_ids_of_type, session_block_specs

__all__ = ["build_search_knowledge_tool", "cite_sources"]


async def cite_sources(ctx: PackSessionContext, hits: list[KbHit]) -> None:
    """Show `hits` in every `kb_citations` block of the panel; never raises.

    Channels without block support (the no-op channel, v1 test doubles) and
    panels without a `kb_citations` block are skipped silently.
    """
    if not hits:
        return
    cite = getattr(ctx.ui, "cite", None)
    if not callable(cite):
        return
    for block_id in block_ids_of_type(session_block_specs(ctx.ui, ctx.config.panel), "kb_citations"):
        try:
            await cite(block_id, hits)
        except Exception:  # noqa: BLE001 - citations are decoration; the answer must not fail
            ctx.log.debug("cite_sources failed", block_id=block_id, exc_info=True)


def build_search_knowledge_tool(
    ctx: PackSessionContext, *, execution: ResolvedExecution | None = None
) -> FunctionTool[..., Any]:
    """Build the `search_knowledge` tool bound to `ctx`.

    Args:
        ctx: The session's `PackSessionContext`.
        execution: The tool's execution policy (docs/v4/BACKGROUND-TOOLS.md); `None`
            runs it blocking, as before. A warm local store answers in tens of
            milliseconds, so `auto` returns inline; a slow remote store announces.
    """
    policy = execution or blocking_policy("search_knowledge")
    flags, on_duplicate, duplicate_scope = tool_flags(policy)

    async def _search(query: str) -> str:
        # Always the agent's own knowledge bases (kb_ids=None -> the api's default of
        # `knowledge.kb_ids`); DECISIONS-W2 §D-W2-12 removed the `kb` filter.
        hits = await ctx.kb.search(query, k=ctx.config.knowledge.top_k)
        if not hits:
            return "No relevant knowledge found."
        await cite_sources(ctx, hits)
        return json.dumps(
            [{"source": hit.filename, "text": hit.text, "score": round(hit.score, 3)} for hit in hits]
        )

    @function_tool(flags=flags, on_duplicate=on_duplicate, duplicate_scope=duplicate_scope)
    async def search_knowledge(context: RunContext[Any], query: str) -> str:
        """Search the agent's attached knowledge bases for relevant passages.

        Args:
            query: The question or topic to search for.
        """
        result: str = await run_with_policy(context, policy, lambda: _search(query))
        return result

    return attach_policy(search_knowledge, ToolPolicy(resolved=policy))
