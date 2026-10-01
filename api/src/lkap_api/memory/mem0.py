"""The Mem0 OSS memory backend (V5-40, D-V5-17): ``mem0ai`` ``AsyncMemory``, loaded lazily.

Installed with the ``lkap-api[memory]`` extra (``mem0ai`` 2.2.x, Apache-2.0,
https://github.com/mem0ai/mem0; docs https://docs.mem0.ai/open-source/features/async-memory).
Without it, :meth:`Mem0MemoryStore.from_settings` raises
:class:`~lkap_api.memory.store.MemoryUnavailableError` and the api reports
memory as unavailable instead of failing.

What is configured, and why:

* **Vector store by database.** On Postgres the Mem0 ``pgvector`` store (one
  ``lkap_memory`` table in the platform's own database, through ``psycopg`` 3;
  Mem0 runs ``CREATE EXTENSION IF NOT EXISTS vector`` itself). On SQLite, Qdrant
  in **local (file) mode** under ``LKAP_DATA_DIR/memory/qdrant``: one
  ``QdrantClient(path=...)`` per process, handed to Mem0 through
  ``QdrantConfig.client`` (https://docs.mem0.ai/components/vectordbs/dbs/qdrant).
  Local mode locks its folder, so only one process may open it: the api, which
  also runs the jobs with the default ``LKAP_JOBS_BACKEND=inline``. Use Postgres
  with ``arq``.
* **No telemetry, no notices.** Mem0 sends anonymous usage events to PostHog
  and fetches a notice config from GitHub unless ``MEM0_TELEMETRY`` is false,
  and writes ``~/.mem0`` at import. Both environment variables are set before
  the first import, and the import is refused if telemetry is still on.
* **No history database.** Mem0's SQLite history keeps every old and new
  memory text and the raw messages of each ``add``, and a delete *adds* a
  history row. It is replaced by a stub that keeps nothing, so a forget leaves
  no copy behind.
* **The platform's model client, not Mem0's.** The extraction call goes through
  :class:`_GuardedExtractionLLM`: the platform's ``net_guard`` HTTP client, the
  model and key of the agent (a contextvar set by :meth:`Mem0MemoryStore.remember`;
  ``asyncio.to_thread`` carries it into Mem0's worker thread). Mem0's own
  OpenAI client is never used, so ``OPENROUTER_API_KEY`` / ``OPENAI_API_KEY`` in
  the environment are ignored.
* **The knowledge-base embedder.** Mem0 embeds with the api's local fastembed
  model (``LKAP_EMBED_MODEL``, cached under ``LKAP_DATA_DIR/models``), so no
  embedding vendor is called and the model is loaded once per process.

Mem0 runs its blocking work in threads (``asyncio.to_thread``); the adapters
above run their coroutines back on the event loop and must never be called from
the loop's own thread.
"""

from __future__ import annotations

import asyncio
import contextvars
import importlib
import os
import threading
from collections.abc import Coroutine, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol, TypeVar

import httpx

from lkap_api import net_guard
from lkap_api.kb.embed import get_fastembed_embedder
from lkap_api.logging import get_logger
from lkap_api.memory.store import (
    ExtractionModel,
    MemoryItem,
    MemoryMessage,
    MemoryUnavailableError,
    parse_timestamp,
)
from lkap_api.settings import Settings

__all__ = [
    "COLLECTION_NAME",
    "EXTRACTION_TIMEOUT_S",
    "LocalEmbedder",
    "Mem0MemoryStore",
    "prepare_environment",
]

log = get_logger(__name__)

#: The Mem0 collection (a Qdrant collection, or the pgvector table) every workspace shares:
#: subject ids are keyed per workspace, so two workspaces can never share one.
COLLECTION_NAME: Final[str] = "lkap_memory"
#: One extraction call (the model's reply) must finish within this many seconds.
EXTRACTION_TIMEOUT_S: Final[float] = 60.0
#: One embedding call must finish within this many seconds (a cold model loads first).
_EMBED_TIMEOUT_S: Final[float] = 300.0
#: Memories listed per subject when recalling without a query (then sorted newest first).
_LIST_LIMIT: Final[int] = 200

_T = TypeVar("_T")


class LocalEmbedder(Protocol):
    """What the store embeds with: the api's fastembed embedder (``kb.embed``) or a test fake."""

    @property
    def dimension(self) -> int:
        """The vector width."""
        ...

    async def warm(self) -> None:
        """Load the model now."""
        ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text."""
        ...


@dataclass(frozen=True, slots=True)
class _Extraction:
    model: ExtractionModel


#: The extraction model of the current `remember` call (carried into Mem0's threads).
_EXTRACTION: contextvars.ContextVar[_Extraction | None] = contextvars.ContextVar(
    "lkap_memory_extraction", default=None
)


def prepare_environment(settings: Settings) -> Path:
    """Switch Mem0's telemetry off and root its files under ``LKAP_DATA_DIR/memory``.

    Must run before the first ``import mem0`` (Mem0 reads both at import time).

    Returns:
        The memory directory.
    """
    root = Path(settings.data_dir) / "memory"
    root.mkdir(parents=True, exist_ok=True)
    os.environ["MEM0_TELEMETRY"] = "False"
    os.environ["MEM0_DIR"] = str(root / "mem0")
    return root


def _import(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise MemoryUnavailableError(
            "caller memory needs the `lkap-api[memory]` extra (uv sync --extra memory)"
        ) from exc


class _BridgeError(RuntimeError):
    """An adapter was called on the event loop's own thread (it would deadlock)."""


class _LoopBridge:
    """Runs a coroutine on the store's event loop from one of Mem0's worker threads."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._loop_thread = threading.get_ident()

    def run(self, coro: Coroutine[Any, Any, _T], timeout: float) -> _T:
        if threading.get_ident() == self._loop_thread:
            coro.close()
            raise _BridgeError("a memory adapter was called on the event loop thread")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)


class _GuardedExtractionLLM:
    """Mem0's ``llm``: one chat completion through the platform's guarded HTTP client."""

    def __init__(self, bridge: _LoopBridge, http: httpx.AsyncClient) -> None:
        self._bridge = bridge
        self._http = http

    async def _complete(
        self, model: ExtractionModel, messages: list[dict[str, Any]], response_format: Any
    ) -> str:
        body: dict[str, Any] = {"model": model.model, "temperature": 0, "messages": messages}
        if response_format:
            body["response_format"] = response_format
        response = await self._http.post(
            f"{model.base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {model.api_key}"},
            json=body,
            timeout=EXTRACTION_TIMEOUT_S,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return content if isinstance(content, str) else ""

    def generate_response(
        self,
        messages: list[dict[str, Any]],
        response_format: Any = None,
        tools: Any = None,
        tool_choice: str = "auto",
        **_: Any,
    ) -> str:
        """Mem0's LLM entry point (called from a worker thread)."""
        extraction = _EXTRACTION.get()
        if extraction is None:
            raise RuntimeError("no extraction model is set for this memory call")
        if tools:
            raise RuntimeError("memory extraction does not use tools")
        return self._bridge.run(
            self._complete(extraction.model, messages, response_format), EXTRACTION_TIMEOUT_S + 5
        )


class _KbEmbedder:
    """Mem0's ``embedding_model``: the api's own fastembed model (``kb.embed``)."""

    def __init__(self, bridge: _LoopBridge, embedder: LocalEmbedder, config: Any) -> None:
        self._bridge = bridge
        self._embedder = embedder
        self.config = config

    def embed(self, text: str, memory_action: str | None = None) -> list[float]:
        return self.embed_batch([text], memory_action)[0]

    def embed_batch(self, texts: Sequence[str], memory_action: str | None = "add") -> list[list[float]]:
        cleaned = [str(text).replace("\n", " ") for text in texts]
        return self._bridge.run(self._embedder.embed(cleaned), _EMBED_TIMEOUT_S)


class _NoHistory:
    """Mem0's history database, keeping nothing (no old texts, no raw messages)."""

    def add_history(self, *args: Any, **kwargs: Any) -> None:
        return None

    def batch_add_history(self, *args: Any, **kwargs: Any) -> None:
        return None

    def get_history(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def get_last_messages(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def save_messages(self, *args: Any, **kwargs: Any) -> None:
        return None

    def reset(self) -> None:
        return None

    def close(self) -> None:
        return None


def _without_keyword_model(store: Any) -> None:
    """Keep Mem0's Qdrant store from downloading its BM25 keyword model (``Qdrant/bm25``).

    Qdrant marks a failed encoder load with ``_bm25_encoder = False`` and then searches
    by meaning only; setting it up front means no model download from inside the store
    (recall lists memories without a query anyway). A no-op for pgvector.
    """
    if hasattr(store, "_bm25_encoder"):
        store._bm25_encoder = False


def _sync_postgres_url(url: str) -> str:
    """``postgresql+asyncpg://…`` → ``postgresql://…`` (what psycopg connects with)."""
    scheme, sep, rest = url.partition("://")
    return f"{scheme.split('+', 1)[0]}{sep}{rest}" if sep else url


def _items(raw: Any) -> list[dict[str, Any]]:
    results = raw.get("results") if isinstance(raw, dict) else raw
    return [item for item in results or [] if isinstance(item, dict)]


def _item(raw: dict[str, Any]) -> MemoryItem | None:
    text = raw.get("memory")
    if not isinstance(text, str) or not text.strip():
        return None
    stamp = parse_timestamp(raw.get("updated_at") or raw.get("created_at"))
    return MemoryItem(id=str(raw.get("id", "")), text=text.strip(), created_at=stamp)


class Mem0MemoryStore:
    """A :class:`~lkap_api.memory.store.MemoryStore` over Mem0 OSS ``AsyncMemory``."""

    def __init__(
        self,
        *,
        vector_store: dict[str, Any],
        embedder: LocalEmbedder,
        http: httpx.AsyncClient,
        qdrant_client: Any = None,
        owns_http: bool = True,
    ) -> None:
        """Build the store; Mem0 itself is created on first use (inside the event loop).

        Args:
            vector_store: Mem0's ``vector_store`` config (``provider`` and ``config``).
            embedder: The api's fastembed embedder.
            http: The guarded client the extraction model is called with.
            qdrant_client: The local Qdrant client to close with the store, if any.
            owns_http: Whether :meth:`aclose` closes ``http``.
        """
        self._vector_store = vector_store
        self._embedder = embedder
        self._http = http
        self._qdrant_client = qdrant_client
        self._owns_http = owns_http
        self._memory: Any = None
        self._building: asyncio.Future[Any] | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> Mem0MemoryStore:
        """The store for this deployment: pgvector on Postgres, local Qdrant on SQLite.

        Raises:
            MemoryUnavailableError: The extra is not installed, or telemetry is still on.
        """
        root = prepare_environment(settings)
        telemetry = _import("mem0.memory.telemetry")
        if telemetry.MEM0_TELEMETRY:
            raise MemoryUnavailableError(
                "Mem0 was imported before its telemetry could be switched off. Restart the api"
            )
        embedder = get_fastembed_embedder(settings.data_dir, settings.embed_model)
        url = settings.resolved_database_url
        qdrant_client: Any = None
        if url.startswith("postgresql"):
            _import("psycopg")
            vector_store: dict[str, Any] = {
                "provider": "pgvector",
                "config": {
                    "connection_string": _sync_postgres_url(url),
                    "collection_name": COLLECTION_NAME,
                    "embedding_model_dims": embedder.dimension,
                    "hnsw": True,
                },
            }
        else:
            qdrant = _import("qdrant_client")
            path = root / "qdrant"
            path.mkdir(parents=True, exist_ok=True)
            qdrant_client = qdrant.QdrantClient(path=str(path))
            vector_store = {
                "provider": "qdrant",
                "config": {
                    "collection_name": COLLECTION_NAME,
                    "embedding_model_dims": embedder.dimension,
                    "client": qdrant_client,
                    "on_disk": True,
                },
            }
        http = net_guard.guarded_http_client(net_guard.policy_from_settings(settings))
        log.info("memory_store_configured", backend=vector_store["provider"])
        return cls(vector_store=vector_store, embedder=embedder, http=http, qdrant_client=qdrant_client)

    async def _ensure(self) -> Any:
        """Mem0, built once; a caller that gives up waiting does not cancel the build."""
        if self._memory is not None:
            return self._memory
        if self._building is None or (self._building.done() and self._building.exception() is not None):
            self._building = asyncio.ensure_future(self._build())
        self._memory = await asyncio.shield(self._building)
        return self._memory

    async def _build(self) -> Any:
        main = _import("mem0.memory.main")
        bridge = _LoopBridge(asyncio.get_running_loop())
        config = {
            "vector_store": self._vector_store,
            # Placeholders only: both are replaced below before anything runs, and
            # constructing them opens no connection.
            "llm": {
                "provider": "openai",
                "config": {"api_key": "unused", "openai_base_url": "https://api.openai.com/v1"},
            },
            "embedder": {
                "provider": "openai",
                "config": {"api_key": "unused", "embedding_dims": self._embedder.dimension},
            },
            "history_db_path": ":memory:",
        }
        memory = await asyncio.to_thread(main.AsyncMemory.from_config, config)
        memory.embedding_model = _KbEmbedder(bridge, self._embedder, memory.embedding_model.config)
        memory.llm = _GuardedExtractionLLM(bridge, self._http)
        old_db, memory.db = memory.db, _NoHistory()
        old_db.close()
        _without_keyword_model(memory.vector_store)
        await self._embedder.warm()
        return memory

    async def recall(self, subject_id: str, scope_key: str, *, query: str | None, k: int) -> list[MemoryItem]:
        """Up to ``k`` memories, newest first (no ``query``) or closest to ``query``."""
        memory = await self._ensure()
        filters = {"user_id": subject_id, "agent_id": scope_key}
        if query:
            raw = await memory.search(query, filters=filters, top_k=k)
            items = [item for item in map(_item, _items(raw)) if item is not None]
            return items[:k]
        raw = await memory.get_all(filters=filters, top_k=max(k, _LIST_LIMIT))
        items = [item for item in map(_item, _items(raw)) if item is not None]
        items.sort(key=lambda item: item.created_at.timestamp() if item.created_at else 0.0, reverse=True)
        return items[:k]

    async def remember(
        self,
        subject_id: str,
        scope_key: str,
        messages: Sequence[MemoryMessage],
        *,
        model: ExtractionModel | None,
    ) -> list[MemoryItem]:
        """Extract facts with ``model`` (or store ``messages`` verbatim when ``None``)."""
        memory = await self._ensure()
        payload = [{"role": message.role, "content": message.content} for message in messages]
        token = _EXTRACTION.set(_Extraction(model) if model is not None else None)
        try:
            raw = await memory.add(payload, user_id=subject_id, agent_id=scope_key, infer=model is not None)
        finally:
            _EXTRACTION.reset(token)
        kept = [item for item in _items(raw) if str(item.get("event", "ADD")).upper() in {"ADD", "UPDATE"}]
        return [item for item in map(_item, kept) if item is not None]

    async def forget(self, subject_id: str, scope_key: str | None = None) -> int:
        """Delete the subject's memories (one scope, or all) and their entity links."""
        memory = await self._ensure()
        # Mem0 clears the linked-entity collection only once it has been opened in this process.
        entities = await asyncio.to_thread(lambda: memory.entity_store)
        _without_keyword_model(entities)
        filters: dict[str, str] = {"user_id": subject_id}
        if scope_key:
            filters["agent_id"] = scope_key
        listed = await memory.get_all(filters=filters, top_k=10_000, show_expired=True)
        count = len(_items(listed))
        await memory.delete_all(user_id=subject_id, agent_id=scope_key or None)
        return count

    async def purge(self, subject_ids: Sequence[str]) -> int:
        """Forget every subject in every scope."""
        total = 0
        for subject in subject_ids:
            total += await self.forget(subject)
        return total

    async def aclose(self) -> None:
        """Close the HTTP client, Mem0's stores and the local Qdrant client."""
        memory, self._memory, self._building = self._memory, None, None
        if memory is not None:
            try:
                memory.close()
            except Exception:  # noqa: BLE001 - closing must not fail the shutdown
                log.debug("memory_close_failed", exc_info=True)
        if self._qdrant_client is not None:
            await asyncio.to_thread(self._qdrant_client.close)
        if self._owns_http:
            await self._http.aclose()
