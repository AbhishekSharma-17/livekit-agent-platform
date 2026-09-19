"""`end_call` built-in tool (docs/ARCHITECTURE.md §7.3).

Says a short goodbye, waits for it to finish playing, then shuts the job
down. `get_job_context().shutdown(reason=...)` is the SDK's canonical way to
end a call from inside a tool (verified: `livekit.agents.get_job_context`,
`JobContext.shutdown(reason=...)` in `job.py`) — but it raises `RuntimeError`
outside a real job (e.g. in a unit test), so the shutdown call is injectable
and only defaults to `get_job_context` in production.

Mirrors the greeting's mode-defensive `say()`/`generate_reply()` switch
(docs/ARCHITECTURE.md §15.9): `AgentSession.say()` needs a TTS, which a
realtime-only pipeline may not have configured, so realtime mode always uses
`generate_reply(instructions=...)` instead.

Deliberately does **not** touch `/status`: ARCHITECTURE §7.3 says "UI
receives `session_ending`" — an event, not a stamp — and `end_call` is not in
any pack's `builtin_tools_disabled` list (e.g. the insurance pack), so
overwriting `/status` here would clobber a pack's own route/routing stamp
(e.g. "Routed: emergency_escalation") right as the call ends.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from typing import Any

from livekit.agents import FunctionTool, RunContext, function_tool, get_job_context
from lkap_contracts.ui_protocol import ActivityEvent
from packs.base import PackSessionContext

_DEFAULT_GOODBYE = "Thanks for calling. Goodbye!"


def _default_shutdown(reason: str) -> None:
    get_job_context().shutdown(reason=reason)


def build_end_call_tool(
    ctx: PackSessionContext,
    *,
    shutdown: Callable[[str], None] | None = None,
) -> FunctionTool[..., Any]:
    """Build the `end_call` tool bound to `ctx`.

    Args:
        ctx: The session's `PackSessionContext`.
        shutdown: Called with a reason string to actually end the job.
            Defaults to `get_job_context().shutdown(...)`; tests inject a
            recorder since no real `JobContext` exists outside a job.
    """
    shutdown_fn = shutdown or _default_shutdown

    @function_tool
    async def end_call(context: RunContext[Any], closing_message: str | None = None) -> None:
        """End the call after saying a short goodbye.

        Args:
            closing_message: Optional closing line to say instead of the default goodbye.
        """
        text = closing_message or _DEFAULT_GOODBYE
        call_id = context.function_call.call_id
        try:
            await ctx.ui.activity(
                ActivityEvent(
                    id=call_id,
                    ts=time.time(),
                    source="end_call",
                    label="Call",
                    phase="done",
                    headline="Session ending",
                )
            )
            if ctx.pipeline_mode == "realtime":
                result: Any = ctx.session.generate_reply(instructions=f"Say goodbye: {text}")
            else:
                result = ctx.session.say(text)
            if inspect.isawaitable(result):
                await result
        finally:
            ctx.log.debug("builtin_tool.end_call", call_id=call_id)
            shutdown_fn("end_call tool invoked")

    return end_call
