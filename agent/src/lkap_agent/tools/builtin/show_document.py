"""`show_document` built-in tool (CONTRACTS-V2 §4.4): open a document in a `document` block.

The source is either an `asset_id` already delivered on `lkap.ui.asset`
(listed in `UiState.assets`) or an `https://` URL. A `note` becomes a
page-level highlight: `bbox` `[0, 0, 1, 1]` in page-relative coordinates
(0..1, origin top-left), i.e. "the whole page". After the state is written the
browser is asked, best effort, to bring the block into view (`show_block`).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.ui_protocol import DocumentBlockState, DocumentHighlight
from packs.base import PackSessionContext

from lkap_agent.ui.blocks import describe_blocks, pick_block, session_block_specs

__all__ = ["FULL_PAGE_BBOX", "build_show_document_tool"]

#: Page-relative (0..1) rectangle covering a whole page.
FULL_PAGE_BBOX: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)


def build_show_document_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `show_document` tool bound to `ctx`."""
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["document"])

    async def show_document(
        context: RunContext[Any],
        url: str = "",
        asset_id: str = "",
        page: int = 1,
        note: str = "",
        block_id: str = "",
    ) -> str:
        """Open a document (PDF, image or markdown) in the user's side panel.

        Args:
            url: An https URL of the document. Leave empty when using asset_id.
            asset_id: The id of a file already shared in this session. Leave empty when using url.
            page: The page to open (1-based).
            note: An optional note to show on that page.
            block_id: The document block to use; leave empty when there is only one.
        """
        specs = session_block_specs(ctx.ui, ctx.config.panel)
        try:
            target = pick_block(specs, "document", block_id or None)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        if bool(url) == bool(asset_id):
            raise ToolError("Pass exactly one of url or asset_id.")
        if url and urlsplit(url).scheme != "https":
            raise ToolError("Only https URLs can be shown.")
        if asset_id and asset_id not in {a.asset_id for a in ctx.ui.state.assets}:
            raise ToolError(f"No shared file has asset_id {asset_id!r}.")
        page = max(1, page)
        highlights = [DocumentHighlight(page=page, bbox=FULL_PAGE_BBOX, note=note)] if note else []
        state = DocumentBlockState(
            asset_id=asset_id or None, url=url or None, page=page, highlights=highlights
        )
        await ctx.ui.set_block(target, state.model_dump(mode="json"))
        show = getattr(ctx.ui, "show_block", None)
        if callable(show):
            show(target)
        ctx.log.debug(
            "builtin_tool.show_document",
            call_id=context.function_call.call_id,
            block_id=target,
            source="asset" if asset_id else "url",
            page=page,
        )
        return f"The document is open on page {page}."

    return function_tool(
        show_document,
        description=(
            "Open a document (PDF, image or markdown) in the user's side panel. "
            f"Document blocks: {inventory}."
        ),
    )
