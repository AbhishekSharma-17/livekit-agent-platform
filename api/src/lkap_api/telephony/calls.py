"""Calls: outbound dialing, hangup, cold transfer, DTMF and the status machine.

Outbound (``POST /v1/calls``, ARCHITECTURE-V2 §2.6):

1. :func:`prepare_outbound_call` stores a ``sessions`` row (``channel=sip_out``,
   ``participant_identity`` = the SIP leg's identity) and a ``calls`` row
   (``dialing``) and returns a :class:`DialPlan`. The route commits and answers
   at once.
2. :func:`run_dial` runs after the response (FastAPI background task, on its
   own database session): it dispatches the agent into the call's room
   **first**, so the callee never answers into silence, then
   ``SipService.create_sip_participant(wait_until_answered=True)``. Answer →
   ``answered``; a ``SipCallError`` maps its SIP status to ``busy`` /
   ``no_answer`` / ``failed`` and the room is deleted so the agent leaves.
   The client is built with ``failover=False``: the SDK's region failover
   replays a request on a transport error, which for a dial would ring the
   callee twice.

Every dial and transfer first passes the workspace's dialing policy
(:mod:`lkap_api.telephony.policy`, R-V2-23); a dial also respects the
policy's open-outbound cap and the agent's ``max_concurrent_sessions``.

Status only moves forward (:func:`advance`): ``dialing → ringing → answered →
{completed | transferred}``, or from any open state to ``no_answer`` /
``busy`` / ``failed``. The dial task, LiveKit webhooks
(:mod:`lkap_api.telephony.webhooks`) and the worker's reports
(``POST /internal/v1/telephony/calls/report``) all feed the same function, so
whichever arrives first wins and the others are no-ops.

**Webhooks (ask #76, V2-22).** :func:`advance` is the one place a call moves
forward, so it is where ``call.started`` (into ``answered``) and ``call.ended``
(into a terminal state) come from. It runs inside its caller's open
transaction, and ``webhooks.emit`` opens a connection of its own (the SQLite
deadlock of ask #40), so it never emits: it adds a ``call_event`` job row to
the call's own session, which commits with the transition (or rolls back with
it), and :func:`_emit_call_event` fans the webhook out a tick later on its own
connection — the outbox pattern ``recordings.finalize`` uses for
``recording.ready``. A bare row outside any session (unit tests) emits nothing.

:func:`sweep_stuck_calls` (run by ``sessions_sweep.sweep_loop``, R-V2-24)
closes rows nobody else will: an outbound dial the api forgot (restart
mid-dial), and any open row whose session already ended.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from google.protobuf.duration_pb2 import Duration
from livekit.api import (
    CreateAgentDispatchRequest,
    CreateSIPParticipantRequest,
    DataPacket,
    DeleteRoomRequest,
    SendDataRequest,
    SipCallError,
    SIPTransferReason,
    SIPTransferStatus,
    TransferSIPParticipantRequest,
)
from lkap_contracts.agent_config import AgentConfig, AgentLimits
from lkap_contracts.api_models import CallCreate, CallOut, CallReportIn
from lkap_contracts.dispatch import DispatchMetadata
from lkap_contracts.telephony import E164_PATTERN
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import object_session

from lkap_api.connections.clients import ConnectionClientFactory
from lkap_api.connections.service import resolve_agent_connection
from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Call, Job, LiveKitConnection, SipTrunk, new_id, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.errors import ConflictError, NotFoundError, UnprocessableEntityError
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.registry import job
from lkap_api.limits import live_session_count
from lkap_api.logging import get_logger
from lkap_api.telephony.common import (
    DTMF_TOPIC,
    LiveKitUpstreamError,
    connection_of,
    get_agent,
    get_trunk,
    is_not_found,
    raise_upstream,
    require_sip,
    to_sip_uri,
)
from lkap_api.telephony.policy import CallsBusyError, TelephonyPolicy, check_destination
from lkap_api.webhooks import emit
from lkap_api.webhooks.events import CALL_ENDED, CALL_STARTED

log = get_logger(__name__)

CallStatus = Literal[
    "dialing", "ringing", "answered", "no_answer", "busy", "failed", "completed", "transferred"
]

#: Open states in the order a call moves through them.
_OPEN_RANK: dict[str, int] = {"dialing": 0, "ringing": 1, "answered": 2}

#: States a call can still leave.
OPEN: frozenset[str] = frozenset(_OPEN_RANK)

#: States a call never leaves.
TERMINAL: frozenset[str] = frozenset({"no_answer", "busy", "failed", "completed", "transferred"})

#: Ring window bounds for ``CallCreate.timeout_s``.
MIN_RING_S, MAX_RING_S = 5, 120

#: Extra seconds the dial request may take beyond the ring window (dispatch + SIP setup).
DIAL_MARGIN_S = 15.0

#: Seconds a cold transfer may take (it dials the target).
TRANSFER_TIMEOUT_S = 45.0

#: Age after which an outbound call still ``dialing``/``ringing`` is swept to ``failed``
#: (R-V2-24): the longest dial the api makes plus a minute.
STUCK_DIAL_AFTER_S = MAX_RING_S + DIAL_MARGIN_S + 60

#: ``hangup_reason`` of a swept dial.
SWEPT_DIAL_REASON = "dial timed out (swept)"

#: Session states after which the session's call cannot be open any more.
_TERMINAL_SESSION: frozenset[str] = frozenset({"ended", "failed"})

_E164 = re.compile(E164_PATTERN)


# ---------------------------------------------------------------------- state machine
def advance(
    call: Call, status: CallStatus, *, at: dt.datetime | None = None, reason: str | None = None
) -> bool:
    """Move ``call`` forward to ``status``; a stale or backwards transition is ignored.

    Args:
        call: The row (mutated in place).
        status: Target state.
        at: When it happened (defaults to now).
        reason: ``hangup_reason`` for a terminal state.

    Returns:
        Whether the row changed.
    """
    if call.status in TERMINAL:
        return False
    if status not in TERMINAL and _OPEN_RANK[status] <= _OPEN_RANK.get(call.status, 0):
        return False
    when = at or utcnow()
    call.status = status
    if status == "answered" and call.answered_at is None:
        call.answered_at = when
    if status in TERMINAL:
        if call.ended_at is None:
            call.ended_at = when
        if reason:
            call.hangup_reason = reason[:128]
    if status == "answered":
        _queue_call_event(call, CALL_STARTED)
    elif status in TERMINAL:
        _queue_call_event(call, CALL_ENDED)
    return True


#: Job kind of the outbox row :func:`advance` writes for a call webhook (ask #76).
CALL_EVENT_JOB = "call_event"


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def call_event_data(call: Call) -> dict[str, Any]:
    """The ``data`` of a ``call.started`` / ``call.ended`` webhook (ids, numbers and times only)."""
    return {
        "call_id": call.id,
        "session_id": call.session_id,
        "direction": call.direction,
        "status": call.status,
        "from_e164": call.from_e164,
        "to_e164": call.to_e164,
        "answered_at": _iso(call.answered_at),
        "ended_at": _iso(call.ended_at),
        "hangup_reason": call.hangup_reason,
    }


def _queue_call_event(call: Call, event_type: str) -> None:
    """Add a ``call_event`` job to the call's own session (committed with the transition)."""
    session = object_session(call)
    if session is None:
        return
    session.add(
        Job(
            kind=CALL_EVENT_JOB,
            payload={
                "event_type": event_type,
                "workspace_id": call.workspace_id,
                "data": call_event_data(call),
            },
            status="pending",
            run_at=utcnow(),
        )
    )


@job(CALL_EVENT_JOB)
async def _emit_call_event(ctx: JobContext, payload: dict[str, object]) -> None:
    """Fan a queued ``call.started`` / ``call.ended`` out to the workspace's webhooks."""
    data = payload.get("data")
    await emit(
        ctx.database,
        ctx.jobs,
        workspace_id=str(payload["workspace_id"]),
        event_type=str(payload["event_type"]),
        data=dict(data) if isinstance(data, dict) else {},
    )


def status_for_sip_code(code: int | None) -> CallStatus:
    """Map a final SIP response of a failed dial to a call status."""
    if code in (486, 600):
        return "busy"
    if code in (408, 480, 487, 603):
        return "no_answer"
    return "failed"


def call_out(row: Call) -> CallOut:
    """Project a ``calls`` row onto the contracts model."""
    return CallOut(
        id=row.id,
        session_id=row.session_id,
        workspace_id=row.workspace_id,
        connection_id=row.connection_id or "",
        direction=row.direction,
        from_e164=row.from_e164,
        to_e164=row.to_e164,
        status=row.status,
        sip_call_id=row.sip_call_id,
        lk_participant_identity=row.lk_participant_identity,
        started_at=row.started_at,
        answered_at=row.answered_at,
        ended_at=row.ended_at,
        hangup_reason=row.hangup_reason,
        transfer_to=row.transfer_to,
    )


# ----------------------------------------------------------------------------- reads
async def list_calls(
    db: AsyncSession,
    workspace_id: str,
    *,
    direction: str | None = None,
    status: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Call], int]:
    """One page of the workspace's calls, newest first."""
    filters: list[Any] = [Call.workspace_id == workspace_id]
    if direction:
        filters.append(Call.direction == direction)
    if status:
        filters.append(Call.status == status)
    if session_id:
        filters.append(Call.session_id == session_id)
    total = await db.scalar(select(func.count()).select_from(Call).where(*filters))
    rows = await db.scalars(
        select(Call)
        .where(*filters)
        .order_by(func.coalesce(Call.started_at, Call.answered_at, Call.ended_at).desc(), Call.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(rows), int(total or 0)


async def get_call(db: AsyncSession, workspace_id: str, call_id: str) -> Call:
    """Load a call of the workspace.

    Raises:
        NotFoundError: Unknown in this workspace.
    """
    row: Call | None = await db.scalar(
        select(Call).where(Call.workspace_id == workspace_id, Call.id == call_id)
    )
    if row is None:
        raise NotFoundError(f"unknown call '{call_id}'")
    return row


async def _session_of(db: AsyncSession, call: Call) -> SessionRow:
    session: SessionRow | None = None
    if call.session_id:
        session = await db.scalar(
            select(SessionRow).where(
                SessionRow.workspace_id == call.workspace_id, SessionRow.id == call.session_id
            )
        )
    if session is None:
        raise ConflictError("the call has no session room yet", details={"call_id": call.id})
    return session


async def _live_leg(db: AsyncSession, call: Call) -> tuple[LiveKitConnection, SessionRow]:
    if call.status in TERMINAL:
        raise ConflictError(f"the call is already {call.status}", details={"status": call.status})
    if not call.connection_id:
        raise ConflictError("the call has no connection")
    conn = await connection_of(db, call.workspace_id, call.connection_id)
    require_sip(conn)
    return conn, await _session_of(db, call)


# -------------------------------------------------------------------------- outbound
@dataclass(frozen=True, slots=True)
class DialPlan:
    """Everything the background dial needs; IDs and public values only."""

    call_id: str
    session_id: str
    workspace_id: str
    connection_id: str
    agent_name: str
    room_name: str
    participant_identity: str
    lk_trunk_id: str
    to_e164: str
    from_e164: str
    ring_timeout_s: int
    dispatch_metadata: str


async def _outbound_trunk(
    db: AsyncSession, workspace_id: str, connection_id: str, trunk_id: str | None
) -> SipTrunk:
    if trunk_id:
        trunk = await get_trunk(db, workspace_id, trunk_id)
        if trunk.direction != "outbound":
            raise UnprocessableEntityError("calls are placed through an outbound trunk")
        if trunk.connection_id != connection_id:
            raise UnprocessableEntityError("the trunk belongs to another connection than the agent")
    else:
        trunks = list(
            await db.scalars(
                select(SipTrunk).where(
                    SipTrunk.workspace_id == workspace_id,
                    SipTrunk.connection_id == connection_id,
                    SipTrunk.direction == "outbound",
                )
            )
        )
        if not trunks:
            raise UnprocessableEntityError("the agent's connection has no outbound trunk; add one first")
        if len(trunks) > 1:
            raise UnprocessableEntityError(
                "the agent's connection has several outbound trunks; pass trunk_id",
                details={"trunk_ids": [t.id for t in trunks]},
            )
        trunk = trunks[0]
    if not trunk.lk_trunk_id:
        raise ConflictError(
            f"trunk '{trunk.name}' is not on LiveKit yet; sync it first",
            details={"reason": "trunk_not_synced", "trunk_id": trunk.id},
        )
    return trunk


def check_outbound_number(policy: TelephonyPolicy, to_e164: str) -> str:
    """The stripped callee number, once it is E.164 and the dialing policy allows it.

    Raises:
        UnprocessableEntityError: Not an E.164 number.
        DestinationNotAllowedError: Outside the policy (422 ``destination_not_allowed``).
    """
    number = to_e164.strip()
    if not _E164.match(number):
        raise UnprocessableEntityError("to_e164 must be an E.164 number such as +15551234567")
    check_destination(policy, number)
    return number


async def open_outbound_calls(db: AsyncSession, workspace_id: str) -> int:
    """How many outbound calls of the workspace are still dialing, ringing or answered."""
    total = await db.scalar(
        select(func.count())
        .select_from(Call)
        .where(Call.workspace_id == workspace_id, Call.direction == "outbound", Call.status.in_(OPEN))
    )
    return int(total or 0)


async def prepare_outbound_call(
    db: AsyncSession, workspace_id: str, payload: CallCreate, *, policy: TelephonyPolicy
) -> tuple[Call, DialPlan]:
    """Validate an outbound call and store its session and call rows (not committed).

    The two cap counts here are only race-free under the workspace's and the
    agent's ``lkap_api.limits.slot_lock`` held through the caller's commit
    (R-V2-34); ``POST /v1/calls`` does that.

    Raises:
        NotFoundError: Unknown agent or trunk.
        ConflictError: Archived agent, SIP disabled, or trunk not on LiveKit.
        UnprocessableEntityError: Bad number or no usable outbound trunk.
        DestinationNotAllowedError: The dialing policy refuses the number.
        CallsBusyError: The workspace's open-outbound cap or the agent's
            ``max_concurrent_sessions`` is reached.
    """
    to_e164 = check_outbound_number(policy, payload.to_e164)
    agent = await get_agent(db, workspace_id, payload.agent_id)
    conn = await resolve_agent_connection(db, agent)
    require_sip(conn)
    trunk = await _outbound_trunk(db, workspace_id, conn.id, payload.trunk_id)
    open_calls = await open_outbound_calls(db, workspace_id)
    if open_calls >= policy.max_concurrent_outbound:
        raise CallsBusyError(
            "the workspace already has its maximum of outbound calls in progress; try again shortly",
            details={"max_concurrent_outbound": policy.max_concurrent_outbound, "open": open_calls},
        )
    limits = AgentLimits.model_validate(agent.limits or {})
    if await live_session_count(db, agent) >= limits.max_concurrent_sessions:
        raise CallsBusyError(
            "this agent is at its concurrent session limit; try again shortly",
            details={"max_concurrent_sessions": limits.max_concurrent_sessions},
        )
    ring = max(MIN_RING_S, min(MAX_RING_S, payload.timeout_s))
    from_e164 = (trunk.numbers or [""])[0]
    call_id, session_id = new_id(), new_id()
    room_name = f"lkap-call-{call_id[:12]}"
    identity = f"sip-{call_id[:12]}"
    config = AgentConfig.model_validate(agent.config)
    db.add(
        SessionRow(
            id=session_id,
            workspace_id=workspace_id,
            agent_id=agent.id,
            connection_id=conn.id,
            config_version=agent.config_version,
            room_name=room_name,
            participant_identity=identity,
            participant_name=to_e164,
            status="created",
            pipeline_mode=config.pipeline.mode,
            channel="sip_out",
            caller={
                "direction": "outbound",
                "from": from_e164,
                "to": to_e164,
                "trunk_id": trunk.lk_trunk_id,
                "call_id": "",  # the SIP call id, filled once the dial is answered
                "lkap_call_id": call_id,
            },
            variables=dict(payload.variables) or None,
        )
    )
    await db.flush()
    call = Call(
        id=call_id,
        session_id=session_id,
        workspace_id=workspace_id,
        connection_id=conn.id,
        direction="outbound",
        from_e164=from_e164,
        to_e164=to_e164,
        status="dialing",
        lk_participant_identity=identity,
    )
    db.add(call)
    await db.flush()
    metadata = DispatchMetadata(
        session_id=session_id,
        agent_id=agent.id,
        config_version=agent.config_version,
        participant_identity=identity,
        channel="sip_out",
        connection_id=conn.id,
    ).model_dump_json()
    plan = DialPlan(
        call_id=call_id,
        session_id=session_id,
        workspace_id=workspace_id,
        connection_id=conn.id,
        agent_name=conn.agent_name,
        room_name=room_name,
        participant_identity=identity,
        lk_trunk_id=str(trunk.lk_trunk_id),
        to_e164=to_e164,
        from_e164=from_e164,
        ring_timeout_s=ring,
        dispatch_metadata=metadata,
    )
    log.info(
        "outbound_call_created", call_id=call_id, session_id=session_id, agent_id=agent.id, trunk_id=trunk.id
    )
    return call, plan


def _participant_request(plan: DialPlan) -> CreateSIPParticipantRequest:
    kwargs: dict[str, Any] = {
        "sip_trunk_id": plan.lk_trunk_id,
        "sip_call_to": plan.to_e164,
        "room_name": plan.room_name,
        "participant_identity": plan.participant_identity,
        "participant_name": plan.to_e164,
        "wait_until_answered": True,
        "ringing_timeout": Duration(seconds=plan.ring_timeout_s),
    }
    if plan.from_e164:
        kwargs["sip_number"] = plan.from_e164
    return CreateSIPParticipantRequest(**kwargs)


async def run_dial(database: Database, factory: ConnectionClientFactory, plan: DialPlan) -> None:
    """Dispatch the agent, dial, and record the outcome (never raises).

    Idempotent: a second run for the same call (a retried task) finds
    ``started_at`` set and returns without dialing again.
    """
    async with database.session() as db:
        call = await db.scalar(
            select(Call).where(Call.workspace_id == plan.workspace_id, Call.id == plan.call_id)
        )
        if call is None or call.started_at is not None or call.status != "dialing":
            return
        call.started_at = utcnow()
        conn = await connection_of(db, plan.workspace_id, plan.connection_id)
        await db.commit()

    status: CallStatus = "answered"
    reason: str | None = None
    sip_call_id: str | None = None
    try:
        async with factory.api(conn, timeout_s=plan.ring_timeout_s + DIAL_MARGIN_S, failover=False) as lk:
            await lk.agent_dispatch.create_dispatch(
                CreateAgentDispatchRequest(
                    agent_name=plan.agent_name, room=plan.room_name, metadata=plan.dispatch_metadata
                )
            )
            info = await lk.sip.create_sip_participant(
                _participant_request(plan), timeout=plan.ring_timeout_s + DIAL_MARGIN_S
            )
            sip_call_id = str(info.sip_call_id) or None
    except SipCallError as exc:
        status = status_for_sip_code(exc.sip_status_code)
        reason = exc.sip_status or f"sip {exc.sip_status_code}"
    except Exception as exc:  # noqa: BLE001 - a background task must record, not raise
        status, reason = "failed", f"{type(exc).__name__}: {exc}"[:128]
    if status != "answered":
        await _dismiss_room(factory, conn, plan.room_name)

    async with database.session() as db:
        call = await db.scalar(
            select(Call).where(Call.workspace_id == plan.workspace_id, Call.id == plan.call_id)
        )
        if call is None:
            return
        if sip_call_id and not call.sip_call_id:
            call.sip_call_id = sip_call_id[:128]
        advance(call, status, reason=reason)
        session = await db.scalar(
            select(SessionRow).where(
                SessionRow.workspace_id == plan.workspace_id, SessionRow.id == plan.session_id
            )
        )
        if session is not None and sip_call_id:
            session.caller = {**(session.caller or {}), "call_id": sip_call_id[:128]}
        if status != "answered":
            if session is not None and session.status == "created":
                session.status = "failed"
                session.error = f"call not answered: {call.status}"
                session.ended_at = utcnow()
        await db.commit()
    log.info("outbound_call_dialed", call_id=plan.call_id, status=status, reason=reason)


async def _dismiss_room(factory: ConnectionClientFactory, conn: LiveKitConnection, room_name: str) -> None:
    """Delete the call's room so a dispatched agent leaves (best effort)."""
    try:
        async with factory.api(conn, failover=False) as lk:
            await lk.room.delete_room(DeleteRoomRequest(room=room_name))
    except Exception:  # noqa: BLE001 - best effort; the room empties itself anyway
        log.warning("outbound_call_room_cleanup_failed", room=room_name)


# ------------------------------------------------------------------ call operations
async def hangup(db: AsyncSession, factory: ConnectionClientFactory, call: Call) -> Call:
    """End a call by deleting its room (the SIP leg hangs up, the agent leaves).

    Raises:
        ConflictError: The call already ended, or SIP is disabled.
        LiveKitUpstreamError: LiveKit failed.
    """
    conn, session = await _live_leg(db, call)
    try:
        async with factory.api(conn) as lk:
            await lk.room.delete_room(DeleteRoomRequest(room=session.room_name))
    except Exception as exc:  # noqa: BLE001 - "room already gone" still ends the call
        if not is_not_found(exc):
            raise_upstream(exc, action="hanging up")
    advance(call, "completed" if call.status == "answered" else "failed", reason="hangup")
    await db.flush()
    return call


async def transfer(
    db: AsyncSession,
    factory: ConnectionClientFactory,
    call: Call,
    to: str,
    *,
    policy: TelephonyPolicy,
    identity: str | None = None,
) -> Call:
    """Cold-transfer the call's SIP leg with a SIP REFER.

    Raises:
        DestinationNotAllowedError: The dialing policy refuses ``to`` (nothing is sent).
        ConflictError: The call is not answered, or SIP is disabled.
        LiveKitUpstreamError: The REFER failed (SIP status in ``details``).
    """
    check_destination(policy, to)
    conn, session = await _live_leg(db, call)
    if call.status != "answered":
        raise ConflictError("only an answered call can be transferred", details={"status": call.status})
    participant = identity or call.lk_participant_identity
    if not participant:
        raise ConflictError("the call's SIP participant is not known yet")
    target = to_sip_uri(to)
    try:
        async with factory.api(conn, timeout_s=TRANSFER_TIMEOUT_S, failover=False) as lk:
            result = await lk.sip.transfer_sip_participant(
                TransferSIPParticipantRequest(
                    room_name=session.room_name, participant_identity=participant, transfer_to=target
                ),
                timeout=TRANSFER_TIMEOUT_S,
            )
    except Exception as exc:  # noqa: BLE001 - mapped to an api error
        raise_upstream(exc, action="transferring the call")
    if result.status == SIPTransferStatus.STS_TRANSFER_FAILED:
        why = _transfer_reason(result.reason)
        sip_status = (
            f"{result.sip_status.code} {result.sip_status.status}".strip() if result.sip_status.code else ""
        )
        raise LiveKitUpstreamError(
            f"transfer failed: {sip_status or why}",
            details={"reason": why, "sip_status": sip_status or None, "transfer_id": result.transfer_id},
        )
    call.transfer_to = to[:64]
    advance(call, "transferred", reason="transferred")
    await db.flush()
    log.info("call_transferred", call_id=call.id, transfer_status=int(result.status))
    return call


def _transfer_reason(value: int) -> str:
    """``SIPTransferReason`` as a short lowercase word (``rejected``, ``ringing_timeout``…)."""
    try:
        return SIPTransferReason.Name(value).removeprefix("STR_").lower()
    except ValueError:
        return "unspecified"


async def send_dtmf(db: AsyncSession, factory: ConnectionClientFactory, call: Call, digits: str) -> None:
    """Hand digits to the worker in the call's room; it publishes them as RFC 4733 DTMF.

    The server SDK has no participant RPC, so the api sends a reliable data
    packet on :data:`~lkap_api.telephony.common.DTMF_TOPIC`; the worker only
    honours packets sent by the server (no participant).

    Raises:
        ConflictError: The call is not answered, or SIP is disabled.
    """
    conn, session = await _live_leg(db, call)
    if call.status != "answered":
        raise ConflictError("DTMF needs an answered call", details={"status": call.status})
    payload = json.dumps({"v": 1, "op": "dtmf", "digits": digits, "call_id": call.id}).encode()
    try:
        async with factory.api(conn) as lk:
            await lk.room.send_data(
                SendDataRequest(
                    room=session.room_name, data=payload, kind=DataPacket.Kind.RELIABLE, topic=DTMF_TOPIC
                )
            )
    except Exception as exc:  # noqa: BLE001 - mapped to an api error
        raise_upstream(exc, action="sending DTMF")


# ------------------------------------------------------------------ worker reports
async def call_for_session(db: AsyncSession, session: SessionRow) -> Call | None:
    """The call leg of a SIP session, if one is recorded."""
    row: Call | None = await db.scalar(
        select(Call)
        .where(Call.workspace_id == session.workspace_id, Call.session_id == session.id)
        .order_by(Call.id)
    )
    return row


def new_inbound_call(session: SessionRow, *, identity: str | None, caller: dict[str, Any] | None) -> Call:
    """A ``calls`` row for an inbound SIP session the platform learns about late."""
    info = caller or session.caller or {}
    return Call(
        id=new_id(),
        session_id=session.id,
        workspace_id=session.workspace_id,
        connection_id=session.connection_id,
        direction="inbound",
        from_e164=str(info.get("from") or "")[:32],
        to_e164=str(info.get("to") or "")[:32],
        status="dialing",
        sip_call_id=(str(info.get("call_id") or "") or None),
        lk_participant_identity=identity or session.participant_identity,
        started_at=session.created_at or utcnow(),
    )


async def orphan_call(
    db: AsyncSession, workspace_id: str, connection_id: str | None, sip_call_id: str | None
) -> Call | None:
    """A call recorded from a webhook before its session existed, found by SIP call id.

    Keyed on ``sip.callID``, never on the participant identity: LiveKit's
    default inbound identity is ``sip_<caller number>``, which repeats across
    calls from one phone.
    """
    if not sip_call_id:
        return None
    row: Call | None = await db.scalar(
        select(Call).where(
            Call.workspace_id == workspace_id,
            Call.connection_id == connection_id,
            Call.session_id.is_(None),
            Call.sip_call_id == sip_call_id,
        )
    )
    return row


def link_session(call: Call, session: SessionRow) -> None:
    """Attach an orphan call to its session and give the session its caller."""
    call.session_id = session.id
    if not session.caller:
        session.caller = {
            "direction": call.direction,
            "from": call.from_e164,
            "to": call.to_e164,
            "call_id": call.sip_call_id or "",
        }


async def apply_report(db: AsyncSession, session: SessionRow, report: CallReportIn) -> Call:
    """Record the worker's view of a SIP leg.

    Inbound rows are created here (or an orphan row a webhook recorded first is
    linked), the session's ``caller`` is filled when still empty (asks #55),
    and the reported participant identity replaces the ``sip_in-caller``
    placeholder ``sessions/start`` stores, so a console transfer targets the
    real SIP participant.
    """
    known = dict(session.caller or {})
    caller = {
        "direction": "outbound" if session.channel == "sip_out" else "inbound",
        "from": report.from_e164 or known.get("from", ""),
        "to": report.to_e164 or known.get("to", ""),
        "trunk_id": known.get("trunk_id", ""),
        "call_id": report.sip_call_id or known.get("call_id", ""),
    }
    call = await call_for_session(db, session)
    if call is None:
        call = await orphan_call(db, session.workspace_id, session.connection_id, caller["call_id"] or None)
        if call is not None:
            link_session(call, session)  # fills the caller from what the webhook saw
    if not session.caller and (caller["from"] or caller["to"] or caller["call_id"]):
        session.caller = caller
    if call is None:
        call = new_inbound_call(session, identity=report.participant_identity, caller=caller)
        if report.direction == "outbound" or session.channel == "sip_out":
            call.direction = "outbound"
        db.add(call)
    if report.participant_identity:
        call.lk_participant_identity = report.participant_identity
    if report.sip_call_id and not call.sip_call_id:
        call.sip_call_id = report.sip_call_id
    if report.from_e164 and not call.from_e164:
        call.from_e164 = report.from_e164
    if report.to_e164 and not call.to_e164:
        call.to_e164 = report.to_e164
    if report.status == "completed" and call.status != "answered":
        advance(call, "failed", reason=report.reason or "ended before answer")
    else:
        advance(call, report.status, reason=report.reason)
    await db.flush()
    return call


# --------------------------------------------------------------------------- sweep
async def sweep_stuck_calls(database: Database, *, now: dt.datetime | None = None) -> int:
    """Close call rows nothing else will close (ruling R-V2-24); never calls LiveKit.

    * **Outbound only**: a ``dialing`` / ``ringing`` row older than
      :data:`STUCK_DIAL_AFTER_S` — by ``calls.started_at``, or the session's
      ``created_at`` when the dial task never ran — becomes ``failed``
      (:data:`SWEPT_DIAL_REASON`), and a still ``created`` / ``active`` session
      becomes ``failed`` with it.
    * **Any direction**: an open row whose session is already ``ended`` /
      ``failed`` follows it: ``answered`` → ``completed``, else ``failed``.

    Inbound rows are never ring-swept: ``new_inbound_call`` creates them in
    ``dialing`` and a call in progress must never be failed. A late worker
    report on a swept row is a no-op (:func:`advance` ignores terminal rows).

    Returns:
        How many call rows changed.
    """
    ts = now or utcnow()
    cutoff = ts - dt.timedelta(seconds=STUCK_DIAL_AFTER_S)
    changed = 0
    async with database.session() as db:
        rows = (
            await db.execute(
                select(Call, SessionRow)
                .outerjoin(SessionRow, SessionRow.id == Call.session_id)
                .where(Call.status.in_(OPEN))
                # The sweep runs platform-wide, across every workspace.
                .execution_options(**{CROSS_WORKSPACE_OPTION: True})
            )
        ).all()
        for call, session in rows:
            if session is not None and session.status in _TERMINAL_SESSION:
                status: CallStatus = "completed" if call.status == "answered" else "failed"
                changed += advance(call, status, at=ts, reason=f"session {session.status}")
                continue
            if call.direction != "outbound" or call.status not in ("dialing", "ringing"):
                continue
            born = call.started_at or (session.created_at if session is not None else None)
            if born is None or born > cutoff:
                continue
            changed += advance(call, "failed", at=ts, reason=SWEPT_DIAL_REASON)
            if session is not None and session.status in ("created", "active"):
                session.status = "failed"
                session.error = f"call not answered: {SWEPT_DIAL_REASON}"
                session.ended_at = ts
    if changed:
        log.info("stuck_calls_swept", count=changed)
    return changed
