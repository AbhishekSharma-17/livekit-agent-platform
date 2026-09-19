"""Live smoke tests against LiveKit Inference (`-m live`).

These are the only tests in W1-AGENT-CORE that touch the network, and they need
LiveKit credentials only — no vendor keys — because everything runs through
LiveKit Inference (docs/ARCHITECTURE.md D8). Run with:

    uv run pytest -m live -v

They never join a room and never dispatch `lkap-agent`, so the unrelated
`other-project-agent` worker in the same LiveKit project is untouched.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from livekit.agents import Agent, AgentSession, inference
from pydantic import BaseModel

from lkap_agent.platform_agent import compose_instructions
from lkap_agent.workflow_llm import PromptJsonStructuredLLM

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("LIVEKIT_API_KEY") and os.environ.get("LIVEKIT_API_SECRET")),
        reason="LIVEKIT_API_KEY/LIVEKIT_API_SECRET are required for live Inference tests",
    ),
]

LLM_MODEL = "google/gemma-4-31b-it"


def _inference_llm() -> Any:
    """An Inference LLM built the way `ProviderFactory` builds it.

    No explicit credentials: the SDK reads `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`
    from the environment (DECISIONS-W2 D-W2-6).
    """
    return inference.LLM(LLM_MODEL)


async def test_an_inference_session_answers_a_typed_turn() -> None:
    """A text-mode session on LiveKit Inference produces an assistant message.

    This is the cheapest end-to-end proof that the default dev pipeline works
    with LiveKit credentials alone, before any room or dispatch is involved.
    """
    session: AgentSession[Any] = AgentSession(llm=_inference_llm(), stt=None, tts=None, vad=None)
    agent = Agent(
        instructions=compose_instructions(
            "You are a terse assistant. Answer in one short sentence.", mode="cascaded"
        )
    )
    await session.start(agent)
    try:
        result = await session.run(user_input="Say hello and nothing else.")
        result.expect.next_event().is_message(role="assistant")
    finally:
        await session.aclose()


class _Extracted(BaseModel):
    """The shape the workflow LLM must produce."""

    policy_number: str
    injuries: bool


async def test_workflow_llm_extracts_structured_json_from_a_real_model() -> None:
    """`PromptJsonStructuredLLM` gets valid JSON out of a real Inference model.

    The design does not rely on provider-side structured output
    (docs/ARCHITECTURE.md §15.4); this test is what proves the prompt-for-JSON
    fallback is good enough on the default dev model.
    """
    workflow = PromptJsonStructuredLLM(_inference_llm())

    result = await workflow.extract(
        instructions="Extract the policy number and whether anyone was injured.",
        input_text="Policy H0-44721, basement flooded yesterday in Denver, nobody was hurt.",
        schema=_Extracted,
        timeout_s=60,
    )

    assert isinstance(result, _Extracted)
    assert "44721" in result.policy_number
    assert result.injuries is False
