"""Session telemetry: structlog context, api events, usage and transcript.

Everything the console shows about a past session originates here
(docs/ARCHITECTURE.md §14, event `type` values in docs/CONTRACTS.md §7).

Event delivery is buffered and flushed on a timer so a chatty session does not
turn into one HTTP round-trip per token, and every api call is best-effort: a
telemetry failure must never end a live conversation.

Two deviations from the plan text, both verified against livekit-agents 1.8.2:

* Tool timing comes from the `tool_execution_updated` event
  (`ToolCallStarted` / `ToolCallEnded`, both carrying `call_id`) rather than
  hand-instrumenting every tool. `ToolCallUpdated` and `ToolReplyUpdated` become the
  `tool_call_updated` and `tool_reply` events of background tools (V4-12, D-V4-38).
* Usage totals and the CONTRACTS `metrics` events come from
  `session_usage_updated` (`AgentSessionUsage`). `metrics.UsageCollector` still
  exists but warns on construction, and subscribing to `metrics_collected` makes
  `AgentSession` log a deprecation warning on every session, so neither is used;
  per-turn latency rides along on `ChatMessage.metrics` instead.
  **Exception (V4-17, D-V4-45):** when the workspace opted into cost
  reconciliation (`ResolvedAgentConfig.cost_reconcile` non-empty) the observer
  does subscribe to `metrics_collected`: it is the only place the SDK exposes
  each LLM/STT/TTS call's vendor `request_id` (OpenRouter's `gen-…` id for its
  `/generation` lookup). The SDK's deprecation warning is then expected, once
  per session, and an `info` line says why just before it. The ids travel as
  one `metrics {kind: "provider_requests"}` event just before the summary;
  never a prompt, a completion or a secret. The vendor's in-band cost field on
  a streamed response is not read (the SDK's stream parser drops it).

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
import re
import time
from collections.abc import Sequence
from typing import Any, Final

import structlog
from livekit.agents import (
    AgentSession,
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    ErrorEvent,
    MetricsCollectedEvent,
    SessionUsageUpdatedEvent,
    ToolExecutionUpdatedEvent,
)
from livekit.agents import llm as lk_llm
from livekit.agents.metrics import LLMMetrics, STTMetrics, TTSMetrics
from lkap_contracts.api_models import (
    LocaleEvent,
    LocaleSource,
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
from lkap_agent.tools.provider import REAUTH_MESSAGE

__all__ = [
    "LOCALE_EVENT",
    "MAX_PROVIDER_REQUEST_IDS",
    "LatencyCollector",
    "ProviderRequestCollector",
    "SessionObserver",
    "bind_session_context",
    "locale_event_payload",
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
#: D-V4-45: the most per-request ids one session reports; later ones are only counted.
MAX_PROVIDER_REQUEST_IDS: Final[int] = 2000


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


#: The SDK's ids for a deferred call's entries: ``{call_id}_update_N`` and ``{call_id}_final``.
_ENTRY_SUFFIX = re.compile(r"_(?:final|update_\d+)$")


def _base_call_ids(entry_ids: list[str]) -> list[str]:
    """The tool calls a deferred reply covers, in order, from its entry ids (no duplicates)."""
    return list(dict.fromkeys(_ENTRY_SUFFIX.sub("", entry_id) for entry_id in entry_ids))


#: R-V5-10: recorded once at session start (`lkap_agent.locale.ensure_session_locale`); the
#: api copies its `caller_timezone` into the summary's `usage`.
LOCALE_EVENT: Final[str] = "locale"


def locale_event_payload(
    *, caller_timezone: str, source: LocaleSource, business_timezone: str
) -> dict[str, Any]:
    """The `locale` event's payload (`LocaleEvent`, docs/CONTRACTS.md §7)."""
    return LocaleEvent(
        caller_timezone=caller_timezone, source=source, business_timezone=business_timezone
    ).model_dump()


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
        cost_reconcile: Sequence[str] = (),
    ) -> None:
        """Build an observer for one session.

        Args:
            session_id: The api's session id.
            client: Where events, metrics and the summary are posted.
            flush_interval_s: How long a non-full event buffer waits.
            cost_reconcile: ``ResolvedAgentConfig.cost_reconcile`` (V4-17). Non-empty
                subscribes to ``metrics_collected`` and posts the per-request ids.
        """
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
        self._cost_reconcile = tuple(cost_reconcile)
        self._requests: ProviderRequestCollector | None = (
            ProviderRequestCollector() if self._cost_reconcile else None
        )
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
        if self._requests is not None:
            logger.info(
                "metrics_collected subscribed for cost reconciliation; "
                "the SDK's deprecation warning that follows is expected",
                vendors=list(self._cost_reconcile),
            )
            session.on("metrics_collected", self._on_metrics)

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
            tool_name = self._tool_names.pop(update.call_id, "")
            self.record(
                "tool_call_ended",
                {
                    "call_id": update.call_id,
                    "tool": tool_name,
                    "status": update.status,
                    "duration_ms": duration_ms,
                    "result_preview": preview,
                },
            )
            if update.status == "error" and REAUTH_MESSAGE in (update.message or ""):
                # V5-47 (COMPOSIO.md D-V5-C9): a connected app's action failed because its
                # connection needs a person; the console shows "Needs reconnect".
                self.record("tool_needs_reauth", {"call_id": update.call_id, "tool": tool_name})
            logger.debug(
                "tool call ended",
                call_id=update.call_id,
                status=update.status,
                duration_ms=duration_ms,
            )
        elif update.type == "tool_call_updated":
            # D-V4-38: a background tool's progress (its first update is the announcement).
            self.record(
                "tool_call_updated",
                {
                    "call_id": update.call_id,
                    "tool": self._tool_names.get(update.call_id, ""),
                    "message_preview": (update.message or "")[:_RESULT_PREVIEW_CHARS],
                },
            )
        elif update.type == "tool_reply_updated":
            # The deferred reply that voices background results: scheduled, then
            # completed / interrupted / skipped (the model had already said it).
            self.record(
                "tool_reply",
                {
                    "call_ids": _base_call_ids(update.update_ids),
                    "status": update.status,
                    "speech_id": update.speech_id,
                },
            )

    def _on_usage(self, ev: SessionUsageUpdatedEvent) -> None:
        self._usage = _dump(ev.usage)
        self.record("metrics", {"kind": "session_usage", "data": self._usage})

    def _on_error(self, ev: ErrorEvent) -> None:
        self.record("error", {"message": str(ev.error)})

    def _on_metrics(self, ev: MetricsCollectedEvent) -> None:
        if self._requests is not None:
            self._requests.add(ev.metrics)

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

        if self._requests is not None:
            # Appended directly (`record` is closed by now); it rides the last flush,
            # so the api has it when the summary below prices the session.
            self._buffer.append(
                SessionEventIn(
                    ts=time.time(),
                    type="metrics",
                    payload={"kind": "provider_requests", "data": self._requests.data()},
                )
            )
            logger.info(
                "provider request ids posted",
                ids=self._requests.kept,
                dropped=self._requests.dropped,
            )
        self._buffer.append(SessionEventIn(ts=time.time(), type="session_ended", payload={"reason": reason}))
        # The summary is the one terminal write: a failed event or metrics post
        # must not skip it (asks #33).
        try:
            await self.flush()
        except Exception:
            logger.warning("could not flush the last session events", exc_info=True)

        if self._latency.turns:
            # Cost lines are computed by the api from `usage` (V2-12), so none are sent here.
            metrics = SessionMetricsIn(latency=self._latency.summary())
            try:
                await self._client.post_metrics(self._session_id, metrics)
            except Exception:
                logger.warning("could not post the session metrics", exc_info=True)

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


class ProviderRequestCollector:
    """Per-request vendor ids of a session's LLM, STT and TTS calls (V4-17, D-V4-45).

    Keeps ``{request_id, provider, model}`` only. ``provider``/``model`` are the
    SDK's display strings (``Metadata.model_provider``/``model_name``), which the
    api never trusts for pricing: it maps each kind to its pipeline slot instead.
    At most :data:`MAX_PROVIDER_REQUEST_IDS` ids across the three kinds; later
    ones are counted in ``dropped``. A repeated id (a streamed STT/TTS request
    reports several metrics under one id) is kept once. Realtime model metrics
    are ignored: no vendor that reconciles has a realtime model (R-V4-8).
    """

    def __init__(self, *, limit: int = MAX_PROVIDER_REQUEST_IDS) -> None:
        self._limit = limit
        self._seen: set[tuple[str, str]] = set()
        self._by_kind: dict[str, list[dict[str, str | None]]] = {"llm": [], "stt": [], "tts": []}
        self.dropped = 0

    @property
    def kept(self) -> int:
        """How many ids are held."""
        return len(self._seen)

    def add(self, metrics: Any) -> None:
        """Record one ``metrics_collected`` payload; anything but LLM/STT/TTS is ignored."""
        if isinstance(metrics, LLMMetrics):
            kind = "llm"
        elif isinstance(metrics, STTMetrics):
            kind = "stt"
        elif isinstance(metrics, TTSMetrics):
            kind = "tts"
        else:
            return
        request_id = metrics.request_id
        if not request_id or (kind, request_id) in self._seen:
            return
        if len(self._seen) >= self._limit:
            self.dropped += 1
            return
        self._seen.add((kind, request_id))
        meta = metrics.metadata
        self._by_kind[kind].append(
            {
                "request_id": request_id,
                "provider": meta.model_provider if meta is not None else None,
                "model": meta.model_name if meta is not None else None,
            }
        )

    def data(self) -> dict[str, Any]:
        """The event's ``data``: ``{llm, stt, tts, dropped}``."""
        out: dict[str, Any] = {kind: list(rows) for kind, rows in self._by_kind.items()}
        out["dropped"] = self.dropped
        return out


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
