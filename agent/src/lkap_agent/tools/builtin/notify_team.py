"""`notify_team` built-in tool (V5-25): a short summary posted to the team's webhook.

Registered only when ``AgentConfig.tools.notify_team`` is set. The webhook URL
is a secret: the api resolves it from an ``http-tool-secret`` key into
``ResolvedAgentConfig.builtin_providers["notify_team"].kwargs["webhook_url"]``
and it is never logged. A Slack incoming webhook gets ``{"text": …}``; any
other webhook gets a JSON object with the fields. The message carries the
summary, the urgency and the session, agent and channel ids; the conversation
itself only when ``include_transcript`` is on (off by default). The post goes
through the vendor guard (``https``, public address, guarded transport, a
timeout). ``escalate_to_human`` posts through :func:`post_team_notification`
too when ``on_escalation`` is on. Runs ``background`` by default; a repeat of
the same summary in one session is not sent again.
"""

from __future__ import annotations

import time
from typing import Any, Final, Literal

import httpx
from livekit.agents import FunctionTool, RunContext, ToolError, function_tool
from lkap_contracts.agent_config import NotifyTeamConfig, ResolvedProvider
from packs.base import PackSessionContext

from lkap_agent.logging import get_logger
from lkap_agent.tools.execution import (
    ResolvedExecution,
    ToolPolicy,
    attach_policy,
    blocking_policy,
    run_with_policy,
    tool_flags,
)
from lkap_agent.tools.vendors import VendorError, vendor_call

__all__ = [
    "MAX_SUMMARY_CHARS",
    "NotifySource",
    "build_notify_team_tool",
    "build_payload",
    "post_team_notification",
    "recent_transcript",
]

_log = get_logger(__name__)

Urgency = Literal["low", "normal", "high"]
#: What made the agent post: the tool itself, or an escalation.
NotifySource = Literal["notify_team", "escalation"]

MAX_SUMMARY_CHARS: Final[int] = 500
MAX_TRANSCRIPT_MESSAGES: Final[int] = 12
MAX_TRANSCRIPT_CHARS: Final[int] = 2000
TIMEOUT_S: Final[float] = 5.0
VENDOR: Final[str] = "The team webhook"

#: `userdata` key of the summaries already posted in this session.
_SENT_KEY: Final[str] = "lkap.notify_team.sent"

NOT_CONFIGURED: Final[str] = (
    "Notifying the team is not set up for this agent (its webhook is missing). "
    "Tell the caller someone will follow up, without promising when."
)


def recent_transcript(ctx: PackSessionContext) -> list[dict[str, str]]:
    """The last few user and assistant messages of the session, as ``{role, text}``, capped in size."""
    history = getattr(getattr(ctx, "session", None), "history", None)
    items = getattr(history, "items", None) or []
    messages: list[dict[str, str]] = []
    for item in items:
        role = getattr(item, "role", None)
        if getattr(item, "type", "message") != "message" or role not in ("user", "assistant"):
            continue
        text = getattr(item, "text_content", None)
        if isinstance(text, str) and text.strip():
            messages.append({"role": str(role), "text": text.strip()})
    kept: list[dict[str, str]] = []
    used = 0
    for message in reversed(messages[-MAX_TRANSCRIPT_MESSAGES:]):
        used += len(message["text"])
        if used > MAX_TRANSCRIPT_CHARS:
            break
        kept.append(message)
    return list(reversed(kept))


def build_payload(
    ctx: PackSessionContext,
    settings: NotifyTeamConfig,
    *,
    summary: str,
    urgency: Urgency,
    source: NotifySource,
) -> dict[str, Any]:
    """The webhook body for ``settings.style`` (see the module docstring)."""
    channel = str(getattr(ctx, "channel", "web"))
    transcript = recent_transcript(ctx) if settings.include_transcript else None
    if settings.style == "slack":
        heading = "Escalation" if source == "escalation" else "Note from the agent"
        marker = " (urgent)" if urgency == "high" else ""
        lines = [
            f"*{heading}*{marker}: {summary}",
            f"Session {ctx.session_id} · agent {ctx.agent_id} · {channel} · urgency {urgency}",
        ]
        if transcript:
            lines.append("Recent conversation:")
            lines.extend(f"> {m['role']}: {m['text']}" for m in transcript)
        return {"text": "\n".join(lines)}
    body: dict[str, Any] = {
        "source": source,
        "summary": summary,
        "urgency": urgency,
        "session_id": ctx.session_id,
        "agent_id": ctx.agent_id,
        "channel": channel,
        "sent_at": time.time(),
    }
    if transcript is not None:
        body["transcript"] = transcript
    return body


async def post_team_notification(
    ctx: PackSessionContext,
    provider: ResolvedProvider | None,
    settings: NotifyTeamConfig,
    *,
    summary: str,
    urgency: Urgency = "normal",
    source: NotifySource = "notify_team",
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Post one notification to the team's webhook.

    Raises:
        VendorError: The webhook is not resolved, not a public ``https`` address, or the post failed.
    """
    webhook_url = str((provider.kwargs if provider is not None else {}).get("webhook_url") or "")
    if not webhook_url:
        raise VendorError(f"{VENDOR} is not set up")
    payload = build_payload(ctx, settings, summary=summary, urgency=urgency, source=source)
    await vendor_call(VENDOR, "POST", webhook_url, json=payload, timeout_s=TIMEOUT_S, transport=transport)
    try:
        ctx.record_event("team_notified", {"source": source, "urgency": urgency, "style": settings.style})
    except Exception:
        _log.debug("could not record the team_notified event", session_id=ctx.session_id, exc_info=True)


def build_notify_team_tool(
    ctx: PackSessionContext,
    provider: ResolvedProvider | None,
    settings: NotifyTeamConfig,
    *,
    execution: ResolvedExecution | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FunctionTool[..., Any]:
    """Build the `notify_team` tool bound to `ctx`.

    Args:
        ctx: The session's context.
        provider: ``builtin_providers["notify_team"]``; ``None`` makes every call answer
            :data:`NOT_CONFIGURED`.
        settings: ``tools.notify_team``.
        execution: The tool's policy; ``None`` runs it blocking.
        transport: A test seam for the post.
    """
    policy = execution or blocking_policy("notify_team")
    flags, on_duplicate, duplicate_scope = tool_flags(policy)

    @function_tool(flags=flags, on_duplicate=on_duplicate, duplicate_scope=duplicate_scope)
    async def notify_team(context: RunContext[Any], summary: str, urgency: Urgency = "normal") -> str:
        """Send your team (staff, not the caller) a short note when someone should know or act now.

        Args:
            summary: One or two sentences on what happened and what is needed; no card numbers or passwords.
            urgency: How quickly the team should look at it.
        """
        result: str = await run_with_policy(context, policy, lambda: _notify(context, summary, urgency))
        return result

    async def _notify(context: RunContext[Any], summary: str, urgency: Urgency) -> str:
        if provider is None:
            raise ToolError(NOT_CONFIGURED)
        text = " ".join(summary.split())
        if not text:
            raise ToolError("Say what the team should know.")
        text = text[:MAX_SUMMARY_CHARS]
        sent: set[str] = ctx.userdata.setdefault(_SENT_KEY, set())
        if text.casefold() in sent:
            return "The team already has this note; there is no need to send it again."
        try:
            await post_team_notification(
                ctx, provider, settings, summary=text, urgency=urgency, transport=transport
            )
        except VendorError as exc:
            raise ToolError(f"The team could not be notified: {exc}.") from exc
        sent.add(text.casefold())
        ctx.log.debug("builtin_tool.notify_team", call_id=context.function_call.call_id, urgency=urgency)
        return "The team has been notified."

    return attach_policy(notify_team, ToolPolicy(resolved=policy))
