"""The declared tool catalog, without an api (for doc-lint, V3-03, and the snapshot, V3-05)."""

from __future__ import annotations

from lkap_mcp.client import LkapClient
from lkap_mcp.registry import Registry, ServerContext, ToolSpec
from lkap_mcp.server import TOOL_MODULES, load_tool_modules
from lkap_mcp.settings import McpSettings


def declared_specs() -> list[ToolSpec]:
    """Every tool every module declares (regardless of scopes or gates), in declaration order."""
    settings = McpSettings()
    registry = Registry(ServerContext(settings=settings, client=LkapClient(settings, api_key="")))
    for module in load_tool_modules(TOOL_MODULES):
        module.register(registry)
    return list(registry.specs.values())


def all_tool_names() -> list[str]:
    """Every declared tool name, sorted."""
    return sorted(spec.name for spec in declared_specs())
