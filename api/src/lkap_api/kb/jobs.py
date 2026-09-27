"""Knowledge-base maintenance jobs: ``kb_delete`` (V5-04) and ``kb_reindex`` (V5-13), and their CLI.

**``kb_delete``** — vector-store cleanup after a document or knowledge base is
deleted. D-V5-12 (single writer): LanceDB tolerates one writer process, and in
production the ``jobs`` process already writes every ingest. So the delete
routes no longer touch the vector store: they delete the SQL rows in the
request (the rows are the source of truth; the cascade removes the chunks and
the lexical-index triggers remove their keyword entries, so the document is
gone from listings and from search at once), commit, and enqueue
:data:`~lkap_api.jobs.kinds.KB_DELETE`. This handler then removes the
vectors. Until it runs, the orphan vectors are invisible: every search joins
its hits back to ``kb_chunks`` and drops the ones with no row. With
``LKAP_JOBS_BACKEND=inline`` (dev) the route awaits the job before it
returns, so the vectors are gone with the response. The handler is
idempotent: it also deletes any chunk row still carrying the deleted document
or knowledge base (an ingest that was running when the delete landed), then
the vectors, then compacts the table. On pgvector (V5-13) the chunk delete
already cascades to ``kb_vectors``; the store call runs in the same
transaction and also drops a deleted knowledge base's HNSW index.

**``kb_reindex``** (D-V5-13) — re-embeds every chunk row of one knowledge base
with the **current** embedder and replaces its vectors in the **current**
store (:func:`~lkap_api.kb.store.resolve_store`), then records that embedder
and width on the knowledge base (overwriting what it recorded before). It is
how a deployment moves its knowledge from LanceDB to pgvector (run it once per
knowledge base after migration ``v5_003``; docs/RUNBOOK.md) and how a
knowledge base follows an embedder change. It reads the chunk *rows*, not the
source files, so knowledge bases whose documents were never stored (seeded
before V4-18, `_asks.md` #15) are covered too; the text embedded is the one
ingest embeds (heading path, then the chunk). Chunks are not re-split: to
re-chunk, use ``POST /v1/knowledge-bases/{id}/reindex``. Progress
(``{"done", "total"}``) and the outcome are written into the job row's own
payload, found by its ``run_id`` (the V5-05 pattern; the table has no result
column). The write phase re-reads the chunk rows and embeds any that appeared
meanwhile, so an ingest that committed during the run keeps its vectors; run
it while the knowledge base is otherwise idle all the same. On pgvector the
swap is one transaction; on LanceDB the table is dropped and rebuilt, so a
search in that instant can come back empty.

**Knowledge connections (V5-20).** A knowledge base stored through a
connection (Qdrant, Pinecone, Weaviate) is cleaned and re-indexed in that
connection's store: ``kb_delete`` carries the connection id (the knowledge
base row is already gone when it runs) and deletes there, outside any
database transaction; ``kb_reindex`` writes the new vectors, with the chunk
text a store may index for keywords, where the knowledge base lives. With
``move_to`` it **moves** the knowledge base: the vectors are written to the
target first (a connection of the same workspace, or ``None`` = the
platform's store), the row is switched, and only then are the old vectors
deleted (best effort for a connection's store), so a failure never loses the
only copy.

CLI::

    python -m lkap_api.kb.jobs reindex --all
    python -m lkap_api.kb.jobs reindex --kb <id> [--kb <id> ...]
    python -m lkap_api.kb.jobs reindex --kb <id> --to-connection <connection id>
    python -m lkap_api.kb.jobs reindex --kb <id> --to-platform

It enqueues one ``kb_reindex`` per knowledge base through the jobs service:
with ``LKAP_JOBS_BACKEND=inline`` the job runs in the CLI process before the
command returns; with ``arq`` the ``jobs`` process runs it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Job, KbChunk, KnowledgeBase, new_id
from lkap_api.db.session import Database
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.kinds import KB_DELETE, KB_REINDEX
from lkap_api.jobs.registry import job
from lkap_api.jobs.service import JobsService
from lkap_api.kb.embed import Embedder, resolve_embedder
from lkap_api.kb.ingest import PROGRESS_EVERY, Chunk, warm_embedder
from lkap_api.kb.store import KbStoreRouter, VectorRecord, VectorStore, resolve_store, vector_store_kind
from lkap_api.knowledge_connections.http import ConnectorError
from lkap_api.knowledge_connections.runtime import ConnectionRuntime
from lkap_api.knowledge_connections.service import bind_for_kb
from lkap_api.logging import get_logger
from lkap_api.settings import Settings, get_settings
from lkap_api.vault import Vault

log = get_logger(__name__)


# --------------------------------------------------------------------------- kb_delete (V5-04)
def kb_delete_payload(
    kb_id: str,
    document_id: str | None = None,
    *,
    connection_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """The ``kb_delete`` payload: one document of ``kb_id``, or the whole knowledge base.

    ``connection_id``/``workspace_id`` (V5-20) name the knowledge connection the
    vectors live in when the knowledge base row is already deleted.
    """
    payload: dict[str, Any] = {"kb_id": kb_id, "document_id": document_id}
    if connection_id is not None:
        payload["connection_id"] = connection_id
        payload["workspace_id"] = workspace_id
    return payload


async def enqueue_kb_delete(
    jobs: JobsService,
    kb_id: str,
    document_id: str | None = None,
    *,
    connection_id: str | None = None,
    workspace_id: str | None = None,
) -> str:
    """Enqueue the vector cleanup for a deleted document or knowledge base.

    Called after the SQL delete is committed. No ``BackgroundTasks`` is
    passed on purpose: with the ``inline`` backend the job is awaited here,
    so the caller's response is sent after the vectors are gone.

    Returns:
        The job id.
    """
    return await jobs.enqueue(
        KB_DELETE,
        kb_delete_payload(kb_id, document_id, connection_id=connection_id, workspace_id=workspace_id),
    )


async def _connection_of(session: AsyncSession, kb_id: str) -> tuple[str, str] | None:
    """``(connection id, workspace id)`` of a knowledge base stored through a connection, else ``None``."""
    kb = await _load_kb(session, kb_id)
    if kb is None or kb.connection_id is None:
        return None
    return kb.connection_id, kb.workspace_id


async def _drop_vectors(store: VectorStore, kb_id: str, document_id: str | None) -> None:
    if document_id is None:
        await store.delete_kb(kb_id)
    else:
        await store.delete_document(kb_id, document_id)


@job(KB_DELETE)
async def run_kb_delete_job(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Job handler: drop leftover chunk rows, then the vectors, of a deleted document or knowledge base.

    Args:
        ctx: The job context (``database``, ``settings``).
        payload: ``{"kb_id": str, "document_id": str | None}``; no document
            id means the whole knowledge base (its vector table or index is dropped).
    """
    kb_id = str(payload["kb_id"])
    raw_document_id = payload.get("document_id")
    document_id = str(raw_document_id) if raw_document_id is not None else None
    in_database = vector_store_kind(ctx.settings) == "pgvector"

    # V5-20: a knowledge base stored through a connection is cleaned in that service.
    connection: tuple[str, str] | None = None
    if payload.get("connection_id"):
        connection = (str(payload["connection_id"]), str(payload.get("workspace_id") or ""))
    async with ctx.database.session() as session:
        scope = KbChunk.document_id == document_id if document_id is not None else KbChunk.kb_id == kb_id
        leftover = await session.execute(delete(KbChunk).where(scope))
        removed = int(getattr(leftover, "rowcount", 0) or 0)
        if connection is None:
            connection = await _connection_of(session, kb_id)
        if in_database and connection is None:
            await _drop_vectors(resolve_store(ctx.settings, session), kb_id, document_id)
    if connection is not None:
        await _drop_connection_vectors(ctx, kb_id, document_id, connection)
        log.info(
            "kb_delete_done", kb_id=kb_id, document_id=document_id, leftover_chunks=removed, connection=True
        )
        return

    if not in_database:
        store = resolve_store(ctx.settings)
        await _drop_vectors(store, kb_id, document_id)
        if document_id is not None:
            await store.optimize(kb_id)
    log.info("kb_delete_done", kb_id=kb_id, document_id=document_id, leftover_chunks=removed)


async def _drop_connection_vectors(
    ctx: JobContext, kb_id: str, document_id: str | None, connection: tuple[str, str]
) -> None:
    """Delete from a connection's store, outside any database transaction (the key is read first)."""
    connection_id, workspace_id = connection
    try:
        async with ctx.database.session() as session:
            runtime = ConnectionRuntime(ctx.settings, session, ctx.vault)
            store = await runtime.store_for_connection(connection_id, workspace_id=workspace_id)
    except ConnectorError as exc:
        # The connection (or its key) is gone: nothing can reach the vectors any more.
        log.warning("kb_delete_connection_unavailable", kb_id=kb_id, reason=exc.message)
        return
    await _drop_vectors(store, kb_id, document_id)


# --------------------------------------------------------------------------- kb_reindex (V5-13)
@dataclass(slots=True, frozen=True)
class _ChunkRow:
    id: str
    document_id: str
    embed_text: str


#: ``move_to`` value meaning "the platform's own store" on the command line.
PLATFORM_TARGET: Final = "platform"


def kb_reindex_payload(
    kb_id: str, run_id: str | None = None, *, move_to: str | None = None
) -> dict[str, Any]:
    """The ``kb_reindex`` payload; the handler adds ``progress`` and ``result`` to its row.

    ``move_to`` (V5-20): a knowledge connection id, or :data:`PLATFORM_TARGET`,
    to move the knowledge base's vectors there; ``None`` re-indexes in place.
    """
    payload: dict[str, Any] = {"run_id": run_id or new_id(), "kb_id": kb_id}
    if move_to is not None:
        payload["move_to"] = {"connection_id": None if move_to == PLATFORM_TARGET else move_to}
    return payload


async def enqueue_kb_reindex(jobs: JobsService, kb_id: str, *, move_to: str | None = None) -> str:
    """Enqueue a re-embedding of ``kb_id`` into its store (or ``move_to``'s); returns the job id.

    With the ``inline`` backend the job has run by the time this returns.
    """
    return await jobs.enqueue(KB_REINDEX, kb_reindex_payload(kb_id, move_to=move_to))


async def _load_kb(session: AsyncSession, kb_id: str) -> KnowledgeBase | None:
    # A job carries only the ids (deliberately cross-workspace, like ingest).
    statement = (
        select(KnowledgeBase)
        .where(KnowledgeBase.id == kb_id)
        .execution_options(**{CROSS_WORKSPACE_OPTION: True})
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def _chunk_rows(session: AsyncSession, kb_id: str) -> list[_ChunkRow]:
    rows = await session.execute(
        select(KbChunk).where(KbChunk.kb_id == kb_id).order_by(KbChunk.document_id, KbChunk.ordinal)
    )
    chunks: list[_ChunkRow] = []
    for row in rows.scalars():
        headings = (row.meta or {}).get("heading_path") or []
        heading_path = tuple(str(heading) for heading in headings) if isinstance(headings, list) else ()
        text = Chunk(
            text=row.text, heading_path=heading_path, char_start=0, char_end=len(row.text)
        ).embed_text
        chunks.append(_ChunkRow(id=row.id, document_id=row.document_id, embed_text=text))
    return chunks


async def _store_of(router: VectorStore, connection_id: str | None, workspace_id: str) -> VectorStore:
    """The platform store (``None``) or a connection's store, from a session's router."""
    if not isinstance(router, KbStoreRouter):
        return router
    if connection_id is None:
        return router.platform
    return await router.runtime.store_for_connection(connection_id, workspace_id=workspace_id)


async def _update_run(ctx: JobContext, run_id: str, **fields: Any) -> None:
    """Merge ``fields`` into this run's job-row payload (reassigned: JSON columns do not track mutation)."""
    async with ctx.database.session() as session:
        row = (
            (
                await session.execute(
                    select(Job).where(Job.kind == KB_REINDEX, Job.payload["run_id"].as_string() == run_id)
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            log.warning("kb_reindex_run_row_missing", run_id=run_id)
            return
        row.payload = {**dict(row.payload or {}), **fields}


async def _embed(
    embedder: Embedder,
    chunks: list[_ChunkRow],
    *,
    ctx: JobContext | None = None,
    run_id: str | None = None,
    total: int | None = None,
) -> dict[str, list[float]]:
    vectors: dict[str, list[float]] = {}
    for start in range(0, len(chunks), PROGRESS_EVERY):
        batch = chunks[start : start + PROGRESS_EVERY]
        embedded = await embedder.embed([chunk.embed_text for chunk in batch])
        if len(embedded) != len(batch):
            raise RuntimeError(f"the embedder returned {len(embedded)} vectors for {len(batch)} chunks")
        vectors.update({chunk.id: vector for chunk, vector in zip(batch, embedded, strict=True)})
        if ctx is not None and run_id is not None and total is not None:
            await _update_run(ctx, run_id, progress={"done": len(vectors), "total": total})
    return vectors


@job(KB_REINDEX)
async def run_kb_reindex_job(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Job handler: re-embed a knowledge base's chunks and replace its vectors in the current store.

    Args:
        ctx: The job context (``database``, ``settings``, ``vault``).
        payload: :func:`kb_reindex_payload`'s ``{"run_id", "kb_id"}``.

    Raises:
        LookupError: The knowledge base no longer exists (the job is retried,
            then marked ``dead`` with this message).
        KbEmbedderMismatchError: The embedder's vectors do not all share one width.
    """
    run_id = str(payload["run_id"])
    kb_id = str(payload["kb_id"])
    kind = vector_store_kind(ctx.settings)
    move = payload.get("move_to")

    async with ctx.database.session() as session:
        found = await _load_kb(session, kb_id)
        if found is None:
            raise LookupError(f"knowledge base '{kb_id}' no longer exists")
        source_connection = found.connection_id
        workspace_id = found.workspace_id
        target_connection = move.get("connection_id") if isinstance(move, dict) else source_connection
        moving = target_connection != source_connection
        target_ref: str | None = None
        if moving and target_connection is not None:
            # Same workspace, a vector store: refused here before any work is done.
            target_ref = (
                await bind_for_kb(session, workspace_id, str(target_connection), kb_id)
            ).external_ref
        embedder = await resolve_embedder(ctx.settings, session, ctx.vault)
        chunks = await _chunk_rows(session, kb_id)
    # The model loads before any write session opens (a first download can take long).
    await warm_embedder(embedder)
    await _update_run(ctx, run_id, progress={"done": 0, "total": len(chunks)})
    vectors = await _embed(embedder, chunks, ctx=ctx, run_id=run_id, total=len(chunks))

    store: VectorStore
    async with ctx.database.session() as session:
        kb = await _load_kb(session, kb_id)
        if kb is None:
            raise LookupError(f"knowledge base '{kb_id}' no longer exists")
        current = await _chunk_rows(session, kb_id)
        vectors.update(await _embed(embedder, [chunk for chunk in current if chunk.id not in vectors]))
        records = [
            # V5-20: the text too, for a connection store with its own keyword index.
            VectorRecord(
                id=chunk.id, vector=vectors[chunk.id], document_id=chunk.document_id, text=chunk.embed_text
            )
            for chunk in current
        ]
        widths = {len(record.vector) for record in records}
        if len(widths) > 1:
            raise ValueError(f"the embedder returned vectors of several widths: {sorted(widths)}")
        dimension = widths.pop() if widths else embedder.dimension

        router = resolve_store(ctx.settings, session, vault=ctx.vault)
        store = await _store_of(router, target_connection, workspace_id)
        source: VectorStore | None = (
            await _store_of(router, source_connection, workspace_id) if moving else None
        )
        if not moving:
            await store.delete_kb(kb_id)
        if records and dimension is not None:
            await store.ensure_namespace(kb_id, dimension)
            await store.upsert(kb_id, records)
        # Overwritten, not filled in: this is how a knowledge base follows an embedder change.
        kb.dimension = dimension
        kb.embedder_model = embedder.model_id
        if moving:
            kb.connection_id = target_connection
            kb.external_ref = target_ref
            if source_connection is None and source is not None:
                await source.delete_kb(kb_id)  # the platform store: in this transaction
    await store.optimize(kb_id)
    if moving and source_connection is not None and source is not None:
        try:  # after the switch committed: a failure leaves only unreachable vectors behind
            await source.delete_kb(kb_id)
        except ConnectorError as exc:
            log.warning("kb_reindex_old_vectors_left", kb_id=kb_id, reason=exc.message)

    result = {
        "chunks": len(records),
        "dimension": dimension,
        "embedder_model": embedder.model_id,
        "store": kind if target_connection is None else "connection",
    }
    if target_connection is not None or moving:  # V5-20: only then (the platform result is unchanged)
        result.update({"connection_id": target_connection, "moved": moving})
    await _update_run(ctx, run_id, progress={"done": len(records), "total": len(records)}, result=result)
    log.info("kb_reindex_done", kb_id=kb_id, **result)


# --------------------------------------------------------------------------- CLI
PROG: Final = "python -m lkap_api.kb.jobs"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG)
    sub = parser.add_subparsers(dest="command", required=True)
    reindex = sub.add_parser(
        "reindex",
        help="re-embed knowledge bases into the current vector store",
        description=(
            "Enqueue one kb_reindex job per knowledge base. With LKAP_JOBS_BACKEND=inline the jobs run "
            "here before the command returns; with arq the jobs process runs them."
        ),
    )
    target = reindex.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", action="store_true", help="every knowledge base of every workspace")
    target.add_argument("--kb", action="append", metavar="ID", help="one knowledge base id (repeatable)")
    destination = reindex.add_mutually_exclusive_group()
    destination.add_argument(
        "--to-connection",
        metavar="CONNECTION_ID",
        help="move the knowledge bases to this knowledge connection",
    )
    destination.add_argument(
        "--to-platform", action="store_true", help="move the knowledge bases to the platform's own store"
    )
    return parser


async def _all_kb_ids(database: Database) -> list[str]:
    async with database.session() as session:
        statement = (
            select(KnowledgeBase.id)
            .order_by(KnowledgeBase.created_at, KnowledgeBase.id)
            .execution_options(**{CROSS_WORKSPACE_OPTION: True})
        )
        return [str(kb_id) for kb_id in (await session.execute(statement)).scalars()]


async def reindex_command(
    kb_ids: Sequence[str] | None,
    *,
    settings: Settings,
    database: Database,
    jobs: JobsService,
    move_to: str | None = None,
) -> int:
    """Enqueue ``kb_reindex`` for ``kb_ids`` (``None`` = every knowledge base); print one line per job.

    Returns:
        A process exit code (1 when an id names no knowledge base).
    """
    targets = list(kb_ids) if kb_ids is not None else await _all_kb_ids(database)
    if kb_ids is not None:
        async with database.session() as session:
            unknown = [kb_id for kb_id in targets if await _load_kb(session, kb_id) is None]
        if unknown:
            sys.stderr.write(f"error: unknown knowledge base id(s): {', '.join(unknown)}\n")
            return 1
    store = vector_store_kind(settings)
    for kb_id in targets:
        job_id = await enqueue_kb_reindex(jobs, kb_id, move_to=move_to)
        sys.stdout.write(f"kb_reindex {kb_id}: job {job_id} ({jobs.backend}, store {store})\n")
    sys.stdout.write(f"{len(targets)} knowledge base(s) queued for re-indexing\n")
    return 0


async def _reindex_main(kb_ids: Sequence[str] | None, move_to: str | None = None) -> int:
    from lkap_api.jobs.handlers import load_all_handlers

    load_all_handlers()
    settings = get_settings()
    database = Database(settings.resolved_database_url)
    jobs = JobsService(database=database, settings=settings, vault=Vault(settings.master_key))
    try:
        return await reindex_command(kb_ids, settings=settings, database=database, jobs=jobs, move_to=move_to)
    finally:
        await jobs.aclose()
        await database.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the knowledge-base maintenance CLI.

    Args:
        argv: Arguments after the program name; defaults to ``sys.argv[1:]``.

    Returns:
        A process exit code.
    """
    try:
        args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    move_to = args.to_connection or (PLATFORM_TARGET if args.to_platform else None)
    return asyncio.run(_reindex_main(None if args.all else list(args.kb), move_to))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
