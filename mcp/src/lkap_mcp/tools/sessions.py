"""Session, transcript and QA tools (``AGENT-ACCESS.md`` §4.8).

Transcript turns and event payloads came from callers and models, so they are
returned inside the ``Untrusted`` envelope (R-V3-12). The time filters are
``since``/``until`` (the api's ``from``/``to``; ``from`` is not a valid
parameter name).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from lkap_contracts.common import SessionChannel
from pydantic import Field

from lkap_mcp.client import ApiFailure
from lkap_mcp.registry import DESTRUCTIVE, READ, Registry
from lkap_mcp.results import ToolResult, untrusted
from lkap_mcp.tools._common import seg

SessionStatus = Literal["created", "active", "ended", "failed"]


def untrusted_transcript(session: dict[str, Any]) -> dict[str, Any]:
    """Wrap every transcript turn's text as untrusted."""
    turns = session.get("transcript")
    if isinstance(turns, list):
        session["transcript"] = [
            {**turn, "text": untrusted(turn.get("text", ""), f"session:{session.get('id')}")}
            for turn in turns
            if isinstance(turn, dict)
        ]
    return session


def untrusted_event(event: dict[str, Any], session_id: str) -> dict[str, Any]:
    """Wrap an event's payload as untrusted (JSON text)."""
    payload = event.get("payload")
    return {**event, "payload": untrusted(json.dumps(payload, default=str), f"events:{session_id}")}


def register(registry: Registry) -> None:
    """Declare the session tools."""
    client = registry.ctx.client

    @registry.tool(scopes={"sessions:read"}, annotations=READ, data="SessionOut[]")
    async def session_list(
        agent_id: str | None = None,
        status: SessionStatus | None = None,
        channel: SessionChannel | None = None,
        connection_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 25,
    ) -> ToolResult:
        """List sessions (newest first) with status, channel, cost and disposition."""
        params: dict[str, Any] = {
            "agent_id": agent_id,
            "status": status,
            "channel": channel,
            "connection_id": connection_id,
            "from": since.isoformat() if since else None,
            "to": until.isoformat() if until else None,
            "limit": limit,
        }
        return ToolResult.success(await client.items("/v1/sessions", params=params))

    @registry.tool(scopes={"sessions:read"}, annotations=READ, data="SessionDetailOut")
    async def session_get(
        session_id: str, include_transcript: bool = True, include_recording_url: bool = False
    ) -> ToolResult:
        """One session: transcript (untrusted), QA, costs, latency, disposition, variables, caller zone."""
        session = await client.get(f"/v1/sessions/{seg(session_id)}")
        if not session.get("caller_timezone"):
            # R-V5-10: an api before the field keeps the zone only in the summary's usage.
            usage = session.get("usage") if isinstance(session.get("usage"), dict) else {}
            session["caller_timezone"] = usage.get("caller_timezone") if usage else None
        if not include_transcript:
            session.pop("transcript", None)
        else:
            session = untrusted_transcript(session)
        warnings: list[str] = []
        if include_recording_url:
            recording = session.get("recording") or {}
            if recording.get("url"):
                session["recording_url"] = recording["url"]
            else:
                warnings.append(f"no playable recording (status {recording.get('status', 'none')})")
        else:
            (session.get("recording") or {}).pop("url", None)
        return ToolResult.success(session, warnings=warnings)

    @registry.tool(scopes={"sessions:read"}, annotations=READ, data="SessionEventOut[]")
    async def session_events(
        session_id: str,
        after_id: int | None = None,
        types: list[str] | None = None,
        limit: Annotated[int, Field(ge=1, le=1000)] = 200,
    ) -> ToolResult:
        """A session's events (tool calls, block updates …); payloads are untrusted and best-effort."""
        events = await client.items(
            f"/v1/sessions/{seg(session_id)}/events", params={"after_id": after_id, "limit": limit}
        )
        rows = [
            untrusted_event(event, session_id) for event in events if not types or event.get("type") in types
        ]
        return ToolResult.success(rows)

    @registry.tool(scopes={"sessions:write"}, annotations=DESTRUCTIVE, data="QaOut")
    async def session_rescore(session_id: str, confirm: bool = False) -> ToolResult:
        """Re-run QA scoring on a session (replaces its QA result; needs confirm and a vendor-key judge)."""
        if not confirm:
            return ToolResult.needs_confirmation(f"replace the QA score of session {session_id}")
        try:
            return ToolResult.success(await client.post(f"/v1/sessions/{seg(session_id)}/qa"))
        except ApiFailure as failure:
            if failure.status == 409:
                result = failure.to_result()
                if result.error is not None:
                    result.error.hint = (
                        "re-scoring needs a QA judge with a vendor key (R-V2-5); configure the agent's "
                        "qa.judge provider and key"
                    )
                return result
            raise
