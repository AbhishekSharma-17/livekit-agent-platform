"""`request_upload` built-in tool (V5-19, B6): ask the caller to send photos or documents.

The tool writes the prompt into an `upload` block (clearing the files and
refusals of an earlier request), then waits on `UiChannel.request_block`
(V5-02) while the caller streams files on `lkap.ui.upload`. The channel checks
each file (count, size, the real type against the block's `accept`) and stores
it through the api; the browser then answers `block_submit {values: {files:
[asset_id, ...]}}`. The answer is always read from the block's `files`, which
only the worker writes, never from what the browser claims.

* **cascaded**: blocking. Returns `{"files": [{asset_id, name, mime, size}]}`,
  or a "not sent" line after a timeout, a cancel or a barge-in (the caller
  speaking cancels the request, D-V5-34; the model may ask again).
* **realtime / half_cascade**: returns `None` at once and the wait runs on the
  session's `BackgroundRunner`, exactly like `request_choice`; `PlatformAgent`
  keeps the model silent after the call (R-V5-1).
* **phone channels** (`sip_in`, `sip_out`): nothing is shown (no screen); the
  tool answers `{"channel": "voice_only"}` at once.
* **text channel**: a typed chat has no panel; the tool answers at once.
"""

from __future__ import annotations

import json
from typing import Any, Final

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.ui_protocol import UiPatchOp
from packs.base import PackSessionContext

from lkap_agent.tools.builtin.request_form import BACKGROUND_FORM_MODES
from lkap_agent.ui.blocks import VOICE_ONLY_CHANNELS, describe_blocks, pick_block, session_block_specs

__all__ = ["NOT_SENT", "UPLOAD_TIMEOUT_S", "build_request_upload_tool", "received_files"]

#: How long the caller has to pick and send files (a phone camera takes a while).
UPLOAD_TIMEOUT_S: Final[float] = 300.0

#: The cascaded result when nothing arrived.
NOT_SENT: Final[str] = (
    "The caller did not send any file (they spoke, closed the picker or it timed out). "
    "If they still want to send one, call request_upload again; otherwise continue without it."
)

_VOICE_ONLY_NOTE: Final[str] = (
    "Files can't be sent on a phone call. Tell the caller they can send them later from the web "
    "page, or continue without them."
)


def received_files(state: dict[str, Any], values: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The files of this request as the model sees them: `{asset_id, name, mime, size}` each.

    Only files the worker stored (`state["files"]`) count; when the browser's
    answer names asset ids, the list is narrowed to those.
    """
    raw = state.get("files")
    files = [f for f in raw if isinstance(f, dict)] if isinstance(raw, list) else []
    named = values.get("files") if isinstance(values, dict) else None
    if isinstance(named, list) and named:
        wanted = {str(n) for n in named}
        files = [f for f in files if f.get("asset_id") in wanted]
    return [
        {"asset_id": f.get("asset_id"), "name": f.get("name"), "mime": f.get("mime"), "size": f.get("size")}
        for f in files
    ]


def build_request_upload_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `request_upload` tool bound to `ctx`."""
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["upload"])

    async def wait_for_files(target: str) -> list[dict[str, Any]] | None:
        """Wait for the caller's files; the stored ones, or `None` when nothing arrived."""
        request = getattr(ctx.ui, "request_block", None)
        if not callable(request):
            return None
        values = await request(target, timeout_s=UPLOAD_TIMEOUT_S)
        if values is None:
            return None
        files = received_files(ctx.ui.state.blocks.get(target) or {}, values)
        return files or None

    async def request_upload(context: RunContext[Any], prompt: str, block_id: str = "") -> str | None:
        """Ask the caller to send photos or documents from their device, and wait for them.

        Args:
            prompt: What to send, shown above the picker, e.g. A photo of the damage.
            block_id: The upload block to use; leave empty when there is only one.
        """
        specs = session_block_specs(ctx.ui, ctx.config.panel)
        try:
            target = pick_block(specs, "upload", block_id or None)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        if not prompt.strip():
            raise ToolError("Say what the caller should send, e.g. a photo of the damage.")
        channel = getattr(ctx, "channel", "web")
        call_id = context.function_call.call_id
        if channel in VOICE_ONLY_CHANNELS:
            ctx.log.debug("builtin_tool.request_upload", call_id=call_id, block_id=target, voice_only=True)
            return json.dumps({"channel": "voice_only", "note": _VOICE_ONLY_NOTE})
        if channel == "text":
            ctx.log.debug("builtin_tool.request_upload", call_id=call_id, block_id=target, text_channel=True)
            return "Nothing was shown: files can't be sent in this text chat. Continue without them."

        patch_block = getattr(ctx.ui, "patch_block", None)
        if callable(patch_block):
            await patch_block(
                target,
                [
                    UiPatchOp(op="set", path="/prompt", value=prompt.strip()[:500]),
                    UiPatchOp(op="set", path="/files", value=[]),
                    UiPatchOp(op="set", path="/rejected", value=[]),
                    UiPatchOp(op="set", path="/progress", value=None),
                ],
            )
        background = ctx.pipeline_mode in BACKGROUND_FORM_MODES
        ctx.log.debug("builtin_tool.request_upload", call_id=call_id, block_id=target, background=background)

        if background:
            ctx.background.submit(
                name="request_upload",
                coro=wait_for_files(target),
                urgent=lambda files: files is not None,
                urgent_instructions=lambda files: (
                    f"The caller just sent {len(files)} file(s): {json.dumps(files)}. "
                    "Acknowledge briefly and continue."
                ),
                routine_note=lambda files: (
                    None if files is not None else f"The caller did not send a file in the {target} block."
                ),
                call_id=call_id,
            )
            return None

        files = await wait_for_files(target)
        if files is None:
            return NOT_SENT
        return json.dumps({"files": files})

    return function_tool(
        request_upload,
        description=(
            "Ask the caller to send photos or documents (a damage photo, a licence, a receipt) from "
            "their phone or computer, and wait for them. Say what to send and that a picker is on their "
            "screen. Each file is checked and stored; use describe_asset to read one. "
            f"Upload blocks: {inventory}."
        ),
    )
