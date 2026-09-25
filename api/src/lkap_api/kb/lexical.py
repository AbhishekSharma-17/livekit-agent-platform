"""Lexical (keyword) search over chunk text, the second list of hybrid search (V5-04).

The index is created by migration ``v5_001_knowledge_p0`` and kept in sync by
the database itself, so nothing here writes:

* **SQLite**: the FTS5 table ``kb_chunks_fts(chunk_id, kb_id UNINDEXED, text)``
  (``porter unicode61`` tokenizer), filled by triggers on ``kb_chunks``.
  Queried with ``MATCH 'text:(...)'`` (always column-scoped: ``chunk_id`` is
  indexed too, for the delete trigger) and ordered by ``bm25()``.
* **Postgres**: the generated column ``kb_chunks.tsv`` =
  ``to_tsvector('english', text)`` with a GIN index, queried with ``@@`` and
  ordered by ``ts_rank_cd``.

Both dialects get the same query semantics: the text is reduced to its
letter/digit tokens and they are **OR**-ed, so a question matches chunks that
share any of its words and the ranking function (BM25 / cover density) puts
the chunks sharing the most, rarest words first. That is what a recall channel
for fusion wants: an identifier such as ``AUTO-11111`` becomes
``"AUTO" OR "11111"`` and the chunk that holds both ranks first, while an
all-words (AND) query would return nothing for most spoken questions.

Only letter/digit runs reach the index query, so no user text can inject FTS5
or ``tsquery`` syntax. A database without the index (a schema built by
``create_all``, or a migration not yet applied) returns no hits and says so
(:attr:`LexicalResult.available` is ``False``), never an error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.logging import get_logger

log = get_logger(__name__)

#: The SQLite FTS5 table ``v5_001`` creates.
FTS_TABLE: Final = "kb_chunks_fts"
#: The Postgres generated ``tsvector`` column ``v5_001`` adds to ``kb_chunks``.
PG_TSV_COLUMN: Final = "tsv"
#: The most query tokens sent to the index (a long utterance is cut, not refused).
MAX_QUERY_TOKENS: Final = 32

#: Letter/digit runs only: underscores and punctuation never reach the index query.
#: The Devanagari block and the combining diacritics are included explicitly, so
#: a Hindi word is not cut at its vowel signs (they are marks, not letters).
_TOKEN_RE = re.compile(r"(?:[^\W_]|[̀-ͯऀ-ॿ])+")


@dataclass(slots=True, frozen=True)
class LexicalHit:
    """One chunk the lexical index matched, with its 1-based rank (1 = best)."""

    chunk_id: str
    rank: int


@dataclass(slots=True, frozen=True)
class LexicalResult:
    """The lexical list, and whether the index existed to produce it."""

    hits: list[LexicalHit]
    available: bool


def query_tokens(query: str) -> list[str]:
    """The distinct letter/digit tokens of ``query``, in order, at most :data:`MAX_QUERY_TOKENS`.

    Case is kept (both tokenizers fold it); duplicates are dropped case-insensitively.
    """
    seen: set[str] = set()
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(query):
        folded = token.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        tokens.append(token)
        if len(tokens) == MAX_QUERY_TOKENS:
            break
    return tokens


def fts5_match(tokens: list[str]) -> str:
    """The FTS5 ``MATCH`` expression for ``tokens``: ``text:("a" OR "b")``.

    Every token is a double-quoted string, so FTS5 operators (``-``, ``*``,
    ``:``, ``NEAR``, ``AND``) inside the user's text are plain words.
    """
    quoted = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
    return f"text:({quoted})"


def tsquery_text(tokens: list[str]) -> str:
    """The ``to_tsquery`` input for ``tokens``: ``a | b`` (letters and digits only, so no operator)."""
    return " | ".join(tokens)


async def lexical_index_available(session: AsyncSession) -> bool:
    """Whether this database has the lexical index ``v5_001`` creates."""
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        found = await session.scalar(
            text("SELECT 1 FROM sqlite_master WHERE name = :name"), {"name": FTS_TABLE}
        )
        return found is not None
    if dialect == "postgresql":
        found = await session.scalar(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'kb_chunks' AND column_name = :col"
            ),
            {"col": PG_TSV_COLUMN},
        )
        return found is not None
    return False


async def lexical_search(
    session: AsyncSession, *, kb_ids: list[str], query: str, limit: int
) -> LexicalResult:
    """Return the ``limit`` best keyword matches for ``query`` across ``kb_ids``, best first.

    One query over every listed knowledge base (BM25 statistics are over the
    whole index, so the ranks are comparable across knowledge bases).

    Args:
        session: The request's session; read only.
        kb_ids: Knowledge bases to search (already filtered by the caller).
        query: The user's search text.
        limit: The most hits to return.

    Returns:
        The ranked hits, and ``available=False`` when the index does not exist.
    """
    if not kb_ids or limit <= 0:
        return LexicalResult(hits=[], available=True)
    if not await lexical_index_available(session):
        log.warning("kb_lexical_index_missing", dialect=session.get_bind().dialect.name)
        return LexicalResult(hits=[], available=False)
    tokens = query_tokens(query)
    if not tokens:
        return LexicalResult(hits=[], available=True)

    params: dict[str, object] = {f"kb_{i}": kb_id for i, kb_id in enumerate(kb_ids)}
    placeholders = ", ".join(f":kb_{i}" for i in range(len(kb_ids)))
    params["limit"] = limit
    if session.get_bind().dialect.name == "sqlite":
        params["match"] = fts5_match(tokens)
        statement = text(
            f"SELECT chunk_id FROM {FTS_TABLE} "
            f"WHERE {FTS_TABLE} MATCH :match AND kb_id IN ({placeholders}) "
            f"ORDER BY bm25({FTS_TABLE}), chunk_id LIMIT :limit"
        )
    else:
        params["tsq"] = tsquery_text(tokens)
        statement = text(
            "SELECT id FROM kb_chunks, to_tsquery('english', :tsq) AS q "
            f"WHERE {PG_TSV_COLUMN} @@ q AND kb_id IN ({placeholders}) "
            f"ORDER BY ts_rank_cd({PG_TSV_COLUMN}, q) DESC, id LIMIT :limit"
        )
    rows = (await session.execute(statement, params)).scalars().all()
    return LexicalResult(
        hits=[LexicalHit(chunk_id=str(chunk_id), rank=rank) for rank, chunk_id in enumerate(rows, start=1)],
        available=True,
    )
