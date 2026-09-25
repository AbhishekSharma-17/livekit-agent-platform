"""v5_001_knowledge_p0: per-KB embedder record, ingest progress, lexical index, eval sets (V5-01)

docs/v5/PLAN-V5.md §0.3 (the migration ledger) and the V5-01 card. Additive only:

* ``knowledge_bases``: ``dimension`` (INTEGER), ``embedder_model``
  (VARCHAR 200) and ``chunking`` (JSON), all nullable. Rows that exist before
  this migration keep ``NULL`` in all three, which the api reads as "not
  recorded, never refused" — every existing knowledge base keeps working
  unchanged and records its embedder on its next successful ingest. Nothing
  is backfilled: which model built an existing KB's vectors is not knowable
  from SQL, and a wrong guess would turn a working KB into a 422.
* ``kb_documents.progress`` (FLOAT, nullable): the ingest job's chunks done /
  total. Existing rows stay ``NULL``.
* ``kb_chunks``: an index on ``(kb_id, document_id)``. ``meta`` is already JSON
  and needs no change; chunks ingested before V5-01 keep ``{"filename"}``
  until their document is re-indexed through ``POST .../reindex`` (explicit,
  never done by this migration).
* Lexical index for V5-04's hybrid search, dialect-conditional in this one
  revision:

  - **SQLite**: ``kb_chunks_fts``, an FTS5 table ``(chunk_id, kb_id UNINDEXED,
    text)`` with the ``porter unicode61 remove_diacritics 2`` tokenizer, kept in
    sync by three triggers on ``kb_chunks`` (insert, delete, update of
    ``text``/``kb_id``) and backfilled from every existing chunk. It stores its
    own copy of the text rather than using ``content='kb_chunks'``, because
    ``kb_chunks`` has no INTEGER PRIMARY KEY and its implicit rowids are not
    stable across ``VACUUM``; the delete trigger finds a row through the
    indexed ``chunk_id`` column (``MATCH 'chunk_id:"<id>"'``), so it never
    scans. Foreign-key cascades (document or knowledge-base deletes) fire the
    delete trigger. Query it as ``kb_chunks_fts MATCH 'text:(...)'`` joined on
    ``chunk_id``.
  - **Postgres**: ``kb_chunks.tsv``, a ``tsvector`` column generated from
    ``to_tsvector('english', text)`` (STORED), with a GIN index. Query it with
    ``tsv @@ plainto_tsquery('english', :q)``.

  Neither object is in the ORM metadata; ``alembic/env.py`` excludes them from
  autogenerate so ``alembic check`` stays clean.
* ``kb_evals`` (new): ``id, kb_id → knowledge_bases ON DELETE CASCADE,
  question, expected_document_id (no FK: a deleted document makes the eval
  "skipped", V5-05), expected_text, tags (JSON), ordinal, created_at``, index
  on ``kb_id``.

Downgrade drops everything above, both FTS variants included (the dialect
decides which exists). SQLite column drops go through ``batch_alter_table``.

Chain note: the ledger chains V5 after the costs package's ``v4_003``, which
has not landed; this revision chains after the current head
``v4_002_provider_models`` and the coordinator re-chains it at merge if a
``v4_003`` lands first.

Rehearsal log: ``docs/v5/_briefs/migration-rehearsal-v5.md``. Only the
coordinator applies this to the live database (HANDOFF rule 4).

Revision ID: v5_001_knowledge_p0
Revises: v4_003_session_estimates
Create Date: 2026-09-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v5_001_knowledge_p0"
down_revision: str | None = "v4_003_session_estimates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHUNKS_INDEX = "ix_kb_chunks_kb_document"
EVALS_INDEX = "ix_kb_evals_kb"
FTS_TABLE = "kb_chunks_fts"
PG_TSV_INDEX = "ix_kb_chunks_tsv"

_SQLITE_FTS_UP: tuple[str, ...] = (
    f"CREATE VIRTUAL TABLE {FTS_TABLE} USING fts5("
    "chunk_id, kb_id UNINDEXED, text, tokenize = 'porter unicode61 remove_diacritics 2')",
    f"""CREATE TRIGGER {FTS_TABLE}_ai AFTER INSERT ON kb_chunks BEGIN
        INSERT INTO {FTS_TABLE} (chunk_id, kb_id, text) VALUES (new.id, new.kb_id, new.text);
    END""",
    f"""CREATE TRIGGER {FTS_TABLE}_ad AFTER DELETE ON kb_chunks BEGIN
        DELETE FROM {FTS_TABLE} WHERE {FTS_TABLE} MATCH 'chunk_id:"' || old.id || '"';
    END""",
    f"""CREATE TRIGGER {FTS_TABLE}_au AFTER UPDATE OF text, kb_id ON kb_chunks BEGIN
        DELETE FROM {FTS_TABLE} WHERE {FTS_TABLE} MATCH 'chunk_id:"' || old.id || '"';
        INSERT INTO {FTS_TABLE} (chunk_id, kb_id, text) VALUES (new.id, new.kb_id, new.text);
    END""",
    f"INSERT INTO {FTS_TABLE} (chunk_id, kb_id, text) SELECT id, kb_id, text FROM kb_chunks",
)

_SQLITE_FTS_DOWN: tuple[str, ...] = (
    f"DROP TRIGGER IF EXISTS {FTS_TABLE}_au",
    f"DROP TRIGGER IF EXISTS {FTS_TABLE}_ad",
    f"DROP TRIGGER IF EXISTS {FTS_TABLE}_ai",
    f"DROP TABLE IF EXISTS {FTS_TABLE}",
)


def _dialect() -> str:
    return str(op.get_bind().dialect.name)


def upgrade() -> None:
    """Add the V5-01 columns, the chunk index, the lexical index and ``kb_evals``."""
    with op.batch_alter_table("knowledge_bases") as batch:
        batch.add_column(sa.Column("dimension", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("embedder_model", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("chunking", sa.JSON(), nullable=True))
    with op.batch_alter_table("kb_documents") as batch:
        batch.add_column(sa.Column("progress", sa.Float(), nullable=True))
    op.create_index(CHUNKS_INDEX, "kb_chunks", ["kb_id", "document_id"], unique=False)

    op.create_table(
        "kb_evals",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("kb_id", sa.String(length=32), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("expected_document_id", sa.String(length=32), nullable=True),
        sa.Column("expected_text", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["kb_id"],
            ["knowledge_bases.id"],
            name="fk_kb_evals_kb_id_knowledge_bases",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_kb_evals"),
    )
    op.create_index(EVALS_INDEX, "kb_evals", ["kb_id"], unique=False)

    dialect = _dialect()
    if dialect == "sqlite":
        for statement in _SQLITE_FTS_UP:
            op.execute(statement)
    elif dialect == "postgresql":
        op.execute(
            "ALTER TABLE kb_chunks ADD COLUMN tsv tsvector "
            "GENERATED ALWAYS AS (to_tsvector('english', text)) STORED"
        )
        op.execute(f"CREATE INDEX {PG_TSV_INDEX} ON kb_chunks USING gin (tsv)")


def downgrade() -> None:
    """Drop everything :func:`upgrade` added (the lexical index of whichever dialect this is)."""
    dialect = _dialect()
    if dialect == "sqlite":
        for statement in _SQLITE_FTS_DOWN:
            op.execute(statement)
    elif dialect == "postgresql":
        op.execute(f"DROP INDEX IF EXISTS {PG_TSV_INDEX}")
        op.execute("ALTER TABLE kb_chunks DROP COLUMN IF EXISTS tsv")

    op.drop_index(EVALS_INDEX, table_name="kb_evals")
    op.drop_table("kb_evals")
    op.drop_index(CHUNKS_INDEX, table_name="kb_chunks")
    with op.batch_alter_table("kb_documents") as batch:
        batch.drop_column("progress")
    with op.batch_alter_table("knowledge_bases") as batch:
        batch.drop_column("chunking")
        batch.drop_column("embedder_model")
        batch.drop_column("dimension")
