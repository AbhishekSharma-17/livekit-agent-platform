"""`lkap_agent.tools.provider`: a connected app's action through Composio (V5-47, COMPOSIO.md §5, §8).

Composio is mocked at the HTTP transport boundary (`httpx.MockTransport` through the
tool's `transport_factory` seam); no network, no real key.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import httpx
import pytest
from fakes.fake_api import FakeApi
from fakes.fake_ctx import FakeRunContext
from livekit.agents import RunContext, ToolError, llm
from livekit.agents.llm import ToolFlag
from livekit.agents.voice.events import ToolCallEnded, ToolCallStarted, ToolExecutionUpdatedEvent
from lkap_contracts.tools import ProviderToolDefinition, ToolExecution

from lkap_agent.observability import SessionObserver
from lkap_agent.tools.declarative import build_http_tools
from lkap_agent.tools.execution import policy_of
from lkap_agent.tools.provider import EXECUTE_BASE, REAUTH_MESSAGE, build_provider_tool, spoken_safe

KEY = "ak_placeholder_resolved_key"


def _definition(**overrides: Any) -> ProviderToolDefinition:
    body: dict[str, Any] = {
        "name": "googlecalendar_find_free_slots",
        "description": "Find free slots.",
        "parameters": {"type": "object", "properties": {"day": {"type": "string"}}},
        "tool_slug": "GOOGLECALENDAR_FIND_FREE_SLOTS",
        "toolkit": "googlecalendar",
        "connection_id": "conn1",
        "subject": "ws:w1",
        "headers": {"x-api-key": KEY},
        "schema_version": "20260901_00",
        "risk": "read",
        "execution": ToolExecution(mode="blocking"),
    }
    body.update(overrides)
    return ProviderToolDefinition.model_validate(body)


class Composio:
    """A recording `MockTransport` that answers every execute call with one response."""

    def __init__(self, response: httpx.Response | None = None, *, delay_s: float = 0.0) -> None:
        self.requests: list[httpx.Request] = []
        self.response = response or httpx.Response(
            200, json={"data": {"slots": ["09:00"]}, "successful": True}
        )
        self.delay_s = delay_s

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return self.response

    def transport(self) -> httpx.AsyncBaseTransport:
        return httpx.MockTransport(self._handle)


def _ctx() -> RunContext[Any]:
    return cast(RunContext[Any], FakeRunContext(name="googlecalendar_find_free_slots"))


async def _call(definition: ProviderToolDefinition, composio: Composio, **arguments: Any) -> str:
    tool = build_provider_tool(definition, transport_factory=composio.transport)
    result: str = await tool(raw_arguments=arguments, context=_ctx())
    return result


# ------------------------------------------------------------------------------ request
async def test_posts_the_exact_execute_body_and_headers() -> None:
    composio = Composio()

    await _call(_definition(), composio, day="monday")

    (request,) = composio.requests
    assert request.method == "POST"
    assert str(request.url) == f"{EXECUTE_BASE}/GOOGLECALENDAR_FIND_FREE_SLOTS"
    assert str(request.url).startswith("https://backend.composio.dev/api/v3.1/tools/execute/")
    assert request.headers["x-api-key"] == KEY
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "user_id": "ws:w1",
        "arguments": {"day": "monday"},
        "version": "20260901_00",
    }


async def test_sends_latest_without_a_pinned_version_and_the_account_when_known() -> None:
    composio = Composio()

    await _call(_definition(schema_version=None, connected_account_id="ca_1"), composio)

    body = json.loads(composio.requests[0].content)
    assert body["version"] == "latest"
    assert body["connected_account_id"] == "ca_1"


async def test_an_unresolved_key_placeholder_never_leaves_the_worker() -> None:
    composio = Composio()

    with pytest.raises(ToolError, match="not configured"):
        await _call(_definition(headers={"x-api-key": "{{ secret.api_key }}"}), composio)

    assert composio.requests == []


async def test_a_redirect_is_not_followed() -> None:
    composio = Composio(httpx.Response(302, headers={"location": "https://elsewhere.example.com/"}))

    with pytest.raises(ToolError):
        await _call(_definition(), composio)

    assert len(composio.requests) == 1


# ------------------------------------------------------------------------------ result
async def test_result_path_data_is_what_the_model_sees() -> None:
    result = await _call(_definition(), Composio())

    assert json.loads(result) == {"slots": ["09:00"]}


async def test_a_json_pointer_result_path_and_the_character_cap_apply() -> None:
    composio = Composio(httpx.Response(200, json={"data": {"text": "x" * 500}, "successful": True}))

    result = await _call(_definition(result_path="/data/text", max_result_chars=120), composio)

    assert len(result) <= 120 + len("… [truncated]") + 5
    assert result.startswith("x" * 100)


# ------------------------------------------------------------------------------ errors
async def test_successful_false_is_one_spoken_safe_sentence_without_links() -> None:
    composio = Composio(
        httpx.Response(
            200,
            json={
                "data": {},
                "successful": False,
                "error": "The calendar is busy. See https://help.example.com/x for details.",
            },
        )
    )

    with pytest.raises(ToolError) as caught:
        await _call(_definition(), composio)

    assert str(caught.value) == "The calendar is busy."
    assert "http" not in str(caught.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(
            200,
            json={
                "successful": False,
                "error": {"message": "Connected account is expired, reconnect at https://x.example.com/a"},
            },
        ),
        httpx.Response(
            400, json={"error": {"message": "No active connection", "slug": "ConnectedAccount_NotFound"}}
        ),
        httpx.Response(401, json={"error": {"message": "nope"}}),
    ],
)
async def test_auth_shaped_failures_become_the_admin_reconnect_message(response: httpx.Response) -> None:
    with pytest.raises(ToolError) as caught:
        await _call(_definition(), Composio(response))

    assert str(caught.value) == REAUTH_MESSAGE
    assert "http" not in str(caught.value)


async def test_a_vendor_5xx_is_a_short_error_not_a_crash() -> None:
    with pytest.raises(ToolError, match="Try later"):
        await _call(_definition(), Composio(httpx.Response(503, json={"error": {"message": "Try later."}})))


def test_spoken_safe_strips_links_and_keeps_one_sentence() -> None:
    assert spoken_safe("Open www.example.com/link now. Then retry.") == "Open now."


async def test_the_observer_records_tool_needs_reauth_for_the_reconnect_error() -> None:
    api = FakeApi()
    observer = SessionObserver(session_id="sess-1", client=cast(Any, api))
    call = llm.FunctionCall(call_id="c1", name="googlecalendar_find_free_slots", arguments="{}")

    observer._on_tool_execution(ToolExecutionUpdatedEvent(update=ToolCallStarted(function_call=call)))
    observer._on_tool_execution(
        ToolExecutionUpdatedEvent(
            update=ToolCallEnded(id="c1", call_id="c1", message=REAUTH_MESSAGE, status="error")
        )
    )
    await observer.flush()

    (event,) = api.events_of("tool_needs_reauth")
    assert event.payload == {"call_id": "c1", "tool": "googlecalendar_find_free_slots"}


async def test_an_ordinary_tool_error_records_no_reauth_event() -> None:
    api = FakeApi()
    observer = SessionObserver(session_id="sess-1", client=cast(Any, api))

    observer._on_tool_execution(
        ToolExecutionUpdatedEvent(update=ToolCallEnded(id="c2", call_id="c2", message="busy", status="error"))
    )
    await observer.flush()

    assert api.events_of("tool_needs_reauth") == []


# ------------------------------------------------------------------------------ policy
def test_a_read_action_runs_auto_under_its_declared_execution() -> None:
    tool = build_provider_tool(
        _definition(execution=ToolExecution(mode="auto", announce="Let me look that up", max_duration_s=20))
    )

    policy = policy_of(tool)
    assert policy is not None and not isinstance(policy, dict)
    resolved = policy.resolved  # type: ignore[union-attr]
    assert resolved.mode == "auto"
    assert resolved.announce == "Let me look that up"
    assert resolved.max_duration_s == 20


def test_a_write_action_blocks_and_is_not_cancellable() -> None:
    tool = build_provider_tool(
        _definition(risk="write", execution=ToolExecution(mode="blocking", cancellable=False))
    )

    resolved = policy_of(tool).resolved  # type: ignore[union-attr]
    assert resolved.mode == "blocking"
    assert resolved.cancellable is False
    assert tool.info.flags == ToolFlag.NONE


async def test_run_with_policy_receives_the_definitions_execution() -> None:
    """A slow `auto` read announces with the definition's own text, then returns the result."""
    composio = Composio(delay_s=0.2)
    definition = _definition(
        execution=ToolExecution(mode="auto", announce="Let me look that up", auto_threshold_ms=10)
    )
    tool = build_provider_tool(definition, transport_factory=composio.transport)
    ctx = FakeRunContext(name=definition.name)

    result = await tool(raw_arguments={}, context=cast(RunContext[Any], ctx))

    assert ctx.updates == ["Let me look that up"]
    assert json.loads(result) == {"slots": ["09:00"]}


def test_the_declarative_builder_dispatches_provider_definitions() -> None:
    (tool,) = build_http_tools([_definition()])

    assert tool.info.name == "googlecalendar_find_free_slots"
    assert policy_of(tool) is not None


def test_a_rebound_tool_keeps_the_provider_builder() -> None:
    tool = build_provider_tool(_definition(execution=ToolExecution()))
    policy = policy_of(tool)
    assert policy is not None and policy.rebind is not None  # type: ignore[union-attr]

    rebound = policy.rebind("auto", False)  # type: ignore[union-attr]

    assert policy_of(rebound).resolved.mode == "auto", "a read without a mode follows the agent default"  # type: ignore[union-attr]
