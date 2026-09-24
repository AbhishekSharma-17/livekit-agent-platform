"""SIP trunks, dispatch rules and phone numbers (CONTRACTS-V2 §3.4 "Telephony").

Every write is mirrored to the LiveKit SIP service of the object's connection
(:mod:`lkap_api.telephony.service`): LiveKit first, then the row, so a refused
request leaves nothing behind. A connection with ``sip_enabled=false`` answers
409 ``sip_disabled``.

Scoping and roles follow ``lkap_api.auth.roles.ROUTE_POLICY``: reads need
``viewer`` + ``connections:read``, writes ``admin`` + ``connections:write``
(CONTRACTS-V2 §3.2, "telephony trunks"). Objects of another workspace are 404.

LiveKit-hosted numbers (V4-05, docs/v4/PHONE-NUMBERS.md): ``POST
/numbers/refresh`` mirrors the project's numbers; assigning an inbound agent
creates a trunk-less managed rule and attaches it. The platform never buys or
gives back a number.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from lkap_contracts.api_models import NumbersRefreshIn, NumbersRefreshOut, Page

from lkap_api.auth.audit import record
from lkap_api.connections.clients import ClientFactoryDep
from lkap_api.deps import AdminCtxDep, DbDep, VaultDep
from lkap_api.telephony import service
from lkap_api.telephony.models import (
    DispatchRuleCreate,
    DispatchRuleOut,
    PhoneNumberCreate,
    PhoneNumberOut,
    PhoneNumberUpdate,
    TrunkCreate,
    TrunkOut,
    TrunkUpdate,
)

router = APIRouter(prefix="/v1/telephony", tags=["telephony"])


# --------------------------------------------------------------------------- trunks
@router.get(
    "/trunks",
    response_model=Page[TrunkOut],
    summary="List SIP trunks",
    description=(
        "The workspace's inbound and outbound SIP trunks with their LiveKit ids. "
        "Passwords are never returned."
    ),
)
async def list_trunks(ctx: AdminCtxDep, db: DbDep) -> Page[TrunkOut]:
    """Return every trunk of the workspace."""
    rows = await service.list_trunks(db, ctx.workspace_id)
    return Page[TrunkOut](items=[service.trunk_out(row) for row in rows], total=len(rows))


@router.post(
    "/trunks",
    response_model=TrunkOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a SIP trunk",
    description=(
        "Creates the trunk on the connection's LiveKit SIP service and stores its `lk_trunk_id`. "
        "Outbound trunks need the carrier's SIP `address` and at least one number. "
        "409 `sip_disabled` when the connection has no SIP service."
    ),
)
async def create_trunk(
    payload: TrunkCreate, ctx: AdminCtxDep, db: DbDep, vault: VaultDep, factory: ClientFactoryDep
) -> TrunkOut:
    """Create a trunk (LiveKit first)."""
    row = await service.create_trunk(db, vault, factory, ctx.workspace_id, payload)
    return service.trunk_out(row)


@router.put(
    "/trunks/{trunk_id}",
    response_model=TrunkOut,
    summary="Update a SIP trunk",
    description=(
        "Updates the given fields on LiveKit, then the stored trunk. Omit `auth_password` to keep it."
    ),
)
async def update_trunk(
    trunk_id: str,
    payload: TrunkUpdate,
    ctx: AdminCtxDep,
    db: DbDep,
    vault: VaultDep,
    factory: ClientFactoryDep,
) -> TrunkOut:
    """Update a trunk."""
    row = await service.update_trunk(db, vault, factory, ctx.workspace_id, trunk_id, payload)
    return service.trunk_out(row)


@router.delete(
    "/trunks/{trunk_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a SIP trunk",
    description="Deletes the trunk and its dispatch rules on LiveKit and here; its numbers stop routing.",
)
async def delete_trunk(trunk_id: str, ctx: AdminCtxDep, db: DbDep, factory: ClientFactoryDep) -> Response:
    """Delete a trunk."""
    await service.delete_trunk(db, factory, ctx.workspace_id, trunk_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/trunks/{trunk_id}/sync",
    response_model=TrunkOut,
    summary="Re-create a trunk on LiveKit",
    description=(
        "Deletes the LiveKit trunk (if any) and creates it again from the stored settings, then "
        "re-creates its dispatch rules against the new id."
    ),
)
async def sync_trunk(
    trunk_id: str, ctx: AdminCtxDep, db: DbDep, vault: VaultDep, factory: ClientFactoryDep
) -> TrunkOut:
    """Re-create a trunk and its rules on LiveKit."""
    row = await service.sync_trunk(db, vault, factory, ctx.workspace_id, trunk_id)
    return service.trunk_out(row)


# ------------------------------------------------------------------- dispatch rules
@router.get(
    "/dispatch-rules",
    response_model=Page[DispatchRuleOut],
    summary="List dispatch rules",
    description="Rules that route inbound calls to agents; `managed_by_number` marks a number's own rule.",
)
async def list_rules(ctx: AdminCtxDep, db: DbDep) -> Page[DispatchRuleOut]:
    """Return every dispatch rule of the workspace."""
    rows = await service.list_rules(db, ctx.workspace_id)
    return Page[DispatchRuleOut](items=[service.rule_out(r, m) for r, m in rows], total=len(rows))


@router.post(
    "/dispatch-rules",
    response_model=DispatchRuleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a dispatch rule",
    description=(
        "Routes calls on an inbound trunk (optionally only to `numbers`) to an agent: each call gets "
        "its own room and the agent's worker is dispatched with `channel=sip_in`."
    ),
)
async def create_rule(
    payload: DispatchRuleCreate, ctx: AdminCtxDep, db: DbDep, factory: ClientFactoryDep
) -> DispatchRuleOut:
    """Create a dispatch rule (LiveKit first)."""
    rule = await service.create_rule(db, factory, ctx.workspace_id, payload)
    return service.rule_out(rule)


@router.delete(
    "/dispatch-rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a dispatch rule",
    description="Deletes the rule on LiveKit and here. A number it routed stops receiving calls.",
)
async def delete_rule(rule_id: str, ctx: AdminCtxDep, db: DbDep, factory: ClientFactoryDep) -> Response:
    """Delete a dispatch rule."""
    await service.delete_rule(db, factory, ctx.workspace_id, rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -------------------------------------------------------------------------- numbers
@router.get(
    "/numbers",
    response_model=Page[PhoneNumberOut],
    summary="List phone numbers",
    description=(
        "The workspace's numbers, their trunk (or `source=livekit` for a LiveKit-hosted number), the "
        "agent that answers inbound calls and the derived `attach_state`."
    ),
)
async def list_numbers(ctx: AdminCtxDep, db: DbDep) -> Page[PhoneNumberOut]:
    """Return every phone number of the workspace."""
    rows = await service.list_numbers(db, ctx.workspace_id)
    return Page[PhoneNumberOut](items=[service.number_out(n, r) for n, r in rows], total=len(rows))


@router.post(
    "/numbers",
    response_model=PhoneNumberOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a phone number",
    description=(
        "Registers an E.164 number. With `trunk_id` it is added to the trunk; with "
        "`inbound_agent_id` (inbound trunk) a dispatch rule sends its calls to that agent."
    ),
)
async def create_number(
    payload: PhoneNumberCreate, ctx: AdminCtxDep, db: DbDep, factory: ClientFactoryDep
) -> PhoneNumberOut:
    """Add a number and route it."""
    number = await service.create_number(db, factory, ctx.workspace_id, payload)
    return service.number_out(number, await service.managed_rule(db, ctx.workspace_id, number))


@router.post(
    "/numbers/refresh",
    response_model=list[NumbersRefreshOut],
    summary="Refresh LiveKit-hosted numbers",
    description=(
        "Reads the phone numbers of one connection's LiveKit project (or of every SIP-capable connection) "
        "and mirrors them here as `source=livekit` numbers. Buy numbers in the LiveKit dashboard or with "
        "`lk number purchase`; this call never buys or gives back a number. A number already registered "
        "as a trunk number is reported in `conflicts`. 409 `sip_disabled` when the connection has no SIP "
        "service, 409 `phone_numbers_unavailable` when LiveKit refuses the key."
    ),
)
async def refresh_numbers(
    ctx: AdminCtxDep, db: DbDep, factory: ClientFactoryDep, payload: NumbersRefreshIn | None = None
) -> list[NumbersRefreshOut]:
    """Mirror the project's LiveKit-hosted numbers."""
    body = payload or NumbersRefreshIn()
    results = await service.refresh_numbers(db, factory, ctx.workspace_id, body.connection_id)
    for result in results:
        record(
            db,
            workspace_id=ctx.workspace_id,
            actor_type=ctx.actor.actor_type,
            actor_id=ctx.actor.id,
            action="telephony.numbers.refresh",
            target_type="livekit_connections",
            target_id=result.connection_id,
            payload={
                "seen": result.seen,
                "added": result.added,
                "updated": result.updated,
                "released": result.released,
                "conflicts": len(result.conflicts),
            },
        )
    return results


@router.put(
    "/numbers/{number_id}",
    response_model=PhoneNumberOut,
    summary="Update a phone number",
    description=(
        "Changes the label, trunk or inbound agent; routing follows on LiveKit. For a LiveKit-hosted "
        "number, picking an agent creates a trunk-less dispatch rule for the number and attaches it "
        "(sending the same agent again re-attaches); `trunk_id` is refused. `warnings` lists project "
        "rules that already match the number."
    ),
)
async def update_number(
    number_id: str, payload: PhoneNumberUpdate, ctx: AdminCtxDep, db: DbDep, factory: ClientFactoryDep
) -> PhoneNumberOut:
    """Update a number and re-route it."""
    number, warnings = await service.update_number(db, factory, ctx.workspace_id, number_id, payload)
    return service.number_out(number, await service.managed_rule(db, ctx.workspace_id, number), warnings)


@router.delete(
    "/numbers/{number_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a phone number",
    description=(
        "Removes the number and its dispatch rule. The trunk keeps the number in its list. A "
        "LiveKit-hosted number is detached and forgotten here; it stays in your LiveKit project."
    ),
)
async def delete_number(number_id: str, ctx: AdminCtxDep, db: DbDep, factory: ClientFactoryDep) -> Response:
    """Delete a number."""
    await service.delete_number(db, factory, ctx.workspace_id, number_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
