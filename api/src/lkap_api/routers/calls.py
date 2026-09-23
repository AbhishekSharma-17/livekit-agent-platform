"""Outbound calls, hangup, transfer and DTMF, plus the worker's telephony routes.

Public (CONTRACTS-V2 §3.4): ``POST /v1/calls`` answers ``201`` with the call
in ``dialing`` and dials after the response (:func:`lkap_api.telephony.calls.run_dial`);
``GET /v1/calls``, ``GET /v1/calls/{id}``, ``POST /v1/calls/{id}/hangup``,
``POST /v1/calls/{id}/transfer {to}`` (cold, SIP REFER),
``POST /v1/calls/{id}/dtmf {digits}`` (handed to the worker in the room).
Roles: reads ``viewer`` + ``sessions:read``, writes ``builder`` + ``calls:write``
(``ROUTE_POLICY``).

Dialing policy (R-V2-23): a dial and every transfer (console or worker) must
pass the workspace's ``settings["telephony"]`` policy (422
``destination_not_allowed``); a dial also takes a token from the workspace's
per-minute call bucket (429 ``rate_limited``, enforced whatever
``LKAP_RATE_LIMIT_ENABLED`` says: it is a toll-fraud control) and respects the
open-outbound and per-agent caps (429 ``calls_busy``). Placed and transferred
calls write ``call.placed`` / ``call.transferred`` audit rows; the worker's
transfer route also records refused and failed attempts.

Internal (service token, ``/internal/v1/telephony``): the worker's
``transfer_call`` tool (ARCHITECTURE-V2 §2.6: worker → api →
``SipService.transfer_sip_participant``) and its call-status reports, which
keep the calls log right when no public webhook url is configured.

``main.py`` includes only :data:`router`, so both halves are declared on their
own routers and merged into it at the bottom of this module. Importing this
module also registers the telephony LiveKit webhook handlers.
"""

from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status
from lkap_contracts.api_models import (
    CallCreate,
    CallDtmfIn,
    CallDtmfOut,
    CallOut,
    CallPage,
    CallReportIn,
    CallTransferIn,
    InternalTransferIn,
    InternalTransferOut,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth.audit import record
from lkap_api.auth.deps import WorkspaceContext
from lkap_api.auth.ratelimit import RateLimitedError, RateLimiterDep, enforce
from lkap_api.connections.clients import ClientFactoryDep
from lkap_api.db.models import Call
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.deps import AdminCtxDep, DbDep, ServiceDep
from lkap_api.errors import ApiError, ConflictError, NotFoundError
from lkap_api.limits import slot_lock
from lkap_api.logging import get_logger
from lkap_api.telephony import calls as call_service
from lkap_api.telephony import webhooks as _webhooks  # noqa: F401 - registers the webhook handlers
from lkap_api.telephony.common import SIP_CHANNELS
from lkap_api.telephony.policy import (
    NOT_ALLOWED_TO_MODEL,
    CallsBusyError,
    DestinationNotAllowedError,
    check_destination,
    workspace_policy,
)

log = get_logger(__name__)

_public = APIRouter(prefix="/v1/calls", tags=["calls"])
_internal = APIRouter(prefix="/internal/v1/telephony", tags=["internal"])


def _process_database(request: Request) -> Database:
    """The process-wide `Database`, for work that outlives the request's session."""
    return cast(Database, request.app.state.db)


DatabaseDep = Annotated[Database, Depends(_process_database)]


def _audit(db: AsyncSession, ctx: WorkspaceContext, action: str, call: Call, **payload: object) -> None:
    """One ``audit_log`` row for a dial or a transfer (ids and numbers only)."""
    record(
        db,
        workspace_id=ctx.workspace_id,
        actor_type=ctx.actor.actor_type,
        actor_id=ctx.actor.id,
        action=action,
        target_type="calls",
        target_id=call.id,
        payload=dict(payload),
    )


#: Refusals that are dialing-policy decisions (R-V2-23) and so leave an audit row.
_POLICY_REFUSALS = (DestinationNotAllowedError, RateLimitedError, CallsBusyError)


async def _audit_refusal(
    db: AsyncSession,
    database: Database,
    ctx: WorkspaceContext,
    action: str,
    exc: ApiError,
    **payload: object,
) -> None:
    """Record a refused dial or transfer in its own transaction (V2-19T-5a, V2-21).

    The request's transaction rolls back with the error, so the row is written
    through a second session. The request session is rolled back **first**:
    SQLite without WAL would otherwise hold its read lock and deadlock the
    second session's commit (asks #40). Nothing is lost by it, because every
    refusal happens before the handler adds a row. Best effort: a failure to
    write the row is logged and never replaces the refusal the caller gets.
    """
    await db.rollback()
    try:
        async with database.session() as audit_db:
            record(
                audit_db,
                workspace_id=ctx.workspace_id,
                actor_type=ctx.actor.actor_type,
                actor_id=ctx.actor.id,
                action=action,
                target_type="calls",
                target_id=None,
                payload={**payload, "code": exc.code, "status": exc.status_code},
            )
    except Exception as audit_exc:  # noqa: BLE001 - the refusal must still reach the caller
        log.warning("call_refusal_audit_failed", action=action, error_type=type(audit_exc).__name__)


# --------------------------------------------------------------------------- public
@_public.post(
    "",
    response_model=CallOut,
    status_code=status.HTTP_201_CREATED,
    summary="Place an outbound call",
    description=(
        "Creates the session (`channel=sip_out`) and the call (`dialing`), answers at once, then "
        "dispatches the agent and dials `to_e164` through the outbound trunk (`trunk_id`, or the "
        "connection's only outbound trunk). Poll `GET /v1/calls/{id}` for `answered`, `busy`, "
        "`no_answer` or `failed`. 409 `sip_disabled` when the agent's connection has no SIP. "
        "The number must pass the workspace's dialing policy (`settings.telephony`): 422 "
        "`destination_not_allowed` (with `details.allowed_prefixes`) otherwise, and every call is refused "
        "until an admin sets `allowed_prefixes`. 429 `rate_limited` past `max_calls_per_min`, "
        "429 `calls_busy` at `max_concurrent_outbound` open calls or the agent's session limit."
    ),
)
async def place_call(
    payload: CallCreate,
    ctx: AdminCtxDep,
    db: DbDep,
    database: DatabaseDep,
    factory: ClientFactoryDep,
    limiter: RateLimiterDep,
    background: BackgroundTasks,
) -> CallOut:
    """Check the policy and caps, store the call, commit, and dial in the background.

    The two caps are counted and the rows committed under the workspace's and
    the agent's slot locks (R-V2-34; workspace outer, agent inner), so a burst
    of dials cannot all read the same count. A refusal is audited after the
    locks are released (``_audit_refusal`` writes through a second session).
    """
    policy = await workspace_policy(db, ctx.workspace_id)
    try:
        call_service.check_outbound_number(policy, payload.to_e164)
        await enforce(
            limiter,
            f"calls:workspace:{ctx.workspace_id}",
            capacity=policy.max_calls_per_min,
            what="outbound calls per minute for this workspace",
        )
        async with (
            slot_lock(db, workspace_id=ctx.workspace_id),
            slot_lock(db, workspace_id=ctx.workspace_id, agent_id=payload.agent_id),
        ):
            call, plan = await call_service.prepare_outbound_call(
                db, ctx.workspace_id, payload, policy=policy
            )
            _audit(
                db,
                ctx,
                "call.placed",
                call,
                agent_id=payload.agent_id,
                session_id=plan.session_id,
                to=plan.to_e164,
            )
            out = call_service.call_out(call)
            await db.commit()  # the dial task reads these rows on its own session
    except _POLICY_REFUSALS as exc:
        await _audit_refusal(
            db, database, ctx, "call.refused", exc, agent_id=payload.agent_id, to=payload.to_e164[:64]
        )
        raise
    background.add_task(call_service.run_dial, database, factory, plan)
    return out


@_public.get(
    "",
    response_model=CallPage,
    summary="List calls",
    description="Inbound and outbound calls of the workspace, newest first.",
)
async def list_calls(
    ctx: AdminCtxDep,
    db: DbDep,
    direction: Literal["inbound", "outbound"] | None = Query(default=None, description="Filter by direction"),
    status_filter: str | None = Query(default=None, alias="status", description="Filter by status"),
    session_id: str | None = Query(default=None, description="The call of one session"),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum rows to return"),
    offset: int = Query(default=0, ge=0, description="Rows to skip"),
) -> CallPage:
    """Return one page of calls."""
    rows, total = await call_service.list_calls(
        db,
        ctx.workspace_id,
        direction=direction,
        status=status_filter,
        session_id=session_id,
        limit=limit,
        offset=offset,
    )
    return CallPage(items=[call_service.call_out(row) for row in rows], total=total)


@_public.get(
    "/{call_id}", response_model=CallOut, summary="Get a call", description="One call and its status."
)
async def get_call(call_id: str, ctx: AdminCtxDep, db: DbDep) -> CallOut:
    """Return one call."""
    return call_service.call_out(await call_service.get_call(db, ctx.workspace_id, call_id))


@_public.post(
    "/{call_id}/hangup",
    response_model=CallOut,
    summary="Hang up a call",
    description="Ends the call by closing its room: the phone leg hangs up and the agent leaves.",
)
async def hangup_call(call_id: str, ctx: AdminCtxDep, db: DbDep, factory: ClientFactoryDep) -> CallOut:
    """Hang up."""
    call = await call_service.get_call(db, ctx.workspace_id, call_id)
    return call_service.call_out(await call_service.hangup(db, factory, call))


@_public.post(
    "/{call_id}/transfer",
    response_model=CallOut,
    summary="Cold-transfer a call",
    description=(
        "Transfers the answered phone leg to `to` (E.164 or a `tel:`/`sip:` URI) with a SIP REFER; "
        "the agent leaves. The trunk must allow transfers (e.g. Twilio: enable call transfer). `to` must "
        "pass the workspace's dialing policy (422 `destination_not_allowed`)."
    ),
)
async def transfer_call(
    call_id: str,
    payload: CallTransferIn,
    ctx: AdminCtxDep,
    db: DbDep,
    database: DatabaseDep,
    factory: ClientFactoryDep,
) -> CallOut:
    """Transfer."""
    call = await call_service.get_call(db, ctx.workspace_id, call_id)
    policy = await workspace_policy(db, ctx.workspace_id)
    try:
        check_destination(policy, payload.to)
    except DestinationNotAllowedError as exc:
        await _audit_refusal(
            db,
            database,
            ctx,
            "call.transfer_refused",
            exc,
            call_id=call_id,
            to=payload.to[:64],
            via="console",
        )
        raise
    call = await call_service.transfer(db, factory, call, payload.to, policy=policy)
    _audit(db, ctx, "call.transferred", call, to=payload.to, via="console")
    return call_service.call_out(call)


@_public.post(
    "/{call_id}/dtmf",
    response_model=CallDtmfOut,
    summary="Send DTMF digits",
    description="The agent's worker plays `digits` into the answered call as RFC 4733 DTMF tones.",
)
async def send_dtmf(
    call_id: str, payload: CallDtmfIn, ctx: AdminCtxDep, db: DbDep, factory: ClientFactoryDep
) -> CallDtmfOut:
    """Queue digits for the worker."""
    call = await call_service.get_call(db, ctx.workspace_id, call_id)
    await call_service.send_dtmf(db, factory, call, payload.digits)
    return CallDtmfOut(call_id=call.id, digits=payload.digits)


# ------------------------------------------------------------------------- internal
async def _sip_session(db: DbDep, session_id: str) -> SessionRow:
    row: SessionRow | None = await db.scalar(
        select(SessionRow)
        .where(SessionRow.id == session_id)
        .execution_options(lkap_cross_workspace=True)  # service-token route: the id is the scope
    )
    if row is None:
        raise NotFoundError(f"unknown session '{session_id}'")
    if row.channel not in SIP_CHANNELS:
        raise ConflictError("the session is not a phone call", details={"channel": row.channel})
    return row


@_internal.post(
    "/calls/report",
    response_model=CallOut,
    summary="Report a SIP leg's status (worker only)",
    description="Creates the inbound call row on first report; status only moves forward.",
)
async def report_call(payload: CallReportIn, db: DbDep, _service: ServiceDep) -> CallOut:
    """Record the worker's view of a call."""
    session = await _sip_session(db, payload.session_id)
    return call_service.call_out(await call_service.apply_report(db, session, payload))


@_internal.post(
    "/sessions/{session_id}/transfer",
    response_model=InternalTransferOut,
    summary="Cold-transfer the session's caller (worker only)",
    description=(
        "The `transfer_call` tool's path to `SipService.transfer_sip_participant`. A destination outside "
        "the workspace's dialing policy is `refused` (reason: destination not allowed by the dialing policy)."
    ),
)
async def internal_transfer(
    session_id: str, payload: InternalTransferIn, db: DbDep, factory: ClientFactoryDep, _service: ServiceDep
) -> InternalTransferOut:
    """Transfer the SIP leg of a session; failures are returned, not raised, for the model to read."""
    session = await _sip_session(db, session_id)
    policy = await workspace_policy(db, session.workspace_id)
    call = await call_service.call_for_session(db, session)
    if call is None:
        report = CallReportIn(
            session_id=session.id, status="answered", participant_identity=payload.participant_identity
        )
        call = await call_service.apply_report(db, session, report)
    out: InternalTransferOut
    try:
        call = await call_service.transfer(
            db, factory, call, payload.to, policy=policy, identity=payload.participant_identity
        )
        out = InternalTransferOut(ok=True, status=call.status, call_id=call.id)
    except DestinationNotAllowedError:
        out = InternalTransferOut(ok=False, status="refused", call_id=call.id, reason=NOT_ALLOWED_TO_MODEL)
    except (ConflictError, NotFoundError) as exc:
        out = InternalTransferOut(ok=False, status="refused", call_id=call.id, reason=exc.message)
    except Exception as exc:  # noqa: BLE001 - LiveKit/SIP failures go back to the model as text
        out = InternalTransferOut(ok=False, status="failed", call_id=call.id, reason=str(exc)[:300])
    record(
        db,
        workspace_id=session.workspace_id,
        actor_type="system",
        actor_id=None,
        action="call.transferred" if out.ok else f"call.transfer_{out.status}",
        target_type="calls",
        target_id=call.id,
        payload={
            "to": payload.to,
            "via": "transfer_call tool",
            "session_id": session.id,
            "status": out.status,
        },
    )
    return out


router = APIRouter()
router.include_router(_public)
router.include_router(_internal)
