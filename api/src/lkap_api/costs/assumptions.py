"""The usage model's assumptions: sourced defaults, workspace averages, request overrides (COSTS.md D-V4-41).

Every number the estimator multiplies a price by is a named :class:`Assumption`
with ``low``/``high`` bounds and a ``source``:

* ``default`` — the table below, each with the page or reasoning it comes from;
* ``workspace`` — derived from the workspace's own ended sessions of the last 30
  days (newest 500, at least :data:`MIN_SESSIONS` with usage), p25–p75 as the band;
* ``request`` — an override the caller sent (``low == high == value``).

Workspace averages are recomputed per request (the analytics route's trade-off)
behind a 10-minute in-process cache per workspace, so the session-creation
snapshot never scans sessions on the hot path twice in a row.
"""

from __future__ import annotations

import datetime as dt
import statistics
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.api_models import Assumption, AssumptionSource
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import Agent, SessionEvent, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.logging import get_logger

log = get_logger(__name__)

#: Ended sessions with usage a workspace needs before its averages replace the defaults.
MIN_SESSIONS = 10
#: How far back, and how many sessions at most, the averages look.
WINDOW_DAYS = 30
MAX_SESSIONS = 500
#: The in-process cache lifetime of one workspace's averages.
CACHE_TTL_S = 600.0

_OPENAI_REALTIME_COSTS = "https://developers.openai.com/api/docs/guides/realtime-costs"
_GOOGLE_PRICING = "https://ai.google.dev/gemini-api/docs/pricing"
_LK_BILLING = "https://docs.livekit.io/deploy/admin/billing/"

#: Audio tokens per second by realtime/TTS registry id: ``(in, out)``. Google documents
#: 25/s ("Audio tokens correspond to 25 tokens per second of audio", pricing page);
#: OpenAI documents 1 token per 100 ms of user audio and 1 per 50 ms of assistant
#: audio (realtime cost guide) — ask #92. Other vendors: unknown, so their audio
#: lines stay unpriced rather than guessed.
AUDIO_TOKENS_PER_S: dict[str, tuple[float, float, str]] = {
    "google-realtime": (25.0, 25.0, _GOOGLE_PRICING),
    "google-tts": (25.0, 25.0, _GOOGLE_PRICING),
    "openai-realtime": (10.0, 20.0, _OPENAI_REALTIME_COSTS),
}


@dataclass(frozen=True, slots=True)
class _Default:
    value: float | str
    low: float | None
    high: float | None
    unit: str | None
    label: str
    source_url: str | None = None


#: The defaults of D-V4-41, keyed by assumption name.
DEFAULTS: dict[str, _Default] = {
    "session_minutes": _Default(5.0, 2.0, 15.0, "minutes", "Call length"),
    "caller_talk_ratio": _Default(0.45, 0.30, 0.60, "share", "How much the caller talks"),
    "agent_talk_ratio": _Default(0.45, 0.30, 0.60, "share", "How much the agent talks"),
    "stt_billing": _Default(
        "stream",
        None,
        None,
        None,
        "How speech-to-text is billed (stream = every second of the call)",
        _LK_BILLING,
    ),
    "speech_wpm": _Default(150.0, 130.0, 170.0, "words/min", "Speaking speed"),
    "chars_per_word": _Default(6.0, 5.5, 6.5, "characters", "Characters per word"),
    "agent_turns_per_min": _Default(3.0, 2.0, 5.0, "replies/min", "Replies per minute"),
    "history_tokens_per_turn": _Default(140.0, 100.0, 200.0, "tokens", "Conversation growth per reply"),
    "output_tokens_per_turn": _Default(60.0, 40.0, 120.0, "tokens", "Words per reply"),
    "tool_calls_per_session": _Default(1.0, 0.0, 4.0, "calls", "Tool calls per call"),
    "kb_queries_per_turn": _Default(1.0, 0.0, 2.0, "lookups", "Knowledge lookups per reply"),
    "images_per_session": _Default(0.0, 0.0, 2.0, "images", "Images per call"),
    "participants": _Default(2.0, 2.0, 2.0, "people", "People in the call"),
}

#: Keys a request may override; anything else is rejected by the route (422).
OVERRIDABLE: frozenset[str] = frozenset(
    {*DEFAULTS, "prompt_tokens", "audio_tokens_in_per_s", "audio_tokens_out_per_s"}
)

#: Token cost of one tool schema, of one knowledge chunk, and of the panel schema (D-V4-41).
TOKENS_PER_TOOL = 120
TOKENS_PER_KB_CHUNK = 180
TOKENS_PANEL_SCHEMA = 200
CHARS_PER_TOKEN = 4


def measured_prompt_tokens(config: AgentConfig) -> int:
    """Estimate the fixed prompt size from the config: instructions, tool schemas, knowledge, panel.

    Args:
        config: The agent configuration.

    Returns:
        ``len(instructions)/4`` + 120 per tool schema (declared tools, the
        enabled built-ins, ``http_request`` when on) + ``top_k × 180`` when
        knowledge is auto-injected + 200 for a panel with blocks.
    """
    from lkap_contracts.tools import BUILTIN_TOOL_NAMES, VISION_TOOL_NAMES

    tokens = len(config.instructions) // CHARS_PER_TOKEN
    vision = config.capabilities.camera or config.capabilities.screen_share
    builtins = [
        name
        for name in BUILTIN_TOOL_NAMES
        if name not in config.tools.builtin_disabled
        and (vision or name not in VISION_TOOL_NAMES)
        and (name != "http_request" or config.tools.http_request_enabled)
        and (name != "search_knowledge" or bool(config.knowledge.kb_ids))
    ]
    tokens += TOKENS_PER_TOOL * (len(builtins) + len(config.tools.tool_ids))
    if config.knowledge.kb_ids and config.knowledge.auto_inject:
        tokens += TOKENS_PER_KB_CHUNK * config.knowledge.top_k
    if config.panel.blocks:
        tokens += TOKENS_PANEL_SCHEMA
    return tokens


def _default(key: str) -> Assumption:
    d = DEFAULTS[key]
    return Assumption(
        key=key,
        value=d.value,
        low=d.low,
        high=d.high,
        unit=d.unit,
        source="default",
        label=d.label,
        source_url=d.source_url,
    )


def default_assumptions(config: AgentConfig | None = None) -> dict[str, Assumption]:
    """The default assumption set; ``prompt_tokens`` and the audio rates depend on ``config``.

    Args:
        config: The agent being estimated (``None`` gives the config-free defaults only).

    Returns:
        Assumptions keyed by name.
    """
    out = {key: _default(key) for key in DEFAULTS}
    if config is None:
        return out
    if not (config.tools.tool_ids or config.tools.http_request_enabled or config.flow is not None):
        # No declared tools: the default tool call per session would be a phantom one.
        out["tool_calls_per_session"] = out["tool_calls_per_session"].model_copy(update={"value": 0.0})
    prompt = float(measured_prompt_tokens(config))
    out["prompt_tokens"] = Assumption(
        key="prompt_tokens",
        value=prompt,
        low=round(prompt * 0.8, 1),
        high=round(prompt * 1.3, 1),
        unit="tokens",
        source="default",
        label="Instructions and tools the agent reads every reply",
    )
    audio_ref = config.pipeline.realtime if config.pipeline.mode != "cascaded" else config.pipeline.tts
    if audio_ref is not None and audio_ref.provider_id in AUDIO_TOKENS_PER_S:
        rate_in, rate_out, url = AUDIO_TOKENS_PER_S[audio_ref.provider_id]
        for key, rate, label in (
            ("audio_tokens_in_per_s", rate_in, "Audio tokens per second heard"),
            ("audio_tokens_out_per_s", rate_out, "Audio tokens per second spoken"),
        ):
            out[key] = Assumption(
                key=key,
                value=rate,
                low=rate,
                high=rate,
                unit="tokens/s",
                source="default",
                label=label,
                source_url=url,
            )
    return out


def apply_overrides(
    base: Mapping[str, Assumption], overrides: Mapping[str, float | str] | None
) -> dict[str, Assumption]:
    """Layer request overrides on ``base`` (``source="request"``, the band collapses to the value).

    Raises:
        ValueError: For an unknown key or a value of the wrong kind.
    """
    out = dict(base)
    for key, value in (overrides or {}).items():
        if key not in OVERRIDABLE:
            raise ValueError(f"unknown assumption {key!r}")
        if key == "stt_billing":
            if value not in ("stream", "segments"):
                raise ValueError("stt_billing must be 'stream' or 'segments'")
            number: float | None = None
        else:
            if isinstance(value, str) or isinstance(value, bool):
                raise ValueError(f"assumption {key!r} takes a number")
            number = float(value)
            if number < 0 or (key.endswith("_ratio") and number > 1):
                raise ValueError(f"assumption {key!r} is out of range")
        prior = out.get(key)
        out[key] = Assumption(
            key=key,
            value=value if number is None else number,
            low=number,
            high=number,
            unit=prior.unit if prior else None,
            source="request",
            label=prior.label if prior else key.replace("_", " "),
            source_url=None,
        )
    return out


# ----------------------------------------------------------------- workspace averages
@dataclass(frozen=True, slots=True)
class WorkspaceAverages:
    """Assumptions derived from a workspace's own sessions (empty below :data:`MIN_SESSIONS`)."""

    assumptions: dict[str, Assumption]
    sessions_sampled: int


_CACHE: dict[str, tuple[float, WorkspaceAverages]] = {}


def clear_cache() -> None:
    """Forget every cached workspace average (tests; a price or config edit does not need it)."""
    _CACHE.clear()


def _quartiles(values: Sequence[float]) -> tuple[float, float, float]:
    ordered = sorted(values)
    median = statistics.median(ordered)
    if len(ordered) < 2:
        return median, median, median
    q = statistics.quantiles(ordered, n=4, method="inclusive")
    return q[0], median, q[2]


def _derived(key: str, values: Sequence[float], digits: int = 3) -> Assumption:
    low, mid, high = _quartiles(values)
    d = DEFAULTS[key]
    return Assumption(
        key=key,
        value=round(mid, digits),
        low=round(low, digits),
        high=round(high, digits),
        unit=d.unit,
        source="workspace",
        label=d.label,
    )


def _usage_entries(usage: Any, kind: str) -> Iterable[Mapping[str, Any]]:
    entries = usage.get("model_usage") if isinstance(usage, Mapping) else None
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, Mapping) and entry.get("type") == kind:
            yield entry


def _total(usage: Any, kind: str, field: str) -> float:
    total = 0.0
    for entry in _usage_entries(usage, kind):
        value = entry.get(field)
        if isinstance(value, int | float) and not isinstance(value, bool):
            total += float(value)
    return total


async def workspace_assumptions(
    db: AsyncSession, workspace_id: str, *, use_cache: bool = True
) -> WorkspaceAverages:
    """Derive assumptions from the workspace's ended sessions (D-V4-41 "Workspace averages").

    Args:
        db: A session scoped to the workspace.
        workspace_id: The workspace.
        use_cache: Read/write the 10-minute in-process cache.

    Returns:
        The derived assumptions (``source="workspace"``) and how many sessions
        they came from; no assumptions when fewer than :data:`MIN_SESSIONS` qualify.
    """
    now_s = time.monotonic()
    if use_cache:
        hit = _CACHE.get(workspace_id)
        if hit is not None and now_s - hit[0] < CACHE_TTL_S:
            return hit[1]
    since = utcnow() - dt.timedelta(days=WINDOW_DAYS)
    rows = (
        await db.execute(
            select(
                SessionRow.id,
                SessionRow.agent_id,
                SessionRow.started_at,
                SessionRow.ended_at,
                SessionRow.usage,
                SessionRow.latency,
            )
            .where(
                SessionRow.workspace_id == workspace_id,
                SessionRow.deleted_at.is_(None),
                SessionRow.status == "ended",
                SessionRow.usage.is_not(None),
                SessionRow.started_at.is_not(None),
                SessionRow.ended_at.is_not(None),
                SessionRow.created_at >= since,
            )
            .order_by(SessionRow.created_at.desc())
            .limit(MAX_SESSIONS)
        )
    ).all()
    sessions = [
        (sid, agent_id, (ended - started).total_seconds() / 60.0, usage, latency)
        for sid, agent_id, started, ended, usage, latency in rows
        if isinstance(usage, Mapping) and ended > started
    ]
    result = WorkspaceAverages({}, len(sessions))
    if len(sessions) >= MIN_SESSIONS:
        result = WorkspaceAverages(await _derive(db, workspace_id, sessions), len(sessions))
    if use_cache:
        _CACHE[workspace_id] = (now_s, result)
    return result


async def _derive(
    db: AsyncSession, workspace_id: str, sessions: list[tuple[str, str, float, Any, Any]]
) -> dict[str, Assumption]:
    out: dict[str, Assumption] = {}
    minutes = [m for _, _, m, _, _ in sessions]
    out["session_minutes"] = _derived("session_minutes", minutes, 2)

    prompts: dict[str, int] = {}
    agent_ids = {agent_id for _, agent_id, _, _, _ in sessions}
    for agent_id, config in (
        await db.execute(
            select(Agent.id, Agent.config).where(Agent.workspace_id == workspace_id, Agent.id.in_(agent_ids))
        )
    ).all():
        try:
            prompts[agent_id] = measured_prompt_tokens(AgentConfig.model_validate(config))
        except ValueError:
            continue

    turns_per_min: list[float] = []
    out_per_turn: list[float] = []
    history_per_turn: list[float] = []
    talk_ratio: list[float] = []
    stt_s_per_min: list[float] = []
    for _, agent_id, mins, usage, latency in sessions:
        turns_raw = latency.get("turns") if isinstance(latency, Mapping) else None
        turns = turns_raw if isinstance(turns_raw, int) and turns_raw > 0 else usage.get("turns")
        if isinstance(turns, int) and turns > 0 and mins > 0:
            turns_per_min.append(turns / mins)
            output = _total(usage, "llm_usage", "output_tokens")
            if output > 0:
                out_per_turn.append(output / turns)
            prompt = prompts.get(agent_id)
            inputs = _total(usage, "llm_usage", "input_tokens")
            if prompt is not None and inputs > 0 and turns > 1:
                growth = (inputs - turns * prompt) * 2 / (turns * (turns + 1))
                if growth > 0:
                    history_per_turn.append(growth)
        chars = _total(usage, "tts_usage", "characters_count")
        if chars > 0 and mins > 0:
            talk_ratio.append(min(1.0, chars / mins / (150.0 * 6.0)))
        stt_s = _total(usage, "stt_usage", "audio_duration")
        if stt_s > 0 and mins > 0:
            stt_s_per_min.append(stt_s / mins)
    if len(turns_per_min) >= MIN_SESSIONS:
        out["agent_turns_per_min"] = _derived("agent_turns_per_min", turns_per_min, 2)
    if len(out_per_turn) >= MIN_SESSIONS:
        out["output_tokens_per_turn"] = _derived("output_tokens_per_turn", out_per_turn, 0)
    if len(history_per_turn) >= MIN_SESSIONS:
        out["history_tokens_per_turn"] = _derived("history_tokens_per_turn", history_per_turn, 0)
    if len(talk_ratio) >= MIN_SESSIONS:
        out["agent_talk_ratio"] = _derived("agent_talk_ratio", talk_ratio, 3)
    if len(stt_s_per_min) >= MIN_SESSIONS:
        billing = "stream" if statistics.median(stt_s_per_min) >= 50 else "segments"
        d = DEFAULTS["stt_billing"]
        out["stt_billing"] = Assumption(
            key="stt_billing",
            value=billing,
            unit=None,
            source="workspace",
            label=d.label,
            source_url=d.source_url,
        )

    ids = [sid for sid, _, _, _, _ in sessions]
    counts = dict(
        (
            await db.execute(
                select(SessionEvent.session_id, func.count(SessionEvent.id))
                .where(SessionEvent.session_id.in_(ids), SessionEvent.type == "tool_call_started")
                .group_by(SessionEvent.session_id)
            )
        )
        .tuples()
        .all()
    )
    out["tool_calls_per_session"] = _derived(
        "tool_calls_per_session", [float(counts.get(sid, 0)) for sid in ids], 2
    )
    return out


def merge(*layers: Mapping[str, Assumption]) -> dict[str, Assumption]:
    """Later layers win key by key (defaults, then workspace, then request)."""
    out: dict[str, Assumption] = {}
    for layer in layers:
        out.update(layer)
    return out


def source_of(assumptions: Mapping[str, Assumption], key: str) -> AssumptionSource | None:
    """The source of one assumption, if present."""
    found = assumptions.get(key)
    return found.source if found else None
