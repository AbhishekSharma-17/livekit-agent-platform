"""`GET /v1/providers/{id}/catalog` orchestration: cache, vendor fetch, fallback.

Fallback chain on a vendor failure (CONTRACTS-V2: "a failed vendor call must
never break the providers page" — the endpoint always answers `200`):

1. A fresh cache row (age < `CatalogSpec.ttl_s`) — no vendor call at all.
2. The live vendor call, cached on success.
3. A *stale* cache row, if one exists (better than nothing).
4. The registry's own static suggestion list (`ProviderSpec.models`), for
   kinds that have one (`models`), so a `models`-kind provider without a
   catalog adapter (or whose adapter just failed) still offers *something*.
5. An empty list.

`CatalogResponse.error` is set only when something actually went wrong with a
*live* attempt (an unsupported kind, or the vendor call itself failing) and
the response fell back to a stale cache row or the static list as a result.
Serving the static list because no credential is configured yet is not an
error — that is the steady state for a provider nobody has keyed up, so
`error` stays `None` there; WP-4 should not render it as a failure banner.
"""

from __future__ import annotations

import httpx
from lkap_contracts.api_models import CatalogItem, CatalogResponse
from lkap_contracts.providers import CatalogKind, ProviderSpec
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.catalogs import cache
from lkap_api.catalogs.adapters import get_adapter
from lkap_api.catalogs.base import CatalogAdapterError, UnsupportedCatalogKind, normalize_secrets
from lkap_api.db.models import utcnow
from lkap_api.logging import get_logger

log = get_logger(__name__)

#: Fallback TTL for adapters/specs that don't name one (defensive; every wired
#: `CatalogSpec` in the registry sets its own).
DEFAULT_TTL_S = 3600


def static_items(spec: ProviderSpec, kind: CatalogKind) -> list[CatalogItem]:
    """The registry's own suggestion list, used as the last-resort fallback."""
    if kind != "models" or not spec.models:
        return []
    return [CatalogItem(id=m.id, label=m.label) for m in spec.models]


async def get_catalog(
    db: AsyncSession,
    client: httpx.AsyncClient,
    *,
    spec: ProviderSpec,
    kind: CatalogKind,
    credential_id: str | None,
    secrets: dict[str, str] | None,
    refresh: bool,
) -> CatalogResponse:
    """Build the `CatalogResponse` for one provider/kind, honouring the cache.

    Args:
        db: Open session (cache reads/writes).
        client: Outbound HTTP client (`HttpClientDep`).
        spec: The provider; ``spec.catalog`` names the adapter and its kinds.
        kind: Which list the caller wants.
        credential_id: The credential the cache entry is keyed to; ``None``
            reads/writes the credential-less slot (adapters that need no
            secret, or a caller with no credential yet — the vendor call
            itself will simply 401 in that case and fall through to static).
        secrets: The credential's decrypted secret bag, or ``None``/``{}``
            when there is nothing to authenticate with (no live call is made;
            straight to cache/static).
        refresh: Bypass a fresh cache row and force a live vendor call.

    Returns:
        Always ``200``-shaped: never raises for a vendor-side failure.
    """
    ttl_s = spec.catalog.ttl_s if spec.catalog else DEFAULT_TTL_S
    cached = await cache.get(db, provider_id=spec.id, credential_id=credential_id, kind=kind)
    if cached is not None and cached.fresh and not refresh:
        return CatalogResponse(kind=kind, items=cached.items, fetched_at=cached.fetched_at, source="vendor")

    adapter = get_adapter(spec.test) if spec.test else None
    if adapter is None or not secrets:
        # No adapter, or no credential configured yet — the expected steady
        # state for an un-keyed provider, not a failure: no `error`.
        return _fallback(spec, kind, cached, error=None)

    try:
        items = await adapter.fetch(client=client, secrets=normalize_secrets(spec, secrets), kind=kind)
    except UnsupportedCatalogKind as exc:
        return _fallback(spec, kind, cached, error=str(exc))
    except CatalogAdapterError as exc:
        log.warning("catalog_fetch_failed", provider_id=spec.id, kind=kind, error_type=type(exc).__name__)
        return _fallback(spec, kind, cached, error=f"{spec.vendor} catalog request failed")

    fetched_at = utcnow()
    await cache.set_(
        db,
        provider_id=spec.id,
        credential_id=credential_id,
        kind=kind,
        items=items,
        ttl_s=ttl_s,
        fetched_at=fetched_at,
    )
    return CatalogResponse(kind=kind, items=items, fetched_at=fetched_at, source="vendor")


def _fallback(
    spec: ProviderSpec, kind: CatalogKind, cached: cache.CachedCatalog | None, *, error: str | None
) -> CatalogResponse:
    """Stale cache, else the static suggestion list, else empty — always `error` set."""
    if cached is not None:
        return CatalogResponse(
            kind=kind, items=cached.items, fetched_at=cached.fetched_at, source="vendor", error=error
        )
    items = static_items(spec, kind)
    return CatalogResponse(kind=kind, items=items, fetched_at=None, source="static", error=error)
