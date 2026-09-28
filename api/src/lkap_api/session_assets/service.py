"""Stored session files (V5-19, D-V5-35): checks, storage, signed downloads, copies and purges.

Every file is untrusted. :func:`store_upload` re-checks what the worker already
checked: the real media type from the bytes (:func:`lkap_contracts.blocks.sniff_mime`,
never the declared one), the block's ``accept`` and ``max_bytes``, and the
per-session caps. The bytes go to the storage backend under a server-generated
key (``sessions/<session id>/<random id><ext>``, :func:`storage_key_for`); the
caller's filename is only ever a sanitised display name. Nothing here logs a
filename, a document's text or any extracted value: ids, sizes and types only.

V6-12 (D-V6-16): a drawing snapshot is a ``frame`` whose ``meta.source`` is ``"ink"`` (the
worker sets it; the page never does). It is stored only when ``meta.block_id`` names a
``canvas`` block of the session's panel that the caller may draw on
(:func:`lkap_contracts.blocks.canvas_caller_can_draw`, the rule the worker applies too), and
only as a PNG of at most :data:`~lkap_contracts.ui_protocol.MAX_CANVAS_SNAPSHOT_BYTES`; the
session's caps apply as to any file.

V6-29 (S6-32, ask #211): a ``signature`` is stored only when ``meta.source`` is ``"signature"``
and ``meta.block_id`` names a ``signature`` block of the session's panel (the worker sets both
and accepts a picture only while it awaits one), and only as a PNG of at most
:data:`~lkap_contracts.ui_protocol.MAX_SIGNATURE_BYTES`.

Downloads: an S3-compatible backend answers with its own presigned GET (the
existing signed-URL path); the local backend, whose ``/internal/v1/storage/local``
URL no route serves (REVIEW-V2 R2-25; the proxy refuses ``/internal/*`` from
browsers anyway), gets a session-asset URL signed with a key derived from the
master key over ``workspace:session:asset:expiry`` (:func:`sign_asset_url`).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import time
from collections.abc import Iterable
from typing import Any, Final, cast
from urllib.parse import quote, urlencode

from fastapi import UploadFile
from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.api_models import SessionAssetOut
from lkap_contracts.blocks import (
    UPLOAD_EXTENSIONS,
    UploadBlockConfig,
    accept_allows,
    canvas_caller_can_draw,
    safe_filename,
    sniff_mime,
)
from lkap_contracts.ui_protocol import (
    CANVAS_SNAPSHOT_SOURCE,
    MAX_CANVAS_SNAPSHOT_BYTES,
    MAX_SIGNATURE_BYTES,
    MAX_UPLOAD_BYTES,
    SIGNATURE_SOURCE,
    BlockSpec,
    SessionAssetKind,
)
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.costs import config_for_session
from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import (
    Agent,
    AgentKnowledgeBase,
    KbDocument,
    KnowledgeBase,
    SessionAsset,
    new_id,
    utcnow,
)
from lkap_api.db.models import Session as SessionRow
from lkap_api.errors import ApiError, ConflictError, NotFoundError, UnprocessableEntityError
from lkap_api.kb.ingest import upload_storage_key
from lkap_api.logging import get_logger
from lkap_api.packs import get_manifest
from lkap_api.panels import effective_layout
from lkap_api.settings import Settings
from lkap_api.storage.base import StorageBackend, UploadTooLargeError
from lkap_api.storage.local import LocalStorage

__all__ = [
    "ASSET_URL_TTL_S",
    "MAX_ASSETS_PER_SESSION",
    "MAX_SESSION_BYTES",
    "UnsupportedMediaTypeError",
    "asset_out",
    "copy_document",
    "download_url",
    "load_session_any",
    "purge_session_assets",
    "read_capped",
    "sign_asset_url",
    "storage_key_for",
    "store_upload",
    "verify_asset_url",
]

log = get_logger(__name__)

#: The most files one session may store (uploads, frames, copied documents together).
MAX_ASSETS_PER_SESSION: Final[int] = 50
#: The most bytes one session may store.
MAX_SESSION_BYTES: Final[int] = 250 * 1024 * 1024
#: How long a download link is valid.
ASSET_URL_TTL_S: Final[int] = 900
#: A signed local link is refused when its expiry is further out than this (a forged far-future ``exp``).
_MAX_URL_TTL_S: Final[int] = 24 * 3600
#: The kinds the worker's upload route stores; ``document`` only comes from :func:`copy_document`.
_UPLOAD_ROUTE_KINDS: Final[frozenset[str]] = frozenset({"upload", "frame", "signature"})
#: ``meta`` keys the worker may set on an upload (short strings only; never ``document_id``).
_UPLOAD_META_KEYS: Final[frozenset[str]] = frozenset({"block_id", "field", "caption", "source"})
_META_VALUE_MAX_CHARS: Final[int] = 200
#: Text documents a citation may open as-is (R-V5-5), by the KB document's recorded type.
_TEXT_DOCUMENT_MIMES: Final[dict[str, str]] = {
    "text/markdown": "text/markdown",
    "text/x-markdown": "text/markdown",
    "text/plain": "text/plain",
}
_READ_CHUNK: Final[int] = 1024 * 1024
_URL_KEY_INFO: Final[bytes] = b"lkap-session-asset-url-v1"


class UnsupportedMediaTypeError(ApiError):
    """415 — the file is not a type a session may store (sniffed from its bytes)."""

    status_code = 415
    code = "unsupported_media_type"


def storage_key_for(session_id: str, asset_id: str, mime: str) -> str:
    """The storage key of a session file: the session folder, a random id, the type's extension."""
    return f"sessions/{session_id}/{asset_id}{UPLOAD_EXTENSIONS.get(mime, '.bin')}"


def asset_out(
    row: SessionAsset, *, url: str | None = None, expires_at: dt.datetime | None = None
) -> SessionAssetOut:
    """The contract shape of one stored file."""
    meta = {str(k): str(v) for k, v in (row.meta or {}).items()}
    return SessionAssetOut(
        id=row.id,
        session_id=row.session_id,
        kind=cast(SessionAssetKind, row.kind),
        name=row.name,
        mime=row.mime,
        size=row.size,
        sha256=row.sha256,
        meta=meta,
        created_at=row.created_at,
        url=url,
        expires_at=expires_at,
    )


async def read_capped(file: UploadFile, *, max_bytes: int) -> bytes:
    """Read ``file`` in chunks, refusing it as soon as it exceeds ``max_bytes`` (a lying size too).

    Raises:
        UploadTooLargeError: 413 past the cap.
    """
    if file.size is not None and file.size > max_bytes:
        raise UploadTooLargeError(f"the file exceeds {max_bytes} bytes", details={"max_bytes": max_bytes})
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_READ_CHUNK):
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLargeError(f"the file exceeds {max_bytes} bytes", details={"max_bytes": max_bytes})
        chunks.append(chunk)
    return b"".join(chunks)


async def load_session_any(db: AsyncSession, session_id: str) -> SessionRow:
    """A session by id in any workspace (the worker holds the service token, not a membership).

    Raises:
        NotFoundError: An unknown id.
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


async def _session_agent(db: AsyncSession, session: SessionRow) -> Agent | None:
    stmt = select(Agent).where(Agent.id == session.agent_id, Agent.workspace_id == session.workspace_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _panel_blocks(db: AsyncSession, settings: Settings, session: SessionRow) -> list[BlockSpec]:
    """The blocks the session's panel shows (the agent config's, else the pack's default panel)."""
    agent = await _session_agent(db, session)
    if agent is None:
        return []
    return list(effective_layout(agent, get_manifest(settings.packs_list, agent.pack_id)).blocks)


def _clean_meta(meta: dict[str, Any] | None) -> dict[str, str]:
    """The allowed, short ``meta`` entries of an upload; anything else is dropped."""
    cleaned: dict[str, str] = {}
    for key, value in (meta or {}).items():
        if key in _UPLOAD_META_KEYS and isinstance(value, str | int | float | bool):
            cleaned[key] = str(value)[:_META_VALUE_MAX_CHARS]
    return cleaned


def _limits_for(kind: str, block: BlockSpec | None) -> tuple[list[str], int]:
    """``(accept, max_bytes)`` a file of ``kind`` for ``block`` must fit."""
    if kind == "frame" and block is not None and block.type == "canvas":
        # V6-12: a drawing snapshot is a PNG the page rendered.
        return ["image/png"], MAX_CANVAS_SNAPSHOT_BYTES
    if kind == "signature":
        # V6-29 (S6-32): a signature is the PNG the page rendered from its pad, as the worker checks.
        return ["image/png"], MAX_SIGNATURE_BYTES
    if kind == "frame":
        return ["image/*"], MAX_UPLOAD_BYTES
    if block is not None and block.type == "upload":
        try:
            config = UploadBlockConfig.model_validate(block.config)
        except ValidationError:
            config = UploadBlockConfig()
        return list(config.accept), config.max_bytes
    # A form's file field: its limits live in the schema the tool wrote at run time, which
    # the api never sees, so the platform's own ceiling applies (images and PDFs, 25 MiB).
    return [], MAX_UPLOAD_BYTES


async def _drawing_board(
    db: AsyncSession, settings: Settings, session: SessionRow, block_id: str | None
) -> BlockSpec:
    """The canvas a drawing snapshot belongs to, when the caller may draw on it (V6-12).

    Raises:
        UnprocessableEntityError: 422 without a ``block_id``, or when it names no canvas of
            the session's panel the caller may draw on.
    """
    if not block_id:
        raise UnprocessableEntityError("a drawing names its board (meta.block_id)")
    blocks = await _panel_blocks(db, settings, session)
    block = next((b for b in blocks if b.id == block_id), None)
    if block is None or block.type != "canvas" or not canvas_caller_can_draw(block_id, blocks):
        raise UnprocessableEntityError(
            f"'{block_id}' is not a drawing board of this session's panel that the caller may draw on"
        )
    return block


async def _signature_block(
    db: AsyncSession, settings: Settings, session: SessionRow, meta: dict[str, str]
) -> BlockSpec:
    """The signature block a signature picture answers (V6-29, S6-32; ask #211).

    Raises:
        UnprocessableEntityError: 422 unless ``meta.source`` is ``"signature"`` and
            ``meta.block_id`` names a ``signature`` block of the session's panel.
    """
    if meta.get("source") != SIGNATURE_SOURCE:
        raise UnprocessableEntityError(f"a signature is marked as one (meta.source = {SIGNATURE_SOURCE!r})")
    block_id = meta.get("block_id")
    if not block_id:
        raise UnprocessableEntityError("a signature names its block (meta.block_id)")
    block = next((b for b in await _panel_blocks(db, settings, session) if b.id == block_id), None)
    if block is None or block.type != "signature":
        raise UnprocessableEntityError(f"'{block_id}' is not a signature block of this session's panel")
    return block


async def _session_totals(db: AsyncSession, session: SessionRow) -> tuple[int, int]:
    count, total = (
        await db.execute(
            select(func.count(SessionAsset.id), func.coalesce(func.sum(SessionAsset.size), 0)).where(
                SessionAsset.session_id == session.id, SessionAsset.workspace_id == session.workspace_id
            )
        )
    ).one()
    return int(count), int(total)


async def _check_session_room(db: AsyncSession, session: SessionRow, size: int) -> None:
    count, total = await _session_totals(db, session)
    if count >= MAX_ASSETS_PER_SESSION:
        raise ConflictError(
            f"this session already stores {MAX_ASSETS_PER_SESSION} files",
            details={"max_files": MAX_ASSETS_PER_SESSION},
        )
    if total + size > MAX_SESSION_BYTES:
        raise UploadTooLargeError(
            "this session has no room left for the file", details={"max_session_bytes": MAX_SESSION_BYTES}
        )


def _require_open(session: SessionRow) -> None:
    if session.status not in ("created", "active"):
        raise ConflictError(f"session '{session.id}' has ended", details={"status": session.status})


async def _put(
    db: AsyncSession,
    storage: StorageBackend,
    session: SessionRow,
    *,
    data: bytes,
    mime: str,
    kind: str,
    name: str,
    meta: dict[str, str],
) -> SessionAsset:
    asset_id = new_id()
    key = storage_key_for(session.id, asset_id, mime)
    await storage.put(key, data, content_type=mime)
    row = SessionAsset(
        id=asset_id,
        session_id=session.id,
        workspace_id=session.workspace_id,
        kind=kind,
        name=name,
        mime=mime,
        size=len(data),
        storage_key=key,
        sha256=hashlib.sha256(data).hexdigest(),
        meta=meta or None,
        created_at=utcnow(),
    )
    db.add(row)
    try:
        await db.flush()
    except Exception:
        # The row did not land: do not leave an orphaned object behind.
        await storage.delete(key)
        raise
    log.info(
        "session_asset_stored", session_id=session.id, asset_id=asset_id, kind=kind, mime=mime, size=len(data)
    )
    return row


async def store_upload(
    db: AsyncSession,
    storage: StorageBackend,
    settings: Settings,
    session: SessionRow,
    *,
    data: bytes,
    kind: str,
    name: str | None,
    meta: dict[str, Any] | None,
) -> SessionAsset:
    """Check and store one file the worker received (``POST /internal/v1/sessions/{id}/assets``).

    Args:
        db: The request's session.
        storage: The configured storage backend.
        settings: For the pack list (a panel may come from the pack's default).
        session: The session (any workspace; the caller holds the service token).
        data: The file's bytes, already capped at 25 MiB.
        kind: ``upload``, ``frame`` or ``signature``.
        name: The caller's filename (sanitised here, display only).
        meta: ``block_id`` (required for an upload, a drawing and a signature), ``field``,
            ``caption``, ``source`` (``"ink"`` marks a drawing snapshot, V6-12; a signature
            carries ``"signature"``, V6-29).

    Returns:
        The stored row (flushed).

    Raises:
        ConflictError: 409 when the session has ended or already holds 50 files.
        UnprocessableEntityError: 422 for an empty file, an unknown kind, an upload
            without an ``upload`` / ``form`` block of the session's panel, a drawing
            without a canvas of the panel the caller may draw on, or a signature without
            ``meta.source == "signature"`` and a ``signature`` block of the panel.
        UnsupportedMediaTypeError: 415 when the bytes are not an allowed type or not one
            the block accepts.
        UploadTooLargeError: 413 over the block's ``max_bytes`` or the session's room.
    """
    _require_open(session)
    if kind not in _UPLOAD_ROUTE_KINDS:
        raise UnprocessableEntityError(f"kind must be one of {sorted(_UPLOAD_ROUTE_KINDS)}")
    if not data:
        raise UnprocessableEntityError("the file is empty")
    cleaned = _clean_meta(meta)
    block: BlockSpec | None = None
    if kind == "upload":
        block_id = cleaned.get("block_id")
        if not block_id:
            raise UnprocessableEntityError("an upload names the block it answers (meta.block_id)")
        block = next((b for b in await _panel_blocks(db, settings, session) if b.id == block_id), None)
        if block is None or block.type not in ("upload", "form"):
            raise UnprocessableEntityError(
                f"'{block_id}' is not an upload or form block of this session's panel"
            )
    elif kind == "frame" and cleaned.get("source") == CANVAS_SNAPSHOT_SOURCE:
        block = await _drawing_board(db, settings, session, cleaned.get("block_id"))
    elif kind == "signature":
        block = await _signature_block(db, settings, session, cleaned)
    mime = sniff_mime(data)
    accept, max_bytes = _limits_for(kind, block)
    if mime is None or not accept_allows(accept, mime):
        raise UnsupportedMediaTypeError(
            "the file is not a type this session accepts",
            details={"accept": accept or ["image/*", "application/pdf"]},
        )
    if len(data) > max_bytes:
        raise UploadTooLargeError(f"the file exceeds {max_bytes} bytes", details={"max_bytes": max_bytes})
    await _check_session_room(db, session, len(data))
    display = safe_filename(name, fallback=f"{kind}{UPLOAD_EXTENSIONS.get(mime, '')}")
    return await _put(db, storage, session, data=data, mime=mime, kind=kind, name=display, meta=cleaned)


async def _agent_kb_ids(db: AsyncSession, session: SessionRow) -> set[str]:
    """The session agent's knowledge bases: its pinned config's, its current config's and its attachments."""
    ids: set[str] = set()
    pinned = await config_for_session(db, session)
    if pinned is not None:
        ids.update(pinned.knowledge.kb_ids)
    agent = await _session_agent(db, session)
    if agent is not None:
        try:
            ids.update(AgentConfig.model_validate(agent.config).knowledge.kb_ids)
        except ValidationError:
            pass
        attached = await db.execute(
            select(AgentKnowledgeBase.kb_id).where(AgentKnowledgeBase.agent_id == agent.id)
        )
        ids.update(attached.scalars().all())
    return ids


def _document_mime(document: KbDocument, data: bytes) -> str | None:
    """The type a copied KB document is stored as, or ``None`` when a citation cannot show it.

    PDFs and images by their bytes; Markdown and plain text only when the KB recorded
    that type and the bytes are UTF-8 without NUL. Word, HTML and the rest: ``None``.
    """
    sniffed = sniff_mime(data)
    if sniffed is not None:
        return sniffed
    text_mime = _TEXT_DOCUMENT_MIMES.get(document.mime.split(";")[0].strip().lower())
    if text_mime is None or b"\x00" in data:
        return None
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text_mime


async def copy_document(
    db: AsyncSession, storage: StorageBackend, session: SessionRow, document_id: str
) -> tuple[SessionAsset, bool]:
    """Copy a KB document of the session agent's knowledge bases into the session (R-V5-5).

    Idempotent: a document the session already holds is returned as it is.

    Returns:
        ``(row, created)``.

    Raises:
        NotFoundError: 404 when the document is unknown, not in one of the agent's
            knowledge bases (or another workspace's), or its source is gone.
        UnsupportedMediaTypeError: 415 when the document cannot be shown (Word, HTML, ...).
        ConflictError / UploadTooLargeError: the session's caps, as for an upload.
    """
    existing = (
        await db.execute(
            select(SessionAsset).where(
                SessionAsset.session_id == session.id,
                SessionAsset.workspace_id == session.workspace_id,
                SessionAsset.kind == "document",
            )
        )
    ).scalars()
    for row in existing:
        if (row.meta or {}).get("document_id") == document_id:
            return row, False
    kb_ids = await _agent_kb_ids(db, session)
    document = (
        await db.execute(
            select(KbDocument)
            .join(KnowledgeBase, KnowledgeBase.id == KbDocument.kb_id)
            .where(
                KbDocument.id == document_id,
                KnowledgeBase.workspace_id == session.workspace_id,
                KnowledgeBase.id.in_(kb_ids or {""}),
            )
        )
    ).scalar_one_or_none()
    if document is None:
        raise NotFoundError(f"unknown document '{document_id}'")
    try:
        data = await storage.get(upload_storage_key(document.kb_id, document.id, document.filename))
    except FileNotFoundError as exc:
        raise NotFoundError(f"the source of document '{document_id}' is no longer stored") from exc
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadTooLargeError(f"the document exceeds {MAX_UPLOAD_BYTES} bytes")
    mime = _document_mime(document, data)
    if mime is None:
        raise UnsupportedMediaTypeError(
            "this document cannot be shown in the session", details={"reason": "no_preview"}
        )
    await _check_session_room(db, session, len(data))
    name = safe_filename(document.filename, fallback=f"document{UPLOAD_EXTENSIONS.get(mime, '')}")
    row = await _put(
        db,
        storage,
        session,
        data=data,
        mime=mime,
        kind="document",
        name=name,
        meta={"document_id": document.id},
    )
    return row, True


# --------------------------------------------------------------------------- downloads


def _url_key(master_key: str) -> bytes:
    """An HKDF-SHA256 key for download links, derived from (never equal to) the master key."""
    prk = hmac.new(b"lkap-session-assets", master_key.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(prk, _URL_KEY_INFO + b"\x01", hashlib.sha256).digest()


def _url_message(workspace_id: str, session_id: str, asset_id: str, exp: int) -> bytes:
    return f"{workspace_id}:{session_id}:{asset_id}:{exp}".encode()


def sign_asset_url(master_key: str, *, workspace_id: str, session_id: str, asset_id: str, exp: int) -> str:
    """The hex signature of one download link (bound to workspace, session, asset and expiry)."""
    return hmac.new(
        _url_key(master_key), _url_message(workspace_id, session_id, asset_id, exp), hashlib.sha256
    ).hexdigest()


def verify_asset_url(
    master_key: str,
    *,
    workspace_id: str,
    session_id: str,
    asset_id: str,
    exp: int,
    sig: str,
    now: float | None = None,
) -> bool:
    """Whether ``sig`` signs this asset link and ``exp`` is neither past nor implausibly far away."""
    current = time.time() if now is None else now
    if exp < current or exp > current + _MAX_URL_TTL_S:
        return False
    expected = sign_asset_url(
        master_key, workspace_id=workspace_id, session_id=session_id, asset_id=asset_id, exp=exp
    )
    return hmac.compare_digest(expected, sig)


async def download_url(
    storage: StorageBackend, settings: Settings, row: SessionAsset, *, base_url: str
) -> tuple[str, dt.datetime]:
    """A time-limited download link for ``row`` and when it expires.

    S3-compatible storage presigns its own GET. The local backend links to
    ``GET /v1/sessions/{id}/assets/{asset id}/content?exp=&sig=`` on this api
    (``base_url``: the public origin, else the one the request came in on).
    """
    expires_at = utcnow() + dt.timedelta(seconds=ASSET_URL_TTL_S)
    if not isinstance(storage, LocalStorage):
        return await storage.signed_url(row.storage_key, expires_in_s=ASSET_URL_TTL_S), expires_at
    exp = int(time.time()) + ASSET_URL_TTL_S
    sig = sign_asset_url(
        settings.master_key,
        workspace_id=row.workspace_id,
        session_id=row.session_id,
        asset_id=row.id,
        exp=exp,
    )
    path = f"/v1/sessions/{quote(row.session_id)}/assets/{quote(row.id)}/content"
    return f"{base_url.rstrip('/')}{path}?{urlencode({'exp': exp, 'sig': sig})}", expires_at


# --------------------------------------------------------------------------- purge


async def purge_session_assets(
    db: AsyncSession, storage: StorageBackend, rows: Iterable[SessionAsset]
) -> int:
    """Delete the bytes, then the rows, of ``rows``; a storage failure keeps that row for a retry.

    Returns:
        How many files were removed.
    """
    removed = 0
    for row in list(rows):
        try:
            await storage.delete(row.storage_key)
        except Exception:  # noqa: BLE001 - keep the row; the next sweep tries again
            log.warning(
                "session_asset_delete_failed", session_id=row.session_id, asset_id=row.id, exc_info=True
            )
            continue
        await db.delete(row)
        removed += 1
    await db.flush()
    return removed
