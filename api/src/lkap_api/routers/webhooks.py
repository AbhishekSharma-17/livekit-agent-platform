"""Outbound webhook endpoints: CRUD, test, deliveries, redeliver (CONTRACTS-V2 §3.4/§4.6).

Filled in by V2-08 (this file was a stub `APIRouter` created by V2-01; see
`main.py`'s docstring — nobody edits `main.py` again to include it, it
already is). Every route here is admin-only and workspace-scoped
(`ctx: AdminCtxDep`, `lkap_api.auth.deps`; `ROUTE_POLICY` already carries an
entry for `/v1/webhooks` — V2-02 added it while this package was in flight);
inbound LiveKit webhooks (`/hooks/livekit/{connection_id}`) are a different
router owned by V2-03 (`routers/hooks.py`).
"""

from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Query, Response, status
from lkap_contracts.api_models import (
    WebhookDeliveryOut,
    WebhookDeliveryPage,
    WebhookEndpointCreate,
    WebhookEndpointOut,
    WebhookEndpointPage,
)
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# Side-effecting imports: ensure this wave's other job handlers are registered
# as soon as any request touches the api (main.py always includes this
# router). Neither package's own router is wired into `main.py` yet in this
# wave (qa's re-score endpoint is V2-12's `routers/sessions.py`), so nothing
# else guarantees these are imported.
from lkap_api import qa as _qa  # noqa: F401,E402
from lkap_api.auth.deps import WorkspaceContext
from lkap_api.db.models import WebhookDelivery, WebhookEndpoint, new_id, utcnow
from lkap_api.deps import AdminCtxDep, DbDep, HttpClientDep, SettingsDep, VaultDep
from lkap_api.errors import NotFoundError, UnprocessableEntityError
from lkap_api.jobs.deps import JobsDep
from lkap_api.jobs.kinds import WEBHOOK_DELIVERY
from lkap_api.logging import get_logger
from lkap_api.settings import Settings
from lkap_api.vault import Vault
from lkap_api.webhooks.delivery import deliver_once
from lkap_api.webhooks.signing import generate_secret, secret_prefix

log = get_logger(__name__)

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])

DEFAULT_LIMIT = 25
MAX_LIMIT = 200

LimitQuery = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="Max rows to return")]
OffsetQuery = Annotated[int, Query(ge=0, description="Rows to skip")]


class WebhookEndpointCreated(WebhookEndpointOut):
    """The one response that carries the plaintext signing secret (shown once).

    `WebhookEndpointOut`/`WebhookEndpointCreate` (contracts, not this
    package's file) have no `secret` field, so a customer has no documented
    way to learn it; this is a stop-gap local response model — an ask to add
    `secret` to the contracts model is filed in `docs/v2/_asks.md`.
    """

    secret: str


def _check_url(settings: Settings, url: str) -> None:
    """Enforce `WebhookEndpointCreate`'s own contract: "https only outside dev".

    Args:
        settings: Used only for `settings.env`.
        url: The endpoint URL an admin is trying to save.

    Raises:
        UnprocessableEntityError: If the scheme is not `https` (or `http` in
            `LKAP_ENV=dev`, matching `scripts/webhook_sink.py`'s local listener).
    """
    allowed = ("https://",) if settings.env != "dev" else ("https://", "http://")
    if not url.startswith(allowed):
        raise UnprocessableEntityError(
            f"webhook url must start with {' or '.join(allowed)}", details={"url": url}
        )


def _endpoint_out(row: WebhookEndpoint, vault: Vault) -> WebhookEndpointOut:
    """Render one endpoint row.

    `webhook_endpoints` has no plaintext `secret_prefix` column (CONTRACTS-V2
    §1.5; `db/models.py` is V2-01's file, not this package's), so the prefix
    is recomputed by decrypting `secret_ct` — the same operation
    `webhooks.delivery.deliver_once` already performs for every attempt, and
    a short prefix is documented as non-secret (CONTRACTS-V2 §3.4).
    """
    secret = vault.decrypt(row.secret_ct)["secret"]
    return WebhookEndpointOut(
        id=row.id,
        url=row.url,
        events=list(row.events),
        description=row.description,
        enabled=bool(row.enabled),
        secret_prefix=secret_prefix(secret),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _delivery_out(row: WebhookDelivery) -> WebhookDeliveryOut:
    return WebhookDeliveryOut(
        id=row.id,
        endpoint_id=row.endpoint_id,
        event_type=row.event_type,
        event_id=row.event_id,
        attempt=row.attempt,
        status=cast(Literal["pending", "delivered", "failed", "dead"], row.status),
        next_attempt_at=row.next_attempt_at,
        last_status_code=row.last_status_code,
        last_error=row.last_error,
        created_at=row.created_at,
        delivered_at=row.delivered_at,
    )


async def _load_endpoint(db: AsyncSession, ctx: WorkspaceContext, endpoint_id: str) -> WebhookEndpoint:
    """Load an endpoint of the caller's workspace.

    Raises:
        NotFoundError: If no endpoint of this workspace matches (no existence
            leak: an endpoint of another workspace 404s the same way).
    """
    row = (
        await db.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.id == endpoint_id, WebhookEndpoint.workspace_id == ctx.workspace_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"unknown webhook endpoint '{endpoint_id}'")
    return row


async def _load_delivery(db: AsyncSession, ctx: WorkspaceContext, delivery_id: str) -> WebhookDelivery:
    """Load a delivery whose endpoint belongs to the caller's workspace.

    `webhook_deliveries` carries no `workspace_id` of its own (CONTRACTS-V2
    §1.5), so the scope check joins through `webhook_endpoints`.
    """
    row = (
        await db.execute(
            select(WebhookDelivery)
            .join(WebhookEndpoint, WebhookEndpoint.id == WebhookDelivery.endpoint_id)
            .where(WebhookDelivery.id == delivery_id, WebhookEndpoint.workspace_id == ctx.workspace_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"unknown webhook delivery '{delivery_id}'")
    return row


@router.post(
    "",
    response_model=WebhookEndpointCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a webhook endpoint",
    description="Creates an endpoint and returns its signing secret once; it is never shown again.",
)
async def create_webhook(
    payload: WebhookEndpointCreate, db: DbDep, settings: SettingsDep, vault: VaultDep, ctx: AdminCtxDep
) -> WebhookEndpointCreated:
    """Create a webhook endpoint with a freshly generated signing secret."""
    _check_url(settings, payload.url)
    secret = generate_secret()
    row = WebhookEndpoint(
        workspace_id=ctx.workspace_id,
        url=payload.url,
        secret_ct=vault.encrypt({"secret": secret}),
        events=list(payload.events),
        enabled=payload.enabled,
        description=payload.description,
    )
    db.add(row)
    await db.flush()
    log.info("webhook_endpoint_created", endpoint_id=row.id, events=row.events)
    out = _endpoint_out(row, vault)
    return WebhookEndpointCreated(**out.model_dump(), secret=secret)


@router.get(
    "",
    response_model=WebhookEndpointPage,
    summary="List webhook endpoints",
)
async def list_webhooks(
    db: DbDep, vault: VaultDep, ctx: AdminCtxDep, limit: LimitQuery = DEFAULT_LIMIT, offset: OffsetQuery = 0
) -> WebhookEndpointPage:
    """Return the caller's workspace's webhook endpoints, newest first."""
    rows = (
        (
            await db.execute(
                select(WebhookEndpoint)
                .where(WebhookEndpoint.workspace_id == ctx.workspace_id)
                .order_by(WebhookEndpoint.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    total = (
        await db.execute(
            select(func.count())
            .select_from(WebhookEndpoint)
            .where(WebhookEndpoint.workspace_id == ctx.workspace_id)
        )
    ).scalar_one()
    return WebhookEndpointPage(items=[_endpoint_out(row, vault) for row in rows], total=total)


@router.get("/{endpoint_id}", response_model=WebhookEndpointOut, summary="Get a webhook endpoint")
async def get_webhook(endpoint_id: str, db: DbDep, vault: VaultDep, ctx: AdminCtxDep) -> WebhookEndpointOut:
    """Return one webhook endpoint."""
    return _endpoint_out(await _load_endpoint(db, ctx, endpoint_id), vault)


@router.put("/{endpoint_id}", response_model=WebhookEndpointOut, summary="Update a webhook endpoint")
async def update_webhook(
    endpoint_id: str,
    payload: WebhookEndpointCreate,
    db: DbDep,
    settings: SettingsDep,
    vault: VaultDep,
    ctx: AdminCtxDep,
) -> WebhookEndpointOut:
    """Update a webhook endpoint's url/events/description/enabled. Never rotates the secret."""
    _check_url(settings, payload.url)
    row = await _load_endpoint(db, ctx, endpoint_id)
    row.url = payload.url
    row.events = list(payload.events)
    row.description = payload.description
    row.enabled = payload.enabled
    row.updated_at = utcnow()
    await db.flush()
    log.info("webhook_endpoint_updated", endpoint_id=row.id)
    return _endpoint_out(row, vault)


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a webhook endpoint")
async def delete_webhook(endpoint_id: str, db: DbDep, ctx: AdminCtxDep) -> Response:
    """Delete a webhook endpoint and its delivery history (cascade)."""
    row = await _load_endpoint(db, ctx, endpoint_id)
    await db.delete(row)
    await db.flush()
    log.info("webhook_endpoint_deleted", endpoint_id=endpoint_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class WebhookTestPayload(BaseModel):
    """Optional body for `POST /v1/webhooks/{id}/test`."""

    data: dict[str, object] = {}


@router.post(
    "/{endpoint_id}/test",
    response_model=WebhookDeliveryOut,
    summary="Send a test event",
    description="Delivers a synthetic `webhook.test` event synchronously and reports the outcome.",
)
async def test_webhook(
    endpoint_id: str,
    db: DbDep,
    vault: VaultDep,
    http: HttpClientDep,
    ctx: AdminCtxDep,
    payload: WebhookTestPayload | None = None,
) -> WebhookDeliveryOut:
    """Attempt one delivery of a synthetic test event immediately (not via the job queue)."""
    endpoint = await _load_endpoint(db, ctx, endpoint_id)
    event_id = new_id()
    delivery = WebhookDelivery(
        endpoint_id=endpoint.id,
        event_type="webhook.test",
        event_id=event_id,
        payload={
            "id": event_id,
            "type": "webhook.test",
            "created_at": utcnow().isoformat(),
            "workspace_id": endpoint.workspace_id,
            "data": (payload or WebhookTestPayload()).data,
        },
        status="pending",
        attempt=0,
    )
    db.add(delivery)
    await db.flush()
    outcome = await deliver_once(db, http, vault, delivery.id, ignore_enabled=True)
    await db.flush()
    if outcome is None:
        raise NotFoundError(f"unknown webhook endpoint '{endpoint_id}'")
    log.info(
        "webhook_test_sent", endpoint_id=endpoint_id, status=outcome.status, status_code=outcome.status_code
    )
    return _delivery_out(await _load_delivery(db, ctx, delivery.id))


@router.get(
    "/{endpoint_id}/deliveries",
    response_model=WebhookDeliveryPage,
    summary="List an endpoint's deliveries",
)
async def list_deliveries(
    endpoint_id: str, db: DbDep, ctx: AdminCtxDep, limit: LimitQuery = DEFAULT_LIMIT, offset: OffsetQuery = 0
) -> WebhookDeliveryPage:
    """Return one endpoint's delivery attempts, newest first."""
    await _load_endpoint(db, ctx, endpoint_id)
    rows = (
        (
            await db.execute(
                select(WebhookDelivery)
                .where(WebhookDelivery.endpoint_id == endpoint_id)
                .order_by(WebhookDelivery.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    total = (
        await db.execute(
            select(func.count())
            .select_from(WebhookDelivery)
            .where(WebhookDelivery.endpoint_id == endpoint_id)
        )
    ).scalar_one()
    return WebhookDeliveryPage(items=[_delivery_out(row) for row in rows], total=total)


@router.post(
    "/deliveries/{delivery_id}/redeliver",
    response_model=WebhookDeliveryOut,
    summary="Redeliver a delivery",
    description="Re-queues one more attempt for a `failed`, `dead` or still-`pending` delivery.",
)
async def redeliver(delivery_id: str, db: DbDep, jobs: JobsDep, ctx: AdminCtxDep) -> WebhookDeliveryOut:
    """Reopen a delivery and enqueue an immediate retry attempt."""
    row = await _load_delivery(db, ctx, delivery_id)
    row.status = "pending"
    row.next_attempt_at = None
    # Committed explicitly (not just flushed): `jobs.enqueue` opens its own
    # connection to write the `jobs` row, and on SQLite a second writer while
    # this request's transaction is still open deadlocks ("database is
    # locked") — the same rule `kb.ingest`/`upload_document` follow.
    await db.commit()
    await jobs.enqueue(WEBHOOK_DELIVERY, {"delivery_id": delivery_id})
    log.info("webhook_delivery_redelivered", delivery_id=delivery_id, endpoint_id=row.endpoint_id)
    # The inline backend may have already run the attempt (a different
    # session updated the row in the database), so re-read it rather than
    # returning the stale in-memory `row` from before `enqueue`.
    await db.refresh(row)
    return _delivery_out(row)
