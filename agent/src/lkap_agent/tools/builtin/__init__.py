"""Built-in standard tools (docs/ARCHITECTURE.md §7.3) and their aggregator.

Every built-in tool is a plain `@function_tool` closure over a
`PackSessionContext`. `PlatformAgent` (W1-AGENT-CORE) is expected to call
`build_builtin_tools` once per session and pass its result alongside
declarative (`tools/declarative.py`) and pack tools to `Agent(tools=...)`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from livekit.agents import FunctionTool
from lkap_contracts.tools import (
    BACKGROUNDABLE_BUILTINS,
    BLOCK_TOOL_NAMES,
    BUILTIN_TOOL_NAMES,
    ToolExecution,
    ToolExecutionMode,
)
from packs.base import PackSessionContext

from lkap_agent.logging import get_logger
from lkap_agent.telephony import TELEPHONY_TOOL_NAMES
from lkap_agent.tools.execution import ResolvedExecution, flow_mode_of, resolve_execution

from .current_time import build_current_time_tool
from .describe_current_frame import build_describe_current_frame_tool
from .end_call import build_end_call_tool
from .escalate_to_human import build_escalate_to_human_tool
from .http_request import build_http_request_tool
from .pin_frame import build_pin_frame_tool
from .push_note import build_push_note_tool
from .request_choice import build_request_choice_tool
from .request_form import build_request_form_tool
from .resolve_choice import build_resolve_choice_tool
from .search_knowledge import build_search_knowledge_tool
from .set_details import build_set_details_tool
from .set_status import build_set_status_tool
from .set_steps import build_set_steps_tool, manual_steps_blocks
from .show_document import build_show_document_tool
from .show_text import build_show_text_tool
from .table_append import build_table_append_tool
from .update_block import UPDATABLE_BLOCK_TYPES, build_update_block_tool

__all__ = [
    "BLOCK_TOOL_NAMES",
    "BUILTIN_TOOL_NAMES",
    "TELEPHONY_TOOL_NAMES",
    "build_builtin_tools",
    "build_current_time_tool",
    "build_describe_current_frame_tool",
    "build_end_call_tool",
    "build_escalate_to_human_tool",
    "build_http_request_tool",
    "build_pin_frame_tool",
    "build_push_note_tool",
    "build_request_choice_tool",
    "build_request_form_tool",
    "build_resolve_choice_tool",
    "build_search_knowledge_tool",
    "build_set_details_tool",
    "build_set_status_tool",
    "build_set_steps_tool",
    "build_show_document_tool",
    "build_show_text_tool",
    "build_table_append_tool",
    "build_update_block_tool",
]

logger = get_logger(__name__)

# `BUILTIN_TOOL_NAMES` (the order `build_builtin_tools` considers them — the names
# `AgentConfig.tools.builtin_disabled` and the console's toggles refer to) and
# `BLOCK_TOOL_NAMES` (panel-block tools, CONTRACTS-V2 §4.4; registered only when the
# panel has a block they write) come from `lkap_contracts.tools`, the single list
# the api's flow validation and the web share (asks V2-16-3, V2-19B-2).

# `TELEPHONY_TOOL_NAMES` (`send_dtmf`, `transfer_call`; R-V2-25) is imported from
# `lkap_agent.telephony` above: like the block tools they stay out of
# `BUILTIN_TOOL_NAMES`, because `build_telephony_tools` registers them only on
# SIP sessions (`send_dtmf` with `capabilities.dtmf`, `transfer_call` with a
# non-empty `config.telephony.transfer_targets`); `builtin_disabled` still
# switches them off.


def build_builtin_tools(
    ctx: PackSessionContext,
    disabled: list[str],
    http_enabled: bool,
    *,
    platform_allowed_hosts: list[str] | None = None,
    shutdown: Callable[[str], None] | None = None,
    http_user_agent: str | None = None,
    execution: Mapping[str, ToolExecution] | None = None,
    execution_default: ToolExecutionMode | None = None,
    flow_node: bool | None = None,
) -> list[FunctionTool[..., Any]]:
    """Build every enabled built-in tool for one session.

    Args:
        ctx: The session's `PackSessionContext`.
        disabled: Tool names to skip (`AgentConfig.tools.builtin_disabled`).
        http_enabled: Whether `http_request` may be registered at all
            (`AgentConfig.tools.http_request_enabled`); it still needs a
            non-empty `platform_allowed_hosts` to ever succeed at call time.
        platform_allowed_hosts: `LKAP_HTTP_TOOL_ALLOWED_HOSTS`, threaded
            through rather than read from `Settings` here so this module
            stays test-friendly (docs/CONTRACTS.md §3).
        shutdown: Injected for `end_call`; defaults to
            `get_job_context().shutdown` when omitted.
        http_user_agent: `LKAP_HTTP_TOOL_USER_AGENT`, the `User-Agent`
            `http_request` sends (none when omitted).
        execution: Per built-in execution settings; defaults to
            `ctx.config.tools.builtin_execution`. Only the names in
            `BACKGROUNDABLE_BUILTINS` are read (others are ignored with a warning).
        execution_default: The agent's read-tool default; defaults to
            `ctx.config.tools.execution_default`.
        flow_node: Whether these tools run on flow nodes (the 1.8.3 gate);
            defaults to whether `ctx.config` is a flow.

    Returns:
        The enabled tools. `describe_current_frame` and `pin_frame` are
        omitted unless the agent has `capabilities.camera` or
        `capabilities.screen_share` (docs/ARCHITECTURE.md §8) — they have
        nothing to encode otherwise. The `BLOCK_TOOL_NAMES` tools are
        registered only when `AgentConfig.panel.blocks` has a block they
        write: `update_block` for any non-envelope block, `show_document` /
        `table_append` / `request_form` for a `document` / `table` / `form`
        block, `request_choice` and `resolve_choice` for a `choices` block,
        `set_details` / `show_text` for a `details` / `markdown` block, and
        `set_steps` for a `steps` block that does not follow the flow (V5-08).
    """
    skip = set(disabled)
    has_vision = ctx.config.capabilities.camera or ctx.config.capabilities.screen_share

    def _want(name: str) -> bool:
        return name not in skip

    tools_config = getattr(ctx.config, "tools", None)
    declared: Mapping[str, ToolExecution] = (
        execution if execution is not None else getattr(tools_config, "builtin_execution", None) or {}
    )
    default: ToolExecutionMode = (
        execution_default
        if execution_default is not None
        else getattr(tools_config, "execution_default", None) or "blocking"
    )
    on_flow = flow_node if flow_node is not None else flow_mode_of(ctx.config)
    for name in sorted(set(declared) - BACKGROUNDABLE_BUILTINS):
        logger.warning(
            "builtin execution setting ignored: only read built-ins run in the background", tool=name
        )

    def _policy(name: str) -> ResolvedExecution:
        return resolve_execution(
            name=name,
            kind="builtin",
            is_read=True,
            declared=declared.get(name),
            agent_default=default,
            flow_node=on_flow,
        )

    tools: list[FunctionTool[..., Any]] = []
    if _want("end_call"):
        tools.append(build_end_call_tool(ctx, shutdown=shutdown))
    if _want("search_knowledge"):
        tools.append(build_search_knowledge_tool(ctx, execution=_policy("search_knowledge")))
    if http_enabled and _want("http_request"):
        tools.append(
            build_http_request_tool(
                ctx,
                platform_allowed_hosts=platform_allowed_hosts,
                user_agent=http_user_agent,
                execution=_policy("http_request"),
            )
        )
    if has_vision and _want("describe_current_frame"):
        tools.append(build_describe_current_frame_tool(ctx, execution=_policy("describe_current_frame")))
    if has_vision and _want("pin_frame"):
        tools.append(build_pin_frame_tool(ctx))
    if _want("push_note"):
        tools.append(build_push_note_tool(ctx))
    if _want("set_status"):
        tools.append(build_set_status_tool(ctx))
    if _want("escalate_to_human"):
        tools.append(build_escalate_to_human_tool(ctx))
    if _want("current_time"):
        tools.append(build_current_time_tool(ctx))

    block_types = {spec.type for spec in ctx.config.panel.blocks}
    if block_types & UPDATABLE_BLOCK_TYPES and _want("update_block"):
        tools.append(build_update_block_tool(ctx))
    if "document" in block_types and _want("show_document"):
        tools.append(build_show_document_tool(ctx))
    if "table" in block_types and _want("table_append"):
        tools.append(build_table_append_tool(ctx))
    if "form" in block_types and _want("request_form"):
        tools.append(build_request_form_tool(ctx))
    if "choices" in block_types and _want("request_choice"):
        tools.append(build_request_choice_tool(ctx))
    if "choices" in block_types and _want("resolve_choice"):
        tools.append(build_resolve_choice_tool(ctx))
    if "details" in block_types and _want("set_details"):
        tools.append(build_set_details_tool(ctx))
    if "markdown" in block_types and _want("show_text"):
        tools.append(build_show_text_tool(ctx))
    if manual_steps_blocks(list(ctx.config.panel.blocks)) and _want("set_steps"):
        tools.append(build_set_steps_tool(ctx))

    return tools
