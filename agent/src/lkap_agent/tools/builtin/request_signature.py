"""`request_signature` built-in tool (V6-23, D-V6-20): ask the caller to sign on screen.

The tool shows the wording over a small signing board in a `signature` block and waits on
`UiChannel.request_block` (V5-02). The wording is the block config's `disclosure_text` when it
sets one (the agent cannot change it), else the model's `disclosure_text`; either way it is
checked (no control characters, at most 2,000 characters) and written to the block before the
request. The caller's strokes stay on their page. When they tap Sign the page answers
`block_submit {values: {signed: true}}` (``false`` for "Not now"); the worker then asks the page
for the picture through the canvas snapshot path (`UiChannel.request_canvas_snapshot`: a PNG of
at most 1 MiB on `lkap.ui.upload`, stored as a session asset of kind `signature`). Only then is
the answer settled, exactly once: the block gets `signed`, `asset_id`, `at` and `text_hash` (the
SHA-256 of the exact wording shown, `compliance.consent_text_hash`: the worker hashes the text
it computed, never block state), and a `signature` session event (`SignatureEvent`) is recorded,
as `consent` is for a consent. A decline is recorded too (no picture).

* **cascaded**: blocking; returns what was settled, or a "not signed" line after a timeout, a
  cancel or a barge-in (the caller speaking over the request withdraws it, R-V5-1).
* **realtime / half_cascade**: returns `None` at once and waits on the session's
  `BackgroundRunner`, like `request_consent`: a settled answer arrives as an *urgent* background
  result, a missed one as a routine note. `PlatformAgent` keeps the model silent after the call
  (`_REALTIME_SILENT_BUILTINS`), except on a phone or text session, where the tool answers at once.
* **phone channels** and **text chat**: nothing can be signed; the tool answers at once, shows
  nothing and records nothing.

The wording is the agent's (or the admin's) own text, shown as plain text; the only thing the
model hears back is the platform's own sentences.
"""

from __future__ import annotations

import json
import time
from typing import Any, Final

from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.blocks import SignatureBlockConfig
from lkap_contracts.compliance import MAX_CONSENT_TEXT_CHARS, consent_text_hash
from lkap_contracts.ui_protocol import SIGNATURE_EVENT, BlockSpec, SignatureEvent, UiPatchOp
from packs.base import PackSessionContext
from pydantic import ValidationError

from lkap_agent.tools.builtin.request_form import BACKGROUND_FORM_MODES
from lkap_agent.tools.untrusted import strip_control
from lkap_agent.ui.blocks import VOICE_ONLY_CHANNELS, describe_blocks, pick_block, session_block_specs

__all__ = [
    "NOT_SIGNED",
    "NO_PICTURE",
    "SIGNATURE_TIMEOUT_S",
    "SignatureOutcome",
    "build_request_signature_tool",
    "signature_block_config",
    "signature_message",
]

#: How long the caller has to sign (longer than a consent tap: signing takes a moment).
SIGNATURE_TIMEOUT_S: Final[float] = 180.0

#: What the model hears when nobody signed (timeout, cancel, barge-in).
NOT_SIGNED: Final[str] = (
    "The caller did not sign on screen (they spoke, closed it or it timed out). Ask whether they "
    "want to sign now; if they do, call request_signature again."
)
#: What the model hears when the caller tapped Sign but the picture never arrived.
NO_PICTURE: Final[str] = (
    "The caller tapped Sign, but their signature did not reach us. Nothing was recorded; ask them "
    "to sign again."
)

#: What was settled: the `signature` event's fields, or `{"signed": None}` without a picture.
SignatureOutcome = dict[str, Any]


def signature_block_config(spec: BlockSpec) -> SignatureBlockConfig:
    """The block's config (the defaults when it does not validate)."""
    try:
        return SignatureBlockConfig.model_validate(spec.config)
    except ValidationError:
        return SignatureBlockConfig()


def signature_message(outcome: SignatureOutcome) -> str:
    """What the model is told once a signature request settled."""
    match outcome.get("signed"):
        case True:
            return "The caller signed on screen; the signature is saved with the call. Continue."
        case False:
            return "The caller chose not to sign. Respect it and carry on."
        case _:
            return NO_PICTURE


def _wording(config: SignatureBlockConfig, disclosure_text: str) -> str:
    """The wording shown: the block's own when it sets one, else the model's (cleaned)."""
    fixed = config.disclosure_text.strip()
    if fixed:
        return fixed
    text = strip_control(disclosure_text).strip()
    if not text:
        raise ToolError("Pass the wording the caller signs, in disclosure_text.")
    if len(text) > MAX_CONSENT_TEXT_CHARS:
        raise ToolError(
            f"The wording is {len(text)} characters; keep it to {MAX_CONSENT_TEXT_CHARS} at most."
        )
    return text


def build_request_signature_tool(ctx: PackSessionContext) -> FunctionTool[..., Any]:
    """Build the `request_signature` tool bound to `ctx`."""
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["signature"])

    async def settle(target: str, text: str, signed: bool, asset_id: str | None) -> SignatureOutcome:
        """Write the answer to the block and record the `signature` event (exactly once)."""
        digest = consent_text_hash(text)
        now = time.time()
        try:
            await ctx.ui.patch_block(
                target,
                [
                    UiPatchOp(op="set", path="/signed", value=signed),
                    UiPatchOp(op="set", path="/asset_id", value=asset_id),
                    UiPatchOp(op="set", path="/at", value=now),
                    UiPatchOp(op="set", path="/text_hash", value=digest),
                ],
            )
        except Exception:  # noqa: BLE001 - the answer is recorded on the event either way
            ctx.log.warning("signature block could not be updated", block_id=target, exc_info=True)
        event = SignatureEvent(block_id=target, signed=signed, text_hash=digest, asset_id=asset_id)
        ctx.record_event(SIGNATURE_EVENT, event.model_dump())
        ctx.log.debug("builtin_tool.signature_settled", block_id=target, signed=signed, asset_id=asset_id)
        return event.model_dump()

    async def reset(target: str) -> None:
        """Put the block back to `cancelled` (nothing settled; the page offers nothing to press)."""
        try:
            await ctx.ui.patch_block(target, [UiPatchOp(op="set", path="/status", value="cancelled")])
        except Exception:  # noqa: BLE001 - the model is told either way
            ctx.log.debug("signature block could not be reset", block_id=target, exc_info=True)

    async def wait_for_signature(target: str, text: str, allow_decline: bool) -> SignatureOutcome | None:
        """Wait for Sign or Not now; fetch the picture of a signature; `None` when nobody answered.

        V6-29 (S6-29): with the block's `allow_decline` off, `signed: false` is a bad answer
        (the page hides Not now; only a stale or forged page sends it): nothing is recorded,
        the block goes back to `cancelled` and the model hears :data:`NOT_SIGNED`.
        """
        values = await ctx.ui.request_block(target, timeout_s=SIGNATURE_TIMEOUT_S)
        if values is None:
            return None
        signed = values.get("signed")
        if not isinstance(signed, bool) or (signed is False and not allow_decline):
            ctx.log.warning("builtin_tool.request_signature.bad_answer", block_id=target)
            if isinstance(signed, bool):
                await reset(target)
            return None
        if not signed:
            return await settle(target, text, False, None)
        request = getattr(ctx.ui, "request_canvas_snapshot", None)
        picture = await request(target) if callable(request) else None
        if picture is None:
            ctx.log.debug("builtin_tool.request_signature.no_picture", block_id=target)
            await reset(target)
            return {"signed": None}
        asset_id, _data = picture
        return await settle(target, text, True, asset_id)

    async def request_signature(
        context: RunContext[Any], disclosure_text: str = "", block_id: str = ""
    ) -> str | None:
        """Ask the caller to sign on their screen, under the wording they agree to, and wait.

        Args:
            disclosure_text: What the caller signs, in plain words (the block's own wording wins).
            block_id: The signature block to use; leave empty when there is only one.
        """
        specs = session_block_specs(ctx.ui, ctx.config.panel)
        try:
            target = pick_block(specs, "signature", block_id or None)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        spec = next(s for s in specs if s.id == target)
        config = signature_block_config(spec)
        text = _wording(config, disclosure_text)
        call_id = context.function_call.call_id
        channel = getattr(ctx, "channel", "web")
        if channel in VOICE_ONLY_CHANNELS or channel == "text":
            ctx.log.debug("builtin_tool.request_signature", call_id=call_id, block_id=target, channel=channel)
            return json.dumps(
                {
                    "channel": "voice_only" if channel in VOICE_ONLY_CHANNELS else "text",
                    "signed": False,
                    "next": "Nothing can be signed here: a signature needs the web page. Tell the "
                    "caller how else they can sign, or carry on without it.",
                }
            )
        try:
            await ctx.ui.patch_block(
                target,
                [
                    UiPatchOp(op="set", path="/disclosure_text", value=text),
                    UiPatchOp(op="set", path="/signed", value=None),
                    UiPatchOp(op="set", path="/asset_id", value=None),
                    UiPatchOp(op="set", path="/at", value=None),
                    UiPatchOp(op="set", path="/text_hash", value=None),
                ],
            )
        except ValidationError as exc:
            raise ToolError(f"The signature could not be asked for: {exc.errors()[0]['msg']}.") from exc
        background = ctx.pipeline_mode in BACKGROUND_FORM_MODES
        ctx.log.debug(
            "builtin_tool.request_signature",
            call_id=call_id,
            block_id=target,
            chars=len(text),
            background=background,
        )
        if background:
            ctx.background.submit(
                name="request_signature",
                coro=wait_for_signature(target, text, config.allow_decline),
                urgent=lambda outcome: outcome is not None,
                urgent_instructions=lambda outcome: f"{signature_message(outcome)} Acknowledge it briefly.",
                routine_note=lambda outcome: (
                    None if outcome is not None else f"The caller did not sign the {target} block on screen."
                ),
                call_id=call_id,
            )
            return None
        outcome = await wait_for_signature(target, text, config.allow_decline)
        if outcome is None:
            return NOT_SIGNED
        return signature_message(outcome)

    return function_tool(
        request_signature,
        description=(
            "Ask the caller to sign by hand on their screen, under the wording they agree to (for "
            "example, accepting an estimate), and wait for Sign or Not now. Read the wording aloud "
            "too. The signature is saved with the call; nothing can be signed on a phone call. "
            f"Signature blocks: {inventory}."
        ),
    )
