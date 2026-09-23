"""Agent CRUD, pack seeding, publication and configuration validation.

Workspace scoping (V2-02): admin handlers take ``ctx: AdminCtxDep`` and every
query filters on ``ctx.workspace_id``; an agent of another workspace is a 404.
``load_agent`` stays the deliberate cross-workspace loader for the public
routes (``GET /v1/agents/{slug}`` for published agents and ``connect``).
Validation and seeding go through V2-03's workspace- and connection-aware
``config_service`` entry points (asks #20).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from fastapi import APIRouter, Query, Response, status
from lkap_contracts import providers as provider_registry
from lkap_contracts.agent_config import AgentConfig, AgentLimits
from lkap_contracts.api_models import (
    AgentCreate,
    AgentOut,
    AgentPage,
    AgentPublicOut,
    AgentUpdate,
    ConfigVersionOut,
    Issue,
    Page,
    ValidationResult,
)
from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.auth.deps import OptionalWorkspaceCtxDep, WorkspaceContext
from lkap_api.auth.roles import Requirement
from lkap_api.config_service import connection_context_for, seed_config_from_manifest, validate_in_db
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Agent, AgentConfigVersion, Credential, LiveKitConnection, utcnow
from lkap_api.db.models import Session as SessionRow
from lkap_api.deps import AdminCtxDep, DbDep, SettingsDep, VaultDep
from lkap_api.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
)
from lkap_api.flows import derived_mode, pack_tool_names_for
from lkap_api.kb.embed import resolve_embedder
from lkap_api.kb.seed import import_pack_kb_seeds
from lkap_api.kb.store import get_lancedb_store
from lkap_api.logging import get_logger
from lkap_api.packs import get_manifest
from lkap_api.panels import effective_layout
from lkap_api.settings import Settings
from lkap_api.vault import Vault

log = get_logger(__name__)

router = APIRouter(prefix="/v1/agents", tags=["agents"])

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_READ = Requirement("viewer", "agents:read")


#: The shape of a row id (``uuid4().hex``); no slug may take it (V2-21).
_ID_SHAPED = re.compile(r"^[0-9a-f]{32}$")


def slugify(name: str) -> str:
    """Return a url-safe slug for an agent name (never empty, never id-shaped).

    Routes accept an id *or* a slug, so a slug equal to another agent's id
    would make that id resolve to the wrong agent (V2-21: an agent named after
    a foreign agent's id read the foreign config history). Such a name gets an
    ``agent-`` prefix.
    """
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_STRIP.sub("-", ascii_name.lower()).strip("-")
    if _ID_SHAPED.match(slug):
        slug = f"agent-{slug}"
    return slug or "agent"


def by_id_first(id_or_slug: str) -> Any:
    """``ORDER BY`` that ranks an exact id match above a slug match (V2-21)."""
    return case((Agent.id == id_or_slug, 0), else_=1)


async def unique_slug(db: AsyncSession, base: str) -> str:
    """Return ``base`` or the first free ``base-N`` suffix.

    Slugs are unique across workspaces (they name public ``/s/{slug}`` pages),
    so this read is deliberately cross-workspace.
    """
    taken = set(
        (
            await db.execute(
                select(Agent.slug)
                .where(Agent.slug.like(f"{base}%"))
                .execution_options(**{CROSS_WORKSPACE_OPTION: True})
            )
        ).scalars()
    )
    if base not in taken:
        return base
    index = 2
    while f"{base}-{index}" in taken:
        index += 1
    return f"{base}-{index}"


def agent_config_of(row: Agent) -> AgentConfig:
    """Parse and return the stored ``AgentConfig`` of an agent row."""
    return AgentConfig.model_validate(row.config)


def to_out(row: Agent, *, session_count: int | None = None, last_session_at: Any | None = None) -> AgentOut:
    """Render the admin view of an agent.

    `session_count`/`last_session_at` are opt-in aggregates (V2-12,
    CONTRACTS-V2 §3.4) — most call sites (create/update/validate) have no use
    for a second query per agent and leave them `None`; `list_agents`/`get_agent`
    fill them from a single aggregated query alongside the page/row itself.
    """
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
        workspace_id=row.workspace_id,
        connection_id=row.connection_id,
        mode=row.mode,
        archived_at=row.archived_at,
        limits=AgentLimits.model_validate(row.limits or {}),
        allowed_origins=[str(origin) for origin in row.allowed_origins or []],
        session_count=session_count,
        last_session_at=last_session_at,
    )


async def _session_aggregates(
    db: AsyncSession, ctx: WorkspaceContext, agent_ids: list[str]
) -> dict[str, tuple[int, Any]]:
    """`{agent_id: (session_count, last_session_at)}` for the given agents, one query.

    Scoped by `ctx.workspace_id` even though every `agent_id` already belongs
    to it (the caller only ever passes ids from a workspace-scoped agent
    query) — `sessions` is a tenant table and the `db.guard` listener rejects
    any query against one with no `workspace_id` predicate at all (CONTRACTS-V2 §3.1).
    """
    if not agent_ids:
        return {}
    rows = (
        await db.execute(
            select(SessionRow.agent_id, func.count(), func.max(SessionRow.created_at))
            .where(
                SessionRow.workspace_id == ctx.workspace_id,
                SessionRow.agent_id.in_(agent_ids),
                SessionRow.deleted_at.is_(None),
            )
            .group_by(SessionRow.agent_id)
        )
    ).all()
    return {agent_id: (count, last_at) for agent_id, count, last_at in rows}


def _snapshot_version(db: AsyncSession, row: Agent, *, created_by: str | None, note: str = "") -> None:
    """Write an immutable `agent_config_versions` row for `row`'s *current* config (D-V2-13).

    Called once on create (version 1) and once per `update_agent` call that
    actually bumps `config_version` — never on a no-op save (the `new_config
    != row.config` guard already in `update_agent` prevents a version churn
    that would make `GET .../versions` useless as a change history).
    `created_by` is the acting principal's id (a user id, an API-key id or
    `break-glass`, as in `audit_log.actor_id`; asks V2-16-9).
    """
    db.add(
        AgentConfigVersion(
            agent_id=row.id,
            config_version=row.config_version,
            config=row.config,
            created_by=created_by,
            note=note,
        )
    )


def to_public(row: Agent, settings: Settings) -> AgentPublicOut:
    """Render the browser-safe view of an agent (no config, no credential ids).

    `panel` (R-V2-7) is the *effective* layout, not `config.panel` verbatim —
    see `lkap_api.panels.effective_layout`. `settings.packs_list` is needed to
    look the agent's pack manifest up for its `default_panel` fallback.
    `ui_panel_id` mirrors `panel.panel_id` for one release (R-V2-7) rather
    than the stored `agents.ui_panel_id` column, which still holds the pack's
    pre-v2 value (e.g. `"generic"` where the effective `panel_id` is now
    `"composite"`).
    """
    config = agent_config_of(row)
    pack = get_manifest(settings.packs_list, row.pack_id)
    layout = effective_layout(row, pack)
    return AgentPublicOut(
        id=row.id,
        slug=row.slug,
        name=row.name,
        description=row.description,
        ui_panel_id=layout.panel_id,
        panel=layout,
        capabilities=config.capabilities,
        pipeline_mode=config.pipeline.mode,
    )


async def load_agent(db: AsyncSession, id_or_slug: str) -> Agent:
    """Load an agent by id or slug in **any** workspace (public routes only).

    Raises:
        NotFoundError: If neither matches.
    """
    row = (
        await db.execute(
            select(Agent)
            .where(or_(Agent.id == id_or_slug, Agent.slug == id_or_slug))
            .order_by(by_id_first(id_or_slug))
            .limit(1)
            .execution_options(**{CROSS_WORKSPACE_OPTION: True})
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"unknown agent '{id_or_slug}'")
    return row


async def load_scoped_agent(db: AsyncSession, ctx: WorkspaceContext, id_or_slug: str) -> Agent:
    """Load an agent of the caller's workspace by id or slug.

    Raises:
        NotFoundError: If no agent of this workspace matches (other workspaces included).
    """
    row = (
        await db.execute(
            select(Agent)
            .where(
                Agent.workspace_id == ctx.workspace_id,
                or_(Agent.id == id_or_slug, Agent.slug == id_or_slug),
            )
            .order_by(by_id_first(id_or_slug))
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"unknown agent '{id_or_slug}'")
    return row


async def validate_stored_config(
    db: AsyncSession,
    config: AgentConfig,
    *,
    workspace_id: str,
    connection_id: str | None,
    pack_id: str | None = None,
) -> ValidationResult:
    """Validate a configuration against its workspace's rows and its connection (asks #20).

    ``pack_id`` limits flow node tool references to the agent's own pack's tools
    (asks V2-16-2); ``None`` falls back to every installed pack's.
    """
    return await validate_in_db(
        db,
        config,
        workspace_id=workspace_id,
        connection_id=connection_id,
        pack_tool_names=pack_tool_names_for(pack_id),
    )


async def _workspace_connection_id(
    db: AsyncSession, workspace_id: str, connection_id: str | None
) -> str | None:
    """Return ``connection_id`` if it belongs to the workspace, else the workspace default.

    Raises:
        UnprocessableEntityError: An explicit id that is not a connection of this workspace.
    """
    stmt = select(LiveKitConnection.id).where(LiveKitConnection.workspace_id == workspace_id)
    if connection_id:
        found = await db.scalar(stmt.where(LiveKitConnection.id == connection_id))
        if found is None:
            raise UnprocessableEntityError(
                f"unknown connection '{connection_id}'", details={"connection_id": connection_id}
            )
        return str(found)
    default = await db.scalar(stmt.where(LiveKitConnection.is_default == 1))
    return str(default) if default is not None else None


async def _seed_config(
    db: AsyncSession,
    settings: Settings,
    vault: Vault,
    pack_id: str,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    connection_id: str | None = None,
) -> tuple[AgentConfig, str]:
    """Build a config from a pack manifest; returns ``(config, ui_panel_id)``."""
    manifest = get_manifest(settings.packs_list, pack_id)
    if manifest is None:
        raise UnprocessableEntityError(
            f"pack '{pack_id}' is not installed; send an explicit config or set LKAP_PACKS",
            details={"packs": settings.packs_list},
        )
    rows = (
        await db.execute(
            select(Credential.provider_id, Credential.id).where(Credential.workspace_id == workspace_id)
        )
    ).tuples()
    by_provider: dict[str, list[str]] = {}
    for provider_id, credential_id in rows:
        by_provider.setdefault(provider_id, []).append(credential_id)
    connection = await connection_context_for(db, workspace_id=workspace_id, connection_id=connection_id)
    config = seed_config_from_manifest(manifest, credentials_by_provider=by_provider, connection=connection)
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
            workspace_id=workspace_id,
        )
        config.knowledge.kb_ids = kb_ids
        log.info("pack_kb_seeds_imported", pack_id=pack_id, kb_ids=kb_ids)
    return config, manifest.ui_panel_id


#: ``ProviderRef.fields`` names that choose where a provider's traffic goes.
_ENDPOINT_FIELD = re.compile(r"(url|endpoint|host|api_base)$", re.IGNORECASE)
#: Pointing a provider at a new endpoint is credential management (V2-21).
_SET_ENDPOINT = Requirement("admin", "providers:write")


def _field_defaults(provider_id: str) -> dict[str, Any]:
    try:
        spec = provider_registry.get(provider_id)
    except KeyError:
        return {}
    return {field.name: field.default for field in spec.fields if field.default is not None}


def endpoint_overrides(config: Any) -> set[tuple[str, str, str]]:
    """Every ``(provider_id, field, value)`` endpoint override in a config document.

    Walks the whole document (pipeline slots, ``qa.model``, flow nodes …) for
    provider references (``{"provider_id": …, "fields": {…}}``) and collects
    the fields whose name ends in ``url``, ``endpoint``, ``host`` or ``api_base``.
    """
    found: set[tuple[str, str, str]] = set()
    stack: list[Any] = [config]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            fields = node.get("fields")
            if isinstance(node.get("provider_id"), str) and isinstance(fields, dict):
                provider_id = str(node["provider_id"])
                defaults = _field_defaults(provider_id)
                for name, value in fields.items():
                    if not _ENDPOINT_FIELD.search(str(name)) or value in (None, ""):
                        continue
                    if defaults.get(str(name)) == value:
                        continue  # the registry's own default is not an override
                    found.add((provider_id, str(name), str(value)))
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return found


def check_endpoint_overrides(ctx: WorkspaceContext, old: Any, new: Any) -> None:
    """Refuse a builder who adds or changes a provider endpoint override (V2-21).

    A provider's endpoint receives its API key (the bound credential, or the
    worker's own environment key when none is bound), so a builder who could
    set ``base_url`` could send an admin-held key to a host they own. Keeping
    an override that is already there, or removing one, stays a builder's job.

    Raises:
        ForbiddenError: The caller is below ``admin`` (or an API key without
            ``providers:write``) and the new config has an override the old one lacked.
    """
    added = endpoint_overrides(new) - endpoint_overrides(old)
    if added and not ctx.allows(_SET_ENDPOINT):
        raise ForbiddenError(
            "setting a provider endpoint (base_url and similar fields) needs the 'admin' role "
            "(and the 'providers:write' scope for API keys): the endpoint receives the provider's key",
            details={
                "fields": sorted({f"{provider}.{field}" for provider, field, _ in added}),
                "required_role": _SET_ENDPOINT.role,
                "required_scope": _SET_ENDPOINT.scope,
            },
        )


def _raise_if_invalid(result: ValidationResult) -> None:
    if not result.ok:
        raise UnprocessableEntityError(
            "agent configuration is invalid",
            details={
                "errors": result.errors,
                "warnings": result.warnings,
                # V2-16: the addressable findings too, so the flow builder can put a dot on the node.
                "issues": [issue.model_dump() for issue in result.issues],
            },
        )


def _check_mode(requested: str | None, config: AgentConfig) -> None:
    """Reject a payload ``mode`` that disagrees with ``config`` (R-V2-12).

    ``agents.mode`` is derived from ``config.flow`` (non-empty ``nodes`` ⇒
    ``flow``); a ``mode`` that agrees is accepted for one release.

    Raises:
        UnprocessableEntityError: ``requested`` differs from the derived mode.
    """
    derived = derived_mode(config)
    if requested is None or requested == derived:
        return
    message = (
        "mode 'flow' needs a flow with at least one node in config.flow"
        if requested == "flow"
        else "mode 'prompt' needs config.flow to be empty; send config with flow: null"
    )
    issue = Issue(path="mode", message=message)
    raise UnprocessableEntityError(
        message,
        details={"errors": [f"mode: {message}"], "warnings": [], "issues": [issue.model_dump()]},
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
    payload: AgentCreate, db: DbDep, settings: SettingsDep, vault: VaultDep, ctx: AdminCtxDep
) -> AgentOut:
    """Create an agent from an explicit config or from its pack's defaults."""
    connection_id = await _workspace_connection_id(db, ctx.workspace_id, payload.connection_id)
    if payload.config is None:
        config, panel_id = await _seed_config(
            db, settings, vault, payload.pack_id, workspace_id=ctx.workspace_id, connection_id=connection_id
        )
    else:
        config = payload.config
        check_endpoint_overrides(ctx, {}, config.model_dump(mode="json"))
        manifest = get_manifest(settings.packs_list, payload.pack_id)
        panel_id = manifest.ui_panel_id if manifest else "generic"
    _raise_if_invalid(
        await validate_stored_config(
            db, config, workspace_id=ctx.workspace_id, connection_id=connection_id, pack_id=payload.pack_id
        )
    )

    row = Agent(
        workspace_id=ctx.workspace_id,
        connection_id=connection_id,
        slug=await unique_slug(db, slugify(payload.name)),
        name=payload.name,
        description=payload.description,
        pack_id=payload.pack_id,
        ui_panel_id=payload.ui_panel_id or panel_id,
        published=False,
        config=config.model_dump(mode="json"),
        config_version=1,
        # R-V2-12: derived from the config; `AgentCreate.mode` (default "prompt") is ignored.
        mode=derived_mode(config),
    )
    db.add(row)
    await db.flush()
    _snapshot_version(db, row, created_by=ctx.actor.id, note="created")
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
    ctx: AdminCtxDep,
    published: bool | None = Query(default=None, description="Filter by publication state"),
    connection_id: str | None = Query(default=None, description="Only agents bound to this connection"),
    mode: str | None = Query(default=None, description="prompt | flow"),
    archived: bool | None = Query(default=None, description="Only archived (true) or active (false) agents"),
) -> AgentPage:
    """Return the workspace's agents, newest first, with each one's session count."""
    conditions = [Agent.workspace_id == ctx.workspace_id]
    if published is not None:
        conditions.append(Agent.published == int(published))
    if connection_id:
        conditions.append(Agent.connection_id == connection_id)
    if mode:
        conditions.append(Agent.mode == mode)
    if archived is not None:
        conditions.append(Agent.archived_at.is_not(None) if archived else Agent.archived_at.is_(None))
    stmt = select(Agent).where(*conditions)
    count_stmt = select(func.count()).select_from(Agent).where(*conditions)
    rows = (await db.execute(stmt.order_by(Agent.created_at.desc()))).scalars().all()
    total = (await db.execute(count_stmt)).scalar_one()
    aggregates = await _session_aggregates(db, ctx, [r.id for r in rows])
    return AgentPage(
        items=[
            to_out(
                r,
                session_count=aggregates.get(r.id, (0, None))[0],
                last_session_at=aggregates.get(r.id, (0, None))[1],
            )
            for r in rows
        ],
        total=total,
    )


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
async def get_agent(
    id_or_slug: str, db: DbDep, ctx: OptionalWorkspaceCtxDep, settings: SettingsDep
) -> AgentOut | AgentPublicOut:
    """Return the member or the public projection of an agent.

    Members of the agent's workspace (``viewer``+, ``agents:read`` for keys)
    get ``AgentOut``. A signed-in caller from another workspace gets the public
    view of a published agent and a 404 otherwise; anonymous callers keep the
    v1 behaviour (public view, or 403 for an unpublished agent).
    """
    row = await load_agent(db, id_or_slug)
    if ctx is not None and ctx.workspace_id == row.workspace_id and ctx.allows(_READ):
        aggregates = await _session_aggregates(db, ctx, [row.id])
        count, last_at = aggregates.get(row.id, (0, None))
        return to_out(row, session_count=count, last_session_at=last_at)
    if not row.published:
        if ctx is not None:
            raise NotFoundError(f"unknown agent '{id_or_slug}'")
        raise ForbiddenError(f"agent '{id_or_slug}' is not published")
    return to_public(row, settings)


@router.put(
    "/{agent_id}",
    response_model=AgentOut,
    summary="Update an agent",
    description=(
        "Partial update. Saving a changed `config` validates it and bumps `config_version`. `mode` is "
        "derived from `config.flow` (nodes ⇒ `flow`); a `mode` that disagrees is a 422."
    ),
)
async def update_agent(agent_id: str, payload: AgentUpdate, db: DbDep, ctx: AdminCtxDep) -> AgentOut:
    """Apply a partial update to an agent."""
    row = await load_scoped_agent(db, ctx, agent_id)
    # R-V2-12: `mode` follows `config.flow`; a disagreeing `mode` is a 422 before anything changes.
    _check_mode(payload.mode, payload.config if payload.config is not None else agent_config_of(row))
    if payload.connection_id is not None:
        row.connection_id = await _workspace_connection_id(db, ctx.workspace_id, payload.connection_id)
    if payload.limits is not None:
        row.limits = payload.limits.model_dump()
    if payload.allowed_origins is not None:
        row.allowed_origins = [origin.strip() for origin in payload.allowed_origins if origin.strip()]
    if payload.name is not None:
        row.name = payload.name
    if payload.description is not None:
        row.description = payload.description
    if payload.ui_panel_id is not None:
        row.ui_panel_id = payload.ui_panel_id
    if payload.config is not None:
        check_endpoint_overrides(ctx, row.config, payload.config.model_dump(mode="json"))
        _raise_if_invalid(
            await validate_stored_config(
                db,
                payload.config,
                workspace_id=row.workspace_id,
                connection_id=row.connection_id,
                pack_id=row.pack_id,
            )
        )
        new_config: dict[str, Any] = payload.config.model_dump(mode="json")
        row.mode = derived_mode(payload.config)
        if new_config != row.config:
            row.config = new_config
            row.config_version += 1
            _snapshot_version(db, row, created_by=ctx.actor.id)
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
    description=(
        "Deletes the agent and its tools. Fails with 409 while sessions still reference it, unless "
        "the agent is archived and `?purge=true` — that cascades its sessions (and their events, "
        "QA and cost rows) first (CONTRACTS-V2 §3.4)."
    ),
)
async def delete_agent(
    agent_id: str,
    db: DbDep,
    ctx: AdminCtxDep,
    purge: bool = Query(default=False, description="Cascade-delete sessions of an archived agent"),
) -> Response:
    """Delete an agent that has no sessions, or purge an archived one that does.

    Raises:
        ConflictError: Sessions reference the agent and either it is not
            archived or `purge` was not set.
    """
    row = await load_scoped_agent(db, ctx, agent_id)
    if purge:
        if row.archived_at is None:
            raise ConflictError("only an archived agent may be purged (archive it first)")
        # `sessions.agent_id` has no `ondelete=CASCADE` (unlike its own child
        # tables, which do) — deleted explicitly rather than relying on the
        # FK, which would otherwise raise the same `IntegrityError` this
        # branch exists to avoid.
        await db.execute(delete(SessionRow).where(SessionRow.agent_id == row.id))
    await db.delete(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise ConflictError("agent still has sessions; delete them first, or archive and purge it") from exc
    log.info("agent_deleted", agent_id=agent_id, purged=purge)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{agent_id}/archive",
    response_model=AgentOut,
    summary="Archive an agent",
    description="Blocks new sessions (`connect` 404s for unprivileged callers) without deleting it.",
)
async def archive_agent(agent_id: str, db: DbDep, ctx: AdminCtxDep) -> AgentOut:
    """Set `archived_at` on an agent that is not already archived."""
    row = await load_scoped_agent(db, ctx, agent_id)
    if row.archived_at is None:
        row.archived_at = utcnow()
        await db.flush()
        log.info("agent_archived", agent_id=agent_id)
    return to_out(row)


@router.post(
    "/{agent_id}/unarchive",
    response_model=AgentOut,
    summary="Unarchive an agent",
    description="Clears `archived_at`, restoring normal `connect`/publish behaviour.",
)
async def unarchive_agent(agent_id: str, db: DbDep, ctx: AdminCtxDep) -> AgentOut:
    """Clear `archived_at`."""
    row = await load_scoped_agent(db, ctx, agent_id)
    if row.archived_at is not None:
        row.archived_at = None
        await db.flush()
        log.info("agent_unarchived", agent_id=agent_id)
    return to_out(row)


@router.get(
    "/{agent_id}/versions",
    response_model=Page[ConfigVersionOut],
    summary="List an agent's config versions",
    description="Every saved snapshot of `config`, newest first (each `config_version`'s full history).",
)
async def list_versions(
    agent_id: str,
    db: DbDep,
    ctx: AdminCtxDep,
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[ConfigVersionOut]:
    """Return a page of an agent's `agent_config_versions`, without their full `config` bodies."""
    # Always the loaded row's id, never the raw path value (which may be a slug).
    agent = await load_scoped_agent(db, ctx, agent_id)
    stmt = select(AgentConfigVersion).where(AgentConfigVersion.agent_id == agent.id)
    count_stmt = (
        select(func.count()).select_from(AgentConfigVersion).where(AgentConfigVersion.agent_id == agent.id)
    )
    rows = (
        (
            await db.execute(
                stmt.order_by(AgentConfigVersion.config_version.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    total = (await db.execute(count_stmt)).scalar_one()
    return Page[ConfigVersionOut](
        items=[
            ConfigVersionOut(
                config_version=r.config_version,
                created_at=r.created_at,
                created_by=r.created_by,
                note=r.note or None,
            )
            for r in rows
        ],
        total=total,
    )


async def _load_version(db: AsyncSession, agent_id: str, config_version: int) -> AgentConfigVersion:
    row = await db.scalar(
        select(AgentConfigVersion).where(
            AgentConfigVersion.agent_id == agent_id, AgentConfigVersion.config_version == config_version
        )
    )
    if row is None:
        raise NotFoundError(f"agent '{agent_id}' has no config version {config_version}")
    return row


@router.get(
    "/{agent_id}/versions/{config_version}",
    response_model=ConfigVersionOut,
    summary="Get one config version",
    description="Includes the full `config` at that version, for a diff view.",
)
async def get_version(agent_id: str, config_version: int, db: DbDep, ctx: AdminCtxDep) -> ConfigVersionOut:
    """Return one version, with its full `config`."""
    agent = await load_scoped_agent(db, ctx, agent_id)
    row = await _load_version(db, agent.id, config_version)
    return ConfigVersionOut(
        config_version=row.config_version,
        created_at=row.created_at,
        created_by=row.created_by,
        note=row.note or None,
        config=AgentConfig.model_validate(row.config),
    )


@router.post(
    "/{agent_id}/versions/{config_version}/restore",
    response_model=AgentOut,
    summary="Restore a config version",
    description="Re-validates the old config and saves it as a *new* version (never rewrites history).",
)
async def restore_version(agent_id: str, config_version: int, db: DbDep, ctx: AdminCtxDep) -> AgentOut:
    """Restore an old version as the agent's current config.

    Raises:
        NotFoundError: Unknown agent or version.
        UnprocessableEntityError: The old config no longer validates (e.g. a
            credential or connection it referenced is gone).
    """
    row = await load_scoped_agent(db, ctx, agent_id)
    version_row = await _load_version(db, row.id, config_version)
    restored = AgentConfig.model_validate(version_row.config)
    check_endpoint_overrides(ctx, row.config, restored.model_dump(mode="json"))
    _raise_if_invalid(
        await validate_stored_config(
            db, restored, workspace_id=row.workspace_id, connection_id=row.connection_id, pack_id=row.pack_id
        )
    )
    new_config = restored.model_dump(mode="json")
    if new_config != row.config:
        row.config = new_config
        row.mode = derived_mode(restored)
        row.config_version += 1
        row.updated_at = utcnow()
        _snapshot_version(db, row, created_by=ctx.actor.id, note=f"restored from version {config_version}")
        await db.flush()
    log.info(
        "agent_version_restored",
        agent_id=agent_id,
        restored_from=config_version,
        new_version=row.config_version,
    )
    return to_out(row)


@router.post(
    "/{agent_id}/validate",
    response_model=ValidationResult,
    summary="Validate an agent configuration",
    description="Re-checks the stored config against the registry, credentials, tools and KBs.",
)
async def validate_agent(agent_id: str, db: DbDep, ctx: AdminCtxDep) -> ValidationResult:
    """Validate the agent's stored configuration."""
    row = await load_scoped_agent(db, ctx, agent_id)
    return await validate_stored_config(
        db,
        agent_config_of(row),
        workspace_id=row.workspace_id,
        connection_id=row.connection_id,
        pack_id=row.pack_id,
    )
