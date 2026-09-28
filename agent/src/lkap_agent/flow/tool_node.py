"""Flow `tool` nodes: one attached tool, called with no model turn (V6-17, D-V6-28).

:class:`ToolNodeExecutor` runs a :class:`~lkap_contracts.flow.ToolNode` the moment the flow
enters it and says which outcome the call had; the runtime then takes the edge the node
names for that outcome (:meth:`FlowRuntime.transition <lkap_agent.flow.runtime.FlowRuntime.transition>`).

**The same execution path as a model call.** The tool is the very object the session built
for its model (an HTTP tool, a connected app's action, a dataset lookup — anything in the
tool pool by name) or, for a server of tools, a toolset built for this one call. It runs
through livekit-agents' own :func:`~livekit.agents.llm.execute_function_call` (public in
1.8.3) with a standalone ``RunContext`` on the real ``AgentSession``: argument parsing, the
tool's own checks (``requires_vars``, read-back, the host rules), its execution policy
(``run_with_policy``: a background-mode tool records its announcement but no reply is
fired, and the node still waits for the result), the tool-output guardrail and the fence
all happen exactly as when the model calls it. The node always waits (``timeout_s``,
at most 30 s).

**Arguments** are rendered with V6-07's single pass (``tools.context.render_template``,
ask #36): ``{{ var.<name> }}`` and ``{{ ctx.<name> }}``; a value that is exactly one
``{{ var.<name> }}`` passes the variable as it is (a number stays a number). A missing
value is the ``error`` outcome and nothing is called. ``confirmed`` is never supplied: a
tool that reads values back first refuses, which is also ``error`` (the api refuses such a
step at save).

**Outcomes.** ``error`` — the call raised, refused, timed out or could not start; ``empty``
— it succeeded with an empty result (nothing, ``null``, ``[]``, ``{}``), or the node has
bindings and none found a value (``BindingReport.applied == []``, ask #36); ``ok`` —
anything else.

**The result never reaches the model.** Only the node's ``bindings`` use it (V6-07's
``apply_bindings``: the panel, the checklist, the status, a note, or ``var:<name>``); a bound
variable reaches later instructions fenced as ``tool_binding`` (V6-13, ask #31).

**Records**, as for a model call: ``tool_call_started`` / ``tool_call_ended`` session
events (with ``flow_node``; the arguments as the admin wrote them, never rendered values,
so a caller's phone number is never recorded), an activity row on the panel, and the
tool's outcome for the rules engine's ``tool.<name>.ok`` (``LiveStructure.on_tool_outcomes``).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from livekit.agents import RunContext
from livekit.agents import llm as lk_llm
from livekit.agents.voice import SpeechHandle
from lkap_contracts.flow import ToolNode, ToolNodeOutcome
from lkap_contracts.tool_context import CONFIRMED_PARAMETER, PlaceholderRef
from lkap_contracts.tools import McpServerDefinition
from lkap_contracts.ui_protocol import ActivityEvent

from lkap_agent.logging import get_logger
from lkap_agent.tools.bindings import apply_bindings, parse_result
from lkap_agent.tools.context import (
    TOOL_CONTEXT_USERDATA_KEY,
    ToolCallContext,
    missing_message,
    render_template,
    session_variables,
)
from lkap_agent.tools.execution import tool_label

__all__ = [
    "RESULT_PREVIEW_CHARS",
    "ToolNodeExecutor",
    "ToolNodeResult",
    "is_empty_result",
    "render_arguments",
    "unfence",
]

logger = get_logger(__name__)

#: How much of a result the ``tool_call_ended`` event previews (the observer's own cut).
RESULT_PREVIEW_CHARS: Final[int] = 240

#: ``<untrusted source="…">content</untrusted>``; ``fence`` removes any tag from the content,
#: so the first closing tag is the fence's own.
_FENCE_RE: Final = re.compile(r'\A<untrusted source="[^"]*">(.*)</untrusted>\Z', re.DOTALL)
_TRUNCATED: Final[str] = "... [truncated]"
#: A text argument that is exactly one ``{{ var.<name> }}``.
_WHOLE_VAR_RE: Final = re.compile(r"\A\s*{{\s*var\.([a-z][a-z0-9_]{0,63})\s*}}\s*\Z")


@dataclass(slots=True)
class ToolNodeResult:
    """What one ``tool`` node call did."""

    outcome: ToolNodeOutcome
    #: A short code for events and logs: ``ok``, ``empty_result``, ``nothing_bound``,
    #: ``tool_error``, ``timeout``, ``missing_values``, ``not_attached``, ``unavailable``.
    reason: str
    tool: str
    call_id: str
    duration_ms: int = 0
    bound: list[str] = field(default_factory=list)


def unfence(text: str) -> str:
    """The content of one fenced result (``fence``'s wrapper and truncation mark removed)."""
    match = _FENCE_RE.match(text.strip())
    if match is None:
        return text
    content = match.group(1)
    return content.removesuffix(_TRUNCATED)


def is_empty_result(value: Any) -> bool:
    """Whether a parsed result is empty: nothing, ``null``, blank text, ``[]`` or ``{}``."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list | dict):
        return not value
    return False


def render_arguments(
    arguments: Mapping[str, Any], context: ToolCallContext | None, variables: Mapping[str, Any]
) -> tuple[dict[str, Any], list[PlaceholderRef]]:
    """Render a node's argument templates (see the module docstring).

    Args:
        arguments: ``ToolNode.arguments``.
        context: The session's tool context (``ctx``/``var`` values).
        variables: The session's variable store (for a whole ``{{ var.<name> }}``).

    Returns:
        ``(arguments, missing)``: the rendered arguments and each placeholder with no value.
    """
    rendered: dict[str, Any] = {}
    missing: list[PlaceholderRef] = []
    for name, value in arguments.items():
        if not isinstance(value, str):
            rendered[name] = value
            continue
        whole = _WHOLE_VAR_RE.match(value)
        if whole is not None:
            raw = variables.get(whole.group(1))
            if raw is not None and not (isinstance(raw, str) and not raw.strip()):
                rendered[name] = raw
                continue
        rendered[name] = render_template(value, {}, context, escape=lambda v: v, missing=missing)
    return rendered, missing


def _readback_names(definition: Any, function_name: str) -> list[str]:
    """The ``confirm_readback`` names of the tool a node calls (empty for most tools)."""
    if isinstance(definition, McpServerDefinition):
        spec = definition.tool_context.get(function_name)
        return list(spec.confirm_readback) if spec is not None else []
    return list(getattr(definition, "confirm_readback", None) or [])


class ToolNodeExecutor:
    """Runs ``tool`` nodes for one flow session (see the module docstring)."""

    def __init__(
        self,
        *,
        ctx: Any,
        definitions: Mapping[str, Any],
        tools_by_name: Mapping[str, lk_llm.Tool | lk_llm.Toolset],
        mcp_servers_builder: Callable[..., list[Any]],
        record_event: Callable[[str, dict[str, Any]], None],
    ) -> None:
        """Wire the executor to the session.

        Args:
            ctx: The session's ``SessionContext`` (``session``, ``ui``, ``userdata``).
            definitions: The agent's attached tools by name (``ResolvedAgentConfig.tools``).
            tools_by_name: The session's tool pool by model-facing name.
            mcp_servers_builder: Builds toolsets for server definitions (``flow_node=True``).
            record_event: Records a session event.
        """
        self.ctx = ctx
        self._definitions = definitions
        self._tools = tools_by_name
        self._mcp_builder = mcp_servers_builder
        self._record = record_event

    # ------------------------------------------------------------------ run

    async def run(self, node: ToolNode) -> ToolNodeResult:
        """Call ``node``'s tool once and classify the outcome. Never raises (but for cancellation)."""
        call_id = f"flow_{node.id}_{uuid.uuid4().hex[:10]}"
        definition = self._definitions.get(node.tool)
        function_name = node.mcp_tool if isinstance(definition, McpServerDefinition) else node.tool
        name = function_name or node.tool
        if definition is None:
            logger.warning("flow tool step names a tool the agent lacks", node=node.id, tool=node.tool)
            return ToolNodeResult("error", "not_attached", name, call_id)
        if isinstance(definition, McpServerDefinition) and not node.mcp_tool:
            logger.warning("flow tool step names a server without the tool to call", node=node.id)
            return ToolNodeResult("error", "not_attached", name, call_id)

        context = self._tool_context()
        arguments, missing = render_arguments(node.arguments, context, session_variables(self.ctx))
        if CONFIRMED_PARAMETER in arguments and _readback_names(definition, name):
            # Never confirm a read-back on the caller's behalf (the tool then refuses).
            arguments.pop(CONFIRMED_PARAMETER)
        started = time.monotonic()
        await self._started(node, name, call_id)
        if missing:
            logger.info(
                "flow tool step is missing values",
                node=node.id,
                tool=name,
                missing=[f"{ref.namespace}.{ref.name}" for ref in missing],
            )
            result = ToolNodeResult("error", "missing_values", name, call_id)
            return await self._ended(node, result, started, missing_message(missing, context))

        try:
            output = await asyncio.wait_for(
                self._execute(definition, name, arguments, call_id), timeout=node.timeout_s
            )
        except TimeoutError:
            logger.info("flow tool step timed out", node=node.id, tool=name, timeout_s=node.timeout_s)
            return await self._ended(node, ToolNodeResult("error", "timeout", name, call_id), started, "")
        except _Unavailable as exc:
            logger.warning("flow tool step could not start its tool", node=node.id, tool=name, why=str(exc))
            return await self._ended(node, ToolNodeResult("error", "unavailable", name, call_id), started, "")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("flow tool step failed", node=node.id, tool=name, exc_info=True)
            return await self._ended(node, ToolNodeResult("error", "tool_error", name, call_id), started, "")

        if output.is_error:
            result = ToolNodeResult("error", "tool_error", name, call_id)
            return await self._ended(node, result, started, output.output)

        parsed = parse_result(unfence(output.output))
        result = ToolNodeResult("ok", "ok", name, call_id)
        if node.bindings:
            report = await apply_bindings(parsed, node.bindings, context, tool=name, call_id=call_id)
            result.bound = list(report.applied)
        if is_empty_result(parsed):
            result.outcome, result.reason = "empty", "empty_result"
        elif node.bindings and not result.bound:
            result.outcome, result.reason = "empty", "nothing_bound"
        return await self._ended(node, result, started, output.output)

    async def _execute(
        self, definition: Any, name: str, arguments: dict[str, Any], call_id: str
    ) -> lk_llm.FunctionCallOutput:
        """Run the tool through livekit-agents' function-call path; the call's output."""
        text = json.dumps(arguments, ensure_ascii=False, default=str)
        toolset: Any = None
        try:
            if isinstance(definition, McpServerDefinition):
                built = self._mcp_builder([definition], flow_node=True)
                if not built:
                    raise _Unavailable("the server was refused or could not be built")
                toolset = built[0]
                await toolset.setup()
                tool: lk_llm.Tool | lk_llm.Toolset = toolset
            else:
                found = self._tools.get(name)
                if found is None:
                    raise _Unavailable("the tool is not in this session")
                tool = found
            tool_context = lk_llm.ToolContext([tool])
            if name not in tool_context.function_tools:
                raise _Unavailable("the tool is not offered")
            call = lk_llm.FunctionCall(call_id=call_id, name=name, arguments=text)
            run_context: RunContext[Any] = RunContext(
                session=self.ctx.session, speech_handle=SpeechHandle.create(), function_call=call
            )
            executed = await lk_llm.execute_function_call(
                lk_llm.FunctionToolCall(name=name, arguments=text, call_id=call_id),
                tool_context,
                call_ctx=run_context,
            )
            return executed.fnc_call_out
        finally:
            if toolset is not None:
                try:
                    await toolset.aclose()
                except Exception:
                    logger.debug("could not close the flow tool step's toolset", tool=name, exc_info=True)

    # ------------------------------------------------------------------ records

    def _tool_context(self) -> ToolCallContext | None:
        userdata = getattr(self.ctx, "userdata", None)
        context = userdata.get(TOOL_CONTEXT_USERDATA_KEY) if isinstance(userdata, dict) else None
        return context if isinstance(context, ToolCallContext) else None

    async def _started(self, node: ToolNode, name: str, call_id: str) -> None:
        from lkap_agent.observability import redact_arguments  # noqa: PLC0415 - heavy, first call only

        self._event(
            "tool_call_started",
            {
                "call_id": call_id,
                "tool": name,
                # The templates as written, never rendered values (V6-07: no caller phone in events).
                "args_redacted": redact_arguments(json.dumps(node.arguments, default=str)),
                "flow_node": node.id,
            },
        )
        await self._activity(call_id, name, "running", f"{tool_label(name)} started", None)

    async def _ended(
        self, node: ToolNode, result: ToolNodeResult, started: float, text: str
    ) -> ToolNodeResult:
        result.duration_ms = int((time.monotonic() - started) * 1000)
        failed = result.outcome == "error"
        self._event(
            "tool_call_ended",
            {
                "call_id": result.call_id,
                "tool": result.tool,
                "status": "error" if failed else "done",
                "duration_ms": result.duration_ms,
                "result_preview": (text or "")[:RESULT_PREVIEW_CHARS],
                "flow_node": node.id,
                "outcome": result.outcome,
                "reason": result.reason,
            },
        )
        label = tool_label(result.tool)
        headline = f"{label} failed" if failed else f"{label} finished"
        await self._activity(
            result.call_id, result.tool, "error" if failed else "done", headline, result.duration_ms
        )
        self._rules_outcome(result.tool, not failed)
        logger.info(
            "flow tool step finished",
            node=node.id,
            tool=result.tool,
            outcome=result.outcome,
            reason=result.reason,
            duration_ms=result.duration_ms,
            bound=result.bound,
        )
        return result

    def _event(self, event_type: str, payload: dict[str, Any]) -> None:
        try:
            self._record(event_type, payload)
        except Exception:
            logger.debug("could not record a flow tool step event", event_type=event_type, exc_info=True)

    async def _activity(
        self, call_id: str, name: str, phase: Any, headline: str, duration_ms: int | None
    ) -> None:
        """One activity row on the panel (the SDK's feed never sees a standalone call)."""
        ui = getattr(self.ctx, "ui", None)
        send = getattr(ui, "activity", None)
        if not callable(send):
            return
        try:
            await send(
                ActivityEvent(
                    id=f"tool:{call_id}",
                    ts=time.time(),
                    source=name,
                    label=tool_label(name),
                    phase=phase,
                    headline=headline,
                    duration_ms=duration_ms,
                    kind="tool",
                )
            )
        except Exception:
            logger.debug("could not send the flow tool step's activity row", exc_info=True)

    def _rules_outcome(self, name: str, ok: bool) -> None:
        """Hand the outcome to the rules engine (``tool.<name>.ok``) when the agent has rules."""
        from lkap_agent.extraction.session import LIVE_STRUCTURE_USERDATA_KEY  # noqa: PLC0415 - import cycle

        userdata = getattr(self.ctx, "userdata", None)
        structure = userdata.get(LIVE_STRUCTURE_USERDATA_KEY) if isinstance(userdata, dict) else None
        hook = getattr(structure, "on_tool_outcomes", None)
        if not callable(hook):
            return
        try:
            hook({name: ok})
        except Exception:
            logger.debug("could not hand the flow tool step's outcome to the rules", exc_info=True)


class _Unavailable(Exception):
    """The tool cannot be run in this session (refused server, missing from the pool)."""
