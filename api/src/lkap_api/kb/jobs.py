"""The ``kb_delete`` job: vector-store cleanup after a document or knowledge base is deleted (V5-04).

D-V5-12 (single writer): LanceDB tolerates one writer process, and in
production the ``jobs`` process already writes every ingest. So the delete
routes no longer touch the vector store: they delete the SQL rows in the
request (the rows are the source of truth; the cascade removes the chunks and
the lexical-index triggers remove their keyword entries, so the document is
gone from listings and from search at once), commit, and enqueue
:data:`~lkap_api.jobs.kinds.KB_DELETE`. This handler then removes the
vectors. Until it runs, the orphan vectors are invisible: every search joins
its hits back to ``kb_chunks`` and drops the ones with no row.

With ``LKAP_JOBS_BACKEND=inline`` (dev) the route awaits the job before it
returns, so the vectors are gone with the response.

The handler is idempotent: it also deletes any chunk row still carrying the
deleted document or knowledge base (an ingest that was running when the
delete landed), then the vectors, then compacts the table.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete

from lkap_api.db.models import KbChunk
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.kinds import KB_DELETE
from lkap_api.jobs.registry import job
from lkap_api.jobs.service import JobsService
from lkap_api.kb.store import get_lancedb_store
from lkap_api.logging import get_logger

log = get_logger(__name__)


def kb_delete_payload(kb_id: str, document_id: str | None = None) -> dict[str, Any]:
    """The ``kb_delete`` payload: one document of ``kb_id``, or the whole knowledge base."""
    return {"kb_id": kb_id, "document_id": document_id}


async def enqueue_kb_delete(jobs: JobsService, kb_id: str, document_id: str | None = None) -> str:
    """Enqueue the vector cleanup for a deleted document or knowledge base.

    Called after the SQL delete is committed. No ``BackgroundTasks`` is
    passed on purpose: with the ``inline`` backend the job is awaited here,
    so the caller's response is sent after the vectors are gone.

    Returns:
        The job id.
    """
    return await jobs.enqueue(KB_DELETE, kb_delete_payload(kb_id, document_id))


@job(KB_DELETE)
async def run_kb_delete_job(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Job handler: drop leftover chunk rows, then the vectors, of a deleted document or knowledge base.

    Args:
        ctx: The job context (``database``, ``settings``).
        payload: ``{"kb_id": str, "document_id": str | None}``; no document
            id means the whole knowledge base (its vector table is dropped).
    """
    kb_id = str(payload["kb_id"])
    raw_document_id = payload.get("document_id")
    document_id = str(raw_document_id) if raw_document_id is not None else None

    async with ctx.database.session() as session:
        scope = KbChunk.document_id == document_id if document_id is not None else KbChunk.kb_id == kb_id
        leftover = await session.execute(delete(KbChunk).where(scope))
        removed = int(getattr(leftover, "rowcount", 0) or 0)

    store = get_lancedb_store(ctx.settings.data_dir)
    if document_id is None:
        await store.delete_kb(kb_id)
    else:
        await store.delete_document(kb_id, document_id)
        await store.optimize(kb_id)
    log.info("kb_delete_done", kb_id=kb_id, document_id=document_id, leftover_chunks=removed)
