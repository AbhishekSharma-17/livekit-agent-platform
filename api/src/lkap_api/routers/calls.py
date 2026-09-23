"""Outbound calls, hangup, transfer and DTMF, plus the worker's telephony routes.

Public (CONTRACTS-V2 §3.4): ``POST /v1/calls`` answers ``201`` with the call
in ``dialing`` and dials after the response (:func:`lkap_api.telephony.calls.run_dial`);
``GET /v1/calls``, ``GET /v1/calls/{id}``, ``POST /v1/calls/{id}/hangup``,
``POST /v1/calls/{id}/transfer {to}`` (cold, SIP REFER),
``POST /v1/calls/{id}/dtmf {digits}`` (handed to the worker in the room).
Roles: reads ``viewer`` + ``sessions:read``, writes ``builder`` + ``calls:write``
(``ROUTE_POLICY``).

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
from lkap_contracts.api_models import CallCreate, CallOut, CallPage
from sqlalchemy import select

from lkap_api.connections.clients import ClientFactoryDep
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.deps import AdminCtxDep, DbDep, ServiceDep
from lkap_api.errors import ConflictError, NotFoundError
from lkap_api.telephony import calls as call_service
from lkap_api.telephony import webhooks as _webhooks  # noqa: F401 - registers the webhook handlers
from lkap_api.telephony.common import SIP_CHANNELS
from lkap_api.telephony.models import (
    CallDtmfIn,
    CallDtmfOut,
    CallReportIn,
    CallTransferIn,
    InternalTransferIn,
    InternalTransferOut,
)

_public = APIRouter(prefix="/v1/calls", tags=["calls"])
_internal = APIRouter(prefix="/internal/v1/telephony", tags=["internal"])


def _process_database(request: Request) -> Database:
    """The process-wide `Database`, for work that outlives the request's session."""
    return cast(Database, request.app.state.db)


DatabaseDep = Annotated[Database, Depends(_process_database)]


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
        "`no_answer` or `failed`. 409 `sip_disabled` when the agent's connection has no SIP."
    ),
)
async def place_call(
    payload: CallCreate,
    ctx: AdminCtxDep,
    db: DbDep,
    database: DatabaseDep,
    factory: ClientFactoryDep,
    background: BackgroundTasks,
) -> CallOut:
    """Store the call, commit, and dial in the background."""
    call, plan = await call_service.prepare_outbound_call(db, ctx.workspace_id, payload)
    out = call_service.call_out(call)
    await db.commit()  # the dial task reads these rows on its own session
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
        "the agent leaves. The trunk must allow transfers (e.g. Twilio: enable call transfer)."
    ),
)
async def transfer_call(
    call_id: str, payload: CallTransferIn, ctx: AdminCtxDep, db: DbDep, factory: ClientFactoryDep
) -> CallOut:
    """Transfer."""
    call = await call_service.get_call(db, ctx.workspace_id, call_id)
    return call_service.call_out(await call_service.transfer(db, factory, call, payload.to))


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
    description="The `transfer_call` tool's path to `SipService.transfer_sip_participant`.",
)
async def internal_transfer(
    session_id: str, payload: InternalTransferIn, db: DbDep, factory: ClientFactoryDep, _service: ServiceDep
) -> InternalTransferOut:
    """Transfer the SIP leg of a session; failures are returned, not raised, for the model to read."""
    session = await _sip_session(db, session_id)
    call = await call_service.call_for_session(db, session)
    if call is None:
        report = CallReportIn(
            session_id=session.id, status="answered", participant_identity=payload.participant_identity
        )
        call = await call_service.apply_report(db, session, report)
    try:
        call = await call_service.transfer(
            db, factory, call, payload.to, identity=payload.participant_identity
        )
    except (ConflictError, NotFoundError) as exc:
        return InternalTransferOut(ok=False, status="refused", call_id=call.id, reason=exc.message)
    except Exception as exc:  # noqa: BLE001 - LiveKit/SIP failures go back to the model as text
        return InternalTransferOut(ok=False, status="failed", call_id=call.id, reason=str(exc)[:300])
    return InternalTransferOut(ok=True, status=call.status, call_id=call.id)


router = APIRouter()
router.include_router(_public)
router.include_router(_internal)
