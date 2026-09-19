"""Session creation and LiveKit token minting (the browser's only write path).

`POST /v1/agents/{id_or_slug}/connect` creates the `sessions` row and mints a
participant JWT whose `RoomConfiguration` dispatches exactly one agent with
ID-only metadata. A client-supplied `roomConfig` or `agentName` is not part of
`ConnectRequest` and is therefore structurally impossible to honour.
"""

from __future__ import annotations

from fastapi import APIRouter
from lkap_contracts.api_models import ConnectRequest, ConnectResponse
from lkap_contracts.dispatch import DispatchMetadata

from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import new_id
from lkap_api.deps import DbDep, OptionalAdminDep, SettingsDep
from lkap_api.errors import ForbiddenError
from lkap_api.livekit_tokens import mint_participant_token, new_participant_identity, room_name_for
from lkap_api.logging import get_logger
from lkap_api.routers.agents import agent_config_of, load_agent, to_public

log = get_logger(__name__)

router = APIRouter(prefix="/v1/agents", tags=["connect"])


@router.post(
    "/{id_or_slug}/connect",
    response_model=ConnectResponse,
    summary="Start a session",
    description=(
        "Creates a session row and returns LiveKit connection details for the browser. "
        "Public for published agents; an admin token also works for unpublished ones. "
        "The agent dispatch and its ID-only metadata are decided here, never by the client."
    ),
)
async def connect(
    id_or_slug: str,
    payload: ConnectRequest,
    db: DbDep,
    settings: SettingsDep,
    admin: OptionalAdminDep,
) -> ConnectResponse:
    """Mint a participant token that dispatches this platform's agent.

    Raises:
        ForbiddenError: If the agent is not published and the caller is not an admin.
    """
    agent = await load_agent(db, id_or_slug)
    if not agent.published and not admin:
        raise ForbiddenError(f"agent '{id_or_slug}' is not published")

    config = agent_config_of(agent)
    session_id = new_id()
    room_name = room_name_for(session_id)
    identity = payload.participant_identity or new_participant_identity()

    db.add(
        SessionRow(
            id=session_id,
            agent_id=agent.id,
            config_version=agent.config_version,
            room_name=room_name,
            participant_identity=identity,
            participant_name=payload.participant_name,
            status="created",
            pipeline_mode=config.pipeline.mode,
        )
    )
    await db.flush()

    token = mint_participant_token(
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
        agent_name=settings.agent_name,
        room_name=room_name,
        identity=identity,
        participant_name=payload.participant_name,
        dispatch=DispatchMetadata(
            session_id=session_id,
            agent_id=agent.id,
            config_version=agent.config_version,
            participant_identity=identity,
        ),
        attributes=payload.participant_metadata or None,
    )
    log.info(
        "session_created",
        session_id=session_id,
        agent_id=agent.id,
        room_name=room_name,
        pipeline_mode=config.pipeline.mode,
    )
    return ConnectResponse(
        serverUrl=settings.livekit_url,
        participantToken=token,
        roomName=room_name,
        participantName=payload.participant_name,
        sessionId=session_id,
        agent=to_public(agent),
        uiPanelId=agent.ui_panel_id,
    )
