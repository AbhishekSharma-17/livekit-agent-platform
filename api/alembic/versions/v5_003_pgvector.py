"""v5_003_pgvector: the pgvector store's table on Postgres; a no-op on SQLite (V5-13)

docs/v5/PLAN-V5.md §0.3 (the migration ledger) and the V5-13 card
(D-V5-12/D-V5-13: the default vector store follows the database). On
**Postgres** only:

* ``CREATE EXTENSION IF NOT EXISTS vector`` — the server must have pgvector
  (``deploy/docker-compose.prod.yml`` uses the ``pgvector/pgvector:pg16``
  image from V5-13 on; on a plain ``postgres`` server this migration fails
  with "extension "vector" is not available", deliberately, rather than
  leaving the default store without its table).
* ``kb_vectors``: ``chunk_id VARCHAR(32)`` primary key and foreign key to
  ``kb_chunks.id`` ``ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED`` (the
  ingest path writes vectors before it flushes the chunk rows of the same
  transaction; the cascade itself is never deferred), ``kb_id VARCHAR(32)``
  with a btree index, ``embedding vector``.
* One HNSW index per existing knowledge base whose width is recorded
  (``knowledge_bases.dimension``, V5-01), through :func:`_hnsw_index_sql`:
  a partial expression index ``(embedding::vector(<d>)) vector_cosine_ops
  WHERE kb_id = '<id>' AND vector_dims(embedding) = <d>`` named
  ``kbv_hnsw_<id>_<d>`` (the width test keeps the cast from ever reading a row
  of another width, e.g. one a re-index deleted in the same transaction); ``halfvec(<d>)`` from
  2,001 to 4,000 dimensions on pgvector ≥ 0.7; none above that (exact
  search). New knowledge bases get theirs from the store on first write.

Deviation from the ledger's ``embedding vector(<dim>)``: the column has **no
fixed width**. Knowledge bases built by different embedders (bge-small 384,
an OpenAI model 1,536) share the one table, which a typed column cannot
hold; HNSW needs a width, which the per-knowledge-base partial expression
index supplies instead.

Everything is ``IF NOT EXISTS``, so the upgrade is idempotent: a database
whose schema ``create_all`` already built (``lkap_api.db.models`` creates the
same table on Postgres) upgrades cleanly. No vectors are copied here: after
upgrading, ``python -m lkap_api.kb.jobs reindex --all`` re-embeds every
knowledge base into ``kb_vectors`` (docs/RUNBOOK.md).

Downgrade (Postgres): drops ``kb_vectors`` with its indexes. The ``vector``
extension is kept: it holds no data once the table is gone, and it may have
been created by an operator for other uses.

On **SQLite** both directions do nothing (LanceDB stays the store; the ORM
keeps ``KbVector`` out of ``Base.metadata`` for this reason).

Chain note: the ledger's numbering is not the chain order; this revision
follows the current head, ``v5_010_tool_provider_kind``.

Rehearsal log: ``docs/v5/_briefs/migration-rehearsal-v5.md``. Only the
coordinator applies this to the live database (HANDOFF rule 4).

Revision ID: v5_003_pgvector
Revises: v5_010_tool_provider_kind
Create Date: 2026-09-27
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v5_003_pgvector"
down_revision: str | None = "v5_010_tool_provider_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
MAX_VECTOR_INDEX_DIM = 2000
MAX_HALFVEC_INDEX_DIM = 4000
HALFVEC_MIN_VERSION = (0, 7, 0)


def _version(value: str | None) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in (value or "").split("."):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    return tuple(parts)


def _hnsw_index_sql(kb_id: str, dimension: int, version: tuple[int, ...]) -> str | None:
    """A copy of ``lkap_api.kb.stores.pgvector.hnsw_index_sql`` (migrations import no app code)."""
    if not _SAFE_ID.match(kb_id) or dimension < 1:
        return None
    if dimension <= MAX_VECTOR_INDEX_DIM:
        cast, ops = f"vector({dimension})", "vector_cosine_ops"
    elif dimension <= MAX_HALFVEC_INDEX_DIM and version >= HALFVEC_MIN_VERSION:
        cast, ops = f"halfvec({dimension})", "halfvec_cosine_ops"
    else:
        return None
    return (
        f"CREATE INDEX IF NOT EXISTS kbv_hnsw_{kb_id}_{dimension} ON kb_vectors "
        f"USING hnsw ((embedding::{cast}) {ops}) "
        f"WHERE kb_id = '{kb_id}' AND vector_dims(embedding) = {dimension}"
    )


def upgrade() -> None:
    """Postgres: the extension, ``kb_vectors`` and the existing knowledge bases' indexes."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        "CREATE TABLE IF NOT EXISTS kb_vectors ("
        "chunk_id VARCHAR(32) NOT NULL, "
        "kb_id VARCHAR(32) NOT NULL, "
        "embedding vector NOT NULL, "
        "CONSTRAINT pk_kb_vectors PRIMARY KEY (chunk_id), "
        "CONSTRAINT fk_kb_vectors_chunk_id_kb_chunks FOREIGN KEY (chunk_id) "
        "REFERENCES kb_chunks (id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_kb_vectors_kb_id ON kb_vectors (kb_id)")
    version = _version(
        bind.execute(sa.text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")).scalar()
    )
    rows = bind.execute(sa.text("SELECT id, dimension FROM knowledge_bases WHERE dimension IS NOT NULL"))
    for kb_id, dimension in rows.all():
        statement = _hnsw_index_sql(str(kb_id), int(dimension), version)
        if statement is not None:
            op.execute(statement)


def downgrade() -> None:
    """Postgres: drop ``kb_vectors`` and its indexes (the extension stays)."""
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TABLE IF EXISTS kb_vectors")
