"""Session, transcript, QA and caller-memory tools (``AGENT-ACCESS.md`` §4.8, V5-40).

Transcript turns and event payloads came from callers and models, so they are
returned inside the ``Untrusted`` envelope (R-V3-12). The time filters are
``since``/``until`` (the api's ``from``/``to``; ``from`` is not a valid
parameter name).

``session_whisper`` (V5-37) sends a live session's agent written guidance the
caller never hears (``POST /v1/sessions/{id}/whisper``); it needs ``confirm=true``
and takes ``plan=true``. It is declared with ``sessions:write`` (which implies the
api's ``sessions:listen``) because this server's scope check knows only the
``x:write`` ⇒ ``x:read`` rule.
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
from lkap_mcp.tools._common import planned, request, seg

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

    @registry.tool(scopes={"sessions:read"}, annotations=READ, data="SessionMemoryOut")
    async def session_memory(session_id: str) -> ToolResult:
        """What a session recalled and stored about its caller (memories untrusted), and the caller's
        pseudonymous id for memory_forget.
        """
        out = await client.get(f"/v1/sessions/{seg(session_id)}/memory")
        source = f"memory:{session_id}"
        for key in ("recalled", "stored"):
            out[key] = [untrusted(text, source) for text in out.get(key) or []]
        return ToolResult.success(out)

    @registry.tool(scopes={"sessions:write"}, annotations=DESTRUCTIVE, data="MemoryForgetOut")
    async def memory_forget(
        subject_id: Annotated[
            str, Field(description="The caller's pseudonymous id (session_memory's subject_id)")
        ],
        confirm: bool = False,
    ) -> ToolResult:
        """Forget one caller: deletes everything every agent remembers about them (needs confirm)."""
        if not confirm:
            return ToolResult.needs_confirmation(
                f"delete every memory of caller {subject_id} in this workspace (cannot be undone)"
            )
        return ToolResult.success(await client.delete(f"/v1/memory/subjects/{seg(subject_id)}"))

    @registry.tool(scopes={"sessions:write"}, annotations=DESTRUCTIVE, data="MemoryPurgeOut")
    async def memory_purge(confirm: bool = False) -> ToolResult:
        """Purge every caller memory of the workspace (needs confirm; cannot be undone)."""
        if not confirm:
            return ToolResult.needs_confirmation(
                "delete every caller memory of this workspace, for every agent (cannot be undone)"
            )
        return ToolResult.success(await client.post("/v1/memory/purge", json={"confirm": True}))

    @registry.tool(scopes={"sessions:listen"}, annotations=DESTRUCTIVE, data="SessionWhisperOut")
    async def session_whisper(
        session_id: str,
        text: Annotated[str, Field(min_length=1, max_length=1000)],
        reply_now: bool = False,
        confirm: bool = False,
        plan: bool = False,
    ) -> ToolResult:
        """Whisper guidance to a live session's agent (the caller never hears it; needs confirm).

        The agent treats it as a supervisor's note for its next reply; ``reply_now`` makes it
        speak at once. Only for an active session with an agent in the room.
        """
        path = f"/v1/sessions/{seg(session_id)}/whisper"
        body = {"text": text, "reply_now": reply_now}
        if plan:
            return planned(request("POST", path, body, note="the agent reads it; the caller never hears it"))
        if not confirm:
            return ToolResult.needs_confirmation(
                f"send guidance to the agent of live session {session_id}; it may change what the agent says"
            )
        try:
            return ToolResult.success(await client.post(path, body))
        except ApiFailure as failure:
            if failure.status == 409:
                result = failure.to_result()
                if result.error is not None:
                    result.error.hint = (
                        "whispers reach only an active session whose agent is in the room; check "
                        "session_get(session_id).status"
                    )
                return result
            raise
