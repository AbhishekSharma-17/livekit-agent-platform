"""V5-37: `session_whisper` (plan-capable, needs confirm, `sessions:write`)."""

from __future__ import annotations

from typing import Any

from conftest import BUILDER_SCOPES, READ_ONLY_SCOPES
from lkap_api.db.session import Database


async def _ended_session(mcp: Any, database: Database) -> str:
    from lkap_api.db.models import Session as SessionRow

    agent = (await mcp.call("agent_create", name="Whispers"))["data"]["agent"]
    async with database.session() as session:
        row = SessionRow(
            agent_id=agent["id"],
            config_version=1,
            room_name="room-whisper",
            participant_identity="c",
            participant_name="C",
            status="ended",
            pipeline_mode="cascaded",
            channel="web",
        )
        session.add(row)
        await session.flush()
        return row.id


def _whisper_calls(mcp: Any) -> list[Any]:
    return [r for r in mcp.transport.requests if r.url.path.endswith("/whisper")]


async def test_session_whisper_needs_confirm_and_sends_nothing_without_it(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)
    async with mcp_session(raw) as mcp:
        result = await mcp.call("session_whisper", session_id="s1", text="Offer the premium plan.")
        sent = _whisper_calls(mcp)

    assert result["ok"] is False
    assert result["error"]["code"] == "needs_confirmation"
    assert sent == []


async def test_session_whisper_plan_shows_the_request(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)
    async with mcp_session(raw) as mcp:
        result = await mcp.call(
            "session_whisper", session_id="s1", text="Offer it.", reply_now=True, plan=True
        )
        sent = _whisper_calls(mcp)

    assert result["ok"] is True
    [step] = result["plan"]
    assert (step["method"], step["path"]) == ("POST", "/v1/sessions/s1/whisper")
    assert step["body"] == {"text": "Offer it.", "reply_now": True}
    assert sent == []


async def test_session_whisper_on_an_ended_session_is_a_409_with_a_hint(
    key: Any, mcp_session: Any, database: Database
) -> None:
    raw = await key(BUILDER_SCOPES)
    async with mcp_session(raw) as mcp:
        session_id = await _ended_session(mcp, database)
        result = await mcp.call("session_whisper", session_id=session_id, text="hello", confirm=True)

    assert result["ok"] is False
    assert result["error"]["code"] == "not_live"
    assert "active session" in result["error"]["hint"]


async def test_session_whisper_is_absent_for_a_read_only_key(key: Any, mcp_session: Any) -> None:
    raw = await key(READ_ONLY_SCOPES)
    async with mcp_session(raw) as mcp:
        names = await mcp.tool_names()

    assert "session_whisper" not in names
