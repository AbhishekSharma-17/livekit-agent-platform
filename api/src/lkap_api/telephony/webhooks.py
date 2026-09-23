"""LiveKit webhook handlers that drive the ``calls`` table (CONTRACTS-V2 §3.4).

Registered on :mod:`lkap_api.connections.webhooks`' handler registry when this
module is imported (``routers/calls.py`` imports it, so registration happens
at app import). Every handler is idempotent and forward-only
(:func:`lkap_api.telephony.calls.advance`), so LiveKit's redeliveries and the
worker's own reports can arrive in any order.

* ``participant_joined`` (SIP participant): ``sip.callStatus == "active"`` →
  ``answered``; an outbound leg that is still ringing → ``ringing``. An inbound
  leg with no ``calls`` row yet gets one, and the session's ``caller`` is
  filled from the ``sip.*`` attributes when the worker did not already.
* ``participant_left`` (SIP participant): ``answered`` → ``completed``; a leg
  that never answered maps the disconnect reason (``USER_UNAVAILABLE`` →
  ``no_answer``, ``USER_REJECTED`` → ``busy``, else ``failed``).
* ``room_finished``: closes any call of that room that is still open.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from livekit.protocol.models import DisconnectReason, ParticipantInfo
from sqlalchemy import select

from lkap_api.connections.webhooks import WebhookContext, register_webhook_handler
from lkap_api.db.models import Call, SipTrunk, new_id, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.logging import get_logger
from lkap_api.telephony.calls import CallStatus, advance, call_for_session, link_session, orphan_call

log = get_logger(__name__)

#: ``sip.*`` participant attributes LiveKit sets on a SIP participant.
ATTR_CALL_STATUS = "sip.callStatus"
ATTR_CALL_ID = "sip.callID"
ATTR_PHONE_NUMBER = "sip.phoneNumber"
ATTR_TRUNK_PHONE_NUMBER = "sip.trunkPhoneNumber"
ATTR_TRUNK_ID = "sip.trunkID"

_NEVER_ANSWERED: dict[int, CallStatus] = {
    DisconnectReason.USER_UNAVAILABLE: "no_answer",
    DisconnectReason.USER_REJECTED: "busy",
}


def caller_from_attributes(attributes: dict[str, str], *, direction: str) -> dict[str, Any]:
    """The ``sessions.caller`` JSON (CONTRACTS-V2 §1.3) from SIP participant attributes."""
    remote = attributes.get(ATTR_PHONE_NUMBER, "")
    ours = attributes.get(ATTR_TRUNK_PHONE_NUMBER, "")
    return {
        "direction": direction,
        "from": remote if direction == "inbound" else ours,
        "to": ours if direction == "inbound" else remote,
        "trunk_id": attributes.get(ATTR_TRUNK_ID, ""),
        "call_id": attributes.get(ATTR_CALL_ID, ""),
    }


def _event_time(ctx: WebhookContext) -> dt.datetime:
    if ctx.event.created_at:
        return dt.datetime.fromtimestamp(ctx.event.created_at, tz=dt.UTC)
    return utcnow()


def _is_sip(participant: ParticipantInfo) -> bool:
    return bool(participant.identity) and participant.kind == ParticipantInfo.Kind.SIP


async def _session(ctx: WebhookContext) -> SessionRow | None:
    room = ctx.event.room.name
    if not room:
        return None
    row: SessionRow | None = await ctx.db.scalar(
        select(SessionRow).where(
            SessionRow.workspace_id == ctx.connection.workspace_id, SessionRow.room_name == room
        )
    )
    if row is not None and row.connection_id not in (None, ctx.connection.id):
        return None
    return row


async def _call(ctx: WebhookContext, session: SessionRow | None, sip_call_id: str) -> Call | None:
    """The call of this SIP leg: by session, else an orphan row with the same SIP call id."""
    if session is not None:
        found = await call_for_session(ctx.db, session)
        if found is not None:
            return found
    orphan = await orphan_call(ctx.db, ctx.connection.workspace_id, ctx.connection.id, sip_call_id or None)
    if orphan is not None and session is not None:
        link_session(orphan, session)
    return orphan


async def _is_our_trunk(ctx: WebhookContext, lk_trunk_id: str) -> bool:
    """Whether a SIP leg came in on one of this workspace's trunks (not another app's)."""
    if not lk_trunk_id:
        return False
    found = await ctx.db.scalar(
        select(SipTrunk.id).where(
            SipTrunk.workspace_id == ctx.connection.workspace_id,
            SipTrunk.connection_id == ctx.connection.id,
            SipTrunk.lk_trunk_id == lk_trunk_id,
        )
    )
    return found is not None


@register_webhook_handler("participant_joined")
async def on_sip_participant_joined(ctx: WebhookContext) -> None:
    """A SIP leg joined its room: answered (or ringing), caller filled, inbound row created.

    The webhook usually beats the worker's ``sessions/start`` for an inbound
    call (the SIP service creates the room before the job is accepted). The
    row is then created without a session, only when the leg came in on one of
    this workspace's trunks (the project may carry other apps' SIP traffic),
    and linked by ``sip.callID`` when the session appears.
    """
    participant = ctx.event.participant
    if not _is_sip(participant):
        return
    attributes = dict(participant.attributes)
    sip_call_id = attributes.get(ATTR_CALL_ID, "")[:128]
    session = await _session(ctx)
    call = await _call(ctx, session, sip_call_id)
    if call is None:
        if session is not None and session.channel != "sip_in":
            return
        if session is None and not await _is_our_trunk(ctx, attributes.get(ATTR_TRUNK_ID, "")):
            return
        caller = caller_from_attributes(attributes, direction="inbound")
        call = Call(
            id=new_id(),
            session_id=session.id if session else None,
            workspace_id=ctx.connection.workspace_id,
            connection_id=ctx.connection.id,
            direction="inbound",
            from_e164=str(caller["from"])[:32],
            to_e164=str(caller["to"])[:32],
            status="dialing",
            sip_call_id=sip_call_id or None,
            started_at=_event_time(ctx),
        )
        ctx.db.add(call)
    if session is not None and not session.caller:
        session.caller = caller_from_attributes(attributes, direction=call.direction)
    call.lk_participant_identity = participant.identity  # LiveKit is authoritative for the identity
    if not call.sip_call_id and sip_call_id:
        call.sip_call_id = sip_call_id
    status = attributes.get(ATTR_CALL_STATUS, "")
    when = _event_time(ctx)
    if status == "active" or (not status and call.direction == "inbound"):
        advance(call, "answered", at=when)
    elif call.direction == "outbound":
        advance(call, "ringing", at=when)
    await ctx.db.flush()
    log.info("sip_participant_joined", call_id=call.id, status=call.status, room=ctx.event.room.name)


@register_webhook_handler("participant_left")
async def on_sip_participant_left(ctx: WebhookContext) -> None:
    """A SIP leg left: completed if it was answered, otherwise why it never was."""
    participant = ctx.event.participant
    if not _is_sip(participant):
        return
    session = await _session(ctx)
    call = await _call(ctx, session, dict(participant.attributes).get(ATTR_CALL_ID, ""))
    if call is None:
        return
    reason_name = DisconnectReason.Name(participant.disconnect_reason).lower()
    when = _event_time(ctx)
    if call.status == "answered":
        advance(call, "completed", at=when, reason=reason_name)
    else:
        advance(
            call, _NEVER_ANSWERED.get(participant.disconnect_reason, "failed"), at=when, reason=reason_name
        )
    await ctx.db.flush()


@register_webhook_handler("room_finished")
async def on_call_room_finished(ctx: WebhookContext) -> None:
    """The room closed: any call of it still open is over."""
    session = await _session(ctx)
    if session is None:
        return
    call = await call_for_session(ctx.db, session)
    if call is None:
        return
    when = _event_time(ctx)
    advance(call, "completed" if call.status == "answered" else "failed", at=when, reason="room_finished")
    await ctx.db.flush()
