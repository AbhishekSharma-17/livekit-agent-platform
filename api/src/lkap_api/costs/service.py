"""Cost computation: `pricing.PRICES` x real usage -> `session_costs` (CONTRACTS-V2 §1.4/§4.2).

Two write paths call into this module, both idempotent so a retried or
duplicated call never double-charges a session:

* :func:`cost_session` — called once from the session summary (real LLM/STT/TTS
  token, character and audio-duration usage) and, when the pipeline has an
  avatar, an avatar-minutes line from wall-clock session duration.
* :func:`add_egress_cost_line` — called from recording finalisation (the
  `egress_ended` webhook or the worker's shutdown-time report) once a
  recording's duration is known; egress happens after the summary, so it is
  a separate, later write against the same `session_costs` table.

Only **priced** lines (`cost_usd is not None`) are ever written to
`session_costs`: `unit_price_usd`/`cost_usd` there are `NOT NULL` columns and
there is no `note` column, so an unpriced line has nowhere honest to live in
that table — writing `0` would be exactly the silent-zero this package must
never produce (PLAN-V2 V2-12 card; `pricing.py`'s own docstring). Unpriced
lines are instead recomputed on every read by :func:`render_cost` (a pure
function of the stored `usage` blob and the resolved pipeline), so the
console still shows every line item with `note="no price"` even though
nothing was persisted for it. `sessions.cost_usd` and `SessionCost.total_usd`
are always the sum of **persisted, priced** lines, or `None` when there are
none — never coerced to `0`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast

from lkap_contracts import pricing
from lkap_contracts.agent_config import AgentConfig, PipelineConfig
from lkap_contracts.api_models import CostLine
from lkap_contracts.api_models import SessionCost as SessionCostOut
from lkap_contracts.pricing import Unit
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.costs.mapping import ref_for_usage_type, resolve_model
from lkap_api.db.models import Agent, AgentConfigVersion
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import SessionCost as SessionCostRow
from lkap_api.logging import get_logger

log = get_logger(__name__)

_QUANT = Decimal("0.000001")


@dataclass(slots=True, frozen=True)
class ComputedLine:
    """One usage line, priced or not — the pure output of :func:`compute_usage_lines`."""

    provider_id: str
    model: str | None
    unit: Unit
    quantity: Decimal
    unit_price_usd: Decimal | None = None
    cost_usd: Decimal | None = None
    note: str | None = None

    @property
    def key(self) -> tuple[str, str | None, str]:
        """Identity used to avoid rendering an unpriced duplicate of a persisted line."""
        return (self.provider_id, self.model, self.unit)


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if value <= 0:
        return None
    return Decimal(str(value))


def price_line(provider_id: str, model: str | None, unit: Unit, quantity: Decimal) -> ComputedLine:
    """Price one `(provider, model, unit, quantity)` triple against `pricing.PRICES`.

    Never returns a zero cost for an unknown price: `cost_usd`/`unit_price_usd`
    are `None` with `note="no price"` instead (`pricing.lookup`'s contract).
    """
    price = pricing.lookup(provider_id, model, unit)
    if price is None:
        return ComputedLine(provider_id, model, unit, quantity, note="no price")
    cost = (quantity * price.usd_per_unit).quantize(_QUANT, rounding=ROUND_HALF_UP)
    return ComputedLine(provider_id, model, unit, quantity, unit_price_usd=price.usd_per_unit, cost_usd=cost)


def compute_usage_lines(pipeline: PipelineConfig, usage: dict[str, Any] | None) -> list[ComputedLine]:
    """Turn a session's `usage` blob into priced (and honestly unpriced) cost lines.

    Args:
        pipeline: The `AgentConfig.pipeline` the session actually ran with
            (from the `agent_config_versions` row at `session.config_version`,
            not necessarily the agent's *current* config).
        usage: `sessions.usage` — `dataclasses.asdict(AgentSessionUsage)`
            (`{"model_usage": [...]}`), or `None`/malformed, in which case an
            empty list is returned rather than raising.

    Returns:
        One line per non-zero (provider, model, unit) triple found in
        `usage["model_usage"]`. `interruption_usage`/`eot_usage` entries and
        entries whose kind has no bound pipeline slot are skipped (see
        `lkap_api.costs.mapping`'s module docstring).
    """
    entries = usage.get("model_usage") if isinstance(usage, dict) else None
    if not isinstance(entries, list):
        return []
    lines: list[ComputedLine] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        usage_type = entry.get("type")
        if not isinstance(usage_type, str):
            continue
        ref = ref_for_usage_type(usage_type, pipeline)
        if ref is None:
            continue
        model = resolve_model(entry, ref)
        if usage_type == "llm_usage":
            for key, unit in (("input_tokens", "tokens_in"), ("output_tokens", "tokens_out")):
                qty = _decimal(entry.get(key))
                if qty is not None:
                    lines.append(price_line(ref.provider_id, model, cast(Unit, unit), qty))
        elif usage_type == "tts_usage":
            chars = _decimal(entry.get("characters_count"))
            audio = _decimal(entry.get("audio_duration"))
            if chars is not None:
                lines.append(price_line(ref.provider_id, model, "chars", chars))
            elif audio is not None:
                lines.append(price_line(ref.provider_id, model, "audio_s_out", audio))
        elif usage_type == "stt_usage":
            audio = _decimal(entry.get("audio_duration"))
            if audio is not None:
                lines.append(price_line(ref.provider_id, model, "audio_s_in", audio))
    return lines


async def config_for_session(db: AsyncSession, session: SessionRow) -> AgentConfig | None:
    """The `AgentConfig` the session actually ran (its pinned version, else the agent's current one)."""
    version_row = await db.scalar(
        select(AgentConfigVersion).where(
            AgentConfigVersion.agent_id == session.agent_id,
            AgentConfigVersion.config_version == session.config_version,
        )
    )
    if version_row is not None:
        return AgentConfig.model_validate(version_row.config)
    agent = (
        await db.execute(
            select(Agent).where(Agent.id == session.agent_id, Agent.workspace_id == session.workspace_id)
        )
    ).scalar_one_or_none()
    if agent is None:
        return None
    return AgentConfig.model_validate(agent.config)


async def _persisted_lines(db: AsyncSession, session_id: str) -> list[SessionCostRow]:
    return list(
        (await db.execute(select(SessionCostRow).where(SessionCostRow.session_id == session_id)))
        .scalars()
        .all()
    )


def _persist(db: AsyncSession, session: SessionRow, line: ComputedLine) -> None:
    assert line.cost_usd is not None and line.unit_price_usd is not None  # noqa: S101 - internal invariant
    db.add(
        SessionCostRow(
            session_id=session.id,
            provider_id=line.provider_id,
            model=line.model or "",
            unit=line.unit,
            quantity=float(line.quantity),
            unit_price_usd=float(line.unit_price_usd),
            cost_usd=float(line.cost_usd),
            price_version=pricing.PRICE_VERSION,
        )
    )


async def _resync_total(db: AsyncSession, session: SessionRow) -> None:
    # `Database.sessionmaker` sets `autoflush=False` (see `db/session.py`), so
    # a just-`db.add()`-ed `SessionCostRow` is invisible to a `SELECT` on this
    # same session until explicitly flushed.
    await db.flush()
    rows = await _persisted_lines(db, session.id)
    session.cost_usd = float(sum((Decimal(str(r.cost_usd)) for r in rows), Decimal(0))) if rows else None


async def cost_session(db: AsyncSession, session: SessionRow) -> None:
    """Price a finished session's usage into `session_costs`/`sessions.cost_usd`.

    Idempotent: a session that already has `session_costs` rows is left
    untouched (a retried summary, or a second call from a lazy read-path,
    never double-charges). No-ops when `session.usage` was never posted.
    """
    if not session.usage:
        return
    if await db.scalar(select(SessionCostRow.id).where(SessionCostRow.session_id == session.id).limit(1)):
        return
    config = await config_for_session(db, session)
    if config is None:
        log.warning("cost_session_no_config", session_id=session.id)
        return
    for line in compute_usage_lines(config.pipeline, session.usage):
        if line.cost_usd is not None:
            _persist(db, session, line)
    if config.pipeline.avatar is not None and session.started_at is not None and session.ended_at is not None:
        minutes = Decimal(str((session.ended_at - session.started_at).total_seconds())) / Decimal(60)
        if minutes > 0:
            avatar_line = price_line(
                config.pipeline.avatar.provider_id, config.pipeline.avatar.model, "minutes", minutes
            )
            if avatar_line.cost_usd is not None:
                _persist(db, session, avatar_line)
    await _resync_total(db, session)
    await db.flush()
    log.info("session_costed", session_id=session.id, cost_usd=session.cost_usd)


async def add_egress_cost_line(db: AsyncSession, session: SessionRow) -> None:
    """Add an egress-minutes line once `recording_duration_s` is known (finalisation time).

    Idempotent per session (checks for an existing `livekit-egress` row
    first); a no-op while `pricing.PRICES` has no entry for it, which is the
    documented, honest state today (`pricing.py`: LiveKit Inference/Egress
    pricing has no single verifiable per-unit figure) — the line still shows
    up in `render_cost` with `note="no price"` either way.
    """
    if not session.recording_duration_s:
        return
    exists = await db.scalar(
        select(SessionCostRow.id)
        .where(SessionCostRow.session_id == session.id, SessionCostRow.provider_id == "livekit-egress")
        .limit(1)
    )
    if exists:
        return
    minutes = Decimal(str(session.recording_duration_s)) / Decimal(60)
    if minutes <= 0:
        return
    line = price_line("livekit-egress", None, "minutes", minutes)
    if line.cost_usd is not None:
        _persist(db, session, line)
        await _resync_total(db, session)
        await db.flush()


def render_cost(
    session: SessionRow, persisted: Sequence[SessionCostRow], config: AgentConfig | None
) -> SessionCostOut:
    """Build the `SessionCost` API block: persisted priced lines + live unpriced ones.

    `total_usd` is the sum of the persisted (priced) rows only, exactly what
    `sessions.cost_usd` holds — `None` when there are none, never `0`.
    """
    lines = [
        CostLine(
            provider_id=row.provider_id,
            model=row.model or None,
            unit=cast(Unit, row.unit),
            quantity=Decimal(str(row.quantity)),
            unit_price_usd=Decimal(str(row.unit_price_usd)),
            cost_usd=Decimal(str(row.cost_usd)),
        )
        for row in persisted
    ]
    seen = {(row.provider_id, row.model or None, row.unit) for row in persisted}
    if config is not None:
        computed = list(compute_usage_lines(config.pipeline, session.usage))
        if session.recording_duration_s:
            minutes = Decimal(str(session.recording_duration_s)) / Decimal(60)
            if minutes > 0:
                computed.append(price_line("livekit-egress", None, "minutes", minutes))
        if config.pipeline.avatar is not None and session.started_at and session.ended_at:
            avatar_minutes = Decimal(str((session.ended_at - session.started_at).total_seconds())) / Decimal(
                60
            )
            if avatar_minutes > 0:
                computed.append(
                    price_line(
                        config.pipeline.avatar.provider_id,
                        config.pipeline.avatar.model,
                        "minutes",
                        avatar_minutes,
                    )
                )
        for line in computed:
            if line.cost_usd is not None or line.key in seen:
                continue
            seen.add(line.key)
            lines.append(
                CostLine(
                    provider_id=line.provider_id,
                    model=line.model,
                    unit=line.unit,
                    quantity=line.quantity,
                    note=line.note,
                )
            )
    total = sum((Decimal(str(row.cost_usd)) for row in persisted), Decimal(0)) if persisted else None
    return SessionCostOut(total_usd=total, lines=lines)
