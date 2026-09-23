"""`send_dtmf` built-in tool: play keypad tones into a phone call (PLAN-V2 V2-17).

For navigating an IVR ("press 1 for sales") on an outbound call, or entering
a PIN. Registered by :func:`lkap_agent.telephony.build_telephony_tools` only on
SIP sessions whose agent has ``capabilities.dtmf``; the tones go out with
``LocalParticipant.publish_dtmf`` (RFC 4733), 0.3 s apart. The ``send``
callable is injected so the tool never reaches for the room itself.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from livekit.agents import FunctionTool, RunContext, function_tool
from packs.base import PackSessionContext

_DIGITS = re.compile(r"^[0-9*#A-D]{1,32}$")


def build_send_dtmf_tool(
    ctx: PackSessionContext, *, send: Callable[[str], Awaitable[None]]
) -> FunctionTool[..., Any]:
    """Build the `send_dtmf` tool bound to `ctx`.

    Args:
        ctx: The session's `PackSessionContext` (for logging).
        send: Publishes the digits into the call and records the `dtmf` event.
    """

    @function_tool
    async def send_dtmf(context: RunContext[Any], digits: str) -> str:
        """Press keys on the phone keypad, for example to navigate a phone menu or enter a code.

        Args:
            digits: The keys to press in order: 0-9, * and # (A-D are rarely needed), e.g. "1" or "1234#".
        """
        keys = re.sub(r"[\s,-]", "", digits).upper()
        if not _DIGITS.match(keys):
            return "Nothing sent: use only the keys 0-9, * and # (at most 32)."
        try:
            await send(keys)
        except Exception as exc:  # noqa: BLE001 - the model gets the failure as text
            ctx.log.warning("builtin_tool.send_dtmf_failed", error=type(exc).__name__)
            return f"Could not send the keys: {type(exc).__name__}."
        ctx.log.debug("builtin_tool.send_dtmf", call_id=context.function_call.call_id, count=len(keys))
        return f"Pressed {' '.join(keys)}."

    return send_dtmf
