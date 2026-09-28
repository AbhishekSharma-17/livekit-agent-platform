"""`push_note` built-in tool (docs/ARCHITECTURE.md §7.3).

V6-06 (D-V6-19): `block_id` pins the note to one block of the panel, so the
console shows it in that block's margin (`Note.block_id`). Without it the note
goes to the notes list exactly as before.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.ui_protocol import Note, UiPatchOp
from packs.base import PackSessionContext

from lkap_agent.ui.blocks import session_block_specs


def build_push_note_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `push_note` tool bound to `ctx`."""

    @function_tool
    async def push_note(context: RunContext[Any], text: str, kind: str = "note", block_id: str = "") -> str:
        """Add a free-text note to the shared notebook UI.

        Args:
            text: The note text.
            kind: A pack-defined note category (default "note").
            block_id: A panel block to pin the note to, shown beside that block; leave empty for
                the notes list.
        """
        if not block_id:
            await ctx.ui.add_note(text, kind=kind)
            return "Noted."
        ids = [spec.id for spec in session_block_specs(ctx.ui, ctx.config.panel)]
        if block_id not in ids:
            raise ToolError(f"Unknown block {block_id!r}; use one of: {', '.join(ids) or 'none'}.")
        note = Note(id=str(uuid.uuid4()), text=text, kind=kind, ts=time.time(), block_id=block_id)
        await ctx.ui.patch([UiPatchOp(op="append", path="/notes", value=note)])
        ctx.log.debug("builtin_tool.push_note", call_id=context.function_call.call_id, block_id=block_id)
        return "Noted."

    return push_note
