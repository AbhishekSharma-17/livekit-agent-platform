"""Compatibility shim: the re-rankers live in :mod:`lkap_api.kb.rerankers` since V5-20.

``routers/knowledge.py`` and ``kb/evals.py`` (and older tests) import from here;
every name keeps working.
"""

from __future__ import annotations

from lkap_api.kb.rerankers.base import Reranker, sigmoid
from lkap_api.kb.rerankers.local import LocalReranker, clear_reranker_cache, get_local_reranker

__all__ = ["LocalReranker", "Reranker", "clear_reranker_cache", "get_local_reranker", "sigmoid"]
