"""Inbound hooks: LiveKit webhooks per connection, and link outcomes per session (V5-43).

``/hooks/livekit/{connection_id}``: configure each LiveKit project/server to
deliver webhooks to ``<LKAP_PUBLIC_BASE_URL>/hooks/livekit/<connection id>``,
signed with the same API key the connection stores. The route reads the
**raw** body (the signature covers its exact bytes), verifies it with that
connection's secret and hands the event to the handler registry in
:mod:`lkap_api.connections.webhooks`.

``/v1/hooks/link/{session_id}`` (V5-43): the business's own system reports how
a ``link`` block's flow ended (a payment completed, a signature declined). It
is signed like LKAP's outbound webhooks (``X-LKAP-Signature: t=<unix>,v1=<hex
HMAC-SHA256 of "<t>.<raw body>">``, five minutes of clock drift) with the
signing secret of any enabled webhook endpoint of the session's workspace (the
secret shown once when the endpoint was created), so the two systems share one
secret already. The body is :class:`~lkap_contracts.ui_protocol.LinkHookIn`.
A verified outcome goes to the room's agents as a reliable
:class:`~lkap_contracts.ui_protocol.LinkCompletedPacket` on ``lkap.ui.link``
with the server API; the worker applies it only while the link is pending or
opened, so a repeated delivery changes nothing. An unknown session and a bad
signature are both 401 (the url does not reveal which sessions exist); a
session that is not live is 409 ``not_live``; no agent in the room is 409
``no_agent``.
"""

from __future__ import annotations

import time
from collections import OrderedDict

from fastapi import APIRouter, Request, Response, status
from livekit.api import ListParticipantsRequest, SendDataRequest
from livekit.protocol.models import DataPacket, ParticipantInfo
from lkap_contracts.ui_protocol import TOPIC_UI_LINK, LinkCompletedPacket, LinkHookIn, LinkHookOut
from pydantic import ValidationError
from sqlalchemy import select

from lkap_api.connections.clients import ClientFactoryDep
from lkap_api.connections.service import get_connection, get_connection_by_id
from lkap_api.connections.webhooks import WebhookContext, dispatch_webhook, verify_webhook
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import WebhookEndpoint, new_id
from lkap_api.deps import DbDep, VaultDep
from lkap_api.errors import ApiError, NotFoundError, UnauthorizedError, UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.telephony.common import raise_upstream
from lkap_api.webhooks.signing import SIGNATURE_HEADER, verify_signature

log = get_logger(__name__)

# No prefix: the LiveKit hook predates `/v1` and keeps `/hooks/livekit/...`; the link hook
# (V5-43) lives under `/v1/hooks/...` with the rest of the public api.
router = APIRouter(tags=["hooks"])

#: The largest link hook body read (the body is a handful of short fields).
MAX_LINK_HOOK_BYTES = 4096

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
    "/hooks/livekit/{connection_id}",
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


class LinkSessionNotLiveError(ApiError):
    """409 — the session has no live room to deliver the link outcome to."""

    status_code = 409
    code = "not_live"


class LinkNoAgentError(ApiError):
    """409 — no agent participant is in the session's room."""

    status_code = 409
    code = "no_agent"


async def _verified_link_hook(
    db: DbDep, vault: VaultDep, session_id: str, body: bytes, header: str | None
) -> SessionRow:
    """The session, once ``header`` verifies ``body`` with one of its workspace's endpoint secrets.

    Raises:
        UnauthorizedError: Unknown session, no enabled endpoint, or no secret verifies it.
    """
    refused = UnauthorizedError("link hook signature is not valid for this session")
    if not header:
        raise refused
    row = (
        await db.execute(
            select(SessionRow)
            .where(SessionRow.id == session_id)
            # The session id is the scope until the signature proves the workspace.
            .execution_options(lkap_cross_workspace=True)
        )
    ).scalar_one_or_none()
    if row is None:
        raise refused
    endpoints = (
        await db.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.workspace_id == row.workspace_id, WebhookEndpoint.enabled.is_(True)
            )
        )
    ).scalars()
    for endpoint in endpoints:
        try:
            secret = str(vault.decrypt(endpoint.secret_ct)["secret"])
        except Exception:  # noqa: BLE001 - an unreadable secret verifies nothing
            continue
        if verify_signature(secret, header, body):
            return row
    raise refused


@router.post(
    "/v1/hooks/link/{session_id}",
    response_model=LinkHookOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Report how a link on a live call ended",
    description=(
        "Your system reports the outcome of a payment, signing or portal link the agent gave the caller "
        "(`completed`, `failed` or `expired`), naming the link block (`block_id`) or the `reference` it "
        "was sent with. Sign the exact body like LKAP's outgoing webhooks: `X-LKAP-Signature: "
        't=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<body>">` with the signing secret of one of the '
        "workspace's webhook endpoints. 401 for an unknown session or a bad signature, 409 `not_live` "
        "when the call is over, 409 `no_agent` when no agent is in the room. The agent is told at once; "
        "a repeated report changes nothing."
    ),
)
async def link_hook(
    session_id: str, request: Request, db: DbDep, vault: VaultDep, factory: ClientFactoryDep
) -> LinkHookOut:
    """Verify a link outcome and hand it to the session's agent as a data packet.

    Raises:
        UnauthorizedError: Unknown session or a bad signature (both 401).
        UnprocessableEntityError: A body that is not a ``LinkHookIn`` or is too large.
        LinkSessionNotLiveError: The session is not active or has no connection.
        LinkNoAgentError: No agent participant is in the room.
    """
    body = await request.body()
    if len(body) > MAX_LINK_HOOK_BYTES:
        raise UnprocessableEntityError("the link hook body is too large")
    row = await _verified_link_hook(db, vault, session_id, body, request.headers.get(SIGNATURE_HEADER))
    try:
        hook = LinkHookIn.model_validate_json(body)
    except ValidationError as exc:
        raise UnprocessableEntityError(
            "the body must be {block_id | reference, status}",
            details={"reason": str(exc.errors()[0]["msg"])},
        ) from exc
    if row.status != "active" or not row.connection_id:
        raise LinkSessionNotLiveError(
            "the session is not live", details={"session_id": row.id, "status": row.status}
        )
    connection = await get_connection(db, row.workspace_id, row.connection_id)
    packet = LinkCompletedPacket(
        id=new_id(), session_id=row.id, block_id=hook.block_id, reference=hook.reference, status=hook.status
    )
    agents: list[str] = []
    try:
        async with factory.api(connection) as lk:
            listing = await lk.room.list_participants(ListParticipantsRequest(room=row.room_name))
            agents = [p.identity for p in listing.participants if p.kind == ParticipantInfo.Kind.AGENT]
            if agents:
                await lk.room.send_data(
                    SendDataRequest(
                        room=row.room_name,
                        data=packet.model_dump_json().encode(),
                        kind=DataPacket.Kind.RELIABLE,
                        topic=TOPIC_UI_LINK,
                        destination_identities=agents,
                    )
                )
    except Exception as exc:  # noqa: BLE001 - mapped to an api error
        raise_upstream(exc, action="sending the link outcome")
    if not agents:
        raise LinkNoAgentError(
            "no agent is in the room to receive the link outcome", details={"session_id": row.id}
        )
    log.info(
        "link_hook_delivered",
        session_id=row.id,
        packet_id=packet.id,
        status=hook.status,
        by_block=hook.block_id is not None,
        agents=len(agents),
    )
    return LinkHookOut(id=packet.id, session_id=row.id, status=hook.status, delivered_to=len(agents))
