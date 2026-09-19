"""Knowledge base CRUD, document ingestion and semantic search (CONTRACTS §7).

Two audiences share this module and are combined into the single `router`
`main.py` discovers by import (see `lkap_api.main._include_knowledge_router`):

* ``/v1/knowledge-bases/...`` — admin surface (`X-Admin-Token`): CRUD, document
  upload (ingested via `BackgroundTasks`, poll for status) and test search.
* ``/internal/v1/kb/search`` — worker surface (`X-Service-Token`): the RAG
  lookup the agent calls for `search_knowledge` and auto-injection.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Request, Response, UploadFile, status
from lkap_contracts import providers
from lkap_contracts.api_models import (
    InternalKbSearchRequest,
    KbCreate,
    KbDocumentOut,
    KbDocumentPage,
    KbOut,
    KbPage,
    KbSearchRequest,
    KbSearchResponse,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import KbDocument, KnowledgeBase, utcnow
from lkap_api.db.session import Database
from lkap_api.deps import AdminDep, DbDep, ServiceDep, SettingsDep, VaultDep
from lkap_api.errors import ConflictError, NotFoundError, UnprocessableEntityError
from lkap_api.kb.embed import Embedder, resolve_embedder
from lkap_api.kb.ingest import run_ingestion_task
from lkap_api.kb.search import search_kbs
from lkap_api.kb.store import VectorStore, get_lancedb_store
from lkap_api.logging import get_logger
from lkap_api.settings import Settings

log = get_logger(__name__)

admin_router = APIRouter(prefix="/v1/knowledge-bases", tags=["knowledge"])
internal_router = APIRouter(prefix="/internal/v1/kb", tags=["internal"])

router = APIRouter()
router.include_router(admin_router)
router.include_router(internal_router)

_VALID_EMBEDDER_IDS = {spec.id for spec in providers.by_kind("embedding", status=None)}

#: DECISIONS-W2.md D-W2-5: the api enforces `1 <= k <= 20` on both search
#: endpoints. Enforced here directly (not only via a contracts `Field` bound)
#: so it holds regardless of the `lkap_contracts` version this api is paired
#: with — the search endpoints are the only guaranteed enforcement point.
MIN_K = 1
MAX_K = 20


# --------------------------------------------------------------------------- dependencies
def get_vector_store(settings: SettingsDep) -> VectorStore:
    """Return the process-wide :class:`~lkap_api.kb.store.LanceDBStore`."""
    return get_lancedb_store(settings.data_dir)


VectorStoreDep = Annotated[VectorStore, Depends(get_vector_store)]


async def get_embedder(settings: SettingsDep, db: DbDep, vault: VaultDep) -> Embedder:
    """Return the active :class:`~lkap_api.kb.embed.Embedder` for ``LKAP_EMBEDDER``."""
    return await resolve_embedder(settings, db, vault)


EmbedderDep = Annotated[Embedder, Depends(get_embedder)]


def get_database(request: Request) -> Database:
    """Return the process's :class:`Database` for the background ingestion task.

    Not `DbDep`: the background task opens its own session after the request's
    session has committed, so a separate connection can see the `pending`
    document row this endpoint just wrote.
    """
    return request.app.state.db  # type: ignore[no-any-return]


DatabaseDep = Annotated[Database, Depends(get_database)]


# --------------------------------------------------------------------------- helpers
def _check_embedder_id(embedder_id: str) -> None:
    if embedder_id not in _VALID_EMBEDDER_IDS:
        raise UnprocessableEntityError(
            f"unknown embedder_id '{embedder_id}'", details={"known": sorted(_VALID_EMBEDDER_IDS)}
        )


def _check_k(k: int) -> None:
    """Reject a hit count outside `[MIN_K, MAX_K]` (DECISIONS-W2.md D-W2-5)."""
    if not (MIN_K <= k <= MAX_K):
        raise UnprocessableEntityError(f"k must be between {MIN_K} and {MAX_K}", details={"k": k})


async def _load_kb(db: AsyncSession, kb_id: str) -> KnowledgeBase:
    row = await db.get(KnowledgeBase, kb_id)
    if row is None:
        raise NotFoundError(f"unknown knowledge base '{kb_id}'")
    return row


async def _load_document(db: AsyncSession, kb_id: str, document_id: str) -> KbDocument:
    row = await db.get(KbDocument, document_id)
    if row is None or row.kb_id != kb_id:
        raise NotFoundError(f"unknown document '{document_id}' in knowledge base '{kb_id}'")
    return row


async def _document_count(db: AsyncSession, kb_id: str) -> int:
    return (
        await db.execute(select(func.count()).select_from(KbDocument).where(KbDocument.kb_id == kb_id))
    ).scalar_one()


async def _kb_out(db: AsyncSession, row: KnowledgeBase) -> KbOut:
    return KbOut(
        id=row.id,
        name=row.name,
        description=row.description,
        embedder_id=row.embedder_id,
        chunk_count=row.chunk_count,
        document_count=await _document_count(db, row.id),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _document_out(row: KbDocument) -> KbDocumentOut:
    return KbDocumentOut(
        id=row.id,
        kb_id=row.kb_id,
        filename=row.filename,
        mime=row.mime,
        bytes=row.bytes,
        status=row.status,
        error=row.error,
        chunk_count=row.chunk_count,
        created_at=row.created_at,
    )


async def _recompute_chunk_count(db: AsyncSession, kb_id: str) -> None:
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        return
    total = (
        await db.execute(
            select(func.coalesce(func.sum(KbDocument.chunk_count), 0)).where(KbDocument.kb_id == kb_id)
        )
    ).scalar_one()
    kb.chunk_count = int(total)


def _persist_upload(settings: Settings, *, kb_id: str, document_id: str, filename: str, data: bytes) -> None:
    """Best-effort raw-file archive under ``LKAP_DATA_DIR/kb/{kb_id}/`` (ARCHITECTURE §5).

    Never blocks ingestion: a write failure here is logged, not raised.
    """
    try:
        directory = Path(settings.data_dir) / "kb" / kb_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{document_id}_{filename}").write_bytes(data)
    except OSError as exc:
        log.warning(
            "kb_document_archive_failed", kb_id=kb_id, document_id=document_id, error_type=type(exc).__name__
        )


# --------------------------------------------------------------------------- knowledge bases
@admin_router.post(
    "",
    response_model=KbOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a knowledge base",
    description="Creates an empty knowledge base; documents are uploaded and ingested separately.",
)
async def create_kb(payload: KbCreate, db: DbDep, _admin: AdminDep) -> KbOut:
    """Create a knowledge base row."""
    _check_embedder_id(payload.embedder_id)
    row = KnowledgeBase(name=payload.name, description=payload.description, embedder_id=payload.embedder_id)
    db.add(row)
    await db.flush()
    log.info("kb_created", kb_id=row.id, name=row.name)
    return await _kb_out(db, row)


@admin_router.get(
    "",
    response_model=KbPage,
    summary="List knowledge bases",
    description="Every knowledge base with its current chunk and document counts.",
)
async def list_kbs(db: DbDep, _admin: AdminDep) -> KbPage:
    """Return every knowledge base row, newest first."""
    rows = (await db.execute(select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()))).scalars().all()
    total = (await db.execute(select(func.count()).select_from(KnowledgeBase))).scalar_one()
    return KbPage(items=[await _kb_out(db, row) for row in rows], total=total)


@admin_router.get(
    "/{kb_id}",
    response_model=KbOut,
    summary="Get a knowledge base",
    description="One knowledge base with its current chunk and document counts.",
)
async def get_kb(kb_id: str, db: DbDep, _admin: AdminDep) -> KbOut:
    """Return one knowledge base row."""
    return await _kb_out(db, await _load_kb(db, kb_id))


@admin_router.put(
    "/{kb_id}",
    response_model=KbOut,
    summary="Update a knowledge base",
    description="Renames a knowledge base; its embedder cannot change once it holds any chunks.",
)
async def update_kb(kb_id: str, payload: KbCreate, db: DbDep, _admin: AdminDep) -> KbOut:
    """Update a knowledge base's name, description and (if empty) embedder."""
    row = await _load_kb(db, kb_id)
    _check_embedder_id(payload.embedder_id)
    if payload.embedder_id != row.embedder_id and row.chunk_count > 0:
        raise ConflictError(
            "embedder_id cannot change once the knowledge base holds chunks; delete and recreate it instead"
        )
    row.name = payload.name
    row.description = payload.description
    row.embedder_id = payload.embedder_id
    row.updated_at = utcnow()
    await db.flush()
    log.info("kb_updated", kb_id=row.id)
    return await _kb_out(db, row)


@admin_router.delete(
    "/{kb_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a knowledge base",
    description="Removes the knowledge base, its documents/chunks and its vector table.",
)
async def delete_kb(kb_id: str, db: DbDep, store: VectorStoreDep, _admin: AdminDep) -> Response:
    """Delete a knowledge base and its LanceDB table."""
    row = await _load_kb(db, kb_id)
    await db.delete(row)
    await db.flush()
    await store.delete_kb(kb_id)
    log.info("kb_deleted", kb_id=kb_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- documents
@admin_router.post(
    "/{kb_id}/documents",
    response_model=KbDocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a document",
    description=(
        "Accepts a `.md`/`.txt`/`.pdf` file, stores it `pending` and schedules ingestion "
        "(chunk, embed, upsert) as a background task. Poll `GET .../documents` for `status`."
    ),
)
async def upload_document(
    kb_id: str,
    db: DbDep,
    settings: SettingsDep,
    database: DatabaseDep,
    store: VectorStoreDep,
    embedder: EmbedderDep,
    background_tasks: BackgroundTasks,
    _admin: AdminDep,
    file: Annotated[UploadFile, File(description="A .md, .txt or .pdf source document")],
) -> KbDocumentOut:
    """Create a pending document row and schedule its ingestion."""
    kb = await _load_kb(db, kb_id)
    data = await file.read()
    filename = file.filename or "document"
    mime = file.content_type or "application/octet-stream"

    document = KbDocument(kb_id=kb.id, filename=filename, mime=mime, bytes=len(data), status="pending")
    db.add(document)
    # Committed explicitly (not just flushed): the background task below opens
    # a *new* connection and, with `PRAGMA foreign_keys=ON`, needs this row to
    # already be durable before it can insert chunks that reference it.
    await db.commit()
    await db.refresh(document)

    await asyncio.to_thread(
        _persist_upload, settings, kb_id=kb.id, document_id=document.id, filename=filename, data=data
    )
    background_tasks.add_task(
        run_ingestion_task,
        database,
        store=store,
        embedder=embedder,
        kb_id=kb.id,
        document_id=document.id,
        filename=filename,
        mime=mime,
        data=data,
    )
    log.info("kb_document_uploaded", kb_id=kb.id, document_id=document.id, filename=filename, bytes=len(data))
    return _document_out(document)


@admin_router.get(
    "/{kb_id}/documents",
    response_model=KbDocumentPage,
    summary="List documents",
    description="Every document uploaded to this knowledge base and its ingestion status.",
)
async def list_documents(kb_id: str, db: DbDep, _admin: AdminDep) -> KbDocumentPage:
    """Return every document row for a knowledge base, newest first."""
    await _load_kb(db, kb_id)
    rows = (
        (
            await db.execute(
                select(KbDocument).where(KbDocument.kb_id == kb_id).order_by(KbDocument.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    total = (
        await db.execute(select(func.count()).select_from(KbDocument).where(KbDocument.kb_id == kb_id))
    ).scalar_one()
    return KbDocumentPage(items=[_document_out(row) for row in rows], total=total)


@admin_router.delete(
    "/{kb_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document",
    description="Removes the document, its chunks (cascade) and its vectors.",
)
async def delete_document(
    kb_id: str, document_id: str, db: DbDep, store: VectorStoreDep, _admin: AdminDep
) -> Response:
    """Delete one document and its vectors."""
    document = await _load_document(db, kb_id, document_id)
    await db.delete(document)
    await db.flush()
    await store.delete_document(kb_id, document_id)
    await _recompute_chunk_count(db, kb_id)
    log.info("kb_document_deleted", kb_id=kb_id, document_id=document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- search
@admin_router.post(
    "/{kb_id}/search",
    response_model=KbSearchResponse,
    summary="Test search a knowledge base",
    description="Runs the same retrieval the agent uses, for the console's Knowledge tab.",
)
async def search_kb(
    kb_id: str,
    payload: KbSearchRequest,
    db: DbDep,
    store: VectorStoreDep,
    embedder: EmbedderDep,
    _admin: AdminDep,
) -> KbSearchResponse:
    """Search one knowledge base."""
    _check_k(payload.k)
    await _load_kb(db, kb_id)
    hits = await search_kbs(db, store, embedder, kb_ids=[kb_id], query=payload.query, k=payload.k)
    return KbSearchResponse(hits=hits)


@internal_router.post(
    "/search",
    response_model=KbSearchResponse,
    summary="Search knowledge bases (worker only)",
    description="Cross-KB retrieval the agent calls for `search_knowledge` and RAG auto-injection.",
)
async def internal_search_kb(
    payload: InternalKbSearchRequest,
    db: DbDep,
    store: VectorStoreDep,
    embedder: EmbedderDep,
    _service: ServiceDep,
) -> KbSearchResponse:
    """Search across the given knowledge bases (worker-only)."""
    _check_k(payload.k)
    hits = await search_kbs(db, store, embedder, kb_ids=payload.kb_ids, query=payload.query, k=payload.k)
    return KbSearchResponse(hits=hits)
