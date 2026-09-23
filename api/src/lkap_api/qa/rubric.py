"""Default QA rubric: Dograh's tag set (`docs/research-v2/dograh.md` §3.6), 1-10 score, sentiment, summary.

`AgentConfig.qa.rubric_prompt` overrides this entirely (CONTRACTS-V2 §4.3);
this is only the platform default.

The rubric text and tag set moved to `lkap_contracts.qa` (V2-08 follow-up,
R-V2-5) so this module and the worker's `lkap_agent.qa` use exactly the same
wording — re-exported here so existing call sites (`from lkap_api.qa.rubric
import DEFAULT_TAGS`) keep working.
"""

from __future__ import annotations

from lkap_contracts.qa import DEFAULT_RUBRIC_PROMPT, DEFAULT_TAGS

__all__ = ["DEFAULT_RUBRIC_PROMPT", "DEFAULT_TAGS", "build_prompt"]


def build_prompt(*, rubric_prompt: str | None, transcript_text: str) -> tuple[str, str]:
    """Return `(system_prompt, user_prompt)` for the judge LLM call.

    Args:
        rubric_prompt: `AgentConfig.qa.rubric_prompt`, or `None` for the default.
        transcript_text: The rendered conversation transcript.

    Returns:
        A `(system, user)` message pair.
    """
    system = rubric_prompt or DEFAULT_RUBRIC_PROMPT
    user = f"Transcript:\n\n{transcript_text}"
    return system, user
