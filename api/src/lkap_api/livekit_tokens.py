"""LiveKit access-token minting with explicit agent dispatch.

The api is the only component allowed to decide which agent joins a room:
:func:`mint_participant_token` embeds a ``RoomConfiguration`` with a single
``RoomAgentDispatch`` whose metadata is an ID-only
:class:`~lkap_contracts.dispatch.DispatchMetadata` payload (CONTRACTS §6).
Anything a client sends as ``roomConfig`` is ignored — it never reaches here.
"""

from __future__ import annotations

import datetime as dt
import uuid

from livekit.api import AccessToken, RoomAgentDispatch, RoomConfiguration, VideoGrants
from lkap_contracts.dispatch import DispatchMetadata

#: Participant tokens live long enough for a demo call, short enough to expire.
TOKEN_TTL = dt.timedelta(hours=2)


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
