"""Agent CRUD, pack seeding, publication and configuration validation."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from fastapi import APIRouter, Query, Response, status
from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.api_models import (
    AgentCreate,
    AgentOut,
    AgentPage,
    AgentPublicOut,
    AgentUpdate,
    ValidationResult,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.config_service import seed_config_from_manifest, validate_agent_config
from lkap_api.db.models import Agent, Credential, KnowledgeBase, Tool, utcnow
from lkap_api.deps import AdminDep, DbDep, OptionalAdminDep, SettingsDep, VaultDep
from lkap_api.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
)
from lkap_api.kb.embed import resolve_embedder
from lkap_api.kb.seed import import_pack_kb_seeds
from lkap_api.kb.store import get_lancedb_store
from lkap_api.logging import get_logger
from lkap_api.packs import get_manifest
from lkap_api.settings import Settings
from lkap_api.vault import Vault

log = get_logger(__name__)

router = APIRouter(prefix="/v1/agents", tags=["agents"])

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Return a url-safe slug for an agent name (never empty)."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_STRIP.sub("-", ascii_name.lower()).strip("-")
    return slug or "agent"


async def unique_slug(db: AsyncSession, base: str) -> str:
    """Return ``base`` or the first free ``base-N`` suffix."""
    taken = set((await db.execute(select(Agent.slug).where(Agent.slug.like(f"{base}%")))).scalars())
    if base not in taken:
        return base
    index = 2
    while f"{base}-{index}" in taken:
        index += 1
    return f"{base}-{index}"


def agent_config_of(row: Agent) -> AgentConfig:
    """Parse and return the stored ``AgentConfig`` of an agent row."""
    return AgentConfig.model_validate(row.config)


def to_out(row: Agent) -> AgentOut:
    """Render the admin view of an agent."""
    return AgentOut(
        id=row.id,
        slug=row.slug,
        name=row.name,
        description=row.description,
        pack_id=row.pack_id,
        ui_panel_id=row.ui_panel_id,
        published=bool(row.published),
        config=agent_config_of(row),
        config_version=row.config_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def to_public(row: Agent) -> AgentPublicOut:
    """Render the browser-safe view of an agent (no config, no credential ids)."""
    config = agent_config_of(row)
    return AgentPublicOut(
        id=row.id,
        slug=row.slug,
        name=row.name,
        description=row.description,
        ui_panel_id=row.ui_panel_id,
        capabilities=config.capabilities,
        pipeline_mode=config.pipeline.mode,
    )


async def load_agent(db: AsyncSession, id_or_slug: str) -> Agent:
    """Load an agent by id or slug.

    Raises:
        NotFoundError: If neither matches.
    """
    row = await db.get(Agent, id_or_slug)
    if row is None:
        row = (await db.execute(select(Agent).where(Agent.slug == id_or_slug))).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"unknown agent '{id_or_slug}'")
    return row


async def validate_stored_config(db: AsyncSession, config: AgentConfig) -> ValidationResult:
    """Validate a configuration against the registry and the rows that exist."""
    credential_providers = dict(
        (await db.execute(select(Credential.id, Credential.provider_id))).tuples().all()
    )
    tool_ids = set((await db.execute(select(Tool.id))).scalars())
    kb_ids = set((await db.execute(select(KnowledgeBase.id))).scalars())
    return validate_agent_config(
        config,
        credential_providers=credential_providers,
        known_tool_ids=tool_ids,
        known_kb_ids=kb_ids,
    )


async def _seed_config(
    db: AsyncSession, settings: Settings, vault: Vault, pack_id: str
) -> tuple[AgentConfig, str]:
    """Build a config from a pack manifest; returns ``(config, ui_panel_id)``."""
    manifest = get_manifest(settings.packs_list, pack_id)
    if manifest is None:
        raise UnprocessableEntityError(
            f"pack '{pack_id}' is not installed; send an explicit config or set LKAP_PACKS",
            details={"packs": settings.packs_list},
        )
    rows = (await db.execute(select(Credential.provider_id, Credential.id))).tuples().all()
    by_provider: dict[str, list[str]] = {}
    for provider_id, credential_id in rows:
        by_provider.setdefault(provider_id, []).append(credential_id)
    config = seed_config_from_manifest(manifest, credentials_by_provider=by_provider)
    if manifest.kb_seeds:
        embedder = await resolve_embedder(settings, db, vault)
        store = get_lancedb_store(settings.data_dir)
        kb_ids = await import_pack_kb_seeds(
            db=db,
            store=store,
            embedder=embedder,
            packs=settings.packs_list,
            pack_id=pack_id,
            seeds=manifest.kb_seeds,
        )
        config.knowledge.kb_ids = kb_ids
        log.info("pack_kb_seeds_imported", pack_id=pack_id, kb_ids=kb_ids)
    return config, manifest.ui_panel_id


def _raise_if_invalid(result: ValidationResult) -> None:
    if not result.ok:
        raise UnprocessableEntityError(
            "agent configuration is invalid",
            details={"errors": result.errors, "warnings": result.warnings},
        )


@router.post(
    "",
    response_model=AgentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an agent",
    description=(
        "Creates an agent. With `config` omitted the configuration is seeded from the pack "
        "manifest, substituting LiveKit Inference for providers without a credential."
    ),
)
async def create_agent(
    payload: AgentCreate, db: DbDep, settings: SettingsDep, vault: VaultDep, _admin: AdminDep
) -> AgentOut:
    """Create an agent from an explicit config or from its pack's defaults."""
    if payload.config is None:
        config, panel_id = await _seed_config(db, settings, vault, payload.pack_id)
    else:
        config = payload.config
        manifest = get_manifest(settings.packs_list, payload.pack_id)
        panel_id = manifest.ui_panel_id if manifest else "generic"
    _raise_if_invalid(await validate_stored_config(db, config))

    row = Agent(
        slug=await unique_slug(db, slugify(payload.name)),
        name=payload.name,
        description=payload.description,
        pack_id=payload.pack_id,
        ui_panel_id=payload.ui_panel_id or panel_id,
        published=False,
        config=config.model_dump(mode="json"),
        config_version=1,
    )
    db.add(row)
    await db.flush()
    log.info("agent_created", agent_id=row.id, slug=row.slug, pack_id=row.pack_id)
    return to_out(row)


@router.get(
    "",
    response_model=AgentPage,
    summary="List agents",
    description="Every agent with its full configuration (admin only).",
)
async def list_agents(
    db: DbDep,
    _admin: AdminDep,
    published: bool | None = Query(default=None, description="Filter by publication state"),
) -> AgentPage:
    """Return all agents, newest first."""
    stmt = select(Agent)
    count_stmt = select(func.count()).select_from(Agent)
    if published is not None:
        stmt = stmt.where(Agent.published == int(published))
        count_stmt = count_stmt.where(Agent.published == int(published))
    rows = (await db.execute(stmt.order_by(Agent.created_at.desc()))).scalars().all()
    total = (await db.execute(count_stmt)).scalar_one()
    return AgentPage(items=[to_out(r) for r in rows], total=total)


@router.get(
    "/{id_or_slug}",
    response_model=None,
    responses={200: {"model": AgentOut, "description": "Admin view, or the public view without a token"}},
    summary="Get an agent",
    description=(
        "With an admin token returns the full `AgentOut`. Without one returns `AgentPublicOut` "
        "for a published agent and 403 otherwise."
    ),
)
async def get_agent(id_or_slug: str, db: DbDep, admin: OptionalAdminDep) -> AgentOut | AgentPublicOut:
    """Return the admin or the public projection of an agent."""
    row = await load_agent(db, id_or_slug)
    if admin:
        return to_out(row)
    if not row.published:
        raise ForbiddenError(f"agent '{id_or_slug}' is not published")
    return to_public(row)


@router.put(
    "/{agent_id}",
    response_model=AgentOut,
    summary="Update an agent",
    description="Partial update. Saving a changed `config` validates it and bumps `config_version`.",
)
async def update_agent(agent_id: str, payload: AgentUpdate, db: DbDep, _admin: AdminDep) -> AgentOut:
    """Apply a partial update to an agent."""
    row = await load_agent(db, agent_id)
    if payload.name is not None:
        row.name = payload.name
    if payload.description is not None:
        row.description = payload.description
    if payload.ui_panel_id is not None:
        row.ui_panel_id = payload.ui_panel_id
    if payload.config is not None:
        _raise_if_invalid(await validate_stored_config(db, payload.config))
        new_config: dict[str, Any] = payload.config.model_dump(mode="json")
        if new_config != row.config:
            row.config = new_config
            row.config_version += 1
    if payload.published is not None:
        row.published = payload.published
    row.updated_at = utcnow()
    await db.flush()
    log.info(
        "agent_updated",
        agent_id=row.id,
        config_version=row.config_version,
        published=bool(row.published),
    )
    return to_out(row)


@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an agent",
    description="Deletes the agent and its tools. Fails with 409 while sessions still reference it.",
)
async def delete_agent(agent_id: str, db: DbDep, _admin: AdminDep) -> Response:
    """Delete an agent that has no sessions."""
    row = await load_agent(db, agent_id)
    await db.delete(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise ConflictError(
            "agent still has sessions; delete them first or keep the agent unpublished"
        ) from exc
    log.info("agent_deleted", agent_id=agent_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{agent_id}/validate",
    response_model=ValidationResult,
    summary="Validate an agent configuration",
    description="Re-checks the stored config against the registry, credentials, tools and KBs.",
)
async def validate_agent(agent_id: str, db: DbDep, _admin: AdminDep) -> ValidationResult:
    """Validate the agent's stored configuration."""
    row = await load_agent(db, agent_id)
    return await validate_stored_config(db, agent_config_of(row))
