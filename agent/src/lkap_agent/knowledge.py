"""Knowledge auto-inject: the retrieval gate, the conversation query and the pre-fetch (V5-06).

`PlatformAgent._inject_knowledge` runs once per completed user turn. This
module holds the parts of it that do not need the agent:

* **The gate** (:func:`skip_reason`): an empty turn, a backchannel ("yes",
  "okay", "haan ji"), a digits-only turn or one under three words never
  searches (research-v4 knowledge-and-memory P0-1).
* **The query** (:func:`build_query`): in ``conversation`` mode the search
  text is the user's turn, the previous assistant sentence and the flow
  variables, concatenated (P1-1, no extra LLM call), so a follow-up such as
  "and the deductible?" searches with what it refers to.
* **The note** (:func:`compose_note`): the hits framed for the model, trimmed
  to ``max_inject_tokens`` (approximate tokens, four characters each).
* **The dedupe ring** (:class:`RecentChunks`): chunk ids injected in the last
  three user turns are not injected again.
* **The pre-fetch** (:class:`KnowledgePrefetch`): a session-level
  ``user_input_transcribed`` listener (:func:`prefetch_listener`) asks the
  current agent to search the running transcript after 300 ms without new
  words; the hook then awaits that task for at most 150 ms and reuses its
  hits when the final text matches (Jaccard >= 0.6) and the knowledge-base
  scope is unchanged (P0-2). A flow handoff between interim and final
  changes the scope, so the hook searches again with the new node's.

State lives on :class:`KnowledgeState`, one per session (kept in
`SessionContext.userdata`), because a flow handoff replaces the agent between
an interim transcript and the end of the turn.

The pre-fetched note is still added to the per-turn context in the hook, not
to `Agent.chat_ctx` during the interim phase: the preemptive-generation spike
(``docs/v5/_briefs/v5-06-spike.md``) records why.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import re
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from lkap_contracts.agent_config import KNOWLEDGE_RERANK_VALUES, KnowledgeConfig
from lkap_contracts.api_models import KbHit, KbSearchOptions

from lkap_agent.logging import get_logger

__all__ = [
    "BACKCHANNEL_PHRASES",
    "INJECT_TIMEOUT_S",
    "KNOWLEDGE_STATE_KEY",
    "KnowledgePrefetch",
    "KnowledgeState",
    "RecentChunks",
    "SkipReason",
    "approx_tokens",
    "build_query",
    "compose_note",
    "jaccard",
    "knowledge_state",
    "last_sentence",
    "prefetch_listener",
    "search_options",
    "skip_reason",
    "words",
]

logger = get_logger(__name__)

#: Upper bound on the auto-inject search in the hook; a slower api must not delay the reply.
INJECT_TIMEOUT_S: Final[float] = 0.4

#: Quiet time after the last transcript update before the pre-fetch searches.
PREFETCH_DEBOUNCE_S: Final[float] = 0.3

#: How long the hook waits for a pre-fetch that is still running.
PREFETCH_AWAIT_S: Final[float] = 0.15

#: Word overlap at which the final text counts as the pre-fetched one.
PREFETCH_REUSE_JACCARD: Final[float] = 0.6

#: A turn with fewer words than this never searches (when `skip_short_turns`).
MIN_QUERY_WORDS: Final[int] = 3

#: How many user turns an injected chunk id stays in the dedupe ring.
DEDUPE_TURNS: Final[int] = 3

#: Approximate characters per token for the `max_inject_tokens` budget.
CHARS_PER_TOKEN: Final[int] = 4

#: A truncated hit keeps at least this many tokens, or it is left out.
MIN_TRUNCATED_TOKENS: Final[int] = 24

#: At most this many flow variables, and this many characters of them, join the query.
MAX_QUERY_VARIABLES: Final[int] = 8
MAX_QUERY_VARIABLE_CHARS: Final[int] = 240

#: `SessionContext.userdata` key of the session's :class:`KnowledgeState`.
KNOWLEDGE_STATE_KEY: Final[str] = "_lkap_knowledge"

#: Whole-turn backchannels: acknowledgements that never need a search (en + hi transliterations).
BACKCHANNEL_PHRASES: Final[frozenset[str]] = frozenset(
    {
        # English
        "yes",
        "yeah",
        "yep",
        "yup",
        "yes please",
        "no",
        "nope",
        "no thanks",
        "ok",
        "okay",
        "okey",
        "okay thanks",
        "ok thanks",
        "okay thank you",
        "sure",
        "right",
        "alright",
        "all right",
        "fine",
        "cool",
        "great",
        "good",
        "perfect",
        "got it",
        "i see",
        "sounds good",
        "go on",
        "go ahead",
        "thanks",
        "thank you",
        "thank you so much",
        "uh huh",
        "mhm",
        "mm",
        "mmm",
        "hmm",
        "hm",
        "oh",
        "ah",
        "hello",
        "hi",
        "hey",
        # Hindi (romanised)
        "haan",
        "haa",
        "han",
        "haan ji",
        "haanji",
        "ha ji",
        "ji",
        "ji haan",
        "ji ha",
        "nahi",
        "nahin",
        "theek hai",
        "thik hai",
        "theek",
        "accha",
        "acha",
        "achha",
        "accha ji",
        "sahi hai",
        "bilkul",
        "ok ji",
        "chalo",
        "dhanyavaad",
        "dhanyavad",
        "shukriya",
    }
)

#: Single words a backchannel is built from ("yes yes okay", "haan haan theek").
_BACKCHANNEL_WORDS: Final[frozenset[str]] = frozenset(
    word for phrase in BACKCHANNEL_PHRASES for word in phrase.split()
) - {"i", "see", "go", "on", "ahead", "so", "much", "please", "sounds", "got", "it", "all"}

_WORD_RE: Final = re.compile(r"[^\W_]+(?:'[^\W_]+)?")
_SENTENCE_END_RE: Final = re.compile(r"(?<=[.!?])\s+")

SkipReason = Literal["empty", "backchannel", "digits", "short"]


# --------------------------------------------------------------------------- the gate


def words(text: str) -> list[str]:
    """Lower-cased words of `text` (letters and digits; an inner apostrophe stays)."""
    return [match.group(0) for match in _WORD_RE.finditer(text.lower())]


def skip_reason(text: str, *, skip_short_turns: bool = True) -> SkipReason | None:
    """Why the auto-inject search should skip this turn, or `None` to search.

    Args:
        text: The user's turn.
        skip_short_turns: `KnowledgeConfig.skip_short_turns`; off, only an empty turn skips.

    Returns:
        ``"empty"``, ``"backchannel"``, ``"digits"`` or ``"short"``; `None` when it should search.
    """
    tokens = words(text)
    if not tokens:
        return "empty"
    if not skip_short_turns:
        return None
    if " ".join(tokens) in BACKCHANNEL_PHRASES or all(t in _BACKCHANNEL_WORDS for t in tokens):
        return "backchannel"
    if all(t.isdigit() for t in tokens):
        return "digits"
    if len(tokens) < MIN_QUERY_WORDS:
        return "short"
    return None


def jaccard(a: str, b: str) -> float:
    """Word-set overlap of `a` and `b` in [0, 1]; two empty texts count as identical."""
    left, right = set(words(a)), set(words(b))
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)


# --------------------------------------------------------------------------- the query


def last_sentence(text: str) -> str:
    """The last sentence of `text` (the whole text when it has no sentence break)."""
    parts = [part.strip() for part in _SENTENCE_END_RE.split(text.strip()) if part.strip()]
    return parts[-1] if parts else ""


def _variable_text(variables: Mapping[str, Any]) -> str:
    values: list[str] = []
    for value in variables.values():
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, str | int | float):
            rendered = str(value).strip()
            if rendered:
                values.append(rendered)
        if len(values) >= MAX_QUERY_VARIABLES:
            break
    return " ".join(values)[:MAX_QUERY_VARIABLE_CHARS].strip()


def build_query(
    user_text: str,
    *,
    query_mode: str,
    previous_assistant: str = "",
    variables: Mapping[str, Any] | None = None,
) -> str:
    """The auto-inject search text for a user turn.

    Args:
        user_text: What the user said this turn.
        query_mode: `KnowledgeConfig.query_mode`; ``last_turn`` searches with `user_text` only.
        previous_assistant: The assistant's previous message; its last sentence joins the query.
        variables: Flow variables (scalars join the query; booleans and empty values do not).

    Returns:
        One search string: the user's words first, then the context, one per line.
    """
    user = user_text.strip()
    if query_mode != "conversation":
        return user
    parts = [user]
    sentence = last_sentence(previous_assistant)
    if sentence:
        parts.append(sentence)
    if variables:
        extra = _variable_text(variables)
        if extra:
            parts.append(extra)
    return "\n".join(part for part in parts if part)


# --------------------------------------------------------------------------- the note


def approx_tokens(text: str) -> int:
    """Approximate token count of `text` (four characters per token, rounded up)."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[: max(0, max_chars - 1)]
    space = cut.rfind(" ")
    if space > max_chars // 2:
        cut = cut[:space]
    return cut.rstrip() + "…"


def compose_note(hits: Sequence[KbHit], *, prefix: str, max_tokens: int) -> tuple[str, list[KbHit]]:
    """Frame `hits` for the model within `max_tokens`.

    Hits are kept best first while they fit; the first one that does not is
    truncated when at least :data:`MIN_TRUNCATED_TOKENS` of room is left, and
    the rest are dropped.

    Args:
        hits: The retrieved chunks, best first.
        prefix: The note's first line.
        max_tokens: `KnowledgeConfig.max_inject_tokens`.

    Returns:
        The note (empty when nothing fits) and the hits it carries.
    """
    budget = max_tokens * CHARS_PER_TOKEN
    lines: list[str] = []
    used: list[KbHit] = []
    length = len(prefix)
    for hit in hits:
        label = f"[{hit.filename}] "
        separator = 1 if not lines else 2  # "\n" after the prefix, "\n\n" between hits
        room = budget - length - separator - len(label)
        if room <= 0:
            break
        text = hit.text
        if len(text) > room:
            if room < MIN_TRUNCATED_TOKENS * CHARS_PER_TOKEN:
                break
            text = _truncate(text, room)
        lines.append(label + text)
        used.append(hit)
        length += separator + len(label) + len(text)
        if text is not hit.text:
            break
    if not lines:
        return "", []
    return prefix + "\n" + "\n\n".join(lines), used


# --------------------------------------------------------------------------- dedupe


class RecentChunks:
    """Chunk ids injected in the last `turns` user turns."""

    def __init__(self, turns: int = DEDUPE_TURNS) -> None:
        self._turns: deque[frozenset[str]] = deque(maxlen=max(turns, 0) or None)
        self._enabled = turns > 0

    def seen(self, chunk_id: str) -> bool:
        """Whether `chunk_id` was injected in the window."""
        return self._enabled and any(chunk_id in turn for turn in self._turns)

    def fresh(self, hits: Iterable[KbHit]) -> list[KbHit]:
        """`hits` without the ones injected in the window, order kept."""
        return [hit for hit in hits if not self.seen(hit.chunk_id)]

    def push(self, chunk_ids: Iterable[str]) -> None:
        """Close a user turn that injected `chunk_ids` (empty for a turn without a note)."""
        if self._enabled:
            self._turns.append(frozenset(chunk_ids))


# --------------------------------------------------------------------------- pre-fetch


@dataclass(slots=True)
class _Pending:
    text: str
    query: str
    scope: tuple[str, ...]
    #: The debounce elapsed and the search request went out.
    started: bool = False
    #: The search raised; the hook searches again rather than injecting nothing.
    failed: bool = False
    #: Loop time the search answered (the spike's measurement, see `KnowledgeState.last_final_at`).
    done_at: float | None = None
    task: asyncio.Task[list[KbHit]] | None = None


SearchFn = Callable[[str], Awaitable[list[KbHit]]]


class KnowledgePrefetch:
    """One debounced search of the running transcript; the hook takes its result.

    Args:
        debounce_s: Quiet time before the search starts.
        await_s: How long :meth:`take` waits for a search still running.
        reuse_jaccard: Word overlap at which the final text reuses the pre-fetch.
    """

    def __init__(
        self,
        *,
        debounce_s: float = PREFETCH_DEBOUNCE_S,
        await_s: float = PREFETCH_AWAIT_S,
        reuse_jaccard: float = PREFETCH_REUSE_JACCARD,
    ) -> None:
        self._debounce_s = debounce_s
        self._await_s = await_s
        self._reuse = reuse_jaccard
        self._pending: _Pending | None = None
        #: Loop time the last reused pre-fetch answered; `None` when the last `take` searched again.
        self.last_done_at: float | None = None

    @property
    def pending(self) -> bool:
        """Whether a pre-fetch is scheduled, running or finished and not yet taken."""
        return self._pending is not None

    def schedule(self, *, text: str, query: str, scope: Sequence[str], search: SearchFn) -> None:
        """(Re)start the debounced search for `query`; a newer call cancels the older one.

        Args:
            text: The running user transcript (compared with the final text).
            query: The search text built from it.
            scope: The knowledge bases the search covers (compared with the hook's).
            search: Runs the search; its exceptions become an empty result.
        """
        current = self._pending
        if current is not None and current.query == query and current.scope == tuple(scope):
            current.text = text
            return
        self.cancel()
        pending = _Pending(text=text, query=query, scope=tuple(scope))
        pending.task = asyncio.create_task(self._run(pending, search), name="lkap.knowledge.prefetch")
        self._pending = pending

    async def _run(self, pending: _Pending, search: SearchFn) -> list[KbHit]:
        await asyncio.sleep(self._debounce_s)
        pending.started = True
        try:
            hits = await search(pending.query)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("knowledge pre-fetch failed", exc_info=True)
            pending.failed = True
            hits = []
        pending.done_at = asyncio.get_running_loop().time()
        return hits

    async def take(self, *, text: str, scope: Sequence[str]) -> list[KbHit] | None:
        """The pre-fetched hits for the final `text`, or `None` to search now.

        The pending search is consumed either way. It is reused only when the
        scope is the same, `text` overlaps the pre-fetched text by at least the
        reuse threshold and the request already went out; one still running
        gets :data:`PREFETCH_AWAIT_S` to finish. One still in its debounce
        window was never sent, so it is cancelled and the hook searches now.
        """
        pending, self._pending = self._pending, None
        self.last_done_at = None
        if pending is None or pending.task is None:
            return None
        task = pending.task
        if not pending.started or pending.scope != tuple(scope) or jaccard(pending.text, text) < self._reuse:
            task.cancel()
            return None
        if not task.done():
            try:
                hits = await asyncio.wait_for(asyncio.shield(task), timeout=self._await_s)
            except TimeoutError:
                task.cancel()
                return None
            except Exception:  # noqa: BLE001 - a failed pre-fetch just means searching again
                return None
        elif task.cancelled() or task.exception() is not None:
            return None
        else:
            hits = task.result()
        if pending.failed:
            return None
        self.last_done_at = pending.done_at
        return hits

    def cancel(self) -> None:
        """Drop the pending pre-fetch, if any."""
        pending, self._pending = self._pending, None
        if pending is not None and pending.task is not None and not pending.task.done():
            pending.task.cancel()


@dataclass(slots=True)
class KnowledgeState:
    """The session's auto-inject state: the running transcript, the pre-fetch and the dedupe ring."""

    prefetch: KnowledgePrefetch = field(default_factory=KnowledgePrefetch)
    recent: RecentChunks = field(default_factory=RecentChunks)
    #: Final STT segments of the turn in progress (a turn can span several).
    finals: list[str] = field(default_factory=list)
    #: Loop time of the turn's latest final segment: the SDK takes its preemptive
    #: snapshot on final segments, so a pre-fetch that answered before this could
    #: have been in that snapshot (the spike's measurement).
    last_final_at: float | None = None

    def running_text(self, transcript: str, *, is_final: bool) -> str:
        """The turn so far: the final segments plus the latest interim one."""
        text = transcript.strip()
        if is_final:
            if text:
                self.finals.append(text)
            with contextlib.suppress(RuntimeError):
                self.last_final_at = asyncio.get_running_loop().time()
            return " ".join(self.finals)
        return " ".join([*self.finals, text]).strip()

    def ready_before_final(self) -> bool | None:
        """Whether the reused pre-fetch answered before the turn's last final segment.

        `None` when there is nothing to compare (no reused pre-fetch, or no final
        segment seen, e.g. a typed turn or a realtime model's own transcription).
        """
        done_at = self.prefetch.last_done_at
        if done_at is None or self.last_final_at is None:
            return None
        return done_at <= self.last_final_at

    def end_turn(self) -> None:
        """Forget the transcript of the turn that just completed."""
        self.finals.clear()
        self.last_final_at = None

    def close(self) -> None:
        """Cancel the pending pre-fetch (the session is closing)."""
        self.prefetch.cancel()
        self.finals.clear()


def knowledge_state(userdata: dict[str, Any]) -> KnowledgeState:
    """The session's :class:`KnowledgeState`, created on first use in `userdata`."""
    state = userdata.get(KNOWLEDGE_STATE_KEY)
    if not isinstance(state, KnowledgeState):
        state = KnowledgeState()
        userdata[KNOWLEDGE_STATE_KEY] = state
    return state


def prefetch_listener(session: Any) -> Callable[[Any], None]:
    """A `user_input_transcribed` handler that hands each transcript to the current agent.

    Registered once per session (the other `plan.session.on(...)` handlers
    live beside it). The agent is resolved at fire time, so after a flow
    handoff the new node's knowledge-base scope is used. Synchronous by SDK
    contract; the agent's `prefetch_knowledge(transcript, is_final)` only
    schedules work.
    """

    def _on_transcribed(ev: Any) -> None:
        try:
            agent = session.current_agent
        except RuntimeError:
            return
        prefetch = getattr(agent, "prefetch_knowledge", None)
        if not callable(prefetch):
            return
        with contextlib.suppress(Exception):
            prefetch(str(getattr(ev, "transcript", "") or ""), bool(getattr(ev, "is_final", False)))

    return _on_transcribed


# --------------------------------------------------------------------------- search options


def search_options(knowledge: KnowledgeConfig) -> KbSearchOptions:
    """The api search options an agent's knowledge config asks for.

    A rerank the platform does not implement yet (a future ``connection:<id>``)
    falls back to ``none`` and a floor outside [0, 1] is ignored; the api
    validator reports both as errors before a config is published.
    """
    rerank = knowledge.rerank if knowledge.rerank in KNOWLEDGE_RERANK_VALUES else "none"
    min_score = knowledge.min_score
    if min_score is not None and not 0.0 <= min_score <= 1.0:
        logger.warning("ignoring knowledge.min_score outside [0, 1]", min_score=min_score)
        min_score = None
    return KbSearchOptions.model_validate({"mode": knowledge.mode, "rerank": rerank, "min_score": min_score})
