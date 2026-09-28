"""`start_timer` built-in tool (V6-23, D-V6-20): start, replace or stop a timer in a `timer` block.

A ``countdown`` shows the time left, an ``elapsed`` timer the time since it started; both end
after ``seconds`` (at most the block's `max_seconds`, itself at most 4 hours). The worker ends
it, not the page: the end runs as a job on the session's `BackgroundRunner` (so it is
cancelled when the session closes), writes ``ended`` to the block, records a ``timer_ended``
session event (`TIMER_ENDED_EVENT`: the block, the mode and the length, never the label) and
tells the model in one line (a routine note it reads on its next turn). Starting a timer on a
block that already runs one replaces it (the old end is cancelled); ``seconds=0`` stops it.
The label is the agent's own words, shown as plain text. A write: blocking, instant (the call
answers at once), never in the background itself; it answers nothing on a realtime model (a
half cascade too). On a phone call the timer still runs and the model is still told at the end;
the caller only cannot see it.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.blocks import TimerBlockConfig
from lkap_contracts.ui_protocol import (
    MAX_TIMER_LABEL_CHARS,
    TIMER_ENDED_EVENT,
    TIMER_MODES,
    TimerBlockState,
    UiPatchOp,
)
from packs.base import PackSessionContext
from pydantic import ValidationError

from lkap_agent.tools.untrusted import strip_control
from lkap_agent.ui.blocks import VOICE_ONLY_CHANNELS, describe_blocks, pick_block, session_block_specs

from .set_checklist import QUIET_MODES

__all__ = ["TIMERS_KEY", "TIMER_JOB_NAME", "TimerJobs", "build_start_timer_tool", "timer_jobs"]

#: `SessionContext.userdata` key of the session's running timers (platform-private).
TIMERS_KEY: Final[str] = "_lkap_timers"
#: The background job's name (the team feed shows "Timer started" / "Timer finished").
TIMER_JOB_NAME: Final[str] = "timer"
#: How the end waits (a seam for tests; `asyncio.sleep` in a session).
_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


@dataclass
class TimerJobs:
    """The session's running timer ends: block id -> (generation, background job id)."""

    running: dict[str, tuple[int, str]] = field(default_factory=dict)
    counter: itertools.count[int] = field(default_factory=lambda: itertools.count(1))


def timer_jobs(ctx: PackSessionContext) -> TimerJobs:
    """The session's :class:`TimerJobs`, created on first use (in `ctx.userdata`)."""
    jobs = ctx.userdata.get(TIMERS_KEY)
    if not isinstance(jobs, TimerJobs):
        jobs = TimerJobs()
        ctx.userdata[TIMERS_KEY] = jobs
    return jobs


def _length(seconds: int) -> str:
    """``90`` -> ``1 min 30 s``; ``120`` -> ``2 min``; ``3600`` -> ``1 h``."""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{hours} h" if hours else "", f"{minutes} min" if minutes else "", f"{secs} s" if secs else ""]
    return " ".join(p for p in parts if p) or "0 s"


def build_start_timer_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `start_timer` tool bound to `ctx`."""
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["timer"])

    async def run_out(target: str, generation: int, seconds: int, mode: str, label: str | None) -> str | None:
        """Wait, then end the timer (unless a newer one replaced it); the one-line note, or `None`."""
        await _sleep(seconds)
        jobs = timer_jobs(ctx)
        current = jobs.running.get(target)
        if current is None or current[0] != generation:
            return None
        del jobs.running[target]
        await ctx.ui.patch_block(
            target,
            [
                UiPatchOp(op="set", path="/status", value="ended"),
                UiPatchOp(op="set", path="/ended_at", value=time.time()),
            ],
        )
        ctx.record_event(TIMER_ENDED_EVENT, {"block_id": target, "mode": mode, "duration_s": seconds})
        ctx.log.debug("builtin_tool.timer_ended", block_id=target, mode=mode, duration_s=seconds)
        name = f'The timer "{label}"' if label else "The timer"
        return f"{name} on the caller's screen has run out ({_length(seconds)}). Tell them if it matters."

    async def stop(target: str) -> str:
        state = ctx.ui.state.blocks.get(target) or {}
        if not isinstance(state, dict) or state.get("status") != "running":
            return "No timer is running there."
        await ctx.ui.patch_block(
            target,
            [
                UiPatchOp(op="set", path="/status", value="stopped"),
                UiPatchOp(op="set", path="/ended_at", value=time.time()),
            ],
        )
        return "The timer is stopped."

    async def start_timer(
        context: RunContext[Any], seconds: int, label: str = "", mode: str = "", block_id: str = ""
    ) -> str | None:
        """Start a timer on the caller's screen (or replace the running one); 0 seconds stops it.

        Args:
            seconds: How long it runs, e.g. 120 for two minutes; 0 stops the running timer.
            label: An optional short label, e.g. Find your policy number.
            mode: countdown (time left) or elapsed (time since it started); empty for the usual one.
            block_id: The timer block; leave empty when there is only one.
        """
        specs = session_block_specs(ctx.ui, ctx.config.panel)
        try:
            target = pick_block(specs, "timer", block_id or None)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        spec = next(s for s in specs if s.id == target)
        try:
            config = TimerBlockConfig.model_validate(spec.config)
        except ValidationError:
            config = TimerBlockConfig()
        if seconds < 0 or seconds > config.max_seconds:
            raise ToolError(f"seconds is 1 to {config.max_seconds} (or 0 to stop the timer).")
        chosen = mode.strip().lower() or config.mode
        if chosen not in TIMER_MODES:
            raise ToolError("mode is countdown or elapsed.")
        text = " ".join(strip_control(label).split()) or None
        if text is not None and len(text) > MAX_TIMER_LABEL_CHARS:
            raise ToolError(f"Keep the label to {MAX_TIMER_LABEL_CHARS} characters.")
        jobs = timer_jobs(ctx)
        previous = jobs.running.pop(target, None)
        if previous is not None:
            ctx.background.cancel(previous[1])
        call_id = context.function_call.call_id
        if seconds == 0:
            answer = await stop(target)
            ctx.log.debug("builtin_tool.start_timer", call_id=call_id, block_id=target, stopped=True)
            return None if ctx.pipeline_mode in QUIET_MODES else answer
        now = time.time()
        state = TimerBlockState(
            mode=chosen,
            label=text,
            status="running",
            duration_s=seconds,
            started_at=now,
            ends_at=now + seconds,
        )
        await ctx.ui.set_block(target, state.model_dump(mode="json"))
        generation = next(jobs.counter)
        job_id = ctx.background.submit(
            name=TIMER_JOB_NAME,
            coro=run_out(target, generation, seconds, chosen, text),
            routine_note=lambda note: note if isinstance(note, str) else None,
        )
        jobs.running[target] = (generation, job_id)
        ctx.log.debug(
            "builtin_tool.start_timer", call_id=call_id, block_id=target, mode=chosen, duration_s=seconds
        )
        if ctx.pipeline_mode in QUIET_MODES:
            return None
        if getattr(ctx, "channel", "web") in VOICE_ONLY_CHANNELS:
            return (
                f"The timer runs ({_length(seconds)}), but the caller cannot see it on a phone call. "
                "You will be told when it runs out."
            )
        return f"The timer is running ({_length(seconds)}). You will be told when it runs out."

    return function_tool(
        start_timer,
        description=(
            "Start a countdown or a stopwatch on the caller's screen, e.g. while they look for a "
            "document; you are told when it runs out. Starting another replaces it; 0 seconds stops "
            f"it. Timer blocks: {inventory}."
        ),
    )
