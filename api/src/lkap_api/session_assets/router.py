"""Routes for stored session files (V5-19, D-V5-35, R-V5-5).

* ``POST /internal/v1/sessions/{id}/assets`` (service token): the worker stores a file
  it received and checked; the api checks it again (type from the bytes, the block's
  limits, the session's caps).
* ``POST /internal/v1/sessions/{id}/assets/from-document`` (service token): copies a
  cited knowledge-base document of the agent's KBs into the session (404 otherwise).
* ``GET /internal/v1/sessions/{id}/assets/{asset_id}/content`` (service token): the
  bytes, for the worker (the display path and ``describe_asset``).
* ``GET /v1/sessions/{id}/assets`` (console, workspace-scoped): the files with a
  time-limited download link each.
* ``GET /v1/sessions/{id}/assets/{asset_id}/content?exp=&sig=``: the local backend's
  signed download; the signature is the authorisation (bound to the workspace, the
  session, the asset and the expiry).

Every response carrying file bytes is ``nosniff`` with a sandboxing CSP; only images
are ``inline``, everything else downloads as an attachment.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Final
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile, status
from lkap_contracts.api_models import SessionAssetFromDocumentIn, SessionAssetOut, SessionAssetPage
from lkap_contracts.ui_protocol import MAX_UPLOAD_BYTES
from sqlalchemy import select

from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.models import SessionAsset
from lkap_api.deps import AdminCtxDep, DbDep, ServiceDep, SettingsDep
from lkap_api.errors import NotFoundError, UnauthorizedError, UnprocessableEntityError
from lkap_api.logging import get_logger
from lkap_api.session_assets.service import (
    asset_out,
    copy_document,
    download_url,
    load_session_any,
    read_capped,
    store_upload,
    verify_asset_url,
)
from lkap_api.storage.deps import StorageDep

__all__ = ["router"]

log = get_logger(__name__)

internal_router = APIRouter(prefix="/internal/v1/sessions", tags=["internal"])
admin_router = APIRouter(prefix="/v1/sessions", tags=["sessions"])

router = APIRouter()

#: Headers on every response that carries a stored file's bytes.
_SAFE_HEADERS: Final[dict[str, str]] = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cache-Control": "private, max-age=300",
    "Referrer-Policy": "no-referrer",
}


def _content_disposition(row: SessionAsset) -> str:
    """``inline`` for images, ``attachment`` for the rest; the sanitised name, RFC 5987 encoded."""
    disposition = "inline" if row.mime.startswith("image/") else "attachment"
    ascii_name = row.name.encode("ascii", "ignore").decode("ascii").replace('"', "") or "file"
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(row.name, safe='')}"


def _content_type(row: SessionAsset) -> str:
    """The stored (sniffed or KB-recorded) type; text is always served as plain UTF-8 text."""
    return "text/plain; charset=utf-8" if row.mime.startswith("text/") else row.mime


def _bytes_response(row: SessionAsset, data: bytes) -> Response:
    headers = {**_SAFE_HEADERS, "Content-Disposition": _content_disposition(row)}
    return Response(content=data, media_type=_content_type(row), headers=headers)


def _parse_meta(raw: str | None) -> dict[str, Any]:
    if raw is None or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise UnprocessableEntityError("meta must be a JSON object") from exc
    if not isinstance(value, dict):
        raise UnprocessableEntityError("meta must be a JSON object")
    return value


async def _load_asset(db: DbDep, session: SessionRow, asset_id: str) -> SessionAsset:
    row = await db.scalar(
        select(SessionAsset).where(
            SessionAsset.id == asset_id,
            SessionAsset.session_id == session.id,
            SessionAsset.workspace_id == session.workspace_id,
        )
    )
    if row is None:
        raise NotFoundError(f"unknown asset '{asset_id}'")
    return row


# --------------------------------------------------------------------------- worker


@internal_router.post(
    "/{session_id}/assets",
    response_model=SessionAssetOut,
    status_code=status.HTTP_201_CREATED,
    summary="Store a session file (worker only)",
    description=(
        "Multipart: `file`, `kind` (`upload`, `frame` or `signature`), optional `name` and `meta` "
        "(a JSON object: `block_id` for an upload, `field`, `caption`, `source`). The file's type is "
        "read from its bytes (photos and PDFs only; never HTML or SVG) and checked against the "
        "block's `accept` and `max_bytes`; a session stores at most 50 files. The bytes go to the "
        "storage backend under `sessions/<id>/`; the response carries the sha256."
    ),
)
async def post_asset(
    session_id: str,
    db: DbDep,
    storage: StorageDep,
    settings: SettingsDep,
    _service: ServiceDep,
    file: Annotated[UploadFile, File(description="The file, max 25 MB")],
    kind: Annotated[str, Form(description="upload | frame | signature")] = "upload",
    name: Annotated[str | None, Form(description="The caller's filename (display only)")] = None,
    meta: Annotated[str | None, Form(description="A JSON object of short strings")] = None,
) -> SessionAssetOut:
    """Store one file the worker already checked, after checking it again."""
    session = await load_session_any(db, session_id)
    data = await read_capped(file, max_bytes=MAX_UPLOAD_BYTES)
    row = await store_upload(
        db,
        storage,
        settings,
        session,
        data=data,
        kind=kind,
        name=name if name is not None else file.filename,
        meta=_parse_meta(meta),
    )
    return asset_out(row)


@internal_router.post(
    "/{session_id}/assets/from-document",
    response_model=SessionAssetOut,
    summary="Copy a cited knowledge-base document into the session (worker only)",
    description=(
        "Copies a document of one of the session agent's knowledge bases into the session's files "
        "(`meta.document_id`), so a tapped citation can open it (R-V5-5). A document the session "
        "already holds is returned as it is (200); a new copy answers 201. 404 for a document "
        "outside the agent's knowledge bases, 415 for one that cannot be shown (only PDFs, images "
        "and Markdown or plain text can)."
    ),
    responses={201: {"model": SessionAssetOut}},
)
async def post_asset_from_document(
    session_id: str,
    payload: SessionAssetFromDocumentIn,
    response: Response,
    db: DbDep,
    storage: StorageDep,
    _service: ServiceDep,
) -> SessionAssetOut:
    """Copy (once) the cited document into the session."""
    session = await load_session_any(db, session_id)
    row, created = await copy_document(db, storage, session, payload.document_id)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return asset_out(row)


@internal_router.get(
    "/{session_id}/assets/{asset_id}/content",
    summary="Read a session file (worker only)",
    description="The stored bytes, for the worker's display path and `describe_asset`.",
    response_class=Response,
)
async def get_asset_content_internal(
    session_id: str, asset_id: str, db: DbDep, storage: StorageDep, _service: ServiceDep
) -> Response:
    """The bytes of one stored file."""
    session = await load_session_any(db, session_id)
    row = await _load_asset(db, session, asset_id)
    try:
        data = await storage.get(row.storage_key)
    except FileNotFoundError as exc:
        raise NotFoundError(f"asset '{asset_id}' is no longer stored") from exc
    return _bytes_response(row, data)


# --------------------------------------------------------------------------- console


@admin_router.get(
    "/{session_id}/assets",
    response_model=SessionAssetPage,
    summary="List a session's files",
    description=(
        "Uploads, pinned frames and copied documents of the session, oldest first, each with a "
        "download link valid for 15 minutes (`url`, `expires_at`)."
    ),
)
async def list_assets(
    session_id: str, request: Request, db: DbDep, storage: StorageDep, settings: SettingsDep, ctx: AdminCtxDep
) -> SessionAssetPage:
    """The session's stored files with signed links (the caller's workspace only)."""
    session = await db.scalar(
        select(SessionRow).where(SessionRow.id == session_id, SessionRow.workspace_id == ctx.workspace_id)
    )
    if session is None:
        raise NotFoundError(f"unknown session '{session_id}'")
    rows = (
        (
            await db.execute(
                select(SessionAsset)
                .where(SessionAsset.session_id == session.id, SessionAsset.workspace_id == ctx.workspace_id)
                .order_by(SessionAsset.created_at, SessionAsset.id)
            )
        )
        .scalars()
        .all()
    )
    base_url = settings.public_base_url or str(request.base_url)
    items: list[SessionAssetOut] = []
    for row in rows:
        url, expires_at = await download_url(storage, settings, row, base_url=base_url)
        items.append(asset_out(row, url=url, expires_at=expires_at))
    return SessionAssetPage(items=items)


@admin_router.get(
    "/{session_id}/assets/{asset_id}/content",
    summary="Download a session file (signed link)",
    description=(
        "The signed link `GET /v1/sessions/{id}/assets` hands out when files are stored on this "
        "server. The signature is the authorisation: it covers the workspace, the session, the "
        "file and the expiry, and expires after 15 minutes."
    ),
    response_class=Response,
)
async def get_asset_content_signed(
    session_id: str,
    asset_id: str,
    db: DbDep,
    storage: StorageDep,
    settings: SettingsDep,
    exp: Annotated[int, Query(description="Expiry, unix seconds")],
    sig: Annotated[str, Query(min_length=64, max_length=64, description="The link's signature")],
) -> Response:
    """Serve one file to the holder of a valid signed link."""
    row = await db.scalar(
        select(SessionAsset)
        .where(SessionAsset.id == asset_id, SessionAsset.session_id == session_id)
        .execution_options(**{CROSS_WORKSPACE_OPTION: True})
    )
    if row is None or not verify_asset_url(
        settings.master_key,
        workspace_id=row.workspace_id,
        session_id=session_id,
        asset_id=asset_id,
        exp=exp,
        sig=sig,
    ):
        # One answer for "unknown" and "bad signature": no way to probe for asset ids.
        raise UnauthorizedError("this download link is invalid or has expired")
    try:
        data = await storage.get(row.storage_key)
    except FileNotFoundError as exc:
        raise NotFoundError("the file is no longer stored") from exc
    return _bytes_response(row, data)


router.include_router(internal_router)
router.include_router(admin_router)
