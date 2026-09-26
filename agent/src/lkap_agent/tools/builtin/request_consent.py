"""`request_consent` built-in tool (V5-15, B5): ask the caller to accept or decline on screen.

The tool shows a `consent` block's wording with Accept / Decline and waits on
`UiChannel.request_block` (V5-02). A tap answers `block_submit {values:
{accepted: true | false}}`; the answer is settled by
`record_consent.settle_consent` (the block, the `consent` event with the
SHA-256 of the exact wording, the recording gate, and a goodbye when a
required consent with `decline_action="end_call"` is declined).

* **cascaded**: blocking. Returns what was settled, or a "not answered" line
  after a timeout, a cancel or a barge-in (the caller speaking over the
  question withdraws it, R-V5-1: they are probably answering out loud, so the
  model is told to call `record_consent`).
* **realtime / half_cascade**: returns `None` at once and waits on the
  session's `BackgroundRunner`, like `request_choice`; a tap arrives as an
  *urgent* background result, a missed answer as a routine note, and an
  answer the model recorded itself (`record_consent`, `via: "voice"`) is not
  announced back. `PlatformAgent` keeps the model silent after the call.
* **phone channels** (`sip_in`, `sip_out`): nothing can be shown; the tool
  answers `{"channel": "voice_only", "say": <wording>}` at once and the model
  reads the wording and records the answer with `record_consent`.
* **text channel**: a typed chat has no panel to tap, so the model asks in
  the conversation and records the answer with `record_consent`.
"""

from __future__ import annotations

import json
from typing import Any, Final

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.compliance import ConsentMethod
from lkap_contracts.ui_protocol import UiPatchOp
from packs.base import PackSessionContext
from pydantic import ValidationError

from lkap_agent.tools.builtin.record_consent import (
    VIA_VOICE,
    ConsentOutcome,
    consent_block_config,
    consent_block_text,
    outcome_message,
    settle_consent,
)
from lkap_agent.tools.builtin.request_form import BACKGROUND_FORM_MODES
from lkap_agent.ui.blocks import VOICE_ONLY_CHANNELS, describe_blocks, pick_block, session_block_specs

__all__ = ["CONSENT_TIMEOUT_S", "NOT_ANSWERED", "build_request_consent_tool"]

#: How long the caller has to tap Accept or Decline.
CONSENT_TIMEOUT_S: Final[float] = 90.0

#: What the model hears when nobody tapped (timeout, cancel, barge-in).
NOT_ANSWERED: Final[str] = (
    "The caller did not answer on screen (they spoke, closed it or it timed out). If they answered "
    "out loud, call record_consent with accepted true or false; otherwise ask again."
)


def build_request_consent_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `request_consent` tool bound to `ctx`."""
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["consent"])

    async def wait_for_consent(target: str) -> ConsentOutcome | None:
        """Wait for the caller's tap and settle it; `None` when nobody answered."""
        values = await ctx.ui.request_block(target, timeout_s=CONSENT_TIMEOUT_S)
        if values is None:
            return None
        accepted = values.get("accepted")
        if not isinstance(accepted, bool):
            ctx.log.warning("builtin_tool.request_consent.bad_answer", block_id=target)
            return None
        via = values.get("via")
        method: ConsentMethod = "voice" if via == VIA_VOICE else "tap"
        spec = next(s for s in session_block_specs(ctx.ui, ctx.config.panel) if s.id == target)
        return await settle_consent(
            ctx,
            spec=spec,
            kind=consent_block_config(spec).kind,
            accepted=accepted,
            method=method,
            text=consent_block_text(ctx, spec),
        )

    async def request_consent(context: RunContext[Any], block_id: str = "") -> str | None:
        """Show the caller a consent question on their screen (for example, agreeing to be recorded) and wait.

        Args:
            block_id: The consent block to use; leave empty when there is only one.
        """
        specs = session_block_specs(ctx.ui, ctx.config.panel)
        try:
            target = pick_block(specs, "consent", block_id or None)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        spec = next(s for s in specs if s.id == target)
        text = consent_block_text(ctx, spec)
        if not text.strip():
            raise ToolError(f"The {target} block has no wording to show; ask out loud instead.")
        state = ctx.ui.state.blocks.get(target) or {}
        call_id = context.function_call.call_id
        if isinstance(state, dict) and state.get("accepted") is True:
            return "The caller has already agreed to this; do not ask again."
        channel = getattr(ctx, "channel", "web")
        if channel in VOICE_ONLY_CHANNELS:
            ctx.log.debug("builtin_tool.request_consent", call_id=call_id, block_id=target, voice_only=True)
            return json.dumps(
                {
                    "channel": "voice_only",
                    "say": text,
                    "next": "Read this to the caller word for word, then call record_consent "
                    "with their answer.",
                }
            )
        if channel == "text":
            ctx.log.debug("builtin_tool.request_consent", call_id=call_id, block_id=target, text_channel=True)
            return (
                "Nothing was shown: this is a text chat, and nothing can be tapped here. Ask in the "
                f'conversation, word for word: "{text}" Then call record_consent with their answer.'
            )

        try:
            await ctx.ui.patch_block(
                target,
                [
                    UiPatchOp(op="set", path="/text", value=text),
                    UiPatchOp(op="set", path="/accepted", value=None),
                    UiPatchOp(op="set", path="/method", value=None),
                    UiPatchOp(op="set", path="/at", value=None),
                    UiPatchOp(op="set", path="/text_hash", value=None),
                ],
            )
        except ValidationError as exc:
            raise ToolError(f"The consent block could not be shown: {exc.errors()[0]['msg']}.") from exc
        background = ctx.pipeline_mode in BACKGROUND_FORM_MODES
        ctx.log.debug(
            "builtin_tool.request_consent",
            call_id=call_id,
            block_id=target,
            kind=consent_block_config(spec).kind,
            background=background,
        )

        if background:
            ctx.background.submit(
                name="request_consent",
                coro=wait_for_consent(target),
                urgent=lambda outcome: (
                    outcome is not None and outcome.get("method") != "voice" and not outcome.get("ending")
                ),
                urgent_instructions=lambda outcome: (
                    f"On screen, the caller just {'agreed' if outcome['accepted'] else 'declined'}: "
                    f"{outcome_message(ctx, outcome)} Acknowledge it briefly."
                ),
                routine_note=lambda outcome: (
                    None
                    if outcome is not None
                    else f"The caller did not answer the {target} block on screen."
                ),
                call_id=call_id,
            )
            return None

        outcome = await wait_for_consent(target)
        if outcome is None:
            return NOT_ANSWERED
        return outcome_message(ctx, outcome)

    return function_tool(
        request_consent,
        description=(
            "Show the caller a consent question on their screen, such as agreeing to be recorded, and "
            "wait for Accept or Decline. Read the question aloud too. If the caller answers out loud, "
            f"call record_consent with their answer. Consent blocks: {inventory}."
        ),
    )
