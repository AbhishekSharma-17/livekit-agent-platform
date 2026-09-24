""" "Test model": one capped, real vendor call per request, recorded per workspace (D-V4-26, R-V4-25).

:func:`run_model_test` runs, in order:

1. the model-id rule on ``model`` and the id rule on the id-like fields a probe
   puts in a URL (422, never echoing a value);
2. the net guard on a ``base_url`` field (422 ``blocked_destination``);
3. the key: the workspace credential for the entry's credential home (like the
   catalog route), or, for LiveKit Inference, a gateway JWT signed with the
   named (or the default) connection's key and secret — the claims
   ``livekit-agents``' ``inference/_utils.py::create_access_token`` sets;
4. the 10-minute re-run suppression: a ``provider_models`` row tested with the
   same key fingerprint within :data:`CACHE_TTL_S` answers ``cached=true`` (its
   ``probes`` list is empty) unless ``force``;
5. the caps, by count: :data:`RATE_CAPACITY` tests per workspace per
   :data:`RATE_WINDOW_S` (``RateLimiter``, shared across replicas on Redis) and
   :data:`MAX_IN_FLIGHT` concurrent probes per workspace, **process-local** like
   ``limits.slot_lock`` (two api replicas allow twice that; the token bucket is
   the cross-replica cap);
6. the probe under ``asyncio.wait_for`` (15 s llm, 20 s stt/tts, 10 s
   embedding/realtime/avatar);
7. :func:`~lkap_api.custom_models.ids.scrub` on every message and the sample,
   the record upsert, one ``provider.test_model`` audit row (provider, model,
   ok, latency — nothing else) and one structured log line (no body, no key).

An entry without a ``probe`` answers ``ok=None`` with no request; an image
model answers ``ok=None`` from catalog membership alone (images cost money).

:func:`with_capabilities` fills ``ResolvedProvider.capabilities`` for the
worker (D-V4-24 delivery).
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from livekit import api as livekit_api
from lkap_contracts.agent_config import ProviderSlot, ResolvedProvider
from lkap_contracts.api_models import CatalogItem, ModelTestRequest, ModelTestResult, ProbeResult
from lkap_contracts.pricing import Unit, lookup
from lkap_contracts.providers import (
    MODEL_KINDS,
    ModelCapabilities,
    ProviderSpec,
    get,
    id_like_field,
    validate_id_value,
    validate_model_id,
)
from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import net_guard
from lkap_api.auth import audit
from lkap_api.auth.deps import WorkspaceContext
from lkap_api.auth.ratelimit import RateLimitedError, RateLimiter
from lkap_api.catalogs.base import normalize_secrets
from lkap_api.connections.clients import ConnectionClientFactory
from lkap_api.connections.service import connection_fingerprint, default_connection, get_connection
from lkap_api.custom_models import records
from lkap_api.custom_models.capabilities import resolve_capabilities
from lkap_api.custom_models.ids import scrub
from lkap_api.custom_models.probes import (
    PROBES,
    ProbeContext,
    ProbeInputError,
    ProbeOutcome,
    Usage,
    WsConnector,
)
from lkap_api.custom_models.probes.base import SAMPLE_MAX
from lkap_api.db.models import Credential, ProviderCatalogCache, utcnow
from lkap_api.errors import NotFoundError, UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.vault import Vault

log = get_logger(__name__)

#: Re-run suppression window (mirrors ``credential_tests.CACHE_TTL_S``).
CACHE_TTL_S = 600
#: Tests per workspace per window, and the window (R-V4-25).
RATE_CAPACITY = 10
RATE_WINDOW_S = 60.0
#: Concurrent probes per workspace in this process (R-V4-25).
MAX_IN_FLIGHT = 2
#: LiveKit Inference gateway token lifetime (the worker's own default).
INFERENCE_TOKEN_TTL_S = 600
#: The registry entries whose probe signs the gateway request with a connection's key.
INFERENCE_PREFIX = "livekit-inference-"
#: The slots whose resolved provider carries model capabilities for the worker.
CAPABILITY_SLOTS: tuple[ProviderSlot, ...] = ("llm", "workflow_llm", "realtime")
#: Kinds a "Test model" run accepts: the model kinds plus avatars (a session-less GET).
TESTABLE_KINDS = MODEL_KINDS | {"avatar"}

NO_PROBE_MESSAGE = "no test for this provider; the model is checked on the first session"
PLUGIN_CAVEAT = (
    "a passing test proves the vendor accepts this id with this key, not that the plugin builds it"
)


@dataclass(frozen=True)
class Timeouts:
    """Per-kind probe budgets in seconds (D-V4-26); tests shrink them."""

    llm: float = 15.0
    stt: float = 20.0
    tts: float = 20.0
    embedding: float = 10.0
    realtime: float = 10.0
    avatar: float = 10.0

    def for_kind(self, kind: str) -> float:
        """The budget of one probe run for ``kind``."""
        value = getattr(self, kind, None)
        return float(value) if isinstance(value, int | float) else self.avatar


class InFlight:
    """Process-local count of running probes per workspace (the 2-slot cap)."""

    def __init__(self, limit: int = MAX_IN_FLIGHT) -> None:
        self.limit = limit
        self._running: dict[str, int] = {}

    def running(self, workspace_id: str) -> int:
        """How many probes the workspace has running in this process."""
        return self._running.get(workspace_id, 0)

    @contextmanager
    def slot(self, workspace_id: str) -> Iterator[None]:
        """Hold one slot, or raise a 429 when the workspace already runs :attr:`limit` probes.

        Raises:
            RateLimitedError: When every slot is taken.
        """
        if self.running(workspace_id) >= self.limit:
            raise _rate_limited(f"{self.limit} model tests already running in this workspace", 1.0)
        self._running[workspace_id] = self.running(workspace_id) + 1
        try:
            yield
        finally:
            left = self.running(workspace_id) - 1
            if left > 0:
                self._running[workspace_id] = left
            else:
                self._running.pop(workspace_id, None)


def _rate_limited(what: str, retry_after_s: float) -> RateLimitedError:
    retry = round(max(retry_after_s, 0.1), 1)
    return RateLimitedError(
        f"rate limit exceeded: {what}",
        details={"limit": what, "retry_after_s": retry, "retry_after": retry},
    )


@dataclass
class _Key:
    """The key a probe authenticates with. **Contains secrets.**"""

    secrets: dict[str, str] = field(repr=False)
    scrub_values: list[str] = field(repr=False)
    fingerprint: str | None
    credential_id: str | None


def mint_inference_token(api_key: str, api_secret: str, ttl_s: int = INFERENCE_TOKEN_TTL_S) -> str:
    """A LiveKit Inference gateway JWT: identity ``agent``, ``inference.perform``, 10 min.

    The same claims as ``livekit-agents``' ``inference/_utils.py::create_access_token``,
    built with the api's own ``livekit-api`` (no ``livekit-agents`` import).
    """
    return (
        livekit_api.AccessToken(api_key, api_secret)
        .with_identity("agent")
        .with_inference_grants(livekit_api.access_token.InferenceGrants(perform=True))
        .with_ttl(dt.timedelta(seconds=ttl_s))
        .to_jwt()
    )


# ------------------------------------------------------------------------ checks
def _checked_request(spec: ProviderSpec, request: ModelTestRequest) -> None:
    if spec.kind not in TESTABLE_KINDS:
        raise UnprocessableEntityError(
            f"provider '{spec.id}' is a {spec.kind} provider and has no model to test"
        )
    reason = validate_model_id(request.model)
    if reason is not None:
        raise UnprocessableEntityError(f"the model id {reason}")
    by_name = {f.name: f for f in spec.fields}
    for name, value in request.fields.items():
        if not isinstance(value, str) or not value:
            continue
        typed = by_name.get(name)
        if not (id_like_field(name) or (typed is not None and typed.type in ("model", "catalog"))):
            continue
        issue = validate_id_value(value)
        if issue is not None and issue.severity == "error":
            raise UnprocessableEntityError(f"field '{name}': the value {issue.reason}")


def _checked_base_url(request: ModelTestRequest, policy: net_guard.NetPolicy) -> str | None:
    value = request.fields.get("base_url")
    if not isinstance(value, str) or not value.strip():
        return None
    net_guard.validate_url(value.strip(), policy, field_name="fields.base_url")
    return value.strip()


async def _credential(
    db: AsyncSession, *, workspace_id: str, spec: ProviderSpec, credential_id: str | None
) -> Credential | None:
    # The catalog route's resolution (explicit → default → the only one), home-aware.
    from lkap_api.routers.providers import _resolve_credential  # noqa: PLC0415 - avoids an import cycle

    return await _resolve_credential(db, workspace_id=workspace_id, spec=spec, credential_id=credential_id)


async def _inference_key(
    db: AsyncSession,
    vault: Vault,
    factory: ConnectionClientFactory,
    *,
    workspace_id: str,
    connection_id: str | None,
) -> _Key:
    if connection_id is not None:
        try:
            row = await get_connection(db, workspace_id, connection_id)
        except NotFoundError as exc:
            raise UnprocessableEntityError(
                f"'{connection_id}' is not a connection of this workspace"
            ) from exc
    else:
        found = await default_connection(db, workspace_id)
        if found is None:
            raise UnprocessableEntityError("no LiveKit connection to sign the Inference request with")
        row = found
    creds = factory.credentials(row)
    token = mint_inference_token(creds.api_key, creds.api_secret)
    return _Key(
        secrets={"api_key": token},
        scrub_values=[token, creds.api_key, creds.api_secret],
        fingerprint=connection_fingerprint(vault, row),
        credential_id=None,
    )


# ------------------------------------------------------------------ catalog + cost
async def cached_catalog_item(
    db: AsyncSession, *, spec: ProviderSpec, credential_id: str | None, model: str
) -> CatalogItem | None:
    """``model``'s item in the entry's cached ``models`` catalog (this key's row or a public one)."""
    condition: ColumnElement[bool] = ProviderCatalogCache.credential_id.is_(None)
    if credential_id is not None:
        condition = or_(ProviderCatalogCache.credential_id == credential_id, condition)
    rows = (
        await db.execute(
            select(ProviderCatalogCache.items).where(
                ProviderCatalogCache.provider_id == spec.id,
                ProviderCatalogCache.kind == "models",
                condition,
            )
        )
    ).scalars()
    for items in rows:
        for raw in items if isinstance(items, list) else []:
            if isinstance(raw, dict) and raw.get("id") == model:
                return CatalogItem.model_validate(raw)
    return None


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except InvalidOperation:
        return None


def _usage_note(usage: Usage) -> str:
    parts: list[str] = []
    if usage.tokens_in or usage.tokens_out:
        parts.append(f"{usage.tokens_in + usage.tokens_out} tokens")
    if usage.chars:
        parts.append(f"{usage.chars} characters")
    if usage.audio_s_in:
        parts.append(f"{usage.audio_s_in:g} s of audio")
    return " / ".join(parts) if parts else "no billable units"


def estimate_cost(
    spec: ProviderSpec, model: str, usage: Usage, catalog_item: CatalogItem | None
) -> tuple[Decimal | None, str]:
    """``pricing.lookup`` × the probe's usage, else OpenRouter's catalog pricing, else ``None``.

    Returns:
        ``(cost_estimate_usd, cost_note)``.
    """
    quantities: list[tuple[Unit, Decimal]] = [
        ("tokens_in", Decimal(usage.tokens_in)),
        ("tokens_out", Decimal(usage.tokens_out)),
        ("chars", Decimal(usage.chars)),
        ("audio_s_in", Decimal(str(usage.audio_s_in))),
    ]
    total = Decimal(0)
    priced = False
    for unit, quantity in quantities:
        if not quantity:
            continue
        price = lookup(spec.id, model, unit)
        if price is not None:
            total += price.usd_per_unit * quantity
            priced = True
    if priced:
        return total, "from the platform's price table"
    pricing = catalog_item.meta.get("pricing") if catalog_item is not None else None
    if isinstance(pricing, dict):
        prompt, completion = _decimal(pricing.get("prompt")), _decimal(pricing.get("completion"))
        if prompt is not None or completion is not None:
            cost = (prompt or Decimal(0)) * usage.tokens_in + (completion or Decimal(0)) * usage.tokens_out
            return cost, "from the vendor catalog's per-token pricing"
    return None, f"no price on file for this model; the probe used {_usage_note(usage)}"


# ------------------------------------------------------------------------ the run
def _scrubbed(outcome: ProbeOutcome, values: Iterable[str]) -> ProbeOutcome:
    secrets = [v for v in values if v]
    results = [
        r.model_copy(update={"message": scrub(r.message, secrets) if r.message else r.message})
        for r in outcome.results
    ]
    sample = scrub(outcome.sample, secrets, max_len=SAMPLE_MAX) if outcome.sample else None
    message = scrub(outcome.message, secrets) if outcome.message else None
    if message is None and results and results[0].message:
        message = results[0].message
    return ProbeOutcome(
        ok=outcome.ok,
        results=results,
        detected=outcome.detected,
        sample=sample or None,
        message=message,
        usage=outcome.usage,
    )


def _failed(message: str, latency_ms: int | None = None) -> ProbeOutcome:
    return ProbeOutcome(
        ok=False,
        results=[ProbeResult(name="basic", ok=False, latency_ms=latency_ms, message=message)],
        message=message,
    )


async def _run_probe(ctx: ProbeContext, probe_name: str, budget_s: float) -> tuple[ProbeOutcome, str | None]:
    """Run one probe; transport failures become ``ok=false`` (``error_type`` for the log)."""
    probe = PROBES[probe_name]
    try:
        return await asyncio.wait_for(probe.run(ctx), budget_s), None
    except TimeoutError:
        return _failed(f"no answer within {budget_s:g} s"), "TimeoutError"
    except ProbeInputError:
        raise
    except net_guard.BlockedDestinationError as exc:
        return _failed(str(exc)), type(exc).__name__
    except httpx.HTTPError as exc:
        blocked = net_guard.blocked_cause(exc)
        if blocked is not None:
            return _failed(str(blocked)), type(blocked).__name__
        return _failed(f"the vendor call failed: {type(exc).__name__}"), type(exc).__name__
    except (OSError, ValueError) as exc:
        return _failed(f"the vendor call failed: {type(exc).__name__}"), type(exc).__name__


def _cached_result(spec: ProviderSpec, model: str, row: Any) -> ModelTestResult:
    out = records.to_out(row)
    return ModelTestResult(
        ok=out.last_test_ok,
        provider_id=spec.id,
        model=model,
        kind=spec.kind,
        checked_at=out.last_test_at or utcnow(),
        cached=True,
        latency_ms=out.last_test_latency_ms,
        probes=[],
        detected=out.detected or ModelCapabilities(),
        cost_estimate_usd=out.last_test_cost_usd,
        cost_note="the result of the last run within 10 minutes; pass force=true to run it again",
        message=out.last_test_message,
        record_id=out.id,
    )


def _audit(
    db: AsyncSession, ctx: WorkspaceContext, spec: ProviderSpec, model: str, result: ModelTestResult
) -> None:
    audit.record(
        db,
        workspace_id=ctx.workspace_id,
        actor_type=ctx.actor.actor_type,
        actor_id=ctx.actor.id,
        action="provider.test_model",
        target_type="provider",
        target_id=spec.id,
        payload={"provider_id": spec.id, "model": model, "ok": result.ok, "latency_ms": result.latency_ms},
    )


async def _no_probe_result(
    db: AsyncSession, spec: ProviderSpec, request: ModelTestRequest, *, workspace_id: str
) -> ModelTestResult:
    if spec.kind == "image_gen":
        credential = await _credential(
            db, workspace_id=workspace_id, spec=spec, credential_id=request.credential_id
        )
        item = await cached_catalog_item(
            db, spec=spec, credential_id=credential.id if credential else None, model=request.model
        )
        listed = "is" if item is not None else "is not (or not yet)"
        message = f"no generation probe: images cost money; the id {listed} in the cached vendor list"
    else:
        message = NO_PROBE_MESSAGE
    return ModelTestResult(
        ok=None,
        provider_id=spec.id,
        model=request.model,
        kind=spec.kind,
        checked_at=utcnow(),
        cost_note="nothing was sent to the vendor",
        message=message,
    )


async def run_model_test(
    db: AsyncSession,
    *,
    ctx: WorkspaceContext,
    spec: ProviderSpec,
    request: ModelTestRequest,
    client: httpx.AsyncClient,
    ws: WsConnector,
    vault: Vault,
    factory: ConnectionClientFactory,
    limiter: RateLimiter,
    in_flight: InFlight,
    policy: net_guard.NetPolicy,
    timeouts: Timeouts | None = None,
) -> ModelTestResult:
    """Test one model id against its vendor, once, within the caps (see the module docstring).

    Raises:
        UnprocessableEntityError: A bad id or field, a refused ``base_url``, no key or connection.
        RateLimitedError: The per-workspace rate or concurrency cap is reached.
    """
    timeouts = timeouts or Timeouts()
    _checked_request(spec, request)
    base_url = _checked_base_url(request, policy)
    if spec.probe is None or spec.probe not in PROBES:
        result = await _no_probe_result(db, spec, request, workspace_id=ctx.workspace_id)
        _audit(db, ctx, spec, request.model, result)
        return result

    if spec.requires_credential:
        credential = await _credential(
            db, workspace_id=ctx.workspace_id, spec=spec, credential_id=request.credential_id
        )
        if credential is None:
            raise UnprocessableEntityError(
                f"no key for '{spec.id}' in this workspace (or several and no default); add one or pass "
                "credential_id"
            )
        raw = vault.decrypt(credential.ciphertext)
        key = _Key(
            secrets=normalize_secrets(spec, raw),
            scrub_values=list(raw.values()),
            fingerprint=credential.fingerprint,
            credential_id=credential.id,
        )
    elif spec.id.startswith(INFERENCE_PREFIX):
        key = await _inference_key(
            db, vault, factory, workspace_id=ctx.workspace_id, connection_id=request.connection_id
        )
    else:
        # A keyless entry that is not LiveKit Inference (AWS ambient credentials, say) has
        # nothing the api can sign with; never mint a LiveKit token for another vendor.
        raise UnprocessableEntityError(f"'{spec.id}' has no key the api can test with")

    row = await records.get_row(db, workspace_id=ctx.workspace_id, spec=spec, model_id=request.model)
    now = utcnow()
    if (
        not request.force
        and row is not None
        and row.last_test_at is not None
        and row.last_test_ok is not None
        and (now - row.last_test_at).total_seconds() < CACHE_TTL_S
        and row.last_test_fingerprint == key.fingerprint
    ):
        cached = _cached_result(spec, request.model, row)
        _audit(db, ctx, spec, request.model, cached)
        return cached

    decision = await limiter.hit(
        f"test-model:ws:{ctx.workspace_id}", capacity=RATE_CAPACITY, per_seconds=RATE_WINDOW_S
    )
    if not decision.allowed:
        raise _rate_limited(f"{RATE_CAPACITY} model tests per minute", decision.retry_after_s)

    probe_ctx = ProbeContext(
        client=client,
        spec=spec,
        model=request.model,
        secrets=key.secrets,
        fields=dict(request.fields),
        probes=frozenset(request.probes) | {"basic"},
        base_url=base_url,
        ws=ws,
    )
    with in_flight.slot(ctx.workspace_id):
        try:
            outcome, error_type = await _run_probe(probe_ctx, spec.probe, timeouts.for_kind(spec.kind))
        except ProbeInputError as exc:
            raise UnprocessableEntityError(str(exc)) from exc
    outcome = _scrubbed(outcome, [*key.scrub_values, *key.secrets.values()])

    catalog_item = await cached_catalog_item(
        db, spec=spec, credential_id=key.credential_id, model=request.model
    )
    cost, cost_note = estimate_cost(spec, request.model, outcome.usage, catalog_item)
    detected = outcome.detected
    if detected.model_dump(exclude={"source"}, exclude_none=True):
        detected = detected.model_copy(update={"source": "detected"})
    checked_at = utcnow()
    record = await records.upsert(
        db,
        workspace_id=ctx.workspace_id,
        spec=spec,
        model_id=request.model,
        last_test_at=checked_at,
        last_test_ok=outcome.ok,
        last_test_message=outcome.message,
        last_test_latency_ms=outcome.latency_ms,
        last_test_cost_usd=float(cost) if cost is not None else None,
        last_test_credential_id=key.credential_id,
        last_test_fingerprint=key.fingerprint,
        detected=detected.model_dump(mode="json"),
    )
    result = ModelTestResult(
        ok=outcome.ok,
        provider_id=spec.id,
        model=request.model,
        kind=spec.kind,
        checked_at=checked_at,
        latency_ms=outcome.latency_ms,
        probes=outcome.results,
        detected=detected,
        cost_estimate_usd=cost,
        cost_note=cost_note,
        message=outcome.message,
        sample=outcome.sample,
        record_id=record.id,
    )
    _audit(db, ctx, spec, request.model, result)
    log.info(
        "model_tested",
        provider_id=spec.id,
        model=request.model,
        probe=spec.probe,
        ok=outcome.ok,
        latency_ms=outcome.latency_ms,
        status_code=_status_code(outcome),
        error_type=error_type,
    )
    return result


def _status_code(outcome: ProbeOutcome) -> int | None:
    for result in outcome.results:
        message = result.message or ""
        if message.startswith("HTTP ") and message[5:8].isdigit():
            return int(message[5:8])
    return None


# ------------------------------------------------------------ delivery to the worker
async def with_capabilities(
    db: AsyncSession,
    *,
    workspace_id: str,
    resolved: Mapping[ProviderSlot, ResolvedProvider],
    credential_ids: Iterable[str],
) -> dict[ProviderSlot, ResolvedProvider]:
    """``resolved`` with ``capabilities`` filled on the ``llm``, ``workflow_llm`` and ``realtime`` slots.

    Declared → detected → the cached live catalog → the registry (R-V4-23); a
    slot whose provider is unknown keeps ``capabilities=None``.
    """
    out = dict(resolved)
    wanted: dict[ProviderSlot, tuple[ProviderSpec, str | None]] = {}
    for slot in CAPABILITY_SLOTS:
        provider = resolved.get(slot)
        if provider is None:
            continue
        try:
            spec = get(provider.provider_id)
        except KeyError:
            continue
        wanted[slot] = (spec, provider.model or spec.default_model)
    if not wanted:
        return out
    from lkap_api.config_service import _catalog_items  # noqa: PLC0415 - config_service imports this package

    by_record = await records.records_for_workspace(db, workspace_id=workspace_id)
    catalog = await _catalog_items(
        db, provider_ids={spec.id for spec, _ in wanted.values()}, credential_ids=list(credential_ids)
    )
    for slot, (spec, model) in wanted.items():
        record = by_record.get(records.record_key(spec, model)) if model else None
        item = catalog.get(spec.id, {}).get(model) if model else None
        caps = resolve_capabilities(spec, model, record, item)
        out[slot] = out[slot].model_copy(update={"capabilities": caps})
    return out


__all__ = [
    "CACHE_TTL_S",
    "CAPABILITY_SLOTS",
    "INFERENCE_TOKEN_TTL_S",
    "MAX_IN_FLIGHT",
    "NO_PROBE_MESSAGE",
    "PLUGIN_CAVEAT",
    "RATE_CAPACITY",
    "RATE_WINDOW_S",
    "TESTABLE_KINDS",
    "InFlight",
    "Timeouts",
    "cached_catalog_item",
    "estimate_cost",
    "mint_inference_token",
    "run_model_test",
    "with_capabilities",
]
