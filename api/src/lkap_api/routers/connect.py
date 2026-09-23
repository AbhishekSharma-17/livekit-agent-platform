"""Session creation and LiveKit token minting (the browser's only write path).

`POST /v1/agents/{id_or_slug}/connect` creates the `sessions` row and mints a
participant JWT whose `RoomConfiguration` dispatches exactly one agent with
ID-only metadata. A client-supplied `roomConfig` or `agentName` is not part of
`ConnectRequest` and is therefore structurally impossible to honour.

Public-connect protection (CONTRACTS-V2 §3.3, closes REVIEW-FINAL F-07/F-13).
A caller is *privileged* when it is a member of the agent's workspace with at
least ``builder`` (cookie session, or the break-glass admin token; an API key
also needs the ``sessions:write`` scope) — that is console test mode.

* Unpublished or archived agents: privileged callers only.
* Origin: an unprivileged call must come from an origin in the agent's
  ``allowed_origins`` (``["*"]`` = any), taken from ``Origin`` or else
  ``Referer``. The platform's own web origins (``LKAP_CORS_ORIGINS`` +
  ``LKAP_WEB_BASE_URL``) always count, because "empty = console/test only"
  means the platform's own pages. No origin at all is refused unless ``*``.
* Rate limits (``LKAP_RATE_LIMIT_ENABLED``): per client address per agent
  (``rate_per_ip_per_min``, unprivileged calls — privileged calls arrive through
  the console proxy and would all share its address) and per agent
  (``rate_per_agent_per_min``, every call). An empty bucket is a 429
  ``rate_limited``.
* Concurrency: ``created``/``active`` sessions of the agent must stay below
  ``max_concurrent_sessions``, else 429 ``agent_busy``.
* The token is minted on the agent's own connection (V2-03's
  ``mint_session_token``: that connection's key/secret, ``agent_name`` and
  URL) and lives ``max_session_duration_s``.
* ``participant_identity`` is honoured only for privileged callers;
  ``participant_metadata`` must serialise to at most 2 KB.

The same router serves ``GET/PUT /v1/agents/{id}/limits`` (CONTRACTS-V2 §3.4),
scoped to the caller's workspace.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Annotated, cast
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from lkap_contracts.agent_config import AgentLimits
from lkap_contracts.api_models import ConnectRequest, ConnectResponse
from lkap_contracts.common import SessionChannel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import webhooks
from lkap_api.auth.audit import record
from lkap_api.auth.deps import (
    OptionalPrincipalDep,
    Principal,
    WorkspaceContext,
    client_ip,
    member_role_in,
    require,
)
from lkap_api.auth.ratelimit import AgentBusyError, RateLimiterDep, enforce
from lkap_api.auth.roles import role_at_least, scope_allows
from lkap_api.connections.clients import ClientFactoryDep
from lkap_api.connections.service import mint_session_token
from lkap_api.db.models import Agent, new_id
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.deps import DbDep, SettingsDep
from lkap_api.errors import ForbiddenError, NotFoundError, UnprocessableEntityError
from lkap_api.jobs.deps import JobsDep
from lkap_api.limits import live_session_count as live_session_count
from lkap_api.livekit_tokens import new_participant_identity, room_name_for
from lkap_api.logging import get_logger
from lkap_api.routers.agents import agent_config_of, load_agent, to_public
from lkap_api.settings import Settings
from lkap_api.webhooks import events as webhook_events

log = get_logger(__name__)

router = APIRouter(prefix="/v1/agents", tags=["connect"])

#: Upper bound on the JSON size of ``participant_metadata`` (F-13).
MAX_PARTICIPANT_METADATA_BYTES = 2048


def _process_database(request: Request) -> Database:
    """The process-wide `Database` (as opposed to `DbDep`'s request-scoped session).

    `webhooks.emit` opens its own session to write the first `webhook_deliveries`
    row, so it needs this rather than the request's already-open `AsyncSession`
    (same reason `routers/internal.py::put_summary` documents, ask #40).
    """
    return cast(Database, request.app.state.db)


DatabaseDep = Annotated[Database, Depends(_process_database)]


async def is_privileged(db: AsyncSession, principal: Principal | None, agent: Agent) -> bool:
    """Return whether the caller is a ``builder``+ of the agent's workspace.

    API keys additionally need the ``sessions:write`` scope.
    """
    if principal is None:
        return False
    role = await member_role_in(db, principal, agent.workspace_id)
    if not role_at_least(role, "builder"):
        return False
    return principal.kind != "api_key" or scope_allows(principal.scopes, "sessions:write")


def _origin_of(value: str | None) -> str | None:
    if not value or value == "null":
        return None
    parts = urlsplit(value.strip())
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def request_origin(request: Request) -> str | None:
    """The calling page's origin, from ``Origin`` or else ``Referer``."""
    return _origin_of(request.headers.get("origin")) or _origin_of(request.headers.get("referer"))


def origin_allowed(origin: str | None, allowed_origins: list[str], settings: Settings) -> bool:
    """Apply the ``allowed_origins`` rule (see the module docstring)."""
    allowed = {entry.strip().rstrip("/").lower() for entry in allowed_origins if entry.strip()}
    if "*" in allowed:
        return True
    if origin is None:
        return False
    platform = {entry.rstrip("/").lower() for entry in settings.web_origins}
    return origin in allowed or origin in platform


def _check_metadata(metadata: dict[str, str]) -> None:
    size = len(json.dumps(metadata, separators=(",", ":"), ensure_ascii=False).encode())
    if size > MAX_PARTICIPANT_METADATA_BYTES:
        raise UnprocessableEntityError(
            f"participant_metadata is {size} bytes; the limit is {MAX_PARTICIPANT_METADATA_BYTES}",
            details={"limit_bytes": MAX_PARTICIPANT_METADATA_BYTES},
        )


@router.post(
    "/{id_or_slug}/connect",
    response_model=ConnectResponse,
    summary="Start a session",
    description=(
        "Creates a session row and returns LiveKit connection details for the browser. "
        "Public for published agents, subject to the agent's `allowed_origins`, per-IP and "
        "per-agent rate limits and `max_concurrent_sessions` (429 `rate_limited` / `agent_busy`). "
        "Builders of the agent's workspace may also connect to unpublished agents (test mode) "
        "and choose the participant identity. The dispatch and its ID-only metadata are "
        "decided here, never by the client; the token lives `max_session_duration_s`."
    ),
)
async def connect(
    id_or_slug: str,
    payload: ConnectRequest,
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    principal: OptionalPrincipalDep,
    limiter: RateLimiterDep,
    factory: ClientFactoryDep,
    jobs: JobsDep,
    database: DatabaseDep,
    background_tasks: BackgroundTasks,
) -> ConnectResponse:
    """Mint a participant token that dispatches this platform's agent.

    Emits `session.started` once the row is committed (docs/v2/_asks.md
    V2-20-1) — `webhooks.emit` opens its own connection, so it runs after an
    explicit early `db.commit()` for the same "don't deadlock SQLite" reason
    `routers/internal.py::put_summary` documents (ask #40).

    Raises:
        ForbiddenError: Unpublished/archived agent for an unprivileged caller, or
            a disallowed origin.
        RateLimitedError: A rate-limit bucket is empty.
        AgentBusyError: ``max_concurrent_sessions`` reached.
        UnprocessableEntityError: ``participant_metadata`` over 2 KB.
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
            log.info("connect_origin_refused", agent_id=agent.id, origin=origin)
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
    live = await live_session_count(db, agent)
    if live >= limits.max_concurrent_sessions:
        raise AgentBusyError(
            "this agent is at its concurrent session limit; try again shortly",
            details={"max_concurrent_sessions": limits.max_concurrent_sessions},
        )

    config = agent_config_of(agent)
    session_id = new_id()
    room_name = room_name_for(session_id)
    identity = (payload.participant_identity if privileged else None) or new_participant_identity()
    channel: SessionChannel = "test" if privileged else "web"

    minted = await mint_session_token(
        db,
        factory,
        agent,
        session_id=session_id,
        room_name=room_name,
        identity=identity,
        participant_name=payload.participant_name,
        channel=channel,
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
            channel=channel,
        )
    )
    await db.flush()
    log.info(
        "session_created",
        session_id=session_id,
        agent_id=agent.id,
        workspace_id=agent.workspace_id,
        connection_id=minted.connection_id,
        room_name=room_name,
        pipeline_mode=config.pipeline.mode,
        channel=channel,
    )
    workspace_id = agent.workspace_id
    await db.commit()
    await webhooks.emit(
        database,
        jobs,
        workspace_id=workspace_id,
        event_type=webhook_events.SESSION_STARTED,
        data={
            "session_id": session_id,
            "agent_id": agent.id,
            "channel": channel,
            "connection_id": minted.connection_id,
        },
        background_tasks=background_tasks,
    )
    public_agent = to_public(agent, settings)
    return ConnectResponse(
        serverUrl=minted.server_url,
        participantToken=minted.participant_token,
        roomName=room_name,
        participantName=payload.participant_name,
        sessionId=session_id,
        agent=public_agent,
        # Mirrors `panel.panel_id` for one release (R-V2-7), not the raw
        # `agents.ui_panel_id` column — `to_public` already resolved this.
        uiPanelId=public_agent.ui_panel_id,
    )


# ---------------------------------------------------------------------- limits
LimitsReaderDep = Annotated[WorkspaceContext, Depends(require("viewer", "agents:read"))]
LimitsWriterDep = Annotated[WorkspaceContext, Depends(require("builder", "agents:write"))]


async def _scoped_agent(db: AsyncSession, ctx: WorkspaceContext, agent_id: str) -> Agent:
    """Load an agent of the caller's workspace by id or slug (404 for any other workspace)."""
    row = (
        await db.execute(
            select(Agent).where(
                Agent.workspace_id == ctx.workspace_id,
                or_(Agent.id == agent_id, Agent.slug == agent_id),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"unknown agent '{agent_id}'")
    return row


@router.get(
    "/{agent_id}/limits",
    response_model=AgentLimits,
    summary="Get an agent's connect limits",
    description="`AgentLimits` enforced by `connect` (defaults when never set).",
)
async def get_limits(agent_id: str, db: DbDep, ctx: LimitsReaderDep) -> AgentLimits:
    """Return the agent's limits."""
    agent = await _scoped_agent(db, ctx, agent_id)
    return AgentLimits.model_validate(agent.limits or {})


@router.put(
    "/{agent_id}/limits",
    response_model=AgentLimits,
    summary="Set an agent's connect limits",
    description=(
        "Replaces `max_concurrent_sessions`, `max_session_duration_s`, `rate_per_ip_per_min` and "
        "`rate_per_agent_per_min`. Needs `builder`."
    ),
)
async def put_limits(agent_id: str, payload: AgentLimits, db: DbDep, ctx: LimitsWriterDep) -> AgentLimits:
    """Replace the agent's limits.

    Raises:
        UnprocessableEntityError: A limit is below 1.
    """
    values = payload.model_dump()
    if any(int(value) < 1 for value in values.values()):
        raise UnprocessableEntityError("every limit must be at least 1", details={"limits": values})
    agent = await _scoped_agent(db, ctx, agent_id)
    agent.limits = values
    await db.flush()
    record(
        db,
        workspace_id=ctx.workspace_id,
        actor_type=ctx.actor.actor_type,
        actor_id=ctx.actor.id,
        action="agent.limits_update",
        target_type="agents",
        target_id=agent.id,
        payload=values,
    )
    return payload
