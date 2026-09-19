"""Worker-only endpoints, guarded by ``X-Service-Token``.

This is the single place decrypted credentials leave the api, and only towards
the LiveKit worker: :func:`resolved_config` returns a
:class:`~lkap_contracts.agent_config.ResolvedAgentConfig` containing provider
kwargs and tool templates with real secret values. Nothing in this module logs
the resolved payload — only ids, counts and slot names.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Response, status
from lkap_contracts.agent_config import AgentConfig, ProviderRef, ResolvedAgentConfig
from lkap_contracts.api_models import SessionEventsIn, SessionSummaryIn
from lkap_contracts.tools import ToolDefinition
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.config_service import resolve_providers, resolve_tool_definition
from lkap_api.db.models import Agent, Credential, SessionEvent, Tool, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.deps import DbDep, ServiceDep, VaultDep
from lkap_api.errors import ConflictError, NotFoundError
from lkap_api.logging import get_logger
from lkap_api.vault import Vault

log = get_logger(__name__)

router = APIRouter(prefix="/internal/v1", tags=["internal"])

_TOOL_ADAPTER: TypeAdapter[ToolDefinition] = TypeAdapter(ToolDefinition)


async def _load_session(db: AsyncSession, session_id: str) -> SessionRow:
    row = await db.get(SessionRow, session_id)
    if row is None:
        raise NotFoundError(f"unknown session '{session_id}'")
    return row


def _credential_ids(config: AgentConfig) -> set[str]:
    """Every credential id the pipeline references, across all slots."""
    ids: set[str] = set()
    for slot in ("realtime", "stt", "llm", "tts", "avatar", "image_gen", "workflow_llm"):
        ref = getattr(config.pipeline, slot, None)
        if isinstance(ref, ProviderRef) and ref.credential_id:
            ids.add(ref.credential_id)
    return ids


async def _decrypt(db: AsyncSession, vault: Vault, ids: set[str]) -> dict[str, dict[str, str]]:
    """Decrypt the requested credentials.

    Raises:
        ConflictError: If a referenced credential no longer exists.
    """
    if not ids:
        return {}
    rows = (await db.execute(select(Credential).where(Credential.id.in_(ids)))).scalars().all()
    found = {row.id: vault.decrypt(row.ciphertext) for row in rows}
    missing = ids - set(found)
    if missing:
        raise ConflictError(
            "agent configuration references a credential that no longer exists",
            details={"credential_ids": sorted(missing)},
        )
    return found


@router.get(
    "/sessions/{session_id}/resolved",
    response_model=ResolvedAgentConfig,
    summary="Resolve a session's configuration (worker only)",
    description=(
        "Returns the fully resolved configuration for a session: provider constructor kwargs "
        "with decrypted credentials, tool definitions with `{{ secret.NAME }}` substituted, "
        "and the attached knowledge bases. Marks the session active. Never exposed to browsers."
    ),
)
async def resolved_config(
    session_id: str, db: DbDep, vault: VaultDep, _service: ServiceDep
) -> ResolvedAgentConfig:
    """Resolve and return a session's configuration for the worker.

    Raises:
        NotFoundError: If the session or its agent is gone.
        ConflictError: If the session has already finished.
    """
    session = await _load_session(db, session_id)
    if session.status in {"ended", "failed"}:
        raise ConflictError(f"session '{session_id}' has already ended")
    agent = await db.get(Agent, session.agent_id)
    if agent is None:
        raise NotFoundError(f"unknown agent '{session.agent_id}'")

    config = AgentConfig.model_validate(agent.config)
    tool_rows: list[Tool] = []
    if config.tools.tool_ids:
        tool_rows = list(
            (await db.execute(select(Tool).where(Tool.id.in_(config.tools.tool_ids), Tool.enabled == 1)))
            .scalars()
            .all()
        )

    wanted = _credential_ids(config)
    for row in tool_rows:
        credential_id = row.definition.get("credential_id") if isinstance(row.definition, dict) else None
        if isinstance(credential_id, str):
            wanted.add(credential_id)
    secrets = await _decrypt(db, vault, wanted)

    tools: list[ToolDefinition] = []
    for row in tool_rows:
        definition = _TOOL_ADAPTER.validate_python(row.definition)
        tools.append(resolve_tool_definition(definition, secrets.get(definition.credential_id or "", {})))

    if session.status == "created":
        session.status = "active"
        session.started_at = utcnow()
        await db.flush()

    log.info(
        "session_resolved",
        session_id=session_id,
        agent_id=agent.id,
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
    )


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


@router.put(
    "/sessions/{session_id}/summary",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Finish a session (worker only)",
    description="Stores the final usage, transcript and UI state and closes the session.",
)
async def put_summary(
    session_id: str, payload: SessionSummaryIn, db: DbDep, _service: ServiceDep
) -> Response:
    """Close a session with its final usage, transcript and UI state."""
    session = await _load_session(db, session_id)
    session.status = payload.status
    session.usage = payload.usage
    session.transcript = [turn.model_dump(mode="json") for turn in payload.transcript]
    session.final_ui_state = (
        payload.final_ui_state.model_dump(mode="json") if payload.final_ui_state else None
    )
    session.error = payload.error
    session.ended_at = utcnow()
    await db.flush()
    log.info(
        "session_finished",
        session_id=session_id,
        status=payload.status,
        transcript_turns=len(payload.transcript),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
