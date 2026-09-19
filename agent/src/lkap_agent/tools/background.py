"""`BackgroundToolRunner` — LKAP's "non-blocking tool" pattern.

Implements `packs.base.BackgroundRunner` (docs/CONTRACTS.md §8;
docs/ARCHITECTURE.md §7.2, D10). LiveKit has no non-blocking tool flag, so a
tool that wants to keep talking/listening while it does slow work (a
workflow run, an image generation, a slow lookup) calls `submit()` and
returns immediately; this runner does the actual work as a session-scoped
`asyncio.Task` and delivers the result on two independent channels:

- **UI**: always, via `ctx.ui.activity(...)` (`running` -> `done`/`error`/
  `cancelled`), regardless of pipeline mode or urgency.
- **Conversation**: mode-agnostic from this runner's point of view (packs
  never branch on realtime vs cascaded here) —
  - *urgent* (`urgent(result)` is true): `session.generate_reply(instructions=...,
    allow_interruptions=False)`, interrupting current speech.
  - *routine*: `agent.update_chat_ctx(agent.chat_ctx.copy() + assistant note)`,
    so the model sees it on its next turn.

  Realtime's `reply_required=False` silencing (`FunctionCallOutput`, read only
  by realtime models per the `function_tools_executed` event) and cascaded's
  "one short acknowledgement clause" are both properties of what the *tool
  function* returns, not of this runner — see docs/ARCHITECTURE.md §7.2.

A job submitted here is independent of tool-reply cancellation: cancelling a
tool's *reply* (`FunctionToolsExecutedEvent.cancel_tool_reply()`, wired by
`PlatformAgent` in W1-AGENT-CORE) never touches `BackgroundToolRunner`'s task
set — only `cancel(job_id)` does.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from livekit.agents import AgentSession
from lkap_contracts.ui_protocol import ActivityEvent

from lkap_agent.logging import get_logger

__all__ = ["BackgroundToolRunner"]

#: Structural stand-in for `packs.base.UiChannel` (avoids a hard import cycle;
#: any object with an async `activity(ActivityEvent)` method satisfies this).
UiActivitySink = Any


def _label(name: str) -> str:
    """A readable "team member" label derived from a tool/workflow name."""
    return name.replace("_", " ").strip().capitalize() or name


class BackgroundToolRunner:
    """Runs background tool/workflow coroutines for one session."""

    def __init__(
        self,
        ui: UiActivitySink,
        session: AgentSession[Any],
        *,
        labels: Mapping[str, str] | None = None,
        log: Any = None,
        record_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        """Create the runner for one session.

        Args:
            ui: The session's UI channel (only `activity()` is used).
            session: The session that receives urgent replies / routine notes.
            labels: Job name -> team-feed label, built from the pack's
                `ToolMeta.activity_label` (e.g. `sync_claim_packet` -> "Claim writer").
                `ActivityEvent.source` stays the job name; names without a label
                fall back to a prettified name.
            log: Optional bound logger.
            record_event: Records a session event (the worker passes
                `SessionObserver.record`); every finished job is recorded as a
                CONTRACTS §7 `workflow_run` event `{name, duration_ms, status}`.
        """
        self._ui = ui
        self._session = session
        self._labels = dict(labels or {})
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._log = log or get_logger(__name__)
        self._record_event = record_event

    # --- packs.base.BackgroundRunner ------------------------------------------

    def submit(
        self,
        *,
        name: str,
        coro: Awaitable[Any],
        on_result: Callable[[Any], Awaitable[None]] | None = None,
        urgent: Callable[[Any], bool] | None = None,
        urgent_instructions: Callable[[Any], str] | None = None,
        routine_note: Callable[[Any], str | None] | None = None,
        call_id: str | None = None,
    ) -> str:
        """Schedule `coro` as a background task and return its job id."""
        job_id = call_id or str(uuid.uuid4())
        task = asyncio.ensure_future(
            self._run_job(
                job_id=job_id,
                name=name,
                coro=coro,
                on_result=on_result,
                urgent=urgent,
                urgent_instructions=urgent_instructions,
                routine_note=routine_note,
            )
        )
        self._jobs[job_id] = task

        def _forget_job(_task: asyncio.Task[None]) -> None:
            self._jobs.pop(job_id, None)

        task.add_done_callback(_forget_job)
        self._log.debug("background_job_submitted", job_id=job_id, name=name)
        return job_id

    def cancel(self, job_id: str) -> None:
        """Cancel a still-running job; a no-op if it already finished."""
        task = self._jobs.get(job_id)
        if task is not None and not task.done():
            task.cancel()
        self._log.debug("background_job_cancel_requested", job_id=job_id)

    def cancel_all(self) -> int:
        """Cancel every still-running job; called by the worker at session shutdown.

        Without this an in-flight workflow outlives the hangup, keeps calling
        its LLM and then delivers into a closed session (REVIEW-FINAL F-11).
        Each cancelled job still reports its `cancelled` activity and
        `workflow_run` event from its own task.

        Returns:
            The number of jobs that were cancelled.
        """
        cancelled = 0
        # Done-callbacks remove entries from `_jobs`, so iterate over a copy.
        for task in list(self._jobs.values()):
            if not task.done():
                task.cancel()
                cancelled += 1
        self._log.debug("background_jobs_cancel_all", cancelled=cancelled)
        return cancelled

    # --- job lifecycle -----------------------------------------------------------

    def _record_run(self, name: str, status: str, duration_ms: int) -> None:
        """Post one `workflow_run` session event; never raises into the job."""
        if self._record_event is None:
            return
        try:
            self._record_event("workflow_run", {"name": name, "duration_ms": duration_ms, "status": status})
        except Exception:  # noqa: BLE001 - observability must not break a job
            self._log.debug("workflow_run event not recorded", name=name, exc_info=True)

    async def _run_job(
        self,
        *,
        job_id: str,
        name: str,
        coro: Awaitable[Any],
        on_result: Callable[[Any], Awaitable[None]] | None,
        urgent: Callable[[Any], bool] | None,
        urgent_instructions: Callable[[Any], str] | None,
        routine_note: Callable[[Any], str | None] | None,
    ) -> None:
        label = self._labels.get(name) or _label(name)
        started_at = time.monotonic()
        await self._ui.activity(
            ActivityEvent(
                id=job_id,
                ts=time.time(),
                source=name,
                label=label,
                phase="running",
                headline=f"{label} started",
            )
        )
        try:
            result = await coro
        except asyncio.CancelledError:
            duration_ms = int((time.monotonic() - started_at) * 1000)
            self._log.debug("background_job_cancelled", job_id=job_id, name=name, duration_ms=duration_ms)
            self._record_run(name, "cancelled", duration_ms)
            await self._ui.activity(
                ActivityEvent(
                    id=job_id,
                    ts=time.time(),
                    source=name,
                    label=label,
                    phase="cancelled",
                    headline=f"{label} cancelled",
                    duration_ms=duration_ms,
                )
            )
            raise
        except Exception as exc:  # noqa: BLE001 - a failed background job must not crash the session
            duration_ms = int((time.monotonic() - started_at) * 1000)
            self._log.debug(
                "background_job_error", job_id=job_id, name=name, duration_ms=duration_ms, error=str(exc)
            )
            self._record_run(name, "error", duration_ms)
            await self._ui.activity(
                ActivityEvent(
                    id=job_id,
                    ts=time.time(),
                    source=name,
                    label=label,
                    phase="error",
                    headline=f"{label} failed: {exc}",
                    duration_ms=duration_ms,
                )
            )
            return

        duration_ms = int((time.monotonic() - started_at) * 1000)
        self._record_run(name, "done", duration_ms)
        try:
            await self._deliver_result(
                job_id=job_id,
                name=name,
                label=label,
                result=result,
                duration_ms=duration_ms,
                on_result=on_result,
                urgent=urgent,
                urgent_instructions=urgent_instructions,
                routine_note=routine_note,
            )
        except Exception as exc:  # noqa: BLE001 - e.g. the session already tore down; must not crash the task
            self._log.debug(
                "background_job_delivery_error",
                job_id=job_id,
                name=name,
                duration_ms=duration_ms,
                error=str(exc),
            )
            await self._ui.activity(
                ActivityEvent(
                    id=job_id,
                    ts=time.time(),
                    source=name,
                    label=label,
                    phase="error",
                    headline=f"{label} result delivery failed: {exc}",
                    duration_ms=duration_ms,
                )
            )

    async def _deliver_result(
        self,
        *,
        job_id: str,
        name: str,
        label: str,
        result: Any,
        duration_ms: int,
        on_result: Callable[[Any], Awaitable[None]] | None,
        urgent: Callable[[Any], bool] | None,
        urgent_instructions: Callable[[Any], str] | None,
        routine_note: Callable[[Any], str | None] | None,
    ) -> None:
        """UI activity + conversation delivery for a successfully completed job.

        Split out from `_run_job` so its own failures (e.g. `generate_reply`
        raising because the session already tore down) are caught by one
        `try`/`except` in the caller instead of becoming an unhandled task
        exception.
        """
        if on_result is not None:
            await on_result(result)

        is_urgent = bool(urgent(result)) if urgent is not None else False
        await self._ui.activity(
            ActivityEvent(
                id=job_id,
                ts=time.time(),
                source=name,
                label=label,
                phase="done",
                headline=f"{label} finished",
                urgent=is_urgent,
                duration_ms=duration_ms,
            )
        )
        self._log.debug(
            "background_job_done", job_id=job_id, name=name, duration_ms=duration_ms, urgent=is_urgent
        )

        if is_urgent:
            instructions = (
                urgent_instructions(result)
                if urgent_instructions is not None
                else f"A background task ({name}) just finished with an urgent result."
            )
            self._session.generate_reply(instructions=instructions, allow_interruptions=False)
            return

        note = routine_note(result) if routine_note is not None else None
        if note is not None:
            agent = self._session.current_agent
            chat_ctx = agent.chat_ctx.copy()
            chat_ctx.add_message(role="assistant", content=note)
            await agent.update_chat_ctx(chat_ctx)
