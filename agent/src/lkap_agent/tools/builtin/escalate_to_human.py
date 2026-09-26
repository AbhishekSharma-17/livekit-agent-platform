"""`escalate_to_human` built-in tool (docs/ARCHITECTURE.md §7.3).

Flags the session (status, activity, an ``escalation`` event). With
``tools.notify_team`` configured and ``on_escalation`` on, it also posts the
reason to the team's webhook (V5-25, :func:`~.notify_team.post_team_notification`);
a failed post is logged and never fails the escalation.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from livekit.agents import FunctionTool, RunContext, function_tool
from lkap_contracts.ui_protocol import ActivityEvent
from packs.base import PackSessionContext

from lkap_agent.logging import get_logger

_log = get_logger(__name__)

Urgency = Literal["low", "normal", "high"]

#: Posts ``(reason, urgency)`` to the team; raises on failure.
TeamNotifier = Callable[[str, Urgency], Awaitable[None]]


def build_escalate_to_human_tool(
    ctx: PackSessionContext, *, notify: TeamNotifier | None = None
) -> FunctionTool[..., Any]:
    """Build the `escalate_to_human` tool bound to `ctx`.

    Args:
        ctx: The session's context.
        notify: Posts the escalation to the team (V5-25); ``None`` when ``notify_team`` is not
            configured or ``on_escalation`` is off.
    """

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
        logged = "Escalation logged."
        if notify is not None:
            try:
                await notify(reason, urgency)
            except Exception as exc:  # noqa: BLE001 - the escalation stands without the post
                _log.warning(
                    "escalation could not be posted to the team",
                    session_id=ctx.session_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
            else:
                logged = "Escalation logged and the team has been notified."
        return f"{logged} Tell the caller a member of the team will follow up with them directly."

    return escalate_to_human
