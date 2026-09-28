"""LiveKit connection CRUD, test, rotate, default, worker-env and deploy bundles.

CONTRACTS-V2 §3.4 "Connections". Secrets are write-only: responses carry a
fingerprint (``…`` + last four of the key), never the key or secret. The fleet
routes under the same prefix (``GET/POST /v1/connections/{id}/fleet``) live in
:mod:`lkap_api.routers.fleet` (V2-04).

Workspace scoping: every route resolves the caller's workspace through
:func:`current_workspace_id`, which reads V2-02's ``WorkspaceContext``
(cookie session, API key or break-glass token; ``X-Workspace`` selects the
workspace). Roles and API-key scopes follow ``lkap_api.auth.roles.ROUTE_POLICY``:
reads need ``viewer`` + ``connections:read``, every write ``admin`` +
``connections:write``. A connection of another workspace is a 404.
"""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from lkap_contracts.api_models import (
    ConnectionCreate,
    ConnectionOut,
    ConnectionPage,
    ConnectionRotateIn,
    ConnectionTestResult,
    ConnectionUpdate,
)

from lkap_api.auth.deps import AUDIT_PAYLOAD_STATE
from lkap_api.connections import bundle, service
from lkap_api.connections.clients import ClientFactoryDep
from lkap_api.connections.probe import probe_connection
from lkap_api.deps import AdminCtxDep, AdminDep, DbDep, SettingsDep, VaultDep
from lkap_api.errors import ConflictError
from lkap_api.fleet import readiness
from lkap_api.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/v1/connections", tags=["connections"])


def current_workspace_id(ctx: AdminCtxDep) -> str:
    """The caller's workspace id, from V2-02's role-checked ``WorkspaceContext``."""
    return ctx.workspace_id


WorkspaceIdDep = Annotated[str, Depends(current_workspace_id)]


@router.get(
    "",
    response_model=ConnectionPage,
    summary="List connections",
    description=(
        "The workspace's LiveKit connections, default first. Secrets are reduced to a fingerprint. "
        "`ready_workers` counts each connection's workers that are ready and were heard from within 90 s."
    ),
)
async def list_connections(
    db: DbDep,
    vault: VaultDep,
    workspace_id: WorkspaceIdDep,
    _admin: AdminDep,
    limit: int = Query(default=25, ge=1, le=200, description="Maximum rows to return"),
    offset: int = Query(default=0, ge=0, description="Rows to skip"),
) -> ConnectionPage:
    """Return one page of connections."""
    rows, total = await service.list_connections(db, workspace_id, limit=limit, offset=offset)
    ready = await readiness.ready_worker_counts(db, [row.id for row in rows])
    return ConnectionPage(
        items=[service.to_out(row, vault, ready_workers=ready.get(row.id, 0)) for row in rows], total=total
    )


@router.post(
    "",
    response_model=ConnectionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a connection",
    description=(
        "Stores a LiveKit Cloud project or self-hosted server. `api_key`/`api_secret` are encrypted "
        "at rest and never returned. The first connection of a workspace becomes its default. "
        "Run `POST /v1/connections/{id}/test` to probe capabilities. 409 `agent_name_in_use` "
        '(`details.field = "agent_name"`) when another connection, in any workspace, already uses '
        "the agent name on the same LiveKit server: LiveKit would split calls between them."
    ),
)
async def create_connection(
    payload: ConnectionCreate,
    request: Request,
    db: DbDep,
    vault: VaultDep,
    settings: SettingsDep,
    workspace_id: WorkspaceIdDep,
    _admin: AdminDep,
) -> ConnectionOut:
    """Create a connection."""
    row = await service.create_connection(db, vault, workspace_id, payload, settings.packs_list)
    _audit_destination(request, payload)
    return service.to_out(row, vault)


def _audit_destination(request: Request, payload: ConnectionCreate) -> None:
    """R-V5-17 (S5-14): the request's audit row names the host and deployment type it reaches."""
    setattr(
        request.state,
        AUDIT_PAYLOAD_STATE,
        {
            "host": (urlsplit(str(payload.url)).hostname or "")[:253],
            "deployment_type": payload.deployment_type,
        },
    )


@router.post(
    "/test",
    response_model=ConnectionTestResult,
    summary="Test unsaved connection details",
    description=(
        "Probes a url/key/secret before it is saved (the create form's first test): "
        "`RoomService.list_rooms` decides `ok`, then SIP/Egress/Ingress list calls set capability "
        "flags. Nothing is stored. Each call has a 5 s budget. The agent name is checked first: "
        "409 `agent_name_in_use` when another connection already uses it on the same LiveKit server."
    ),
)
async def test_unsaved_connection(
    payload: ConnectionCreate,
    request: Request,
    db: DbDep,
    vault: VaultDep,
    factory: ClientFactoryDep,
    workspace_id: WorkspaceIdDep,
    _admin: AdminDep,
) -> ConnectionTestResult:
    """Probe connection details without saving them (the destination is audited, S5-14).

    Raises:
        AgentNameInUseError: V6-27 — the agent name clashes on this server; the form shows
            it under the Agent name field before anything is saved.
    """
    _audit_destination(request, payload)
    unsaved = service.UnsavedConnection.from_create(vault, payload)
    await service.ensure_agent_name_free(
        db, workspace_id=workspace_id, url=unsaved.url, agent_name=payload.agent_name
    )
    return await probe_connection(
        factory, unsaved, deployment_type=payload.deployment_type, use_inference=payload.use_inference
    )


@router.get(
    "/{connection_id}",
    response_model=ConnectionOut,
    summary="Get a connection",
    description=(
        "One connection by id or slug, with its effective capability flags and `ready_workers` "
        "(its workers that are ready and were heard from within 90 s)."
    ),
)
async def get_connection(
    connection_id: str, db: DbDep, vault: VaultDep, workspace_id: WorkspaceIdDep, _admin: AdminDep
) -> ConnectionOut:
    """Return one connection."""
    row = await service.get_connection(db, workspace_id, connection_id)
    ready = await readiness.ready_worker_counts(db, [row.id])
    return service.to_out(row, vault, ready_workers=ready.get(row.id, 0))


@router.put(
    "/{connection_id}",
    response_model=ConnectionOut,
    summary="Update a connection",
    description=(
        "Partial update of non-secret fields (secrets change through `rotate`). Changing the url "
        "resets `status` to `unverified`; any change that alters the worker inputs updates the "
        "pool's desired-state hash. A changed url or agent name gets the create route's "
        "409 `agent_name_in_use` check."
    ),
)
async def update_connection(
    connection_id: str,
    payload: ConnectionUpdate,
    db: DbDep,
    vault: VaultDep,
    settings: SettingsDep,
    workspace_id: WorkspaceIdDep,
    _admin: AdminDep,
) -> ConnectionOut:
    """Update a connection."""
    row = await service.update_connection(db, workspace_id, connection_id, payload, settings.packs_list)
    return service.to_out(row, vault)


@router.delete(
    "/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a connection",
    description="Deletes a connection. 409 while agents are bound to it or while it is the default.",
)
async def delete_connection(
    connection_id: str,
    db: DbDep,
    factory: ClientFactoryDep,
    workspace_id: WorkspaceIdDep,
    _admin: AdminDep,
) -> Response:
    """Delete a connection that nothing depends on."""
    row = await service.get_connection(db, workspace_id, connection_id)
    await service.delete_connection(db, workspace_id, row.id)
    factory.invalidate(row.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{connection_id}/test",
    response_model=ConnectionTestResult,
    summary="Test a connection",
    description=(
        "Runs `RoomService.list_rooms` (5 s budget) to validate the stored credentials, then the "
        "SIP/Egress/Ingress list probes for capability flags (their failures do not fail the test). "
        "Stores `status`, `capabilities`, `last_checked_at` and `last_error`. `warnings` (never a "
        "failure) says when another connection uses the same agent name on the same LiveKit server, "
        "or when workers started for another connection run under it."
    ),
)
async def test_connection(
    connection_id: str,
    db: DbDep,
    factory: ClientFactoryDep,
    workspace_id: WorkspaceIdDep,
    _admin: AdminDep,
) -> ConnectionTestResult:
    """Probe a stored connection and record the outcome."""
    row = await service.get_connection(db, workspace_id, connection_id)
    result = await probe_connection(
        factory, row, deployment_type=row.deployment_type, use_inference=bool(row.use_inference)
    )
    await service.record_test_result(db, row, result)
    log.info(
        "connection_tested",
        connection_id=row.id,
        ok=result.ok,
        latency_ms=result.latency_ms,
        sip_enabled=result.capabilities.sip_enabled,
        egress_enabled=result.capabilities.egress_enabled,
        ingress_enabled=result.capabilities.ingress_enabled,
    )
    # V6-27: non-blocking — a clash that predates the check, or another connection's workers
    # registered under this name, still splits this connection's calls.
    users = await service.agent_name_users(
        db, workspace_id=workspace_id, url=row.url, agent_name=row.agent_name, exclude_id=row.id
    )
    warnings = readiness.agent_name_warnings(
        row.agent_name,
        clash=bool(users),
        shared_workers=await readiness.shared_agent_name_workers(db, row),
    )
    if not result.ok:
        # A failed probe keeps the stored flags; report those rather than the blanked ones.
        return result.model_copy(update={"capabilities": service.capabilities_of(row), "warnings": warnings})
    return result.model_copy(update={"warnings": warnings})


@router.post(
    "/{connection_id}/rotate",
    response_model=ConnectionOut,
    summary="Rotate a connection's credentials",
    description=(
        "Replaces the API key/secret, bumps `credentials_version` and the pool's desired-state "
        "hash (a supervised pool drains and restarts with the new secret). Status returns to "
        "`unverified` until the next test."
    ),
)
async def rotate_connection(
    connection_id: str,
    payload: ConnectionRotateIn,
    db: DbDep,
    vault: VaultDep,
    factory: ClientFactoryDep,
    settings: SettingsDep,
    workspace_id: WorkspaceIdDep,
    _admin: AdminDep,
) -> ConnectionOut:
    """Rotate credentials."""
    row = await service.rotate_credentials(
        db,
        vault,
        workspace_id,
        connection_id,
        api_key=payload.api_key,
        api_secret=payload.api_secret,
        packs=settings.packs_list,
    )
    factory.invalidate(row.id)
    return service.to_out(row, vault)


@router.post(
    "/{connection_id}/default",
    response_model=ConnectionOut,
    summary="Make a connection the default",
    description="The default connection receives new and unbound agents. Exactly one per workspace.",
)
async def set_default_connection(
    connection_id: str,
    db: DbDep,
    vault: VaultDep,
    settings: SettingsDep,
    workspace_id: WorkspaceIdDep,
    _admin: AdminDep,
) -> ConnectionOut:
    """Set the workspace default connection."""
    row = await service.set_default_connection(db, workspace_id, connection_id, settings.packs_list)
    return service.to_out(row, vault)


@router.get(
    "/{connection_id}/worker-env",
    response_class=PlainTextResponse,
    summary="Worker environment template",
    description=(
        "The environment an `external` worker pool for this connection needs, as a dotenv file "
        "(`format=env`), a docker compose service (`compose`) or LiveKit CLI + shell commands "
        "(`lk`). Secrets are `<NAME>` placeholders; the api never returns them."
    ),
)
async def worker_env_template(
    connection_id: str,
    db: DbDep,
    vault: VaultDep,
    settings: SettingsDep,
    workspace_id: WorkspaceIdDep,
    _admin: AdminDep,
    format: Literal["env", "compose", "lk"] = Query(default="env", description="env | compose | lk"),  # noqa: A002
) -> PlainTextResponse:
    """Render the redacted worker environment."""
    row = await service.get_connection(db, workspace_id, connection_id)
    text = bundle.worker_env_template(row, settings, service.connection_fingerprint(vault, row), format)
    return PlainTextResponse(text)


@router.post(
    "/{connection_id}/deploy-bundle",
    response_class=Response,
    summary="Download a LiveKit Cloud deploy bundle",
    description=(
        "A zip with `livekit.toml`, `secrets.env` (secrets as placeholders) and the `lk agent` "
        "commands for a `cloud_hosted` pool. Requires a Cloud connection and a public HTTPS "
        "`LKAP_PUBLIC_BASE_URL` (the hosted worker calls the api over the internet)."
    ),
    responses={200: {"content": {"application/zip": {}}}},
)
async def deploy_bundle(
    connection_id: str,
    db: DbDep,
    vault: VaultDep,
    settings: SettingsDep,
    workspace_id: WorkspaceIdDep,
    _admin: AdminDep,
) -> Response:
    """Build and return the deploy bundle.

    Raises:
        ConflictError: For a self-hosted connection or without a public HTTPS api url.
    """
    row = await service.get_connection(db, workspace_id, connection_id)
    if row.deployment_type != "cloud":
        raise ConflictError("deploy bundles are for LiveKit Cloud connections only")
    if not (settings.public_base_url or "").startswith("https://"):
        raise ConflictError(
            "cloud-hosted workers need LKAP_PUBLIC_BASE_URL set to the api's public https url",
            details={"setting": "LKAP_PUBLIC_BASE_URL"},
        )
    payload = bundle.deploy_bundle(row, settings, service.connection_fingerprint(vault, row))
    log.info("connection_deploy_bundle_built", connection_id=row.id, size=len(payload))
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="lkap-{row.slug}-bundle.zip"'},
    )
