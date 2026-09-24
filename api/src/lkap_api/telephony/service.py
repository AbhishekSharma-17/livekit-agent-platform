"""Trunks, dispatch rules and phone numbers, mirrored to LiveKit (ARCHITECTURE-V2 D-V2-14).

The platform row is the source of truth for what the console shows; LiveKit
holds the live object, whose id is stored on the row (``lk_trunk_id``,
``lk_rule_id``). Every write goes to LiveKit **first** and the row changes
only when LiveKit accepted it, so a refused request leaves nothing behind.

Number → agent routing: a phone number with an ``inbound_agent_id`` owns one
*managed* dispatch rule (``numbers == [e164]``, linked by
``sip_dispatch_rules.phone_number_id``): on the number's trunk for a
``source="trunk"`` number, trunk-less and attached with ``UpdatePhoneNumber``
for a LiveKit-hosted number (V4-05, PHONE-NUMBERS.md D-V4-17). The rule
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

Telnyx outbound trunks carry ``X-Telnyx-Username: <auth_username>`` in
``SIPOutboundTrunkInfo.headers`` ("include these SIP X-* headers in INVITE
request", ``livekit_sip.proto`` field 9), so Telnyx always answers with a
407 digest challenge. ``headers_to_attributes`` (field 10) maps headers of
the 200 OK *into* participant attributes and is not the field for it.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final, cast

from livekit.api import (
    CreateSIPDispatchRuleRequest,
    CreateSIPInboundTrunkRequest,
    CreateSIPOutboundTrunkRequest,
    DeleteSIPDispatchRuleRequest,
    DeleteSIPTrunkRequest,
    ListSIPDispatchRuleRequest,
    ListUpdate,
    RoomAgentDispatch,
    RoomConfiguration,
    SIPDispatchRule,
    SIPDispatchRuleIndividual,
    SIPDispatchRuleInfo,
    SIPInboundTrunkInfo,
    SIPOutboundTrunkInfo,
)
from lkap_contracts.api_models import (
    AttachState,
    LkInboundStatus,
    LkNumberStatus,
    NumberSource,
    NumbersRefreshOut,
)
from lkap_contracts.dispatch import DispatchMetadata
from lkap_contracts.telephony import E164_PATTERN
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.connections.clients import ConnectionClientFactory
from lkap_api.connections.service import capabilities_of, resolve_agent_connection
from lkap_api.db.models import LiveKitConnection, PhoneNumber, SipDispatchRule, SipTrunk, new_id, utcnow
from lkap_api.errors import ApiError, ConflictError, NotFoundError, UnprocessableEntityError
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
from lkap_api.telephony.phone_numbers import LkPhoneNumber
from lkap_api.vault import Vault

log = get_logger(__name__)

#: Key of the one-key secret bag in ``sip_trunks.auth_password_ct`` (asks #15).
PASSWORD_KEY = "auth_password"

#: Room prefix of the managed rule a number's inbound agent owns.
MANAGED_ROOM_PREFIX = "call-"

#: The header Telnyx needs on every outbound INVITE to challenge with a 407 (LiveKit's Telnyx guide).
TELNYX_USERNAME_HEADER: Final = "X-Telnyx-Username"

E164_RE: Final = re.compile(E164_PATTERN)


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
        phone_number_id=row.phone_number_id,
        agent_id=row.agent_id,
        numbers=list(row.numbers or []),
        room_prefix=row.room_prefix,
        has_pin=bool(row.pin),
        managed_by_number=managed_by_number,
        created_at=row.created_at,
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


def outbound_headers(provider_hint: str, auth_username: str | None) -> dict[str, str]:
    """The SIP X-* headers an outbound trunk sends on every INVITE (Telnyx: the username header)."""
    if provider_hint == "telnyx" and auth_username:
        return {TELNYX_USERNAME_HEADER: auth_username}
    return {}


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
    headers = outbound_headers(row.provider_hint, row.auth_username)
    if headers:
        kwargs["headers"] = headers
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
    provider_hint = payload.provider_hint if payload.provider_hint is not None else row.provider_hint
    headers_changed = row.direction == "outbound" and outbound_headers(
        provider_hint, username
    ) != outbound_headers(row.provider_hint, row.auth_username)

    if row.lk_trunk_id and headers_changed:
        # The field update has no ``headers``; the whole trunk is replaced from the new values.
        require_sip(conn)
        replacement = SipTrunk(
            id=row.id,
            workspace_id=row.workspace_id,
            connection_id=row.connection_id,
            direction=row.direction,
            name=payload.name.strip() if payload.name is not None else row.name,
            numbers=numbers,
            provider_hint=provider_hint,
            address=address,
            auth_username=username,
        )
        password = (payload.auth_password or "") if password_changed else _password(row, vault)
        try:
            async with factory.api(conn) as lk:
                await lk.sip.update_outbound_trunk(row.lk_trunk_id, _outbound_info(replacement, password))
        except Exception as exc:  # noqa: BLE001 - mapped to an api error
            raise_upstream(exc, action="updating the SIP trunk")
    elif row.lk_trunk_id:
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


def _rule_info(conn: LiveKitConnection, trunk: SipTrunk | None, rule: SipDispatchRule) -> SIPDispatchRuleInfo:
    """The LiveKit rule for ``rule``: on ``trunk``, or trunk-less for a LiveKit-hosted number.

    A trunk-less rule (``trunk_ids=[]``) matches **every** trunk of the project, so
    its called-number filter is mandatory (R-V4-13): without it the rule would
    answer the user's own carrier trunks and other apps' traffic.

    Raises:
        ConflictError: The trunk is not on LiveKit yet.
        UnprocessableEntityError: A trunk-less rule without a called number.
    """
    if trunk is not None and not trunk.lk_trunk_id:
        raise ConflictError(
            f"trunk '{trunk.name}' is not on LiveKit yet; sync it first",
            details={"reason": "trunk_not_synced", "trunk_id": trunk.id},
        )
    numbers = list(rule.numbers or [])
    if trunk is None and not numbers:
        raise UnprocessableEntityError(
            "a dispatch rule without a trunk must name the called number; refusing a project-wide catch-all"
        )
    return SIPDispatchRuleInfo(
        rule=SIPDispatchRule(
            dispatch_rule_individual=SIPDispatchRuleIndividual(
                room_prefix=rule.room_prefix, pin=rule.pin or ""
            )
        ),
        trunk_ids=[trunk.lk_trunk_id] if trunk is not None and trunk.lk_trunk_id else [],
        numbers=numbers,
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


async def _create_rule_on_livekit(
    factory: ConnectionClientFactory,
    conn: LiveKitConnection,
    trunk: SipTrunk | None,
    rule: SipDispatchRule,
) -> str:
    info = _rule_info(conn, trunk, rule)
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
    e164_of = {n.id: n.e164 for n in numbers}
    return [(rule, e164_of.get(rule.phone_number_id or "")) for rule in rules]


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
    trunk: SipTrunk | None,
    *,
    agent_id: str,
    agent_connection_id: str,
    numbers: list[str],
    room_prefix: str,
    pin: str | None,
    phone_number: PhoneNumber | None = None,
) -> SipDispatchRule:
    """Create a rule on LiveKit, then store it; ``phone_number`` makes it that number's managed rule.

    ``trunk=None`` builds the trunk-less rule of a LiveKit-hosted number, on the
    number's own connection.
    """
    if trunk is not None:
        if trunk.direction != "inbound":
            raise UnprocessableEntityError("dispatch rules route inbound calls; pick an inbound trunk")
        home_connection_id = trunk.connection_id
    elif phone_number is not None and phone_number.connection_id:
        home_connection_id = phone_number.connection_id
    else:
        raise UnprocessableEntityError("a dispatch rule needs an inbound trunk or a LiveKit-hosted number")
    if agent_connection_id != home_connection_id:
        raise UnprocessableEntityError(
            "the agent runs on a different connection than the "
            + ("trunk" if trunk is not None else "number's LiveKit project")
            + "; its worker pool would never receive these calls",
            details={"agent_connection_id": agent_connection_id, "number_connection_id": home_connection_id},
        )
    conn = await connection_of(db, workspace_id, home_connection_id)
    require_sip(conn)
    rule = SipDispatchRule(
        id=new_id(),
        workspace_id=workspace_id,
        connection_id=conn.id,
        trunk_id=trunk.id if trunk is not None else None,
        numbers=numbers,
        agent_id=agent_id,
        room_prefix=room_prefix,
        pin=pin,
        phone_number_id=phone_number.id if phone_number is not None else None,
    )
    rule.lk_rule_id = await _create_rule_on_livekit(factory, conn, trunk, rule)
    db.add(rule)
    await db.flush()
    log.info(
        "sip_dispatch_rule_created",
        rule_id=rule.id,
        trunk_id=rule.trunk_id,
        phone_number_id=rule.phone_number_id,
        agent_id=agent_id,
    )
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
    number = (
        await db.scalar(
            select(PhoneNumber).where(
                PhoneNumber.workspace_id == workspace_id, PhoneNumber.id == rule.phone_number_id
            )
        )
        if rule.phone_number_id
        else None
    )
    if number is not None and number.source == SOURCE_LIVEKIT:
        await _drop_hosted_rule(db, factory, conn, number, rule)
        await _reread_hosted(factory, conn, number)
    else:
        await _delete_rule_everywhere(db, factory, conn, rule)
    if number is not None:
        number.inbound_agent_id = None
    await db.flush()


# -------------------------------------------------------------------------- numbers
#: ``phone_numbers.source`` values (D-V4-14).
SOURCE_TRUNK = "trunk"
SOURCE_LIVEKIT = "livekit"


def attach_state(row: PhoneNumber, rule: SipDispatchRule | None) -> AttachState:
    """Whether calls to ``row`` reach its inbound agent (PHONE-NUMBERS.md §4.4)."""
    if row.lk_status in ("released", "offline", "pending"):
        return cast(AttachState, row.lk_status)
    if row.source == SOURCE_LIVEKIT:
        if rule is None:
            return "not_routed"
        return "routed" if rule.lk_rule_id and rule.lk_rule_id in (row.lk_rule_ids or []) else "detached"
    return "routed" if rule is not None else "not_routed"


def number_out(
    row: PhoneNumber, rule: SipDispatchRule | None, warnings: list[str] | None = None
) -> PhoneNumberOut:
    """Project a phone number with the rule that routes it and its derived ``attach_state``."""
    return PhoneNumberOut(
        id=row.id,
        e164=row.e164,
        source=cast(NumberSource, row.source or SOURCE_TRUNK),
        trunk_id=row.trunk_id,
        connection_id=row.connection_id,
        inbound_agent_id=row.inbound_agent_id,
        label=row.label,
        dispatch_rule_id=rule.id if rule else None,
        lk_number_id=row.lk_number_id,
        lk_status=cast(LkNumberStatus | None, row.lk_status),
        lk_inbound_status=cast(LkInboundStatus | None, row.lk_inbound_status),
        lk_rule_ids=[str(r) for r in row.lk_rule_ids or []],
        attach_state=attach_state(row, rule),
        region=row.region or "",
        lk_synced_at=row.lk_synced_at,
        warnings=list(warnings or []),
    )


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
    """The rule a number owns: linked by ``phone_number_id`` (D-V4-17), whatever the source."""
    for rule in rules:
        if rule.phone_number_id == number.id:
            return rule
    return None


async def managed_rule(db: AsyncSession, workspace_id: str, number: PhoneNumber) -> SipDispatchRule | None:
    """The dispatch rule that routes ``number`` to its inbound agent, if any."""
    row: SipDispatchRule | None = await db.scalar(
        select(SipDispatchRule)
        .where(SipDispatchRule.workspace_id == workspace_id, SipDispatchRule.phone_number_id == number.id)
        .order_by(SipDispatchRule.created_at, SipDispatchRule.id)
        .limit(1)
    )
    return row


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
    """Make LiveKit match a trunk number's ``inbound_agent_id``: drop the old managed rule, add the new."""
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
        phone_number=number,
    )


async def create_number(
    db: AsyncSession, factory: ConnectionClientFactory, workspace_id: str, payload: PhoneNumberCreate
) -> PhoneNumber:
    """Register a trunk number; bind it to a trunk and route it to an agent when asked.

    A LiveKit-hosted number is never created here (Refresh mirrors it).

    Raises:
        ConflictError: The number is already registered (in any workspace, either source).
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
        source=SOURCE_TRUNK,
        trunk_id=trunk.id if trunk else None,
        inbound_agent_id=payload.inbound_agent_id,
        label=payload.label.strip(),
        lk_rule_ids=[],
        region="",
    )
    if trunk is not None:
        await _ensure_on_trunk(db, factory, workspace_id, trunk, payload.e164)
    db.add(number)
    await db.flush()
    if number.inbound_agent_id:
        agent = await get_agent(db, workspace_id, number.inbound_agent_id)
        number.inbound_agent_id = agent.id
        await _route_number(db, factory, workspace_id, number, previous=None)
    await db.flush()
    return number


async def update_number(
    db: AsyncSession,
    factory: ConnectionClientFactory,
    workspace_id: str,
    number_id: str,
    payload: PhoneNumberUpdate,
) -> tuple[PhoneNumber, list[str]]:
    """Change a number's label, trunk or inbound agent; re-route when either of the latter moves.

    A LiveKit-hosted number has no trunk (``trunk_id`` in the body is a 422) and
    its routing goes through :func:`assign_number` / :func:`unassign_number`.
    Sending the current ``inbound_agent_id`` again re-runs the assignment (the
    console's **Re-attach**).

    Returns:
        The number and the assignment's pre-check warnings (empty for trunk numbers).
    """
    number = await get_number(db, workspace_id, number_id)
    fields = payload.model_fields_set
    if number.source == SOURCE_LIVEKIT:
        return number, await _update_hosted_number(db, factory, workspace_id, number, payload)
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
    return number, []


async def delete_number(
    db: AsyncSession, factory: ConnectionClientFactory, workspace_id: str, number_id: str
) -> None:
    """Delete a number and its managed rule. The trunk keeps the number in its list.

    A LiveKit-hosted number is detached and forgotten here; it stays in the
    LiveKit project (LKAP never gives a number back).
    """
    number = await get_number(db, workspace_id, number_id)
    previous = await managed_rule(db, workspace_id, number)
    if number.source == SOURCE_LIVEKIT:
        if previous is not None:
            conn = await _hosted_connection(db, workspace_id, number)
            await _drop_hosted_rule(db, factory, conn, number, previous)
    elif previous is not None:
        conn = await connection_of(db, workspace_id, previous.connection_id)
        await _delete_rule_everywhere(db, factory, conn, previous)
    await db.flush()
    await db.delete(number)
    await db.flush()


# --------------------------------------------------------------- LiveKit-hosted numbers
def _apply_lk(row: PhoneNumber, lk: LkPhoneNumber) -> bool:
    """Copy LiveKit's view of a hosted number onto its mirror row; returns whether anything changed."""
    values: dict[str, Any] = {
        "lk_status": lk.status,
        "lk_inbound_status": lk.inbound_status,
        "lk_rule_ids": lk.rule_ids,
        "region": lk.display_region,
    }
    changed = False
    for key, value in values.items():
        if getattr(row, key) != value:
            setattr(row, key, value)
            changed = True
    if not row.label and lk.name:
        row.label = lk.name[:200]
        changed = True
    return changed


async def _hosted_connection(db: AsyncSession, workspace_id: str, number: PhoneNumber) -> LiveKitConnection:
    if not number.connection_id or not number.lk_number_id:
        raise ConflictError(
            f"{number.e164} is not linked to a LiveKit project; press Refresh from LiveKit",
            details={"reason": "number_not_mirrored", "number_id": number.id},
        )
    conn = await connection_of(db, workspace_id, number.connection_id)
    require_sip(conn)
    return conn


async def _update_hosted_number(
    db: AsyncSession,
    factory: ConnectionClientFactory,
    workspace_id: str,
    number: PhoneNumber,
    payload: PhoneNumberUpdate,
) -> list[str]:
    fields = payload.model_fields_set
    if "trunk_id" in fields and payload.trunk_id is not None:
        raise UnprocessableEntityError(
            "a LiveKit-hosted number has no trunk; pick its inbound agent instead",
            details={"reason": "hosted_number_has_no_trunk"},
        )
    if payload.label is not None:
        number.label = payload.label.strip()
    warnings: list[str] = []
    if "inbound_agent_id" in fields:
        if payload.inbound_agent_id:
            warnings = await assign_number(db, factory, workspace_id, number, payload.inbound_agent_id)
        elif number.inbound_agent_id or await managed_rule(db, workspace_id, number) is not None:
            await unassign_number(db, factory, workspace_id, number)
    await db.flush()
    return warnings


async def _precheck(factory: ConnectionClientFactory, conn: LiveKitConnection, e164: str) -> list[str]:
    """Warn (never refuse) about project rules that already match ``e164`` (R-V4-13).

    The project may hold other apps' rules LKAP cannot see in its tables; LiveKit
    refuses a new rule whose match set conflicts, and that refusal is relayed.
    """
    try:
        async with factory.api(conn) as lk:
            listed = await lk.sip.list_dispatch_rule(ListSIPDispatchRuleRequest())
    except Exception as exc:  # noqa: BLE001 - a pre-check never blocks the assignment
        log.info("dispatch_rule_precheck_failed", error=type(exc).__name__)
        return ["could not list the project's dispatch rules to check for conflicts"]
    warnings: list[str] = []
    for item in listed.items:
        label = item.name or item.sip_dispatch_rule_id
        if e164 in list(item.numbers):
            warnings.append(f"dispatch rule '{label}' already accepts calls to {e164}")
        elif not list(item.trunk_ids) and not list(item.numbers):
            warnings.append(
                f"dispatch rule '{label}' matches every call in the project; LiveKit may refuse a second rule"
            )
    return warnings


async def _detach(factory: ConnectionClientFactory, conn: LiveKitConnection, number: PhoneNumber) -> None:
    """Ask LiveKit to clear the number's dispatch rule; a refusal of the empty id is tolerated (§1 b)."""
    if not number.lk_number_id:
        return
    try:
        async with factory.phone_numbers(conn) as client:
            await client.update(number.lk_number_id, sip_dispatch_rule_id=None)
    except (UnprocessableEntityError, NotFoundError) as exc:
        log.info("phone_number_detach_refused", number_id=number.id, error=exc.message)


async def _drop_hosted_rule(
    db: AsyncSession,
    factory: ConnectionClientFactory,
    conn: LiveKitConnection,
    number: PhoneNumber,
    rule: SipDispatchRule,
) -> None:
    """Detach the number first, then delete its managed rule on LiveKit and here."""
    await _detach(factory, conn, number)
    await _delete_rule_everywhere(db, factory, conn, rule)
    await db.flush()


async def _reread_hosted(
    factory: ConnectionClientFactory, conn: LiveKitConnection, number: PhoneNumber
) -> None:
    """Store LiveKit's fresh view of the number (best effort: the change itself already happened)."""
    if not number.lk_number_id:
        return
    try:
        async with factory.phone_numbers(conn) as client:
            _apply_lk(number, await client.get(number.lk_number_id))
        number.lk_synced_at = utcnow()
    except ApiError as exc:
        log.info("phone_number_reread_failed", number_id=number.id, error=exc.message)


async def assign_number(
    db: AsyncSession, factory: ConnectionClientFactory, workspace_id: str, number: PhoneNumber, agent_id: str
) -> list[str]:
    """Route a LiveKit-hosted number to an agent: a trunk-less managed rule, attached (D-V4-17).

    Order: the agent must run on the number's connection; the conflict pre-check
    (warnings only); the previous managed rule is detached and deleted; the new
    rule is created with ``numbers=[e164]``; ``UpdatePhoneNumber`` attaches it.
    If LiveKit refuses the rule, nothing is left behind; if it refuses the
    attachment, the new rule is deleted again before the error is relayed.

    Returns:
        The pre-check warnings.

    Raises:
        UnprocessableEntityError: The agent's connection is not the number's, or LiveKit refused.
        ConflictError: SIP disabled, or the number was never mirrored.
    """
    conn = await _hosted_connection(db, workspace_id, number)
    agent = await get_agent(db, workspace_id, agent_id)
    agent_conn = await resolve_agent_connection(db, agent)
    if agent_conn.id != conn.id:
        raise UnprocessableEntityError(
            "the agent's connection is not the number's LiveKit project; its worker pool would never "
            "receive these calls",
            details={"agent_connection_id": agent_conn.id, "number_connection_id": conn.id},
        )
    lk_number_id = number.lk_number_id or ""  # checked by _hosted_connection
    warnings = await _precheck(factory, conn, number.e164)
    previous = await managed_rule(db, workspace_id, number)
    if previous is not None:
        await _drop_hosted_rule(db, factory, conn, number, previous)
    rule = await _new_rule(
        db,
        factory,
        workspace_id,
        None,
        agent_id=agent.id,
        agent_connection_id=agent_conn.id,
        numbers=[number.e164],
        room_prefix=MANAGED_ROOM_PREFIX,
        pin=None,
        phone_number=number,
    )
    try:
        async with factory.phone_numbers(conn) as client:
            attached = await client.update(lk_number_id, sip_dispatch_rule_id=rule.lk_rule_id)
    except ApiError:
        await _delete_rule_everywhere(db, factory, conn, rule)
        await db.flush()
        raise
    _apply_lk(number, attached)
    number.lk_synced_at = utcnow()
    number.inbound_agent_id = agent.id
    await db.flush()
    log.info("phone_number_assigned", number_id=number.id, rule_id=rule.id, warnings=len(warnings))
    return warnings


async def unassign_number(
    db: AsyncSession, factory: ConnectionClientFactory, workspace_id: str, number: PhoneNumber
) -> None:
    """Stop routing a LiveKit-hosted number: detach, delete the managed rule, re-read the number."""
    conn = await _hosted_connection(db, workspace_id, number)
    previous = await managed_rule(db, workspace_id, number)
    if previous is not None:
        await _drop_hosted_rule(db, factory, conn, number, previous)
    else:
        await _detach(factory, conn, number)
    number.inbound_agent_id = None
    await _reread_hosted(factory, conn, number)
    await db.flush()


async def _refresh_targets(
    db: AsyncSession, workspace_id: str, connection_id: str | None
) -> list[LiveKitConnection]:
    if connection_id:
        conn = await resolve_connection(db, workspace_id, connection_id)
        require_sip(conn)
        return [conn]
    rows = list(
        await db.scalars(
            select(LiveKitConnection)
            .where(LiveKitConnection.workspace_id == workspace_id)
            .order_by(LiveKitConnection.is_default.desc(), LiveKitConnection.name, LiveKitConnection.id)
        )
    )
    capable = [row for row in rows if capabilities_of(row).sip_enabled]
    if not capable:
        # The same 409 ``sip_disabled`` as every other telephony write, naming the default.
        require_sip(await resolve_connection(db, workspace_id, None))
    return capable


async def refresh_numbers(
    db: AsyncSession, factory: ConnectionClientFactory, workspace_id: str, connection_id: str | None
) -> list[NumbersRefreshOut]:
    """Mirror the LiveKit-hosted numbers of one (or every SIP-capable) connection (D-V4-15).

    Upserts ``source="livekit"`` rows by ``(workspace_id, lk_number_id)``; a number
    already registered as a trunk number (or in another workspace) is a conflict
    and is skipped; a mirrored number LiveKit no longer lists becomes
    ``lk_status="released"`` (kept, so the console can say what happened). Never
    touches ``inbound_agent_id`` or rules, and never buys or gives back a number.

    Raises:
        ConflictError: 409 ``sip_disabled``.
        PhoneNumbersUnavailableError: 409 ``phone_numbers_unavailable``.
        LiveKitUpstreamError: 502.
    """
    results: list[NumbersRefreshOut] = []
    targets = await _refresh_targets(db, workspace_id, connection_id)
    for conn in targets:
        try:
            async with factory.phone_numbers(conn) as client:
                listed = await client.list()
        except ApiError as exc:
            if len(targets) == 1:
                raise
            # Several projects: one that refuses is reported, the others still refresh.
            results.append(NumbersRefreshOut(connection_id=conn.id, warnings=[f"{exc.code}: {exc.message}"]))
            continue
        results.append(await _mirror(db, workspace_id, conn, listed))
    await db.flush()
    return results


async def _mirror(
    db: AsyncSession, workspace_id: str, conn: LiveKitConnection, listed: list[LkPhoneNumber]
) -> NumbersRefreshOut:
    now = utcnow()
    out = NumbersRefreshOut(connection_id=conn.id, seen=len(listed))
    mirrored = list(
        await db.scalars(
            select(PhoneNumber).where(
                PhoneNumber.workspace_id == workspace_id,
                PhoneNumber.source == SOURCE_LIVEKIT,
                PhoneNumber.connection_id == conn.id,
            )
        )
    )
    by_lk_id = {row.lk_number_id: row for row in mirrored}
    seen: set[str] = set()
    for lk in listed:
        e164 = lk.e164_format.strip()
        if not E164_RE.fullmatch(e164):
            out.warnings.append(f"skipped LiveKit number {lk.id}: '{e164}' is not an E.164 number")
            continue
        seen.add(lk.id)
        row = by_lk_id.get(lk.id)
        if row is None:
            holder = await db.scalar(
                select(PhoneNumber)
                .where(PhoneNumber.e164 == e164)
                .execution_options(lkap_cross_workspace=True)  # e164 is globally unique
            )
            if holder is not None:
                out.conflicts.append(e164)
                continue
            row = PhoneNumber(
                id=new_id(),
                workspace_id=workspace_id,
                e164=e164,
                source=SOURCE_LIVEKIT,
                connection_id=conn.id,
                lk_number_id=lk.id,
                label="",
                lk_rule_ids=[],
                region="",
            )
            _apply_lk(row, lk)
            row.lk_synced_at = now
            db.add(row)
            await db.flush()
            out.added += 1
            continue
        if _apply_lk(row, lk):
            out.updated += 1
        row.lk_synced_at = now
    for row in mirrored:
        if row.lk_number_id not in seen and row.lk_status != "released":
            row.lk_status = "released"
            row.lk_synced_at = now
            out.released += 1
    offline = sum(1 for lk in listed if lk.status == "offline")
    if offline:
        out.warnings.append(f"{offline} number(s) offline in LiveKit")
    if out.conflicts:
        out.warnings.append(
            f"{len(out.conflicts)} number(s) already registered here as trunk numbers (or in another "
            "workspace) and were skipped"
        )
    log.info(
        "phone_numbers_refreshed",
        connection_id=conn.id,
        seen=out.seen,
        added=out.added,
        updated=out.updated,
        released=out.released,
        conflicts=len(out.conflicts),
    )
    return out
