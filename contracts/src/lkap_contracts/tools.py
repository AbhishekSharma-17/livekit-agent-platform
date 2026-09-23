"""Declarative tool definitions (admin-authored HTTP tools and MCP servers) and the
platform's built-in tool names.

The built-in names are the single source the worker, the api and the web share
(asks V2-16-3): ``lkap_agent.tools.builtin`` registers them,
``lkap_api.flows.validation`` checks flow node tool references against them, and
``generated/builtin_tools.json`` carries them to the console.
"""

from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, Field

#: Model-facing tool names must match this pattern.
TOOL_NAME_PATTERN = r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$"

#: Every built-in tool name, in the order the worker's ``build_builtin_tools``
#: considers them; the names ``AgentConfig.tools.builtin_disabled`` refers to.
BUILTIN_TOOL_NAMES: Final[tuple[str, ...]] = (
    "end_call",
    "search_knowledge",
    "http_request",
    "describe_current_frame",
    "pin_frame",
    "push_note",
    "set_status",
    "escalate_to_human",
    "current_time",
)

#: Built-ins registered only when the agent has camera or screen share on.
VISION_TOOL_NAMES: Final[frozenset[str]] = frozenset({"describe_current_frame", "pin_frame"})

#: Panel-block tools (CONTRACTS-V2 §4.4). Not in :data:`BUILTIN_TOOL_NAMES`: each is
#: registered only when the panel has a block it can write (``builtin_disabled``
#: still switches it off).
BLOCK_TOOL_NAMES: Final[tuple[str, ...]] = ("update_block", "show_document", "table_append", "request_form")

#: Block types whose state ``update_block`` may write (envelope blocks and forms have their own tools).
UPDATABLE_BLOCK_TYPES: Final[frozenset[str]] = frozenset(
    {"document", "gallery", "table", "transcript", "video", "kb_citations", "custom"}
)

#: Each block tool → the panel block types that make the worker register it.
BLOCK_TOOL_TYPES: Final[dict[str, frozenset[str]]] = {
    "update_block": UPDATABLE_BLOCK_TYPES,
    "show_document": frozenset({"document"}),
    "table_append": frozenset({"table"}),
    "request_form": frozenset({"form"}),
}


def builtin_tools_document() -> dict[str, Any]:
    """The built-in tool names as plain JSON (``generated/builtin_tools.json``).

    Returns:
        ``{builtin_tool_names, vision_tool_names, block_tool_names, block_tool_types}``;
        sets are sorted so the export is deterministic.
    """
    return {
        "builtin_tool_names": list(BUILTIN_TOOL_NAMES),
        "vision_tool_names": sorted(VISION_TOOL_NAMES),
        "block_tool_names": list(BLOCK_TOOL_NAMES),
        "block_tool_types": {name: sorted(types) for name, types in BLOCK_TOOL_TYPES.items()},
    }


class HttpToolDefinition(BaseModel):
    """An HTTP call exposed to the model as a raw-schema function tool."""

    kind: Literal["http"] = "http"
    name: str = Field(pattern=TOOL_NAME_PATTERN)
    description: str
    parameters: dict[str, Any]
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    url: str
    headers: dict[str, str] = {}
    credential_id: str | None = None
    body_template: str | None = None
    allowed_hosts: list[str] = []
    timeout_s: float = 10
    max_result_chars: int = 4000
    result_path: str | None = None
    silent_reply: bool = False


class McpServerDefinition(BaseModel):
    """A streamable-HTTP MCP server attached to the agent."""

    kind: Literal["mcp"] = "mcp"
    name: str
    url: str
    headers: dict[str, str] = {}
    credential_id: str | None = None
    allowed_tools: list[str] | None = None
    timeout_s: float = 5
    sse_read_timeout_s: float = 300


ToolDefinition = Annotated[HttpToolDefinition | McpServerDefinition, Field(discriminator="kind")]
