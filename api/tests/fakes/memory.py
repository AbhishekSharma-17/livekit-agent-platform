"""An in-memory `MemoryStore` for the V5-40 tests (the Mem0 backend's stand-in).

It enforces the pseudonymity rule the real backend relies on: every subject id
it is handed must be 64 lowercase hex characters (a keyed hash), so a raw phone
number or identity reaching the store fails the test at once. Extraction is
deterministic: with a model, each caller line becomes the memory
``"Caller said: <line>"``; verbatim, each message is stored as it is.
"""

from __future__ import annotations

import datetime as dt
import itertools
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from lkap_api.memory.store import ExtractionModel, MemoryItem, MemoryMessage

SUBJECT_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass
class RememberCall:
    subject_id: str
    scope_key: str
    messages: list[MemoryMessage]
    model: ExtractionModel | None


@dataclass
class FakeMemoryStore:
    """Memories per ``(subject_id, scope_key)``, plus a record of every call."""

    memories: dict[tuple[str, str], list[MemoryItem]] = field(default_factory=dict)
    remember_calls: list[RememberCall] = field(default_factory=list)
    recall_calls: list[tuple[str, str]] = field(default_factory=list)
    forget_calls: list[tuple[str, str | None]] = field(default_factory=list)
    purge_calls: list[list[str]] = field(default_factory=list)
    fail_with: Exception | None = None
    closed: bool = False
    _ids: itertools.count[int] = field(default_factory=itertools.count)

    @staticmethod
    def _check(subject_id: str) -> None:
        assert SUBJECT_RE.match(subject_id), f"the store received a non-pseudonymous id: {subject_id!r}"

    def seed(self, subject_id: str, scope_key: str, *texts: str) -> None:
        """Put memories in place, oldest first."""
        self._check(subject_id)
        base = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        bucket = self.memories.setdefault((subject_id, scope_key), [])
        for text in texts:
            n = next(self._ids)
            bucket.append(MemoryItem(id=f"m{n}", text=text, created_at=base + dt.timedelta(minutes=n)))

    def count(self) -> int:
        return sum(len(items) for items in self.memories.values())

    async def recall(self, subject_id: str, scope_key: str, *, query: str | None, k: int) -> list[MemoryItem]:
        self._check(subject_id)
        self.recall_calls.append((subject_id, scope_key))
        if self.fail_with is not None:
            raise self.fail_with
        items = list(self.memories.get((subject_id, scope_key), []))
        items.sort(key=lambda item: item.created_at or dt.datetime.min.replace(tzinfo=dt.UTC), reverse=True)
        return items[:k]

    async def remember(
        self,
        subject_id: str,
        scope_key: str,
        messages: Sequence[MemoryMessage],
        *,
        model: ExtractionModel | None,
    ) -> list[MemoryItem]:
        self._check(subject_id)
        self.remember_calls.append(RememberCall(subject_id, scope_key, list(messages), model))
        if self.fail_with is not None:
            raise self.fail_with
        if model is None:
            texts = [message.content for message in messages]
        else:
            texts = [f"Caller said: {message.content}" for message in messages if message.role == "user"]
        before = len(self.memories.get((subject_id, scope_key), []))
        self.seed(subject_id, scope_key, *texts)
        return self.memories[(subject_id, scope_key)][before:]

    async def forget(self, subject_id: str, scope_key: str | None = None) -> int:
        self._check(subject_id)
        self.forget_calls.append((subject_id, scope_key))
        if self.fail_with is not None:
            raise self.fail_with
        removed = 0
        for key in [key for key in self.memories if key[0] == subject_id and scope_key in (None, key[1])]:
            removed += len(self.memories.pop(key))
        return removed

    async def purge(self, subject_ids: Sequence[str]) -> int:
        self.purge_calls.append(list(subject_ids))
        removed = 0
        for subject in subject_ids:
            removed += await self.forget(subject)
        return removed

    async def aclose(self) -> None:
        self.closed = True
