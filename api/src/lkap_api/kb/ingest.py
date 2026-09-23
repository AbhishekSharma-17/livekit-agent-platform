"""Document ingestion: text extraction, chunking, embedding, vector upsert.

Per docs/ARCHITECTURE.md §7.4: ``.md``/``.txt``/``.pdf`` -> chunks (800 chars,
120 overlap, tiktoken-free char heuristics) -> embeddings -> LanceDB. Every
public function here takes an already-open :class:`~sqlalchemy.ext.asyncio.AsyncSession`
and never commits it — the caller (a request handler or a background task
owning its own session) controls the transaction boundary.

PLAN-V2 V2-08 moves ingestion "onto jobs": the router
(:mod:`lkap_api.routers.knowledge`) uploads the raw bytes to the platform
:mod:`~lkap_api.storage` backend first (so the job payload stays JSON-only —
required for the ``arq`` backend, whose queue is Redis, not an in-process
call) and enqueues :data:`~lkap_api.jobs.kinds.KB_INGEST` with the storage key
instead of calling straight into :func:`ingest_into_session`. The `inline`
backend still runs it via the caller's ``BackgroundTasks`` when given one, so
the existing "ingestion is done by the time ``POST .../documents`` returns
under ``ASGITransport``" test behaviour is unchanged.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import KbChunk, KbDocument, KnowledgeBase
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.kinds import KB_INGEST
from lkap_api.jobs.registry import job
from lkap_api.kb.embed import Embedder, resolve_embedder
from lkap_api.kb.store import VectorRecord, VectorStore, get_lancedb_store
from lkap_api.logging import get_logger
from lkap_api.storage.resolve import default_storage

log = get_logger(__name__)

#: Char-based chunking (no tiktoken dependency): ~800 chars/chunk, 120 overlap.
CHUNK_SIZE = 800
CHUNK_OVERLAP = 120

#: Ingestion failures are stored on the row; truncate so one bad file can't
#: blow up the `kb_documents.error` column.
MAX_ERROR_CHARS = 500


def extract_text(*, filename: str, mime: str, data: bytes) -> str:
    """Extract plain text from an uploaded document's bytes.

    Args:
        filename: The original filename (used for extension sniffing).
        mime: The declared content type.
        data: The raw file bytes.

    Returns:
        Extracted text; markdown and plain text pass through as UTF-8 decoded
        text, PDFs are extracted page by page with :mod:`pypdf`.
    """
    is_pdf = mime == "application/pdf" or filename.lower().endswith(".pdf")
    if is_pdf:
        return _extract_pdf_text(data)
    return data.decode("utf-8", errors="replace")


def _extract_pdf_text(data: bytes) -> str:
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages)


def chunk_text(text: str, *, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split ``text`` into overlapping character-window chunks.

    Args:
        text: The document's full extracted text.
        size: Target chunk length in characters.
        overlap: How many trailing characters of a chunk reappear at the
            start of the next one, so retrieval doesn't lose context at a
            chunk boundary.

    Returns:
        Non-empty, stripped chunks in document order. Empty input returns `[]`.
    """
    stripped = text.strip()
    if not stripped:
        return []
    if size <= 0:
        raise ValueError("size must be positive")
    step = max(size - overlap, 1)
    chunks: list[str] = []
    start = 0
    length = len(stripped)
    while start < length:
        end = min(start + size, length)
        piece = stripped[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= length:
            break
        start += step
    return chunks


@dataclass(slots=True, frozen=True)
class IngestOutcome:
    """The result of ingesting one document, for logging/tests."""

    status: Literal["ready", "failed"]
    chunk_count: int
    error: str | None = None


async def _finish_document(
    session: AsyncSession,
    *,
    document_id: str,
    kb_id: str,
    status: Literal["ready", "failed"],
    chunk_count: int,
    error: str | None,
) -> None:
    """Set a document's terminal status and recompute its KB's `chunk_count`."""
    document = await session.get(KbDocument, document_id)
    if document is None:
        log.warning("kb_document_missing_on_finish", document_id=document_id, kb_id=kb_id)
        return
    document.status = status
    document.chunk_count = chunk_count
    document.error = error
    await session.flush()
    # Ingestion runs as a job that carries only the ids (deliberately cross-workspace).
    kb = (
        await session.execute(
            select(KnowledgeBase)
            .where(KnowledgeBase.id == kb_id)
            .execution_options(**{CROSS_WORKSPACE_OPTION: True})
        )
    ).scalar_one_or_none()
    if kb is not None:
        total = (
            await session.execute(
                select(func.coalesce(func.sum(KbDocument.chunk_count), 0)).where(KbDocument.kb_id == kb_id)
            )
        ).scalar_one()
        kb.chunk_count = int(total)


async def ingest_into_session(
    session: AsyncSession,
    *,
    store: VectorStore,
    embedder: Embedder,
    kb_id: str,
    document_id: str,
    filename: str,
    mime: str,
    data: bytes,
) -> IngestOutcome:
    """Chunk, embed and upsert one document, using the caller's session/transaction.

    Never raises: any failure (bad encoding, embedder error, vector store
    error) is caught, logged and recorded as the document's ``failed`` status
    within the same session, so the caller's transaction still commits
    cleanly with that failure visible to callers polling the document.

    Args:
        session: An open session; not committed here.
        store: The vector store the chunks' embeddings are upserted into.
        embedder: The embedder used to vectorise the chunks.
        kb_id: The owning knowledge base.
        document_id: The (already persisted, ``pending``) document row's id.
        filename: Original filename, used for extension sniffing and metadata.
        mime: Declared content type.
        data: Raw file bytes.

    Returns:
        The terminal :class:`IngestOutcome`.
    """
    try:
        text = extract_text(filename=filename, mime=mime, data=data)
        chunks = chunk_text(text)
        vectors = await embedder.embed(chunks) if chunks else []
        chunk_rows = [
            KbChunk(kb_id=kb_id, document_id=document_id, ordinal=i, text=chunk, meta={"filename": filename})
            for i, chunk in enumerate(chunks)
        ]
        if chunk_rows:
            session.add_all(chunk_rows)
            await session.flush()
            records = [
                VectorRecord(id=row.id, vector=vector, document_id=document_id)
                for row, vector in zip(chunk_rows, vectors, strict=True)
            ]
            await store.upsert(kb_id, records)
        outcome = IngestOutcome(status="ready", chunk_count=len(chunk_rows))
    except Exception as exc:  # noqa: BLE001 - persisted as a document-level failure, never raised
        log.warning(
            "kb_ingest_failed",
            kb_id=kb_id,
            document_id=document_id,
            filename=filename,
            error_type=type(exc).__name__,
        )
        outcome = IngestOutcome(status="failed", chunk_count=0, error=str(exc)[:MAX_ERROR_CHARS])

    await _finish_document(
        session,
        document_id=document_id,
        kb_id=kb_id,
        status=outcome.status,
        chunk_count=outcome.chunk_count,
        error=outcome.error,
    )
    log.info(
        "kb_ingest_finished",
        kb_id=kb_id,
        document_id=document_id,
        status=outcome.status,
        chunk_count=outcome.chunk_count,
    )
    return outcome


def upload_storage_key(kb_id: str, document_id: str, filename: str) -> str:
    """Return the storage key a KB source upload is written to.

    Shared by the router (writes the object) and :func:`run_ingestion_job`
    (reads it back), so the layout only needs to change in one place.
    """
    return f"kb/{kb_id}/{document_id}_{filename}"


@job(KB_INGEST)
async def run_ingestion_job(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Job handler: fetch the uploaded bytes from storage, then ingest them.

    Args:
        ctx: The job context (``database``, ``settings``, ``vault``).
        payload: ``{"kb_id", "document_id", "storage_key", "filename", "mime"}``,
            JSON-serialisable so this also works through the ``arq`` backend.

    The request handler that enqueued this must have already committed the
    ``pending`` document row (a separate connection needs it to exist before
    the chunk rows' foreign key can be inserted) and the uploaded bytes to
    storage (this handler cannot read a request body).
    """
    kb_id = str(payload["kb_id"])
    document_id = str(payload["document_id"])
    storage_key = str(payload["storage_key"])
    filename = str(payload["filename"])
    mime = str(payload["mime"])

    storage = default_storage(ctx.settings)
    try:
        data = await storage.get(storage_key)
    except FileNotFoundError:
        log.warning("kb_ingest_upload_missing", kb_id=kb_id, document_id=document_id, storage_key=storage_key)
        async with ctx.database.session() as session:
            await _finish_document(
                session,
                document_id=document_id,
                kb_id=kb_id,
                status="failed",
                chunk_count=0,
                error="uploaded file missing from storage",
            )
        return

    async with ctx.database.session() as session:
        store = get_lancedb_store(ctx.settings.data_dir)
        embedder = await resolve_embedder(ctx.settings, session, ctx.vault)
        await ingest_into_session(
            session,
            store=store,
            embedder=embedder,
            kb_id=kb_id,
            document_id=document_id,
            filename=filename,
            mime=mime,
            data=data,
        )
