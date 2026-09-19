"""Declarative tool definitions: admin-authored HTTP tools and MCP servers."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

#: Model-facing tool names must match this pattern.
TOOL_NAME_PATTERN = r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$"


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
