"""Background and parallel tool calls (docs/v4/BACKGROUND-TOOLS.md §8, V4-12).

Three layers, all offline:

* `resolve_execution` / `tool_flags`: the mechanical safety rule (D-V4-32) and the
  flow gate (D-V4-37), as pure functions.
* `run_with_policy` against `fakes.fake_ctx.FakeRunContext`: the three modes, the
  bound, cancellation and fillers.
* The real SDK path: an `AgentSession` whose scripted LLM calls a wrapped HTTP tool
  served by `respx` with a delay, so the async-tool executor of livekit-agents 1.8.2
  (`voice/tool_executor.py`) does the scheduling exactly as in production.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable, Sequence
from typing import Any, cast

import httpx
import livekit.agents
import pytest
import respx
import structlog
from fakes.fake_api import FakeApi
from fakes.fake_ctx import FakePackSessionContext, FakeRunContext, default_agent_config
from livekit.agents import (
    Agent,
    AgentSession,
    APIConnectOptions,
    RunContext,
    ToolError,
    ToolExecutionUpdatedEvent,
    function_tool,
    llm,
)
from livekit.agents.llm import ToolFlag
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr
from livekit.agents.voice.events import ToolCallEnded, ToolCallStarted, ToolCallUpdated, ToolReplyUpdated
from lkap_contracts.tools import (
    BACKGROUNDABLE_BUILTINS,
    NEVER_BACKGROUND_TOOLS,
    HttpToolDefinition,
    ToolExecution,
)

from lkap_agent.observability import SessionObserver
from lkap_agent.session_builder import ASYNC_TOOL_OPTIONS
from lkap_agent.telephony import TELEPHONY_TOOL_NAMES
from lkap_agent.text_mode import inject_user_text, rewind
from lkap_agent.tools import execution
from lkap_agent.tools.builtin import build_builtin_tools
from lkap_agent.tools.declarative import build_http_tools
from lkap_agent.tools.execution import (
    ResolvedExecution,
    cancel_running,
    resolve_execution,
    run_with_policy,
    tool_flags,
)

API_URL = "https://api.example.com/items/42"


@pytest.fixture(autouse=True)
def _fresh_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test sees the once-per-process warnings afresh."""
    monkeypatch.setattr(execution, "_warned", set())


def _resolve(**overrides: Any) -> ResolvedExecution:
    arguments: dict[str, Any] = {
        "name": "lookup_item",
        "kind": "http",
        "is_read": True,
        "declared": None,
        "agent_default": "blocking",
        "flow_node": False,
    }
    arguments.update(overrides)
    return resolve_execution(**arguments)


# ------------------------------------------------------------ resolve_execution


def test_resolve_a_get_tool_follows_the_agent_default() -> None:
    resolved = _resolve(agent_default="auto")

    assert resolved.mode == "auto"
    assert resolved.on_duplicate == "reject"
    assert resolved.duplicate_scope == "name_and_args"
    assert resolved.cancellable is True
    assert tool_flags(resolved) == (ToolFlag.CANCELLABLE, "reject", "name_and_args")


def test_resolve_a_post_tool_ignores_the_agent_default() -> None:
    resolved = _resolve(is_read=False, agent_default="auto")

    assert resolved.mode == "blocking"
    assert tool_flags(resolved) == (ToolFlag.NONE, "allow", "name")


def test_resolve_a_post_tool_declared_background_confirms_and_is_not_cancellable() -> None:
    resolved = _resolve(is_read=False, declared=ToolExecution(mode="background"))

    assert resolved.mode == "background"
    assert resolved.on_duplicate == "confirm"
    assert resolved.cancellable is False
    assert tool_flags(resolved) == (ToolFlag.NONE, "confirm", "name_and_args")


def test_resolve_explicit_settings_win_over_the_read_defaults() -> None:
    declared = ToolExecution(
        mode="background", cancellable=False, on_duplicate="replace", duplicate_scope="name"
    )

    resolved = _resolve(declared=declared)

    assert (resolved.cancellable, resolved.on_duplicate, resolved.duplicate_scope) == (
        False,
        "replace",
        "name",
    )


def test_resolve_a_blocking_tool_keeps_todays_flags() -> None:
    assert tool_flags(_resolve()) == (ToolFlag.NONE, "allow", "name")


@pytest.mark.parametrize("name", sorted(NEVER_BACKGROUND_TOOLS | {"go_to_collect_details"}))
def test_resolve_a_never_list_tool_always_blocks_with_a_warning(name: str) -> None:
    with structlog.testing.capture_logs() as logs:
        resolved = _resolve(
            name=name, kind="builtin", declared=ToolExecution(mode="background", fillers=["x"])
        )

    assert resolved.mode == "blocking"
    assert resolved.fillers == ()
    assert resolved.downgraded_from == "background"
    assert tool_flags(resolved) == (ToolFlag.NONE, "allow", "name")
    assert [entry["tool"] for entry in logs if entry["log_level"] == "warning"] == [name]


def test_the_never_list_names_the_worker_s_telephony_tools() -> None:
    assert frozenset(TELEPHONY_TOOL_NAMES) <= NEVER_BACKGROUND_TOOLS
    assert not BACKGROUNDABLE_BUILTINS & NEVER_BACKGROUND_TOOLS


@pytest.mark.parametrize("kind", ["mcp", "pack"])
def test_resolve_the_agent_default_never_reaches_mcp_or_pack_tools(kind: str) -> None:
    assert _resolve(kind=kind, agent_default="background").mode == "blocking"


def test_resolve_a_flow_node_tool_blocks_below_1_8_3() -> None:
    with structlog.testing.capture_logs() as logs:
        resolved = _resolve(declared=ToolExecution(mode="background"), flow_node=True, sdk_version="1.8.2")

    assert resolved.mode == "blocking"
    assert resolved.downgraded_from == "background"
    assert any("1.8.3" in entry["event"] for entry in logs)


@pytest.mark.parametrize("version", ["1.8.3", "1.9.0", "2.0.0rc1"])
def test_resolve_a_flow_node_tool_runs_as_declared_from_1_8_3(version: str) -> None:
    resolved = _resolve(declared=ToolExecution(mode="background"), flow_node=True, sdk_version=version)

    assert resolved.mode == "background"
    assert resolved.downgraded_from is None


def test_resolve_the_installed_sdk_is_at_or_above_the_flow_gate() -> None:
    """V4-14 pinned 1.8.3, so the gate (it reads `livekit.agents.__version__`) is lifted (R-V4-54)."""
    assert execution.sdk_version_at_least(execution.FLOW_BACKGROUND_MIN_SDK)
    resolved = _resolve(declared=ToolExecution(mode="auto"), flow_node=True)

    assert resolved.mode == "auto"
    assert resolved.downgraded_from is None


def test_resolve_an_installed_1_8_2_still_downgrades_flow_nodes_with_one_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-V4-54: the downgrade stays tested through the installed-version path."""
    monkeypatch.setattr(livekit.agents, "__version__", "1.8.2")

    with structlog.testing.capture_logs() as logs:
        first = _resolve(declared=ToolExecution(mode="auto"), flow_node=True)
        second = _resolve(declared=ToolExecution(mode="auto"), flow_node=True)

    assert (first.mode, second.mode) == ("blocking", "blocking")
    assert first.downgraded_from == "auto"
    assert [entry["event"] for entry in logs if "1.8.3" in entry["event"]] == [
        "flow-node tool runs blocking until livekit-agents >= 1.8.3 (#7321)"
    ]


def test_resolve_without_the_sdk_executor_every_tool_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(execution, "SDK_ASYNC_TOOLS", False)

    with structlog.testing.capture_logs() as logs:
        first = _resolve(agent_default="auto")
        second = _resolve(name="other", agent_default="auto")

    assert (first.mode, second.mode) == ("blocking", "blocking")
    assert len([entry for entry in logs if entry["log_level"] == "warning"]) == 1


def test_resolve_the_announce_defaults_to_the_label() -> None:
    assert _resolve(agent_default="auto").announce == "Working on lookup item."
    assert _resolve(agent_default="auto", declared=ToolExecution(announce="  Fetching it.  ")).announce == (
        "Fetching it."
    )


def test_resolve_report_progress_is_for_non_blocking_mcp_tools_only() -> None:
    declared = ToolExecution(mode="background", report_progress=True)

    assert _resolve(kind="mcp", is_read=False, declared=declared).report_progress is True
    assert _resolve(kind="http", declared=declared).report_progress is False
    assert (
        _resolve(kind="mcp", is_read=False, declared=ToolExecution(report_progress=True)).report_progress
        is False
    )


# --------------------------------------------------------------- run_with_policy


def _policy(mode: str = "auto", **declared: Any) -> ResolvedExecution:
    return _resolve(declared=ToolExecution(mode=mode, announce="Looking that up.", **declared))


def _work(delay: float, result: str = "done", *, started: asyncio.Event | None = None) -> Callable[[], Any]:
    async def _run() -> str:
        if started is not None:
            started.set()
        await asyncio.sleep(delay)
        return result

    return _run


async def test_run_with_policy_auto_fast_work_returns_inline_without_an_update() -> None:
    context = FakeRunContext()

    result = await run_with_policy(context, _policy("auto"), _work(0.01))  # type: ignore[arg-type]

    assert result == "done"
    assert context.updates == []


async def test_run_with_policy_auto_slow_work_announces_exactly_once_then_returns() -> None:
    context = FakeRunContext()

    result = await run_with_policy(context, _policy("auto"), _work(2.0))  # type: ignore[arg-type]

    assert result == "done"
    assert context.updates == ["Looking that up."]


async def test_run_with_policy_background_announces_before_the_work_finishes() -> None:
    context = FakeRunContext()
    seen_at_finish: list[list[str]] = []

    async def _slow() -> str:
        await asyncio.sleep(0.05)
        seen_at_finish.append(list(context.updates))
        return "done"

    result = await run_with_policy(context, _policy("background"), _slow)  # type: ignore[arg-type]

    assert result == "done"
    assert seen_at_finish == [["Looking that up."]]


async def test_run_with_policy_blocking_runs_the_work_directly() -> None:
    context = FakeRunContext()

    result = await run_with_policy(context, _resolve(), _work(0.01))  # type: ignore[arg-type]

    assert result == "done"
    assert context.updates == []
    assert context.fillers == []


async def test_run_with_policy_max_duration_is_a_tool_error_naming_the_label() -> None:
    context = FakeRunContext()

    with pytest.raises(ToolError, match=r"Lookup item took longer than 0\.05 seconds"):
        await run_with_policy(context, _policy("background", max_duration_s=0.05), _work(5.0))  # type: ignore[arg-type]


async def test_run_with_policy_cancelling_the_call_cancels_the_inner_task() -> None:
    context = FakeRunContext()
    started = asyncio.Event()
    inner: list[asyncio.Task[Any]] = []

    async def _work_forever() -> str:
        inner.append(asyncio.current_task())  # type: ignore[arg-type]
        started.set()
        await asyncio.sleep(60)
        return "never"

    call = asyncio.create_task(run_with_policy(context, _policy("background"), _work_forever))  # type: ignore[arg-type]
    await started.wait()
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    await asyncio.sleep(0)

    assert inner and inner[0].cancelled()


async def test_cancel_running_cancels_the_work_of_a_session() -> None:
    context = FakeRunContext()
    started = asyncio.Event()

    call = asyncio.create_task(
        run_with_policy(context, _policy("background"), _work(60, started=started))  # type: ignore[arg-type]
    )
    await started.wait()

    assert await cancel_running(context.session) == ["call-1"]
    with pytest.raises(asyncio.CancelledError):
        await call
    assert await cancel_running(context.session) == []


async def test_cancel_running_waits_at_most_its_bound_for_work_that_ignores_cancellation() -> None:
    context = FakeRunContext()
    started = asyncio.Event()
    release = asyncio.Event()

    async def _stubborn() -> str:
        started.set()
        while True:
            try:
                await release.wait()
                return "late"
            except asyncio.CancelledError:
                continue

    call = asyncio.create_task(run_with_policy(context, _policy("background"), _stubborn))  # type: ignore[arg-type]
    await started.wait()

    with structlog.testing.capture_logs() as logs:
        cancelled = await asyncio.wait_for(cancel_running(context.session, timeout_s=0.05), 1)

    assert cancelled == ["call-1"]
    assert [entry["event"] for entry in logs] == ["cancelled tool calls did not end in time"]
    release.set()
    await call


class _EmittingSession:
    """A session double with `on`/`off`: `cancel_running` waits for the SDK's ended event."""

    def __init__(self) -> None:
        self.tts = None
        self.handlers: list[Callable[[Any], None]] = []

    def on(self, event: str, callback: Callable[[Any], None]) -> None:
        assert event == "tool_execution_updated"
        self.handlers.append(callback)

    def off(self, event: str, callback: Callable[[Any], None]) -> None:
        self.handlers.remove(callback)


async def test_cancel_running_returns_once_the_sdk_reports_the_call_ended() -> None:
    context = FakeRunContext()
    session = _EmittingSession()
    context.session = cast(Any, session)
    started = asyncio.Event()
    call = asyncio.create_task(
        run_with_policy(context, _policy("background"), _work(60, started=started))  # type: ignore[arg-type]
    )
    await started.wait()

    waiter = asyncio.create_task(cancel_running(session, timeout_s=5))
    await asyncio.sleep(0.05)
    assert not waiter.done(), "the inner task ending is not enough: the SDK must report the call ended"
    ended = ToolCallEnded(id="call-1", call_id="call-1", message=None, status="cancelled")
    for handler in list(session.handlers):
        handler(ToolExecutionUpdatedEvent(update=ended))

    assert await asyncio.wait_for(waiter, 1) == ["call-1"]
    assert session.handlers == [], "the listener is removed"
    with pytest.raises(asyncio.CancelledError):
        await call


async def test_run_with_policy_skips_fillers_without_a_voice() -> None:
    context = FakeRunContext(tts=None)

    result = await run_with_policy(context, _policy("background", fillers=["Still checking."]), _work(0.01))  # type: ignore[arg-type]

    assert result == "done"
    assert context.fillers == []


async def test_run_with_policy_enters_with_filler_once_when_there_is_a_voice() -> None:
    context = FakeRunContext(tts=object())
    policy = _policy(
        "blocking", fillers=["Still checking.", "Almost there."], filler_delay_s=3, filler_interval_s=6
    )

    result = await run_with_policy(context, policy, _work(0.01))  # type: ignore[arg-type]

    assert result == "done"
    assert context.updates == []
    (entry,) = context.fillers
    assert (entry["delay"], entry["interval"], entry["max_steps"]) == (3, 6, 2)
    source = entry["source"]
    assert [source(0), source(1), source(2)] == ["Still checking.", "Almost there.", None]


async def test_run_with_policy_a_single_filler_fires_at_most_once() -> None:
    context = FakeRunContext(tts=object())

    await run_with_policy(context, _policy("background", fillers=["Still checking."]), _work(0.01))  # type: ignore[arg-type]

    (entry,) = context.fillers
    assert entry["interval"] is None
    assert context.log[0] == ("update", "Looking that up.")


async def test_run_with_policy_errors_from_the_work_propagate_unchanged() -> None:
    async def _fail() -> str:
        raise ToolError("upstream said no")

    with pytest.raises(ToolError, match="upstream said no"):
        await run_with_policy(FakeRunContext(), _policy("auto"), _fail)  # type: ignore[arg-type]


# ------------------------------------------------------------------- tripwire


def test_tripwire_every_sdk_symbol_the_design_relies_on_exists() -> None:
    """D-V4-29: if one of these disappears, the wrapper degrades to blocking."""
    from livekit.agents.llm.mcp import MCPToolOptions, MCPToolset  # noqa: PLC0415
    from livekit.agents.voice.tool_executor import AsyncToolOptions  # noqa: PLC0415

    assert callable(RunContext.update)
    assert callable(RunContext.with_filler)
    assert ToolFlag.CANCELLABLE
    assert MCPToolset.__init__ is not None and MCPToolOptions is not None
    assert ToolExecutionUpdatedEvent is not None and AsyncToolOptions is not None
    assert "tool_handling" in inspect.signature(AgentSession.__init__).parameters
    assert execution.SDK_ASYNC_TOOLS is True


def test_execution_never_uses_the_pack_background_runner() -> None:
    """R-V4-34: no second scheduler."""

    assert "BackgroundToolRunner" not in inspect.getsource(execution)


# ----------------------------------------------------------- the real SDK path


class ToolCall:
    """A scripted step that calls one tool."""

    def __init__(self, name: str, arguments: dict[str, Any] | None = None) -> None:
        self.name = name
        self.arguments = arguments or {}


Step = str | ToolCall | list[ToolCall]


class _ScriptedStream(llm.LLMStream):
    def __init__(self, parent: ScriptedLLM, *, step: Step, **kwargs: Any) -> None:
        self._step = step
        super().__init__(parent, **kwargs)

    async def _run(self) -> None:
        step = self._step
        calls = [step] if isinstance(step, ToolCall) else step if isinstance(step, list) else []
        if calls:
            tool_calls = [
                llm.FunctionToolCall(
                    name=c.name, arguments=json.dumps(c.arguments), call_id=self._llm.next_call_id()
                )
                for c in calls
            ]
            delta = llm.ChoiceDelta(role="assistant", tool_calls=tool_calls)
        else:
            delta = llm.ChoiceDelta(role="assistant", content=str(step))
        self._event_ch.send_nowait(llm.ChatChunk(id="scripted", delta=delta))


class ScriptedLLM(llm.LLM[Any]):
    """Plays `steps` in order (text or tool calls) and records each call's context and tools."""

    def __init__(self, steps: Sequence[Step]) -> None:
        super().__init__()
        self.steps: list[Step] = list(steps)
        self.calls: list[tuple[llm.ChatContext, list[str], NotGivenOr[Any]]] = []
        self._ids = 0

    @property
    def model(self) -> str:
        return "scripted-llm"

    def next_call_id(self) -> str:
        self._ids += 1
        return f"call-{self._ids}"

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[llm.ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> _ScriptedStream:
        names = [t.id for t in tools or []]
        self.calls.append((chat_ctx.copy(), names, tool_choice))
        step = self.steps.pop(0) if self.steps else "Okay."
        return _ScriptedStream(
            self, step=step, chat_ctx=chat_ctx, tools=list(tools or []), conn_options=conn_options
        )


def _http_definition(**execution_fields: Any) -> HttpToolDefinition:
    return HttpToolDefinition(
        name="lookup_item",
        description="Look up an item.",
        parameters={"type": "object", "properties": {"item_id": {"type": "string"}}, "required": ["item_id"]},
        method="GET",
        url="https://api.example.com/items/{{ item_id }}",
        allowed_hosts=["api.example.com"],
        execution=ToolExecution(**execution_fields),
    )


class _Recorder:
    def __init__(self, session: AgentSession[Any]) -> None:
        self.updates: list[Any] = []
        session.on("tool_execution_updated", lambda ev: self.updates.append(ev.update))

    def kinds(self) -> list[str]:
        out: list[str] = []
        for update in self.updates:
            kind = update.type
            if kind == "tool_call_ended":
                kind = f"ended({update.status})"
            elif kind == "tool_reply_updated":
                kind = f"reply({update.status})"
            out.append(kind)
        return out

    async def wait_for(self, predicate: Callable[[Any], bool], limit_s: float = 5.0) -> Any:
        async def _poll() -> Any:
            while True:
                for update in self.updates:
                    if predicate(update):
                        return update
                await asyncio.sleep(0.01)

        return await asyncio.wait_for(_poll(), limit_s)


def _session(llm_: ScriptedLLM) -> AgentSession[Any]:
    return AgentSession(llm=llm_, tool_handling={"async_options": ASYNC_TOOL_OPTIONS["cascaded"]})


async def _slow_ok(request: httpx.Request) -> httpx.Response:
    await asyncio.sleep(1.5)
    return httpx.Response(200, text="item 42 is in stock")


@respx.mock
async def test_real_sdk_a_slow_auto_tool_announces_then_replies_at_idle() -> None:
    respx.get(API_URL).mock(side_effect=_slow_ok)
    tools = build_http_tools(
        [_http_definition()], platform_allowed_hosts=["api.example.com"], execution_default="auto"
    )
    scripted = ScriptedLLM(
        [ToolCall("lookup_item", {"item_id": "42"}), "Let me pull that up.", "Item 42 is in stock."]
    )
    session = _session(scripted)
    recorder = _Recorder(session)
    await session.start(Agent(instructions="Help.", tools=tools))

    await session.run(user_input="Is item 42 in stock?")
    await recorder.wait_for(lambda u: u.type == "tool_reply_updated" and u.status == "completed")

    kinds = recorder.kinds()
    assert sorted(kinds) == sorted(
        ["tool_call_started", "tool_call_updated", "ended(done)", "reply(scheduled)", "reply(completed)"]
    )
    # started → updated → ended(done) and → reply(scheduled) → reply(completed). The SDK
    # queues the deferred reply from inside the tool task, so `scheduled` may be emitted
    # just before the task's done-callback reports `ended`: both follow the update.
    assert kinds.index("tool_call_started") < kinds.index("tool_call_updated")
    assert kinds.index("tool_call_updated") < kinds.index("ended(done)")
    assert (
        kinds.index("tool_call_updated") < kinds.index("reply(scheduled)") < kinds.index("reply(completed)")
    )
    updated = recorder.updates[1]
    assert updated.message == "Working on lookup item."
    # The deferred reply is generated with tool_choice="none", after the final result landed.
    _ctx, _tools, choice = scripted.calls[-1]
    assert choice == "none"
    outputs = [i for i in session.history.items if i.type == "function_call_output"]
    assert outputs[-1].call_id == "call-1_final"
    assert outputs[-1].output == "item 42 is in stock"
    assistant = [
        i.text_content for i in session.history.items if i.type == "message" and i.role == "assistant"
    ]
    assert assistant == ["Let me pull that up.", "Item 42 is in stock."]
    await session.aclose()


def _items(ctx: llm.ChatContext) -> list[tuple[str, str | None, Any]]:
    return [(i.type, getattr(i, "call_id", None), getattr(i, "output", None)) for i in ctx.items]


@respx.mock
async def test_real_sdk_a_fast_auto_tool_returns_inline_with_one_generation() -> None:
    respx.get(API_URL).mock(return_value=httpx.Response(200, text="item 42 is in stock"))
    tools = build_http_tools(
        [_http_definition()], platform_allowed_hosts=["api.example.com"], execution_default="auto"
    )
    scripted = ScriptedLLM([ToolCall("lookup_item", {"item_id": "42"}), "Item 42 is in stock."])
    session = _session(scripted)
    recorder = _Recorder(session)
    await session.start(Agent(instructions="Help.", tools=tools))

    await session.run(user_input="Is item 42 in stock?")

    assert recorder.kinds() == ["tool_call_started", "ended(done)"]
    assert len(scripted.calls) == 2
    await session.aclose()


@respx.mock
async def test_real_sdk_the_model_sees_a_running_call_as_in_progress() -> None:
    """The SDK's placeholder pair: a running call missing from the context is shown as pending."""
    respx.get(API_URL).mock(side_effect=_slow_ok)
    tools = build_http_tools(
        [_http_definition()], platform_allowed_hosts=["api.example.com"], execution_default="auto"
    )
    scripted = ScriptedLLM(
        [
            ToolCall("lookup_item", {"item_id": "42"}),
            "Let me pull that up.",
            "Still on it.",
            "It is in stock.",
        ]
    )
    session = _session(scripted)
    recorder = _Recorder(session)
    agent = Agent(instructions="Help.", tools=tools)
    await session.start(agent)
    await session.run(user_input="Is item 42 in stock?")

    # A context without the call's items (a trimmed or rewound conversation), then a new turn.
    trimmed = llm.ChatContext([i for i in agent.chat_ctx.items if i.type == "message"])
    await agent.update_chat_ctx(trimmed)
    await session.run(user_input="Any news?")

    ctx, _tools, _choice = scripted.calls[2]
    assert ("function_call_output", "call-1", "The tool call is still in progress.") in _items(ctx)
    assert all(i[2] != "The tool call is still in progress." for i in _items(session.history))
    await recorder.wait_for(lambda u: u.type == "tool_reply_updated" and u.status == "completed")
    await session.aclose()


@respx.mock
async def test_real_sdk_a_duplicate_call_while_running_gets_the_reject_template() -> None:
    route = respx.get(API_URL).mock(side_effect=_slow_ok)
    tools = build_http_tools(
        [_http_definition()], platform_allowed_hosts=["api.example.com"], execution_default="auto"
    )
    scripted = ScriptedLLM(
        [
            ToolCall("lookup_item", {"item_id": "42"}),
            "Let me pull that up.",
            ToolCall("lookup_item", {"item_id": "42"}),
            "It is on its way.",
            "It is in stock.",
        ]
    )
    session = _session(scripted)
    recorder = _Recorder(session)
    await session.start(Agent(instructions="Help.", tools=tools))
    await session.run(user_input="Is item 42 in stock?")

    await session.run(user_input="Fetch it again.")

    outputs = {i.call_id: i.output for i in session.history.items if i.type == "function_call_output"}
    assert outputs["call-2"] == "That is already being looked up; tell the user it is on its way."
    await recorder.wait_for(lambda u: u.type == "tool_reply_updated" and u.status == "completed")
    assert [u.type for u in recorder.updates].count("tool_call_started") == 1
    assert route.call_count == 1
    await session.aclose()


@respx.mock
@pytest.mark.parametrize(("default", "expected"), [("auto", True), ("blocking", False)])
async def test_real_sdk_companion_tools_exist_only_with_a_cancellable_tool(
    default: str, expected: bool
) -> None:
    respx.get(API_URL).mock(return_value=httpx.Response(200, text="ok"))
    tools = build_http_tools(
        [_http_definition()], platform_allowed_hosts=["api.example.com"], execution_default=default
    )
    scripted = ScriptedLLM(["Hello."])
    session = _session(scripted)
    await session.start(Agent(instructions="Help.", tools=tools))

    await session.run(user_input="Hi")

    _ctx, names, _choice = scripted.calls[0]
    assert "lookup_item" in names
    assert ("lk_agents_cancel_task" in names) is expected
    assert ("lk_agents_get_running_tasks" in names) is expected
    await session.aclose()


@respx.mock
async def test_real_sdk_interrupting_the_turn_keeps_the_tool_running_and_the_result_lands() -> None:
    """The premise of live step 9, offline: an interruption does not cancel the work."""
    respx.get(API_URL).mock(side_effect=_slow_ok)
    tools = build_http_tools(
        [_http_definition()], platform_allowed_hosts=["api.example.com"], execution_default="auto"
    )
    scripted = ScriptedLLM(
        [ToolCall("lookup_item", {"item_id": "42"}), "It is noon.", "Item 42 is in stock."]
    )
    session = _session(scripted)
    recorder = _Recorder(session)
    await session.start(Agent(instructions="Help.", tools=tools))

    first = session.run(user_input="Is item 42 in stock?")
    await recorder.wait_for(lambda u: u.type == "tool_call_started")
    await asyncio.sleep(0.2)
    await session.interrupt()
    await first
    await session.run(user_input="What time is it?")
    await recorder.wait_for(lambda u: u.type == "tool_reply_updated" and u.status == "completed")

    assert "ended(done)" in recorder.kinds()
    assert "ended(cancelled)" not in recorder.kinds()
    final = [
        i for i in session.history.items if i.type == "function_call_output" and i.call_id == "call-1_final"
    ]
    assert final and final[0].output == "item 42 is in stock"
    await session.aclose()


@respx.mock
async def test_real_sdk_a_text_rewind_cancels_the_running_tool() -> None:
    respx.get(API_URL).mock(side_effect=_slow_ok)
    tools = build_http_tools(
        [_http_definition()], platform_allowed_hosts=["api.example.com"], execution_default="auto"
    )
    scripted = ScriptedLLM([ToolCall("lookup_item", {"item_id": "42"}), "Let me pull that up.", "Okay."])
    session = _session(scripted)
    recorder = _Recorder(session)
    await session.start(Agent(instructions="Help.", tools=tools))
    await session.run(user_input="Is item 42 in stock?")

    await rewind(session, 1)

    ended = await recorder.wait_for(lambda u: u.type == "tool_call_ended")
    assert ended.status == "cancelled"
    await session.aclose()


def _mentions(ctx: llm.ChatContext, call_id: str) -> list[tuple[str, str | None, Any]]:
    """Items of ``ctx`` belonging to ``call_id`` (its own id or a ``_update_N``/``_final`` entry)."""
    return [
        i for i in _items(ctx) if i[1] is not None and (i[1] == call_id or i[1].startswith(f"{call_id}_"))
    ]


@respx.mock
async def test_real_sdk_a_rewind_frees_the_model_to_call_the_cancelled_tool_again() -> None:
    """Ask #102: the regenerated reply sees nothing of the cancelled call, so it can call again.

    Before the fix, the regenerated inference read the SDK's placeholder pair
    ("The tool call is still in progress.") for the cancelled call, because the
    executor had not yet dropped it from its running set, and the model told the
    user the result was on its way.
    """
    route = respx.get(API_URL).mock(side_effect=_slow_ok)
    tools = build_http_tools(
        [_http_definition()], platform_allowed_hosts=["api.example.com"], execution_default="auto"
    )
    scripted = ScriptedLLM(
        [
            ToolCall("lookup_item", {"item_id": "42"}),
            "Let me pull that up.",
            ToolCall("lookup_item", {"item_id": "42"}),
            "Let me pull that up again.",
            "Item 42 is in stock.",
        ]
    )
    session = _session(scripted)
    recorder = _Recorder(session)
    await session.start(Agent(instructions="Help.", tools=tools))
    await session.run(user_input="Is item 42 in stock?")

    await rewind(session, 1)
    await recorder.wait_for(lambda u: u.type == "tool_call_started" and u.function_call.call_id == "call-2")

    regenerated, names, choice = scripted.calls[2]
    assert _mentions(regenerated, "call-1") == []
    assert all(i[2] != "The tool call is still in progress." for i in _items(regenerated))
    assert "lookup_item" in names
    assert choice is NOT_GIVEN
    cancelled = [u for u in recorder.updates if u.type == "tool_call_ended" and u.call_id == "call-1"]
    assert [u.status for u in cancelled] == ["cancelled"]
    # The re-call runs: not rejected as a duplicate of the cancelled one.
    outputs = {i.call_id: i.output for i in session.history.items if i.type == "function_call_output"}
    assert outputs.get("call-2") != "That is already being looked up; tell the user it is on its way."
    second = await recorder.wait_for(lambda u: u.type == "tool_call_ended" and u.call_id == "call-2")
    assert second.status == "done"
    # respx records a call once its response is produced: the cancelled request never was.
    assert route.call_count == 1
    await recorder.wait_for(lambda u: u.type == "tool_reply_updated" and u.status == "completed")
    assert _mentions(session.history, "call-1") == []
    await session.aclose()


@respx.mock
async def test_real_sdk_a_rewind_strips_an_earlier_turn_s_cancelled_call() -> None:
    """A call from a kept turn is cancelled too, so its announcement must not stay behind the cut."""
    respx.get(API_URL).mock(side_effect=_slow_ok)
    tools = build_http_tools(
        [_http_definition()], platform_allowed_hosts=["api.example.com"], execution_default="auto"
    )
    scripted = ScriptedLLM(
        [ToolCall("lookup_item", {"item_id": "42"}), "Let me pull that up.", "It is noon.", "Okay."]
    )
    session = _session(scripted)
    recorder = _Recorder(session)
    agent = Agent(instructions="Help.", tools=tools)
    await session.start(agent)
    await session.run(user_input="Is item 42 in stock?")
    await session.run(user_input="What time is it?")
    assert _mentions(agent.chat_ctx, "call-1"), "the announcement pair sits in turn 1"

    await rewind(session, 2)
    await recorder.wait_for(lambda u: u.type == "tool_call_ended" and u.call_id == "call-1")
    await asyncio.wait_for(_until(lambda: len(scripted.calls) == 4), 5)

    regenerated, _names, _choice = scripted.calls[3]
    assert _mentions(regenerated, "call-1") == []
    assert _mentions(session.history, "call-1") == []
    assert _mentions(agent.chat_ctx, "call-1") == []
    await session.aclose()


@respx.mock
async def test_real_sdk_an_edit_during_a_running_call_leaves_no_trace_of_it() -> None:
    """The edit path (`inject_user_text` with a `turn_index`) has the same guarantee."""
    respx.get(API_URL).mock(side_effect=_slow_ok)
    tools = build_http_tools(
        [_http_definition()], platform_allowed_hosts=["api.example.com"], execution_default="auto"
    )
    scripted = ScriptedLLM(
        [ToolCall("lookup_item", {"item_id": "42"}), "Let me pull that up.", "You're welcome."]
    )
    session = _session(scripted)
    recorder = _Recorder(session)
    await session.start(Agent(instructions="Help.", tools=tools))
    await session.run(user_input="Is item 42 in stock?")

    await inject_user_text(session, "Thanks, that's all.", turn_index=0)
    await asyncio.wait_for(_until(lambda: len(scripted.calls) == 3), 5)

    regenerated, _names, _choice = scripted.calls[2]
    assert _mentions(regenerated, "call-1") == []
    assert [u.status for u in recorder.updates if u.type == "tool_call_ended"] == ["cancelled"]
    await session.aclose()


async def _until(predicate: Callable[[], bool]) -> None:
    while True:
        if predicate():
            return
        await asyncio.sleep(0.01)


def test_no_lkap_code_generates_a_reply_for_a_tool_result() -> None:
    """R-V4-37: results are voiced by the SDK's deferred reply only."""
    assert "generate_reply(" not in inspect.getsource(execution)
    from lkap_agent.tools import declarative  # noqa: PLC0415

    assert "generate_reply(" not in inspect.getsource(declarative)


# --------------------------------------------------------------- pack wrapping


async def test_wrap_tool_runs_a_pack_tool_through_the_policy_and_keeps_its_schema() -> None:
    @function_tool
    async def start_workflow(topic: str) -> str:
        """Start the claim workflow.

        Args:
            topic: What the workflow is about.
        """
        await asyncio.sleep(0.01)
        return f"started {topic}"

    resolved = _resolve(
        name="start_workflow", kind="pack", is_read=False, declared=ToolExecution(mode="background")
    )
    wrapped = execution.wrap_tool(start_workflow, resolved)

    assert wrapped.info.name == "start_workflow"
    assert wrapped.info.description == start_workflow.info.description
    assert wrapped.info.on_duplicate == "confirm"
    assert execution.policy_of(wrapped) is not None
    scripted = ScriptedLLM(
        [
            ToolCall("start_workflow", {"topic": "hail", "lk_agents_confirm_duplicate": None}),
            "On it.",
            "Done.",
        ]
    )
    session = _session(scripted)
    recorder = _Recorder(session)
    await session.start(Agent(instructions="Help.", tools=[wrapped]))
    await session.run(user_input="Start it.")
    await recorder.wait_for(lambda u: u.type == "tool_call_ended")

    _ctx, _names, _choice = scripted.calls[0]
    schema = llm.utils.build_legacy_openai_schema(wrapped, internally_tagged=True)
    assert "lkap_run_context" not in json.dumps(schema)
    assert [u.type for u in recorder.updates][:2] == ["tool_call_started", "tool_call_updated"]
    outputs = {i.call_id: i.output for i in session.history.items if i.type == "function_call_output"}
    assert outputs.get("call-1_final") == "started hail"
    await session.aclose()


# ------------------------------------------------------------------- built-ins


def _builtin_ctx(**tools_fields: Any) -> FakePackSessionContext:
    config = default_agent_config()
    config = config.model_copy(update={"tools": config.tools.model_copy(update=tools_fields)})
    return FakePackSessionContext(config=config)


def _flags(tools: list[Any]) -> dict[str, tuple[Any, str]]:
    return {t.info.name: (t.info.flags, t.info.on_duplicate) for t in tools}


def test_builtins_follow_the_agent_default_only_for_read_tools() -> None:
    tools = build_builtin_tools(_builtin_ctx(execution_default="auto"), [], True)

    flags = _flags(tools)
    assert flags["search_knowledge"] == (ToolFlag.CANCELLABLE, "reject")
    assert flags["http_request"] == (ToolFlag.CANCELLABLE, "reject")
    for name in ("end_call", "push_note", "set_status", "escalate_to_human", "current_time"):
        assert flags[name] == (ToolFlag.NONE, "allow"), name


def test_builtins_take_their_own_execution_settings() -> None:
    ctx = _builtin_ctx(
        execution_default="auto",
        builtin_execution={
            "search_knowledge": ToolExecution(mode="blocking"),
            "end_call": ToolExecution(mode="auto"),
        },
    )

    flags = _flags(build_builtin_tools(ctx, [], False))

    assert flags["search_knowledge"] == (ToolFlag.NONE, "allow")
    assert flags["end_call"] == (ToolFlag.NONE, "allow")


def test_builtins_are_unchanged_by_default() -> None:
    flags = _flags(build_builtin_tools(_builtin_ctx(), [], True))

    assert all(value == (ToolFlag.NONE, "allow") for value in flags.values())


async def test_search_knowledge_under_auto_returns_a_warm_result_inline() -> None:
    ctx = _builtin_ctx(execution_default="auto")
    (tool,) = [t for t in build_builtin_tools(ctx, [], False) if t.info.name == "search_knowledge"]
    context = FakeRunContext()

    result = await tool(context=context, query="flood")

    assert result == "No relevant knowledge found."
    assert context.updates == []


async def test_http_request_runs_a_write_blocking_even_under_a_background_policy() -> None:
    ctx = _builtin_ctx(builtin_execution={"http_request": ToolExecution(mode="background")})
    (tool,) = [t for t in build_builtin_tools(ctx, [], True) if t.info.name == "http_request"]
    context = FakeRunContext()

    with pytest.raises(ToolError):
        await tool(context=context, method="POST", url="https://api.example.com/x", body=None)

    assert context.updates == []


# ---------------------------------------------------------------- observability


async def test_the_observer_records_tool_call_updated_and_tool_reply() -> None:
    api = FakeApi()
    observer = SessionObserver(session_id="sess-1", client=cast(Any, api))
    call = llm.FunctionCall(call_id="call-7", name="lookup_policy", arguments="{}")

    for update in (
        ToolCallStarted(function_call=call),
        ToolCallUpdated(id="call-7", call_id="call-7", message="Looking up policy H0-44721."),
        ToolCallEnded(id="call-7_final", call_id="call-7", message="active", status="done"),
        ToolReplyUpdated(
            update_ids=["call-7_final", "call-8_update_1"], status="scheduled", speech_id="sp-1"
        ),
        ToolReplyUpdated(update_ids=["call-7_final"], status="skipped", speech_id="sp-1"),
    ):
        observer._on_tool_execution(ToolExecutionUpdatedEvent(update=update))
    await observer.flush()

    (updated,) = api.events_of("tool_call_updated")
    assert updated.payload == {
        "call_id": "call-7",
        "tool": "lookup_policy",
        "message_preview": "Looking up policy H0-44721.",
    }
    replies = api.events_of("tool_reply")
    assert [r.payload for r in replies] == [
        {"call_ids": ["call-7", "call-8"], "status": "scheduled", "speech_id": "sp-1"},
        {"call_ids": ["call-7"], "status": "skipped", "speech_id": "sp-1"},
    ]


# ---------------------------------------------------------------- activity feed


def test_the_feed_shows_a_tracked_tool_from_its_start_and_upserts_by_call_id() -> None:
    rows: list[Any] = []
    policy = _policy("background")
    feed = execution.ToolActivityFeed(emit=rows.append, policies=lambda: {"lookup_item": policy})
    call = llm.FunctionCall(call_id="c1", name="lookup_item", arguments="{}")

    feed.handle(ToolExecutionUpdatedEvent(update=ToolCallStarted(function_call=call)))
    feed.handle(
        ToolExecutionUpdatedEvent(update=ToolCallUpdated(id="c1_update_1", call_id="c1", message="Halfway."))
    )
    feed.handle(ToolExecutionUpdatedEvent(update=ToolCallEnded(id="c1_final", call_id="c1", status="done")))

    assert [(r.id, r.phase, r.headline) for r in rows] == [
        ("tool:c1", "running", "Lookup item started"),
        ("tool:c1", "running", "Halfway."),
        ("tool:c1", "done", "Lookup item finished"),
    ]
    assert rows[1].detail == {"message": "Halfway."}
