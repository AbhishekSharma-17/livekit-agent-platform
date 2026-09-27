"""`send_link` built-in tool (V5-43, B11): hand the caller a checkout, e-sign or portal link.

Payments never happen in LKAP (D-V5-6): the agent only shows a link to the
business's own, already-trusted flow and later learns how it ended. The link
must be ``https://`` on one of the ``link`` block's ``allowed_hosts``
(:func:`lkap_contracts.ui_protocol.https_url_problem`, checked here as well as
by the block's state model), so a link the model made up or a caller dictated
to it is refused with a plain reason.

* **web**: the block shows the link (``status: "pending"``) and is brought into
  view; the caller's tap marks it ``opened``. The outcome arrives from the api's
  link hook (``POST /v1/hooks/link/{session_id}``, signed) as a
  ``link_completed`` packet, and ``PlatformAgent`` tells the model.
* **phone** (``sip_in``/``sip_out``): nothing can be shown; with ``tools.sms``
  configured the link is texted to the caller (the ``send_sms`` vendors, the
  caller's own number) and the block records ``channel: "sms"`` so the
  outcome still reaches the model; without it the tool answers
  ``{"channel": "voice_only"}``.
* **text chat**: there is no panel; the tool answers with the link for the
  model to put in its reply.

Never run in the background: the model's next sentence depends on it.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Final, Literal

import httpx
from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.agent_config import ResolvedProvider
from lkap_contracts.blocks import LinkBlockConfig
from lkap_contracts.ui_protocol import (
    LINK_REFERENCE_PATTERN,
    MAX_LINK_LABEL_CHARS,
    LinkBlockState,
    https_url_problem,
)
from packs.base import PackSessionContext
from pydantic import ValidationError

from lkap_agent.tools.builtin.send_sms import _send, resolve_destination
from lkap_agent.tools.vendors import VendorError
from lkap_agent.ui.blocks import VOICE_ONLY_CHANNELS, describe_blocks, pick_block, session_block_specs

__all__ = ["MAX_EXPIRY_MINUTES", "build_send_link_tool"]

#: The longest an expiry may be set to.
MAX_EXPIRY_MINUTES: Final[int] = 7 * 24 * 60
_REFERENCE_RE: Final[re.Pattern[str]] = re.compile(LINK_REFERENCE_PATTERN)

LinkKindArg = Literal["checkout", "esign", "portal", "other"]


def _config(spec_config: dict[str, Any]) -> LinkBlockConfig:
    try:
        return LinkBlockConfig.model_validate(spec_config)
    except ValidationError as exc:
        raise ToolError("The link block is not set up: it lists no sites links may go to.") from exc


def build_send_link_tool(
    ctx: PackSessionContext,
    sms_provider: ResolvedProvider | None = None,
    *,
    sms_configured: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FunctionTool[..., Any]:
    """Build the `send_link` tool bound to `ctx`.

    Args:
        ctx: The session.
        sms_provider: The resolved ``sms`` vendor (``builtin_providers["sms"]``), if any.
        sms_configured: Whether the agent configures ``tools.sms`` (a phone call then
            gets the link by text message).
        transport: An httpx transport for tests.
    """
    inventory = describe_blocks(session_block_specs(ctx.ui, ctx.config.panel), ["link"])

    async def text_the_link(message: str) -> dict[str, Any]:
        if not sms_configured or sms_provider is None:
            return {"channel": "voice_only"}
        targets = list(getattr(getattr(ctx.config, "telephony", None), "sms_targets", None) or [])
        number, _ = resolve_destination(ctx, None, targets)
        try:
            receipt = await _send(sms_provider, to=number, body=message, transport=transport)
        except VendorError as exc:
            raise ToolError(
                "The text message with the link could not be sent. Offer to read the details out."
            ) from exc
        try:
            ctx.record_event(
                "sms_sent",
                {
                    "to": "the caller",
                    "to_last4": number[-4:],
                    "provider": sms_provider.provider_id,
                    "message_id": receipt.message_id,
                    "status": receipt.status,
                    "chars": len(message),
                    "purpose": "link",
                },
            )
        except Exception:
            ctx.log.debug("could not record the sms_sent event", exc_info=True)
        return {"channel": "sms", "sent": True}

    async def send_link(
        context: RunContext[Any],
        url: str,
        label: str,
        kind: LinkKindArg = "other",
        reference: str = "",
        expires_in_minutes: int = 0,
        block_id: str = "",
    ) -> str:
        """Give the caller a secure link to pay, sign or open their account, and track whether they finish.

        Args:
            url: The full https link, exactly as the payment or signing tool returned it.
            label: The button text, e.g. Pay the 250 GBP excess.
            kind: checkout, esign, portal or other.
            reference: The order or envelope id the link is for, if any.
            expires_in_minutes: When the link stops working, if known (0 = not known).
            block_id: The link block; leave empty when there is only one.
        """
        specs = session_block_specs(ctx.ui, ctx.config.panel)
        try:
            target = pick_block(specs, "link", block_id or None)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        spec_config = next((s.config for s in specs if s.id == target), {})
        config = _config(spec_config)
        link = url.strip()
        problem = https_url_problem(link, allowed_hosts=config.allowed_hosts)
        if problem is not None:
            raise ToolError(f"Cannot send that link: {problem}.")
        text = " ".join(label.split())
        if not text:
            raise ToolError("Give the link a short label, e.g. Pay the excess.")
        if len(text) > MAX_LINK_LABEL_CHARS:
            raise ToolError(f"Keep the label under {MAX_LINK_LABEL_CHARS} characters.")
        ref = reference.strip() or None
        if ref is not None and not _REFERENCE_RE.match(ref):
            raise ToolError("The reference may only use letters, digits and _ . : - (up to 128).")
        if not 0 <= expires_in_minutes <= MAX_EXPIRY_MINUTES:
            raise ToolError("expires_in_minutes must be between 0 and 10080 (a week).")
        now = time.time()
        channel = str(getattr(ctx, "channel", "web"))
        call_id = context.function_call.call_id

        if channel == "text":
            ctx.log.debug("builtin_tool.send_link", call_id=call_id, block_id=target, text_channel=True)
            return json.dumps(
                {"channel": "text", "note": "Put this link in your reply.", "url": link, "label": text}
            )

        sent_by: Literal["panel", "sms"] = "panel"
        if channel in VOICE_ONLY_CHANNELS:
            result = await text_the_link(f"{text}: {link}")
            if result["channel"] == "voice_only":
                ctx.log.debug("builtin_tool.send_link", call_id=call_id, block_id=target, voice_only=True)
                return json.dumps(result)
            sent_by = "sms"

        state = LinkBlockState(
            url=link,
            label=text,
            kind=kind,
            status="pending",
            reference=ref,
            channel=sent_by,
            sent_at=now,
            expires_at=now + expires_in_minutes * 60 if expires_in_minutes else None,
        )
        await ctx.ui.set_block(target, state.model_dump(mode="json"))
        show = getattr(ctx.ui, "show_block", None)
        if sent_by == "panel" and callable(show):
            show(target)
        ctx.log.debug(
            "builtin_tool.send_link",
            call_id=call_id,
            block_id=target,
            kind=kind,
            channel=sent_by,
            has_reference=bool(ref),
        )
        where = "texted to the caller's phone" if sent_by == "sms" else "on the caller's screen"
        return (
            f"The link is {where}. Tell the caller in one sentence what it is for; you will be told when "
            "the business's system reports it completed, failed or expired. Never read the link out."
        )

    return function_tool(
        send_link,
        description=(
            "Give the caller a secure https link (payment checkout, e-signature or account portal) on their "
            "screen, or by text message on a phone call, and track whether they finish. Payments happen on "
            f"that page, never in the call. Link blocks: {inventory}."
        ),
    )
