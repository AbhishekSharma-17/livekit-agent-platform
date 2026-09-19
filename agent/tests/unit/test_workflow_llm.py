"""`PromptJsonStructuredLLM` extracts Pydantic models via prompt-for-JSON."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fakes.fake_llm import FakeLLM
from livekit.agents import APIConnectOptions, llm
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS
from pydantic import BaseModel

from lkap_agent.workflow_llm import PromptJsonStructuredLLM, StructuredExtractionError


class Claim(BaseModel):
    """A tiny extraction target."""

    policy_number: str
    injuries: bool
    city: str | None = None


INSTRUCTIONS = "Extract the claim details."
INPUT = "Policy H0-44721, basement flooded in Denver, nobody hurt."


async def test_extract_parses_a_clean_json_reply() -> None:
    """The happy path is one call and one validated model."""
    model = FakeLLM(['{"policy_number": "H0-44721", "injuries": false, "city": "Denver"}'])

    result = await PromptJsonStructuredLLM(model).extract(
        instructions=INSTRUCTIONS, input_text=INPUT, schema=Claim
    )

    assert isinstance(result, Claim)
    assert result.policy_number == "H0-44721"
    assert len(model.calls) == 1


async def test_extract_includes_the_schema_and_the_input_in_the_prompt() -> None:
    """The model is told exactly what shape to produce, and from what."""
    model = FakeLLM(['{"policy_number": "H0-44721", "injuries": false}'])

    await PromptJsonStructuredLLM(model).extract(instructions=INSTRUCTIONS, input_text=INPUT, schema=Claim)

    prompt = model.calls[0].prompt
    assert "policy_number" in prompt
    assert INSTRUCTIONS in prompt
    assert INPUT in prompt


@pytest.mark.parametrize(
    "reply",
    [
        '```json\n{"policy_number": "H0-44721", "injuries": false}\n```',
        '```\n{"policy_number": "H0-44721", "injuries": false}\n```',
        'Sure! {"policy_number": "H0-44721", "injuries": false} Hope that helps.',
    ],
    ids=["json-fence", "bare-fence", "chatty-prose"],
)
async def test_extract_recovers_json_from_a_decorated_reply(reply: str) -> None:
    """Models wrap JSON in fences and prose; the extractor unwraps both."""
    result = await PromptJsonStructuredLLM(FakeLLM([reply])).extract(
        instructions=INSTRUCTIONS, input_text=INPUT, schema=Claim
    )

    assert result.model_dump()["policy_number"] == "H0-44721"


async def test_extract_repairs_once_by_feeding_the_validation_error_back() -> None:
    """An invalid first reply gets one corrective round trip, not a failure."""
    model = FakeLLM(['{"injuries": false}', '{"policy_number": "H0-44721", "injuries": false}'])

    result = await PromptJsonStructuredLLM(model).extract(
        instructions=INSTRUCTIONS, input_text=INPUT, schema=Claim
    )

    assert result.model_dump()["policy_number"] == "H0-44721"
    assert len(model.calls) == 2
    assert "policy_number" in model.calls[1].prompt
    assert "rejected" in model.calls[1].prompt


async def test_extract_raises_after_the_repair_attempt_also_fails() -> None:
    """Two bad replies is a real failure the caller must handle."""
    model = FakeLLM(["not json at all"])

    with pytest.raises(StructuredExtractionError, match="Claim extraction failed"):
        await PromptJsonStructuredLLM(model).extract(
            instructions=INSTRUCTIONS, input_text=INPUT, schema=Claim
        )

    assert len(model.calls) == 2


async def test_extract_wraps_an_llm_error() -> None:
    """A vendor error surfaces as `StructuredExtractionError`, not a raw exception."""

    class _BrokenLLM(FakeLLM):
        def chat(self, **kwargs: Any) -> Any:
            raise RuntimeError("vendor exploded")

    with pytest.raises(StructuredExtractionError, match="workflow LLM call failed"):
        await PromptJsonStructuredLLM(_BrokenLLM()).extract(
            instructions=INSTRUCTIONS, input_text=INPUT, schema=Claim
        )


async def test_extract_times_out_rather_than_hanging_a_session() -> None:
    """A stalled workflow LLM must not wedge the conversation."""

    class _SlowStream(llm.LLMStream):
        async def _run(self) -> None:
            await asyncio.sleep(10)

    class _SlowLLM(FakeLLM):
        def chat(
            self,
            *,
            chat_ctx: llm.ChatContext,
            tools: list[llm.Tool] | None = None,
            conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
            **kwargs: Any,
        ) -> Any:
            return _SlowStream(self, chat_ctx=chat_ctx, tools=list(tools or []), conn_options=conn_options)

    with pytest.raises(StructuredExtractionError, match="timed out"):
        await PromptJsonStructuredLLM(_SlowLLM()).extract(
            instructions=INSTRUCTIONS, input_text=INPUT, schema=Claim, timeout_s=0.05
        )
