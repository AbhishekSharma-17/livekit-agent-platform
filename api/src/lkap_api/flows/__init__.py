"""Flow builder support (PLAN-V2 V2-16): node specs and flow validation.

Importing this package registers :func:`lkap_api.flows.validation.flow_issues`
into ``lkap_api.config_service.VALIDATORS`` (the documented extension point),
the same way :mod:`lkap_api.catalogs` registers its check. ``routers/flows.py``
imports it, and ``main.py`` already includes that router, so every app
validates flows without ``config_service`` knowing about them.
"""

from __future__ import annotations

from lkap_contracts.tools import BLOCK_TOOL_TYPES, BUILTIN_TOOL_NAMES

from lkap_api.flows.specs import NODE_KINDS, node_specs
from lkap_api.flows.validation import (
    allowed_tool_names,
    derived_mode,
    draft_flow_issues,
    flow_issues,
    pack_tool_names_for,
)

__all__ = [
    "BLOCK_TOOL_TYPES",
    "BUILTIN_TOOL_NAMES",
    "NODE_KINDS",
    "allowed_tool_names",
    "derived_mode",
    "draft_flow_issues",
    "flow_issues",
    "node_specs",
    "pack_tool_names_for",
]
