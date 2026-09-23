"""Post-call QA rubric and verdict schema, shared by the worker and the api (R-V2-5).

PLAN-V2 §8 ruling R-V2-5: the judge runs **in the worker** (the only process
where LiveKit Inference is callable) at session end, and reports its verdict
with ``PUT /internal/v1/sessions/{id}/qa``. The api's own ``qa/scorer.py``
only re-scores, and only when the resolved judge is an OpenAI-compatible
vendor-key provider it can call directly — never Inference. Both sides need
the exact same rubric text and the exact same verdict/request shapes, hence
this module: previously the worker (``agent/src/lkap_agent/qa.py``) carried a
local, TODO-marked copy of everything below, with the note "when this module
exists, import from it instead."
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

__all__ = [
    "DEFAULT_RUBRIC_PROMPT",
    "DEFAULT_TAGS",
    "QaScoredBy",
    "QaStatus",
    "QaVerdict",
    "SessionQaIn",
]

QaStatus = Literal["pending", "done", "failed", "skipped"]
QaScoredBy = Literal["worker", "api"]

#: Dograh's default QA tag set (docs/research-v2/dograh.md §3.6).
DEFAULT_TAGS: tuple[str, ...] = (
    "DEAD_AIR",
    "USER_FRUSTRATED",
    "ASSISTANT_IN_LOOP",
    "ASSISTANT_REPLY_IMPROPER",
    "USER_NOT_UNDERSTANDING",
    "HEARING_ISSUES",
    "UNCLEAR_CONVERSATION",
    "USER_REQUESTING_FEATURE",
    "ASSISTANT_LACKS_EMPATHY",
    "USER_DETECTS_AI",
)

_TAG_LIST = "\n".join(f"- {tag}" for tag in DEFAULT_TAGS)

#: The platform default rubric; `AgentConfig.qa.rubric_prompt` replaces it entirely.
DEFAULT_RUBRIC_PROMPT: str = (
    "You are a call-quality reviewer for a voice AI agent. You will be given the full transcript "
    "of one finished conversation between the agent and a caller. Score it strictly on what is in "
    "the transcript; do not assume anything that is not written.\n\n"
    f"Apply zero or more of these tags where they occurred in the conversation:\n{_TAG_LIST}\n\n"
    "Reply with a single JSON object: score (integer 1-10, 10 is a flawless call), sentiment "
    '("positive", "neutral" or "negative"), tags (zero or more of the tags above) and summary '
    "(one or two sentences on what happened and why the call was scored this way)."
)


class QaVerdict(BaseModel):
    """The judge LLM's expected JSON reply, asked for by :data:`DEFAULT_RUBRIC_PROMPT`."""

    score: int = Field(ge=1, le=10)
    sentiment: Literal["positive", "neutral", "negative"]
    tags: list[str] = []
    summary: str = ""


class SessionQaIn(BaseModel):
    """``PUT /internal/v1/sessions/{id}/qa`` (CONTRACTS-V2 §3.4, R-V2-5).

    Posted by the worker after it runs the judge (or decides not to);
    ``status="skipped"`` when ``AgentConfig.qa.enabled`` is false,
    ``"failed"`` when no judge could be built or the judge call/JSON-repair
    both failed, ``"done"`` with a full :class:`QaVerdict` otherwise.
    """

    status: Literal["done", "failed", "skipped"]
    score: int | None = None
    sentiment: Literal["positive", "neutral", "negative"] | None = None
    tags: list[str] = []
    summary: str | None = None
    raw: dict[str, Any] | None = None
    model: str | None = None
    error: str | None = None
