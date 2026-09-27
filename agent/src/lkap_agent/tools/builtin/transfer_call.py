"""`transfer_call` built-in tool: hand the caller to a person (PLAN-V2 V2-17, V5-32).

Cold: a SIP REFER through the api (``POST /internal/v1/telephony/sessions/{id}/transfer``
→ ``SipService.transfer_sip_participant``), per ARCHITECTURE-V2 §2.6. Warm (V5-32,
D-V5-21): a target with ``mode="warm"`` on a LiveKit Cloud connection runs the
SDK's ``WarmTransferTask`` (hold music, a private call that briefs the person,
then the person joins the caller); anywhere else it falls back to cold and the
summary is kept on the call row (``TelephonySession.hand_over``). The destination
is never free text: the model picks one of the agent's configured targets
(``config.telephony.transfer_targets``), so a caller cannot talk the agent into
dialing an arbitrary (premium-rate) number. The tool is registered only when that
allowlist is non-empty.

On success the caller is with the person, so the job is shut down; a cold failure
is told to the model as text, and a warm one raises ``ToolError`` (nobody
answered, they declined): either way the conversation resumes.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool, get_job_context
from packs.base import PackSessionContext

_DEFAULT_ANNOUNCEMENT = "Please hold while I transfer your call."

#: Said once a warm transfer has joined the person to the call, before the agent leaves.
_WARM_CONNECTED_LINE = "You're now connected. I'll leave you to it. Goodbye."


class TransferOutcome(Protocol):
    """The fields of a transfer result the tool reads."""

    @property
    def ok(self) -> bool: ...  # noqa: D102 - protocol member

    @property
    def status(self) -> str: ...  # noqa: D102 - protocol member

    @property
    def reason(self) -> str | None: ...  # noqa: D102 - protocol member


def _default_shutdown(reason: str) -> None:
    get_job_context().shutdown(reason=reason)


def match_target(targets: dict[str, str], destination: str) -> str | None:
    """The target for a label (case-insensitive) or for a number/URI already in the list."""
    wanted = destination.strip()
    for label, target in targets.items():
        if wanted.lower() == label.lower() or wanted == target:
            return target
    return None


def _label_of(targets: Mapping[str, str], to: str, destination: str) -> str:
    """The label the model named, else the first label of the number it named."""
    wanted = destination.strip().lower()
    by_name = next((label for label in targets if label.lower() == wanted), None)
    return by_name or next((label for label, target in targets.items() if target == to), destination)


def _chat_ctx_of(context: Any) -> Any:
    """The running agent's conversation (the warm briefing reads it); ``None`` outside a session."""
    session = getattr(context, "session", None)
    agent = getattr(session, "current_agent", None)
    return getattr(agent, "chat_ctx", None)


def build_transfer_call_tool(
    ctx: PackSessionContext,
    *,
    targets: dict[str, str],
    transfer: Callable[[str], Awaitable[TransferOutcome]],
    shutdown: Callable[[str], None] | None = None,
    modes: Mapping[str, str] | None = None,
    hand_over: Callable[[Any], Awaitable[Any]] | None = None,
) -> FunctionTool[..., Any]:
    """Build the `transfer_call` tool bound to `ctx`.

    Args:
        ctx: The session's `PackSessionContext`.
        targets: ``{label: E.164 or tel:/sip: URI}``; the only allowed destinations.
        transfer: Cold-transfers to a target (`TelephonySession.transfer`); used when
            `hand_over` is not given.
        shutdown: Ends the job after a successful transfer (default: the job context's).
        modes: ``{label: "cold" | "warm"}`` (V5-32); a missing label is cold.
        hand_over: `TelephonySession.hand_over` (V5-32): warm or cold, the handoff
            block, the event and the call report. Takes a `telephony.HandOver`.
    """
    shutdown_fn = shutdown or _default_shutdown
    names = ", ".join(targets)
    target_modes = dict(modes or {})

    async def _say(line: str) -> None:
        spoken: Any = (
            ctx.session.generate_reply(instructions=f"Say exactly: {line}")
            if ctx.pipeline_mode == "realtime"
            else ctx.session.say(line)
        )
        if inspect.isawaitable(spoken):
            await spoken

    @function_tool(
        description=(
            "Transfer the caller to a person or department and leave the call. "
            f"Available destinations: {names}. Use only when the caller asks for a human "
            "or the request is outside what you can handle."
        )
    )
    async def transfer_call(
        context: RunContext[Any],
        destination: str,
        announcement: str | None = None,
        summary: str | None = None,
    ) -> str:
        """Transfer the caller.

        Args:
            destination: One of the available destinations, by name.
            announcement: Optional short line to say before transferring.
            summary: One or two sentences for the person taking the call: who the caller
                is and what they need.
        """
        target = match_target(targets, destination)
        if target is None:
            return f"Unknown destination '{destination}'. Choose one of: {names}."
        await _say(announcement or _DEFAULT_ANNOUNCEMENT)
        label = _label_of(targets, target, destination)
        mode = target_modes.get(label, "cold")
        result: Any
        if hand_over is None:
            result = await transfer(target)
        else:
            from lkap_agent.telephony import HandOver  # noqa: PLC0415 - avoids an import cycle

            result = await hand_over(
                HandOver(label=label, to=target, mode=mode, summary=summary, chat_ctx=_chat_ctx_of(context))
            )
        ran = getattr(result, "mode", "cold")
        ctx.log.info(
            "builtin_tool.transfer_call",
            call_id=context.function_call.call_id,
            ok=result.ok,
            status=result.status,
            mode=ran,
        )
        if not result.ok:
            why = result.reason or result.status
            if ran == "warm":
                raise ToolError(
                    f"The transfer to {label} did not connect ({why}). The caller is back with you: "
                    "tell them and offer to help another way."
                )
            return f"The transfer did not go through ({why}). Tell the caller and offer to help."
        if ran == "warm":
            await _say(_WARM_CONNECTED_LINE)
        shutdown_fn("call transferred")
        return "Transferred."

    return transfer_call
