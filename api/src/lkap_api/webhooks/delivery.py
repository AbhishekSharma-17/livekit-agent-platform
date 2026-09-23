"""Durable webhook delivery: one attempt at a time, HMAC-signed, retried on failure.

Matches (and per `docs/research-v2/dograh.md`, improves on) the dograh design:
durable `webhook_deliveries` rows, an atomic per-attempt claim (via the `jobs`
row wrapping each attempt), retry on 408/425/429/5xx/network errors on the
fixed CONTRACTS-V2 schedule, a dead letter after the schedule is exhausted,
and a signed, redaction-safe log line per attempt (never the url query, the
secret, or the payload body).
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any, Literal, cast

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import WebhookDelivery, WebhookEndpoint, utcnow
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.kinds import WEBHOOK_DELIVERY
from lkap_api.jobs.registry import job
from lkap_api.logging import get_logger
from lkap_api.vault import Vault
from lkap_api.webhooks.signing import EVENT_ID_HEADER, SIGNATURE_HEADER, sign

log = get_logger(__name__)

#: 1m, 5m, 30m, 2h, 6h, 12h, 24h, 24h (PLAN-V2 V2-08 card). Index `attempt - 1`
#: is the delay scheduled *after* that attempt fails; exhausting the list
#: (an 8th failure) is the dead letter.
RETRY_SCHEDULE_S: list[int] = [60, 300, 1800, 7200, 21600, 43200, 86400, 86400]
MAX_ATTEMPTS = len(RETRY_SCHEDULE_S)

#: Status codes that are retried like a 5xx even though they are 4xx
#: (timeout / precondition-failed / rate-limited — all transient).
RETRYABLE_4XX = frozenset({408, 425, 429})

DeliveryStatus = Literal["delivered", "pending", "failed", "dead"]


@dataclass(slots=True, frozen=True)
class DeliveryOutcome:
    """The result of one delivery attempt, for the job handler and tests."""

    status: DeliveryStatus
    attempt: int
    status_code: int | None
    error: str | None
    next_attempt_at: dt.datetime | None


def _is_retryable(status_code: int | None) -> bool:
    if status_code is None:
        return True  # network error / no response
    if status_code >= 500:
        return True
    return status_code in RETRYABLE_4XX


def _serialize(payload: dict[str, Any]) -> bytes:
    """Canonical JSON bytes: signed once, sent as those exact bytes (never re-encoded)."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")


async def deliver_once(
    session: AsyncSession,
    http: httpx.AsyncClient,
    vault: Vault,
    delivery_id: str,
    *,
    now: dt.datetime | None = None,
    ignore_enabled: bool = False,
) -> DeliveryOutcome | None:
    """Attempt one delivery, updating the `webhook_deliveries` row in place.

    Args:
        session: An open session; not committed here (the caller's `async with
            database.session()` block commits on success).
        http: The outbound HTTP client (mocked with `respx` in tests).
        vault: Decrypts the endpoint's signing secret.
        delivery_id: The `webhook_deliveries.id` to attempt.
        now: Injected clock for tests; defaults to the real current time.
        ignore_enabled: `POST /v1/webhooks/{id}/test` sends to a disabled
            endpoint anyway (an admin testing why they disabled it); the
            scheduled job path never passes this.

    Returns:
        The outcome, or `None` if the delivery row (or its endpoint) is gone —
        never raises for an HTTP-level failure, only for a delivery that no
        longer exists.
    """
    row = await session.get(WebhookDelivery, delivery_id)
    if row is None:
        log.warning("webhook_delivery_missing", delivery_id=delivery_id)
        return None
    if row.status in ("delivered", "dead"):
        return DeliveryOutcome(
            cast(DeliveryStatus, row.status), row.attempt, row.last_status_code, row.last_error, None
        )
    # A delivery row names its endpoint by id; the delivery job is workspace-agnostic.
    endpoint = (
        await session.execute(
            select(WebhookEndpoint)
            .where(WebhookEndpoint.id == row.endpoint_id)
            .execution_options(**{CROSS_WORKSPACE_OPTION: True})
        )
    ).scalar_one_or_none()
    if endpoint is None or (not endpoint.enabled and not ignore_enabled):
        log.info("webhook_delivery_endpoint_gone_or_disabled", delivery_id=delivery_id)
        return None

    ts = now or utcnow()
    secret = vault.decrypt(endpoint.secret_ct)["secret"]
    body = _serialize(row.payload)
    attempt = row.attempt + 1
    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: sign(secret, body, t=int(ts.timestamp())),
        EVENT_ID_HEADER: row.event_id,
    }

    status_code: int | None = None
    error: str | None = None
    try:
        response = await http.post(endpoint.url, content=body, headers=headers)
        status_code = response.status_code
        if 200 <= status_code < 300:
            row.status = "delivered"
            row.attempt = attempt
            row.last_status_code = status_code
            row.last_error = None
            row.delivered_at = ts
            row.next_attempt_at = None
            log.info(
                "webhook_delivered",
                delivery_id=delivery_id,
                endpoint_id=endpoint.id,
                event_type=row.event_type,
                attempt=attempt,
                status_code=status_code,
            )
            return DeliveryOutcome("delivered", attempt, status_code, None, None)
        error = f"http {status_code}"
    except httpx.HTTPError as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]

    if not _is_retryable(status_code):
        row.status = "failed"
        row.attempt = attempt
        row.last_status_code = status_code
        row.last_error = error
        row.next_attempt_at = None
        log.warning(
            "webhook_delivery_failed",
            delivery_id=delivery_id,
            endpoint_id=endpoint.id,
            attempt=attempt,
            status_code=status_code,
        )
        return DeliveryOutcome("failed", attempt, status_code, error, None)

    if attempt >= MAX_ATTEMPTS:
        row.status = "dead"
        row.attempt = attempt
        row.last_status_code = status_code
        row.last_error = error
        row.next_attempt_at = None
        log.warning(
            "webhook_delivery_dead",
            delivery_id=delivery_id,
            endpoint_id=endpoint.id,
            attempt=attempt,
            status_code=status_code,
        )
        return DeliveryOutcome("dead", attempt, status_code, error, None)

    delay_s = RETRY_SCHEDULE_S[attempt - 1]
    next_attempt_at = ts + dt.timedelta(seconds=delay_s)
    row.status = "pending"
    row.attempt = attempt
    row.last_status_code = status_code
    row.last_error = error
    row.next_attempt_at = next_attempt_at
    log.info(
        "webhook_delivery_retry_scheduled",
        delivery_id=delivery_id,
        endpoint_id=endpoint.id,
        attempt=attempt,
        status_code=status_code,
        next_attempt_at=next_attempt_at.isoformat(),
    )
    return DeliveryOutcome("pending", attempt, status_code, error, next_attempt_at)


@job(WEBHOOK_DELIVERY)
async def handle_webhook_delivery(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Job handler: one attempt, then (if it should retry) enqueue the next one.

    A delivery outcome is never a job failure — the `jobs` row for this
    attempt always finishes `done`; the delivery's own `pending`/`delivered`/
    `failed`/`dead` status lives on `webhook_deliveries`, per CONTRACTS-V2 §1.5.
    """
    delivery_id = str(payload["delivery_id"])
    async with ctx.database.session() as session:
        outcome = await deliver_once(session, ctx.http, ctx.vault, delivery_id)
    if outcome is not None and outcome.status == "pending" and outcome.next_attempt_at is not None:
        await ctx.jobs.enqueue(WEBHOOK_DELIVERY, {"delivery_id": delivery_id}, run_at=outcome.next_attempt_at)
