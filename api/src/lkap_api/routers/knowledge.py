"""Knowledge base CRUD, document ingestion and semantic search (CONTRACTS §7).

Two audiences share this module and are combined into the single `router`
`main.py` discovers by import (see `lkap_api.main._include_knowledge_router`):

* ``/v1/knowledge-bases/...`` — admin surface (``WorkspaceContext``; scoped to
  the caller's workspace, another workspace's knowledge base is a 404): CRUD,
  document upload or url import (v3, R-V3-14: the api fetches through
  ``net_guard``; either way ingested as a job, poll for status) and test search.
* ``/internal/v1/kb/search`` — worker surface (`X-Service-Token`): the RAG
  lookup the agent calls for `search_knowledge` and auto-injection.

V5-01 adds, on the admin surface: the embedder record on every knowledge base
(`dimension`, `embedder_model`, `chunking`; a query from another embedder is a
`422 kb_embedder_mismatch`), ingest `progress` on documents, the evaluation
set (`GET/PUT .../evals`; the runner is V5-05) and the explicit re-index
(`POST .../reindex`), which re-chunks stored documents so chunks ingested
before V5-01 gain their locators. The response models that carry the new
fields live in contracts since V5-06 (asks #12/#27).

V5-04 (knowledge search): both search routes run
:class:`~lkap_api.kb.service.KnowledgeService` and accept `mode`
(`vector`/`hybrid`), `rerank` (`none`/`local`) and `min_score`, defaulting to
the pre-V5-04 behaviour; every hit carries its locators (`meta`) and the score
of each stage that ran. The worker route skips a knowledge base built by
another embedder with a warning instead of refusing the whole search. Deletes
no longer write the vector store: they delete the SQL rows and enqueue
`kb_delete` (`kb/jobs.py`), so in production only the jobs process writes
vectors (D-V5-12).

V5-05 (the eval harness): ``POST .../evaluate`` runs the evaluation set as a
``kb_evaluate`` job (:mod:`lkap_api.kb.evals`) and returns its id;
``GET .../evaluate/{job_id}`` and ``GET .../evaluate/latest`` read the run's
status and result (recall@k, MRR, every question's outcome) back.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, File, Response, UploadFile, status
from lkap_contracts import providers
from lkap_contracts.api_models import (
    KB_MAX_EVAL_TEXT,
    KB_MAX_EVALS,
    InternalKbSearchRequest,
    KbCreate,
    KbDocumentOut,
    KbEvalOut,
    KbEvalSetIn,
    KbEvalSetOut,
    KbImportIn,
    KbOut,
    KbReindexIn,
    KbReindexOut,
    KbReindexSkipped,
    KbSearchRequest,
)
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import net_guard
from lkap_api.auth.deps import WorkspaceContext
from lkap_api.db.models import KbDocument, KbEval, KnowledgeBase, utcnow
from lkap_api.deps import AdminCtxDep, DbDep, HttpClientDep, ServiceDep, SettingsDep, VaultDep
from lkap_api.errors import ConflictError, NotFoundError, UnprocessableEntityError
from lkap_api.jobs.deps import JobsDep
from lkap_api.jobs.kinds import KB_INGEST
from lkap_api.jobs.service import JobsService
from lkap_api.kb.embed import Embedder, check_kb_embedder, record_kb_embedder, resolve_embedder
from lkap_api.kb.evals import (
    KbEvalRunOut,
    KbEvaluateIn,
    enqueue_kb_evaluate,
    get_run,
    latest_run,
    load_evals,
    run_out,
)
from lkap_api.kb.ingest import (
    IMPORT_SUFFIX,
    ChunkingConfig,
    fetch_import_source,
    import_policy,
    upload_storage_key,
)
from lkap_api.kb.jobs import enqueue_kb_delete
from lkap_api.kb.rerank import Reranker, get_local_reranker
from lkap_api.kb.service import KnowledgeSearchResponse, KnowledgeService
from lkap_api.kb.store import VectorStore, get_lancedb_store
from lkap_api.logging import get_logger
from lkap_api.storage.base import StorageBackend, UploadTooLargeError
from lkap_api.storage.deps import StorageDep

log = get_logger(__name__)

#: REVIEW-FINAL F-29: cap KB uploads so an unbounded file can't be read fully
#: into memory. Enforced while reading (`_read_capped`), not after.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_UPLOAD_READ_CHUNK = 1024 * 1024

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

#: The evaluation set's bounds (one `PUT` replaces the whole set); pinned in contracts.
MAX_EVALS = KB_MAX_EVALS
MAX_EVAL_TEXT = KB_MAX_EVAL_TEXT

#: The file types an upload is ingested as (the rest decode as UTF-8 text).
SUPPORTED_UPLOADS = ".md, .txt, .csv, .json, .pdf, .docx, .pptx, .xlsx and .html"


# --------------------------------------------------------------------------- response models (V5-01)
# The contracts models since V5-06 moved them (asks #12/#27); the api-local names are aliases.
KnowledgeBaseOut = KbOut
KnowledgeDocumentOut = KbDocumentOut
KnowledgeSearchIn = KbSearchRequest
InternalKnowledgeSearchIn = InternalKbSearchRequest


class KnowledgeBasePage(BaseModel):
    """`GET /v1/knowledge-bases`."""

    items: list[KnowledgeBaseOut]
    total: int


class KnowledgeDocumentPage(BaseModel):
    """`GET /v1/knowledge-bases/{id}/documents`."""

    items: list[KnowledgeDocumentOut]
    total: int


# --------------------------------------------------------------------------- dependencies
def get_vector_store(settings: SettingsDep) -> VectorStore:
    """Return the process-wide :class:`~lkap_api.kb.store.LanceDBStore`."""
    return get_lancedb_store(settings.data_dir)


VectorStoreDep = Annotated[VectorStore, Depends(get_vector_store)]


async def get_embedder(settings: SettingsDep, db: DbDep, vault: VaultDep) -> Embedder:
    """Return the active :class:`~lkap_api.kb.embed.Embedder` for ``LKAP_EMBEDDER``."""
    return await resolve_embedder(settings, db, vault)


EmbedderDep = Annotated[Embedder, Depends(get_embedder)]


def get_reranker(settings: SettingsDep) -> Reranker:
    """Return the process-wide local cross-encoder for ``LKAP_RERANK_MODEL`` (loaded on first rerank)."""
    return get_local_reranker(settings.data_dir, settings.rerank_model)


RerankerDep = Annotated[Reranker, Depends(get_reranker)]


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


async def _load_kb(db: AsyncSession, ctx: WorkspaceContext, kb_id: str) -> KnowledgeBase:
    """Load a knowledge base of the caller's workspace (404 for any other workspace)."""
    row = await db.scalar(
        select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.workspace_id == ctx.workspace_id)
    )
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


async def _kb_out(db: AsyncSession, row: KnowledgeBase) -> KnowledgeBaseOut:
    return KnowledgeBaseOut(
        id=row.id,
        name=row.name,
        description=row.description,
        embedder_id=row.embedder_id,
        chunk_count=row.chunk_count,
        document_count=await _document_count(db, row.id),
        created_at=row.created_at,
        updated_at=row.updated_at,
        dimension=row.dimension,
        embedder_model=row.embedder_model,
        chunking=ChunkingConfig.from_json(row.chunking).to_json() if row.chunking is not None else None,
    )


def _document_out(row: KbDocument) -> KnowledgeDocumentOut:
    return KnowledgeDocumentOut(
        id=row.id,
        kb_id=row.kb_id,
        filename=row.filename,
        mime=row.mime,
        bytes=row.bytes,
        status=row.status,
        error=row.error,
        chunk_count=row.chunk_count,
        created_at=row.created_at,
        progress=row.progress,
    )


def _eval_out(row: KbEval) -> KbEvalOut:
    return KbEvalOut(
        id=row.id,
        question=row.question,
        expected_document_id=row.expected_document_id,
        expected_text=row.expected_text,
        tags=list(row.tags or []),
        created_at=row.created_at,
    )


async def _recompute_chunk_count(db: AsyncSession, kb: KnowledgeBase) -> None:
    kb_id = kb.id
    total = (
        await db.execute(
            select(func.coalesce(func.sum(KbDocument.chunk_count), 0)).where(KbDocument.kb_id == kb_id)
        )
    ).scalar_one()
    kb.chunk_count = int(total)


async def _read_capped(file: UploadFile, *, max_bytes: int) -> bytes:
    """Read `file` up to `max_bytes` + 1, stopping as soon as the cap is exceeded.

    REVIEW-FINAL F-29: the previous implementation did `await file.read()` with
    no limit at all. `Content-Length` (checked by the caller) covers the whole
    multipart body, not this one part, so it is a fast pre-check, not the
    enforcement point — this loop is.

    Raises:
        UploadTooLargeError: As soon as more than `max_bytes` have been read.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLargeError(
                f"upload exceeds the {max_bytes} byte limit", details={"max_bytes": max_bytes}
            )
        chunks.append(chunk)
    return b"".join(chunks)


def upload_basename(filename: str | None) -> str:
    """The client's filename reduced to its last path segment (V2-22, REVIEW-V2 R2-26).

    It becomes part of the object key and the stored document name, so any
    directory part (``../../x``, ``C:\\x``, ``/etc/x``) is dropped; an empty or
    dot-only result falls back to ``document``.
    """
    base = PurePosixPath((filename or "").replace("\\", "/")).name.strip()
    return base if base.strip(".") else "document"


def import_filename(url: str, filename: str | None, mime: str) -> str:
    """The stored name of a url-imported document (v3).

    ``filename`` when given, else the url's last path segment, both reduced by
    :func:`upload_basename`. A name without an extension gets its media type's
    (``.md``, ``.txt``, ``.pdf``, ``.json``) so the console can tell them apart.
    """
    name = upload_basename(filename if filename else unquote(urlsplit(url).path))
    if "." not in name.strip("."):
        name += IMPORT_SUFFIX.get(mime, "")
    return name


async def _store_and_enqueue(
    db: AsyncSession,
    storage: StorageBackend,
    jobs: JobsService,
    background_tasks: BackgroundTasks,
    *,
    kb: KnowledgeBase,
    filename: str,
    mime: str,
    data: bytes,
) -> KbDocument:
    """Create the ``pending`` document row, store the bytes and enqueue ingestion.

    Shared by the multipart upload and the url import.
    """
    document = KbDocument(
        kb_id=kb.id, filename=filename, mime=mime, bytes=len(data), status="pending", progress=0.0
    )
    db.add(document)
    # Committed explicitly (not just flushed): the job below opens a *new*
    # connection and, with `PRAGMA foreign_keys=ON`, needs this row to already
    # be durable before it can insert chunks that reference it.
    await db.commit()
    await db.refresh(document)

    storage_key = upload_storage_key(kb.id, document.id, filename)
    await storage.put(storage_key, data, content_type=mime)

    await jobs.enqueue(
        KB_INGEST,
        {
            "kb_id": kb.id,
            "document_id": document.id,
            "storage_key": storage_key,
            "filename": filename,
            "mime": mime,
        },
        background_tasks=background_tasks,
    )
    return document


# --------------------------------------------------------------------------- knowledge bases
@admin_router.post(
    "",
    response_model=KnowledgeBaseOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a knowledge base",
    description=(
        "Creates an empty knowledge base; documents are uploaded and ingested separately. The "
        "configured embedder's model and vector width are recorded on it (`embedder_model`, "
        "`dimension`), and a later query from a different embedder is refused with 422 "
        "`kb_embedder_mismatch`."
    ),
)
async def create_kb(
    payload: KbCreate, db: DbDep, embedder: EmbedderDep, ctx: AdminCtxDep
) -> KnowledgeBaseOut:
    """Create a knowledge base row in the caller's workspace, recording the active embedder."""
    _check_embedder_id(payload.embedder_id)
    row = KnowledgeBase(
        workspace_id=ctx.workspace_id,
        name=payload.name,
        description=payload.description,
        embedder_id=payload.embedder_id,
        chunking=ChunkingConfig().to_json(),
    )
    record_kb_embedder(row, embedder)
    db.add(row)
    await db.flush()
    log.info("kb_created", kb_id=row.id, name=row.name, embedder_model=row.embedder_model)
    return await _kb_out(db, row)


@admin_router.get(
    "",
    response_model=KnowledgeBasePage,
    summary="List knowledge bases",
    description="Every knowledge base with its current chunk and document counts.",
)
async def list_kbs(db: DbDep, ctx: AdminCtxDep) -> KnowledgeBasePage:
    """Return the workspace's knowledge bases, newest first."""
    in_workspace = KnowledgeBase.workspace_id == ctx.workspace_id
    rows = (
        (
            await db.execute(
                select(KnowledgeBase).where(in_workspace).order_by(KnowledgeBase.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    total = (
        await db.execute(select(func.count()).select_from(KnowledgeBase).where(in_workspace))
    ).scalar_one()
    return KnowledgeBasePage(items=[await _kb_out(db, row) for row in rows], total=total)


@admin_router.get(
    "/{kb_id}",
    response_model=KnowledgeBaseOut,
    summary="Get a knowledge base",
    description="One knowledge base with its current chunk and document counts.",
)
async def get_kb(kb_id: str, db: DbDep, ctx: AdminCtxDep) -> KnowledgeBaseOut:
    """Return one knowledge base row."""
    return await _kb_out(db, await _load_kb(db, ctx, kb_id))


@admin_router.put(
    "/{kb_id}",
    response_model=KnowledgeBaseOut,
    summary="Update a knowledge base",
    description="Renames a knowledge base; its embedder cannot change once it holds any chunks.",
)
async def update_kb(kb_id: str, payload: KbCreate, db: DbDep, ctx: AdminCtxDep) -> KnowledgeBaseOut:
    """Update a knowledge base's name, description and (if empty) embedder."""
    row = await _load_kb(db, ctx, kb_id)
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
    description=(
        "Removes the knowledge base, its documents, chunks and evaluation set at once, then removes "
        "its vectors in a background job (with the default in-process jobs, before this returns)."
    ),
)
async def delete_kb(kb_id: str, db: DbDep, jobs: JobsDep, ctx: AdminCtxDep) -> Response:
    """Delete a knowledge base's rows, then enqueue its vector cleanup (D-V5-12: single writer)."""
    row = await _load_kb(db, ctx, kb_id)
    await db.delete(row)
    # Durable before the job runs on its own connection (and before the
    # response): a failed cleanup job leaves only invisible vectors behind.
    await db.commit()
    await enqueue_kb_delete(jobs, kb_id)
    log.info("kb_deleted", kb_id=kb_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- documents
@admin_router.post(
    "/{kb_id}/documents",
    response_model=KnowledgeDocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a document",
    description=(
        f"Accepts a {SUPPORTED_UPLOADS} file (max 25 MB), stores it `pending` and schedules ingestion "
        "as a background job: PDFs are read page by page; Word, PowerPoint, Excel and HTML files are "
        "converted to Markdown (headings kept, tags removed); everything else is read as UTF-8 text. "
        "The text is split at headings, paragraphs and sentences, and every chunk records its heading "
        "path, page and character offsets. Poll `GET .../documents` for `status` and `progress`."
    ),
)
async def upload_document(
    kb_id: str,
    db: DbDep,
    storage: StorageDep,
    jobs: JobsDep,
    background_tasks: BackgroundTasks,
    ctx: AdminCtxDep,
    file: Annotated[UploadFile, File(description=f"A {SUPPORTED_UPLOADS} source document, max 25 MB")],
) -> KnowledgeDocumentOut:
    """Create a pending document row, store the upload, and enqueue its ingestion.

    Raises:
        UploadTooLargeError: 413 when the file exceeds `MAX_UPLOAD_BYTES` (F-29).
    """
    kb = await _load_kb(db, ctx, kb_id)
    content_length = file.size
    if content_length is not None and content_length > MAX_UPLOAD_BYTES:
        raise UploadTooLargeError(
            f"upload exceeds the {MAX_UPLOAD_BYTES} byte limit", details={"max_bytes": MAX_UPLOAD_BYTES}
        )
    data = await _read_capped(file, max_bytes=MAX_UPLOAD_BYTES)
    filename = upload_basename(file.filename)
    mime = file.content_type or "application/octet-stream"

    document = await _store_and_enqueue(
        db, storage, jobs, background_tasks, kb=kb, filename=filename, mime=mime, data=data
    )
    log.info("kb_document_uploaded", kb_id=kb.id, document_id=document.id, filename=filename, bytes=len(data))
    return _document_out(document)


@admin_router.post(
    "/{kb_id}/documents/import",
    response_model=KnowledgeDocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Import a document from a url",
    description=(
        "The api fetches `url` itself — never the caller — through its outbound network guard: "
        "private, loopback, link-local and metadata destinations are refused (422, "
        "`details.reason=blocked_destination`) and redirects are not followed. The body is capped "
        "at 25 MB (413) and must be `text/*`, `application/json` or `application/pdf` (415); an "
        "HTML page is converted to Markdown, never ingested as raw tags. The document is stored "
        "`pending` and ingested as a job exactly like an upload; poll `GET .../documents` for "
        "`status` and `progress`."
    ),
)
async def import_document(
    kb_id: str,
    payload: KbImportIn,
    db: DbDep,
    storage: StorageDep,
    jobs: JobsDep,
    client: HttpClientDep,
    background_tasks: BackgroundTasks,
    ctx: AdminCtxDep,
) -> KnowledgeDocumentOut:
    """Fetch a public url and ingest it like an uploaded document (R-V3-14).

    Raises:
        UnprocessableEntityError: A refused destination or a failed fetch.
        UnsupportedMediaTypeError: 415 for a content type the ingester cannot read.
        UploadTooLargeError: 413 when the body exceeds `MAX_UPLOAD_BYTES`.
    """
    kb = await _load_kb(db, ctx, kb_id)
    url = str(payload.url)
    net_guard.validate_url(url, import_policy(), field_name="url")
    source = await fetch_import_source(client, url, max_bytes=MAX_UPLOAD_BYTES)
    filename = import_filename(url, payload.filename, source.mime)

    document = await _store_and_enqueue(
        db, storage, jobs, background_tasks, kb=kb, filename=filename, mime=source.mime, data=source.data
    )
    log.info(
        "kb_document_imported",
        kb_id=kb.id,
        document_id=document.id,
        filename=filename,
        host=urlsplit(url).hostname,
        bytes=len(source.data),
    )
    return _document_out(document)


@admin_router.get(
    "/{kb_id}/documents",
    response_model=KnowledgeDocumentPage,
    summary="List documents",
    description="Every document uploaded to this knowledge base and its ingestion status.",
)
async def list_documents(kb_id: str, db: DbDep, ctx: AdminCtxDep) -> KnowledgeDocumentPage:
    """Return every document row for a knowledge base, newest first."""
    await _load_kb(db, ctx, kb_id)
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
    return KnowledgeDocumentPage(items=[_document_out(row) for row in rows], total=total)


@admin_router.delete(
    "/{kb_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document",
    description=(
        "Removes the document and its chunks at once (it no longer appears in listings or search), "
        "then removes its vectors in a background job (with the default in-process jobs, before "
        "this returns)."
    ),
)
async def delete_document(
    kb_id: str, document_id: str, db: DbDep, jobs: JobsDep, ctx: AdminCtxDep
) -> Response:
    """Delete one document's rows, then enqueue its vector cleanup (D-V5-12: single writer)."""
    kb = await _load_kb(db, ctx, kb_id)
    document = await _load_document(db, kb_id, document_id)
    await db.delete(document)
    await db.flush()
    await _recompute_chunk_count(db, kb)
    # Durable before the job runs on its own connection (and before the response).
    await db.commit()
    await enqueue_kb_delete(jobs, kb_id, document_id)
    log.info("kb_document_deleted", kb_id=kb_id, document_id=document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- search
@admin_router.post(
    "/{kb_id}/search",
    response_model=KnowledgeSearchResponse,
    summary="Test search a knowledge base",
    description=(
        "Runs the same retrieval the agent uses, for the console's Knowledge tab. `mode` `hybrid` "
        "adds keyword matches (exact policy numbers, form names) fused with the vector list by rank; "
        "`rerank` `local` rescores the top candidates with the local cross-encoder; `min_score` drops "
        "weaker hits (`dropped` counts them). Each hit shows every stage that ran for it "
        "(`vector_score`, `lexical_rank`, `fused_score`, `rerank_score`), `score` is the one that "
        "decided (`score_source`), and `meta` holds its locators (heading path, page, offsets). 422 "
        "`kb_embedder_mismatch` when the knowledge base was built by another embedder than the one "
        "configured now."
    ),
)
async def search_kb(
    kb_id: str,
    payload: KnowledgeSearchIn,
    db: DbDep,
    store: VectorStoreDep,
    embedder: EmbedderDep,
    reranker: RerankerDep,
    ctx: AdminCtxDep,
) -> KnowledgeSearchResponse:
    """Search one knowledge base."""
    _check_k(payload.k)
    check_kb_embedder(await _load_kb(db, ctx, kb_id), embedder)
    service = KnowledgeService(db, store=store, embedder=embedder, reranker=reranker)
    return await service.search(
        [kb_id],
        payload.query,
        payload.k,
        min_score=payload.min_score,
        rerank=payload.rerank,
        mode=payload.mode,
        workspace_id=ctx.workspace_id,
    )


# --------------------------------------------------------------------------- evals (V5-01)
@admin_router.get(
    "/{kb_id}/evals",
    response_model=KbEvalSetOut,
    summary="Get the evaluation set",
    description="The knowledge base's golden questions, in the order they were put.",
)
async def get_evals(kb_id: str, db: DbDep, ctx: AdminCtxDep) -> KbEvalSetOut:
    """Return the stored evaluation set."""
    await _load_kb(db, ctx, kb_id)
    rows = await load_evals(db, kb_id)
    return KbEvalSetOut(items=[_eval_out(row) for row in rows], total=len(rows))


@admin_router.put(
    "/{kb_id}/evals",
    response_model=KbEvalSetOut,
    summary="Replace the evaluation set",
    description=(
        f"Replaces every golden question of the knowledge base (at most {MAX_EVALS}). Each names the "
        "`expected_document_id` (a document of this knowledge base), an `expected_text` a correct "
        "hit contains, or both. An empty list clears the set."
    ),
)
async def put_evals(kb_id: str, payload: KbEvalSetIn, db: DbDep, ctx: AdminCtxDep) -> KbEvalSetOut:
    """Replace the evaluation set.

    Raises:
        UnprocessableEntityError: An `expected_document_id` is not a document of this knowledge base.
    """
    await _load_kb(db, ctx, kb_id)
    expected = {item.expected_document_id for item in payload.items if item.expected_document_id is not None}
    if expected:
        known = set(
            (
                await db.execute(
                    select(KbDocument.id).where(KbDocument.kb_id == kb_id, KbDocument.id.in_(expected))
                )
            )
            .scalars()
            .all()
        )
        unknown = sorted(expected - known)
        if unknown:
            raise UnprocessableEntityError(
                "expected_document_id must name a document of this knowledge base",
                details={"field": "expected_document_id", "unknown": unknown},
            )
    await db.execute(delete(KbEval).where(KbEval.kb_id == kb_id))
    rows = [
        KbEval(
            kb_id=kb_id,
            question=item.question,
            expected_document_id=item.expected_document_id,
            expected_text=item.expected_text,
            tags=list(item.tags),
            ordinal=ordinal,
        )
        for ordinal, item in enumerate(payload.items)
    ]
    db.add_all(rows)
    await db.flush()
    log.info("kb_evals_replaced", kb_id=kb_id, count=len(rows))
    return KbEvalSetOut(items=[_eval_out(row) for row in rows], total=len(rows))


# --------------------------------------------------------------------------- evaluate (V5-05)
@admin_router.post(
    "/{kb_id}/evaluate",
    response_model=KbEvalRunOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Evaluate retrieval",
    description=(
        "Runs every golden question of the evaluation set through the same search the agent uses, "
        "with the given `mode`, `rerank`, `min_score` and `k` (by default an agent's defaults: "
        "hybrid, no rerank, no floor, top 4), as a background job. A question is found when one of "
        "the top `k` hits is its expected document or contains its expected text. The run reports "
        "`recall_at_k` (found / scored), `recall_at_1`, `mrr` (mean reciprocal rank), the same per "
        "tag, and every question's rank and top hits; a question whose expected document was deleted "
        "is `skipped` and left out of the averages. Poll `GET .../evaluate/{job_id}` until `status` "
        "is `done`; `GET .../evaluate/latest` returns the last finished run. 422 when the set is "
        "empty or the knowledge base was built by another embedder than the one configured now."
    ),
)
async def evaluate_kb(
    kb_id: str,
    db: DbDep,
    embedder: EmbedderDep,
    jobs: JobsDep,
    background_tasks: BackgroundTasks,
    ctx: AdminCtxDep,
    payload: KbEvaluateIn | None = None,
) -> KbEvalRunOut:
    """Enqueue an evaluation run of the knowledge base's evaluation set.

    Raises:
        UnprocessableEntityError: The evaluation set is empty, or the embedder does not match.
    """
    options = payload or KbEvaluateIn()
    _check_k(options.k)
    kb = await _load_kb(db, ctx, kb_id)
    check_kb_embedder(kb, embedder)
    count_query = select(func.count()).select_from(KbEval).where(KbEval.kb_id == kb_id)
    count = (await db.execute(count_query)).scalar_one()
    if count == 0:
        raise UnprocessableEntityError(
            "the knowledge base has no evaluation set; add golden questions with PUT .../evals first",
            details={"field": "evals"},
        )
    now = utcnow()
    job_id = await enqueue_kb_evaluate(
        jobs, kb_id=kb_id, workspace_id=ctx.workspace_id, options=options, background_tasks=background_tasks
    )
    log.info("kb_evaluate_queued", kb_id=kb_id, job_id=job_id, evals=count, mode=options.mode, k=options.k)
    return KbEvalRunOut(
        job_id=job_id, kb_id=kb_id, status="pending", options=options, created_at=now, updated_at=now
    )


@admin_router.get(
    "/{kb_id}/evaluate/latest",
    response_model=KbEvalRunOut,
    summary="Latest evaluation",
    description="The most recently finished evaluation run of this knowledge base; 404 when none has.",
)
async def latest_evaluation(kb_id: str, db: DbDep, ctx: AdminCtxDep) -> KbEvalRunOut:
    """Return the last ``done`` evaluation run.

    Raises:
        NotFoundError: No evaluation of this knowledge base has finished.
    """
    await _load_kb(db, ctx, kb_id)
    row = await latest_run(db, kb_id)
    if row is None:
        raise NotFoundError(f"knowledge base '{kb_id}' has no finished evaluation")
    return run_out(row)


@admin_router.get(
    "/{kb_id}/evaluate/{job_id}",
    response_model=KbEvalRunOut,
    summary="Get an evaluation run",
    description=(
        "One evaluation run's status (`pending`, `running`, `done`, `failed`, `dead`) and, once "
        "`done`, its result."
    ),
)
async def get_evaluation(kb_id: str, job_id: str, db: DbDep, ctx: AdminCtxDep) -> KbEvalRunOut:
    """Return one evaluation run of this knowledge base.

    Raises:
        NotFoundError: ``job_id`` is not an evaluation run of this knowledge base.
    """
    await _load_kb(db, ctx, kb_id)
    row = await get_run(db, kb_id, job_id)
    if row is None:
        raise NotFoundError(f"unknown evaluation run '{job_id}' of knowledge base '{kb_id}'")
    return run_out(row)


# --------------------------------------------------------------------------- re-index (V5-01)
@admin_router.post(
    "/{kb_id}/reindex",
    response_model=KbReindexOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-index documents",
    description=(
        "Re-extracts, re-chunks and re-embeds stored documents with the current chunker, so chunks "
        "ingested before heading/page locators existed gain them. Each queued document is `pending` "
        "until its job finishes, and its previous chunks stay searchable until the new ones replace "
        "them. Documents whose source file was never stored (seeded from a pack or template) and "
        "documents still being ingested are skipped and listed with the reason. Nothing is "
        "re-indexed unless this is called."
    ),
)
async def reindex_kb(
    kb_id: str,
    db: DbDep,
    storage: StorageDep,
    jobs: JobsDep,
    background_tasks: BackgroundTasks,
    ctx: AdminCtxDep,
    payload: KbReindexIn | None = None,
) -> KbReindexOut:
    """Queue a re-ingest of every (or the listed) stored document of a knowledge base.

    Raises:
        NotFoundError: A listed document is not in this knowledge base.
    """
    kb = await _load_kb(db, ctx, kb_id)
    query = select(KbDocument).where(KbDocument.kb_id == kb_id).order_by(KbDocument.created_at)
    wanted = payload.document_ids if payload is not None else None
    if wanted is not None:
        query = query.where(KbDocument.id.in_(wanted))
    documents = list((await db.execute(query)).scalars().all())
    if wanted is not None:
        missing = sorted(set(wanted) - {document.id for document in documents})
        if missing:
            raise NotFoundError(f"unknown document '{missing[0]}' in knowledge base '{kb_id}'")

    queued: list[tuple[KbDocument, str]] = []
    skipped: list[KbReindexSkipped] = []
    for document in documents:
        if document.status == "pending":
            skipped.append(_skipped(document, "ingest_in_progress"))
            continue
        key = upload_storage_key(kb.id, document.id, document.filename)
        try:
            await storage.get(key)
        except FileNotFoundError:
            skipped.append(_skipped(document, "source_not_stored"))
            continue
        document.status = "pending"
        document.progress = 0.0
        document.error = None
        queued.append((document, key))
    # Durable before the jobs run on their own connections (same rule as an upload).
    await db.commit()
    for document, key in queued:
        await jobs.enqueue(
            KB_INGEST,
            {
                "kb_id": kb.id,
                "document_id": document.id,
                "storage_key": key,
                "filename": document.filename,
                "mime": document.mime,
            },
            background_tasks=background_tasks,
        )
    log.info("kb_reindex_queued", kb_id=kb.id, queued=len(queued), skipped=len(skipped))
    return KbReindexOut(queued=[document.id for document, _ in queued], skipped=skipped)


def _skipped(
    document: KbDocument, reason: Literal["source_not_stored", "ingest_in_progress"]
) -> KbReindexSkipped:
    return KbReindexSkipped(document_id=document.id, filename=document.filename, reason=reason)


@internal_router.post(
    "/search",
    response_model=KnowledgeSearchResponse,
    summary="Search knowledge bases (worker only)",
    description=(
        "Cross-KB retrieval the agent calls for `search_knowledge` and RAG auto-injection. The "
        "knowledge bases are searched concurrently, each with a timeout. `mode`, `rerank` and "
        "`min_score` work as on the admin test search and default to plain vector search. A "
        "knowledge base that is unknown, built by another embedder, slow or failing is skipped and "
        "named in `warnings`; the others still answer."
    ),
)
async def internal_search_kb(
    payload: InternalKnowledgeSearchIn,
    db: DbDep,
    store: VectorStoreDep,
    embedder: EmbedderDep,
    reranker: RerankerDep,
    _service: ServiceDep,
) -> KnowledgeSearchResponse:
    """Search across the given knowledge bases (worker-only)."""
    _check_k(payload.k)
    service = KnowledgeService(db, store=store, embedder=embedder, reranker=reranker)
    return await service.search(
        payload.kb_ids,
        payload.query,
        payload.k,
        min_score=payload.min_score,
        rerank=payload.rerank,
        mode=payload.mode,
    )
