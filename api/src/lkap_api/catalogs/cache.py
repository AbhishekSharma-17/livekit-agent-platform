"""The `provider_catalog_cache` table: a 1 h vendor-catalog cache (CONTRACTS-V2 §1.3).

`ProviderCatalogCache` is the fallback CONTRACTS-V2 names for when Redis is
absent; since no `LKAP_REDIS_URL` is configured on the dev host this package
targets (per the task brief), this module implements only that DB-backed
path — it is a complete, spec-compliant cache on its own (`GET .../catalog`'s
acceptance criteria only require *a* cache with a TTL and a `refresh=true`
bypass, not specifically Redis). A Redis-backed `CatalogCache` implementing
the same two methods can be added later as a drop-in without touching
callers (`api/src/lkap_api/routers/providers.py` only calls `get`/`set`).
"""

from __future__ import annotations

import datetime as dt

from lkap_contracts.api_models import CatalogItem
from lkap_contracts.providers import CatalogKind
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import ProviderCatalogCache, new_id, utcnow


class CachedCatalog:
    """A cache hit: the items and when they were fetched."""

    __slots__ = ("items", "fetched_at", "fresh")

    def __init__(self, items: list[CatalogItem], fetched_at: dt.datetime, *, fresh: bool) -> None:
        self.items = items
        self.fetched_at = fetched_at
        self.fresh = fresh
        """Whether ``fetched_at + ttl_s`` has not yet elapsed."""


async def get(
    db: AsyncSession, *, provider_id: str, credential_id: str | None, kind: CatalogKind
) -> CachedCatalog | None:
    """Return the cached page for ``(provider_id, credential_id, kind)``, if any.

    Returns a row even when it is stale (``fresh=False``) so a failed vendor
    call can still fall back to "old but present" data instead of an empty
    list (CONTRACTS-V2: a failed vendor call must never break the page).
    """
    row = await db.scalar(
        select(ProviderCatalogCache).where(
            ProviderCatalogCache.provider_id == provider_id,
            ProviderCatalogCache.credential_id == credential_id,
            ProviderCatalogCache.kind == kind,
        )
    )
    if row is None:
        return None
    items = [CatalogItem.model_validate(item) for item in row.items]
    age_s = (utcnow() - row.fetched_at).total_seconds()
    return CachedCatalog(items, row.fetched_at, fresh=age_s < row.ttl_s)


async def set_(
    db: AsyncSession,
    *,
    provider_id: str,
    credential_id: str | None,
    kind: CatalogKind,
    items: list[CatalogItem],
    ttl_s: int,
    fetched_at: dt.datetime | None = None,
) -> None:
    """Upsert the cached page for ``(provider_id, credential_id, kind)``.

    Args:
        fetched_at: The timestamp to store; defaults to "now". Callers that
            also return `fetched_at` in the same response (the catalog
            endpoint) should pass the exact value they returned, so the next
            cache hit echoes back the same timestamp rather than one computed
            microseconds later by this function's own clock read.
    """
    row = await db.scalar(
        select(ProviderCatalogCache).where(
            ProviderCatalogCache.provider_id == provider_id,
            ProviderCatalogCache.credential_id == credential_id,
            ProviderCatalogCache.kind == kind,
        )
    )
    dumped = [item.model_dump(mode="json") for item in items]
    stamp = fetched_at or utcnow()
    if row is None:
        db.add(
            ProviderCatalogCache(
                id=new_id(),
                provider_id=provider_id,
                credential_id=credential_id,
                kind=kind,
                items=dumped,
                fetched_at=stamp,
                ttl_s=ttl_s,
            )
        )
    else:
        row.items = dumped
        row.fetched_at = stamp
        row.ttl_s = ttl_s
    await db.flush()
