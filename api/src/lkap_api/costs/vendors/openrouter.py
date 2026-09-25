"""OpenRouter's charge per generation (V4-17, docs/v4/COSTS.md D-V4-45, R-V4-48).

``GET {base}/generation?id=<gen-id>`` with the workspace's regular OpenRouter
key returns the generation's record, ``total_cost`` in USD among it
(`docs/v4/_sources/costs-llm.md`). The worker reports the ids
(`metrics {kind: "provider_requests"}`, the LLM's ``request_id`` is the
completion id OpenRouter issued); :mod:`lkap_api.jobs.reconcile` sums them.

Rules:

* The credential is resolved the way the OpenRouter catalog adapter resolves
  it (R-V4-7): the slot's own ``credential_id``, else the workspace's default
  for the provider, else the workspace's only credential stored under the
  credential home ``openrouter-llm``; never another workspace's row.
* Every request goes through the job's ``net_guard`` client (and an offline
  :func:`lkap_api.net_guard.check_url` first), with a per-request timeout and
  at most :data:`MAX_LOOKUPS_PER_S` lookups a second.
* A ``404`` is "not recorded yet" (OpenRouter does not document the delay), so
  the id is returned in ``missing`` for the job to retry once later; a timeout,
  a transport error, a ``429`` or a ``5xx`` is retried here with a short backoff;
  a ``401``/``403`` stops the run (:class:`OpenRouterAuthError`).
* The key is never logged, never put in an error message, never returned.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import httpx
from lkap_contracts.common import ProviderRef
from lkap_contracts.providers import OPENROUTER_BASE_URL, OPENROUTER_CREDENTIAL_HOME, credential_home
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import net_guard
from lkap_api.db.models import Credential, WorkspaceProvider
from lkap_api.logging import get_logger
from lkap_api.vault import Vault

__all__ = [
    "MAX_LOOKUPS_PER_S",
    "Generation",
    "GenerationLookup",
    "OpenRouterAuthError",
    "OpenRouterLookupError",
    "fetch_generations",
    "openrouter_api_key",
]

log = get_logger(__name__)

#: D-V4-45: the lookup rate ceiling.
MAX_LOOKUPS_PER_S: Final = 10
#: Per-request timeout (seconds).
REQUEST_TIMEOUT_S: Final = 10.0
#: Extra attempts for a timeout, a transport error, a 429 or a 5xx.
TRANSIENT_RETRIES: Final = 2
#: Backoff before each transient retry (seconds): 1, then 2.
_BACKOFF_S: Final = (1.0, 2.0)

Sleep = Callable[[float], Awaitable[None]]


class OpenRouterLookupError(RuntimeError):
    """The lookup could not run at all (a blocked url)."""


class OpenRouterAuthError(OpenRouterLookupError):
    """OpenRouter refused the key (401/403); no further lookups are attempted."""


@dataclass(frozen=True, slots=True)
class Generation:
    """One generation's charge as OpenRouter recorded it."""

    id: str
    total_cost: Decimal
    model: str | None = None


@dataclass(slots=True)
class GenerationLookup:
    """The outcome of one batch of lookups.

    Attributes:
        found: Generations with a ``total_cost``, by id.
        missing: Ids OpenRouter answered ``404`` for (not recorded yet, or unknown).
        failed: Ids that still failed after the transient retries, or whose record
            carried no readable ``total_cost``.
    """

    found: dict[str, Generation] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


async def openrouter_api_key(
    db: AsyncSession, vault: Vault, *, workspace_id: str, ref: ProviderRef | None
) -> str | None:
    """The OpenRouter key a session's slot used, or the workspace's default/only one.

    Args:
        db: An open session.
        vault: Decrypts the credential.
        workspace_id: The session's workspace; rows of any other workspace are never read.
        ref: The slot's provider reference (its ``credential_id`` wins when set).

    Returns:
        The decrypted key, or ``None`` when no credential can be chosen.
    """
    provider_id = ref.provider_id if ref is not None else OPENROUTER_CREDENTIAL_HOME
    home = credential_home(provider_id)
    row: Credential | None = None
    if ref is not None and ref.credential_id:
        row = await db.scalar(
            select(Credential).where(
                Credential.id == ref.credential_id,
                Credential.workspace_id == workspace_id,
                Credential.provider_id == home,
            )
        )
    if row is None:
        settings_row = await db.get(WorkspaceProvider, (workspace_id, provider_id))
        if settings_row is not None and settings_row.default_credential_id:
            row = await db.scalar(
                select(Credential).where(
                    Credential.id == settings_row.default_credential_id,
                    Credential.workspace_id == workspace_id,
                    Credential.provider_id == home,
                )
            )
    if row is None:
        candidates = (
            (
                await db.execute(
                    select(Credential).where(
                        Credential.workspace_id == workspace_id, Credential.provider_id == home
                    )
                )
            )
            .scalars()
            .all()
        )
        row = candidates[0] if len(candidates) == 1 else None
    if row is None:
        return None
    key = vault.decrypt(row.ciphertext).get("api_key", "")
    return key or None


def _record(body: Any) -> dict[str, Any] | None:
    """The generation record: OpenRouter wraps it in ``{"data": {...}}``; a bare record is accepted too."""
    if not isinstance(body, dict):
        return None
    data = body.get("data", body)
    return data if isinstance(data, dict) else None


def _usd(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() and amount >= 0 else None


async def fetch_generations(
    http: httpx.AsyncClient,
    *,
    api_key: str,
    ids: Sequence[str],
    policy: net_guard.NetPolicy,
    base_url: str = OPENROUTER_BASE_URL,
    sleep: Sleep = asyncio.sleep,
    rate_per_s: float = MAX_LOOKUPS_PER_S,
) -> GenerationLookup:
    """Look up each generation id's charge.

    Args:
        http: The job's outbound client (``net_guard``-guarded in production).
        api_key: The workspace's OpenRouter key.
        ids: ``gen-…`` ids, looked up in order (duplicates once).
        policy: The api's network policy, checked offline before any request.
        base_url: OpenRouter's API root.
        sleep: Injected for tests; paces the requests and the retry backoff.
        rate_per_s: Lookups per second (≤ :data:`MAX_LOOKUPS_PER_S`).

    Returns:
        What was found, missing (404) and failed.

    Raises:
        OpenRouterLookupError: The url is refused by the network policy.
        OpenRouterAuthError: OpenRouter answered 401/403 (a wrong or revoked key).
    """
    url = f"{base_url.rstrip('/')}/generation"
    problem = net_guard.check_url(url, policy)
    if problem is not None:
        raise OpenRouterLookupError(f"OpenRouter url refused: {problem}")
    interval = 1.0 / min(max(rate_per_s, 0.1), MAX_LOOKUPS_PER_S)
    headers = {"Authorization": f"Bearer {api_key}"}
    out = GenerationLookup()
    for index, gen_id in enumerate(dict.fromkeys(i for i in ids if i)):
        if index:
            await sleep(interval)
        response: httpx.Response | None = None
        for attempt in range(TRANSIENT_RETRIES + 1):
            if attempt:
                await sleep(_BACKOFF_S[min(attempt - 1, len(_BACKOFF_S) - 1)])
            try:
                response = await http.get(
                    url, params={"id": gen_id}, headers=headers, timeout=REQUEST_TIMEOUT_S
                )
            except httpx.HTTPError as exc:
                if net_guard.blocked_cause(exc) is not None:
                    raise OpenRouterLookupError("OpenRouter url refused by the network policy") from None
                log.info("openrouter_generation_transient", attempt=attempt + 1, error=type(exc).__name__)
                response = None
                continue
            if response.status_code == 429 or response.status_code >= 500:
                log.info("openrouter_generation_transient", attempt=attempt + 1, status=response.status_code)
                continue
            break
        if response is None or response.status_code == 429 or response.status_code >= 500:
            out.failed.append(gen_id)
            continue
        if response.status_code in (401, 403):
            raise OpenRouterAuthError(f"OpenRouter refused the key ({response.status_code})")
        if response.status_code == 404:
            out.missing.append(gen_id)
            continue
        if response.status_code != 200:
            out.failed.append(gen_id)
            continue
        try:
            record = _record(response.json())
        except ValueError:
            record = None
        cost = _usd(record.get("total_cost")) if record is not None else None
        if record is None or cost is None:
            out.failed.append(gen_id)
            continue
        model = record.get("model")
        out.found[gen_id] = Generation(
            id=gen_id, total_cost=cost, model=model if isinstance(model, str) else None
        )
    return out
