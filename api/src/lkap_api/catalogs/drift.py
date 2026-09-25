"""Registry catalog drift report (docs/v4/CUSTOM-MODELS.md D-V4-27, R-V4-27).

``python -m lkap_api.catalogs.drift [--only ids|keyless] [--fixtures DIR] [--out DIR]``

For every ``available`` registry entry whose catalog lists ``models``, fetch
page one of the vendor list through the one adapter tree
(:mod:`lkap_api.catalogs.adapters`), apply the entry's
:class:`~lkap_contracts.providers.CatalogFilter`, and compare it with the
registry's own ids (``models[].id`` and ``default_model``). The report has the
four sections of D-V4-27:

* ``registry_not_upstream`` — a registry id the vendor's filtered list no
  longer carries (the serious one: a default may be gone);
* ``upstream_new`` — vendor ids the registry does not list, newest first when
  the vendor dates them, at most :data:`UPSTREAM_NEW_CAP` per entry;
* ``deprecation_notices`` — explicit vendor signals on a registry id (OpenAI
  ``shutdown_date``, OpenRouter ``expiration_date``, Mistral
  ``archived``/``deprecation``, Bedrock ``modelLifecycle.status``);
* ``skipped`` — no secret in the environment, a vendor error, a catalog with
  no model list, or not selected. Never a failure.

Keyless lists always run: Deepgram and Rime (``public`` adapters) and
OpenRouter, whose ``/models`` is public. For OpenRouter the job reads the
registration's filtered ``/models`` URL directly, never through the adapter's
``/key`` probe (that probe is a credential check, R-V4-9, not part of the
list) and never with a key: the raw items keep ``created`` and
``expiration_date``, which the adapter's trimmed ``meta`` drops. Keyed lists
read their secret from the environment variable :data:`SECRET_ENV_BY_HOME`
names for the entry's credential home; a missing variable skips the entry.

Output: ``catalog-drift.md`` (the body of the one ``Catalog drift`` issue the
workflow keeps) and ``catalog-drift.json``. The process exits 0 whatever
happens, so the weekly job never fails a build. Secret values never reach a
log line, the report or an exception message (vendor errors are reported by
status code and exception type only).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import httpx
from lkap_contracts import pricing
from lkap_contracts.api_models import CatalogItem
from lkap_contracts.providers import ProviderSpec, available_providers, credential_home
from pydantic import BaseModel, Field

from lkap_api.catalogs.adapters import get_adapter
from lkap_api.catalogs.base import (
    CatalogAdapter,
    CatalogAdapterError,
    VendorStatusError,
    get_json,
    normalize_secrets,
    parse_items,
)
from lkap_api.catalogs.openrouter import OpenRouterCatalogAdapter
from lkap_api.logging import configure_logging, get_logger

log = get_logger(__name__)

#: The issue the workflow creates once and then edits in place.
ISSUE_TITLE = "Catalog drift"

#: ``upstream_new`` ids reported per entry (the rest are counted, not listed).
UPSTREAM_NEW_CAP = 25

#: Deprecation notices reported per entry.
NOTICE_CAP = 25

#: Wall-clock budget for one adapter's page one (the adapter's own 10 s per request applies too).
FETCH_BUDGET_S = 30.0

#: How many vendor lists are fetched at once.
CONCURRENCY = 4

#: GitHub caps an issue body at 65 536 characters; stay well under it.
MARKDOWN_BUDGET = 60_000

#: The environment variable holding each credential home's key (the registry's
#: ``env_fallback`` for the home's primary secret field: ``OPENAI_API_KEY``,
#: ``GOOGLE_API_KEY``, ``ANTHROPIC_API_KEY`` …). The workflow maps repository
#: secrets onto these names. OpenRouter's home is listed but never read: its
#: list is fetched keyless.
SECRET_ENV_BY_HOME: dict[str, str] = {
    spec.id: spec.secret_fields[0].env_fallback
    for spec in available_providers()
    if spec.credential_provider is None and spec.secret_fields and spec.secret_fields[0].env_fallback
}

#: Registry short names a vendor lists under another id (asks #42). Deepgram's
#: public ``/v1/models`` names the STT models by ``canonical_name``
#: (``nova-3-general``), while the listen API, the LiveKit plugin and the
#: registry use the short model name (``nova-3``). The drift comparison reads
#: either as "listed".
REGISTRY_ALIASES: dict[str, dict[str, str]] = {
    "deepgram-stt": {"nova-3": "nova-3-general", "nova-2": "nova-2-general"},
}

#: Registry ids a vendor's list is known not to carry, with the reason. They are
#: still reported under ``registry_not_upstream`` (so a change is visible), but
#: marked as expected. Deepgram's ``/v1/models`` has no Flux entry (live fetch,
#: 2026-09-25); Flux is a ``/v2/listen`` model.
KNOWN_UNLISTED: dict[str, dict[str, str]] = {
    "deepgram-stt": {"flux-general-en": "Deepgram's /v1/models does not list Flux (a /v2/listen model)"},
}

_PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


# ------------------------------------------------------------------------------ report
class RegistryMissing(BaseModel):
    """A registry id the vendor's filtered list does not carry."""

    provider_id: str
    model: str
    is_default: bool
    expected: str | None = None
    """Set when the vendor is known not to list this id (:data:`KNOWN_UNLISTED`)."""


class UpstreamNew(BaseModel):
    """Vendor ids one entry's registry list does not name (capped)."""

    provider_id: str
    total: int
    ids: list[str]


class DeprecationNotice(BaseModel):
    """An explicit vendor retirement signal on a registry id."""

    provider_id: str
    model: str
    signal: str
    value: str


class Skipped(BaseModel):
    """An entry the run did not compare, and why (never a failure)."""

    provider_id: str
    reason: str


class Checked(BaseModel):
    """An entry the run compared."""

    provider_id: str
    adapter: str
    keyless: bool
    upstream_count: int


class PriceDrift(BaseModel):
    """A table price that differs from its live twin by more than :data:`PRICE_TOLERANCE_PCT` (D-V4-40)."""

    provider_id: str
    model: str | None
    unit: str
    table_usd_per_unit: Decimal
    table_as_of: str
    live_usd_per_unit: Decimal
    live_source: Literal["openrouter", "livekit"]
    diff_pct: float


class PriceStale(BaseModel):
    """A table row older than ``pricing.PRICE_STALE_DAYS``."""

    provider_id: str
    model: str | None
    unit: str
    as_of: str
    age_days: int


class DriftReport(BaseModel):
    """The four D-V4-27 sections, the two price sections (D-V4-40), and the entries compared."""

    generated_at: dt.datetime
    checked: list[Checked] = Field(default_factory=list)
    registry_not_upstream: list[RegistryMissing] = Field(default_factory=list)
    upstream_new: list[UpstreamNew] = Field(default_factory=list)
    deprecation_notices: list[DeprecationNotice] = Field(default_factory=list)
    skipped: list[Skipped] = Field(default_factory=list)
    price_drift: list[PriceDrift] = Field(default_factory=list)
    price_stale: list[PriceStale] = Field(default_factory=list)
    price_skipped: list[Skipped] = Field(default_factory=list)
    error: str | None = None


# ------------------------------------------------------------------------- selection
def model_entries(specs: Iterable[ProviderSpec] | None = None) -> list[ProviderSpec]:
    """Every ``available`` entry that has a catalog (the drift job's universe)."""
    pool = available_providers() if specs is None else list(specs)
    return [spec for spec in pool if spec.availability == "available" and spec.catalog is not None]


def is_keyless(adapter: CatalogAdapter) -> bool:
    """Whether the job can read this list with no secret (public adapters and OpenRouter)."""
    return adapter.public or isinstance(adapter, OpenRouterCatalogAdapter)


def parse_only(raw: str | None) -> set[str] | None:
    """The ``--only`` selection: ``None`` for everything, else ids and/or ``keyless``.

    The value is data (the workflow's free-text dispatch input, V2-21): only
    tokens shaped like a registry id are kept, the rest are dropped unread.
    """
    if raw is None or not raw.strip():
        return None
    tokens = {token.strip() for token in raw.split(",")}
    return {token for token in tokens if _PROVIDER_ID_RE.match(token)} or None


def registry_ids(spec: ProviderSpec) -> list[str]:
    """The ids the registry names for ``spec``: its suggestions, then the default."""
    ids = [model.id for model in spec.models]
    if spec.default_model and spec.default_model not in ids:
        ids.append(spec.default_model)
    return ids


# ---------------------------------------------------------------------------- compare
def _created(meta: Mapping[str, Any]) -> float | None:
    for key in ("created", "created_at"):
        value = meta.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
    return None


def _signals(meta: Mapping[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for key in ("shutdown_date", "expiration_date"):
        value = meta.get(key)
        if value:
            found.append((key, str(value)))
    if meta.get("archived") is True:
        found.append(("archived", "true"))
    deprecation = meta.get("deprecation")
    if isinstance(deprecation, str) and deprecation:
        found.append(("deprecation", deprecation))
    lifecycle = meta.get("modelLifecycle")
    status = lifecycle.get("status") if isinstance(lifecycle, dict) else None
    if isinstance(status, str) and status.upper() != "ACTIVE":
        found.append(("modelLifecycle.status", status))
    return found


def compare(
    spec: ProviderSpec, items: Sequence[CatalogItem]
) -> tuple[list[RegistryMissing], UpstreamNew | None, list[DeprecationNotice]]:
    """Compare one entry's filtered vendor list with its registry ids.

    Args:
        spec: The registry entry.
        items: The vendor's items, already filtered by the entry's ``CatalogFilter``.

    Returns:
        ``(registry_not_upstream, upstream_new or None, deprecation_notices)``.
    """
    aliases = REGISTRY_ALIASES.get(spec.id, {})
    by_id = {item.id: item for item in items}
    missing: list[RegistryMissing] = []
    notices: list[DeprecationNotice] = []
    known: set[str] = set()
    for model in registry_ids(spec):
        listed_as = next((name for name in (model, aliases.get(model)) if name and name in by_id), None)
        if listed_as is None:
            missing.append(
                RegistryMissing(
                    provider_id=spec.id,
                    model=model,
                    is_default=model == spec.default_model,
                    expected=KNOWN_UNLISTED.get(spec.id, {}).get(model),
                )
            )
            continue
        known.add(listed_as)
        notices.extend(
            DeprecationNotice(provider_id=spec.id, model=model, signal=signal, value=value)
            for signal, value in _signals(by_id[listed_as].meta)
        )
    fresh = [item for item in by_id.values() if item.id not in known]
    # Newest first where the vendor dates its items, then by id: a stable order week to week.
    fresh.sort(key=lambda item: (-(_created(item.meta) or 0.0), item.id))
    new = (
        UpstreamNew(provider_id=spec.id, total=len(fresh), ids=[item.id for item in fresh[:UPSTREAM_NEW_CAP]])
        if fresh
        else None
    )
    return missing, new, notices[:NOTICE_CAP]


# ------------------------------------------------------------------------------ fetch
@dataclass(frozen=True, slots=True)
class _Group:
    """One vendor list fetched once and shared by every entry it feeds (OpenAI's feeds seven)."""

    adapter_name: str
    adapter: CatalogAdapter
    secret: str | None
    spec: ProviderSpec  # the first entry, for `normalize_secrets`


async def _fetch_openrouter_public(
    client: httpx.AsyncClient, adapter: OpenRouterCatalogAdapter
) -> list[CatalogItem]:
    body = await get_json(client, adapter.models_url, vendor=adapter.vendor, headers={})
    return parse_items(body, label_keys=("name",))


async def _fetch_group(client: httpx.AsyncClient, group: _Group) -> list[CatalogItem]:
    adapter = group.adapter
    if isinstance(adapter, OpenRouterCatalogAdapter):
        return await _fetch_openrouter_public(client, adapter)
    secrets: dict[str, str] = {}
    if group.secret is not None and group.spec.secret_fields:
        secrets = normalize_secrets(group.spec, {group.spec.secret_fields[0].name: group.secret})
    return await adapter.fetch(client=client, secrets=secrets, kind="models", first_page_only=True)


def _failure_reason(vendor: str, exc: BaseException) -> str:
    if isinstance(exc, VendorStatusError):
        return f"{vendor} answered HTTP {exc.status_code}"
    if isinstance(exc, TimeoutError):
        return f"{vendor} did not answer within {FETCH_BUDGET_S:.0f} s"
    if isinstance(exc, CatalogAdapterError):
        return str(exc)  # the adapters' messages carry a status or an exception type, never a secret
    return f"{vendor} request failed ({type(exc).__name__})"


async def run(
    client: httpx.AsyncClient,
    *,
    env: Mapping[str, str],
    only: set[str] | None = None,
    specs: Iterable[ProviderSpec] | None = None,
    adapter_lookup: Callable[[str], CatalogAdapter | None] = get_adapter,
    now: dt.datetime | None = None,
    prices: bool = False,
) -> DriftReport:
    """Fetch, filter and compare every selected entry; never raises for a vendor error.

    Args:
        client: The outbound client (tests pass one over ``httpx.MockTransport``).
        env: Where secrets are read from (``os.environ`` in the CLI).
        only: Provider ids and/or ``"keyless"``; ``None`` selects every entry.
        specs: The registry entries to consider (default: every available one).
        adapter_lookup: Resolves ``CatalogSpec.adapter`` (default: the api's adapter tree).
        now: The report timestamp (default: the current UTC time).
        prices: Also fill the price sections (D-V4-40: OpenRouter twins, the LiveKit payload, stale rows).

    Returns:
        The :class:`DriftReport`.
    """
    report = DriftReport(generated_at=now or dt.datetime.now(dt.UTC))
    groups: dict[tuple[str, str | None], _Group] = {}
    members: dict[tuple[str, str | None], list[ProviderSpec]] = {}
    for spec in model_entries(specs):
        catalog = spec.catalog
        assert catalog is not None  # model_entries keeps only entries with a catalog
        adapter = adapter_lookup(catalog.adapter)
        keyless = adapter is not None and is_keyless(adapter)
        if only is not None and spec.id not in only and not ("keyless" in only and keyless):
            report.skipped.append(Skipped(provider_id=spec.id, reason="not selected by --only"))
            continue
        if "models" not in catalog.kinds:
            kinds = "/".join(catalog.kinds) or "nothing"
            report.skipped.append(
                Skipped(
                    provider_id=spec.id, reason=f"the catalog lists {kinds}, not models: nothing to compare"
                )
            )
            continue
        if adapter is None:
            report.skipped.append(Skipped(provider_id=spec.id, reason=f"no adapter '{catalog.adapter}'"))
            continue
        secret: str | None = None
        if not keyless:
            env_name = SECRET_ENV_BY_HOME.get(credential_home(spec))
            secret = (env.get(env_name) or None) if env_name else None
            if secret is None:
                reason = (
                    f"no secret: {env_name} is not set" if env_name else "no secret variable for this entry"
                )
                report.skipped.append(Skipped(provider_id=spec.id, reason=reason))
                continue
        key = (catalog.adapter, secret)
        groups.setdefault(key, _Group(catalog.adapter, adapter, secret, spec))
        members.setdefault(key, []).append(spec)

    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def fetch(group: _Group) -> list[CatalogItem] | BaseException:
        async with semaphore:
            try:
                return await asyncio.wait_for(_fetch_group(client, group), FETCH_BUDGET_S)
            except (CatalogAdapterError, httpx.HTTPError, TimeoutError, ValueError) as exc:
                return exc

    keys = list(groups)
    results = await asyncio.gather(*(fetch(groups[key]) for key in keys))
    for key, result in zip(keys, results, strict=True):
        group = groups[key]
        for spec in members[key]:
            if isinstance(result, BaseException):
                log.warning(
                    "catalog_drift_fetch_failed",
                    provider_id=spec.id,
                    adapter=group.adapter_name,
                    error_type=type(result).__name__,
                )
                report.skipped.append(
                    Skipped(provider_id=spec.id, reason=_failure_reason(spec.vendor, result))
                )
                continue
            catalog_filter = spec.catalog.filter if spec.catalog else None
            items = [i for i in result if catalog_filter is None or catalog_filter.matches(i.id, i.meta)]
            missing, new, notices = compare(spec, items)
            report.checked.append(
                Checked(
                    provider_id=spec.id,
                    adapter=group.adapter_name,
                    keyless=group.secret is None,
                    upstream_count=len(items),
                )
            )
            report.registry_not_upstream.extend(missing)
            if new is not None:
                report.upstream_new.append(new)
            report.deprecation_notices.extend(notices)
    order = {spec.id: index for index, spec in enumerate(model_entries(specs))}
    report.checked.sort(key=lambda row: order.get(row.provider_id, 0))
    report.skipped.sort(key=lambda row: order.get(row.provider_id, 0))
    if prices:
        await run_prices(client, report, now=report.generated_at)
    return report


# ----------------------------------------------------------------------------- prices
#: A table price further than this from its live twin is reported (D-V4-40).
PRICE_TOLERANCE_PCT = 2.0

#: OpenRouter's keyless list including speech models (the twins of the table rows).
OPENROUTER_PRICES_URL = f"{pricing.OPENROUTER_MODELS_URL}?output_modalities=all"

#: LiveKit's pricing page as a Next.js RSC payload: **unofficial** (livekit/agents#7420 asks for
#: an API), read by this report only, never as a price source; the trailing slash matters.
LIVEKIT_PRICING_URL = "https://www.livekit.com/pricing/inference/"

#: How the LiveKit page states a table unit: ``(scale, what)`` — ``usd_per_unit × scale`` is the page figure.
_LIVEKIT_SCALE: dict[str, Decimal] = {
    "audio_s_in": Decimal(60),  # $/minute of connection time
    "tokens_in": Decimal(1_000_000),
    "tokens_out": Decimal(1_000_000),
    "cached_tokens_in": Decimal(1_000_000),
    "chars": Decimal(1_000_000),
}

_MODEL_ID = re.compile(r'"model_id"\s*:\s*"(?P<id>[^"]+)"')
_BUILD_AMOUNT = re.compile(r'"build"\s*:\s*\{[^{}]*?"amount"\s*:\s*"?(?P<amount>\d+(?:\.\d+)?)')


def _diff_pct(table: Decimal, live: Decimal) -> float:
    if table == 0:
        return 0.0 if live == 0 else 100.0
    return float(abs(live - table) / table * 100)


def price_stale(now: dt.datetime, rows: Iterable[pricing.Price] | None = None) -> list[PriceStale]:
    """Every table row older than ``PRICE_STALE_DAYS``."""
    out: list[PriceStale] = []
    for row in rows if rows is not None else [*pricing.PRICES, *pricing.INFRA_PRICES]:
        try:
            age = (now.date() - dt.date.fromisoformat(row.as_of)).days
        except ValueError:
            age = pricing.PRICE_STALE_DAYS + 1
        if age > pricing.PRICE_STALE_DAYS:
            out.append(
                PriceStale(
                    provider_id=row.provider_id, model=row.model, unit=row.unit, as_of=row.as_of, age_days=age
                )
            )
    return out


def openrouter_price_drift(body: Any, rows: Iterable[pricing.Price] | None = None) -> list[PriceDrift]:
    """Compare every table row that has an OpenRouter twin with OpenRouter's listed price."""
    sheet: dict[str, Mapping[str, Any]] = {}
    data = body.get("data") if isinstance(body, Mapping) else None
    for raw in data if isinstance(data, list) else []:
        if (
            isinstance(raw, Mapping)
            and isinstance(raw.get("id"), str)
            and isinstance(raw.get("pricing"), Mapping)
        ):
            sheet[raw["id"]] = raw["pricing"]
    out: list[PriceDrift] = []
    for row in rows if rows is not None else pricing.PRICES:
        twin = pricing.OPENROUTER_TWINS.get((row.provider_id, row.model or ""))
        key = pricing.openrouter_key(row.provider_id, row.unit)
        listed = sheet.get(twin) if twin else None
        if listed is None or key is None:
            continue
        try:
            live = Decimal(str(listed.get(key)))
        except (ArithmeticError, ValueError):
            continue
        if not live.is_finite() or live < 0:
            continue
        diff = _diff_pct(row.usd_per_unit, live)
        if diff > PRICE_TOLERANCE_PCT:
            out.append(
                PriceDrift(
                    provider_id=row.provider_id,
                    model=row.model,
                    unit=row.unit,
                    table_usd_per_unit=row.usd_per_unit,
                    table_as_of=row.as_of,
                    live_usd_per_unit=live,
                    live_source="openrouter",
                    diff_pct=round(diff, 2),
                )
            )
    return out


def livekit_price_drift(payload: str, rows: Iterable[pricing.Price] | None = None) -> list[PriceDrift]:
    """Compare every ``livekit-inference-*`` row with LiveKit's RSC payload, leniently.

    The payload's shape is not documented, so each model's segment (from its
    ``"model_id"`` to the next one) is searched for every Build-tier ``amount``;
    a row is reported only when **none** of them is within the tolerance of the
    table's page figure (``usd_per_unit`` × 60 for $/min, × 1e6 for $/1M).
    A model the payload does not mention is not reported.

    Raises:
        ValueError: The payload carries no model records at all (reported as unavailable).
    """
    matches = list(_MODEL_ID.finditer(payload))
    if not matches:
        raise ValueError("no model records in the payload")
    segments: dict[str, list[Decimal]] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(payload)
        amounts = [Decimal(m["amount"]) for m in _BUILD_AMOUNT.finditer(payload, match.end(), end)]
        segments.setdefault(match["id"], []).extend(amounts)
    out: list[PriceDrift] = []
    for row in rows if rows is not None else pricing.PRICES:
        scale = _LIVEKIT_SCALE.get(row.unit)
        if not row.provider_id.startswith("livekit-inference-") or scale is None or row.model is None:
            continue
        found = segments.get(row.model) or []
        if not found:
            continue
        figure = row.usd_per_unit * scale
        closest = min(found, key=lambda amount: abs(amount - figure))
        diff = _diff_pct(figure, closest)
        if diff > PRICE_TOLERANCE_PCT:
            out.append(
                PriceDrift(
                    provider_id=row.provider_id,
                    model=row.model,
                    unit=row.unit,
                    table_usd_per_unit=row.usd_per_unit,
                    table_as_of=row.as_of,
                    live_usd_per_unit=closest / scale,
                    live_source="livekit",
                    diff_pct=round(diff, 2),
                )
            )
    return out


async def run_prices(client: httpx.AsyncClient, report: DriftReport, *, now: dt.datetime) -> None:
    """Fill the report's ``price_drift``/``price_stale``/``price_skipped``; never raises for a fetch."""
    report.price_stale = price_stale(now)
    try:
        body = await asyncio.wait_for(
            get_json(client, OPENROUTER_PRICES_URL, vendor="OpenRouter", headers={}), FETCH_BUDGET_S
        )
        report.price_drift.extend(openrouter_price_drift(body))
    except (CatalogAdapterError, httpx.HTTPError, TimeoutError, ValueError) as exc:
        report.price_skipped.append(
            Skipped(
                provider_id="openrouter",
                reason=f"OpenRouter prices unavailable: {_failure_reason('OpenRouter', exc)}",
            )
        )
    try:
        response = await asyncio.wait_for(
            client.get(LIVEKIT_PRICING_URL, headers={"RSC": "1"}), FETCH_BUDGET_S
        )
        response.raise_for_status()
        report.price_drift.extend(livekit_price_drift(response.text))
    except (httpx.HTTPError, TimeoutError, ValueError) as exc:
        log.info("price_drift_livekit_unavailable", error_type=type(exc).__name__)
        report.price_skipped.append(
            Skipped(
                provider_id="livekit-inference", reason=f"LiveKit payload unavailable ({type(exc).__name__})"
            )
        )


# ----------------------------------------------------------------------------- render
def _code(value: str) -> str:
    """A vendor-supplied id as inline code: data, never markup or a mention."""
    return "`" + value.replace("`", "'").replace("\n", " ")[:200] + "`"


def render_markdown(report: DriftReport) -> str:
    """The issue body: a summary, then the four sections, trimmed to :data:`MARKDOWN_BUDGET`."""
    stamp = report.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# {ISSUE_TITLE}",
        "",
        f"Generated {stamp} by `.github/workflows/catalog-drift.yml` "
        "(`python -m lkap_api.catalogs.drift`). Checked "
        f"{len(report.checked)} entries, skipped {len(report.skipped)}. "
        'Promotion rule: `CONTRIBUTING.md`, "Adding a model to the registry".',
    ]
    if report.error:
        lines += ["", f"**The run stopped early:** {report.error}"]

    lines += ["", "## `registry_not_upstream`: registry ids the vendor no longer lists", ""]
    if report.registry_not_upstream:
        lines += [
            f"- `{row.provider_id}`: {_code(row.model)}"
            + (" (the entry's **default**)" if row.is_default else "")
            + (f"; expected: {row.expected}" if row.expected else "")
            for row in report.registry_not_upstream
        ]
    else:
        lines.append("None.")

    lines += [
        "",
        f"## `upstream_new`: vendor ids the registry does not list (up to {UPSTREAM_NEW_CAP} each)",
        "",
    ]
    if report.upstream_new:
        for row in report.upstream_new:
            more = f", and {row.total - len(row.ids)} more" if row.total > len(row.ids) else ""
            lines.append(
                f"- `{row.provider_id}` ({row.total}): " + ", ".join(_code(i) for i in row.ids) + more
            )
    else:
        lines.append("None.")

    lines += ["", "## `deprecation_notices`: vendor retirement signals on registry ids", ""]
    if report.deprecation_notices:
        lines += [
            f"- `{row.provider_id}`: {_code(row.model)} {row.signal} = {_code(row.value)}"
            for row in report.deprecation_notices
        ]
    else:
        lines.append("None.")

    lines += [
        "",
        f"## `price_drift`: table prices more than {PRICE_TOLERANCE_PCT:g} % from their live twin",
        "",
        "A prompt to re-read the vendor's page, never an automatic edit (LiveKit's payload is unofficial).",
        "",
    ]
    if report.price_drift:
        lines += [
            f"- `{row.provider_id}` {_code(row.model or '(any model)')} `{row.unit}`: table "
            f"{row.table_usd_per_unit:.10f} (as_of {row.table_as_of}) vs {row.live_source} "
            f"{row.live_usd_per_unit:.10f} ({row.diff_pct:+.1f} %)"
            for row in report.price_drift
        ]
    else:
        lines.append("None.")
    lines += ["", f"## `price_stale`: rows older than {pricing.PRICE_STALE_DAYS} days", ""]
    if report.price_stale:
        lines += [
            f"- `{row.provider_id}` {_code(row.model or '(any model)')} `{row.unit}`: as_of {row.as_of} "
            f"({row.age_days} days)"
            for row in report.price_stale
        ]
    else:
        lines.append("None.")
    if report.price_skipped:
        lines += [
            "",
            *[f"- price check skipped, `{row.provider_id}`: {row.reason}" for row in report.price_skipped],
        ]

    lines += ["", "## `skipped`", ""]
    if report.skipped:
        lines += [f"- `{row.provider_id}`: {row.reason}" for row in report.skipped]
    else:
        lines.append("None.")

    if report.checked:
        lines += ["", "<details><summary>Checked entries</summary>", ""]
        lines += [
            f"- `{row.provider_id}` via `{row.adapter}`"
            + (" (keyless)" if row.keyless else "")
            + f": {row.upstream_count} ids after the entry's filter"
            for row in report.checked
        ]
        lines += ["", "</details>"]

    body = "\n".join(lines) + "\n"
    if len(body) > MARKDOWN_BUDGET:
        body = (
            body[:MARKDOWN_BUDGET] + "\n\n…trimmed; the full report is the `catalog-drift.json` artifact.\n"
        )
    return body


def write_outputs(report: DriftReport, out_dir: Path) -> tuple[Path, Path]:
    """Write ``catalog-drift.md`` and ``catalog-drift.json`` under ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "catalog-drift.md"
    json_path = out_dir / "catalog-drift.json"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return md_path, json_path


# --------------------------------------------------------------------------- fixtures
def fixture_names(url: httpx.URL) -> list[str]:
    """The file names ``--fixtures`` tries for a request, most specific first.

    ``https://openrouter.ai/api/v1/models?supported_parameters=tools`` →
    ``openrouter.ai_api_v1_models_supported_parameters_tools.json``, then
    ``openrouter.ai_api_v1_models.json``.
    """
    base = re.sub(r"[^A-Za-z0-9.-]+", "_", f"{url.host}{url.path}").strip("_")
    names = []
    if url.query:
        query = re.sub(r"[^A-Za-z0-9.-]+", "_", url.query.decode()).strip("_")
        names.append(f"{base}_{query}.json")
    names.append(f"{base}.json")
    return names


def fixture_transport(directory: Path) -> httpx.MockTransport:
    """An offline transport answering each GET from a JSON file in ``directory`` (404 when absent)."""

    def handler(request: httpx.Request) -> httpx.Response:
        for name in fixture_names(request.url):
            path = directory / name
            if path.is_file():
                return httpx.Response(
                    200, content=path.read_bytes(), headers={"content-type": "application/json"}
                )
        return httpx.Response(404, json={"error": "no fixture"})

    return httpx.MockTransport(handler)


# -------------------------------------------------------------------------------- CLI
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lkap_api.catalogs.drift",
        description="Compare the provider registry with the vendors' live model lists (always exits 0).",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="Comma-separated provider ids and/or 'keyless' (OpenRouter, Deepgram, Rime); default: all.",
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=None,
        help="Answer every vendor call from JSON files in this directory.",
    )
    parser.add_argument("--out", type=Path, default=Path("."), help="Where to write catalog-drift.{md,json}.")
    return parser


async def _amain(args: argparse.Namespace, env: Mapping[str, str]) -> DriftReport:
    transport = fixture_transport(args.fixtures) if args.fixtures is not None else None
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        only = parse_only(args.only)
        return await run(client, env=env, only=only, prices=only is None or "prices" in only)


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """Run the drift report and write its two files; always returns 0 (D-V4-27)."""
    args = _parser().parse_args(argv)
    try:
        report = asyncio.run(_amain(args, os.environ if env is None else env))
    except Exception as exc:  # the job never fails the build: report the crash instead
        log.error("catalog_drift_crashed", error_type=type(exc).__name__)
        report = DriftReport(
            generated_at=dt.datetime.now(dt.UTC), error=f"internal error ({type(exc).__name__})"
        )
    try:
        md_path, json_path = write_outputs(report, args.out)
    except OSError as exc:
        log.error("catalog_drift_write_failed", error_type=type(exc).__name__)
        return 0
    log.info(
        "catalog_drift_done",
        checked=len(report.checked),
        registry_not_upstream=len(report.registry_not_upstream),
        upstream_new=sum(row.total for row in report.upstream_new),
        deprecation_notices=len(report.deprecation_notices),
        skipped=len(report.skipped),
        markdown=str(md_path),
        json=str(json_path),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    configure_logging()
    sys.exit(main())


__all__ = [
    "ISSUE_TITLE",
    "KNOWN_UNLISTED",
    "REGISTRY_ALIASES",
    "SECRET_ENV_BY_HOME",
    "UPSTREAM_NEW_CAP",
    "DriftReport",
    "compare",
    "fixture_names",
    "fixture_transport",
    "main",
    "parse_only",
    "registry_ids",
    "render_markdown",
    "run",
    "write_outputs",
]
