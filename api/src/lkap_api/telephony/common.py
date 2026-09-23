"""Shared helpers: the SIP capability gate, LiveKit error mapping, lookups.

Every LiveKit SIP call the api makes goes through
:meth:`lkap_api.connections.clients.ConnectionClientFactory.api` for the
connection that owns the trunk/call, and is preceded by :func:`require_sip`,
so a self-hosted server without the SIP service (``sip_enabled=false``) is a
409 before anything is sent.
"""

from __future__ import annotations

from typing import NoReturn

from livekit.api import ServerError, SipCallError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.connections.service import capabilities_of, default_connection, get_connection
from lkap_api.db.models import Agent, LiveKitConnection, SipTrunk
from lkap_api.errors import ApiError, ConflictError, NotFoundError, UnprocessableEntityError

#: Data-packet topic the api uses to hand DTMF digits to the worker in a call's
#: room (``RoomService.SendData``). The worker listens on the same literal
#: (``lkap_agent.telephony.DTMF_TOPIC``); only server-sent packets are honoured.
DTMF_TOPIC = "lkap.telephony.dtmf"

#: Channel values of SIP sessions (CONTRACTS-V2 §4.6 ``SessionChannel``).
SIP_CHANNELS: frozenset[str] = frozenset({"sip_in", "sip_out"})


class LiveKitUpstreamError(ApiError):
    """502: LiveKit's SIP service failed or refused the request."""

    status_code = 502
    code = "livekit_error"


def require_sip(connection: LiveKitConnection) -> None:
    """Refuse telephony on a connection whose SIP service is not reachable.

    Raises:
        ConflictError: ``sip_enabled`` is false (never probed, or a self-hosted
            server without the separate SIP service).
    """
    if not capabilities_of(connection).sip_enabled:
        raise ConflictError(
            f"SIP is not enabled on connection '{connection.slug}'. Test the connection, and on a "
            "self-hosted server deploy the LiveKit SIP service first.",
            details={"reason": "sip_disabled", "connection_id": connection.id},
        )


def raise_upstream(exc: Exception, *, action: str) -> NoReturn:
    """Translate a LiveKit client failure into an api error.

    A SIP dialing failure carries the SIP status; a Twirp 4xx (bad argument,
    conflicting rule) is the caller's problem (422); anything else is a 502.

    Raises:
        UnprocessableEntityError: LiveKit rejected the request as invalid.
        LiveKitUpstreamError: Transport failure, 5xx, or a SIP-level failure.
    """
    if isinstance(exc, SipCallError):
        raise LiveKitUpstreamError(
            f"{action} failed: {exc.sip_status or exc.message}",
            details={"sip_status_code": exc.sip_status_code, "sip_status": exc.sip_status},
        ) from exc
    if isinstance(exc, ServerError) and 400 <= exc.status < 500:
        raise UnprocessableEntityError(
            f"LiveKit rejected {action}: {exc.message}", details={"livekit_code": exc.code}
        ) from exc
    raise LiveKitUpstreamError(f"{action} failed: {type(exc).__name__}: {exc}") from exc


def is_not_found(exc: Exception) -> bool:
    """Whether a LiveKit error means the object is already gone (safe to ignore on delete)."""
    return isinstance(exc, ServerError) and (exc.status == 404 or exc.code == "not_found")


async def resolve_connection(
    db: AsyncSession, workspace_id: str, connection_id: str | None
) -> LiveKitConnection:
    """The named connection of the workspace, or its default.

    Raises:
        NotFoundError: An explicit id that does not exist in the workspace.
        UnprocessableEntityError: No id given and the workspace has no default.
    """
    if connection_id:
        return await get_connection(db, workspace_id, connection_id)
    row = await default_connection(db, workspace_id)
    if row is None:
        raise UnprocessableEntityError("the workspace has no default connection; pass connection_id")
    return row


async def connection_of(db: AsyncSession, workspace_id: str, connection_id: str) -> LiveKitConnection:
    """Load a connection by id inside one workspace (for trunks, rules and calls)."""
    return await get_connection(db, workspace_id, connection_id)


async def get_trunk(db: AsyncSession, workspace_id: str, trunk_id: str) -> SipTrunk:
    """Load a trunk of the workspace.

    Raises:
        NotFoundError: Unknown in this workspace (never 403: no existence leak).
    """
    row: SipTrunk | None = await db.scalar(
        select(SipTrunk).where(SipTrunk.workspace_id == workspace_id, SipTrunk.id == trunk_id)
    )
    if row is None:
        raise NotFoundError(f"unknown trunk '{trunk_id}'")
    return row


async def get_agent(db: AsyncSession, workspace_id: str, agent_id: str) -> Agent:
    """Load a live (not archived) agent of the workspace by id or slug.

    Raises:
        NotFoundError: Unknown in this workspace.
        ConflictError: The agent is archived.
    """
    row: Agent | None = await db.scalar(
        select(Agent).where(
            Agent.workspace_id == workspace_id, (Agent.id == agent_id) | (Agent.slug == agent_id)
        )
    )
    if row is None:
        raise NotFoundError(f"unknown agent '{agent_id}'")
    if row.archived_at is not None:
        raise ConflictError(f"agent '{row.slug}' is archived")
    return row


def to_sip_uri(target: str) -> str:
    """The REFER target LiveKit expects: ``tel:+E164`` for a number, a URI unchanged."""
    if target.startswith(("tel:", "sip:", "sips:")):
        return target
    return f"tel:{target}"
