"""Inbound LiveKit webhooks, verified per connection (CONTRACTS-V2 §3.4).

Each connection registers its own receiver url, ``/hooks/livekit/{connection_id}``,
because a webhook's claims only identify the sending project *after* the JWT
is verified — so the connection id in the path picks the key/secret to verify
with (research §6). Verification is :class:`livekit.api.WebhookReceiver`: the
``Authorization`` JWT must be signed with that connection's secret and carry
the sha256 of the **raw** request body.

Handlers are a registry keyed by event name, so later packages attach their
own behaviour from their own modules without editing this one (the same idea
as ``config_service.VALIDATORS``)::

    from lkap_api.connections.webhooks import register_webhook_handler

    @register_webhook_handler("egress_ended")
    async def finalise_recording(ctx: WebhookContext) -> None: ...

Built-in handlers (all idempotent, none overwrite worker-reported data):

* ``room_finished`` — appends a ``room_finished`` session event; a session still
  ``created`` (the agent never joined) becomes ``failed``/``never started``, the
  same outcome the stale-session sweep would reach later.
* ``participant_joined`` / ``participant_left`` — append a session event.
* ``egress_ended`` — sets ``recording_status`` to ``ready``/``failed`` on the
  session that owns the egress id (V2-12 adds object key/duration handling).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from livekit.api import WebhookReceiver
from livekit.protocol.egress import EgressStatus
from livekit.protocol.webhook import WebhookEvent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.connections.clients import ConnectionClientFactory
from lkap_api.db.models import LiveKitConnection, SessionEvent, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.errors import UnauthorizedError
from lkap_api.logging import get_logger
from lkap_api.sessions_sweep import NEVER_STARTED

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WebhookContext:
    """Everything a handler gets: the open session, the connection and the event."""

    db: AsyncSession
    connection: LiveKitConnection
    event: WebhookEvent


WebhookHandler = Callable[[WebhookContext], Awaitable[None]]

#: ``event name → handlers`` in registration order.
WEBHOOK_HANDLERS: dict[str, list[WebhookHandler]] = {}


def register_webhook_handler(event_name: str) -> Callable[[WebhookHandler], WebhookHandler]:
    """Decorator registering ``handler`` for one LiveKit webhook event name.

    Args:
        event_name: E.g. ``"egress_ended"``, ``"room_finished"``, ``"participant_left"``.

    Returns:
        The decorator; it returns the handler unchanged.
    """

    def _register(handler: WebhookHandler) -> WebhookHandler:
        handlers = WEBHOOK_HANDLERS.setdefault(event_name, [])
        if handler not in handlers:
            handlers.append(handler)
        return handler

    return _register


def verify_webhook(
    factory: ConnectionClientFactory, connection: LiveKitConnection, body: str, auth_token: str | None
) -> WebhookEvent:
    """Verify a webhook against its connection's key/secret and parse it.

    Args:
        factory: Client factory (decrypts the connection secret).
        connection: The connection named in the url.
        body: The raw request body, exactly as received.
        auth_token: The ``Authorization`` header value (a bare JWT, optionally ``Bearer``-prefixed).

    Returns:
        The parsed event.

    Raises:
        UnauthorizedError: On a missing, forged, expired or body-mismatched signature.
    """
    if not auth_token:
        raise UnauthorizedError("missing webhook Authorization header")
    token = auth_token.removeprefix("Bearer ").strip()
    receiver = WebhookReceiver(factory.token_verifier(connection))
    try:
        return receiver.receive(body, token)
    except Exception as exc:  # noqa: BLE001 - jwt/hash/parse failures all mean "not authentic"
        log.warning("livekit_webhook_rejected", connection_id=connection.id, error_type=type(exc).__name__)
        raise UnauthorizedError("webhook signature is not valid for this connection") from exc


async def dispatch_webhook(ctx: WebhookContext) -> int:
    """Run every handler registered for the event.

    A failing handler is logged and does not stop the others (LiveKit retries a
    non-2xx delivery, which would re-run the handlers that already succeeded).

    Returns:
        How many handlers ran successfully.
    """
    ran = 0
    for handler in WEBHOOK_HANDLERS.get(ctx.event.event, []):
        try:
            await handler(ctx)
        except Exception:  # noqa: BLE001 - one bad handler must not drop the event for the rest
            log.exception(
                "livekit_webhook_handler_failed",
                connection_id=ctx.connection.id,
                event_type=ctx.event.event,
                handler=getattr(handler, "__name__", repr(handler)),
            )
            continue
        ran += 1
    return ran


def _event_time(event: WebhookEvent) -> dt.datetime:
    if event.created_at:
        return dt.datetime.fromtimestamp(event.created_at, tz=dt.UTC)
    return utcnow()


async def _session_for_room(ctx: WebhookContext) -> SessionRow | None:
    room_name = ctx.event.room.name
    if not room_name:
        return None
    row: SessionRow | None = await ctx.db.scalar(
        select(SessionRow).where(
            SessionRow.workspace_id == ctx.connection.workspace_id,
            SessionRow.room_name == room_name,
        )
    )
    if row is not None and row.connection_id not in (None, ctx.connection.id):
        return None
    return row


def _add_event(ctx: WebhookContext, session: SessionRow, payload: dict[str, object]) -> None:
    ctx.db.add(
        SessionEvent(
            session_id=session.id,
            ts=_event_time(ctx.event),
            type=ctx.event.event,
            payload={"source": "livekit_webhook", "event_id": ctx.event.id, **payload},
        )
    )


@register_webhook_handler("room_finished")
async def _on_room_finished(ctx: WebhookContext) -> None:
    session = await _session_for_room(ctx)
    if session is None:
        return
    _add_event(ctx, session, {"room": ctx.event.room.name})
    if session.status == "created":
        session.status = "failed"
        session.error = NEVER_STARTED
        session.ended_at = _event_time(ctx.event)
    await ctx.db.flush()


@register_webhook_handler("participant_joined")
@register_webhook_handler("participant_left")
async def _on_participant(ctx: WebhookContext) -> None:
    session = await _session_for_room(ctx)
    if session is None:
        return
    _add_event(ctx, session, {"identity": ctx.event.participant.identity})
    await ctx.db.flush()


@register_webhook_handler("egress_ended")
async def _on_egress_ended(ctx: WebhookContext) -> None:
    info = ctx.event.egress_info
    if not info.egress_id:
        return
    session: SessionRow | None = await ctx.db.scalar(
        select(SessionRow).where(
            SessionRow.workspace_id == ctx.connection.workspace_id,
            SessionRow.recording_egress_id == info.egress_id,
        )
    )
    if session is None:
        return
    session.recording_status = "ready" if info.status == EgressStatus.EGRESS_COMPLETE else "failed"
    await ctx.db.flush()
