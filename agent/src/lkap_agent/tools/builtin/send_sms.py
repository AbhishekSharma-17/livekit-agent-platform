"""`send_sms` built-in tool (V5-25): a text message to the caller or to a saved contact.

Registered only when ``AgentConfig.tools.sms`` names an ``sms`` provider
(``twilio-sms`` or ``telnyx-sms``, with its sending number in ``from_number``);
the api resolves the key into ``ResolvedAgentConfig.builtin_providers["sms"]``.

**Destination policy.** The model never types a number. On a phone call (a SIP
session) the default destination is the caller's own number (the SIP
participant's ``sip.phoneNumber``); any other destination is named by the label
of an entry in ``AgentConfig.telephony.sms_targets``. On web and text sessions
only those saved contacts can be texted. A raw number is refused.

**Twice.** The same message to the same number is sent once per session unless
the model passes ``confirm_repeat`` after asking the caller. As a write it runs
``background`` by default (fire-and-forget), is not cancellable, and a second
call while one is running asks first. Each message sent records an
``sms_sent`` session event (the destination's label and last four digits, the
vendor's message id; never the text or the full number).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Final

import httpx
from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.agent_config import ResolvedProvider
from lkap_contracts.telephony import E164_PATTERN, SmsTarget
from packs.base import PackSessionContext

from lkap_agent.telephony import ATTR_PHONE_NUMBER, SIP_CHANNELS, sip_participant
from lkap_agent.tools.execution import (
    ResolvedExecution,
    ToolPolicy,
    attach_policy,
    blocking_policy,
    run_with_policy,
    tool_flags,
)
from lkap_agent.tools.vendors import SmsReceipt, VendorError, telnyx, twilio

__all__ = ["MAX_MESSAGE_CHARS", "build_send_sms_tool", "caller_number", "resolve_destination"]

#: Three SMS segments of plain text.
MAX_MESSAGE_CHARS: Final[int] = 480
#: Words the model may use for "the caller".
CALLER_WORDS: Final[frozenset[str]] = frozenset({"caller", "the caller", "me", "my phone", "customer"})
#: `userdata` key of the (number, message) digests already sent in this session.
_SENT_KEY: Final[str] = "lkap.send_sms.sent"

_E164_RE = re.compile(E164_PATTERN)

NOT_CONFIGURED: Final[str] = (
    "Text messages are not set up for this agent (its SMS service, key or sending number is missing). "
    "Offer to read the details out instead."
)


def caller_number(ctx: PackSessionContext) -> str | None:
    """The caller's phone number on a SIP session (E.164), else ``None``."""
    room = getattr(ctx, "room", None)
    if room is None:
        return None
    participant = sip_participant(room)
    if participant is None:
        return None
    number = str(participant.attributes.get(ATTR_PHONE_NUMBER) or "").strip()
    return number if _E164_RE.match(number) else None


def resolve_destination(ctx: PackSessionContext, to: str | None, targets: list[SmsTarget]) -> tuple[str, str]:
    """``(number, what to call it)`` for ``to`` under the destination policy.

    Raises:
        ToolError: ``to`` is not the caller (on a phone call) or a saved contact's label.
    """
    labels = ", ".join(t.label for t in targets)
    saved = f" Saved contacts: {labels}." if labels else ""
    wanted = " ".join((to or "").split())
    if not wanted or wanted.casefold() in CALLER_WORDS:
        if str(getattr(ctx, "channel", "web")) not in SIP_CHANNELS:
            raise ToolError(
                "Texting the caller works only on a phone call." + (saved or " There is no one else to text.")
            )
        number = caller_number(ctx)
        if number is None:
            raise ToolError("The caller's phone number is not available, so no text can be sent.")
        return number, "the caller"
    for target in targets:
        if target.label.casefold() == wanted.casefold():
            return target.to, target.label
    raise ToolError(
        "Texts go only to the caller on a phone call or to a saved contact by name. Numbers cannot be "
        "typed in." + saved
    )


async def _send(
    provider: ResolvedProvider, *, to: str, body: str, transport: httpx.AsyncBaseTransport | None
) -> SmsReceipt:
    kwargs = provider.kwargs
    from_number = str(kwargs.get("from_number") or "").strip()
    if not _E164_RE.match(from_number):
        raise ToolError(NOT_CONFIGURED)
    match provider.provider_id:
        case "twilio-sms":
            return await twilio.send(
                to=to,
                body=body,
                from_number=from_number,
                account_sid=str(kwargs.get("account_sid") or ""),
                auth_token=str(kwargs.get("auth_token") or ""),
                transport=transport,
            )
        case "telnyx-sms":
            profile = kwargs.get("messaging_profile_id")
            return await telnyx.send(
                to=to,
                body=body,
                from_number=from_number,
                api_key=str(kwargs.get("api_key") or ""),
                messaging_profile_id=str(profile) if profile else None,
                transport=transport,
            )
        case _:
            raise ToolError(NOT_CONFIGURED)


def _description(targets: list[SmsTarget]) -> str:
    text = (
        "Send a short text message to the caller's phone (on a phone call) or to a saved contact by name, "
        "e.g. a link or reference number they asked for."
    )
    if targets:
        text += " Saved contacts: " + ", ".join(t.label for t in targets) + "."
    return text


def build_send_sms_tool(
    ctx: PackSessionContext,
    provider: ResolvedProvider | None,
    *,
    execution: ResolvedExecution | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FunctionTool[..., Any]:
    """Build the `send_sms` tool bound to `ctx`.

    Args:
        ctx: The session's context (``config.telephony.sms_targets``, ``channel``, ``room``).
        provider: ``builtin_providers["sms"]``; ``None`` makes every call answer :data:`NOT_CONFIGURED`.
        execution: The tool's policy; ``None`` runs it blocking.
        transport: A test seam for the vendor call.
    """
    policy = execution or blocking_policy("send_sms")
    flags, on_duplicate, duplicate_scope = tool_flags(policy)
    telephony = getattr(ctx.config, "telephony", None)
    targets: list[SmsTarget] = list(getattr(telephony, "sms_targets", None) or [])

    @function_tool(
        flags=flags,
        on_duplicate=on_duplicate,
        duplicate_scope=duplicate_scope,
        description=_description(targets),
    )
    async def send_sms(
        context: RunContext[Any], message: str, to: str | None = None, confirm_repeat: bool = False
    ) -> str:
        """Send a short text message.

        Args:
            message: The text, under 480 characters. Never put card numbers or passwords in it.
            to: A saved contact's name, or leave empty for the caller on a phone call.
            confirm_repeat: True only after the caller agreed to receive the same message again.
        """
        result: str = await run_with_policy(
            context, policy, lambda: _run(context, message, to, confirm_repeat)
        )
        return result

    async def _run(context: RunContext[Any], message: str, to: str | None, confirm_repeat: bool) -> str:
        if provider is None:
            raise ToolError(NOT_CONFIGURED)
        body = message.strip()
        if not body:
            raise ToolError("Say what the text message should contain.")
        if len(body) > MAX_MESSAGE_CHARS:
            raise ToolError(f"The message is too long: keep it under {MAX_MESSAGE_CHARS} characters.")
        number, name = resolve_destination(ctx, to, targets)
        digest = hashlib.sha256(f"{number}\n{body}".encode()).hexdigest()
        sent: set[str] = ctx.userdata.setdefault(_SENT_KEY, set())
        if digest in sent and not confirm_repeat:
            return (
                f"This exact message was already sent to {name}. Ask the caller whether to send it again; "
                "only if they say yes, call send_sms again with confirm_repeat set to true."
            )
        try:
            receipt = await _send(provider, to=number, body=body, transport=transport)
        except VendorError as exc:
            raise ToolError(f"The text message was not sent: {exc}.") from exc
        sent.add(digest)
        try:
            ctx.record_event(
                "sms_sent",
                {
                    "to": name,
                    "to_last4": number[-4:],
                    "provider": provider.provider_id,
                    "message_id": receipt.message_id,
                    "status": receipt.status,
                    "chars": len(body),
                },
            )
        except Exception:
            ctx.log.debug("could not record the sms_sent event", exc_info=True)
        ctx.log.debug(
            "builtin_tool.send_sms",
            call_id=context.function_call.call_id,
            provider=provider.provider_id,
            status=receipt.status,
        )
        return f"Text message sent to {name}. Do not read the number aloud."

    return attach_policy(send_sms, ToolPolicy(resolved=policy))
