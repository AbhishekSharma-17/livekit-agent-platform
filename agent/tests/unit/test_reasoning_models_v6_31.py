"""V6-31: reasoning models and request parameters a model does not accept.

On 2026-09-29 every turn on ``openai/gpt-6-luna`` through OpenRouter failed with a 404 ("No
endpoints found that can handle the requested parameters"): the platform sends
``provider.require_parameters=true`` and the registry's ``temperature``, which no Luna endpoint
accepts. The worker now leaves out what the model's capability view says it refuses and sends the
lowest reasoning effort by default. Nothing here opens a socket: each chat request is answered by
``respx`` and its body read back.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from livekit.agents import APIConnectOptions, RunContext, function_tool, inference, llm
from lkap_contracts.agent_config import ResolvedProvider
from lkap_contracts.providers import ModelCapabilities, get
from structlog.testing import capture_logs

from lkap_agent.providers.factory import ProviderFactory
from lkap_agent.providers.openrouter_llm import OpenRouterLLM
from lkap_agent.providers.reasoning import filter_request_kwargs, llm_capability_kwargs

API_KEY = "sk-or-v1-test-not-real"
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

#: `openai/gpt-6-luna` as the api resolves it from OpenRouter's catalog record (2026-09-29).
LUNA_VIEW = ModelCapabilities(
    vision=True,
    tools=True,
    context_tokens=1_050_000,
    source="catalog",
    reasoning=True,
    reasoning_efforts=["none", "low", "medium", "high", "xhigh", "max"],
    request_parameters=[
        "include_reasoning",
        "max_completion_tokens",
        "max_tokens",
        "reasoning",
        "reasoning_effort",
        "response_format",
        "seed",
        "structured_outputs",
        "tool_choice",
        "tools",
    ],
)
#: `openai/gpt-4.1-mini` from the same catalog: no reasoning, takes `temperature`.
MINI_VIEW = ModelCapabilities(
    vision=True,
    tools=True,
    source="catalog",
    reasoning=False,
    reasoning_efforts=[],
    request_parameters=[
        "max_completion_tokens",
        "max_tokens",
        "response_format",
        "seed",
        "structured_outputs",
        "temperature",
        "tool_choice",
        "tools",
        "top_p",
    ],
)


async def lookup_policy(context: RunContext[Any], policy_number: str) -> str:
    """Find a policy.

    Args:
        policy_number: The number on the card.
    """
    return "found"


@pytest.fixture
def inference_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``inference.LLM`` mints its token from the worker's LiveKit key pair (placeholders here)."""
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret")


def _openrouter(model: str, capabilities: ModelCapabilities | None, **fields: Any) -> ResolvedProvider:
    """``openrouter-llm`` as the api resolves it: registry defaults (temperature 0.7) plus ``fields``."""
    spec = get("openrouter-llm")
    kwargs: dict[str, Any] = {f.name: f.default for f in spec.fields if f.default is not None}
    kwargs.update(fields)
    kwargs["api_key"] = API_KEY
    return ResolvedProvider(
        provider_id=spec.id,
        python_class=spec.python_class,
        model=model,
        kwargs=kwargs,
        capabilities=capabilities,
    )


async def _request_body(model: llm.LLM, **chat_kwargs: Any) -> dict[str, Any]:
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(400, json={"error": {"message": "stop here"}})
    )
    chat_ctx = llm.ChatContext.empty()
    chat_ctx.add_message(role="user", content="my policy is PX-1")
    stream = model.chat(
        chat_ctx=chat_ctx,
        tools=[function_tool(lookup_policy)],
        conn_options=APIConnectOptions(max_retry=0, timeout=5),
        **chat_kwargs,
    )
    with pytest.raises(Exception):  # noqa: B017 - the mocked 400 ends the stream
        async with stream:
            async for _ in stream:
                pass
    assert route.called
    body: dict[str, Any] = json.loads(route.calls.last.request.content)
    return body


@respx.mock
async def test_luna_request_drops_temperature_keeps_tools_and_requires_parameters() -> None:
    built = ProviderFactory().build("llm", _openrouter("openai/gpt-6-luna", LUNA_VIEW))
    assert isinstance(built, OpenRouterLLM)

    body = await _request_body(built)

    assert body["model"] == "openai/gpt-6-luna"
    assert "temperature" not in body
    assert "parallel_tool_calls" not in body
    assert body["reasoning_effort"] == "none"  # the lowest Luna lists: the voice default
    assert body["provider"] == {"require_parameters": True}
    assert [tool["function"]["name"] for tool in body["tools"]] == ["lookup_policy"]
    assert body["tool_choice"] == "auto"  # Luna lists tool_choice, so it stays


@respx.mock
async def test_luna_request_drops_parallel_tool_calls_passed_per_request() -> None:
    built = ProviderFactory().build("llm", _openrouter("openai/gpt-6-luna", LUNA_VIEW))

    body = await _request_body(built, parallel_tool_calls=True)

    assert "parallel_tool_calls" not in body
    assert "temperature" not in body


@pytest.mark.parametrize(("configured", "sent"), [("high", "high"), ("low", "low"), ("minimal", "low")])
@respx.mock
async def test_luna_request_carries_the_configured_effort_or_the_next_one_it_lists(
    configured: str, sent: str
) -> None:
    built = ProviderFactory().build(
        "llm", _openrouter("openai/gpt-6-luna", LUNA_VIEW, reasoning_effort=configured)
    )

    body = await _request_body(built)

    assert body["reasoning_effort"] == sent


@respx.mock
async def test_non_reasoning_model_request_is_unchanged_by_its_capability_view() -> None:
    today = await _request_body(ProviderFactory().build("llm", _openrouter("openai/gpt-4.1-mini", None)))
    with_view = await _request_body(
        ProviderFactory().build("llm", _openrouter("openai/gpt-4.1-mini", MINI_VIEW))
    )

    assert with_view == today
    assert with_view["temperature"] == 0.7
    assert "reasoning_effort" not in with_view
    assert "parallel_tool_calls" not in with_view
    assert with_view["provider"] == {"require_parameters": True}


@respx.mock
async def test_unknown_capabilities_keep_todays_request() -> None:
    body = await _request_body(ProviderFactory().build("llm", _openrouter("openai/gpt-6-luna", None)))

    assert body["temperature"] == 0.7
    assert "reasoning_effort" not in body
    assert body["provider"] == {"require_parameters": True}


@respx.mock
async def test_a_stored_require_parameters_false_workaround_still_builds() -> None:
    built = ProviderFactory().build(
        "llm", _openrouter("openai/gpt-6-luna", LUNA_VIEW, provider='{"require_parameters": false}')
    )

    body = await _request_body(built)

    assert body["provider"] == {"require_parameters": False}
    assert "temperature" not in body


def test_workflow_llm_slot_gets_the_same_shaping() -> None:
    built = ProviderFactory().build("workflow_llm", _openrouter("openai/gpt-6-luna", LUNA_VIEW))
    assert isinstance(built, OpenRouterLLM)
    assert built.lkap_request_parameters == frozenset(LUNA_VIEW.request_parameters or [])
    assert built._opts.reasoning_effort == "none"
    assert not isinstance(built._opts.temperature, float)


def test_inference_gpt5_gets_the_lowest_effort_in_extra_kwargs(inference_env: None) -> None:
    spec = get("livekit-inference-llm")
    model = next(m for m in spec.models if m.id == "openai/gpt-5.5")
    view = ModelCapabilities(
        reasoning=model.reasoning, reasoning_efforts=model.reasoning_efforts, source="registry"
    )
    provider = ResolvedProvider(
        provider_id=spec.id,
        python_class=spec.python_class,
        model=model.id,
        kwargs={"temperature": 0.7},
        capabilities=view,
    )

    built = ProviderFactory().build("llm", provider)

    assert isinstance(built, inference.LLM)
    assert built._opts.extra_kwargs == {"temperature": 0.7, "reasoning_effort": "none"}


def test_inference_default_model_is_unchanged(inference_env: None) -> None:
    spec = get("livekit-inference-llm")
    provider = ResolvedProvider(
        provider_id=spec.id,
        python_class=spec.python_class,
        model="google/gemma-4-31b-it",
        kwargs={"temperature": 0.7},
        capabilities=ModelCapabilities(vision=False, tools=True, source="registry"),
    )

    built = ProviderFactory().build("llm", provider)

    assert built._opts.extra_kwargs == {"temperature": 0.7}


def test_llm_capability_kwargs_logs_what_it_dropped_at_debug() -> None:
    with capture_logs() as logs:
        shaped = llm_capability_kwargs(
            get("openrouter-llm"),
            {"model": "openai/gpt-6-luna", "temperature": 0.7, "top_p": 0.9, "reasoning_effort": ""},
            LUNA_VIEW,
        )

    assert shaped == {"model": "openai/gpt-6-luna", "reasoning_effort": "none"}
    (entry,) = [log for log in logs if log["event"] == "llm options shaped by the model's capabilities"]
    assert entry["log_level"] == "debug"
    assert entry["dropped"] == ["temperature", "top_p"]
    assert entry["reasoning_effort"] == "none"


def test_llm_capability_kwargs_removes_an_empty_stored_effort_without_a_view() -> None:
    shaped = llm_capability_kwargs(get("openai-llm"), {"model": "gpt-4.1", "reasoning_effort": " "}, None)
    assert shaped == {"model": "gpt-4.1"}


def test_filter_request_kwargs_keeps_everything_when_nothing_is_known() -> None:
    extra = {"temperature": 0.7, "parallel_tool_calls": True, "tool_choice": "auto"}
    assert filter_request_kwargs(extra, None) == extra


def test_filter_request_kwargs_drops_default_tool_choice_only() -> None:
    accepted = {"tools"}
    assert filter_request_kwargs({"tool_choice": "auto", "extra_body": {}}, accepted) == {"extra_body": {}}
    assert filter_request_kwargs({"tool_choice": "required"}, accepted) == {"tool_choice": "required"}
