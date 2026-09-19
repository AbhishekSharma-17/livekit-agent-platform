"""The generic pack: satisfies the ``Pack`` protocol with no code tools or hooks."""

from __future__ import annotations

from typing import Any

from livekit.agents import ChatContext, ChatMessage, llm

from packs.base import Pack, PackSessionContext, ToolMeta
from packs.generic.manifest import MANIFEST


class GenericPack:
    """No code tools, no custom UI state, no lifecycle behaviour beyond the platform defaults."""

    manifest = MANIFEST

    def tools(self, ctx: PackSessionContext) -> list[llm.FunctionTool[..., Any]]:
        """The generic pack registers no code tools."""
        return []

    def tool_meta(self) -> list[ToolMeta]:
        """No tools, so no metadata."""
        return []

    def initial_state(self, ctx: PackSessionContext) -> dict[str, Any]:
        """No pack-specific state; ``UiState.custom`` starts empty."""
        return {}

    async def on_session_start(self, ctx: PackSessionContext) -> None:
        """No-op: the platform speaks the greeting and snapshots after this hook (DECISIONS-W2 §D-W2-9a)."""
        return None

    async def on_user_turn_completed(
        self, ctx: PackSessionContext, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        """No-op."""
        return None

    async def on_agent_turn_completed(self, ctx: PackSessionContext, text: str, interrupted: bool) -> None:
        """No-op."""
        return None

    async def on_ui_action(
        self, ctx: PackSessionContext, action: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """No pack-defined UI actions."""
        return {"ok": False, "error": f"generic pack has no ui_action handler for {action!r}"}

    async def on_session_end(self, ctx: PackSessionContext, reason: str) -> None:
        """No-op."""
        return None


PACK: Pack = GenericPack()

__all__ = ["PACK", "GenericPack"]
