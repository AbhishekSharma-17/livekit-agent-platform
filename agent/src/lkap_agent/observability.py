"""Session telemetry: structlog context, api events, usage and transcript.

Everything the console shows about a past session originates here
(docs/ARCHITECTURE.md §14, event `type` values in docs/CONTRACTS.md §7).

Event delivery is buffered and flushed on a timer so a chatty session does not
turn into one HTTP round-trip per token, and every api call is best-effort: a
telemetry failure must never end a live conversation.

Two deviations from the plan text, both verified against livekit-agents 1.8.2:

* Tool timing comes from the `tool_execution_updated` event
  (`ToolCallStarted` / `ToolCallEnded`, both carrying `call_id`) rather than
  hand-instrumenting every tool.
* Usage totals and the CONTRACTS `metrics` events come from
  `session_usage_updated` (`AgentSessionUsage`). `metrics.UsageCollector` still
  exists but warns on construction, and subscribing to `metrics_collected` makes
  `AgentSession` log a deprecation warning on every session, so neither is used;
  per-turn latency rides along on `ChatMessage.metrics` instead.

The v2 per-session latency (`SessionLatency`, CONTRACTS-V2 §4.6; PLAN-V2 V2-07
names it "metrics_collected latency") is therefore computed from the same
`ChatMessage.metrics` of every assistant turn — `AgentSession.on` in 1.8.2
itself says "Use ... ChatMessage.metrics for per-turn latency":
`e2e_latency` (end of user speech → agent starts responding) is the
EOU-to-first-audio figure, `llm_node_ttft` the LLM time to first token and
`tts_node_ttfb` the TTS time to first byte. p50/p95 are posted to
`POST /internal/v1/sessions/{id}/metrics` just before the summary.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from typing import Any, Final

import structlog
from livekit.agents import (
    AgentSession,
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    ErrorEvent,
    SessionUsageUpdatedEvent,
    ToolExecutionUpdatedEvent,
)
from livekit.agents import llm as lk_llm
from lkap_contracts.api_models import (
    SessionEventIn,
    SessionLatency,
    SessionMetricsIn,
    SessionSummaryIn,
    TranscriptTurn,
)
from lkap_contracts.flow import FlowState
from lkap_contracts.ui_protocol import UiState

from lkap_agent.config_client import ConfigClientProtocol
from lkap_agent.logging import get_logger

__all__ = [
    "LatencyCollector",
    "SessionObserver",
    "bind_session_context",
    "percentile",
    "transcript_from_history",
]

logger = get_logger(__name__)

#: How long a full event buffer waits before flushing.
_FLUSH_INTERVAL_S: Final[float] = 2.0
#: Flush immediately once this many events are queued.
_FLUSH_THRESHOLD: Final[int] = 25
#: Tool results are previewed, not stored in full, in `tool_call_ended`.
_RESULT_PREVIEW_CHARS: Final[int] = 240
#: Argument keys never echoed into an event payload or a log line.
_REDACTED_ARG_KEYS: Final[frozenset[str]] = frozenset(
    {"api_key", "apikey", "authorization", "password", "secret", "token"}
)


#: `ChatMessage.metrics` keys (seconds) → the `SessionLatency` figure they feed.
_LATENCY_KEYS: Final[dict[str, str]] = {
    "e2e_latency": "eou_to_first_audio_ms",
    "llm_node_ttft": "llm_ttft_ms",
    "tts_node_ttfb": "tts_ttfb_ms",
}


def percentile(values: list[float], q: float) -> float | None:
    """The `q` quantile (0–1) of `values` by linear interpolation, `None` when empty."""
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * q
    low = math.floor(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


class LatencyCollector:
    """Accumulates per-turn latency from assistant `ChatMessage.metrics`."""

    def __init__(self) -> None:
        self._samples: dict[str, list[float]] = {name: [] for name in _LATENCY_KEYS.values()}
        self._turns = 0

    def add(self, metrics: Any) -> None:
        """Record one assistant turn's metrics (a `MetricsReport` dict, possibly empty)."""
        if not isinstance(metrics, dict):
            return
        seen = False
        for key, name in _LATENCY_KEYS.items():
            value = metrics.get(key)
            if isinstance(value, int | float) and value >= 0:
                self._samples[name].append(float(value) * 1000.0)
                seen = True
        if seen:
            self._turns += 1

    @property
    def turns(self) -> int:
        """How many assistant turns carried at least one latency figure."""
        return self._turns

    def summary(self) -> SessionLatency:
        """p50/p95 in milliseconds, `None` where no turn reported the figure."""
        values: dict[str, Any] = {"turns": self._turns}
        for name, samples in self._samples.items():
            p50, p95 = percentile(samples, 0.5), percentile(samples, 0.95)
            values[f"{name}_p50"] = round(p50, 1) if p50 is not None else None
            values[f"{name}_p95"] = round(p95, 1) if p95 is not None else None
        return SessionLatency(**values)


def bind_session_context(*, session_id: str, agent_id: str, job_id: str) -> None:
    """Bind the per-job identifiers onto every subsequent log line in this task."""
    structlog.contextvars.bind_contextvars(session_id=session_id, agent_id=agent_id, job_id=job_id)


def redact_arguments(raw: str) -> dict[str, Any]:
    """Parse a tool's JSON arguments, masking anything that looks like a secret.

    Args:
        raw: The `FunctionCall.arguments` JSON string.

    Returns:
        A dict safe to log and to post as an event payload. Unparseable
        arguments collapse to `{"_raw_len": n}` rather than leaking the text.
    """
    import json  # noqa: PLC0415  (only needed on the tool path)

    try:
        parsed = json.loads(raw or "{}")
    except ValueError:
        return {"_raw_len": len(raw or "")}
    if not isinstance(parsed, dict):
        return {"_value_type": type(parsed).__name__}
    return {key: ("***" if key.lower() in _REDACTED_ARG_KEYS else value) for key, value in parsed.items()}


def transcript_from_history(history: lk_llm.ChatContext) -> list[TranscriptTurn]:
    """Convert `session.history` into storable transcript turns.

    Only user and assistant `ChatMessage` items survive; `ImageContent` is
    dropped because `ChatMessage.text_content` returns text parts only, so
    frames never reach the database.
    """
    turns: list[TranscriptTurn] = []
    for item in history.items:
        if not isinstance(item, lk_llm.ChatMessage) or item.role not in ("user", "assistant"):
            continue
        text = item.text_content
        if not text:
            continue
        turns.append(
            TranscriptTurn(
                role=item.role,
                text=text,
                ts=item.created_at,
                interrupted=bool(item.interrupted),
            )
        )
    return turns


class SessionObserver:
    """Collects session telemetry and ships it to the api.

    Attach to a session with :meth:`attach` before `session.start`, then call
    :meth:`shutdown` from the job's shutdown callback.
    """

    def __init__(
        self,
        *,
        session_id: str,
        client: ConfigClientProtocol,
        flush_interval_s: float = _FLUSH_INTERVAL_S,
    ) -> None:
        self._session_id = session_id
        self._client = client
        self._flush_interval_s = flush_interval_s
        self._buffer: list[SessionEventIn] = []
        self._flush_task: asyncio.Task[None] | None = None
        self._session: AgentSession[Any] | None = None
        self._usage: dict[str, Any] = {}
        self._tool_started_at: dict[str, float] = {}
        self._tool_names: dict[str, str] = {}
        self._latency = LatencyCollector()
        self._closed = False
        #: The transcript posted with the summary (the QA judge scores exactly this).
        self.transcript: list[TranscriptTurn] = []

    # ------------------------------------------------------------------ events

    def record(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        """Queue one session event for delivery.

        Args:
            event_type: One of the `type` values in docs/CONTRACTS.md §7.
            payload: JSON-serialisable detail; must never contain a secret.
        """
        if self._closed:
            return
        self._buffer.append(SessionEventIn(ts=time.time(), type=event_type, payload=payload or {}))
        self._ensure_flush_loop()
        if len(self._buffer) >= _FLUSH_THRESHOLD:
            self._schedule_flush()

    async def flush(self) -> None:
        """Send and clear the buffered events."""
        if not self._buffer:
            return
        batch, self._buffer = self._buffer, []
        await self._client.post_events(self._session_id, batch)

    def _ensure_flush_loop(self) -> None:
        """Start the periodic flush on the first event, not on `attach`.

        A session that never records anything (a job that fails during build)
        then leaves no pending task behind.
        """
        if self._flush_task is None:
            self._flush_task = asyncio.create_task(self._flush_loop())

    def _schedule_flush(self) -> None:
        task = asyncio.create_task(self.flush())
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval_s)
            try:
                await self.flush()
            except Exception:
                logger.warning("event flush failed", exc_info=True)

    # ------------------------------------------------------------- attachment

    def attach(self, session: AgentSession[Any]) -> None:
        """Subscribe to the session's telemetry events and start the flush loop."""
        self._session = session
        session.on("agent_state_changed", self._on_agent_state)
        session.on("conversation_item_added", self._on_conversation_item)
        session.on("tool_execution_updated", self._on_tool_execution)
        session.on("session_usage_updated", self._on_usage)
        session.on("error", self._on_error)

    # Handlers below are plain `def`: livekit emits synchronously and a coroutine
    # handler would be scheduled as a task, running after the framework has
    # already consumed the event.

    def _on_agent_state(self, ev: AgentStateChangedEvent) -> None:
        self.record("agent_state", {"state": ev.new_state})

    def _on_conversation_item(self, ev: ConversationItemAddedEvent) -> None:
        item = ev.item
        if not isinstance(item, lk_llm.ChatMessage):
            return
        text = item.text_content
        if not text:
            return
        if item.role == "user":
            self.record("user_turn", {"text": text})
        elif item.role == "assistant":
            payload: dict[str, Any] = {"text": text, "interrupted": bool(item.interrupted)}
            if item.metrics is not None:
                payload["metrics"] = _dump(item.metrics)
                self._latency.add(item.metrics)
            self.record("agent_turn", payload)

    def _on_tool_execution(self, ev: ToolExecutionUpdatedEvent) -> None:
        update = ev.update
        if update.type == "tool_call_started":
            call = update.function_call
            self._tool_started_at[call.call_id] = time.time()
            # `ToolCallEnded` carries only the call id; remember the name for its event.
            self._tool_names[call.call_id] = call.name
            self.record(
                "tool_call_started",
                {
                    "call_id": call.call_id,
                    "tool": call.name,
                    "args_redacted": redact_arguments(call.arguments),
                },
            )
            logger.debug("tool call started", tool=call.name, call_id=call.call_id)
        elif update.type == "tool_call_ended":
            started = self._tool_started_at.pop(update.call_id, None)
            duration_ms = int((time.time() - started) * 1000) if started is not None else None
            preview = (update.message or "")[:_RESULT_PREVIEW_CHARS]
            self.record(
                "tool_call_ended",
                {
                    "call_id": update.call_id,
                    "tool": self._tool_names.pop(update.call_id, ""),
                    "status": update.status,
                    "duration_ms": duration_ms,
                    "result_preview": preview,
                },
            )
            logger.debug(
                "tool call ended",
                call_id=update.call_id,
                status=update.status,
                duration_ms=duration_ms,
            )

    def _on_usage(self, ev: SessionUsageUpdatedEvent) -> None:
        self._usage = _dump(ev.usage)
        self.record("metrics", {"kind": "session_usage", "data": self._usage})

    def _on_error(self, ev: ErrorEvent) -> None:
        self.record("error", {"message": str(ev.error)})

    # ----------------------------------------------------------------- summary

    @property
    def usage(self) -> dict[str, Any]:
        """The latest `AgentSessionUsage` snapshot, as a plain dict."""
        return dict(self._usage)

    @property
    def latency(self) -> SessionLatency:
        """The session's latency percentiles so far."""
        return self._latency.summary()

    async def shutdown(
        self,
        *,
        reason: str,
        status: str = "ended",
        error: str | None = None,
        final_ui_state: UiState | None = None,
        flow: FlowState | None = None,
    ) -> None:
        """Flush events and `PUT` the session summary. Safe to call twice.

        `flow` is a flow session's final `FlowState` (R-V2-8): its end-node
        disposition and extracted variables travel in the summary, the one
        terminal write. Prompt agents pass nothing and send the defaults.
        """
        if self._closed:
            return
        self._closed = True

        if self._flush_task is not None:
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task
            self._flush_task = None

        transcript: list[TranscriptTurn] = []
        if self._session is not None:
            try:
                transcript = transcript_from_history(self._session.history)
            except Exception:
                logger.warning("could not build the transcript from session history", exc_info=True)
        self.transcript = transcript

        self._buffer.append(SessionEventIn(ts=time.time(), type="session_ended", payload={"reason": reason}))
        await self.flush()

        if self._latency.turns:
            # Cost lines are computed by the api from `usage` (V2-12), so none are sent here.
            metrics = SessionMetricsIn(latency=self._latency.summary())
            await self._client.post_metrics(self._session_id, metrics)

        summary = SessionSummaryIn(
            status="failed" if status == "failed" else "ended",
            usage=self.usage,
            transcript=transcript,
            final_ui_state=final_ui_state,
            error=error,
            disposition=flow.disposition if flow is not None else None,
            variables=dict(flow.variables) if flow is not None else {},
        )
        await self._client.put_summary(self._session_id, summary)
        logger.info(
            "session summary posted",
            reason=reason,
            status=summary.status,
            turns=len(transcript),
        )


def _dump(obj: Any) -> dict[str, Any]:
    """Best-effort JSON-safe dict for a metrics/usage dataclass, model or TypedDict."""
    if isinstance(obj, dict):
        # `ChatMessage.metrics` is a `MetricsReport` TypedDict of floats/strings.
        return dict(obj)
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        result = dump(mode="json")
        return result if isinstance(result, dict) else {"value": result}
    try:
        import dataclasses  # noqa: PLC0415

        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return dataclasses.asdict(obj)
    except Exception:  # pragma: no cover - defensive
        pass
    return {"repr": repr(obj)}
