"""Document ingestion: text extraction, chunking, embedding, vector upsert.

Per docs/ARCHITECTURE.md §7.4: ``.md``/``.txt``/``.pdf`` -> chunks (800 chars,
120 overlap, tiktoken-free char heuristics) -> embeddings -> LanceDB. Every
public function here takes an already-open :class:`~sqlalchemy.ext.asyncio.AsyncSession`
and never commits it — the caller (a request handler or a background task
owning its own session) controls the transaction boundary.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import KbChunk, KbDocument, KnowledgeBase
from lkap_api.db.session import Database
from lkap_api.kb.embed import Embedder
from lkap_api.kb.store import VectorRecord, VectorStore
from lkap_api.logging import get_logger

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
    kb = await session.get(KnowledgeBase, kb_id)
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


async def run_ingestion_task(
    database: Database,
    *,
    store: VectorStore,
    embedder: Embedder,
    kb_id: str,
    document_id: str,
    filename: str,
    mime: str,
    data: bytes,
) -> None:
    """Ingest one document in its own session (the ``BackgroundTasks`` entry point).

    The request handler that schedules this must have already committed the
    ``pending`` document row (a separate connection needs it to exist before
    the chunk rows' foreign key can be inserted).
    """
    async with database.session() as session:
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
