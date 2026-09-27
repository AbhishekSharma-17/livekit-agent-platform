"""Re-rankers behind one Protocol (V5-04; hosted ones since V5-20, D-V5-19).

* :mod:`~lkap_api.kb.rerankers.local` — the local cross-encoder, the default
  (``rerank="local"``; formerly ``kb/rerank.py``, which still re-exports it).
* :mod:`~lkap_api.kb.rerankers.cohere` and :mod:`~lkap_api.kb.rerankers.voyage`
  — hosted services behind a knowledge connection (``rerank="connection:<id>"``),
  used by the ``search_knowledge`` tool path only, never by automatic
  per-turn injection. They return relevance on ``[0, 1]`` and report what each
  call used, which the search attaches as a cost line.
"""

from __future__ import annotations

from lkap_api.kb.rerankers.base import (
    HostedReranker,
    Reranker,
    RerankUsage,
    scores_are_normalized,
    sigmoid,
)
from lkap_api.kb.rerankers.local import LocalReranker, clear_reranker_cache, get_local_reranker

__all__ = [
    "HostedReranker",
    "LocalReranker",
    "RerankUsage",
    "Reranker",
    "clear_reranker_cache",
    "get_local_reranker",
    "scores_are_normalized",
    "sigmoid",
]
