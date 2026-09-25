"""`update_block` built-in tool (CONTRACTS-V2 §4.4): change fields of a panel block.

Registered only when the agent's panel has a block this tool can write (see
`tools.builtin.build_builtin_tools`). The model passes the changed fields as a
JSON-object **string**: a free-form `dict` parameter becomes a Gemini
function declaration of type OBJECT with no properties, which Gemini rejects
for the whole tool list.
"""

from __future__ import annotations

import json
from typing import Any, Final

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.ui_protocol import BlockType, UiPatchOp
from packs.base import PackSessionContext
from pydantic import ValidationError

from lkap_agent.ui.blocks import ENVELOPE_BLOCK_TYPES, describe_blocks, session_block_specs

__all__ = ["UPDATABLE_BLOCK_TYPES", "build_update_block_tool", "parse_json_object"]

#: Block types whose state the model may edit directly. Envelope blocks have
#: their own tools (`set_status`, `push_note`); a form's status/values belong
#: to `request_form` and the user.
UPDATABLE_BLOCK_TYPES: Final[frozenset[BlockType]] = frozenset(
    {
        "document",
        "gallery",
        "table",
        "transcript",
        "video",
        "kb_citations",
        "custom",
        "details",
        "markdown",
        "steps",
    }
)

#: Fields of a form block the model must not rewrite.
_FORM_PROTECTED: Final[frozenset[str]] = frozenset({"status", "values", "submitted_at"})


def parse_json_object(raw: str, what: str) -> dict[str, Any]:
    """Parse a model-supplied JSON-object string or raise a model-readable `ToolError`."""
    try:
        value = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        raise ToolError(f"{what} must be a JSON object: {exc.msg}.") from exc
    if not isinstance(value, dict):
        raise ToolError(f"{what} must be a JSON object, not {type(value).__name__}.")
    return value


def build_update_block_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `update_block` tool bound to `ctx`."""
    specs = session_block_specs(ctx.ui, ctx.config.panel)
    inventory = describe_blocks(specs, UPDATABLE_BLOCK_TYPES | {"form"})

    async def update_block(context: RunContext[Any], block_id: str, patch: str) -> str:
        """Change fields of a block in the user's side panel.

        Args:
            block_id: The id of the block to change.
            patch: A JSON object of the fields to replace, e.g. {"selected": "abc"}.
        """
        live = {s.id: s for s in session_block_specs(ctx.ui, ctx.config.panel)}
        spec = live.get(block_id)
        if spec is None:
            raise ToolError(f"Unknown block {block_id!r}; blocks: {describe_blocks(live.values())}.")
        if spec.type in ENVELOPE_BLOCK_TYPES - {"custom"}:
            raise ToolError(f"Block {block_id!r} shows {spec.type}; use set_status or push_note instead.")
        fields = parse_json_object(patch, "patch")
        if not fields:
            raise ToolError("patch is empty; pass the fields to change.")
        if spec.type == "form" and _FORM_PROTECTED & fields.keys():
            raise ToolError("A form's status and values are set by the user; use request_form to ask.")
        ops = [UiPatchOp(op="set", path=f"/{key}", value=value) for key, value in fields.items()]
        try:
            await ctx.ui.patch_block(block_id, ops)
        except ValidationError as exc:
            raise ToolError(
                f"That change does not fit a {spec.type} block: {exc.errors()[0]['msg']}."
            ) from exc
        ctx.log.debug(
            "builtin_tool.update_block",
            call_id=context.function_call.call_id,
            block_id=block_id,
            fields=sorted(fields),
        )
        return f"Updated {block_id}."

    return function_tool(
        update_block,
        description=(
            f"Change fields of a block in the user's side panel. Blocks you can change: {inventory}."
        ),
    )
