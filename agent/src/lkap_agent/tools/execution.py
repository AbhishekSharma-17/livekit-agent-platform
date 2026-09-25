"""Background and parallel tool calls: LKAP's policy on the SDK's async-tool executor.

docs/v4/BACKGROUND-TOOLS.md (D-V4-29 … D-V4-38, rulings R-V4-34 … R-V4-39). The
scheduler is livekit-agents' own, verified in the installed 1.8.3:

* ``voice/tool_executor.py`` ``_ToolExecutor.execute`` runs every tool as its own
  task and returns to the LLM at the tool's first ``RunContext.update()`` or at its
  return; later updates and the final result are inserted into the chat context and
  voiced by one deferred ``generate_reply`` after ``session.wait_for_idle()``
  (``_deliver_reply``), so a background result never interrupts.
* ``voice/events.py`` ``RunContext.update`` / ``RunContext.with_filler``;
  ``voice/filler_scheduler.py`` speaks a filler through ``session.say()`` with no
  guard, so fillers are only ever scheduled when the session has a TTS.
* ``llm/tool_context.py`` ``ToolFlag.CANCELLABLE`` (the activity then exposes
  ``lk_agents_get_running_tasks`` / ``lk_agents_cancel_task``), ``on_duplicate`` ×
  ``duplicate_scope``.
* ``voice/generation.py`` injects "The tool call is still in progress." for every
  running call before each inference, so the model never re-issues it.

This module adds only policy: which mode a tool runs in (:func:`resolve_execution`,
the mechanical safety rule of D-V4-32), the flags it is declared with
(:func:`tool_flags`), and one wrapper every built-in, declarative HTTP and opted-in
pack tool goes through (:func:`run_with_policy`). It never schedules a reply itself
(R-V4-37) and uses no SDK private: :func:`cancel_running` keeps its own per-session
registry of the inner work tasks it created, and waits for the SDK's public
``tool_call_ended`` event to know a cancelled call has left the executor.

If a future SDK drops one of the symbols used here, :data:`SDK_ASYNC_TOOLS` is
false and every tool resolves to ``blocking`` with one warning per process (the
``_claim_user_turn`` pattern, D-W2-9p).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal, get_origin, get_type_hints

import livekit.agents
from livekit.agents import AgentSession, RunContext, ToolError, function_tool
from livekit.agents import llm as lk_llm
from livekit.agents.llm import ToolFlag
from lkap_contracts.tools import (
    DuplicatePolicy,
    DuplicateScope,
    ToolExecution,
    ToolExecutionMode,
    never_background,
)
from lkap_contracts.ui_protocol import ActivityEvent

from lkap_agent.logging import get_logger

__all__ = [
    "CANCEL_WAIT_S",
    "FLOW_BACKGROUND_MIN_SDK",
    "SDK_ASYNC_TOOLS",
    "SLOW_BLOCKING_S",
    "ResolvedExecution",
    "ToolActivityFeed",
    "ToolKind",
    "ToolPolicy",
    "attach_policy",
    "bind_agent_policy",
    "blocking_policy",
    "cancel_running",
    "flow_mode_of",
    "policies_for",
    "policy_of",
    "register_policies",
    "resolve_execution",
    "run_with_policy",
    "sdk_version_at_least",
    "tool_flags",
    "tool_label",
    "wrap_tool",
]

logger = get_logger(__name__)

ToolKind = Literal["builtin", "http", "mcp", "pack"]


#: Flow-node tools stay blocking below this SDK (livekit/agents #7321: in 1.8.2 a
#: handoff is dropped when a sibling tool in the same batch finishes after the edge
#: tool). V4-14 bumps the pin, which lifts the downgrade (R-V4-39).
FLOW_BACKGROUND_MIN_SDK: Final[str] = "1.8.3"

#: Name of the attribute a built tool carries its :class:`ToolPolicy` in.
_POLICY_ATTR: Final[str] = "_lkap_execution_policy"

#: Keyword-only parameter added to a wrapped pack tool that took no `RunContext`.
_CONTEXT_PARAM: Final[str] = "lkap_run_context"


def _sdk_has_async_tools() -> bool:
    """Tripwire: every SDK symbol this module relies on exists (D-V4-29)."""
    try:
        signature = inspect.signature(AgentSession.__init__)
    except (TypeError, ValueError):
        return False
    return (
        callable(getattr(RunContext, "update", None))
        and callable(getattr(RunContext, "with_filler", None))
        and hasattr(ToolFlag, "CANCELLABLE")
        and hasattr(livekit.agents, "ToolExecutionUpdatedEvent")
        and "tool_handling" in signature.parameters
    )


#: Whether the installed livekit-agents has the async-tool executor LKAP builds on.
SDK_ASYNC_TOOLS: bool = _sdk_has_async_tools()

_warned: set[str] = set()


def _warn_once(key: str, event: str, **fields: Any) -> None:
    """Log ``event`` at warning level once per process for ``key``."""
    if key in _warned:
        return
    _warned.add(key)
    logger.warning(event, **fields)


def _version_tuple(version: str) -> tuple[int, ...]:
    """``"1.8.2"`` → ``(1, 8, 2)``; a pre-release or local suffix ends the number."""
    parts: list[int] = []
    for piece in version.split("."):
        digits = ""
        for char in piece:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def sdk_version_at_least(minimum: str, version: str | None = None) -> bool:
    """Whether ``version`` (default: the installed ``livekit.agents.__version__``) is ≥ ``minimum``.

    Compared with :class:`packaging.version.Version` (present in the worker image
    through the locked dependency tree), falling back to a numeric tuple compare.
    """
    current = version if version is not None else livekit.agents.__version__
    try:
        from packaging.version import Version  # noqa: PLC0415 - optional at runtime

        return Version(current) >= Version(minimum)
    except ImportError:
        return _version_tuple(current) >= _version_tuple(minimum)


def tool_label(name: str) -> str:
    """A readable label from a tool name: ``lookup_policy`` → ``Lookup policy``."""
    return name.replace("_", " ").strip().capitalize() or name


@dataclass(frozen=True, slots=True)
class ResolvedExecution:
    """One tool's execution policy after the safety rule (what the worker actually does)."""

    mode: ToolExecutionMode
    announce: str
    auto_threshold_ms: int
    fillers: tuple[str, ...]
    filler_delay_s: float
    filler_interval_s: float
    cancellable: bool
    on_duplicate: DuplicatePolicy
    duplicate_scope: DuplicateScope
    max_duration_s: float
    label: str
    report_progress: bool = False
    #: The declared (or defaulted) mode the safety rule overrode, when it did.
    downgraded_from: ToolExecutionMode | None = None

    @property
    def non_blocking(self) -> bool:
        """``background`` or ``auto``: the conversation continues while the tool runs."""
        return self.mode != "blocking"

    @property
    def tracked(self) -> bool:
        """Whether the activity feed shows this tool from its start (D-V4-38)."""
        return self.non_blocking or bool(self.fillers)


def resolve_execution(
    *,
    name: str,
    kind: ToolKind,
    is_read: bool,
    declared: ToolExecution | None,
    agent_default: ToolExecutionMode,
    flow_node: bool,
    sdk_version: str | None = None,
    label: str | None = None,
) -> ResolvedExecution:
    """Apply the mechanical safety rule (D-V4-32) and the flow gate (D-V4-37) to one tool.

    Args:
        name: The model-facing tool name.
        kind: Where the tool comes from; the agent default reaches only ``builtin``
            and ``http`` read tools, never ``mcp`` or ``pack`` tools.
        is_read: A GET HTTP tool, or a built-in in ``BACKGROUNDABLE_BUILTINS``.
        declared: The tool's own :class:`ToolExecution`, if any.
        agent_default: ``AgentConfig.tools.execution_default``.
        flow_node: The tool runs on a flow node (activity-scoped per node).
        sdk_version: The livekit-agents version; defaults to the installed one.
        label: The activity label; defaults to :func:`tool_label` of ``name``.

    Returns:
        The resolved policy. The never-list and ``go_to_*`` edge tools always block
        (a declared mode is ignored with a warning); below livekit-agents 1.8.3 a
        flow-node tool always blocks (one warning per tool name and process).
    """
    spec = declared or ToolExecution()
    shown = label or tool_label(name)
    default_applies = is_read and kind in ("builtin", "http")
    mode: ToolExecutionMode = spec.mode or (agent_default if default_applies else "blocking")
    downgraded_from: ToolExecutionMode | None = None

    if never_background(name):
        if spec.mode not in (None, "blocking"):
            _warn_once(
                f"never:{name}",
                "tool execution mode ignored: this tool always blocks",
                tool=name,
                declared=spec.mode,
            )
        return ResolvedExecution(
            mode="blocking",
            announce="",
            auto_threshold_ms=spec.auto_threshold_ms,
            fillers=(),
            filler_delay_s=spec.filler_delay_s,
            filler_interval_s=spec.filler_interval_s,
            cancellable=False,
            on_duplicate="allow",
            duplicate_scope="name",
            max_duration_s=spec.max_duration_s,
            label=shown,
            downgraded_from=mode if mode != "blocking" else None,
        )

    if mode != "blocking" and not SDK_ASYNC_TOOLS:
        _warn_once(
            "sdk",
            "livekit-agents lacks the async-tool executor; every tool runs blocking",
            version=livekit.agents.__version__,
        )
        downgraded_from, mode = mode, "blocking"
    elif mode != "blocking" and flow_node and not sdk_version_at_least(FLOW_BACKGROUND_MIN_SDK, sdk_version):
        _warn_once(
            f"flow:{name}",
            "flow-node tool runs blocking until livekit-agents >= 1.8.3 (#7321)",
            tool=name,
            declared=mode,
        )
        downgraded_from, mode = mode, "blocking"

    non_blocking = mode != "blocking"
    if non_blocking:
        cancellable = spec.cancellable if spec.cancellable is not None else is_read
        on_duplicate: DuplicatePolicy = spec.on_duplicate or ("reject" if is_read else "confirm")
    else:
        cancellable = bool(spec.cancellable)
        on_duplicate = spec.on_duplicate or "allow"
    return ResolvedExecution(
        mode=mode,
        announce=(spec.announce or "").strip() or f"Working on {shown[:1].lower()}{shown[1:]}.",
        auto_threshold_ms=spec.auto_threshold_ms,
        fillers=tuple(f for f in (s.strip() for s in spec.fillers) if f),
        filler_delay_s=spec.filler_delay_s,
        filler_interval_s=spec.filler_interval_s,
        cancellable=cancellable,
        on_duplicate=on_duplicate,
        duplicate_scope=spec.duplicate_scope if on_duplicate != "allow" else "name",
        max_duration_s=spec.max_duration_s,
        label=shown,
        report_progress=spec.report_progress and non_blocking and kind == "mcp",
        downgraded_from=downgraded_from,
    )


def blocking_policy(name: str) -> ResolvedExecution:
    """The plain blocking policy for ``name`` (a tool exactly as it ran before V4-12)."""
    return resolve_execution(
        name=name, kind="builtin", is_read=False, declared=None, agent_default="blocking", flow_node=False
    )


def tool_flags(resolved: ResolvedExecution) -> tuple[ToolFlag, DuplicatePolicy, DuplicateScope]:
    """The ``@function_tool(flags=, on_duplicate=, duplicate_scope=)`` arguments for ``resolved``."""
    flags = ToolFlag.CANCELLABLE if resolved.cancellable else ToolFlag.NONE
    return flags, resolved.on_duplicate, resolved.duplicate_scope


# ------------------------------------------------------------------ running work

#: Per session: the inner work tasks :func:`run_with_policy` owns, by call id.
_RUNNING: weakref.WeakKeyDictionary[Any, dict[str, asyncio.Task[Any]]] = weakref.WeakKeyDictionary()
#: Sessions already told once that their fillers are dropped (no TTS).
_FILLERS_SKIPPED: weakref.WeakSet[Any] = weakref.WeakSet()


def _register(session: Any, call_id: str, task: asyncio.Task[Any]) -> None:
    try:
        _RUNNING.setdefault(session, {})[call_id] = task
    except TypeError:  # a session double that cannot be weak-referenced
        return


def _unregister(session: Any, call_id: str) -> None:
    try:
        tasks = _RUNNING.get(session)
    except TypeError:
        return
    if tasks is not None:
        tasks.pop(call_id, None)


#: How long :func:`cancel_running` waits for the SDK to end the calls it cancelled.
CANCEL_WAIT_S: Final[float] = 1.0


async def cancel_running(session: Any, *, timeout_s: float = CANCEL_WAIT_S) -> list[str]:
    """Cancel every inner work task :func:`run_with_policy` started for ``session``, and wait.

    Used by the text channel's rewind (the conversation the result belongs to is
    gone) and by tests. The SDK then ends each call as ``cancelled``.

    Cancelling the inner task is not enough on its own (ask #102): the SDK drops
    the call from its running set only later, in the done-callback of its own
    tool task (livekit-agents 1.8.2 ``voice/tool_executor.py:431-434``,
    ``_on_done``). Until then, every inference injects the "The tool call is still
    in progress." placeholder for it (``agent_activity.py:3496-3503`` →
    ``generation.py:73-105``) and the duplicate guard still sees it running, so a
    reply generated right after the cancel tells the user the result is on its
    way. ``_on_done`` pops the running set and *then* emits the public
    ``tool_execution_updated`` event with ``ToolCallEnded`` (``:461-468``, same
    order in 1.8.3), so waiting for that event per cancelled call means the SDK no
    longer counts it as running. The wait is bounded by ``timeout_s``; a session
    without ``on``/``off`` (a test double) waits for the inner tasks instead.

    Args:
        session: The ``AgentSession`` the work belongs to.
        timeout_s: Upper bound on the wait; on expiry a warning is logged and the
            call ids are returned anyway.

    Returns:
        The call ids whose work was cancelled (empty when nothing was running).
    """
    try:
        tasks = _RUNNING.get(session)
    except TypeError:
        return []
    if not tasks:
        return []
    running = {call_id: task for call_id, task in tasks.items() if not task.done()}
    tasks.clear()
    if not running:
        return []

    pending = set(running)
    all_ended = asyncio.get_running_loop().create_future()

    def _on_update(ev: Any) -> None:
        update = getattr(ev, "update", None)
        if getattr(update, "type", None) != "tool_call_ended":
            return
        pending.discard(getattr(update, "call_id", ""))
        if not pending and not all_ended.done():
            all_ended.set_result(None)

    on: Callable[..., Any] | None = getattr(session, "on", None)
    off: Callable[..., Any] | None = getattr(session, "off", None)
    subscribed = callable(on) and callable(off)
    if on is not None and subscribed:
        on("tool_execution_updated", _on_update)  # before cancelling: no ended event can be missed
    try:
        for task in running.values():
            task.cancel()
        # `asyncio.wait`, not `wait_for`: on expiry it neither cancels nor waits on
        # what it watched, so work that ignores cancellation cannot stall a rewind.
        watched: set[asyncio.Future[Any]] = {all_ended} if subscribed else set(running.values())
        _done, not_done = await asyncio.wait(watched, timeout=timeout_s)
        if not_done:
            logger.warning(
                "cancelled tool calls did not end in time",
                call_ids=sorted(pending if subscribed else (i for i, t in running.items() if not t.done())),
                timeout_s=timeout_s,
            )
    finally:
        if off is not None and subscribed:
            off("tool_execution_updated", _on_update)
    return list(running)


def _has_voice(session: Any) -> bool:
    """Whether ``session.say()`` can speak (a TTS); realtime plugins in 1.8.3 set no ``supports_say``."""
    try:
        return getattr(session, "tts", None) is not None
    except Exception:  # noqa: BLE001 - a session without the property simply has no voice
        return False


@contextlib.asynccontextmanager
async def _fillers(context: RunContext[Any], resolved: ResolvedExecution) -> AsyncIterator[None]:
    """``ctx.with_filler`` rotating ``resolved.fillers`` in order, or nothing (R-V4-38)."""
    fillers = resolved.fillers
    if not fillers:
        yield
        return
    session = context.session
    if not _has_voice(session):
        if session not in _FILLERS_SKIPPED:
            with contextlib.suppress(TypeError):
                _FILLERS_SKIPPED.add(session)
            logger.info("tool fillers skipped: this session has no voice (TTS)", tool=resolved.label)
        yield
        return

    def _source(step: int) -> str | None:
        return fillers[step] if step < len(fillers) else None

    async with context.with_filler(
        _source,
        delay=resolved.filler_delay_s,
        interval=resolved.filler_interval_s if len(fillers) > 1 else None,
        max_steps=len(fillers),
    ):
        yield


async def run_with_policy(
    context: RunContext[Any],
    resolved: ResolvedExecution,
    work: Callable[[], Awaitable[Any]],
) -> Any:
    """Run a tool body under its execution policy (BACKGROUND-TOOLS.md §4.1).

    * ``blocking`` without fillers: ``work()`` is awaited as it always was.
    * ``auto``: the work starts; if it finishes within ``auto_threshold_ms`` its
      result is returned inline (one generation), otherwise the tool announces.
    * ``background`` / slow ``auto``: ``context.update(announce)`` releases the LLM
      with the announcement as the tool's first output; the SDK voices the final
      result when the session is next idle.
    * Fillers (any mode) are spoken on the idle timer, only when the session has a TTS.

    Every non-fast-path run is bounded by ``max_duration_s`` (a ``ToolError`` naming
    the label); cancelling the call cancels the inner work task.

    Args:
        context: The call's ``RunContext``.
        resolved: The tool's policy.
        work: The tool's existing body, unchanged.

    Returns:
        Whatever ``work()`` returns.

    Raises:
        ToolError: The work ran longer than ``max_duration_s``, or raised one itself.
    """
    if resolved.mode == "blocking" and not resolved.fillers:
        return await work()

    loop = asyncio.get_running_loop()
    deadline = loop.time() + resolved.max_duration_s

    async def _work() -> Any:
        return await work()

    call_id = context.function_call.call_id
    task: asyncio.Task[Any] = asyncio.create_task(_work(), name=f"lkap_tool_{resolved.label}")
    session = context.session
    _register(session, call_id, task)
    try:
        if resolved.mode == "auto":
            done, _ = await asyncio.wait({task}, timeout=resolved.auto_threshold_ms / 1000)
            if done:
                return task.result()
        if resolved.mode != "blocking":
            await context.update(resolved.announce)
        async with _fillers(context, resolved):
            return await asyncio.wait_for(task, timeout=max(0.0, deadline - loop.time()))
    except TimeoutError as exc:
        raise ToolError(f"{resolved.label} took longer than {resolved.max_duration_s:g} seconds") from exc
    except asyncio.CancelledError:
        task.cancel()
        raise
    finally:
        _unregister(session, call_id)


# ------------------------------------------------------------- policy on tools


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    """A built tool's resolved policy, plus how to rebuild it for an agent's own default.

    ``rebind`` exists for tools built before the agent's ``tools.execution_default``
    and flow-ness are known (declarative HTTP tools: the worker's builder is called
    with the definitions only); :func:`bind_agent_policy` calls it.
    """

    resolved: ResolvedExecution
    rebind: Callable[[ToolExecutionMode, bool], Any] | None = None
    built_with: tuple[ToolExecutionMode, bool] = ("blocking", False)


def attach_policy[T](tool: T, policy: ToolPolicy | Mapping[str, ToolPolicy]) -> T:
    """Record ``policy`` on ``tool`` (a function tool or an ``MCPToolset``) and return it."""
    setattr(tool, _POLICY_ATTR, policy)
    return tool


def policy_of(tool: Any) -> ToolPolicy | Mapping[str, ToolPolicy] | None:
    """The policy :func:`attach_policy` recorded: one for a tool, a mapping for a toolset."""
    value = getattr(tool, _POLICY_ATTR, None)
    return value if isinstance(value, (ToolPolicy, Mapping)) else None


def bind_agent_policy(
    tools: Iterable[Any], *, execution_default: ToolExecutionMode, flow_node: bool
) -> list[Any]:
    """Rebuild each tool whose policy depends on the agent default or flow-ness it was built without.

    Tools without a :class:`ToolPolicy`, or already built for these settings, are
    returned as they are (same objects).
    """
    out: list[Any] = []
    for tool in tools:
        policy = policy_of(tool)
        if (
            isinstance(policy, ToolPolicy)
            and policy.rebind is not None
            and policy.built_with != (execution_default, flow_node)
        ):
            tool = policy.rebind(execution_default, flow_node)
        out.append(tool)
    return out


def _tool_name(tool: Any) -> str | None:
    info = getattr(tool, "info", None)
    name = getattr(info, "name", None)
    return name if isinstance(name, str) else None


def flow_mode_of(config: Any) -> bool:
    """Whether an ``AgentConfig`` runs as a flow (``flow`` with nodes): its tools are flow-node tools."""
    flow = getattr(config, "flow", None)
    return bool(flow is not None and getattr(flow, "nodes", None))


#: Per session: every tool name's resolved policy, merged from each agent built for it.
_POLICIES: weakref.WeakKeyDictionary[Any, dict[str, ResolvedExecution]] = weakref.WeakKeyDictionary()


def register_policies(session: Any, tools: Iterable[Any]) -> dict[str, ResolvedExecution]:
    """Remember the policies of ``tools`` (tools and toolsets) for ``session``'s activity feed.

    Returns:
        The session's merged ``{tool name: policy}`` map.
    """
    found: dict[str, ResolvedExecution] = {}
    for tool in tools:
        policy = policy_of(tool)
        if isinstance(policy, ToolPolicy):
            name = _tool_name(tool)
            if name is not None:
                found[name] = policy.resolved
        elif isinstance(policy, Mapping):
            for name, item in policy.items():
                if isinstance(item, ToolPolicy):
                    found[str(name)] = item.resolved
    try:
        merged = _POLICIES.setdefault(session, {})
    except TypeError:
        return found
    merged.update(found)
    return merged


def policies_for(session: Any) -> Mapping[str, ResolvedExecution]:
    """The ``{tool name: policy}`` map :func:`register_policies` built for ``session``."""
    try:
        return _POLICIES.get(session) or {}
    except TypeError:
        return {}


def _context_param(hints: Mapping[str, Any]) -> str | None:
    for name, hint in hints.items():
        origin = get_origin(hint) or hint
        if isinstance(origin, type) and issubclass(origin, RunContext):
            return name
    return None


def wrap_tool(tool: Any, resolved: ResolvedExecution) -> Any:
    """Re-create a pack's function tool so its body runs through :func:`run_with_policy`.

    The new tool keeps the original's name, description and argument schema, and
    declares the policy's flags/duplicate settings. A tool that took no
    ``RunContext`` gains a keyword-only one the SDK injects (it never reaches the
    model's schema). Mirrors how the SDK itself extends a tool's signature for
    ``on_duplicate="confirm"`` (``llm/tool_context.py::_wrap_with_confirm_duplicate``).

    Args:
        tool: A ``FunctionTool`` or ``RawFunctionTool``.
        resolved: The policy to apply.

    Returns:
        The wrapped tool, carrying its :class:`ToolPolicy`.
    """
    info = tool.info
    signature = inspect.signature(tool)
    try:
        hints = get_type_hints(inspect.unwrap(tool), include_extras=True)
    except Exception:  # noqa: BLE001 - unresolvable annotations: fall back to the raw ones
        hints = dict(getattr(tool, "__annotations__", {}))
    hints = {k: v for k, v in hints.items() if k in signature.parameters or k == "return"}
    context_param = _context_param(hints)
    added = context_param is None
    parameters = list(signature.parameters.values())
    if added:
        parameters.append(
            inspect.Parameter(_CONTEXT_PARAM, inspect.Parameter.KEYWORD_ONLY, annotation=RunContext[Any])
        )
        hints[_CONTEXT_PARAM] = RunContext[Any]
        context_param = _CONTEXT_PARAM
    assert context_param is not None  # noqa: S101 - set just above when missing

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        context = kwargs.pop(context_param) if added else kwargs.get(context_param)
        if context is None:
            context = next((a for a in args if isinstance(a, RunContext)), None)

        async def _call() -> Any:
            result = tool(*args, **kwargs)
            return await result if inspect.isawaitable(result) else result

        if not isinstance(context, RunContext):
            return await _call()
        return await run_with_policy(context, resolved, _call)

    # Name and docs only (not `functools.update_wrapper`): copying the tool's `__dict__`
    # or setting `__wrapped__` would make the SDK read the old tool's info and signature.
    wrapper.__name__ = info.name
    wrapper.__qualname__ = info.name
    wrapper.__doc__ = getattr(tool, "__doc__", None)
    wrapper.__signature__ = signature.replace(parameters=parameters)  # type: ignore[attr-defined]
    wrapper.__annotations__ = hints
    flags, on_duplicate, scope = tool_flags(resolved)
    new_tool: Any
    if isinstance(tool, lk_llm.RawFunctionTool):
        new_tool = function_tool(
            wrapper,
            raw_schema=dict(info.raw_schema),
            flags=flags,
            on_duplicate=on_duplicate,
            duplicate_scope=scope,
        )
    else:
        new_tool = function_tool(
            wrapper,
            name=info.name,
            description=info.description,
            flags=flags,
            on_duplicate=on_duplicate,
            duplicate_scope=scope,
        )
    return attach_policy(new_tool, ToolPolicy(resolved=resolved))


# ------------------------------------------------------------------ activity feed

#: A blocking tool without fillers appears in the feed once it has run this long.
SLOW_BLOCKING_S: Final[float] = 1.0

#: Longest headline the feed shows for an update or an error.
_HEADLINE_MAX: Final[int] = 200


@dataclass(slots=True)
class _Row:
    """One activity row the feed is tracking for a running call (D-V4-38)."""

    name: str
    label: str
    started: float
    shown: bool = False
    timer: asyncio.TimerHandle | None = field(default=None)


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _HEADLINE_MAX else text[: _HEADLINE_MAX - 1] + "…"


class ToolActivityFeed:
    """Turns the SDK's ``ToolExecutionUpdatedEvent``s into activity rows (D-V4-38).

    A row is upserted by ``call_id``: ``running`` on start (for a tool with a
    non-blocking mode or fillers; a plain blocking tool only once it has run
    :data:`SLOW_BLOCKING_S`), ``running`` again on each update with the update text
    as headline and ``detail={"message": …}``, then ``done``/``error``/``cancelled``
    with ``duration_ms``. A call that never showed a row emits nothing at its end.
    """

    def __init__(
        self,
        *,
        emit: Callable[[ActivityEvent], None],
        policies: Callable[[], Mapping[str, ResolvedExecution]],
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Create the feed.

        Args:
            emit: Called with each ``ActivityEvent`` in order (the agent sends it to the UI).
            policies: The session's ``{tool name: policy}`` map (:func:`policies_for`).
            clock: Monotonic clock seam for ``duration_ms``.
        """

        self._emit = emit
        self._policies = policies
        self._clock = clock or time.monotonic
        self._rows: dict[str, _Row] = {}

    def handle(self, ev: Any) -> None:
        """Synchronous ``tool_execution_updated`` handler."""
        update = getattr(ev, "update", None)
        kind = getattr(update, "type", None)
        if kind == "tool_call_started":
            self._started(update)
        elif kind == "tool_call_updated":
            self._updated(update)
        elif kind == "tool_call_ended":
            self._ended(update)

    def _started(self, update: Any) -> None:
        call = update.function_call
        policy = self._policies().get(call.name)
        label = policy.label if policy is not None else tool_label(call.name)
        row = _Row(name=call.name, label=label, started=self._clock())
        self._rows[call.call_id] = row
        if policy is not None and policy.tracked:
            self._show(call.call_id, row)
            return
        with contextlib.suppress(RuntimeError):  # no running loop: a slow row is simply not shown
            row.timer = asyncio.get_running_loop().call_later(SLOW_BLOCKING_S, self._slow, call.call_id)

    def _slow(self, call_id: str) -> None:
        row = self._rows.get(call_id)
        if row is not None and not row.shown:
            row.timer = None
            self._show(call_id, row)

    def _show(self, call_id: str, row: _Row) -> None:
        row.shown = True
        self._emit(self._event(call_id, row, "running", f"{row.label} started"))

    def _updated(self, update: Any) -> None:
        row = self._rows.get(update.call_id)
        if row is None:
            return
        if row.timer is not None:
            row.timer.cancel()
            row.timer = None
        row.shown = True
        message = str(update.message or "")
        self._emit(
            self._event(
                update.call_id,
                row,
                "running",
                _clip(message) or f"{row.label} running",
                detail={"message": message},
            )
        )

    def _ended(self, update: Any) -> None:
        row = self._rows.pop(update.call_id, None)
        if row is None:
            return
        if row.timer is not None:
            row.timer.cancel()
        if not row.shown:
            return
        status = update.status
        if status == "error":
            headline = _clip(f"{row.label} failed: {update.message or 'error'}")
        elif status == "cancelled":
            headline = f"{row.label} cancelled"
        else:
            headline = f"{row.label} finished"
        self._emit(
            self._event(
                update.call_id,
                row,
                status,
                headline,
                duration_ms=int((self._clock() - row.started) * 1000),
            )
        )

    @staticmethod
    def _event(
        call_id: str,
        row: _Row,
        phase: Literal["running", "done", "error", "cancelled"],
        headline: str,
        *,
        detail: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> Any:

        return ActivityEvent(
            id=f"tool:{call_id}",
            ts=time.time(),
            source=row.name,
            label=row.label,
            phase=phase,
            headline=headline,
            duration_ms=duration_ms,
            detail=detail,
        )
