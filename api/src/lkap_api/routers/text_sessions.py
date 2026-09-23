"""Text-channel sessions for the console **Test chat** drawer and the widget's text mode.

`POST /v1/agents/{id}/text-sessions` mints a session exactly like
`routers.connect.connect`, with the same public-connect protection
(CONTRACTS-V2 §3.3: origin, per-IP/per-agent rate limits, concurrency,
participant-metadata cap) — it reuses `connect`'s helper functions directly
rather than keeping a second copy of that security logic in sync — except it
always mints `channel="text"`. The worker then drops every audio slot before
building anything (`session_builder.prepare_resolved`, V2-07) and accepts the
`rewind`/`inject_user_text` `AgentAction`s over `lkap.agent.action`
(`lkap_agent.text_mode`, this package's worker-side half).

Also serves `GET /v1/agents/{id}/embed-policy`: a small, unauthenticated,
locally-defined response model (not part of `lkap_contracts` — `AgentPublicOut`
carries no `allowed_origins`, and CSP is the one legitimate reason the web app
needs that list before a caller identity exists) that `web/src/middleware.ts`
calls to build the `Content-Security-Policy: frame-ancestors` header for
`/s/[slug]?embed=1`. That header is the browser-side half of the same origin
policy `connect`/`text-sessions` already enforce server-side.
"""

from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Request
from lkap_contracts.agent_config import AgentLimits
from lkap_contracts.api_models import ConnectRequest, ConnectResponse
from pydantic import BaseModel

from lkap_api.auth.deps import OptionalPrincipalDep, client_ip
from lkap_api.auth.ratelimit import RateLimiterDep, enforce
from lkap_api.connections.clients import ClientFactoryDep
from lkap_api.connections.service import mint_session_token
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import new_id
from lkap_api.deps import DbDep, SettingsDep
from lkap_api.errors import ForbiddenError, UnprocessableEntityError
from lkap_api.limits import reserve_session_slot
from lkap_api.livekit_tokens import new_participant_identity, room_name_for
from lkap_api.logging import get_logger
from lkap_api.routers.agents import agent_config_of, load_agent, to_public
from lkap_api.routers.connect import (
    MAX_PARTICIPANT_METADATA_BYTES,
    is_privileged,
    origin_allowed,
    request_origin,
)

log = get_logger(__name__)

router = APIRouter(prefix="/v1/agents", tags=["text-sessions"])


class EmbedPolicy(BaseModel):
    """`GET /v1/agents/{id}/embed-policy` response: the embed CSP's raw ingredient."""

    allowed_origins: list[str]


def _check_metadata(metadata: dict[str, str]) -> None:
    """Same 2 KB cap as `routers.connect._check_metadata` (F-13), kept local: it is
    the one piece of `connect`'s protection this router does not import, since
    ruff's private-member rules would otherwise flag reaching into another
    router module for a leading-underscore helper."""
    size = len(json.dumps(metadata, separators=(",", ":"), ensure_ascii=False).encode())
    if size > MAX_PARTICIPANT_METADATA_BYTES:
        raise UnprocessableEntityError(
            f"participant_metadata is {size} bytes; the limit is {MAX_PARTICIPANT_METADATA_BYTES}",
            details={"limit_bytes": MAX_PARTICIPANT_METADATA_BYTES},
        )


@router.get(
    "/{id_or_slug}/embed-policy",
    response_model=EmbedPolicy,
    summary="An agent's allowed embed origins",
    description=(
        "Published agents only (403 for a draft/archived one, 404 for an unknown id or slug "
        "— matching `GET /v1/agents/{slug}`'s anonymous behaviour). `allowed_origins` verbatim "
        '(CONTRACTS-V2 §3.3, empty = console/test only, `["*"]` = any): the web app\'s '
        "middleware turns this into the `/s/[slug]?embed=1` `Content-Security-Policy: "
        "frame-ancestors` header."
    ),
)
async def embed_policy(id_or_slug: str, db: DbDep) -> EmbedPolicy:
    """Return the published agent's `allowed_origins`.

    Raises:
        NotFoundError: Unknown id or slug (`load_agent`).
        ForbiddenError: An unpublished or archived agent — the same "not published"
            error `connect` raises for an anonymous caller; this route never has a
            caller identity to special-case.
    """
    agent = await load_agent(db, id_or_slug)
    if agent.archived_at is not None or not agent.published:
        raise ForbiddenError(f"agent '{id_or_slug}' is not published")
    return EmbedPolicy(allowed_origins=list(agent.allowed_origins or []))


@router.post(
    "/{id_or_slug}/text-sessions",
    response_model=ConnectResponse,
    summary="Start a text-channel session",
    description=(
        "Same protection as `POST .../connect` (origin, rate limits, concurrency, F-13) but "
        'always mints `channel="text"`: the worker starts with every audio slot dropped and '
        "typed input on, and accepts `rewind`/`inject_user_text` over `lkap.agent.action` "
        "(CONTRACTS-V2 §3.4). Used by the console's Test chat drawer (privileged, unpublished "
        "agents included) and the widget's text mode (public, subject to `allowed_origins`)."
    ),
)
async def start_text_session(
    id_or_slug: str,
    payload: ConnectRequest,
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    principal: OptionalPrincipalDep,
    limiter: RateLimiterDep,
    factory: ClientFactoryDep,
) -> ConnectResponse:
    """Mint a `channel="text"` participant token (see `routers.connect.connect`).

    Raises:
        ForbiddenError: Unpublished/archived agent for an unprivileged caller, or a
            disallowed origin.
        RateLimitedError: A rate-limit bucket is empty.
        AgentBusyError: `max_concurrent_sessions` reached.
        UnprocessableEntityError: `participant_metadata` over 2 KB.
    """
    agent = await load_agent(db, id_or_slug)
    privileged = await is_privileged(db, principal, agent)
    if agent.archived_at is not None and not privileged:
        raise ForbiddenError(f"agent '{id_or_slug}' is archived")
    if not agent.published and not privileged:
        raise ForbiddenError(f"agent '{id_or_slug}' is not published")
    if not privileged:
        origin = request_origin(request)
        if not origin_allowed(origin, list(agent.allowed_origins or []), settings):
            log.info("text_session_origin_refused", agent_id=agent.id, origin=origin)
            raise ForbiddenError(
                "this origin may not start sessions with this agent",
                details={"origin": origin},
            )
    _check_metadata(payload.participant_metadata)

    limits = AgentLimits.model_validate(agent.limits or {})
    if settings.rate_limit_enabled:
        if not privileged:
            await enforce(
                limiter,
                f"connect:ip:{agent.id}:{client_ip(request)}",
                capacity=limits.rate_per_ip_per_min,
                what="connects per minute from this address",
            )
        await enforce(
            limiter,
            f"connect:agent:{agent.id}",
            capacity=limits.rate_per_agent_per_min,
            what="connects per minute for this agent",
        )
    # R-V2-34: count, insert and commit under the agent's slot lock (the commit
    # is explicit so the lock is released only once the row is visible).
    async with reserve_session_slot(db, agent, limits):
        config = agent_config_of(agent)
        session_id = new_id()
        room_name = room_name_for(session_id)
        identity = (payload.participant_identity if privileged else None) or new_participant_identity()

        minted = await mint_session_token(
            db,
            factory,
            agent,
            session_id=session_id,
            room_name=room_name,
            identity=identity,
            participant_name=payload.participant_name,
            channel="text",
            attributes=payload.participant_metadata or None,
            ttl=dt.timedelta(seconds=limits.max_session_duration_s),
        )
        db.add(
            SessionRow(
                id=session_id,
                workspace_id=agent.workspace_id,
                agent_id=agent.id,
                connection_id=minted.connection_id,
                config_version=agent.config_version,
                room_name=room_name,
                participant_identity=identity,
                participant_name=payload.participant_name,
                status="created",
                pipeline_mode=config.pipeline.mode,
                channel="text",
            )
        )
        await db.flush()
        log.info(
            "text_session_created",
            session_id=session_id,
            agent_id=agent.id,
            workspace_id=agent.workspace_id,
            connection_id=minted.connection_id,
            room_name=room_name,
            pipeline_mode=config.pipeline.mode,
        )
        await db.commit()
    public_agent = to_public(agent, settings)
    return ConnectResponse(
        serverUrl=minted.server_url,
        participantToken=minted.participant_token,
        roomName=room_name,
        participantName=payload.participant_name,
        sessionId=session_id,
        agent=public_agent,
        uiPanelId=public_agent.ui_panel_id,
    )
