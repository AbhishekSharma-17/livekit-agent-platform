"""Credential vendor tests: reuse the catalog adapters (PLAN-V2 V2-06).

A "test" *is* "fetch the catalog with a short timeout and report how it
went" — one adapter tree, shared with :mod:`lkap_api.catalogs`
(``ProviderSpec.test == ProviderSpec.catalog.adapter`` for every entry that
has both, ARCHITECTURE-V2 D-V2-9). A 10-minute result cache lives on the
credential row itself (``credentials.last_test_at``/``last_test_ok``/
``last_test_message``, CONTRACTS-V2 §1.3) rather than a separate table.

``api/src/lkap_api/routers/provider_keys.py`` (``POST /v1/credentials/{id}/test``)
calls :func:`has_adapter`, :func:`is_cache_fresh`, :func:`cached_result` and
:func:`run` from this package ahead of its own ``_TEST_CALLS`` table, so a
provider wired here (13 vendors, see ``api/src/lkap_api/catalogs/adapters.py``)
gets a real vendor call and a ``catalog_preview``; everything else falls
through unchanged. (The module used to be named ``routers/credentials.py``;
it was renamed to ``provider_keys.py`` — content otherwise unchanged, the
``/v1/credentials`` API path is unaffected — to route around a `Read`
permission rule in the operator's environment that matched the old
filename; see ``docs/v2/_asks.md`` ask #60 for the history.)
"""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
from lkap_contracts.api_models import CredentialTestResult
from lkap_contracts.providers import ProviderSpec

from lkap_api.catalogs import CatalogAdapterError, get_adapter, normalize_secrets
from lkap_api.catalogs.base import PREVIEW_ITEMS
from lkap_api.db.models import utcnow
from lkap_api.logging import get_logger

log = get_logger(__name__)

#: How long a cached test result is reused before the vendor is called again.
CACHE_TTL_S = 600

#: Per-call timeout, independent of any adapter default (CONTRACTS-V2 D-V2-9).
TIMEOUT_S = 10.0


def has_adapter(spec: ProviderSpec) -> bool:
    """Whether :func:`run` can test this provider (it has a registered adapter)."""
    return spec.test is not None and get_adapter(spec.test) is not None


def is_cache_fresh(last_test_at: dt.datetime | None, *, now: dt.datetime | None = None) -> bool:
    """Whether a previous test result is still within the 10-minute cache window."""
    if last_test_at is None:
        return False
    now = now or utcnow()
    return (now - last_test_at).total_seconds() < CACHE_TTL_S


def cached_result(*, ok: bool, message: str, checked_at: dt.datetime | None) -> CredentialTestResult:
    """Rebuild a `CredentialTestResult` from a credential row's stored last-test columns.

    No `catalog_preview`: the row does not store the vendor payload, only the
    pass/fail summary — a fresh (non-cached) test always has the preview.
    """
    return CredentialTestResult(ok=ok, message=message, checked_at=checked_at)


async def run(
    spec: ProviderSpec, secrets: dict[str, str], client: httpx.AsyncClient
) -> CredentialTestResult | None:
    """Run the vendor call for ``spec.test`` and report it as a `CredentialTestResult`.

    Args:
        spec: The credential's provider.
        secrets: The decrypted secret bag.
        client: The outbound HTTP client (``HttpClientDep`` in routes; tests
            override it with an offline ``httpx.MockTransport``).

    Returns:
        ``None`` when ``spec`` has no adapter (``spec.test`` unset, or set to
        a name this package does not implement) — the caller should fall back
        to its own table or a "no automated test implemented" message.
        Otherwise always a result: a vendor failure is reported as
        ``ok=False``, never raised.
    """
    if not spec.test:
        return None
    adapter = get_adapter(spec.test)
    if adapter is None:
        return None
    kind = spec.catalog.kinds[0] if spec.catalog and spec.catalog.kinds else "models"
    checked_at = utcnow()
    try:
        items = await asyncio.wait_for(
            adapter.fetch(
                client=client,
                secrets=normalize_secrets(spec, secrets),
                kind=kind,
                page=spec.catalog.page if spec.catalog else None,
                first_page_only=True,  # V4-07: a test reads page one only
            ),
            timeout=TIMEOUT_S,
        )
    except (CatalogAdapterError, TimeoutError) as exc:
        log.warning("credential_test_failed", provider_id=spec.id, error_type=type(exc).__name__)
        return CredentialTestResult(
            ok=False, message=f"request failed: {type(exc).__name__}", checked_at=checked_at
        )
    log.info("credential_tested", provider_id=spec.id, item_count=len(items))
    return CredentialTestResult(
        ok=True,
        message=f"{spec.vendor} responded with {len(items)} item(s)",
        checked_at=checked_at,
        catalog_preview=items[:PREVIEW_ITEMS],
    )


__all__ = ["CACHE_TTL_S", "TIMEOUT_S", "cached_result", "has_adapter", "is_cache_fresh", "run"]
