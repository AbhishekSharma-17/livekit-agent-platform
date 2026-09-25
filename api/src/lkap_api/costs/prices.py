"""Where a price comes from at request time: workspace prices, the cached OpenRouter sheet, the table.

* **Workspace prices** live in ``workspaces.settings["cost"]["prices"]`` (the
  telephony-policy precedent: no migration). :func:`workspace_prices` reads them
  leniently (a broken row is dropped and logged, never widened);
  :func:`store_workspace_prices` validates and writes the full list (≤ 100 rows,
  ``provider_id`` a registry or pseudo id, USD) and audits
  ``workspace.prices_updated``.
* **The live sheet** is the cached ``models`` catalog of an OpenRouter entry
  (``provider_catalog_cache``); its item ``meta["pricing"]`` is USD per single
  unit. Nothing here calls a vendor: an uncached id simply has no live quote.
* :class:`PriceBook` binds both to :func:`lkap_contracts.pricing.quote` and is
  the ``quote`` callable the estimator and the costing take.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from lkap_contracts import pricing, providers
from lkap_contracts.common import ProviderRef
from lkap_contracts.pricing import PriceQuote, Unit, WorkspacePrice
from pydantic import ValidationError
from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth.audit import ActorType, record
from lkap_api.db.models import ProviderCatalogCache, Workspace, utcnow
from lkap_api.logging import get_logger

log = get_logger(__name__)

#: ``workspaces.settings`` key holding the cost settings (``prices`` today; V4-17 adds ``reconcile``).
SETTINGS_KEY = "cost"
MAX_WORKSPACE_PRICES = 100


class WorkspacePriceError(ValueError):
    """A workspace price list that cannot be stored (unknown id, too many rows)."""


def _is_openrouter(provider_id: str) -> bool:
    try:
        spec = providers.get(provider_id)
    except KeyError:
        return False
    return spec.catalog is not None and spec.catalog.adapter.startswith("openrouter")


def known_price_id(provider_id: str) -> bool:
    """Whether ``provider_id`` may carry a price: a registry id or a LiveKit Cloud pseudo id."""
    if provider_id in pricing.PSEUDO_PROVIDER_IDS:
        return True
    try:
        providers.get(provider_id)
    except KeyError:
        return False
    return True


def workspace_prices(settings: Mapping[str, Any] | None) -> list[WorkspacePrice]:
    """The prices stored in a workspace's ``settings`` (invalid rows dropped)."""
    cost = (settings or {}).get(SETTINGS_KEY)
    raw = cost.get("prices") if isinstance(cost, Mapping) else None
    out: list[WorkspacePrice] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            out.append(WorkspacePrice.model_validate(item))
        except ValidationError:
            log.warning("workspace_price_invalid")
    return out


async def load_workspace_prices(db: AsyncSession, workspace_id: str) -> list[WorkspacePrice]:
    """Read the workspace's own prices."""
    workspace = await db.get(Workspace, workspace_id)
    return workspace_prices(workspace.settings if workspace is not None else None)


def validate_workspace_prices(
    prices: Sequence[WorkspacePrice], *, today: str | None = None
) -> list[WorkspacePrice]:
    """Check ids and size; stamp an ``as_of`` of today on a row without a valid ISO date.

    Raises:
        WorkspacePriceError: Too many rows, an unknown provider id, or a duplicate key.
    """
    if len(prices) > MAX_WORKSPACE_PRICES:
        raise WorkspacePriceError(f"at most {MAX_WORKSPACE_PRICES} prices")
    stamp = today or utcnow().date().isoformat()
    seen: set[tuple[str, str | None, str]] = set()
    out: list[WorkspacePrice] = []
    for price in prices:
        if not known_price_id(price.provider_id):
            raise WorkspacePriceError(f"unknown provider id {price.provider_id!r}")
        key = (price.provider_id, price.model, price.unit)
        if key in seen:
            raise WorkspacePriceError(
                f"duplicate price for {price.provider_id} {price.model or ''} {price.unit}"
            )
        seen.add(key)
        try:
            dt.date.fromisoformat(price.as_of)
            as_of = price.as_of
        except ValueError:
            as_of = stamp
        out.append(price.model_copy(update={"as_of": as_of}))
    return out


async def store_workspace_prices(
    db: AsyncSession,
    workspace_id: str,
    prices: Sequence[WorkspacePrice],
    *,
    actor_type: ActorType,
    actor_id: str,
) -> list[WorkspacePrice]:
    """Replace the workspace's price list and audit ``workspace.prices_updated``.

    Rows whose value did not change keep their ``as_of``; an edited or new row
    is dated today.

    Raises:
        WorkspacePriceError: See :func:`validate_workspace_prices`.
    """
    workspace = await db.get(Workspace, workspace_id)
    if workspace is None:  # pragma: no cover - the auth dependency already resolved it
        raise WorkspacePriceError("unknown workspace")
    previous = {(p.provider_id, p.model, p.unit): p for p in workspace_prices(workspace.settings)}
    today = utcnow().date().isoformat()
    cleaned: list[WorkspacePrice] = []
    for price in validate_workspace_prices(prices, today=today):
        before = previous.get((price.provider_id, price.model, price.unit))
        unchanged = (
            before is not None and before.usd_per_unit == price.usd_per_unit and before.note == price.note
        )
        cleaned.append(price.model_copy(update={"as_of": before.as_of if unchanged and before else today}))
    settings = dict(workspace.settings or {})
    cost = dict(settings.get(SETTINGS_KEY) or {})
    cost["prices"] = [p.model_dump(mode="json") for p in cleaned]
    settings[SETTINGS_KEY] = cost
    workspace.settings = settings
    record(
        db,
        workspace_id=workspace_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action="workspace.prices_updated",
        target_type="workspace",
        target_id=workspace_id,
        payload={"count": len(cleaned), "provider_ids": sorted({p.provider_id for p in cleaned})},
    )
    await db.flush()
    return cleaned


# ------------------------------------------------------------------ the live sheet
@dataclass(frozen=True, slots=True)
class LiveSheet:
    """One OpenRouter model's cached catalog ``meta`` and when it was fetched."""

    meta: Mapping[str, Any]
    fetched_at: dt.datetime | None
    ttl_s: int


async def load_live_sheets(
    db: AsyncSession, refs: Iterable[ProviderRef | tuple[str, str | None]]
) -> dict[tuple[str, str], LiveSheet]:
    """The cached OpenRouter pricing for every OpenRouter ref given (a public or a keyed row).

    Args:
        db: Any session (the catalog cache is not workspace-scoped).
        refs: ``ProviderRef``s or ``(provider_id, model)`` pairs; non-OpenRouter ones are ignored.

    Returns:
        ``(provider_id, model) -> LiveSheet`` for the ids found in the cache.
    """
    wanted: dict[str, set[str]] = {}
    credentials: set[str] = set()
    for ref in refs:
        if isinstance(ref, ProviderRef):
            provider_id, model = ref.provider_id, ref.model
            if ref.credential_id:
                credentials.add(ref.credential_id)
            if model is None:
                try:
                    model = providers.get(provider_id).default_model
                except KeyError:
                    model = None
        else:
            provider_id, model = ref
        if model and _is_openrouter(provider_id):
            wanted.setdefault(provider_id, set()).add(model)
    if not wanted:
        return {}
    condition: ColumnElement[bool] = ProviderCatalogCache.credential_id.is_(None)
    if credentials:
        condition = or_(ProviderCatalogCache.credential_id.in_(credentials), condition)
    rows = (
        await db.execute(
            select(
                ProviderCatalogCache.provider_id,
                ProviderCatalogCache.items,
                ProviderCatalogCache.fetched_at,
                ProviderCatalogCache.ttl_s,
            )
            .where(
                ProviderCatalogCache.provider_id.in_(sorted(wanted)),
                ProviderCatalogCache.kind == "models",
                condition,
            )
            .order_by(ProviderCatalogCache.fetched_at.desc())
        )
    ).all()
    out: dict[tuple[str, str], LiveSheet] = {}
    for provider_id, items, fetched_at, ttl_s in rows:
        for raw in items if isinstance(items, list) else []:
            if not isinstance(raw, dict) or raw.get("id") not in wanted[provider_id]:
                continue
            meta = raw.get("meta")
            key = (provider_id, str(raw["id"]))
            if isinstance(meta, dict) and isinstance(meta.get("pricing"), dict) and key not in out:
                out[key] = LiveSheet(meta=meta, fetched_at=fetched_at, ttl_s=int(ttl_s))
    return out


@dataclass(slots=True)
class PriceBook:
    """A ``quote`` callable bound to one workspace's prices and the cached live sheets."""

    workspace_prices: list[WorkspacePrice] = field(default_factory=list)
    live: dict[tuple[str, str], LiveSheet] = field(default_factory=dict)
    now: dt.datetime | None = None

    def __call__(self, provider_id: str, model: str | None, unit: Unit) -> PriceQuote | None:
        """Resolve one price: workspace → live → table → ``None``."""
        sheet = self.live.get((provider_id, model)) if model else None
        return pricing.quote(
            provider_id,
            model,
            unit,
            workspace_prices=self.workspace_prices,
            catalog_meta=sheet.meta if sheet else None,
            catalog_fetched_at=sheet.fetched_at if sheet else None,
            catalog_ttl_s=sheet.ttl_s if sheet else providers.TTL_OPENROUTER_S,
            now=self.now,
        )


async def load_price_book(
    db: AsyncSession, workspace_id: str | None, refs: Iterable[ProviderRef | tuple[str, str | None]] = ()
) -> PriceBook:
    """Build the workspace's :class:`PriceBook` (``workspace_id=None`` gives table + live only)."""
    prices = await load_workspace_prices(db, workspace_id) if workspace_id else []
    return PriceBook(workspace_prices=prices, live=await load_live_sheets(db, refs))


def pipeline_refs(config_pipeline: Any, extra: Iterable[ProviderRef | None] = ()) -> list[ProviderRef]:
    """Every ``ProviderRef`` a pipeline (plus extras such as the QA judge) names."""
    refs: list[ProviderRef] = []
    for name in (
        "stt",
        "llm",
        "tts",
        "realtime",
        "avatar",
        "image_gen",
        "workflow_llm",
        "vad",
        "turn_detection",
    ):
        ref = getattr(config_pipeline, name, None)
        if isinstance(ref, ProviderRef):
            refs.append(ref)
    refs.extend(ref for ref in extra if ref is not None)
    return refs
