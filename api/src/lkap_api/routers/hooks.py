"""Inbound LiveKit webhooks, verified per connection (`/hooks/livekit/{connection_id}`).

Configure each LiveKit project/server to deliver webhooks to
``<LKAP_PUBLIC_BASE_URL>/hooks/livekit/<connection id>``, signed with the same
API key the connection stores. The route reads the **raw** body (the signature
covers its exact bytes), verifies it with that connection's secret and hands
the event to the handler registry in :mod:`lkap_api.connections.webhooks`.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from lkap_api.connections.clients import ClientFactoryDep
from lkap_api.connections.service import get_connection_by_id
from lkap_api.connections.webhooks import WebhookContext, dispatch_webhook, verify_webhook
from lkap_api.deps import DbDep
from lkap_api.errors import NotFoundError, UnauthorizedError
from lkap_api.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/hooks", tags=["hooks"])


@router.post(
    "/livekit/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="LiveKit webhook receiver",
    description=(
        "Receives LiveKit server webhooks for one connection. The `Authorization` JWT must be "
        "signed with that connection's API secret and carry the body's sha256; otherwise 401. "
        "Handles `room_finished`, `participant_joined/left` and `egress_ended`."
    ),
)
async def livekit_webhook(
    connection_id: str, request: Request, db: DbDep, factory: ClientFactoryDep
) -> Response:
    """Verify and dispatch one LiveKit webhook.

    Raises:
        UnauthorizedError: Unknown connection or invalid signature (both 401, so
            the url does not reveal which connection ids exist).
    """
    body = (await request.body()).decode("utf-8", errors="replace")
    try:
        connection = await get_connection_by_id(db, connection_id)
    except NotFoundError as exc:  # an unknown id is reported exactly like a bad signature
        raise UnauthorizedError("webhook signature is not valid for this connection") from exc
    event = verify_webhook(factory, connection, body, request.headers.get("Authorization"))
    ran = await dispatch_webhook(WebhookContext(db=db, connection=connection, event=event))
    log.info(
        "livekit_webhook_received",
        connection_id=connection.id,
        event_type=event.event,
        event_id=event.id,
        room=event.room.name or None,
        handlers=ran,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
