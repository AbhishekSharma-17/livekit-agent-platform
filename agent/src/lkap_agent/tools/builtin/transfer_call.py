"""`transfer_call` built-in tool: cold-transfer the caller to a person (PLAN-V2 V2-17).

A SIP REFER through the api (``POST /internal/v1/telephony/sessions/{id}/transfer``
→ ``SipService.transfer_sip_participant``), per ARCHITECTURE-V2 §2.6. The
destination is never free text: the model picks one of the agent's configured
targets (``pack_settings["transfer_targets"]``), so a caller cannot talk the
agent into dialing an arbitrary (premium-rate) number. The tool is registered
only when that allowlist is non-empty.

On success the caller is gone from the room, so the job is shut down; on
failure the model is told why and the call continues.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from livekit.agents import FunctionTool, RunContext, function_tool, get_job_context
from packs.base import PackSessionContext

_DEFAULT_ANNOUNCEMENT = "Please hold while I transfer your call."


class TransferOutcome(Protocol):
    """The fields of `lkap_agent.telephony.TransferResult` the tool reads."""

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


def build_transfer_call_tool(
    ctx: PackSessionContext,
    *,
    targets: dict[str, str],
    transfer: Callable[[str], Awaitable[TransferOutcome]],
    shutdown: Callable[[str], None] | None = None,
) -> FunctionTool[..., Any]:
    """Build the `transfer_call` tool bound to `ctx`.

    Args:
        ctx: The session's `PackSessionContext`.
        targets: ``{label: E.164 or tel:/sip: URI}``; the only allowed destinations.
        transfer: Performs the transfer (the `TelephonySession.transfer` method).
        shutdown: Ends the job after a successful transfer (default: the job context's).
    """
    shutdown_fn = shutdown or _default_shutdown
    names = ", ".join(targets)

    @function_tool(
        description=(
            "Transfer the caller to a person or department and leave the call. "
            f"Available destinations: {names}. Use only when the caller asks for a human "
            "or the request is outside what you can handle."
        )
    )
    async def transfer_call(
        context: RunContext[Any], destination: str, announcement: str | None = None
    ) -> str:
        """Transfer the caller.

        Args:
            destination: One of the available destinations, by name.
            announcement: Optional short line to say before transferring.
        """
        target = match_target(targets, destination)
        if target is None:
            return f"Unknown destination '{destination}'. Choose one of: {names}."
        line = announcement or _DEFAULT_ANNOUNCEMENT
        spoken: Any = (
            ctx.session.generate_reply(instructions=f"Say exactly: {line}")
            if ctx.pipeline_mode == "realtime"
            else ctx.session.say(line)
        )
        if inspect.isawaitable(spoken):
            await spoken
        result = await transfer(target)
        ctx.log.info(
            "builtin_tool.transfer_call",
            call_id=context.function_call.call_id,
            ok=result.ok,
            status=result.status,
        )
        if not result.ok:
            why = result.reason or result.status
            return f"The transfer did not go through ({why}). Tell the caller and offer to help."
        shutdown_fn("call transferred")
        return "Transferred."

    return transfer_call
