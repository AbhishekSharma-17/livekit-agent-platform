"""Cross-knowledge-base semantic search.

Embeds the query, fans out to the vector store per knowledge base, and joins
hits back to :class:`~lkap_api.db.models.KbChunk`/`KbDocument` for their text
and filename.
"""

from __future__ import annotations

from lkap_contracts.api_models import KbHit
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import KbChunk, KbDocument
from lkap_api.kb.embed import Embedder
from lkap_api.kb.store import VectorHit, VectorStore
from lkap_api.logging import get_logger

log = get_logger(__name__)


async def search_kbs(
    session: AsyncSession, store: VectorStore, embedder: Embedder, *, kb_ids: list[str], query: str, k: int
) -> list[KbHit]:
    """Return the top ``k`` chunks across ``kb_ids`` for ``query``, best first.

    Args:
        session: Used to join vector hits back to their chunk text and filename.
        store: The vector store queried per knowledge base.
        embedder: Embeds the query text.
        kb_ids: Knowledge bases to search; an empty list returns no hits.
        query: The search text.
        k: Overall result count across all `kb_ids` combined.

    Returns:
        Hits sorted by score descending, truncated to `k`.
    """
    if not kb_ids or not query.strip():
        return []
    vectors = await embedder.embed([query])
    if not vectors:
        return []
    vector = vectors[0]

    hits: list[VectorHit] = []
    for kb_id in kb_ids:
        hits.extend(await store.query(kb_id, vector, k))
    if not hits:
        return []

    chunk_ids = [hit.id for hit in hits]
    chunk_rows = {
        row.id: row
        for row in (await session.execute(select(KbChunk).where(KbChunk.id.in_(chunk_ids)))).scalars()
    }
    document_ids = {row.document_id for row in chunk_rows.values()}
    document_rows = {
        row.id: row
        for row in (
            await session.execute(select(KbDocument).where(KbDocument.id.in_(document_ids)))
        ).scalars()
    }

    results: list[KbHit] = []
    for hit in hits:
        chunk = chunk_rows.get(hit.id)
        if chunk is None:
            continue
        document = document_rows.get(chunk.document_id)
        results.append(
            KbHit(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                filename=document.filename if document is not None else "",
                score=hit.score,
                text=chunk.text,
            )
        )
    results.sort(key=lambda hit: hit.score, reverse=True)
    return results[:k]
