"""`update_block` built-in tool (CONTRACTS-V2 §4.4): change fields of a panel block.

Registered only when the agent's panel has a block this tool can write (see
`tools.builtin.build_builtin_tools`). V6-21 (S6-1, ask #96): it writes only an
allow-listed type (:data:`UPDATABLE_BLOCK_TYPES` and `form`); every other block — the
envelope, a `notebook` or `layout` (V6-08), a `canvas` (V6-12), and each requestable or
host-checked block (`consent`, `link`, `upload`, `slots`, `choices`, `handoff`,
`captions`) — is refused, so the model cannot forge a caller's answer or put a link on a
host its own tool refuses. A card's `image_url` passes the block's `image_hosts` as
`show_cards` checks it. The model passes the changed fields as a
JSON-object **string**: a free-form `dict` parameter becomes a Gemini
function declaration of type OBJECT with no properties, which Gemini rejects
for the whole tool list.
"""

from __future__ import annotations

import json
from typing import Any, Final

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.blocks import CardsBlockConfig
from lkap_contracts.ui_protocol import BlockType, UiPatchOp, https_url_problem
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
        # V5-43: cards are display data (a tap arrives as a block action).
        "cards",
    }
)

#: Fields of a form block the model must not rewrite.
_FORM_PROTECTED: Final[frozenset[str]] = frozenset({"status", "values", "submitted_at"})

#: What to use instead, for the refused types that have their own tools.
_OWN_TOOLS: Final[dict[str, str]] = {
    "notebook": "is a notebook; use notebook_write or notebook_check",
    "layout": "only groups other blocks; change those blocks instead",
    "canvas": "is a drawing board; use draw_on_canvas or clear_canvas",
    "link": "is a link; use send_link",
    "consent": "asks for consent; use request_consent",
    "upload": "asks for files; use request_upload",
    "choices": "asks for a choice; use request_choice",
}


def _refusal(block_id: str, block_type: str) -> str:
    """Why ``update_block`` does not write a block of ``block_type`` (outside the allow-list)."""
    if block_type in ENVELOPE_BLOCK_TYPES:
        return f"Block {block_id!r} shows {block_type}; use set_status or push_note instead."
    hint = _OWN_TOOLS.get(block_type)
    if hint is not None:
        return f"Block {block_id!r} {hint}."
    return f"Block {block_id!r} shows {block_type}, which update_block cannot change."


def _check_card_images(fields: dict[str, Any], config: dict[str, Any]) -> None:
    """A patched card's ``image_url`` must be on the block's ``image_hosts`` (as ``show_cards`` checks)."""
    cards = fields.get("cards")
    if not isinstance(cards, list):
        return
    try:
        hosts = CardsBlockConfig.model_validate(config).image_hosts
    except ValidationError:
        hosts = []
    for card in cards:
        url = card.get("image_url") if isinstance(card, dict) else None
        if not url:
            continue
        if not hosts:
            raise ToolError("This cards block shows no pictures from the web; use image_asset_id or none.")
        problem = https_url_problem(str(url), allowed_hosts=hosts)
        if problem is not None:
            raise ToolError(f"A card's picture: {problem}.")


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
        # V6-21 (S6-1): an allow-list, not a list of refusals.
        if spec.type not in UPDATABLE_BLOCK_TYPES | {"form"}:
            raise ToolError(_refusal(block_id, spec.type))
        fields = parse_json_object(patch, "patch")
        if not fields:
            raise ToolError("patch is empty; pass the fields to change.")
        if spec.type == "form" and _FORM_PROTECTED & fields.keys():
            raise ToolError("A form's status and values are set by the user; use request_form to ask.")
        if spec.type == "cards":
            _check_card_images(fields, spec.config)
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
