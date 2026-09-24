"""Worker-only endpoints, guarded by ``X-Service-Token``.

This is the single place decrypted credentials leave the api, and only towards
the LiveKit worker and the supervisor: :func:`resolved_config` and
:func:`start_session` return a
:class:`~lkap_contracts.agent_config.ResolvedAgentConfig` containing provider
kwargs and tool templates with real secret values, and
:func:`connection_worker_env` returns a connection's decrypted LiveKit key and
secret. Nothing in this module logs a resolved payload or an environment —
only ids, counts and slot names.

Route ownership (CONTRACTS-V2 §3.4): ``sessions/*``, ``sessions/start`` and
``connections/{id}/worker-env`` live here (V2-03); ``workers/register``,
``workers/{key}/heartbeat`` and ``fleet/desired`` live in
:mod:`lkap_api.routers.fleet_internal` (V2-04).
"""

from __future__ import annotations

import datetime as dt
import importlib
from collections.abc import Awaitable, Callable
from typing import Annotated, cast

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from lkap_contracts.agent_config import AgentConfig, ProviderRef, ResolvedAgentConfig, effective_qa
from lkap_contracts.api_models import (
    RecordingStartOut,
    SessionEventsIn,
    SessionMetricsIn,
    SessionRecordingIn,
    SessionStartIn,
    SessionSummaryIn,
)
from lkap_contracts.connections import ConnectionInfo, DeploymentType
from lkap_contracts.fleet import WorkerEnv
from lkap_contracts.qa import SessionQaIn
from lkap_contracts.tools import ToolDefinition
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import webhooks
from lkap_api.config_service import (
    SLOT_KIND,
    installed_provider_ids,
    resolve_providers,
    resolve_tool_definition,
)
from lkap_api.connections.bundle import worker_env
from lkap_api.connections.clients import ClientFactoryDep, ConnectionClientFactory
from lkap_api.connections.service import (
    capabilities_of,
    get_connection_by_id,
    resolve_agent_connection,
)
from lkap_api.costs import cost_session
from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Agent, Credential, LiveKitConnection, SessionEvent, SessionQa, Tool, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.deps import DbDep, ServiceDep, SettingsDep, VaultDep
from lkap_api.errors import ApiError, ConflictError, NotFoundError
from lkap_api.jobs.deps import JobsDep
from lkap_api.logging import get_logger
from lkap_api.packs import get_manifest
from lkap_api.panels import effective_layout
from lkap_api.recordings.finalize import apply_egress_result, schedule_finalize_once
from lkap_api.settings import Settings
from lkap_api.vault import Vault
from lkap_api.webhooks import events as webhook_events

log = get_logger(__name__)

router = APIRouter(prefix="/internal/v1", tags=["internal"])


def _process_database(request: Request) -> Database:
    """The process-wide `Database` (as opposed to `DbDep`'s request-scoped session).

    `webhooks.emit` opens its own session to write the first `webhook_deliveries`
    row, so it needs the `Database`, not this request's already-open
    `AsyncSession` — see `put_summary`'s docstring (ask #40).
    """
    return cast(Database, request.app.state.db)


DatabaseDep = Annotated[Database, Depends(_process_database)]

_TOOL_ADAPTER: TypeAdapter[ToolDefinition] = TypeAdapter(ToolDefinition)

#: Module that owns Egress recordings (V2-12); imported lazily so this router
#: works before it exists.
RECORDINGS_MODULE = "lkap_api.recordings"

RecordingStarter = Callable[
    [AsyncSession, SessionRow, LiveKitConnection, ConnectionClientFactory], Awaitable[RecordingStartOut]
]


class NotImplementedApiError(ApiError):
    """501: the feature's owning package has not landed yet."""

    status_code = 501
    code = "not_implemented"


async def _load_session(db: AsyncSession, session_id: str) -> SessionRow:
    """Load a session by id in any workspace — the worker (service token) is not a member of one.

    Everything else a worker route reads is then scoped to ``row.workspace_id``.
    """
    row = (
        await db.execute(
            select(SessionRow)
            .where(SessionRow.id == session_id)
            .execution_options(**{CROSS_WORKSPACE_OPTION: True})
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"unknown session '{session_id}'")
    return row


def _credential_ids(config: AgentConfig) -> set[str]:
    """Every credential id the pipeline references, across all slots.

    `qa.model` is not a `pipeline.*` field (R-V2-6's `qa_llm` slot is resolved
    from `config.qa.model`, not from `SLOT_KIND`'s pipeline-attribute loop),
    so it is added explicitly — otherwise a vendor-key QA judge would resolve
    with no secret and the worker would get an unauthenticated LLM.
    """
    ids: set[str] = set()
    for slot in SLOT_KIND:
        ref = getattr(config.pipeline, slot, None)
        if isinstance(ref, ProviderRef) and ref.credential_id:
            ids.add(ref.credential_id)
    qa = effective_qa(config)  # R-V2-11: a flow `qa` node turns QA on
    if qa.enabled and qa.model is not None and qa.model.credential_id:
        ids.add(qa.model.credential_id)
    return ids


async def _session_agent(db: AsyncSession, session: SessionRow) -> Agent:
    """The session's agent, inside the session's workspace.

    Raises:
        NotFoundError: If the agent is gone.
    """
    agent = (
        await db.execute(
            select(Agent).where(Agent.id == session.agent_id, Agent.workspace_id == session.workspace_id)
        )
    ).scalar_one_or_none()
    if agent is None:
        raise NotFoundError(f"unknown agent '{session.agent_id}'")
    return agent


async def _decrypt(
    db: AsyncSession, vault: Vault, ids: set[str], *, workspace_id: str
) -> dict[str, dict[str, str]]:
    """Decrypt the requested credentials.

    Raises:
        ConflictError: If a referenced credential no longer exists.
    """
    if not ids:
        return {}
    # Only the agent's own workspace: a config naming another workspace's
    # credential id resolves to "missing", never to that workspace's secret.
    rows = (
        (
            await db.execute(
                select(Credential).where(Credential.id.in_(ids), Credential.workspace_id == workspace_id)
            )
        )
        .scalars()
        .all()
    )
    found = {row.id: vault.decrypt(row.ciphertext) for row in rows}
    missing = ids - set(found)
    if missing:
        raise ConflictError(
            "agent configuration references a credential that no longer exists",
            details={"credential_ids": sorted(missing)},
        )
    return found


async def _session_connection(db: AsyncSession, session: SessionRow, agent: Agent) -> LiveKitConnection:
    """The connection a session runs on: its own, else the agent's (or the default)."""
    if session.connection_id:
        row = await db.scalar(
            select(LiveKitConnection).where(
                LiveKitConnection.workspace_id == session.workspace_id,
                LiveKitConnection.id == session.connection_id,
            )
        )
        if row is not None:
            return row
    row = await resolve_agent_connection(db, agent)
    session.connection_id = row.id
    return row


async def _build_resolved(
    db: AsyncSession, vault: Vault, settings: Settings, session: SessionRow, agent: Agent
) -> ResolvedAgentConfig:
    """Resolve a session's configuration and mark it active. **Contains secrets.**"""
    config = AgentConfig.model_validate(agent.config)
    pack = get_manifest(settings.packs_list, agent.pack_id)
    tool_rows: list[Tool] = []
    if config.tools.tool_ids:
        tool_rows = list(
            (
                await db.execute(
                    select(Tool).where(
                        Tool.id.in_(config.tools.tool_ids),
                        Tool.enabled.is_(True),
                        Tool.workspace_id == agent.workspace_id,
                    )
                )
            )
            .scalars()
            .all()
        )

    wanted = _credential_ids(config)
    for row in tool_rows:
        credential_id = row.definition.get("credential_id") if isinstance(row.definition, dict) else None
        if isinstance(credential_id, str):
            wanted.add(credential_id)
    secrets = await _decrypt(db, vault, wanted, workspace_id=agent.workspace_id)

    tools: list[ToolDefinition] = []
    for row in tool_rows:
        definition = _TOOL_ADAPTER.validate_python(row.definition)
        tools.append(resolve_tool_definition(definition, secrets.get(definition.credential_id or "", {})))

    connection = await _session_connection(db, session, agent)
    installed = await installed_provider_ids(db, connection.id)

    if session.status == "created":
        session.status = "active"
        session.started_at = utcnow()
    await db.flush()

    log.info(
        "session_resolved",
        session_id=session.id,
        agent_id=agent.id,
        connection_id=connection.id,
        channel=session.channel,
        config_version=session.config_version,
        pipeline_mode=config.pipeline.mode,
        tool_count=len(tools),
        kb_count=len(config.knowledge.kb_ids),
    )
    return ResolvedAgentConfig(
        session_id=session.id,
        agent_id=agent.id,
        agent_slug=agent.slug,
        config_version=session.config_version,
        pack_id=agent.pack_id,
        ui_panel_id=agent.ui_panel_id,
        config=config,
        resolved=resolve_providers(config, secrets),
        tools=tools,
        kb_ids=list(config.knowledge.kb_ids),
        participant_identity=session.participant_identity,
        workspace_id=session.workspace_id,
        channel=session.channel,
        connection=ConnectionInfo(
            connection_id=connection.id,
            deployment_type=cast(DeploymentType, connection.deployment_type),
            capabilities=capabilities_of(connection),
        ),
        recording=config.recording,
        panel=effective_layout(agent, pack),
        installed_provider_ids=sorted(installed) if installed is not None else None,
        # R-V2-22: the session's seed variables (an outbound call's `CallCreate.variables`).
        variables=dict(session.variables or {}),
    )


@router.get(
    "/sessions/{session_id}/resolved",
    response_model=ResolvedAgentConfig,
    summary="Resolve a session's configuration (worker only)",
    description=(
        "Returns the fully resolved configuration for a session: provider constructor kwargs "
        "with decrypted credentials, tool definitions with `{{ secret.NAME }}` substituted, "
        "the attached knowledge bases and the connection's capability flags. Marks the session "
        "active. Never exposed to browsers."
    ),
)
async def resolved_config(
    session_id: str, db: DbDep, vault: VaultDep, settings: SettingsDep, _service: ServiceDep
) -> ResolvedAgentConfig:
    """Resolve and return a session's configuration for the worker.

    Raises:
        NotFoundError: If the session or its agent is gone.
        ConflictError: If the session has already finished.
    """
    session = await _load_session(db, session_id)
    if session.status in {"ended", "failed"}:
        raise ConflictError(f"session '{session_id}' has already ended")
    agent = await _session_agent(db, session)
    return await _build_resolved(db, vault, settings, session, agent)


@router.post(
    "/sessions/start",
    response_model=ResolvedAgentConfig,
    status_code=status.HTTP_201_CREATED,
    summary="Create a session from the worker (worker only)",
    description=(
        "For rooms the platform did not create (inbound SIP, API-created rooms): the dispatch "
        "metadata carries no `session_id`, so the worker creates the session row here and gets "
        "the resolved configuration back. 409 if the room already has a session."
    ),
)
async def start_session(
    payload: SessionStartIn,
    db: DbDep,
    vault: VaultDep,
    settings: SettingsDep,
    _service: ServiceDep,
    jobs: JobsDep,
    database: DatabaseDep,
    background_tasks: BackgroundTasks,
) -> ResolvedAgentConfig:
    """Create a session row for a worker-discovered room and resolve it.

    Emits `session.started` once the row is committed (docs/v2/_asks.md
    V2-20-1) — `webhooks.emit` opens its own connection, so it runs after
    `db.commit()` for the same "don't deadlock SQLite" reason `put_summary`
    documents (ask #40).

    Raises:
        NotFoundError: If the agent does not exist.
        ConflictError: If the agent is archived or the room already has a session.
    """
    # The worker names the agent from dispatch metadata; the agent's row decides
    # the workspace the new session belongs to (deliberately cross-workspace lookup).
    agent = (
        await db.execute(
            select(Agent)
            .where(Agent.id == payload.agent_id)
            .execution_options(**{CROSS_WORKSPACE_OPTION: True})
        )
    ).scalar_one_or_none()
    if agent is None:
        raise NotFoundError(f"unknown agent '{payload.agent_id}'")
    if agent.archived_at is not None:
        raise ConflictError(f"agent '{agent.slug}' is archived")
    # Room names are unique across every workspace (one LiveKit room, one session).
    existing = await db.scalar(
        select(SessionRow.id)
        .where(SessionRow.room_name == payload.room_name)
        .execution_options(**{CROSS_WORKSPACE_OPTION: True})
    )
    if existing is not None:
        raise ConflictError(
            f"room '{payload.room_name}' already has a session", details={"session_id": existing}
        )
    connection = await resolve_agent_connection(db, agent)
    config = AgentConfig.model_validate(agent.config)
    identity = payload.participant_identity or f"{payload.channel}-caller"
    session = SessionRow(
        workspace_id=agent.workspace_id,
        agent_id=agent.id,
        connection_id=connection.id,
        config_version=agent.config_version,
        room_name=payload.room_name,
        participant_identity=identity,
        participant_name=identity,
        status="created",
        pipeline_mode=config.pipeline.mode,
        channel=payload.channel,
        caller=payload.caller,
    )
    db.add(session)
    await db.flush()
    log.info(
        "session_started_by_worker",
        session_id=session.id,
        agent_id=agent.id,
        connection_id=connection.id,
        channel=payload.channel,
    )
    return await _build_resolved(db, vault, settings, session, agent)


@router.post(
    "/sessions/{session_id}/recording/start",
    response_model=RecordingStartOut,
    summary="Start a session recording (worker only)",
    description=(
        "Called by the worker after it joined the room when `recording.enabled`: the api starts "
        "a room-composite Egress with the session's connection. 501 until the recordings "
        "package is installed."
    ),
)
async def start_recording(
    session_id: str, db: DbDep, factory: ClientFactoryDep, _service: ServiceDep
) -> RecordingStartOut:
    """Start an Egress recording through the recordings package.

    Raises:
        NotFoundError: If the session is unknown.
        NotImplementedApiError: If ``lkap_api.recordings`` is not installed yet.
    """
    session = await _load_session(db, session_id)
    starter = _recording_starter()
    agent = await _session_agent(db, session)
    connection = await _session_connection(db, session, agent)
    return await starter(db, session, connection, factory)


def _recording_starter() -> RecordingStarter:
    """Return ``lkap_api.recordings.start_recording`` or raise a 501."""
    try:
        module = importlib.import_module(RECORDINGS_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name not in {RECORDINGS_MODULE, "recordings"}:
            raise
        raise NotImplementedApiError("session recording is not available in this build") from exc
    starter = getattr(module, "start_recording", None)
    if starter is None:
        raise NotImplementedApiError("session recording is not available in this build")
    return cast(RecordingStarter, starter)


@router.get(
    "/connections/{connection_id}/worker-env",
    response_model=WorkerEnv,
    summary="Decrypted worker environment of a connection (supervisor only)",
    description=(
        "The complete environment of one worker replica for a connection: LiveKit url/key/secret, "
        "agent name, connection id, api url, service token and packs. Fetch it just in time and "
        "never store or log it."
    ),
)
async def connection_worker_env(
    connection_id: str,
    db: DbDep,
    settings: SettingsDep,
    factory: ClientFactoryDep,
    _service: ServiceDep,
) -> WorkerEnv:
    """Return a connection's decrypted worker environment.

    ``LKAP_API_BASE_URL`` is ``bundle.api_base_url``, i.e.
    :attr:`~lkap_api.settings.Settings.worker_callback_base_url` (V2-22, ask
    #75, which retired V2-20F's override here). When that url is only a
    ``PORT`` guess it is logged on every fetch, as well as at startup.

    Raises:
        NotFoundError: If the connection does not exist.
    """
    row = await get_connection_by_id(db, connection_id)
    env = worker_env(row, factory.credentials(row), settings)
    if settings.worker_callback_url_is_derived:
        log.warning(
            "worker_callback_url_derived_from_port",
            connection_id=row.id,
            port=settings.port,
            derived_url=settings.worker_callback_base_url,
        )
    log.info("connection_worker_env_served", connection_id=row.id, keys=len(env.env))
    return env


@router.post(
    "/sessions/{session_id}/events",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Append session events (worker only)",
    description="Appends a batch of timeline events posted by the worker.",
)
async def post_events(session_id: str, payload: SessionEventsIn, db: DbDep, _service: ServiceDep) -> Response:
    """Append worker events to the session timeline."""
    session = await _load_session(db, session_id)
    for event in payload.events:
        db.add(
            SessionEvent(
                session_id=session.id,
                ts=dt.datetime.fromtimestamp(event.ts, tz=dt.UTC),
                type=event.type,
                payload=event.payload,
            )
        )
    await db.flush()
    log.debug("session_events_appended", session_id=session_id, count=len(payload.events))
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/sessions/{session_id}/metrics",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Post per-session latency (worker only)",
    description=(
        "Posted just before the summary: p50/p95 latency percentiles computed from each "
        "assistant turn's `ChatMessage.metrics` (`metrics_collected` is deprecated in "
        "livekit-agents 1.8.2). `usage_lines` always arrives empty — the api prices "
        "`sessions.usage` itself at summary time (CONTRACTS-V2 §4.2) rather than trusting a "
        "worker-computed cost."
    ),
)
async def post_metrics(
    session_id: str, payload: SessionMetricsIn, db: DbDep, _service: ServiceDep
) -> Response:
    """Persist a session's latency percentiles (ask #49)."""
    session = await _load_session(db, session_id)
    session.latency = payload.latency.model_dump(mode="json")
    await db.flush()
    log.info("session_metrics_posted", session_id=session_id, turns=payload.latency.turns)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/sessions/{session_id}/recording",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Report a recording's final state (worker only)",
    description=(
        "The worker's shutdown-time fallback: it reads the Egress state directly from LiveKit "
        "(`list_egress`) and reports it here, covering deployments where the `egress_ended` "
        "webhook cannot reach this api (no `LKAP_PUBLIC_BASE_URL`). Idempotent with the webhook "
        "path — whichever arrives first wins, the other is a no-op update to the same fields."
    ),
)
async def post_recording(
    session_id: str, payload: SessionRecordingIn, db: DbDep, _service: ServiceDep
) -> Response:
    """Apply the worker's best-effort Egress finalisation report (ask #49)."""
    session = await _load_session(db, session_id)
    if session.recording_egress_id and session.recording_egress_id != payload.egress_id:
        log.warning(
            "recording_egress_id_mismatch",
            session_id=session_id,
            expected=session.recording_egress_id,
            reported=payload.egress_id,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    await apply_egress_result(
        db, session, status=payload.status, duration_s=payload.duration_s, error=payload.error
    )
    # One `recording.ready` per session, whichever of this and `egress_ended` comes first (R2-20).
    await schedule_finalize_once(db, session)
    await db.flush()
    log.info(
        "recording_reported_by_worker",
        session_id=session_id,
        egress_id=payload.egress_id,
        status=payload.status,
        error=payload.error,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _merge_turn_count(session: SessionRow) -> None:
    """Fold `latency.turns` into `usage` so the console's turn count has one source.

    `AgentSessionUsage` (the worker's `usage` blob) carries no turn count of
    its own; `SessionLatency.turns` (posted moments earlier by `post_metrics`,
    always before the summary per `agent/src/lkap_agent/observability.py`) is
    the number of assistant turns that reported at least one latency figure —
    the same population the console's sessions list wants under "Turns".
    """
    if not isinstance(session.usage, dict) or session.latency is None:
        return
    turns = session.latency.get("turns") if isinstance(session.latency, dict) else None
    if isinstance(turns, int) and "turns" not in session.usage:
        session.usage = {**session.usage, "turns": turns}


@router.put(
    "/sessions/{session_id}/summary",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Finish a session (worker only)",
    description="Stores the final usage, transcript and UI state and closes the session.",
)
async def put_summary(
    session_id: str,
    payload: SessionSummaryIn,
    db: DbDep,
    _service: ServiceDep,
    jobs: JobsDep,
    database: DatabaseDep,
    background_tasks: BackgroundTasks,
) -> Response:
    """Close a session with its final usage, transcript and UI state.

    Also prices the session's usage into `session_costs`/`sessions.cost_usd`
    (V2-12) and, once the row is committed, schedules the `session.ended`
    webhook (ask #40) — `webhooks.emit` opens its own database connection to
    write its first row, and calling it while this handler's own transaction
    is still open deadlocks SQLite ("database is locked", ask #40's own
    words), so the fix is the same one applied in
    `routers/webhooks.py::redeliver`: commit first, then call it.

    QA scoring is **not** triggered from here (R-V2-5/R-V2-6, superseding
    ask #40's original QA half): the worker runs the judge itself at session
    end and reports the verdict with `PUT .../qa` (`put_qa`, below); enqueueing
    the `qa_scoring` job here too would double-score every session and, for
    the common Inference-only judge, overwrite the worker's `done` verdict
    with the api's inevitable `qa_judge_unavailable` failure.
    """
    session = await _load_session(db, session_id)
    session.status = payload.status
    session.usage = payload.usage
    session.transcript = [turn.model_dump(mode="json") for turn in payload.transcript]
    session.final_ui_state = (
        payload.final_ui_state.model_dump(mode="json") if payload.final_ui_state else None
    )
    session.error = payload.error
    # R-V2-8: the summary is the one terminal write for a flow's outcome (never
    # the best-effort `flow_ended` event); prompt agents send `None` / `{}`.
    session.disposition = payload.disposition
    # R-V2-22: merged, not overwritten — an outbound call stores `CallCreate.variables`
    # on the row before it starts; the summary's values win on a conflict.
    merged_variables = {**(session.variables or {}), **payload.variables}
    session.variables = merged_variables
    session.ended_at = utcnow()
    _merge_turn_count(session)
    await cost_session(db, session)
    await db.flush()
    workspace_id, terminal_status = await _summary_webhook_context(db, session)
    log.info(
        "session_finished",
        session_id=session_id,
        status=payload.status,
        transcript_turns=len(payload.transcript),
        cost_usd=session.cost_usd,
    )
    await db.commit()

    if terminal_status:
        await webhooks.emit(
            database,
            jobs,
            workspace_id=workspace_id,
            event_type=webhook_events.SESSION_ENDED,
            data={
                "session_id": session_id,
                "agent_id": session.agent_id,
                "status": payload.status,
                "disposition": payload.disposition,
                "variables": merged_variables,
            },
            background_tasks=background_tasks,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _summary_webhook_context(db: AsyncSession, session: SessionRow) -> tuple[str, bool]:
    """Read what the post-commit `webhooks.emit` call needs before the transaction closes."""
    terminal = session.status in {"ended", "failed"}
    return session.workspace_id, terminal


@router.put(
    "/sessions/{session_id}/qa",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Report the worker-side QA verdict (worker only)",
    description=(
        "Stores the judge's verdict the worker produced at session end (R-V2-5/R-V2-6); "
        "`scored_by` is always 'worker' here — the api's own re-score path sets 'api'."
    ),
)
async def put_qa(
    session_id: str,
    payload: SessionQaIn,
    db: DbDep,
    _service: ServiceDep,
    jobs: JobsDep,
    database: DatabaseDep,
    background_tasks: BackgroundTasks,
) -> Response:
    """Upsert `session_qa` from the worker's verdict.

    The worker posts this once, after `put_summary`, from its shutdown
    callback (`lkap_agent.qa.score_session` / `_score_quality`) — never
    blocking the ≤10 s summary target. `status="skipped"` when the agent's
    `qa.enabled` is false; `status="failed"` when no judge could be built or
    the judge/repair calls both failed; `status="done"` with a full verdict
    otherwise.

    Emits `session.qa_completed` once the verdict is committed (docs/v2/_asks.md
    V2-20-1), for every terminal status (`done`/`failed`/`skipped`) — the
    worker calls this exactly once per session, so "completed" means "the
    worker's QA pass is over", not "scored successfully".
    """
    session = await _load_session(db, session_id)
    row = await db.get(SessionQa, session_id)
    if row is None:
        row = SessionQa(session_id=session_id)
        db.add(row)
    row.status = payload.status
    row.scored_by = "worker"
    row.score = payload.score
    row.sentiment = payload.sentiment
    row.tags = list(payload.tags)
    row.summary = payload.summary or ""
    row.raw = payload.raw
    row.model = payload.model or ""
    row.error = payload.error
    row.scored_at = utcnow()
    await db.flush()
    workspace_id = session.workspace_id
    log.info(
        "session_qa_reported",
        session_id=session_id,
        status=payload.status,
        scored_by="worker",
        score=row.score,
    )
    await db.commit()
    await webhooks.emit(
        database,
        jobs,
        workspace_id=workspace_id,
        event_type=webhook_events.SESSION_QA_COMPLETED,
        data={
            "session_id": session_id,
            "status": payload.status,
            "score": payload.score,
            "sentiment": payload.sentiment,
            "scored_by": "worker",
        },
        background_tasks=background_tasks,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
