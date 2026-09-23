"""Trunks, dispatch rules and phone numbers, mirrored to LiveKit (ARCHITECTURE-V2 D-V2-14).

The platform row is the source of truth for what the console shows; LiveKit
holds the live object, whose id is stored on the row (``lk_trunk_id``,
``lk_rule_id``). Every write goes to LiveKit **first** and the row changes
only when LiveKit accepted it, so a refused request leaves nothing behind.

Number → agent routing: a phone number with an ``inbound_agent_id`` owns one
*managed* dispatch rule (``numbers == [e164]`` on the number's trunk). The rule
dispatches ``connection.agent_name`` with ``DispatchMetadata{agent_id,
channel: "sip_in", session_id: null, config_version: 0, participant_identity:
""}`` (asks #55), so the worker creates the session with
``POST /internal/v1/sessions/start``.

Protobuf shapes are those of the installed ``livekit-api`` 1.2.1
(``livekit.protocol.sip``): ``SIPInboundTrunkInfo``/``SIPOutboundTrunkInfo``
inside ``Create*TrunkRequest(trunk=…)``, and ``CreateSIPDispatchRuleRequest(
dispatch_rule=SIPDispatchRuleInfo(…))``. On ``SIPDispatchRuleInfo``,
``numbers`` (field 13) is the **called**-number filter ("will only accept a
call made to these numbers") and ``inbound_numbers`` (field 7) the **caller**
filter ("made from these numbers"), per the protocol comments
(``@livekit/protocol`` ``livekit_sip_pb.d.ts``); the flat, deprecated fields of
the request carry no called-number filter, so the nested form is used.
"""

from __future__ import annotations

import json
from typing import Any

from livekit.api import (
    CreateSIPDispatchRuleRequest,
    CreateSIPInboundTrunkRequest,
    CreateSIPOutboundTrunkRequest,
    DeleteSIPDispatchRuleRequest,
    DeleteSIPTrunkRequest,
    ListUpdate,
    RoomAgentDispatch,
    RoomConfiguration,
    SIPDispatchRule,
    SIPDispatchRuleIndividual,
    SIPDispatchRuleInfo,
    SIPInboundTrunkInfo,
    SIPOutboundTrunkInfo,
)
from lkap_contracts.dispatch import DispatchMetadata
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.connections.clients import ConnectionClientFactory
from lkap_api.connections.service import resolve_agent_connection
from lkap_api.db.models import LiveKitConnection, PhoneNumber, SipDispatchRule, SipTrunk, new_id
from lkap_api.errors import ConflictError, NotFoundError, UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.telephony.common import (
    connection_of,
    get_agent,
    get_trunk,
    is_not_found,
    raise_upstream,
    require_sip,
    resolve_connection,
)
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
from lkap_api.vault import Vault

log = get_logger(__name__)

#: Key of the one-key secret bag in ``sip_trunks.auth_password_ct`` (asks #15).
PASSWORD_KEY = "auth_password"

#: Room prefix of the managed rule a number's inbound agent owns.
MANAGED_ROOM_PREFIX = "call-"


# --------------------------------------------------------------------------- output
def trunk_out(row: SipTrunk) -> TrunkOut:
    """Project a trunk row; the password is reduced to ``has_password``."""
    return TrunkOut(
        id=row.id,
        connection_id=row.connection_id,
        direction=row.direction,
        name=row.name,
        lk_trunk_id=row.lk_trunk_id,
        numbers=list(row.numbers or []),
        provider_hint=row.provider_hint,
        address=row.address,
        auth_username=row.auth_username,
        has_password=row.auth_password_ct is not None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def rule_out(row: SipDispatchRule, managed_by_number: str | None = None) -> DispatchRuleOut:
    """Project a dispatch rule row; the PIN is reduced to ``has_pin``."""
    return DispatchRuleOut(
        id=row.id,
        connection_id=row.connection_id,
        lk_rule_id=row.lk_rule_id,
        trunk_id=row.trunk_id,
        agent_id=row.agent_id,
        numbers=list(row.numbers or []),
        room_prefix=row.room_prefix,
        has_pin=bool(row.pin),
        managed_by_number=managed_by_number,
        created_at=row.created_at,
    )


def number_out(row: PhoneNumber, rule: SipDispatchRule | None) -> PhoneNumberOut:
    """Project a phone number with the id of the rule that routes it."""
    return PhoneNumberOut(
        id=row.id,
        e164=row.e164,
        trunk_id=row.trunk_id,
        inbound_agent_id=row.inbound_agent_id,
        label=row.label,
        dispatch_rule_id=rule.id if rule else None,
    )


# --------------------------------------------------------------------------- trunks
def _password(row: SipTrunk, vault: Vault) -> str:
    if row.auth_password_ct is None:
        return ""
    return str(vault.decrypt(row.auth_password_ct).get(PASSWORD_KEY, ""))


def _trunk_metadata(row: SipTrunk) -> str:
    return json.dumps({"lkap_trunk_id": row.id, "workspace_id": row.workspace_id})


def _inbound_info(row: SipTrunk, password: str) -> SIPInboundTrunkInfo:
    kwargs: dict[str, Any] = {
        "name": row.name,
        "metadata": _trunk_metadata(row),
        "numbers": list(row.numbers or []),
    }
    if row.address:
        kwargs["allowed_addresses"] = [row.address]
    if row.auth_username:
        kwargs["auth_username"] = row.auth_username
    if password:
        kwargs["auth_password"] = password
    return SIPInboundTrunkInfo(**kwargs)


def _outbound_info(row: SipTrunk, password: str) -> SIPOutboundTrunkInfo:
    kwargs: dict[str, Any] = {
        "name": row.name,
        "metadata": _trunk_metadata(row),
        "address": row.address or "",
        "numbers": list(row.numbers or []),
    }
    if row.auth_username:
        kwargs["auth_username"] = row.auth_username
    if password:
        kwargs["auth_password"] = password
    return SIPOutboundTrunkInfo(**kwargs)


async def _create_on_livekit(
    factory: ConnectionClientFactory, conn: LiveKitConnection, row: SipTrunk, password: str
) -> str:
    """Create the LiveKit trunk for ``row``; returns its ``sip_trunk_id``."""
    try:
        async with factory.api(conn) as lk:
            if row.direction == "inbound":
                inbound = await lk.sip.create_inbound_trunk(
                    CreateSIPInboundTrunkRequest(trunk=_inbound_info(row, password))
                )
                return str(inbound.sip_trunk_id)
            outbound = await lk.sip.create_outbound_trunk(
                CreateSIPOutboundTrunkRequest(trunk=_outbound_info(row, password))
            )
            return str(outbound.sip_trunk_id)
    except Exception as exc:  # noqa: BLE001 - mapped to an api error
        raise_upstream(exc, action="creating the SIP trunk")


async def _delete_trunk_on_livekit(
    factory: ConnectionClientFactory, conn: LiveKitConnection, lk_trunk_id: str
) -> None:
    try:
        async with factory.api(conn) as lk:
            await lk.sip.delete_trunk(DeleteSIPTrunkRequest(sip_trunk_id=lk_trunk_id))
    except Exception as exc:  # noqa: BLE001 - "already gone" is success
        if not is_not_found(exc):
            raise_upstream(exc, action="deleting the SIP trunk")


def _validate_trunk_shape(direction: str, address: str | None, numbers: list[str]) -> None:
    if direction == "outbound" and not (address or "").strip():
        raise UnprocessableEntityError(
            "an outbound trunk needs the carrier's SIP address (e.g. example.pstn.twilio.com)"
        )
    if direction == "outbound" and not numbers:
        raise UnprocessableEntityError("an outbound trunk needs at least one number to call from")


async def list_trunks(db: AsyncSession, workspace_id: str) -> list[SipTrunk]:
    """Every trunk of the workspace, by name."""
    rows = await db.scalars(
        select(SipTrunk).where(SipTrunk.workspace_id == workspace_id).order_by(SipTrunk.name, SipTrunk.id)
    )
    return list(rows)


async def create_trunk(
    db: AsyncSession, vault: Vault, factory: ConnectionClientFactory, workspace_id: str, payload: TrunkCreate
) -> SipTrunk:
    """Create a trunk on LiveKit, then store it with its ``lk_trunk_id``.

    Raises:
        ConflictError: SIP is not enabled on the connection.
        UnprocessableEntityError: Invalid shape, or LiveKit rejected it.
        LiveKitUpstreamError: LiveKit failed.
    """
    conn = await resolve_connection(db, workspace_id, payload.connection_id)
    require_sip(conn)
    numbers = list(dict.fromkeys(payload.numbers))
    _validate_trunk_shape(payload.direction, payload.address, numbers)
    row = SipTrunk(
        id=new_id(),
        workspace_id=workspace_id,
        connection_id=conn.id,
        direction=payload.direction,
        name=payload.name.strip(),
        numbers=numbers,
        provider_hint=payload.provider_hint,
        address=(payload.address or "").strip() or None,
        auth_username=(payload.auth_username or "").strip() or None,
        auth_password_ct=vault.encrypt({PASSWORD_KEY: payload.auth_password})
        if payload.auth_password
        else None,
    )
    row.lk_trunk_id = await _create_on_livekit(factory, conn, row, payload.auth_password or "")
    db.add(row)
    await db.flush()
    log.info("sip_trunk_created", trunk_id=row.id, direction=row.direction, connection_id=conn.id)
    return row


async def update_trunk(
    db: AsyncSession,
    vault: Vault,
    factory: ConnectionClientFactory,
    workspace_id: str,
    trunk_id: str,
    payload: TrunkUpdate,
) -> SipTrunk:
    """Update a trunk on LiveKit (field update), then the row.

    A trunk that was never mirrored (``lk_trunk_id`` empty) is only stored;
    ``POST …/sync`` creates it on LiveKit.
    """
    row = await get_trunk(db, workspace_id, trunk_id)
    conn = await connection_of(db, workspace_id, row.connection_id)
    fields = payload.model_fields_set
    numbers = list(dict.fromkeys(payload.numbers)) if payload.numbers is not None else list(row.numbers or [])
    address = (payload.address or "").strip() or None if "address" in fields else row.address
    _validate_trunk_shape(row.direction, address, numbers)
    username = (
        (payload.auth_username or "").strip() or None if "auth_username" in fields else row.auth_username
    )
    password_changed = "auth_password" in fields and payload.auth_password is not None

    if row.lk_trunk_id:
        require_sip(conn)
        kwargs: dict[str, Any] = {}
        if payload.name is not None:
            kwargs["name"] = payload.name.strip()
        if payload.numbers is not None:
            kwargs["numbers"] = numbers
        if "auth_username" in fields:
            kwargs["auth_username"] = username or ""
        if password_changed:
            kwargs["auth_password"] = payload.auth_password or ""
        try:
            async with factory.api(conn) as lk:
                if row.direction == "inbound":
                    if "address" in fields:
                        kwargs["allowed_addresses"] = [address] if address else []
                    await lk.sip.update_inbound_trunk_fields(row.lk_trunk_id, **kwargs)
                else:
                    if "address" in fields and address:
                        kwargs["address"] = address
                    await lk.sip.update_outbound_trunk_fields(row.lk_trunk_id, **kwargs)
        except Exception as exc:  # noqa: BLE001 - mapped to an api error
            raise_upstream(exc, action="updating the SIP trunk")

    if payload.name is not None:
        row.name = payload.name.strip()
    row.numbers = numbers
    row.address = address
    row.auth_username = username
    if payload.provider_hint is not None:
        row.provider_hint = payload.provider_hint
    if password_changed:
        row.auth_password_ct = (
            vault.encrypt({PASSWORD_KEY: payload.auth_password}) if payload.auth_password else None
        )
    await db.flush()
    return row


async def delete_trunk(
    db: AsyncSession, factory: ConnectionClientFactory, workspace_id: str, trunk_id: str
) -> None:
    """Delete a trunk, its dispatch rules (LiveKit and rows) and unroute its numbers."""
    row = await get_trunk(db, workspace_id, trunk_id)
    conn = await connection_of(db, workspace_id, row.connection_id)
    rules = await _rules_of_trunk(db, workspace_id, row.id)
    if row.lk_trunk_id or any(rule.lk_rule_id for rule in rules):
        require_sip(conn)
    for rule in rules:
        await _delete_rule_everywhere(db, factory, conn, rule)
    if row.lk_trunk_id:
        await _delete_trunk_on_livekit(factory, conn, row.lk_trunk_id)
    numbers = await db.scalars(
        select(PhoneNumber).where(PhoneNumber.workspace_id == workspace_id, PhoneNumber.trunk_id == row.id)
    )
    for number in numbers:
        number.trunk_id = None
        number.inbound_agent_id = None
    await db.delete(row)
    await db.flush()
    log.info("sip_trunk_deleted", trunk_id=row.id)


async def sync_trunk(
    db: AsyncSession, vault: Vault, factory: ConnectionClientFactory, workspace_id: str, trunk_id: str
) -> SipTrunk:
    """Re-create a trunk on LiveKit and re-point its dispatch rules at the new id.

    Used after the LiveKit object was deleted out of band, after moving
    projects, or for a trunk that was never mirrored.
    """
    row = await get_trunk(db, workspace_id, trunk_id)
    conn = await connection_of(db, workspace_id, row.connection_id)
    require_sip(conn)
    if row.lk_trunk_id:
        await _delete_trunk_on_livekit(factory, conn, row.lk_trunk_id)
    row.lk_trunk_id = await _create_on_livekit(factory, conn, row, _password(row, vault))
    await db.flush()
    for rule in await _rules_of_trunk(db, workspace_id, row.id):
        if rule.lk_rule_id:
            await _delete_rule_on_livekit(factory, conn, rule.lk_rule_id)
        rule.lk_rule_id = await _create_rule_on_livekit(factory, conn, row, rule)
    await db.flush()
    log.info("sip_trunk_synced", trunk_id=row.id)
    return row


# ------------------------------------------------------------------- dispatch rules
async def _rules_of_trunk(db: AsyncSession, workspace_id: str, trunk_id: str) -> list[SipDispatchRule]:
    rows = await db.scalars(
        select(SipDispatchRule).where(
            SipDispatchRule.workspace_id == workspace_id, SipDispatchRule.trunk_id == trunk_id
        )
    )
    return list(rows)


def inbound_dispatch_metadata(agent_id: str, connection_id: str) -> str:
    """The ``RoomAgentDispatch.metadata`` of an inbound rule (ID-only, asks #55)."""
    return DispatchMetadata(
        session_id=None,
        agent_id=agent_id,
        config_version=0,
        participant_identity="",
        channel="sip_in",
        connection_id=connection_id,
    ).model_dump_json()


async def _create_rule_on_livekit(
    factory: ConnectionClientFactory,
    conn: LiveKitConnection,
    trunk: SipTrunk,
    rule: SipDispatchRule,
) -> str:
    if not trunk.lk_trunk_id:
        raise ConflictError(
            f"trunk '{trunk.name}' is not on LiveKit yet; sync it first",
            details={"reason": "trunk_not_synced", "trunk_id": trunk.id},
        )
    info = SIPDispatchRuleInfo(
        rule=SIPDispatchRule(
            dispatch_rule_individual=SIPDispatchRuleIndividual(
                room_prefix=rule.room_prefix, pin=rule.pin or ""
            )
        ),
        trunk_ids=[trunk.lk_trunk_id],
        numbers=list(rule.numbers or []),
        name=f"lkap:{rule.id}",
        metadata=json.dumps({"lkap_rule_id": rule.id, "agent_id": rule.agent_id}),
        room_config=RoomConfiguration(
            agents=[
                RoomAgentDispatch(
                    agent_name=conn.agent_name,
                    metadata=inbound_dispatch_metadata(rule.agent_id, conn.id),
                )
            ]
        ),
    )
    try:
        async with factory.api(conn) as lk:
            created = await lk.sip.create_dispatch_rule(CreateSIPDispatchRuleRequest(dispatch_rule=info))
    except Exception as exc:  # noqa: BLE001 - mapped to an api error
        raise_upstream(exc, action="creating the dispatch rule")
    return str(created.sip_dispatch_rule_id)


async def _delete_rule_on_livekit(
    factory: ConnectionClientFactory, conn: LiveKitConnection, lk_rule_id: str
) -> None:
    try:
        async with factory.api(conn) as lk:
            await lk.sip.delete_dispatch_rule(DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=lk_rule_id))
    except Exception as exc:  # noqa: BLE001 - "already gone" is success
        if not is_not_found(exc):
            raise_upstream(exc, action="deleting the dispatch rule")


async def _delete_rule_everywhere(
    db: AsyncSession, factory: ConnectionClientFactory, conn: LiveKitConnection, rule: SipDispatchRule
) -> None:
    if rule.lk_rule_id:
        await _delete_rule_on_livekit(factory, conn, rule.lk_rule_id)
    await db.delete(rule)


async def list_rules(db: AsyncSession, workspace_id: str) -> list[tuple[SipDispatchRule, str | None]]:
    """Every dispatch rule with the number that manages it (if any)."""
    rules = list(
        await db.scalars(
            select(SipDispatchRule)
            .where(SipDispatchRule.workspace_id == workspace_id)
            .order_by(SipDispatchRule.created_at, SipDispatchRule.id)
        )
    )
    numbers = list(await db.scalars(select(PhoneNumber).where(PhoneNumber.workspace_id == workspace_id)))
    managed = {(n.trunk_id, n.e164): n.e164 for n in numbers if n.inbound_agent_id and n.trunk_id}
    out: list[tuple[SipDispatchRule, str | None]] = []
    for rule in rules:
        nums = list(rule.numbers or [])
        key = (rule.trunk_id, nums[0]) if len(nums) == 1 else None
        out.append((rule, managed.get(key) if key else None))
    return out


async def create_rule(
    db: AsyncSession, factory: ConnectionClientFactory, workspace_id: str, payload: DispatchRuleCreate
) -> SipDispatchRule:
    """Create a dispatch rule (LiveKit first) routing an inbound trunk's calls to an agent.

    Raises:
        UnprocessableEntityError: Outbound trunk, or the agent runs on another connection.
        ConflictError: SIP disabled, or the trunk is not on LiveKit yet.
    """
    trunk = await get_trunk(db, workspace_id, payload.trunk_id)
    agent = await get_agent(db, workspace_id, payload.agent_id)
    return await _new_rule(
        db,
        factory,
        workspace_id,
        trunk,
        agent_id=agent.id,
        agent_connection_id=(await resolve_agent_connection(db, agent)).id,
        numbers=list(dict.fromkeys(payload.numbers)),
        room_prefix=payload.room_prefix,
        pin=payload.pin,
    )


async def _new_rule(
    db: AsyncSession,
    factory: ConnectionClientFactory,
    workspace_id: str,
    trunk: SipTrunk,
    *,
    agent_id: str,
    agent_connection_id: str,
    numbers: list[str],
    room_prefix: str,
    pin: str | None,
) -> SipDispatchRule:
    if trunk.direction != "inbound":
        raise UnprocessableEntityError("dispatch rules route inbound calls; pick an inbound trunk")
    if agent_connection_id != trunk.connection_id:
        raise UnprocessableEntityError(
            "the agent runs on a different connection than the trunk; its worker pool would never "
            "receive these calls",
            details={"agent_connection_id": agent_connection_id, "trunk_connection_id": trunk.connection_id},
        )
    conn = await connection_of(db, workspace_id, trunk.connection_id)
    require_sip(conn)
    rule = SipDispatchRule(
        id=new_id(),
        workspace_id=workspace_id,
        connection_id=conn.id,
        trunk_id=trunk.id,
        numbers=numbers,
        agent_id=agent_id,
        room_prefix=room_prefix,
        pin=pin,
    )
    rule.lk_rule_id = await _create_rule_on_livekit(factory, conn, trunk, rule)
    db.add(rule)
    await db.flush()
    log.info("sip_dispatch_rule_created", rule_id=rule.id, trunk_id=trunk.id, agent_id=agent_id)
    return rule


async def get_rule(db: AsyncSession, workspace_id: str, rule_id: str) -> SipDispatchRule:
    """Load a dispatch rule of the workspace.

    Raises:
        NotFoundError: Unknown in this workspace.
    """
    row: SipDispatchRule | None = await db.scalar(
        select(SipDispatchRule).where(
            SipDispatchRule.workspace_id == workspace_id, SipDispatchRule.id == rule_id
        )
    )
    if row is None:
        raise NotFoundError(f"unknown dispatch rule '{rule_id}'")
    return row


async def delete_rule(
    db: AsyncSession, factory: ConnectionClientFactory, workspace_id: str, rule_id: str
) -> None:
    """Delete a dispatch rule; a number it routed stops receiving inbound calls."""
    rule = await get_rule(db, workspace_id, rule_id)
    conn = await connection_of(db, workspace_id, rule.connection_id)
    if rule.lk_rule_id:
        require_sip(conn)
    for number in await db.scalars(
        select(PhoneNumber).where(
            PhoneNumber.workspace_id == workspace_id, PhoneNumber.trunk_id == rule.trunk_id
        )
    ):
        if list(rule.numbers or []) == [number.e164]:
            number.inbound_agent_id = None
    await _delete_rule_everywhere(db, factory, conn, rule)
    await db.flush()


# -------------------------------------------------------------------------- numbers
async def list_numbers(
    db: AsyncSession, workspace_id: str
) -> list[tuple[PhoneNumber, SipDispatchRule | None]]:
    """Every number of the workspace with its managed rule."""
    numbers = list(
        await db.scalars(
            select(PhoneNumber).where(PhoneNumber.workspace_id == workspace_id).order_by(PhoneNumber.e164)
        )
    )
    rules = list(
        await db.scalars(select(SipDispatchRule).where(SipDispatchRule.workspace_id == workspace_id))
    )
    return [(n, _managed_rule_in(rules, n)) for n in numbers]


def _managed_rule_in(rules: list[SipDispatchRule], number: PhoneNumber) -> SipDispatchRule | None:
    if not number.trunk_id or not number.inbound_agent_id:
        return None
    for rule in rules:
        if rule.trunk_id == number.trunk_id and list(rule.numbers or []) == [number.e164]:
            return rule
    return None


async def managed_rule(db: AsyncSession, workspace_id: str, number: PhoneNumber) -> SipDispatchRule | None:
    """The dispatch rule that routes ``number`` to its inbound agent, if any."""
    if not number.trunk_id:
        return None
    return _managed_rule_in(await _rules_of_trunk(db, workspace_id, number.trunk_id), number)


async def get_number(db: AsyncSession, workspace_id: str, number_id: str) -> PhoneNumber:
    """Load a phone number of the workspace.

    Raises:
        NotFoundError: Unknown in this workspace.
    """
    row: PhoneNumber | None = await db.scalar(
        select(PhoneNumber).where(PhoneNumber.workspace_id == workspace_id, PhoneNumber.id == number_id)
    )
    if row is None:
        raise NotFoundError(f"unknown phone number '{number_id}'")
    return row


async def _ensure_on_trunk(
    db: AsyncSession, factory: ConnectionClientFactory, workspace_id: str, trunk: SipTrunk, e164: str
) -> None:
    """Add ``e164`` to the trunk's numbers (LiveKit first) when it is missing."""
    if e164 in (trunk.numbers or []):
        return
    if trunk.lk_trunk_id:
        conn = await connection_of(db, workspace_id, trunk.connection_id)
        require_sip(conn)
        try:
            async with factory.api(conn) as lk:
                change = ListUpdate(add=[e164])
                if trunk.direction == "inbound":
                    await lk.sip.update_inbound_trunk_fields(trunk.lk_trunk_id, numbers=change)
                else:
                    await lk.sip.update_outbound_trunk_fields(trunk.lk_trunk_id, numbers=change)
        except Exception as exc:  # noqa: BLE001 - mapped to an api error
            raise_upstream(exc, action="adding the number to the trunk")
    trunk.numbers = [*list(trunk.numbers or []), e164]


async def _route_number(
    db: AsyncSession,
    factory: ConnectionClientFactory,
    workspace_id: str,
    number: PhoneNumber,
    *,
    previous: SipDispatchRule | None,
) -> None:
    """Make LiveKit match ``number.inbound_agent_id``: drop the old managed rule, create the new one."""
    if previous is not None:
        conn = await connection_of(db, workspace_id, previous.connection_id)
        await _delete_rule_everywhere(db, factory, conn, previous)
        await db.flush()
    if not number.inbound_agent_id:
        return
    if not number.trunk_id:
        raise UnprocessableEntityError("pick the inbound trunk that receives calls to this number")
    trunk = await get_trunk(db, workspace_id, number.trunk_id)
    agent = await get_agent(db, workspace_id, number.inbound_agent_id)
    await _new_rule(
        db,
        factory,
        workspace_id,
        trunk,
        agent_id=agent.id,
        agent_connection_id=(await resolve_agent_connection(db, agent)).id,
        numbers=[number.e164],
        room_prefix=MANAGED_ROOM_PREFIX,
        pin=None,
    )


async def create_number(
    db: AsyncSession, factory: ConnectionClientFactory, workspace_id: str, payload: PhoneNumberCreate
) -> PhoneNumber:
    """Register a number; bind it to a trunk and route it to an agent when asked.

    Raises:
        ConflictError: The number is already registered (in any workspace).
    """
    taken = await db.scalar(
        select(PhoneNumber.id)
        .where(PhoneNumber.e164 == payload.e164)
        .execution_options(lkap_cross_workspace=True)  # e164 is globally unique
    )
    if taken is not None:
        raise ConflictError(f"{payload.e164} is already registered")
    trunk = await get_trunk(db, workspace_id, payload.trunk_id) if payload.trunk_id else None
    if payload.inbound_agent_id and (trunk is None or trunk.direction != "inbound"):
        raise UnprocessableEntityError("inbound routing needs the number's inbound trunk")
    number = PhoneNumber(
        id=new_id(),
        workspace_id=workspace_id,
        e164=payload.e164,
        trunk_id=trunk.id if trunk else None,
        inbound_agent_id=payload.inbound_agent_id,
        label=payload.label.strip(),
    )
    if trunk is not None:
        await _ensure_on_trunk(db, factory, workspace_id, trunk, payload.e164)
    if number.inbound_agent_id:
        agent = await get_agent(db, workspace_id, number.inbound_agent_id)
        number.inbound_agent_id = agent.id
        await _route_number(db, factory, workspace_id, number, previous=None)
    db.add(number)
    await db.flush()
    return number


async def update_number(
    db: AsyncSession,
    factory: ConnectionClientFactory,
    workspace_id: str,
    number_id: str,
    payload: PhoneNumberUpdate,
) -> PhoneNumber:
    """Change a number's label, trunk or inbound agent; re-route when either of the latter moves."""
    number = await get_number(db, workspace_id, number_id)
    fields = payload.model_fields_set
    previous = await managed_rule(db, workspace_id, number)
    trunk_id = payload.trunk_id if "trunk_id" in fields else number.trunk_id
    agent_id = payload.inbound_agent_id if "inbound_agent_id" in fields else number.inbound_agent_id
    trunk = await get_trunk(db, workspace_id, trunk_id) if trunk_id else None
    if agent_id:
        if trunk is None or trunk.direction != "inbound":
            raise UnprocessableEntityError("inbound routing needs the number's inbound trunk")
        agent_id = (await get_agent(db, workspace_id, agent_id)).id
    if payload.label is not None:
        number.label = payload.label.strip()
    rerouted = trunk_id != number.trunk_id or agent_id != number.inbound_agent_id
    if trunk is not None and trunk_id != number.trunk_id:
        await _ensure_on_trunk(db, factory, workspace_id, trunk, number.e164)
    number.trunk_id = trunk_id
    number.inbound_agent_id = agent_id
    if rerouted or (agent_id and previous is None):
        await _route_number(db, factory, workspace_id, number, previous=previous)
    await db.flush()
    return number


async def delete_number(
    db: AsyncSession, factory: ConnectionClientFactory, workspace_id: str, number_id: str
) -> None:
    """Delete a number and its managed rule. The trunk keeps the number in its list."""
    number = await get_number(db, workspace_id, number_id)
    previous = await managed_rule(db, workspace_id, number)
    if previous is not None:
        conn = await connection_of(db, workspace_id, previous.connection_id)
        await _delete_rule_everywhere(db, factory, conn, previous)
    await db.delete(number)
    await db.flush()
