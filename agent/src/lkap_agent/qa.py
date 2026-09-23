"""Post-call QA judge, run in the worker (PLAN-V2 §8 rulings R-V2-5, R-V2-6).

The worker is the only process where LiveKit Inference is callable, so it
scores its own session: after the summary is posted (the ≤10 s summary target
is unchanged), it asks the judge LLM for a verdict on the transcript through
the existing `StructuredLLM` (prompt for JSON, one repair retry, 30 s budget)
and reports it with ``PUT /internal/v1/sessions/{id}/qa``:

* `qa.enabled` false → ``status="skipped"``, no LLM call;
* a verdict → ``status="done"`` with score, sentiment, tags, summary, raw, model;
* no judge, an LLM error, a timeout or JSON that still fails after the repair
  → ``status="failed"`` with `error`.

R-V2-6: the judge is built from the **api-resolved** ``resolved["qa_llm"]``
slot (:func:`build_judge`) — the api already walks
``qa.model -> workflow_llm -> llm -> Inference default`` and attaches secrets
at resolve time (``GET /internal/v1/sessions/{id}/resolved`` /
``POST /internal/v1/sessions/start``), so this module never walks that chain
or reads a credential itself.

The default rubric and the verdict/request schemas live in
``lkap_contracts.qa`` (moved there by the V2-08 follow-up, R-V2-5) so the
worker and the api share one definition.
"""

from __future__ import annotations

from typing import Final

from lkap_contracts.agent_config import QaConfig, ResolvedAgentConfig
from lkap_contracts.api_models import TranscriptTurn
from lkap_contracts.qa import DEFAULT_RUBRIC_PROMPT, DEFAULT_TAGS, QaVerdict, SessionQaIn
from packs.base import StructuredLLM

from lkap_agent.logging import get_logger
from lkap_agent.providers.factory import ProviderBuildError, ProviderFactory
from lkap_agent.workflow_llm import PromptJsonStructuredLLM

__all__ = [
    "DEFAULT_RUBRIC_PROMPT",
    "DEFAULT_TAGS",
    "JUDGE_TIMEOUT_S",
    "QaVerdict",
    "SessionQaIn",
    "build_judge",
    "render_transcript",
    "score_session",
]

logger = get_logger(__name__)

#: R-V2-5: the whole judge call chain (first try + repair) gets this long.
JUDGE_TIMEOUT_S: Final[float] = 30.0

#: Longest error text sent to the api.
_MAX_ERROR_CHARS: Final[int] = 500


def build_judge(
    factory: ProviderFactory, resolved: ResolvedAgentConfig
) -> tuple[StructuredLLM | None, str | None, str | None]:
    """Build the QA judge from the api-resolved ``qa_llm`` slot (R-V2-6).

    Args:
        factory: The worker's `ProviderFactory`.
        resolved: The session's resolved config; `resolved["qa_llm"]` is
            present, with secrets, whenever `AgentConfig.qa.enabled` — the api
            attaches it at resolve time, so this function never walks
            `qa.model -> workflow_llm -> llm -> Inference default` itself.

    Returns:
        `(judge, model_label, error)`; `judge` is `None` exactly when `error`
        is set. A missing slot with `qa.enabled` (`error="qa_llm not
        resolved"`) means the api-side resolve step did not run — it should
        not happen once R-V2-6 is deployed end to end.
    """
    provider = resolved.resolved.get("qa_llm")
    if provider is None:
        return None, None, "qa_llm not resolved"
    label = f"{provider.provider_id}:{provider.model}" if provider.model else provider.provider_id
    try:
        model = factory.build("qa_llm", provider, mode=resolved.config.pipeline.mode)
    except ProviderBuildError as exc:
        return None, label, str(exc)
    return PromptJsonStructuredLLM(model), label, None


def render_transcript(turns: list[TranscriptTurn]) -> str:
    """Render transcript turns as `User: …` / `Agent: …` lines for the judge."""
    lines: list[str] = []
    for turn in turns:
        speaker = "User" if turn.role == "user" else "Agent"
        suffix = " [interrupted]" if turn.interrupted else ""
        lines.append(f"{speaker}: {turn.text}{suffix}")
    return "\n".join(lines)


async def score_session(
    *,
    qa: QaConfig,
    transcript: list[TranscriptTurn],
    judge: StructuredLLM | None,
    model_label: str | None,
    judge_error: str | None = None,
    timeout_s: float = JUDGE_TIMEOUT_S,
) -> SessionQaIn:
    """Produce the session's QA verdict. Never raises.

    Args:
        qa: The agent's `QaConfig`.
        transcript: The session transcript as posted in the summary.
        judge: The structured LLM to ask, or `None` when none could be built.
        model_label: `provider_id:model` of the judge, stored as `session_qa.model`.
        judge_error: Why `judge` is `None`, when it is.
        timeout_s: Budget for the judge call chain.

    Returns:
        The body for ``PUT /internal/v1/sessions/{id}/qa``.
    """
    if not qa.enabled:
        return SessionQaIn(status="skipped")
    if not transcript:
        return SessionQaIn(status="skipped", model=model_label, error="no conversation to score")
    if judge is None:
        return SessionQaIn(
            status="failed", model=model_label, error=(judge_error or "no judge model")[:_MAX_ERROR_CHARS]
        )
    try:
        result = await judge.extract(
            instructions=qa.rubric_prompt or DEFAULT_RUBRIC_PROMPT,
            input_text=f"Transcript:\n\n{render_transcript(transcript)}",
            schema=QaVerdict,
            timeout_s=timeout_s,
        )
    except Exception as exc:
        logger.warning("qa judge failed", error=str(exc), model=model_label)
        return SessionQaIn(status="failed", model=model_label, error=str(exc)[:_MAX_ERROR_CHARS])
    verdict = QaVerdict.model_validate(result.model_dump())
    return SessionQaIn(
        status="done",
        score=verdict.score,
        sentiment=verdict.sentiment,
        tags=list(verdict.tags),
        summary=verdict.summary or None,
        raw=verdict.model_dump(mode="json"),
        model=model_label,
    )
