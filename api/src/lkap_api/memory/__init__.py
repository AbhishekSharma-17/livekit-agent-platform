"""Caller memory across sessions (V5-40, D-V5-17, research-v4 knowledge-and-memory §6.1).

Opt-in per agent (``AgentConfig.memory``). Callers are known by a pseudonymous
subject id, never by their number (:mod:`~lkap_api.memory.identity`); the
memories live in a backend behind the :class:`~lkap_api.memory.store.MemoryStore`
Protocol, Mem0 OSS by default (:mod:`~lkap_api.memory.mem0`, the
``lkap-api[memory]`` extra, imported lazily).

* :mod:`~lkap_api.memory.service` — recall, remember, forget, purge, retention.
* :mod:`~lkap_api.memory.jobs` — ``memory_remember`` / ``memory_purge``.
* :mod:`~lkap_api.memory.router` — the console routes.
* :mod:`~lkap_api.memory.validation` — save-time warnings, registered into
  ``config_service.VALIDATORS`` by importing this package.
"""

from __future__ import annotations

from lkap_api.memory import validation as _validation  # noqa: F401 - registers the validator
from lkap_api.memory.jobs import enqueue_remember_if_due
from lkap_api.memory.service import recall_for_session, sweep_memory_retention
from lkap_api.memory.store import MemoryStore, get_memory_store, reset_memory_store, set_memory_store

__all__ = [
    "MemoryStore",
    "enqueue_remember_if_due",
    "get_memory_store",
    "recall_for_session",
    "reset_memory_store",
    "set_memory_store",
    "sweep_memory_retention",
]
