"""`emit()`: the entry point other packages call to fan an event out to webhooks.

V2-03's `PUT /internal/v1/sessions/{id}/summary` (`routers/internal.py`) and
V2-12's session/recording code are the natural callers of `emit(...,
events.SESSION_ENDED, ...)` / `events.RECORDING_READY` — neither file is
owned by this package, so a one-line ask is filed in `docs/v2/_asks.md`
instead of calling `emit` from here.
"""

from __future__ import annotations

from typing import Any

from fastapi import BackgroundTasks
from sqlalchemy import select

from lkap_api.db.models import WebhookDelivery, WebhookEndpoint, new_id, utcnow
from lkap_api.db.session import Database
from lkap_api.jobs.kinds import WEBHOOK_DELIVERY
from lkap_api.jobs.service import JobsService
from lkap_api.logging import get_logger

log = get_logger(__name__)


async def emit(
    database: Database,
    jobs: JobsService,
    *,
    workspace_id: str,
    event_type: str,
    data: dict[str, Any],
    background_tasks: BackgroundTasks | None = None,
) -> list[str]:
    """Create a durable delivery row for every matching, enabled endpoint and enqueue it.

    Args:
        database: The process database (opens its own session; safe to call
            from a request handler, a job handler, or a background loop).
        jobs: The `JobsService` used to schedule each delivery attempt.
        workspace_id: The event's workspace (every endpoint outside it is ignored).
        event_type: One of `lkap_api.webhooks.events` (or a future one; an
            endpoint with an empty `events` list matches everything).
        data: The event body (`WebhookEvent.data`) — never a secret or raw
            credential; caller's responsibility per CONTRACTS §7 logging rules.
        background_tasks: Forwarded to `JobsService.enqueue` for every
            delivery — pass this from a request handler (V2-03's session
            summary endpoint, V2-12's recording-ready hook) so the first
            delivery attempt's HTTP call runs after the response is sent
            rather than blocking it; omit it when calling from a job handler
            or a background loop, where an inline `await` is already fine.

    Returns:
        The ids of the `webhook_deliveries` rows created (may be empty).
    """
    event_id = new_id()
    created_at = utcnow()
    envelope = {
        "id": event_id,
        "type": event_type,
        "created_at": created_at.isoformat(),
        "workspace_id": workspace_id,
        "data": data,
    }
    delivery_ids: list[str] = []
    async with database.session() as session:
        endpoints = (
            (
                await session.execute(
                    select(WebhookEndpoint).where(
                        WebhookEndpoint.workspace_id == workspace_id, WebhookEndpoint.enabled.is_(True)
                    )
                )
            )
            .scalars()
            .all()
        )
        matching = [e for e in endpoints if not e.events or event_type in e.events]
        for endpoint in matching:
            delivery = WebhookDelivery(
                endpoint_id=endpoint.id,
                event_type=event_type,
                event_id=event_id,
                payload=envelope,
                status="pending",
                attempt=0,
            )
            session.add(delivery)
            await session.flush()
            delivery_ids.append(delivery.id)

    for delivery_id in delivery_ids:
        await jobs.enqueue(WEBHOOK_DELIVERY, {"delivery_id": delivery_id}, background_tasks=background_tasks)

    log.info(
        "webhook_event_emitted",
        event_type=event_type,
        workspace_id=workspace_id,
        deliveries=len(delivery_ids),
    )
    return delivery_ids
