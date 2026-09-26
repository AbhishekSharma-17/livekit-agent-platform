"""MCP sign-in routes (V5-14, research-v4 tools §4.3.3; D-V5-3).

* ``POST /v1/tools/{tool_id}/oauth/start`` — admin + ``providers:write``.
* ``GET /v1/tools/{tool_id}/oauth/status`` — admin.
* ``POST /v1/tools/{tool_id}/oauth/revoke`` — admin + ``providers:write`` (V5-16): revoke
  at the provider (best effort), delete the sign-in.
* ``GET /v1/oauth/mcp/callback`` — **unauthenticated** (the provider's browser
  redirect), bound to its flow by the single-use ``state``.
* ``GET /v1/oauth/mcp/client-metadata.json`` — public: the deployment's Client ID
  Metadata Document, served only when ``LKAP_PUBLIC_BASE_URL`` is a public ``https``
  origin (otherwise 404).

The ``/v1/oauth/mcp/*`` paths sit on their own prefix so a proxy can expose exactly
them when the console itself is private (D-V5-3).

The callback's query carries the authorization ``code`` and ``state``; uvicorn's
access log prints request paths with their query, so :class:`CallbackQueryFilter`
drops the query of this one path from that log (installed once, on import).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Final

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from lkap_contracts.api_models import McpOauthStartIn, McpOauthStartOut, McpOauthStatusOut

from lkap_api.db.session import Database
from lkap_api.deps import AdminCtxDep, DbDep, HttpClientDep, SettingsDep, VaultDep
from lkap_api.errors import BadRequestError, NotFoundError
from lkap_api.mcp_oauth import callback, revoke, service
from lkap_api.mcp_oauth.registration import CALLBACK_PATH, CLIENT_METADATA_PATH, client_metadata_document

router = APIRouter(tags=["mcp-oauth"])

#: Longest ``iss`` / ``error`` value read from the callback query (longer counts as malformed).
_MAX_PARAM_CHARS: Final[int] = 2000
_NO_STORE: Final[dict[str, str]] = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


class CallbackQueryFilter(logging.Filter):
    """Removes the query string of the sign-in callback from uvicorn access-log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite the path argument in place; never drops the record."""
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path = args[2]
            if path.startswith(CALLBACK_PATH) and "?" in path:
                record.args = (*args[:2], path.split("?", 1)[0] + "?[redacted]", *args[3:])
        return True


def install_access_log_filter() -> None:
    """Attach :class:`CallbackQueryFilter` to ``uvicorn.access`` (idempotent)."""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(existing, CallbackQueryFilter) for existing in logger.filters):
        logger.addFilter(CallbackQueryFilter())


install_access_log_filter()


def _bounded(value: str | None, limit: int) -> str | None:
    """``value``, or a marker that matches no issuer and no error code when it is too long."""
    return value if value is None or len(value) <= limit else "\x00"


@router.post(
    "/v1/tools/{tool_id}/oauth/start",
    response_model=McpOauthStartOut,
    summary="Start signing in to an MCP server",
    description=(
        'For an MCP server saved with `auth.kind = "oauth"`: finds the server\'s sign-in provider, '
        "obtains a client id (pre-registered, the deployment's client metadata document, or "
        "dynamic registration) and returns the address to open in the admin's browser, valid "
        "for ten minutes. When the provider supports no automatic registration the answer says "
        "so and gives the return address to register. Needs the admin role (`providers:write` "
        "for API keys). Never returns a token."
    ),
)
async def start(
    tool_id: str,
    db: DbDep,
    vault: VaultDep,
    client: HttpClientDep,
    settings: SettingsDep,
    ctx: AdminCtxDep,
    payload: McpOauthStartIn | None = None,
) -> McpOauthStartOut:
    """Begin a sign-in for one MCP tool."""
    return await service.start_sign_in(
        db, vault, client, settings, ctx, tool_id, payload or McpOauthStartIn()
    )


@router.get(
    "/v1/tools/{tool_id}/oauth/status",
    response_model=McpOauthStatusOut,
    summary="MCP server sign-in status",
    description=(
        "Whether the MCP server is signed in, with the provider, scopes and expiry. No token "
        "material. `worker_supported` is true: sessions use the sign-in through an access "
        "token the api refreshes and hands to the worker."
    ),
)
async def get_status(tool_id: str, db: DbDep, vault: VaultDep, ctx: AdminCtxDep) -> McpOauthStatusOut:
    """Report one MCP tool's sign-in."""
    return await service.sign_in_status(db, vault, ctx, tool_id)


@router.post(
    "/v1/tools/{tool_id}/oauth/revoke",
    response_model=McpOauthStatusOut,
    summary="Disconnect an MCP server's sign-in",
    description=(
        "Revokes the sign-in at the provider (RFC 7009: the refresh token, then the access "
        "token; RFC 7592 deletion of a dynamically registered client no other tool uses), then "
        "deletes the stored sign-in. The provider calls are best effort: the sign-in is deleted "
        "even when the provider cannot be reached. Needs the admin role (`providers:write` for "
        "API keys). Returns the new status (`not_connected`)."
    ),
)
async def post_revoke(
    tool_id: str,
    db: DbDep,
    vault: VaultDep,
    client: HttpClientDep,
    settings: SettingsDep,
    ctx: AdminCtxDep,
) -> McpOauthStatusOut:
    """Disconnect one MCP tool's sign-in."""
    return await revoke.revoke_tool_sign_in(db, vault, client, settings, ctx, tool_id)


@router.get(
    CALLBACK_PATH,
    summary="MCP sign-in return address",
    description=(
        "Where the sign-in provider sends the browser. Not for API clients: it checks the "
        "single-use `state`, the provider's identity (`iss`), exchanges the code and redirects "
        "to the console's tools page with `oauth=ok` or `oauth=error`. A `state` that names no "
        "live sign-in (unknown, used, expired) is answered 400."
    ),
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
)
async def get_callback(
    request: Request,
    vault: VaultDep,
    client: HttpClientDep,
    settings: SettingsDep,
    state: Annotated[str | None, Query()] = None,
    code: Annotated[str | None, Query()] = None,
    iss: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """Finish a sign-in and send the browser back to the console."""
    database: Database = request.app.state.db
    outcome = await callback.handle_callback(
        database,
        vault,
        client,
        settings,
        state=state,
        code=code,
        iss=_bounded(iss, _MAX_PARAM_CHARS),
        error=_bounded(error, _MAX_PARAM_CHARS),
    )
    if outcome.bad_state:
        raise BadRequestError(
            "this sign-in link is not valid any more: start the sign-in again from the console",
            details={"reason": outcome.reason},
        )
    return RedirectResponse(
        callback.console_redirect(settings, outcome), status_code=status.HTTP_302_FOUND, headers=_NO_STORE
    )


@router.get(
    CLIENT_METADATA_PATH,
    summary="OAuth client metadata document",
    description=(
        "The deployment's OAuth Client ID Metadata Document, which sign-in providers fetch to "
        "learn LKAP's name and return address. Served only when `LKAP_PUBLIC_BASE_URL` is a "
        "public https address; otherwise 404."
    ),
    responses={200: {"content": {"application/json": {}}}},
)
async def get_client_metadata(settings: SettingsDep) -> JSONResponse:
    """Serve the client metadata document."""
    document: dict[str, Any] | None = client_metadata_document(settings)
    if document is None:
        raise NotFoundError("no client metadata document: LKAP_PUBLIC_BASE_URL is not a public https address")
    return JSONResponse(document, headers={"Cache-Control": "public, max-age=3600"})


__all__ = ["CallbackQueryFilter", "install_access_log_filter", "router"]
