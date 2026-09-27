"""LiveKit access-token minting with explicit agent dispatch.

The api is the only component allowed to decide which agent joins a room:
:func:`mint_participant_token` embeds a ``RoomConfiguration`` with a single
``RoomAgentDispatch`` whose metadata is an ID-only
:class:`~lkap_contracts.dispatch.DispatchMetadata` payload (CONTRACTS §6).
Anything a client sends as ``roomConfig`` is ignored — it never reaches here.

V5-37 adds :func:`mint_listener_token`: a hidden, subscribe-only token for a
supervisor listening in to one live room, with no dispatch at all.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping

from livekit.api import AccessToken, RoomAgentDispatch, RoomConfiguration, VideoGrants
from lkap_contracts.api_models import (
    LISTEN_TOKEN_TTL_S,
    PARTICIPANT_ROLE_ATTRIBUTE,
    SUPERVISOR_IDENTITY_PREFIX,
)
from lkap_contracts.common import CALLER_TIMEZONE_ATTRIBUTE, is_iana_timezone
from lkap_contracts.dispatch import DispatchMetadata

#: Participant tokens live long enough for a demo call, short enough to expire.
TOKEN_TTL = dt.timedelta(hours=2)

#: A supervisor's listen token lives 15 minutes (V5-37); the console asks for a new one.
LISTEN_TOKEN_TTL = dt.timedelta(seconds=LISTEN_TOKEN_TTL_S)

#: Participant attributes the platform sets itself; a client never supplies one (R-V5-10).
RESERVED_ATTRIBUTE_PREFIX = "lkap."
#: Every attribute prefix a client may not set (S5-25): the platform's own, LiveKit's
#: (``lk.publish_on_behalf``, ``lk.agent.*``) and SIP's (``sip.phoneNumber``, ``sip.callID``).
RESERVED_ATTRIBUTE_PREFIXES: tuple[str, ...] = (RESERVED_ATTRIBUTE_PREFIX, "lk.", "sip.")

#: The ``participant_metadata`` key carrying the browser's IANA timezone (R-V5-10).
TIMEZONE_METADATA_KEY = "timezone"


def participant_attributes(
    metadata: Mapping[str, str], *, timezone: str | None = None
) -> dict[str, str] | None:
    """The caller's token attributes from ``participant_metadata`` (R-V5-10).

    Every ``lkap.*``, ``lk.*`` and ``sip.*`` key the client sent is dropped, so the
    worker can trust the platform's, LiveKit's and SIP's own (S5-25). The caller's
    timezone (``timezone``, else ``metadata["timezone"]``) is kept only when it is an IANA name, as
    ``lkap.tz``; the raw ``timezone`` key is never forwarded, and an unknown name
    is dropped without an error.

    Args:
        metadata: ``ConnectRequest.participant_metadata`` (already size-checked).
        timezone: ``TextSessionCreate.timezone``, which wins when valid.

    Returns:
        The attributes, or ``None`` when nothing is left.
    """
    attributes = {
        key: value
        for key, value in metadata.items()
        if not key.lower().startswith(RESERVED_ATTRIBUTE_PREFIXES)
    }
    from_metadata = attributes.pop(TIMEZONE_METADATA_KEY, None)
    zone = next((name for name in (timezone, from_metadata) if is_iana_timezone(name)), None)
    if zone is not None:
        attributes[CALLER_TIMEZONE_ATTRIBUTE] = zone
    return attributes or None


def room_name_for(session_id: str) -> str:
    """Return the deterministic LiveKit room name for a session id."""
    return f"lkap-{session_id[:8]}"


def new_participant_identity() -> str:
    """Return a fresh, unguessable participant identity."""
    return f"user-{uuid.uuid4().hex[:8]}"


def mint_participant_token(
    *,
    api_key: str,
    api_secret: str,
    agent_name: str,
    room_name: str,
    identity: str,
    participant_name: str,
    dispatch: DispatchMetadata,
    attributes: dict[str, str] | None = None,
    ttl: dt.timedelta = TOKEN_TTL,
) -> str:
    """Mint a browser participant JWT that dispatches exactly one agent.

    Args:
        api_key: ``LIVEKIT_API_KEY``.
        api_secret: ``LIVEKIT_API_SECRET``.
        agent_name: The dispatch target, i.e. ``LKAP_AGENT_NAME`` (``lkap-agent``).
        room_name: Room the participant may join.
        identity: Participant identity (also referenced by the dispatch metadata).
        participant_name: Display name shown to other participants.
        dispatch: ID-only metadata handed to the worker as ``ctx.job.metadata``.
        attributes: Optional participant attributes (public, never secret).
        ttl: Token lifetime.

    Returns:
        A signed JWT for ``room.connect``.
    """
    grants = VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )
    room_config = RoomConfiguration(
        agents=[RoomAgentDispatch(agent_name=agent_name, metadata=dispatch.model_dump_json())]
    )
    token = (
        AccessToken(api_key=api_key, api_secret=api_secret)
        .with_identity(identity)
        .with_name(participant_name)
        .with_grants(grants)
        .with_room_config(room_config)
        .with_ttl(ttl)
    )
    if attributes:
        token = token.with_attributes(attributes)
    return token.to_jwt()


def supervisor_identity(actor_id: str) -> str:
    """The LiveKit identity of a listening supervisor (``supervisor:<user or API key id>``)."""
    return f"{SUPERVISOR_IDENTITY_PREFIX}{actor_id}"


def listener_grants(room_name: str) -> VideoGrants:
    """The grants of a listen token (V5-37): join one room, hidden, subscribe only.

    ``can_publish`` and ``can_publish_data`` are off (a whisper goes through the api, and a
    hidden participant cannot call RPCs anyway), ``can_publish_sources`` is empty,
    ``room_create`` is off (an ended room is never recreated by a listener) and the
    participant may not change its own metadata or attributes.
    """
    return VideoGrants(
        room_join=True,
        room=room_name,
        room_create=False,
        hidden=True,
        can_subscribe=True,
        can_publish=False,
        can_publish_data=False,
        can_publish_sources=[],
        can_update_own_metadata=False,
    )


def mint_listener_token(
    *,
    api_key: str,
    api_secret: str,
    room_name: str,
    identity: str,
    participant_name: str,
    ttl: dt.timedelta = LISTEN_TOKEN_TTL,
) -> str:
    """Mint a hidden, listen-only JWT for one room (V5-37).

    Unlike :func:`mint_participant_token` it carries no ``RoomConfiguration``, so joining
    never dispatches an agent. The attribute ``lkap.role=supervisor`` is stamped here; a
    client can never set an ``lkap.*`` attribute itself.

    Args:
        api_key: The connection's LiveKit API key.
        api_secret: The connection's LiveKit API secret.
        room_name: The only room the token may join.
        identity: :func:`supervisor_identity` of the caller.
        participant_name: Display name (never shown to the room: the participant is hidden).
        ttl: Token lifetime (:data:`LISTEN_TOKEN_TTL`).

    Returns:
        The signed JWT.
    """
    return (
        AccessToken(api_key=api_key, api_secret=api_secret)
        .with_identity(identity)
        .with_name(participant_name)
        .with_grants(listener_grants(room_name))
        .with_attributes({PARTICIPANT_ROLE_ATTRIBUTE: "supervisor"})
        .with_ttl(ttl)
        .to_jwt()
    )
