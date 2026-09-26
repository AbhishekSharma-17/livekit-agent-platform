"""The vocabulary every :class:`~lkap_api.kb.store.VectorStore` implementation shares (V5-13).

The backends live beside this module (``lancedb.py``, ``pgvector.py``); the
Protocol and :func:`~lkap_api.kb.store.resolve_store` live in
:mod:`lkap_api.kb.store`, which re-exports everything here, so callers keep
importing from ``lkap_api.kb.store``.

D-V5-37: no store is the source of truth for chunk text. Every store carries
the chunk id, its document id and the vector; the text is joined from
``kb_chunks`` by the search pipeline (``stores_text`` only lets a store skip
that join).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ConfigDict

#: Ids are our own uuid4 hex values; this guards every hand-built filter or
#: identifier (a LanceDB ``IN (...)`` clause, a Postgres partial-index
#: predicate) against anything else ever reaching one.
SAFE_ID: Final = re.compile(r"^[A-Za-z0-9_-]+$")

#: The only metadata filter every store understands: restrict a query to these documents.
FILTER_DOCUMENT_ID: Final = "document_id"


def check_safe_id(value: str, *, what: str = "id") -> str:
    """Return ``value`` when it matches :data:`SAFE_ID`.

    Raises:
        ValueError: When it contains anything outside ``[A-Za-z0-9_-]``.
    """
    if not SAFE_ID.match(value):
        raise ValueError(f"unsafe {what} for a vector-store filter: {value!r}")
    return value


def document_filter(filters: Mapping[str, object] | None) -> list[str] | None:
    """The document ids a query's ``filters`` restrict it to (``None`` = no restriction).

    ``filters`` accepts one key, :data:`FILTER_DOCUMENT_ID`, whose value is one
    id or a list of ids; every id is checked with :func:`check_safe_id`.

    Raises:
        ValueError: An unknown filter key, a value of the wrong type, or an unsafe id.
    """
    if not filters:
        return None
    unknown = set(filters) - {FILTER_DOCUMENT_ID}
    if unknown:
        raise ValueError(f"unsupported vector-store filter(s): {sorted(unknown)}")
    raw = filters[FILTER_DOCUMENT_ID]
    values: Sequence[object] = [raw] if isinstance(raw, str) else raw if isinstance(raw, list | tuple) else []
    if not values or not all(isinstance(value, str) for value in values):
        raise ValueError("the document_id filter takes one id or a non-empty list of ids")
    return [check_safe_id(str(value), what="document id") for value in values]


class StoreCapabilities(BaseModel):
    """What a vector store can do natively (K §5.1).

    Attributes:
        hybrid: ``query(text=...)`` fuses a lexical list with the dense one inside
            the store; the search pipeline then skips its own lexical stage.
        filters: ``query(filters=...)`` restricts the search at query time.
        stores_text: The store can return chunk text without the SQL join (D-V5-37).
        namespaces: Per-knowledge-base isolation is a first-class object (a
            table, a partial index, a namespace) rather than a filter.
    """

    model_config = ConfigDict(frozen=True)

    hybrid: bool = False
    filters: bool = False
    stores_text: bool = False
    namespaces: bool = False


#: What a store that declares nothing (a test double written before V5-13) is assumed to support.
NO_CAPABILITIES: Final = StoreCapabilities()


class StoreHealth(BaseModel):
    """The result of :meth:`~lkap_api.kb.store.VectorStore.health`."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    backend: str
    #: The backend's own version where it has one (the ``vector`` extension's for pgvector).
    version: str | None = None
    #: Why ``ok`` is false, in words an operator can act on.
    detail: str | None = None


@dataclass(slots=True, frozen=True)
class VectorRecord:
    """One chunk's vector, keyed by its `KbChunk.id`."""

    id: str
    vector: list[float]
    document_id: str


@dataclass(slots=True, frozen=True)
class VectorHit:
    """One nearest-neighbour result: a chunk id, its document and a similarity score.

    ``score`` is the cosine similarity (``1 - cosine distance``) for a dense
    query. For a native hybrid query (``text`` given to a store whose
    capabilities say ``hybrid``) it is the normalised reciprocal-rank-fusion
    score, and ``vector_score`` / ``lexical_rank`` carry the two inputs.
    """

    id: str
    document_id: str
    score: float
    vector_score: float | None = None
    lexical_rank: int | None = None
    fused: bool = False
