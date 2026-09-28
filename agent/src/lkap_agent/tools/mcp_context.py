"""MCP tools with tool context: pinned arguments, required variables, read-back, bindings (V6-07).

Built only for an ``McpServerDefinition`` whose ``tool_context`` names at least one tool;
every other server is a plain :class:`~lkap_agent.tools.mcp_client.GuardedMCPServerHTTP`,
exactly as before V6-07.

Verified against livekit-agents 1.8.3 (``livekit/agents/llm/mcp.py``;
``tests/unit/test_sdk_tripwires.py`` pins each fact):

* ``MCPServer._make_function_tool(name, description, input_schema, meta, *, options)``
  builds each tool with ``function_tool(impl, raw_schema=…)``; ``impl`` takes
  ``(raw_arguments)``, or ``(ctx, raw_arguments)`` when ``options["report_progress"]`` is on.
  The override builds the SDK's tool from the schema the model should see (pinned
  arguments hidden, ``confirmed`` added) and wraps its ``impl`` so the checks and pins run
  before ``call_tool``.
* ``_resolve`` awaits the ``tool_result_resolver`` when it returns a coroutine, so the
  resolver applies the bindings to the raw ``CallToolResult`` and then hands it to the
  fence (``mcp_client.fenced_result_resolver``). An ``isError`` result never reaches the
  resolver, so bindings only ever see a successful result.

The value bindings point into: the result's ``structuredContent`` when the server sends
one; else a single text item parsed as JSON (or the text itself); else the list of items.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from livekit.agents import RunContext, function_tool
from livekit.agents.llm.mcp import MCPToolOptions, MCPToolResultContext, MCPToolResultResolver
from lkap_contracts.tool_context import ToolContextSpec

from lkap_agent.tools.bindings import apply_bindings, parse_result
from lkap_agent.tools.context import (
    ToolCallContext,
    check_readback,
    check_requires,
    hide_pinned,
    pin_arguments,
    readback_parameters,
)
from lkap_agent.tools.mcp_client import GuardedMCPServerHTTP

__all__ = ["ContextMCPServerHTTP", "mcp_result_value"]


def mcp_result_value(result: Any) -> Any:
    """The value an MCP result's bindings point into (see the module docstring)."""
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    content = list(getattr(result, "content", None) or [])
    if len(content) == 1 and getattr(content[0], "type", None) == "text":
        return parse_result(str(getattr(content[0], "text", "")))
    return [item.model_dump() if hasattr(item, "model_dump") else item for item in content]


class ContextMCPServerHTTP(GuardedMCPServerHTTP):
    """A guarded MCP server whose listed tools honour their ``tool_context`` settings."""

    def __init__(
        self,
        *args: Any,
        tool_context: Mapping[str, ToolContextSpec],
        context: ToolCallContext | None = None,
        **kwargs: Any,
    ) -> None:
        """Same arguments as ``GuardedMCPServerHTTP``, plus the per-tool settings and the session.

        Args:
            args: ``GuardedMCPServerHTTP``'s positional arguments.
            tool_context: ``McpServerDefinition.tool_context``.
            context: The session's tool context (``None`` = no session values, no panel).
            kwargs: ``GuardedMCPServerHTTP``'s keyword arguments.
        """
        super().__init__(*args, **kwargs)
        self._specs: dict[str, ToolContextSpec] = dict(tool_context)
        self._session_context = context
        self._tool_result_resolver = self._binding_resolver(self._tool_result_resolver)

    def _binding_resolver(self, inner: MCPToolResultResolver) -> Callable[[MCPToolResultContext], Any]:
        async def _resolve(ctx: MCPToolResultContext) -> Any:
            spec = self._specs.get(ctx.tool_name)
            if spec is not None and spec.bindings:
                await apply_bindings(
                    mcp_result_value(ctx.result), spec.bindings, self._session_context, tool=ctx.tool_name
                )
            resolved = inner(ctx)
            if hasattr(resolved, "__await__"):
                resolved = await resolved
            return resolved

        return _resolve

    def _make_function_tool(
        self,
        name: str,
        description: str | None,
        input_schema: dict[str, Any],
        meta: dict[str, Any] | None,
        *,
        options: MCPToolOptions,
    ) -> Any:
        spec = self._specs.get(name)
        if spec is None:
            return super()._make_function_tool(name, description, input_schema, meta, options=options)
        schema = readback_parameters(hide_pinned(input_schema, spec.pinned_arguments), spec.confirm_readback)
        tool = super()._make_function_tool(name, description, schema, meta, options=options)
        impl = tool._func
        takes_ctx = bool(options.get("report_progress"))
        session = self._session_context

        async def _called(ctx: RunContext[Any], raw_arguments: dict[str, Any]) -> Any:
            arguments = check_readback(dict(raw_arguments), spec.confirm_readback, tool=name)
            check_requires(spec.requires_vars, session, tool=name)
            arguments = pin_arguments(arguments, spec.pinned_arguments, session, tool=name)
            if takes_ctx:
                return await impl(ctx, arguments)
            return await impl(arguments)

        return function_tool(
            _called,
            raw_schema=dict(tool.info.raw_schema),
            flags=options["flags"],
            on_duplicate=options["on_duplicate"],
            duplicate_scope=options["duplicate_scope"],
        )
