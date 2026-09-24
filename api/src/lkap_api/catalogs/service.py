"""`GET /v1/providers/{id}/catalog` orchestration: cache, vendor fetch, fallback, search.

Fallback chain on a vendor failure (CONTRACTS-V2: "a failed vendor call must
never break the providers page" — the endpoint always answers `200`):

1. A fresh cache row (age < `CatalogSpec.ttl_s`) — no vendor call at all.
2. The live vendor call, filtered by `CatalogSpec.filter`, cached on success.
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

V4-07 (docs/v4/CUSTOM-MODELS.md D-V4-25, R-V4-28):

* The adapter is resolved from **`spec.catalog.adapter`** (credential tests keep
  `spec.test`). A `public` adapter (Deepgram, Rime) is fetched with an empty
  secret bag and cached under `credential_id=None`, so a catalog-only entry
  with no credential still answers `source="vendor"`.
* `CatalogSpec.filter` is applied to a `models` list after the fetch, before
  caching; `CatalogSpec.page` is passed to the adapter.
* :func:`page_catalog` searches (`q`, `model`) and pages (`limit`, `offset`) the
  cached list and sets `total`; :func:`search_vendor` forwards `q` to the
  vendor for OpenRouter only, never caching the subset.
* Every successful `models` fetch records sightings on the workspace's
  `provider_models` rows (D-V4-27 (2)), compared only against the previous
  cached fetch of the same entry, credential and filter.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

import httpx
from lkap_contracts.api_models import CatalogItem, CatalogResponse
from lkap_contracts.providers import CatalogKind, ProviderSpec
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.catalogs import cache
from lkap_api.catalogs.adapters import get_adapter
from lkap_api.catalogs.base import (
    CatalogAdapter,
    CatalogAdapterError,
    UnsupportedCatalogKind,
    normalize_secrets,
)
from lkap_api.catalogs.openrouter import OpenRouterCatalogAdapter
from lkap_api.custom_models import records
from lkap_api.db.models import utcnow
from lkap_api.logging import get_logger

log = get_logger(__name__)

#: Fallback TTL for adapters/specs that don't name one (defensive; every wired
#: `CatalogSpec` in the registry sets its own).
DEFAULT_TTL_S = 3600

#: `GET .../catalog?limit=` default and ceiling (D-V4-25).
DEFAULT_PAGE_LIMIT = 200
MAX_PAGE_LIMIT = 1000


def static_items(spec: ProviderSpec, kind: CatalogKind) -> list[CatalogItem]:
    """The registry's own suggestion list, used as the last-resort fallback."""
    if kind != "models" or not spec.models:
        return []
    return [CatalogItem(id=m.id, label=m.label) for m in spec.models]


def catalog_adapter(spec: ProviderSpec) -> CatalogAdapter | None:
    """The adapter that lists ``spec``'s catalog: ``spec.catalog.adapter``, not ``spec.test``."""
    if spec.catalog is None:
        return None
    return get_adapter(spec.catalog.adapter)


def apply_filter(spec: ProviderSpec, kind: CatalogKind, items: list[CatalogItem]) -> list[CatalogItem]:
    """Keep the items of a ``models`` list that pass the entry's `CatalogFilter`."""
    catalog_filter = spec.catalog.filter if spec.catalog else None
    if catalog_filter is None or kind != "models":
        return items
    return [item for item in items if catalog_filter.matches(item.id, item.meta)]


async def get_catalog(
    db: AsyncSession,
    client: httpx.AsyncClient,
    *,
    spec: ProviderSpec,
    kind: CatalogKind,
    credential_id: str | None,
    secrets: dict[str, str] | None,
    refresh: bool,
    workspace_id: str | None = None,
) -> CatalogResponse:
    """Build the full (unpaged) `CatalogResponse` for one provider/kind, honouring the cache.

    Args:
        db: Open session (cache reads/writes, sighting bookkeeping).
        client: Outbound HTTP client (`HttpClientDep`).
        spec: The provider; ``spec.catalog`` names the adapter, kinds, filter and paging.
        kind: Which list the caller wants.
        credential_id: The credential the cache entry is keyed to; ``None``
            reads/writes the credential-less slot. Ignored (forced to ``None``)
            for a public adapter.
        secrets: The credential's decrypted secret bag, or ``None``/``{}``
            when there is nothing to authenticate with (a keyed adapter then
            makes no live call: straight to cache/static).
        refresh: Bypass a fresh cache row and force a live vendor call.
        workspace_id: The caller's workspace; when set, a successful ``models``
            fetch records catalog sightings on its ``provider_models`` rows.

    Returns:
        Always ``200``-shaped: never raises for a vendor-side failure. ``total``
        is the number of items.
    """
    ttl_s = spec.catalog.ttl_s if spec.catalog else DEFAULT_TTL_S
    adapter = catalog_adapter(spec)
    public = adapter is not None and adapter.public
    if public:
        # One shared row for a keyless list, whichever credential the caller holds.
        credential_id, secrets = None, {}
    cached = await cache.get(db, provider_id=spec.id, credential_id=credential_id, kind=kind)
    if cached is not None and cached.fresh and not refresh:
        # Filtered on read too, so a row cached before the entry had a filter stays in its lane.
        return _response(kind, apply_filter(spec, kind, cached.items), cached.fetched_at, source="vendor")

    if adapter is None or (not public and not secrets):
        # No adapter, or no credential configured yet — the expected steady
        # state for an un-keyed provider, not a failure: no `error`.
        return _fallback(spec, kind, cached, error=None)

    try:
        items = await adapter.fetch(
            client=client,
            secrets=normalize_secrets(spec, secrets) if secrets else {},
            kind=kind,
            page=spec.catalog.page if spec.catalog else None,
        )
    except UnsupportedCatalogKind as exc:
        return _fallback(spec, kind, cached, error=str(exc))
    except CatalogAdapterError as exc:
        log.warning("catalog_fetch_failed", provider_id=spec.id, kind=kind, error_type=type(exc).__name__)
        return _fallback(spec, kind, cached, error=f"{spec.vendor} catalog request failed")

    items = apply_filter(spec, kind, items)
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
    if workspace_id is not None and kind == "models":
        await records.apply_catalog_sightings(
            db,
            workspace_id=workspace_id,
            spec=spec,
            previous_ids=None
            if cached is None
            else [item.id for item in apply_filter(spec, kind, cached.items)],
            items=items,
            now=fetched_at,
        )
    return _response(kind, items, fetched_at, source="vendor")


async def search_vendor(
    client: httpx.AsyncClient,
    *,
    spec: ProviderSpec,
    kind: CatalogKind,
    secrets: dict[str, str] | None,
    query: str,
) -> CatalogResponse | None:
    """Forward ``query`` to the vendor's own search: OpenRouter registrations only (R-V4-28).

    Returns ``None`` when the entry's adapter has no vendor search or there is
    no key (the caller then searches the cached list instead). The result is a
    subset of the vendor list and is **never cached**, so the next cached search
    stays whole. A vendor failure is reported in ``error``, never raised.
    """
    adapter = catalog_adapter(spec)
    if not isinstance(adapter, OpenRouterCatalogAdapter) or not secrets:
        return None
    try:
        items = await adapter.search(
            client=client, secrets=normalize_secrets(spec, secrets), kind=kind, query=query
        )
    except CatalogAdapterError as exc:
        log.warning("catalog_search_failed", provider_id=spec.id, kind=kind, error_type=type(exc).__name__)
        return CatalogResponse(
            kind=kind, items=[], source="vendor", error=f"{spec.vendor} search failed", total=0
        )
    return _response(kind, apply_filter(spec, kind, items), utcnow(), source="vendor")


def page_catalog(
    response: CatalogResponse,
    *,
    query: str | None = None,
    model: str | None = None,
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
) -> CatalogResponse:
    """Search and page a catalog response; ``total`` counts the matches before paging.

    Args:
        response: The full list (cached, live or static).
        query: Case-insensitive substring over each item's id and label.
        model: Keep only items whose ``meta.model`` equals it (a TTS model's voices).
        limit: Page size (1..1000).
        offset: How many matches to skip.

    Returns:
        A copy of ``response`` with the page of items and ``total`` set.
    """
    items = response.items
    if model:
        items = [item for item in items if item.meta.get("model") == model]
    if query:
        needle = query.casefold()
        items = [item for item in items if needle in item.id.casefold() or needle in item.label.casefold()]
    total = len(items)
    return response.model_copy(update={"items": items[offset : offset + limit], "total": total})


def _response(
    kind: CatalogKind,
    items: list[CatalogItem],
    fetched_at: dt.datetime | None,
    *,
    source: Literal["vendor", "static"],
    error: str | None = None,
) -> CatalogResponse:
    return CatalogResponse(
        kind=kind, items=items, fetched_at=fetched_at, source=source, error=error, total=len(items)
    )


def _fallback(
    spec: ProviderSpec, kind: CatalogKind, cached: cache.CachedCatalog | None, *, error: str | None
) -> CatalogResponse:
    """Stale cache, else the static suggestion list, else empty — ``error`` as given."""
    if cached is not None:
        return _response(
            kind, apply_filter(spec, kind, cached.items), cached.fetched_at, source="vendor", error=error
        )
    return _response(kind, static_items(spec, kind), None, source="static", error=error)
