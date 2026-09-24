"""Provider registry endpoints — the catalogue the console renders forms from.

V2-06 adds this workspace's enablement settings (`ProviderOut`, `PUT
.../settings`) and vendor catalog listings (`GET .../catalog`,
:mod:`lkap_api.catalogs`) on top of V2-00/V2-05's bare registry. Importing
:mod:`lkap_api.catalogs` also registers the workspace-enablement validator
into :mod:`lkap_api.config_service` (see that package's docstring) — this
router is what pulls it into the running app.

V4-07 (docs/v4/CUSTOM-MODELS.md D-V4-23…25): `GET /v1/providers` carries the
model-id rule (`model_id_rules`); the catalog route searches and pages the
cached list (`q`, `limit`, `offset`, `model`) and forwards a search to
OpenRouter only on `search_vendor=true`; `GET/PUT .../models[/{model_id}]`
read the workspace's `provider_models` records and store an admin's declared
capabilities. Importing :mod:`lkap_api.custom_models.validation` registers the
custom-model validator the same way.
"""

from __future__ import annotations

from fastapi import APIRouter, Path, Query
from lkap_contracts.api_models import (
    CatalogResponse,
    ModelIdRules,
    ProviderModelDeclare,
    ProviderModelOut,
    ProviderModelPage,
    ProviderOut,
    ProviderSettingsIn,
    ProvidersResponse,
)
from lkap_contracts.providers import (
    MODEL_KINDS,
    REGISTRY,
    Availability,
    CatalogKind,
    ProviderKind,
    ProviderSpec,
    credential_home,
    get,
    validate_model_id,
)
from sqlalchemy import select

from lkap_api import catalogs
from lkap_api.catalogs import service as catalog_service
from lkap_api.config_service import ConnectionContext, installed_on, installed_provider_ids
from lkap_api.custom_models import records
from lkap_api.custom_models import (
    validation as _custom_model_validation,  # noqa: F401 - registers the validator
)
from lkap_api.db.models import Credential, LiveKitConnection, WorkspaceProvider
from lkap_api.deps import AdminCtxDep, DbDep, HttpClientDep, VaultDep
from lkap_api.errors import NotFoundError, UnprocessableEntityError

router = APIRouter(prefix="/v1/providers", tags=["providers"])


def _spec_or_404(provider_id: str) -> ProviderSpec:
    try:
        return get(provider_id)
    except KeyError as exc:
        raise NotFoundError(f"unknown provider '{provider_id}'") from exc


async def _settings_by_provider(db: DbDep, workspace_id: str) -> dict[str, WorkspaceProvider]:
    """This workspace's `workspace_providers` rows, keyed by provider id."""
    rows = (
        await db.execute(select(WorkspaceProvider).where(WorkspaceProvider.workspace_id == workspace_id))
    ).scalars()
    return {row.provider_id: row for row in rows}


async def _installed_on_map(db: DbDep, workspace_id: str) -> dict[str, list[str]]:
    """``{provider_id: [connection_id, ...]}`` across this workspace's connections.

    Reuses :func:`lkap_api.config_service.installed_on` (the same rule
    `validate_in_db` enforces at save time — registered workers' reported ids
    win, else the connection's `worker_image`) so this list and a slot's
    validation error never disagree.
    """
    connections = (
        (await db.execute(select(LiveKitConnection).where(LiveKitConnection.workspace_id == workspace_id)))
        .scalars()
        .all()
    )
    result: dict[str, list[str]] = {spec.id: [] for spec in REGISTRY}
    for connection in connections:
        ctx = ConnectionContext.from_row(connection, await installed_provider_ids(db, connection.id))
        for spec in REGISTRY:
            if installed_on(spec, ctx):
                result[spec.id].append(connection.id)
    return result


async def _credential_for(
    db: DbDep, *, workspace_id: str, provider_id: str, credential_id: str
) -> Credential | None:
    """The workspace's own credential for `provider_id`, or `None` (never another tenant's row).

    Tenant-scoped in the query itself (not a post-fetch check) so the guard
    `lkap_api.db.guard` will eventually enforce autouse (ask #13) never flags
    this file — the same recipe ask #25 asks of `routers/provider_keys.py`.
    Rows are matched against the provider's credential home (R-V4-7), so an
    `openrouter-stt` lookup finds the key stored under `openrouter-llm`.
    """
    result: Credential | None = await db.scalar(
        select(Credential).where(
            Credential.id == credential_id,
            Credential.workspace_id == workspace_id,
            Credential.provider_id == credential_home(provider_id),
        )
    )
    return result


def _to_out(spec: ProviderSpec, row: WorkspaceProvider | None, installed: list[str]) -> ProviderOut:
    return ProviderOut(
        **spec.model_dump(),
        enabled=bool(row.enabled) if row is not None else True,
        installed_on=installed,
        default_credential_id=row.default_credential_id if row is not None else None,
    )


@router.get(
    "",
    response_model=ProvidersResponse,
    summary="List providers",
    description=(
        "The full provider registry, including `availability != 'available'` entries so the "
        "console can show them as coming soon, plus this workspace's enablement settings and "
        "which of its connections can construct each one."
    ),
)
async def list_providers(
    db: DbDep,
    ctx: AdminCtxDep,
    kind: ProviderKind | None = Query(default=None, description="Filter by registry kind"),  # noqa: B008
    availability: Availability | None = Query(  # noqa: B008
        default=None, description="Filter by availability"
    ),
    enabled: bool | None = Query(default=None, description="Filter by this workspace's enablement"),
) -> ProvidersResponse:
    """Return every registry entry enriched with this workspace's settings."""
    settings_by_id = await _settings_by_provider(db, ctx.workspace_id)
    installed_map = await _installed_on_map(db, ctx.workspace_id)
    out: list[ProviderOut] = []
    for spec in REGISTRY:
        if kind is not None and spec.kind != kind:
            continue
        if availability is not None and spec.availability != availability:
            continue
        row = settings_by_id.get(spec.id)
        is_enabled = bool(row.enabled) if row is not None else True
        if enabled is not None and is_enabled != enabled:
            continue
        out.append(_to_out(spec, row, installed_map.get(spec.id, [])))
    return ProvidersResponse(providers=out, model_id_rules=ModelIdRules())


@router.get(
    "/{provider_id}",
    response_model=ProviderSpec,
    summary="Get one provider",
    description="The spec for a single registry id, including its secret and config fields.",
)
async def get_provider(provider_id: str, _ctx: AdminCtxDep) -> ProviderSpec:
    """Return one provider spec (the bare registry entry; unchanged from v1)."""
    return _spec_or_404(provider_id)


@router.put(
    "/{provider_id}/settings",
    response_model=ProviderOut,
    summary="Enable, disable or set the default credential for a provider",
    description=(
        "A disabled provider is hidden from slot pickers and rejected at validation "
        "(`providers.disabled`, ARCHITECTURE-V2 D-V2-9). Absence of a row means enabled."
    ),
)
async def update_provider_settings(
    provider_id: str, payload: ProviderSettingsIn, db: DbDep, ctx: AdminCtxDep
) -> ProviderOut:
    """Upsert this workspace's `workspace_providers` row for `provider_id`."""
    spec = _spec_or_404(provider_id)
    if payload.default_credential_id is not None:
        credential = await _credential_for(
            db,
            workspace_id=ctx.workspace_id,
            provider_id=spec.id,
            credential_id=payload.default_credential_id,
        )
        if credential is None:
            raise UnprocessableEntityError(
                f"'{payload.default_credential_id}' is not a credential for provider '{spec.id}' "
                "in this workspace"
            )
    row = await db.get(WorkspaceProvider, (ctx.workspace_id, spec.id))
    if row is None:
        row = WorkspaceProvider(
            workspace_id=ctx.workspace_id,
            provider_id=spec.id,
            enabled=payload.enabled,
            default_credential_id=payload.default_credential_id,
        )
        db.add(row)
    else:
        row.enabled = payload.enabled
        row.default_credential_id = payload.default_credential_id
    await db.flush()
    installed = (await _installed_on_map(db, ctx.workspace_id)).get(spec.id, [])
    return _to_out(spec, row, installed)


async def _resolve_credential(
    db: DbDep, *, workspace_id: str, spec: ProviderSpec, credential_id: str | None
) -> Credential | None:
    """The credential a catalog fetch should authenticate with, or `None`.

    Explicit `credential_id` must belong to this workspace and provider (422
    otherwise). With none given: the workspace's configured default, else —
    if the workspace has exactly one credential for this provider — that one.
    An ambiguous or missing credential returns `None` (the catalog endpoint
    then falls back to the static list rather than guessing which key to use).
    """
    if credential_id is not None:
        credential = await _credential_for(
            db, workspace_id=workspace_id, provider_id=spec.id, credential_id=credential_id
        )
        if credential is None:
            raise UnprocessableEntityError(
                f"'{credential_id}' is not a credential for provider '{spec.id}' in this workspace"
            )
        return credential
    settings_row = await db.get(WorkspaceProvider, (workspace_id, spec.id))
    if settings_row is not None and settings_row.default_credential_id is not None:
        return await _credential_for(
            db,
            workspace_id=workspace_id,
            provider_id=spec.id,
            credential_id=settings_row.default_credential_id,
        )
    candidates = (
        (
            await db.execute(
                select(Credential).where(
                    Credential.workspace_id == workspace_id,
                    Credential.provider_id == credential_home(spec),
                )
            )
        )
        .scalars()
        .all()
    )
    return candidates[0] if len(candidates) == 1 else None


@router.get(
    "/{provider_id}/catalog",
    response_model=CatalogResponse,
    summary="List a vendor's models, voices, avatars or personas",
    description=(
        "Runs the registry's `catalog` adapter for this provider, cached for `CatalogSpec.ttl_s` "
        "(default 1 h). A failed or missing vendor call never 5xxs: it falls back to a cached or "
        "static list and reports why in `error`. `refresh=true` bypasses a fresh cache entry. "
        "`q` searches the cached list by id and label, `model` keeps one model's voices, and "
        "`limit`/`offset` page it (`total` counts the matches). `search_vendor=true` forwards `q` to "
        "the vendor's own search for OpenRouter entries only; every other search reads the cache."
    ),
)
async def get_provider_catalog(
    provider_id: str,
    db: DbDep,
    client: HttpClientDep,
    vault: VaultDep,
    ctx: AdminCtxDep,
    kind: CatalogKind | None = Query(  # noqa: B008
        default=None, description="Defaults to the provider's first kind"
    ),
    credential_id: str | None = Query(default=None, description="Defaults to the workspace's default"),
    refresh: bool = Query(default=False, description="Bypass a fresh cache entry"),
    q: str | None = Query(  # noqa: B008
        default=None, max_length=200, description="Case-insensitive substring over item id and label"
    ),
    limit: int = Query(  # noqa: B008
        default=catalog_service.DEFAULT_PAGE_LIMIT,
        ge=1,
        le=catalog_service.MAX_PAGE_LIMIT,
        description="Page size",
    ),
    offset: int = Query(default=0, ge=0, description="Matches to skip"),  # noqa: B008
    model: str | None = Query(  # noqa: B008
        default=None, max_length=200, description="Keep only items whose `meta.model` is this id (voices)"
    ),
    search_vendor: bool = Query(  # noqa: B008
        default=False, description="Forward `q` to the vendor's search (OpenRouter entries only)"
    ),
) -> CatalogResponse:
    """Return one vendor's catalog for this workspace, searched and paged."""
    spec = _spec_or_404(provider_id)
    if spec.catalog is None:
        raise UnprocessableEntityError(f"provider '{spec.id}' has no catalog")
    resolved_kind = kind or spec.catalog.kinds[0]
    if resolved_kind not in spec.catalog.kinds:
        raise UnprocessableEntityError(f"provider '{spec.id}' has no '{resolved_kind}' catalog")

    credential = await _resolve_credential(
        db, workspace_id=ctx.workspace_id, spec=spec, credential_id=credential_id
    )
    secrets = vault.decrypt(credential.ciphertext) if credential is not None else None
    if search_vendor and q:
        searched = await catalog_service.search_vendor(
            client, spec=spec, kind=resolved_kind, secrets=secrets, query=q
        )
        if searched is not None:
            return catalog_service.page_catalog(searched, model=model, limit=limit, offset=offset)
    full = await catalogs.get_catalog(
        db,
        client,
        spec=spec,
        kind=resolved_kind,
        credential_id=credential.id if credential is not None else None,
        secrets=secrets,
        refresh=refresh,
        workspace_id=ctx.workspace_id,
    )
    return catalog_service.page_catalog(full, query=q, model=model, limit=limit, offset=offset)


# ------------------------------------------------------------------ model records (V4-07)
def _model_spec_or_422(provider_id: str) -> ProviderSpec:
    spec = _spec_or_404(provider_id)
    if spec.kind not in MODEL_KINDS:
        raise UnprocessableEntityError(
            f"provider '{spec.id}' is a {spec.kind} provider and takes no model id"
        )
    return spec


def _checked_model_id(model_id: str) -> str:
    """``model_id`` if it passes the id rule; a 422 that never echoes it otherwise (R-V4-21)."""
    reason = validate_model_id(model_id)
    if reason is not None:
        raise UnprocessableEntityError(f"the model id {reason}")
    return model_id


@router.get(
    "/{provider_id}/models",
    response_model=ProviderModelPage,
    summary="List this workspace's model records for a provider",
    description=(
        "The workspace's `provider_models` rows for this provider's credential home and kind: "
        "declared capabilities, the last Test model result and catalog sightings, most recently "
        "tested first. `custom=true` leaves out the ids the registry itself suggests."
    ),
)
async def list_provider_models(
    provider_id: str,
    db: DbDep,
    ctx: AdminCtxDep,
    custom: bool = Query(default=False, description="Only ids the registry does not list"),  # noqa: B008
    limit: int = Query(  # noqa: B008
        default=records.DEFAULT_LIST_LIMIT, ge=1, le=records.MAX_LIST_LIMIT, description="At most this many"
    ),
) -> ProviderModelPage:
    """Return the workspace's model records for one provider."""
    spec = _model_spec_or_422(provider_id)
    rows = await records.list_rows(
        db, workspace_id=ctx.workspace_id, spec=spec, custom_only=custom, limit=limit
    )
    items = [records.to_out(row) for row in rows]
    return ProviderModelPage(items=items, total=len(items))


@router.get(
    "/{provider_id}/models/{model_id:path}",
    response_model=ProviderModelOut,
    summary="Get this workspace's record of one model id",
    description="404 when the workspace has no record of the id (it was never tested, declared or seen).",
)
async def get_provider_model(
    provider_id: str,
    db: DbDep,
    ctx: AdminCtxDep,
    model_id: str = Path(description="The model id; may contain `/`"),  # noqa: B008
) -> ProviderModelOut:
    """Return one model record."""
    spec = _model_spec_or_422(provider_id)
    row = await records.get_row(
        db, workspace_id=ctx.workspace_id, spec=spec, model_id=_checked_model_id(model_id)
    )
    if row is None:
        raise NotFoundError(f"no record of this model id for provider '{spec.id}'")
    return records.to_out(row)


@router.put(
    "/{provider_id}/models/{model_id:path}",
    response_model=ProviderModelOut,
    summary="Declare what a model can do",
    description=(
        "Stores an admin's `ModelCapabilities` for a (usually custom) model id; declared values win "
        "over a probe's, the live catalog's and the registry's (R-V4-23). The id is checked by the "
        "model-id rule (422 without echo)."
    ),
)
async def declare_provider_model(
    provider_id: str,
    payload: ProviderModelDeclare,
    db: DbDep,
    ctx: AdminCtxDep,
    model_id: str = Path(description="The model id; may contain `/`"),  # noqa: B008
) -> ProviderModelOut:
    """Upsert the workspace's record with the declared capabilities."""
    spec = _model_spec_or_422(provider_id)
    row = await records.declare(
        db,
        workspace_id=ctx.workspace_id,
        spec=spec,
        model_id=_checked_model_id(model_id),
        declared=payload.declared,
    )
    return records.to_out(row)
