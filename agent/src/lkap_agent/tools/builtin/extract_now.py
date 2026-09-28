"""`extract_now` built-in tool (V6-13, D-V6-24): run the live extraction when the model asks.

Registered only when ``AgentConfig.extraction`` is on with a ``manual`` trigger. It waits for
an extraction already running (within the budget), runs one more (a no-op when the
conversation has not changed) and answers with what is known and what is still needed. The
values are the caller's words restated by the extraction model, so they reach the model
fenced (``source="extraction"``); a ``sensitive`` field is named, never read back.
"""

from __future__ import annotations

from typing import Any, Final

from livekit.agents import FunctionTool, RunContext, function_tool
from lkap_contracts.rules_expr import is_set
from packs.base import PackSessionContext

from lkap_agent.extraction.runner import field_label
from lkap_agent.extraction.session import live_structure
from lkap_agent.logging import get_logger
from lkap_agent.tools.untrusted import fence

__all__ = ["build_extract_now_tool"]

_log = get_logger(__name__)

#: Longest answer the model reads.
_MAX_ANSWER_CHARS: Final[int] = 2000


def build_extract_now_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `extract_now` tool bound to `ctx`."""

    @function_tool
    async def extract_now(context: RunContext[Any]) -> str:
        """Capture the details this agent collects from the conversation so far, right now.

        Use it before you rely on a detail the caller has just given (to look something up, to
        read it back, or to decide what to ask next). It tells you what is known and what is
        still needed.
        """
        structure = live_structure(ctx)
        runner = structure.runner if structure is not None else None
        if structure is None or runner is None:
            return "Nothing is set up to be captured in this conversation."
        run = await structure.run_now()
        _log.debug(
            "builtin_tool.extract_now",
            call_id=context.function_call.call_id,
            status=run.status if run is not None else "unchanged",
        )
        store = runner.variables()
        known: list[str] = []
        for spec in runner.config.fields:
            value = store.get(spec.name)
            if not is_set(value):
                continue
            if spec.sensitive:
                known.append(f"- {field_label(spec)}: captured (not read back)")
            else:
                text = ("yes" if value else "no") if isinstance(value, bool) else str(value)
                known.append(f"- {field_label(spec)}: {text}")
        needed = [
            field_label(spec) for spec in runner.config.fields if spec.name in set(runner.still_needed())
        ]
        parts: list[str] = []
        if run is not None and run.status != "ok":
            parts.append("The capture did not finish this time; here is what was known before.")
        if known:
            parts.append(
                "Captured so far (from the caller's words; confirm important values with them):\n"
                + fence("\n".join(known), source="extraction", max_chars=_MAX_ANSWER_CHARS)
            )
        else:
            parts.append("Nothing has been captured yet.")
        parts.append(f"Still needed: {', '.join(needed)}." if needed else "Nothing required is missing.")
        return "\n".join(parts)

    return extract_now
