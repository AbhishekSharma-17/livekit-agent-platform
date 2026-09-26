"""``/v1/tool-providers/composio/*``: connected apps (docs/v5/COMPOSIO.md §4, D-V5-C13).

Permissions are declared per route with :func:`lkap_api.auth.deps.require`
(v2 style): reads need ``viewer`` + ``providers:read``; everything that
spends the key on a change, stores a connection or tests a pasted key needs
``admin`` + ``providers:write``. The callback is the one unauthenticated
route: it is bound to its connection by a single-use nonce and verified
against Composio (D-V5-C5), and it answers with a redirect that carries
``connect=ok|error`` and nothing else.

Rotate is the existing ``PUT /v1/credentials/{id}`` on the same row; the
stored-key test is ``POST /v1/credentials/{id}/test`` (both unchanged).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from lkap_contracts.tool_providers import (
    AppActionPage,
    AppActionsPickIn,
    AppActionsPickOut,
    AppConnectIn,
    AppConnectionOut,
    AppConnectionPage,
    AppConnectOut,
    AppKeyTestIn,
    AppKeyTestOut,
    AppReconnectIn,
    AppsStatusOut,
    ConnectionRenameIn,
    ToolkitOut,
    ToolkitPage,
)

from lkap_api.auth.deps import WorkspaceContext, require
from lkap_api.auth.ratelimit import RateLimiterDep, enforce
from lkap_api.deps import DbDep, HttpClientDep, SettingsDep, VaultDep
from lkap_api.errors import NotFoundError
from lkap_api.logging import get_logger
from lkap_api.tool_providers import materialise, service
from lkap_api.tool_providers.adapter import AdapterFactory, ToolProviderAdapter, ToolProviderError
from lkap_api.tool_providers.composio import ComposioAdapter

log = get_logger(__name__)

router = APIRouter(prefix="/v1/tool-providers/composio", tags=["apps"])

ReadCtx = Annotated[WorkspaceContext, Depends(require("viewer", "providers:read"))]
WriteCtx = Annotated[WorkspaceContext, Depends(require("admin", "providers:write"))]

_CACHE_ATTR = "tool_provider_catalog_cache"


def get_adapter_factory(client: HttpClientDep) -> AdapterFactory:
    """Build Composio adapters on the request's guarded HTTP client (tests override this)."""

    def factory(api_key: str) -> ToolProviderAdapter:
        return ComposioAdapter(client, api_key)

    return factory


def get_catalog_cache(request: Request) -> service.CatalogCache:
    """The process's catalogue cache, created on first use."""
    cache: service.CatalogCache | None = getattr(request.app.state, _CACHE_ATTR, None)
    if cache is None:
        cache = service.CatalogCache()
        setattr(request.app.state, _CACHE_ATTR, cache)
    return cache


FactoryDep = Annotated[AdapterFactory, Depends(get_adapter_factory)]
CacheDep = Annotated[service.CatalogCache, Depends(get_catalog_cache)]


# ------------------------------------------------------------------------------ key
@router.get(
    "/status",
    response_model=AppsStatusOut,
    summary="Apps status",
    description=(
        "Whether Apps are on for this workspace, which Composio key is used (fingerprint only), that "
        "key's last test result and time, how many apps are connected and how many tools are paused."
    ),
)
async def get_status(db: DbDep, vault: VaultDep, ctx: ReadCtx) -> AppsStatusOut:
    """The Apps section header."""
    return await service.status(db, vault, ctx)


@router.post(
    "/key/test",
    response_model=AppKeyTestOut,
    summary="Test a Composio key before saving it",
    description=(
        "Checks a pasted key with Composio and reports the project it belongs to and how many apps "
        "it can reach. The key is used for this one check and is never stored or logged. At most "
        "10 checks per workspace per minute."
    ),
)
async def post_key_test(
    payload: AppKeyTestIn,
    ctx: WriteCtx,
    factory: FactoryDep,
    limiter: RateLimiterDep,
) -> AppKeyTestOut:
    """Test a pasted key (never persisted)."""
    await enforce(
        limiter,
        f"apps_key_test:{ctx.workspace_id}",
        capacity=service.KEY_TEST_PER_MIN,
        per_seconds=60,
        what=f"{service.KEY_TEST_PER_MIN} key tests per minute",
    )
    api_key = payload.api_key.strip()
    result = await service.test_key(factory(api_key), api_key=api_key)
    log.info("apps_key_tested", ok=result.ok)
    return result


@router.post(
    "/enable",
    response_model=AppsStatusOut,
    summary="Turn Apps on",
    description="Turns Composio on for this workspace (a key must exist) and resumes the tools that use it.",
)
async def post_enable(db: DbDep, vault: VaultDep, ctx: WriteCtx) -> AppsStatusOut:
    """Enable the provider for the workspace."""
    return await service.set_enabled(db, vault, ctx, enabled=True)


@router.post(
    "/disable",
    response_model=AppsStatusOut,
    summary="Turn Apps off",
    description=(
        "Turns Composio off for this workspace. The key and every connection are kept; the tools that "
        "use them are switched off until Apps are turned on again."
    ),
)
async def post_disable(db: DbDep, vault: VaultDep, ctx: WriteCtx) -> AppsStatusOut:
    """Disable the provider for the workspace."""
    return await service.set_enabled(db, vault, ctx, enabled=False)


# ------------------------------------------------------------------------ catalogue
@router.get(
    "/toolkits",
    response_model=ToolkitPage,
    summary="Browse apps",
    description=(
        "Composio's apps, most used first, with logos, categories and whether this workspace has "
        "connected each one. Search, filter by category and page with `cursor`. Cached for 10 minutes "
        "per key; `refresh=true` reads again. Names and descriptions are vendor text."
    ),
)
async def get_toolkits(
    db: DbDep,
    vault: VaultDep,
    ctx: ReadCtx,
    factory: FactoryDep,
    cache: CacheDep,
    query: Annotated[str | None, Query(max_length=100, description="Search app names")] = None,
    category: Annotated[str | None, Query(max_length=64, description="A category slug")] = None,
    cursor: Annotated[
        str | None, Query(max_length=512, description="`next_cursor` of the previous page")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    connected_only: Annotated[bool, Query(description="Only apps this workspace has connected")] = False,
    refresh: bool = False,
) -> ToolkitPage:
    """A page of apps."""
    return await service.list_toolkits(
        db,
        vault,
        factory,
        cache,
        ctx,
        query=query,
        category=category,
        cursor=cursor,
        limit=limit,
        connected_only=connected_only,
        refresh=refresh,
    )


@router.get(
    "/toolkits/{slug}",
    response_model=ToolkitOut,
    summary="One app",
    description=(
        "One app with the ways it can be connected and, per way, the fields the Connect dialog asks "
        "for, plus the redirect address to register when you bring your own OAuth app."
    ),
)
async def get_toolkit(
    slug: str,
    db: DbDep,
    vault: VaultDep,
    ctx: ReadCtx,
    factory: FactoryDep,
    cache: CacheDep,
    refresh: bool = False,
) -> ToolkitOut:
    """One app's detail."""
    return await service.get_toolkit(db, vault, factory, cache, ctx, slug.lower(), refresh=refresh)


@router.get(
    "/toolkits/{slug}/actions",
    response_model=AppActionPage,
    summary="An app's actions",
    description=(
        "The actions an app offers, each labelled Read, Writes or Destructive, with its input schema. "
        "`important=true` lists Composio's featured actions only. Descriptions are vendor text."
    ),
)
async def get_actions(
    slug: str,
    db: DbDep,
    vault: VaultDep,
    ctx: ReadCtx,
    factory: FactoryDep,
    cache: CacheDep,
    query: Annotated[str | None, Query(max_length=100)] = None,
    important: bool = False,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    refresh: bool = False,
) -> AppActionPage:
    """A page of one app's actions."""
    return await service.list_actions(
        db,
        vault,
        factory,
        cache,
        ctx,
        slug.lower(),
        query=query,
        important=important,
        cursor=cursor,
        limit=limit,
        refresh=refresh,
    )


# ---------------------------------------------------------------------- connections
@router.post(
    "/connections",
    response_model=AppConnectOut,
    status_code=status.HTTP_201_CREATED,
    summary="Connect an app",
    description=(
        "`managed` uses Composio's shared sign-in: open `redirect_url` in a browser tab, sign in, and "
        "the browser comes back to the console. `custom_oauth` does the same with your own OAuth app "
        "(`fields`: `client_id`, `client_secret`). `api_key` connects at once with the app's key "
        "fields. `none` is for apps that need no sign-in. `fields` are passed to Composio once and "
        "never stored or returned. `subject='agent'` makes the connection usable by one agent only. "
        "Connecting an app that is already connected adds another account of it (`label` names it, "
        "e.g. 'Work'); the first account stays the app's default."
    ),
)
async def post_connection(
    payload: AppConnectIn,
    db: DbDep,
    vault: VaultDep,
    settings: SettingsDep,
    ctx: WriteCtx,
    factory: FactoryDep,
) -> AppConnectOut:
    """Start (or finish, for keys) a connection."""
    return await service.connect(db, vault, factory, settings, ctx, payload)


@router.get(
    "/connections",
    response_model=AppConnectionPage,
    summary="List connected apps",
    description="Every connected app of this workspace with its last known status; no Composio call.",
)
async def get_connections(db: DbDep, vault: VaultDep, ctx: ReadCtx) -> AppConnectionPage:
    """The workspace's connections."""
    return await service.list_connections(db, vault, ctx)


@router.get(
    "/connections/{connection_id}",
    response_model=AppConnectionOut,
    summary="Check a connected app",
    description=(
        "Asks Composio for the connection's current state (active, expired, failed, inactive) and "
        "records it. A sign-in still in progress is reported without a call."
    ),
)
async def get_connection(
    connection_id: str, db: DbDep, vault: VaultDep, ctx: ReadCtx, factory: FactoryDep
) -> AppConnectionOut:
    """Refresh and return one connection."""
    return await service.refresh_connection(db, vault, factory, ctx, connection_id)


@router.patch(
    "/connections/{connection_id}",
    response_model=AppConnectionOut,
    summary="Rename an account or make it the app's default",
    description=(
        "`label` renames the account (also at Composio); another account of the same app may not "
        "have the same name. `is_default=true` makes it the app's default account: its tools keep "
        "the plain names and an agent that names no account uses it; the previous default loses "
        "the flag. Tool names already made keep theirs."
    ),
)
async def patch_connection(
    connection_id: str,
    payload: ConnectionRenameIn,
    db: DbDep,
    vault: VaultDep,
    ctx: WriteCtx,
    factory: FactoryDep,
) -> AppConnectionOut:
    """Rename an account and/or make it the default (R-V5-13)."""
    return await service.update_connection(db, vault, factory, ctx, connection_id, payload)


@router.post(
    "/connections/{connection_id}/reconnect",
    response_model=AppConnectOut,
    summary="Reconnect an app",
    description=(
        "A new sign-in link on the same connection (its picked actions and tools stay), or, for a "
        "key-based app, the new key in `fields`."
    ),
)
async def post_reconnect(
    connection_id: str,
    db: DbDep,
    vault: VaultDep,
    settings: SettingsDep,
    ctx: WriteCtx,
    factory: FactoryDep,
    payload: AppReconnectIn | None = None,
) -> AppConnectOut:
    """Reconnect one connection."""
    fields = payload.fields if payload is not None else {}
    return await service.reconnect(db, vault, factory, settings, ctx, connection_id, fields)


@router.delete(
    "/connections/{connection_id}",
    response_model=None,
    summary="Disconnect an app",
    description=(
        "Removes the connection at Composio and switches off the tools that use it; the entry stays "
        "as 'needs reconnect' so Reconnect restores everything. `purge=true` also deletes the entry "
        "(refused while a tool still uses it)."
    ),
    responses={200: {"model": AppConnectionOut}, 204: {"description": "Purged"}},
)
async def delete_connection(
    connection_id: str,
    db: DbDep,
    vault: VaultDep,
    ctx: WriteCtx,
    factory: FactoryDep,
    purge: bool = False,
) -> AppConnectionOut | Response:
    """Disconnect one connection."""
    out = await service.disconnect(db, vault, factory, ctx, connection_id, purge=purge)
    return out if out is not None else Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/callback",
    include_in_schema=True,
    summary="Sign-in return address",
    description=(
        "Where Composio sends the browser after a sign-in. Not for API clients: it checks the "
        "single-use `flow` value, confirms the connection with Composio and redirects to the console's "
        "Apps tab with `connect=ok` or `connect=error`."
    ),
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
)
async def get_callback(
    db: DbDep,
    vault: VaultDep,
    settings: SettingsDep,
    factory: FactoryDep,
    flow: Annotated[str | None, Query(max_length=256)] = None,
    status_: Annotated[str | None, Query(alias="status", max_length=32)] = None,
    connected_account_id: Annotated[str | None, Query(max_length=128)] = None,
) -> RedirectResponse:
    """Finish a hosted sign-in and send the browser back to the console."""
    outcome = await service.handle_callback(
        db, vault, factory, flow=flow, status=status_, connected_account_id=connected_account_id
    )
    return RedirectResponse(
        service.console_redirect(settings, "ok" if outcome.ok else "error"),
        status_code=status.HTTP_302_FOUND,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


# ---------------------------------------------------------------------------- picks
@router.post(
    "/materialise",
    response_model=AppActionsPickOut,
    summary="Pick actions of a connected app and add them as tools",
    description=(
        "Records the actions agents may use from a connected app and turns each into an agent tool "
        "(one per action; picking it again reuses the tool). Every action must belong to the app; an "
        "action that deletes, removes or moves money needs `allow_destructive=true`. With `agent_id` the "
        "tools are attached to that agent (a new configuration version). Read actions run while the "
        "conversation continues; actions that change something wait for their result."
    ),
)
async def post_materialise(
    payload: AppActionsPickIn, db: DbDep, vault: VaultDep, ctx: WriteCtx, factory: FactoryDep
) -> AppActionsPickOut:
    """Store picked actions on the connection and materialise them as ``provider`` tools."""
    return await service.pick_actions(db, vault, factory, ctx, payload)


@router.post(
    "/tools/{tool_id}/refresh-schema",
    response_model=materialise.SchemaRefreshOut,
    summary="Compare an app action tool with Composio's current version",
    description=(
        "Reads the action's current inputs from Composio and lists the fields added, removed or changed "
        "since the tool was created. Nothing changes unless `apply=true`, which writes the new inputs "
        "and version to the tool and, when its app has several accounts, puts the account's name in "
        "front of the description."
    ),
)
async def post_refresh_schema(
    tool_id: str,
    db: DbDep,
    vault: VaultDep,
    ctx: WriteCtx,
    factory: FactoryDep,
    apply: bool = Query(default=False, description="Write the new inputs to the tool"),
) -> materialise.SchemaRefreshOut:
    """Diff (and optionally apply) the pinned schema of one ``provider`` tool."""
    tool = await materialise.load_provider_tool(db, ctx, tool_id)
    definition = tool.definition if isinstance(tool.definition, dict) else {}
    slug = str(definition.get("tool_slug") or "")
    toolkit = str(definition.get("toolkit") or "") or None
    adapter, _ = await service.workspace_adapter(db, vault, factory, ctx.workspace_id)
    try:
        listed = await adapter.list_tools(toolkit=toolkit, tool_slugs=[slug], limit=1)
    except ToolProviderError as exc:
        raise service.api_error(exc) from exc
    items = [service.action_out(item) for item in listed.get("items", []) if isinstance(item, dict)]
    action = next((item for item in items if item.slug.upper() == slug.upper()), None)
    if action is None:
        raise NotFoundError(f"Composio no longer lists the action '{slug}'")
    # R-V5-13: a tool of an app that has gained another account learns its label here.
    records = await service.list_connection_records(db, vault, ctx.workspace_id)
    conn = next((c for c in records if c.id == definition.get("connection_id")), None)
    account = service.account_naming(records, conn) if conn is not None else None
    result = materialise.refresh_result(tool, action, apply=apply, account=account)
    await db.flush()
    log.info("apps_schema_refreshed", tool_id=tool_id, changed=result.changed, applied=result.applied)
    return result
