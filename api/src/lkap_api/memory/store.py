"""The `MemoryStore` Protocol and the process's store (V5-40, D-V5-17).

A backend keeps short texts ("memories") per ``(subject_id, scope_key)``:
``subject_id`` is the caller's pseudonymous id (64 hex characters, never a
phone number, :mod:`lkap_api.memory.identity`) and ``scope_key`` is the agent
id or ``workspace``. The default backend is Mem0 OSS
(:mod:`lkap_api.memory.mem0`, the ``lkap-api[memory]`` extra, imported lazily);
without the extra the store is unavailable and every memory route says so.
Tests install a fake with :func:`set_memory_store`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from lkap_api.logging import get_logger
from lkap_api.settings import Settings

__all__ = [
    "ExtractionModel",
    "MemoryItem",
    "MemoryMessage",
    "MemoryStore",
    "MemoryUnavailableError",
    "get_memory_store",
    "parse_timestamp",
    "reset_memory_store",
    "set_memory_store",
]

log = get_logger(__name__)


def parse_timestamp(value: object) -> dt.datetime | None:
    """An ISO-8601 timestamp as an aware UTC datetime (naive values are UTC); ``None`` otherwise."""
    if isinstance(value, dt.datetime):
        stamp = value
    elif isinstance(value, str) and value.strip():
        try:
            stamp = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return stamp.replace(tzinfo=dt.UTC) if stamp.tzinfo is None else stamp.astimezone(dt.UTC)


class MemoryUnavailableError(RuntimeError):
    """The memory backend is not installed or could not be opened."""


@dataclass(frozen=True, slots=True)
class MemoryItem:
    """One stored memory."""

    id: str
    text: str
    created_at: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class MemoryMessage:
    """One transcript turn handed to :meth:`MemoryStore.remember` (already masked when due)."""

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class ExtractionModel:
    """The OpenAI-compatible chat model that picks facts out of a transcript.

    ``base_url`` is always a registry vendor's fixed base URL (never an admin-typed
    one): the backend's own HTTP client is not the platform's guarded client.
    ``api_key`` is a secret and is kept out of ``repr``.
    """

    provider_id: str
    model: str
    base_url: str
    api_key: str = field(repr=False)


class MemoryStore(Protocol):
    """A memory backend (research-v4 K §6.1: ``recall`` / ``remember`` / ``forget`` / ``purge``)."""

    async def recall(self, subject_id: str, scope_key: str, *, query: str | None, k: int) -> list[MemoryItem]:
        """Up to ``k`` memories of the subject in the scope, newest first (``query=None``) or
        the closest to ``query``."""
        ...

    async def remember(
        self,
        subject_id: str,
        scope_key: str,
        messages: Sequence[MemoryMessage],
        *,
        model: ExtractionModel | None,
    ) -> list[MemoryItem]:
        """Store what ``messages`` say about the subject; returns the memories added or updated.

        ``model=None`` stores the messages as they are (no model call).
        """
        ...

    async def forget(self, subject_id: str, scope_key: str | None = None) -> int:
        """Delete the subject's memories in ``scope_key`` (every scope when ``None``); returns how many."""
        ...

    async def purge(self, subject_ids: Sequence[str]) -> int:
        """Delete every memory of each subject, in every scope; returns how many."""
        ...

    async def aclose(self) -> None:
        """Release the backend's resources."""
        ...


_STORE: MemoryStore | None = None


def set_memory_store(store: MemoryStore | None) -> None:
    """Install the process's store (tests; ``None`` goes back to building the default)."""
    global _STORE
    _STORE = store


def get_memory_store(settings: Settings) -> MemoryStore:
    """The process's store, built on first use.

    Raises:
        MemoryUnavailableError: The ``lkap-api[memory]`` extra is not installed or the
            backend could not be opened.
    """
    global _STORE
    if _STORE is None:
        from lkap_api.memory.mem0 import Mem0MemoryStore  # noqa: PLC0415 - optional extra, loaded lazily

        _STORE = Mem0MemoryStore.from_settings(settings)
    return _STORE


async def reset_memory_store() -> None:
    """Close and forget the process's store (the api's shutdown)."""
    global _STORE
    store, _STORE = _STORE, None
    if store is not None:
        try:
            await store.aclose()
        except Exception:  # noqa: BLE001 - shutdown must not fail on a backend close
            log.warning("memory_store_close_failed", exc_info=True)
