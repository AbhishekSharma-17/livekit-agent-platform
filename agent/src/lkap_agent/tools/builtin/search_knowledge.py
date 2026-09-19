"""`search_knowledge` built-in tool (docs/ARCHITECTURE.md §7.3, §7.4).

The same `PackSessionContext.kb` (`KbClient` over
`POST /internal/v1/kb/search`) also backs RAG auto-injection in
`PlatformAgent.on_user_turn_completed` (W1-AGENT-CORE, gated by
`AgentConfig.knowledge.auto_inject`); this module is only the explicit
tool-call surface.
"""

from __future__ import annotations

import json
from typing import Any

from livekit.agents import FunctionTool, RunContext, function_tool
from packs.base import PackSessionContext


def build_search_knowledge_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `search_knowledge` tool bound to `ctx`."""

    @function_tool
    async def search_knowledge(context: RunContext[Any], query: str) -> str:
        """Search the agent's attached knowledge bases for relevant passages.

        Args:
            query: The question or topic to search for.
        """
        # Always the agent's own knowledge bases (kb_ids=None -> the api's default of
        # `knowledge.kb_ids`); DECISIONS-W2 §D-W2-12 removed the `kb` filter.
        hits = await ctx.kb.search(query, k=ctx.config.knowledge.top_k)
        if not hits:
            return "No relevant knowledge found."
        return json.dumps(
            [{"source": hit.filename, "text": hit.text, "score": round(hit.score, 3)} for hit in hits]
        )

    return search_knowledge
