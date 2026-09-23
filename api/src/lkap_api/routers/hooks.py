"""Inbound LiveKit webhooks, verified per connection (`/hooks/livekit/{connection_id}`).

Configure each LiveKit project/server to deliver webhooks to
``<LKAP_PUBLIC_BASE_URL>/hooks/livekit/<connection id>``, signed with the same
API key the connection stores. The route reads the **raw** body (the signature
covers its exact bytes), verifies it with that connection's secret and hands
the event to the handler registry in :mod:`lkap_api.connections.webhooks`.
"""

from __future__ import annotations

import time
from collections import OrderedDict

from fastapi import APIRouter, Request, Response, status

from lkap_api.connections.clients import ClientFactoryDep
from lkap_api.connections.service import get_connection_by_id
from lkap_api.connections.webhooks import WebhookContext, dispatch_webhook, verify_webhook
from lkap_api.deps import DbDep
from lkap_api.errors import NotFoundError, UnauthorizedError
from lkap_api.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/hooks", tags=["hooks"])

#: How long a handled event id is remembered. The signed JWT's own expiry
#: (``WebhookReceiver`` requires ``exp``) bounds how late a replay can arrive;
#: this window only has to outlast it.
REPLAY_WINDOW_S = 600
#: How many handled event ids each api process remembers.
SEEN_EVENTS_MAX = 10_000


class SeenEvents:
    """Event ids this process already handled, per connection, for :data:`REPLAY_WINDOW_S`.

    LiveKit signs the body and the JWT expires, but before it does a
    captured delivery could be posted again and would append duplicate session
    events or re-run finalisation. An id is remembered only once its handlers
    ran, so LiveKit's own retry of a failed (non-2xx) delivery is still handled.
    Per process: with several api replicas a replay can land once per replica,
    which the handlers' own idempotency covers.
    """

    def __init__(self) -> None:
        """Start empty."""
        self._clock = time.monotonic
        self._seen: OrderedDict[tuple[str, str], float] = OrderedDict()

    def seen(self, connection_id: str, event_id: str) -> bool:
        """Whether this event was already handled within the window."""
        self._expire()
        return (connection_id, event_id) in self._seen

    def remember(self, connection_id: str, event_id: str) -> None:
        """Record a handled event."""
        self._seen[(connection_id, event_id)] = self._clock()
        self._seen.move_to_end((connection_id, event_id))
        while len(self._seen) > SEEN_EVENTS_MAX:
            self._seen.popitem(last=False)

    def _expire(self) -> None:
        cutoff = self._clock() - REPLAY_WINDOW_S
        while self._seen and next(iter(self._seen.values())) < cutoff:
            self._seen.popitem(last=False)


def _seen_events(request: Request) -> SeenEvents:
    seen: SeenEvents | None = getattr(request.app.state, "livekit_seen_events", None)
    if seen is None:
        seen = SeenEvents()
        request.app.state.livekit_seen_events = seen
    return seen


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
    seen = _seen_events(request)
    if event.id and seen.seen(connection.id, event.id):
        log.info(
            "livekit_webhook_replay_ignored",
            connection_id=connection.id,
            event_type=event.event,
            event_id=event.id,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    ran = await dispatch_webhook(WebhookContext(db=db, connection=connection, event=event))
    if event.id:
        seen.remember(connection.id, event.id)
    log.info(
        "livekit_webhook_received",
        connection_id=connection.id,
        event_type=event.event,
        event_id=event.id,
        room=event.room.name or None,
        handlers=ran,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
