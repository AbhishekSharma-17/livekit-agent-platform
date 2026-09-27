"""The local cross-encoder re-ranker (V5-04, D-V5-19: the default; moved here from ``kb/rerank.py`` by V5-20).

:class:`LocalReranker` is fastembed's ONNX ``TextCrossEncoder`` with the model
from ``LKAP_RERANK_MODEL`` (``Xenova/ms-marco-MiniLM-L-6-v2`` unless
configured), cached under ``LKAP_DATA_DIR/models``, loaded once per process on
first use and run on a worker thread so the event loop never blocks. It
returns logits, which the search maps to ``(0, 1)`` with
:func:`~lkap_api.kb.rerankers.base.sigmoid`. The hosted re-rankers (Cohere,
Voyage) live beside it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

from lkap_api.logging import get_logger

log = get_logger(__name__)


class LocalReranker:
    """fastembed ``TextCrossEncoder``; the model is loaded lazily, once, on first :meth:`rerank`.

    Constructing one never touches disk or the network, so building the
    dependency for a request that does not rerank costs nothing.
    """

    def __init__(self, cache_dir: str | Path, *, model_name: str) -> None:
        """Create the reranker.

        Args:
            cache_dir: Directory the ONNX weights are cached under.
            model_name: The fastembed cross-encoder id (``LKAP_RERANK_MODEL``).
        """
        self._cache_dir = str(cache_dir)
        self._model_name = model_name
        self._model: object | None = None
        self._lock = asyncio.Lock()

    @property
    def model_id(self) -> str:
        """The cross-encoder model id."""
        return self._model_name

    async def _get_model(self) -> object:
        if self._model is None:
            async with self._lock:
                if self._model is None:
                    from fastembed.rerank.cross_encoder import TextCrossEncoder

                    log.info("rerank_model_loading", model=self._model_name, cache_dir=self._cache_dir)
                    self._model = await asyncio.to_thread(
                        TextCrossEncoder, model_name=self._model_name, cache_dir=self._cache_dir
                    )
        return self._model

    async def rerank(self, query: str, passages: Sequence[str]) -> list[float]:
        """Score every passage against ``query`` with the cross-encoder, off the event loop."""
        if not passages:
            return []
        model = await self._get_model()
        documents = list(passages)
        scores = await asyncio.to_thread(
            lambda: list(model.rerank(query, documents)),  # type: ignore[attr-defined]
        )
        return [float(score) for score in scores]


_RERANKER_CACHE: dict[tuple[str, str], LocalReranker] = {}


def get_local_reranker(data_dir: str | Path, model_name: str) -> LocalReranker:
    """Return the process-wide :class:`LocalReranker` for ``model_name`` (loaded once per process).

    Args:
        data_dir: ``LKAP_DATA_DIR``; the weights are cached at ``{data_dir}/models``.
        model_name: The fastembed cross-encoder id (``LKAP_RERANK_MODEL``).
    """
    cache_dir = str(Path(data_dir) / "models")
    key = (cache_dir, model_name)
    if key not in _RERANKER_CACHE:
        _RERANKER_CACHE[key] = LocalReranker(cache_dir, model_name=model_name)
    return _RERANKER_CACHE[key]


def clear_reranker_cache() -> None:
    """Drop cached reranker instances (tests only)."""
    _RERANKER_CACHE.clear()
