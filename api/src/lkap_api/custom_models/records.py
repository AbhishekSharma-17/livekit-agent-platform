"""`provider_models` rows: upsert, read, list, declare, catalog sightings (D-V4-24, D-V4-27).

One row per ``(workspace_id, provider_home, kind, model_id)``, where
``provider_home`` is :func:`lkap_contracts.providers.credential_home` of the
entry and ``kind`` its registry kind. Every query here carries the workspace
predicate (the tenant guard's rule, CONTRACTS-V2 §3.1).

"Tested" for validation (R-V4-24): the last test ran within
:data:`TESTED_WINDOW_S` (30 days) **with the credential fingerprint the slot
uses now**; a rotated key no longer matches and resets it. That window is
separate from the 10-minute re-run suppression V4-08's route applies.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any

from lkap_contracts.api_models import CatalogItem, ProviderModelOut
from lkap_contracts.providers import ModelCapabilities, ProviderSpec, credential_home
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.config_service import TESTED_WINDOW_S
from lkap_api.db.models import ProviderModel, new_id, utcnow

#: Default and ceiling of `GET /v1/providers/{id}/models?limit=`.
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 500

#: ``(provider_home, kind, model_id)`` — the per-workspace key of a record.
RecordKey = tuple[str, str, str]


def record_key(spec: ProviderSpec, model_id: str) -> RecordKey:
    """The ``(provider_home, kind, model_id)`` a model of ``spec`` is recorded under."""
    return (credential_home(spec), spec.kind, model_id)


def _capabilities(value: Any) -> ModelCapabilities | None:
    if not isinstance(value, dict):
        return None
    return ModelCapabilities.model_validate(value)


def to_out(row: ProviderModel) -> ProviderModelOut:
    """The API shape of a row (no secret is stored on it; the message is already scrubbed)."""
    return ProviderModelOut(
        id=row.id,
        provider_id=row.provider_id,
        provider_home=row.provider_home,
        kind=row.kind,  # a registry kind, written by this module only (validated on the way out)
        model_id=row.model_id,
        declared=_capabilities(row.declared),
        detected=_capabilities(row.detected),
        last_test_at=row.last_test_at,
        last_test_ok=row.last_test_ok,
        last_test_message=row.last_test_message,
        last_test_latency_ms=row.last_test_latency_ms,
        last_test_cost_usd=Decimal(str(row.last_test_cost_usd))
        if row.last_test_cost_usd is not None
        else None,
        last_test_credential_id=row.last_test_credential_id,
        last_test_fingerprint=row.last_test_fingerprint,
        catalog_seen_at=row.catalog_seen_at,
        catalog_missing_since=row.catalog_missing_since,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def current_test_result(
    record: ProviderModelOut | None,
    *,
    fingerprint: str | None,
    now: dt.datetime | None = None,
) -> bool | None:
    """The last test's ``ok`` if it still counts for validation, else ``None``.

    It counts when it ran within :data:`TESTED_WINDOW_S` and, for a slot that
    uses a credential (``fingerprint`` given), with that same fingerprint. A
    slot without a credential (LiveKit Inference) accepts any fingerprint.
    """
    if record is None or record.last_test_at is None or record.last_test_ok is None:
        return None
    now = now or utcnow()
    if (now - record.last_test_at).total_seconds() > TESTED_WINDOW_S:
        return None
    if fingerprint is not None and record.last_test_fingerprint != fingerprint:
        return None
    return record.last_test_ok


async def get_row(
    db: AsyncSession, *, workspace_id: str, spec: ProviderSpec, model_id: str
) -> ProviderModel | None:
    """The workspace's row for ``model_id`` under ``spec``'s home and kind, if any."""
    home, kind, _ = record_key(spec, model_id)
    row: ProviderModel | None = await db.scalar(
        select(ProviderModel).where(
            ProviderModel.workspace_id == workspace_id,
            ProviderModel.provider_home == home,
            ProviderModel.kind == kind,
            ProviderModel.model_id == model_id,
        )
    )
    return row


async def list_rows(
    db: AsyncSession,
    *,
    workspace_id: str,
    spec: ProviderSpec,
    custom_only: bool = False,
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[ProviderModel]:
    """The workspace's rows for ``spec``'s home and kind, most recently tested first.

    Args:
        custom_only: Leave out ids the registry lists (``spec.models``/``default_model``).
        limit: At most this many rows.
    """
    home = credential_home(spec)
    rows = (
        await db.execute(
            select(ProviderModel)
            .where(
                ProviderModel.workspace_id == workspace_id,
                ProviderModel.provider_home == home,
                ProviderModel.kind == spec.kind,
            )
            .order_by(
                ProviderModel.last_test_at.is_(None),
                ProviderModel.last_test_at.desc(),
                ProviderModel.updated_at.desc(),
            )
        )
    ).scalars()
    listed = registry_ids(spec) if custom_only else frozenset()
    out = [row for row in rows if row.model_id not in listed]
    return out[:limit]


def registry_ids(spec: ProviderSpec) -> frozenset[str]:
    """The ids the registry itself suggests for ``spec``."""
    ids = {m.id for m in spec.models}
    if spec.default_model:
        ids.add(spec.default_model)
    return frozenset(ids)


async def upsert(
    db: AsyncSession, *, workspace_id: str, spec: ProviderSpec, model_id: str, **values: Any
) -> ProviderModel:
    """Create or update the workspace's row for ``model_id``; ``values`` are column values.

    ``provider_id`` is set to ``spec.id`` (the entry the write came from).
    The caller has already validated ``model_id`` (``validate_model_id``).
    """
    row = await get_row(db, workspace_id=workspace_id, spec=spec, model_id=model_id)
    if row is None:
        home, kind, _ = record_key(spec, model_id)
        now = utcnow()
        row = ProviderModel(
            id=new_id(),
            workspace_id=workspace_id,
            provider_id=spec.id,
            provider_home=home,
            kind=kind,
            model_id=model_id,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.provider_id = spec.id
    for name, value in values.items():
        setattr(row, name, value)
    row.updated_at = utcnow()
    await db.flush()
    return row


async def declare(
    db: AsyncSession,
    *,
    workspace_id: str,
    spec: ProviderSpec,
    model_id: str,
    declared: ModelCapabilities,
) -> ProviderModel:
    """Store what an admin says ``model_id`` can do (``source`` is always ``declared``)."""
    body = declared.model_copy(update={"source": "declared"}).model_dump(mode="json")
    return await upsert(db, workspace_id=workspace_id, spec=spec, model_id=model_id, declared=body)


async def records_for_workspace(db: AsyncSession, *, workspace_id: str) -> dict[RecordKey, ProviderModelOut]:
    """Every record of the workspace keyed by ``(home, kind, model_id)`` (one query, for validation)."""
    rows = (
        await db.execute(select(ProviderModel).where(ProviderModel.workspace_id == workspace_id))
    ).scalars()
    return {(row.provider_home, row.kind, row.model_id): to_out(row) for row in rows}


# ---------------------------------------------------------------- catalog sightings
def _parse_date(value: Any, now: dt.datetime) -> dt.datetime:
    """A vendor deprecation date (epoch seconds or ISO text); ``now`` when unreadable."""
    if isinstance(value, bool):
        return now
    if isinstance(value, int | float):
        try:
            return dt.datetime.fromtimestamp(float(value), tz=dt.UTC)
        except (OverflowError, OSError, ValueError):
            return now
    if isinstance(value, str) and value:
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return now
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=dt.UTC)
    return now


def deprecation_date(meta: Mapping[str, Any], now: dt.datetime) -> dt.datetime | None:
    """The explicit vendor deprecation signal of a catalog item, if any (D-V4-27 (1)).

    OpenAI ``shutdown_date`` (the date), Mistral ``archived: true`` (now),
    Bedrock ``modelLifecycle.status == "LEGACY"`` (now).
    """
    shutdown = meta.get("shutdown_date")
    if shutdown not in (None, ""):
        return _parse_date(shutdown, now)
    if meta.get("archived") is True:
        return now
    lifecycle = meta.get("modelLifecycle")
    if isinstance(lifecycle, dict) and str(lifecycle.get("status", "")).upper() == "LEGACY":
        return now
    return None


async def _rows_for(db: AsyncSession, *, workspace_id: str, spec: ProviderSpec) -> Sequence[ProviderModel]:
    return (
        (
            await db.execute(
                select(ProviderModel).where(
                    ProviderModel.workspace_id == workspace_id,
                    ProviderModel.provider_home == credential_home(spec),
                    ProviderModel.kind == spec.kind,
                )
            )
        )
        .scalars()
        .all()
    )


async def mark_catalog_seen(
    db: AsyncSession,
    *,
    workspace_id: str,
    spec: ProviderSpec,
    model_ids: Iterable[str],
    now: dt.datetime,
    deprecations: Mapping[str, dt.datetime] | None = None,
) -> int:
    """Stamp ``catalog_seen_at`` on listed rows; set or clear ``catalog_missing_since``.

    A listed id with an explicit deprecation signal gets that date; any other
    listed id has ``catalog_missing_since`` cleared (it reappeared).

    Returns:
        How many rows were touched.
    """
    wanted = set(model_ids)
    deprecations = deprecations or {}
    touched = 0
    for row in await _rows_for(db, workspace_id=workspace_id, spec=spec):
        if row.model_id not in wanted:
            continue
        row.catalog_seen_at = now
        row.catalog_missing_since = deprecations.get(row.model_id)
        touched += 1
    await db.flush()
    return touched


async def mark_catalog_missing(
    db: AsyncSession, *, workspace_id: str, spec: ProviderSpec, model_ids: Iterable[str], now: dt.datetime
) -> int:
    """Set ``catalog_missing_since = now`` (if unset) on the rows of ``model_ids``.

    Returns:
        How many rows were newly marked.
    """
    wanted = set(model_ids)
    marked = 0
    for row in await _rows_for(db, workspace_id=workspace_id, spec=spec):
        if row.model_id in wanted and row.catalog_missing_since is None:
            row.catalog_missing_since = now
            marked += 1
    await db.flush()
    return marked


async def apply_catalog_sightings(
    db: AsyncSession,
    *,
    workspace_id: str,
    spec: ProviderSpec,
    previous_ids: Sequence[str] | None,
    items: Sequence[CatalogItem],
    now: dt.datetime,
) -> None:
    """Record one successful, filtered ``models`` fetch on the workspace's rows (R-V4-28).

    A row is marked missing only when its id was in the **previous cached fetch
    of the same entry, credential and filter** and is absent now; an id that was
    never in that list (or no previous fetch at all) is left alone, so a
    differently filtered list never flags anything.
    """
    current = {item.id for item in items}
    deprecations = {item.id: date for item in items if (date := deprecation_date(item.meta, now)) is not None}
    await mark_catalog_seen(
        db, workspace_id=workspace_id, spec=spec, model_ids=current, now=now, deprecations=deprecations
    )
    if previous_ids is not None:
        gone = set(previous_ids) - current
        if gone:
            await mark_catalog_missing(db, workspace_id=workspace_id, spec=spec, model_ids=gone, now=now)


__all__ = [
    "DEFAULT_LIST_LIMIT",
    "MAX_LIST_LIMIT",
    "TESTED_WINDOW_S",
    "RecordKey",
    "apply_catalog_sightings",
    "current_test_result",
    "declare",
    "deprecation_date",
    "get_row",
    "list_rows",
    "mark_catalog_missing",
    "mark_catalog_seen",
    "record_key",
    "records_for_workspace",
    "registry_ids",
    "to_out",
    "upsert",
]
