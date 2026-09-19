"""`escalate_to_human` built-in tool — stub escalation path (docs/ARCHITECTURE.md §7.3)."""

from __future__ import annotations

import time
from typing import Any, Literal

from livekit.agents import FunctionTool, RunContext, function_tool
from lkap_contracts.ui_protocol import ActivityEvent
from packs.base import PackSessionContext

from lkap_agent.logging import get_logger

_log = get_logger(__name__)

Urgency = Literal["low", "normal", "high"]


def build_escalate_to_human_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `escalate_to_human` tool bound to `ctx`."""

    @function_tool
    async def escalate_to_human(context: RunContext[Any], reason: str, urgency: Urgency = "normal") -> str:
        """Flag this session for human follow-up.

        Args:
            reason: Why a human needs to get involved.
            urgency: How urgently a human should follow up.
        """
        call_id = context.function_call.call_id
        _log.info(
            "builtin_tool.escalate_to_human",
            session_id=ctx.session_id,
            call_id=call_id,
            reason=reason,
            urgency=urgency,
        )
        await ctx.ui.set_status("Escalated", "warning")
        try:
            ctx.record_event("escalation", {"reason": reason, "urgency": urgency})
        except Exception:
            _log.debug("could not record the escalation event", session_id=ctx.session_id, exc_info=True)
        await ctx.ui.activity(
            ActivityEvent(
                id=call_id,
                ts=time.time(),
                source="escalate_to_human",
                label="Escalation",
                phase="done",
                headline=reason,
                urgent=urgency == "high",
                detail={"urgency": urgency},
            )
        )
        return "Escalation logged. Tell the caller a member of the team will follow up with them directly."

    return escalate_to_human
