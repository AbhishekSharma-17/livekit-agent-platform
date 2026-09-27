"""`escalate_to_human` built-in tool (docs/ARCHITECTURE.md §7.3).

Flags the session (status, activity, an ``escalation`` event). With
``tools.notify_team`` configured and ``on_escalation`` on, it also posts the
reason to the team's webhook (V5-25, :func:`~.notify_team.post_team_notification`);
a failed post is logged and never fails the escalation.

V5-37 adds ``mode`` (:data:`lkap_contracts.tools.EscalationMode`: ``transfer``, the
default and the old behaviour, ``takeover``, ``listen_in`` or ``callback``). The
mode rides on the ``escalation`` event (:class:`~lkap_contracts.api_models.EscalationEvent`)
and every ``handoff`` block is written ``requested`` with a fixed plain line per mode
(never the model's ``reason``: on the web the caller sees the block; ``mode`` stays
null there, it names the transfer that ran). From the first escalation on, a
participant who joins with ``lkap.role=human`` (a platform-minted token; a client can
never set an ``lkap.*`` attribute) turns the block ``connected`` with their name. The
tool itself dials no one: the takeover is P2, the listen-in is the console's Live tab.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, Final, Literal

from livekit.agents import FunctionTool, RunContext, function_tool
from lkap_contracts.api_models import PARTICIPANT_ROLE_ATTRIBUTE, EscalationEvent
from lkap_contracts.tools import EscalationMode
from lkap_contracts.ui_protocol import ActivityEvent
from packs.base import PackSessionContext

from lkap_agent.logging import get_logger
from lkap_agent.ui.blocks import set_handoff

_log = get_logger(__name__)

Urgency = Literal["low", "normal", "high"]

#: Posts ``(reason, urgency)`` to the team; raises on failure.
TeamNotifier = Callable[[str, Urgency], Awaitable[None]]

#: What the ``handoff`` block says while a person is being asked for, per mode (plain words
#: the caller may read; never the model's own reason).
HANDOFF_REASONS: Final[dict[str, str]] = {
    "transfer": "Waiting for a member of the team to take the call.",
    "takeover": "A member of the team will join the call.",
    "listen_in": "A member of the team may listen in to help.",
    "callback": "A member of the team will call you back.",
}

#: What the model is told to say next, per mode (``transfer`` keeps the pre-V5-37 line).
NEXT_STEP: Final[dict[str, str]] = {
    "transfer": "Tell the caller a member of the team will follow up with them directly.",
    "takeover": "Tell the caller a member of the team will join shortly, and keep helping until they do.",
    "listen_in": "Carry on helping the caller; a supervisor may listen in and send you guidance.",
    "callback": "Tell the caller a member of the team will call them back.",
}

#: How the team post names a mode other than ``transfer`` (appended to the reason).
_TEAM_MODE_LABELS: Final[dict[str, str]] = {
    "takeover": "asks for a person to take over the call",
    "listen_in": "asks for a supervisor to listen in",
    "callback": "asks for a call back",
}

#: The participant role that marks a person joining to take the call over.
HUMAN_ROLE: Final[str] = "human"

#: `userdata` key: the human-participant watcher is registered (once per session).
_HUMAN_WATCH_KEY: Final[str] = "_lkap_human_watch"


def is_human_participant(participant: Any) -> bool:
    """Whether a room participant joined as a person taking the call (``lkap.role=human``)."""
    attributes = getattr(participant, "attributes", None) or {}
    return bool(isinstance(attributes, dict) and attributes.get(PARTICIPANT_ROLE_ATTRIBUTE) == HUMAN_ROLE)


def watch_for_human(ctx: PackSessionContext) -> bool:
    """Mark the ``handoff`` blocks ``connected`` when a person joins (once per session).

    Checks the participants already in the room, then listens for
    ``participant_connected``. Never raises.

    Returns:
        Whether the watcher was registered by this call.
    """
    userdata = getattr(ctx, "userdata", None)
    room = getattr(ctx, "room", None)
    on = getattr(room, "on", None)
    if not isinstance(userdata, dict) or userdata.get(_HUMAN_WATCH_KEY) or not callable(on):
        return False
    userdata[_HUMAN_WATCH_KEY] = True
    tasks: set[asyncio.Task[None]] = set()

    def _joined(participant: Any) -> None:
        if not is_human_participant(participant):
            return
        name = str(getattr(participant, "name", "") or "").strip() or None
        _log.info(
            "human joined the call",
            session_id=ctx.session_id,
            identity=getattr(participant, "identity", None),
        )
        task = asyncio.create_task(_connected(ctx, name))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    try:
        on("participant_connected", _joined)
        for participant in list((getattr(room, "remote_participants", None) or {}).values()):
            _joined(participant)
    except Exception:  # noqa: BLE001 - the escalation stands without the watcher
        _log.debug("could not watch for a person joining", session_id=ctx.session_id, exc_info=True)
    return True


async def _connected(ctx: PackSessionContext, name: str | None) -> None:
    try:
        await set_handoff(ctx.ui, ctx.config.panel, "connected", agent_name=name)
    except Exception:  # noqa: BLE001 - the panel is advisory
        _log.debug("handoff block not marked connected", session_id=ctx.session_id, exc_info=True)


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
    async def escalate_to_human(
        context: RunContext[Any],
        reason: str,
        urgency: Urgency = "normal",
        mode: EscalationMode = "transfer",
    ) -> str:
        """Flag this session for a person on the team to get involved.

        Args:
            reason: Why a human needs to get involved.
            urgency: How urgently a human should follow up.
            mode: How a person should get involved: "transfer" (take the caller over; the
                default), "takeover" (join this call and take it over), "listen_in" (a
                supervisor listens and may guide you), or "callback" (call the caller back later).
        """
        call_id = context.function_call.call_id
        _log.info(
            "builtin_tool.escalate_to_human",
            session_id=ctx.session_id,
            call_id=call_id,
            reason=reason,
            urgency=urgency,
            mode=mode,
        )
        await ctx.ui.set_status("Escalated", "warning")
        try:
            payload = EscalationEvent(reason=reason, urgency=urgency, mode=mode).model_dump(mode="json")
            if mode == "transfer":
                # The pre-V5-37 shape stays byte-identical; an absent `mode` reads as `transfer`.
                payload.pop("mode")
            ctx.record_event("escalation", payload)
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
                detail={"urgency": urgency, "mode": mode},
            )
        )
        try:
            await set_handoff(ctx.ui, ctx.config.panel, "requested", reason=HANDOFF_REASONS[mode])
        except Exception:  # noqa: BLE001 - the panel is advisory; the escalation stands
            _log.debug("handoff block not written", session_id=ctx.session_id, exc_info=True)
        watch_for_human(ctx)
        logged = "Escalation logged."
        if notify is not None:
            label = _TEAM_MODE_LABELS.get(mode)
            try:
                await notify(f"{reason} ({label})" if label else reason, urgency)
            except Exception as exc:  # noqa: BLE001 - the escalation stands without the post
                _log.warning(
                    "escalation could not be posted to the team",
                    session_id=ctx.session_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
            else:
                logged = "Escalation logged and the team has been notified."
        return f"{logged} {NEXT_STEP[mode]}"

    return escalate_to_human
