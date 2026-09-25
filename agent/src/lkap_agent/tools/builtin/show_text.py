"""`show_text` built-in tool (V5-08, B3): show longer text in a `markdown` block.

The block renders a strict Markdown subset (no raw HTML; links only when the
block's `allow_links` is on; images only from session assets), so the tool
refuses raw HTML outright and text longer than the block's `max_chars`,
telling the model why. On phone channels the caller has no screen: the tool
answers `{"visible": false}` and writes nothing.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Final

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.ui_protocol import MarkdownBlockState
from packs.base import PackSessionContext

from lkap_agent.ui.blocks import VOICE_ONLY_CHANNELS, describe_blocks, pick_block, session_block_specs

__all__ = ["DEFAULT_MAX_CHARS", "build_show_text_tool", "contains_html"]

#: `MarkdownBlockConfig.max_chars` default.
DEFAULT_MAX_CHARS: Final[int] = 8000

#: An HTML tag (`<b>`, `</div>`, `<img src=...>`) or comment, as a browser would parse
#: one: no space after `<`. `<https://...>` autolinks are Markdown, not HTML: a tag name
#: is letters, digits and dashes only.
_HTML = re.compile(r"<!--|</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>")


def contains_html(text: str) -> bool:
    """Whether `text` carries a raw HTML tag or comment."""
    return _HTML.search(text) is not None


def build_show_text_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `show_text` tool bound to `ctx`."""
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["markdown"])

    async def show_text(context: RunContext[Any], markdown: str, title: str = "", block_id: str = "") -> str:
        """Show longer text (Markdown) in the caller's side panel, replacing what it showed.

        Args:
            markdown: The text, in Markdown. No HTML.
            title: An optional heading above the text.
            block_id: The text block; leave empty when there is only one.
        """
        specs = session_block_specs(ctx.ui, ctx.config.panel)
        try:
            target = pick_block(specs, "markdown", block_id or None)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        if getattr(ctx, "channel", "web") in VOICE_ONLY_CHANNELS:
            return json.dumps({"visible": False})
        text = markdown.strip()
        if not text:
            raise ToolError("Pass the text to show.")
        if contains_html(text) or contains_html(title):
            raise ToolError("Raw HTML is not allowed here; write Markdown instead.")
        config = next((s.config for s in specs if s.id == target), {})
        limit = config.get("max_chars")
        max_chars = limit if isinstance(limit, int) else DEFAULT_MAX_CHARS
        if len(text) > max_chars:
            raise ToolError(f"The text is {len(text)} characters; this block shows at most {max_chars}.")
        state = MarkdownBlockState(markdown=text, title=title.strip() or None, updated_at=time.time())
        await ctx.ui.set_block(target, state.model_dump(mode="json"))
        show = getattr(ctx.ui, "show_block", None)
        if callable(show):
            show(target)
        ctx.log.debug(
            "builtin_tool.show_text", call_id=context.function_call.call_id, block_id=target, chars=len(text)
        )
        return "The text is on screen. Give a one-sentence summary; do not read it aloud."

    return function_tool(
        show_text,
        description=(
            "Show longer text in Markdown on the caller's screen, such as a recap, instructions or a "
            "quoted clause. Speak a one-sentence summary; the full text is on screen. "
            f"Text blocks: {inventory}."
        ),
    )
