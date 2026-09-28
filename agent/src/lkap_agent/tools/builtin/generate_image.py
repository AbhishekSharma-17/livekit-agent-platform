"""`generate_image` built-in tool (V6-06, D-V6-19): a picture into a gallery block.

The generic form of the insurance pack's `draw_incident_sketch`: the agent's own
`pipeline.image_gen` model (any `image_gen` provider) draws what the model
describes, and the picture appears in a `gallery` block of the panel. Registered
only when that slot resolved to a model for the session (`ctx.image_gen`) and the
panel has a gallery block; `builtin_disabled` still switches it off.

It runs the way the pack's sketch does: the call answers at once ("making it
now"; nothing on a realtime model) and the picture is made as a background job
bounded by :data:`GENERATE_TIMEOUT_S`. The bytes must really be a picture (the
first bytes are sniffed, the model's claimed type is never trusted); they are
pushed on `lkap.ui.asset`, stored as a session file (kind `frame`,
`meta.source="generated"`, best effort as a pinned frame is) and shown in the
chosen gallery only. The model is told when it is ready or when it did not come
through. The prompt never reaches a log line.
"""

from __future__ import annotations

import asyncio
from typing import Any, Final

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.blocks import sniff_mime
from lkap_contracts.ui_protocol import MAX_UPLOAD_BYTES, UiPatchOp
from packs.base import PackSessionContext

from lkap_agent.ui.blocks import block_path, describe_blocks, pick_block, session_block_specs

from .set_checklist import QUIET_MODES

__all__ = [
    "GENERATED_SOURCE",
    "GENERATE_TIMEOUT_S",
    "MAX_CAPTION_CHARS",
    "MAX_PROMPT_CHARS",
    "build_generate_image_tool",
]

#: How long one picture may take (the pack sketch's bound, `image_gen.DEFAULT_TIMEOUT_S`).
GENERATE_TIMEOUT_S: Final[float] = 40.0
#: The longest description the model may send.
MAX_PROMPT_CHARS: Final[int] = 2000
#: The longest caption shown under the picture.
MAX_CAPTION_CHARS: Final[int] = 200
#: `AssetRef.meta["source"]` and the stored file's source of a generated picture.
GENERATED_SOURCE: Final[str] = "generated"
#: The display kind of a generated picture (`AssetRef.kind`).
GENERATED_KIND: Final[str] = "picture"

NOT_AVAILABLE: Final[str] = "Pictures are not available in this session; describe it in words instead."


def _clean(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


def build_generate_image_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `generate_image` tool bound to `ctx`."""
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["gallery"])

    async def _show(data: bytes, mime: str, caption: str | None, gallery: str) -> str:
        meta = {"source": GENERATED_SOURCE}
        store = getattr(ctx.ui, "store_asset", None)
        if callable(store):
            asset_id: str = await store(
                data, mime, GENERATED_KIND, caption=caption, meta=meta, galleries=[gallery]
            )
            return asset_id
        # A channel without session files (test doubles, the no-op channel): show it only.
        asset_id = await ctx.ui.push_asset(data, mime, GENERATED_KIND, caption=caption, meta=meta)
        gallery_state = ctx.ui.state.blocks.get(gallery)
        ids = gallery_state.get("asset_ids") if isinstance(gallery_state, dict) else None
        if not isinstance(ids, list) or asset_id not in ids:
            await ctx.ui.patch(
                [UiPatchOp(op="append", path=block_path(gallery, "asset_ids"), value=asset_id)]
            )
        return asset_id

    async def generate_image(
        context: RunContext[Any], prompt: str, caption: str = "", block_id: str = ""
    ) -> str | None:
        """Make a picture and show it in the caller's gallery.

        Args:
            prompt: What to draw, in plain words: the subject, the layout, labels to write on it and
                the style (for example "a simple pen sketch").
            caption: A short caption shown under the picture.
            block_id: The gallery block; leave empty when there is only one.
        """
        image_gen = ctx.image_gen
        if image_gen is None:
            raise ToolError(NOT_AVAILABLE)
        text = _clean(prompt, MAX_PROMPT_CHARS + 1)
        if not text:
            raise ToolError("Describe the picture to make.")
        if len(text) > MAX_PROMPT_CHARS:
            raise ToolError(f"Keep the description under {MAX_PROMPT_CHARS} characters.")
        try:
            gallery = pick_block(session_block_specs(ctx.ui, ctx.config.panel), "gallery", block_id or None)
        except ValueError as exc:
            raise ToolError(f"{exc} A picture needs a gallery block.") from exc
        shown_caption = _clean(caption, MAX_CAPTION_CHARS) or None
        call_id = context.function_call.call_id

        async def _job() -> str | None:
            try:
                data, _claimed = await asyncio.wait_for(
                    image_gen.generate(text, timeout_s=GENERATE_TIMEOUT_S), timeout=GENERATE_TIMEOUT_S + 5
                )
            except Exception as exc:  # noqa: BLE001 - any provider failure degrades to a routine note
                ctx.log.warning(
                    "builtin_tool.generate_image.failed", call_id=call_id, error=type(exc).__name__
                )
                return None
            if len(data) > MAX_UPLOAD_BYTES:
                # V6-21 (S6-25): nothing past the upload cap is streamed to the room.
                ctx.log.warning("builtin_tool.generate_image.too_large", call_id=call_id, size=len(data))
                return None
            mime = sniff_mime(data)
            if mime is None or not mime.startswith("image/"):
                ctx.log.warning("builtin_tool.generate_image.not_a_picture", call_id=call_id, size=len(data))
                return None
            try:
                asset_id = await _show(data, mime, shown_caption, gallery)
            except Exception as exc:  # noqa: BLE001 - the panel is advisory
                ctx.log.warning(
                    "builtin_tool.generate_image.not_shown", call_id=call_id, error=type(exc).__name__
                )
                return None
            ctx.log.debug("builtin_tool.generate_image", call_id=call_id, asset_id=asset_id, gallery=gallery)
            return asset_id

        def _routine_note(asset_id: str | None) -> str | None:
            if asset_id is None:
                return "The picture did not come through this time; tell the caller and offer to try again."
            return "The picture is in the gallery; ask the caller whether it looks right."

        ctx.background.submit(name="generate_image", coro=_job(), routine_note=_routine_note, call_id=call_id)
        if ctx.pipeline_mode in QUIET_MODES:
            return None
        return "Making the picture now; it appears in the gallery in a few seconds."

    return function_tool(
        generate_image,
        description=(
            "Make a picture, such as a sketch of a scene, a diagram or an illustration, and show it in "
            "the caller's gallery. It takes several seconds and you are told when it is ready. Call it "
            f"again with corrections. Gallery blocks: {inventory}."
        ),
    )
