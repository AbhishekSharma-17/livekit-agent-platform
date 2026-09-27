"""V5-40: `session_memory`, `memory_forget` and `memory_purge` against the in-process api."""

from __future__ import annotations

import datetime as dt
from typing import Any

from conftest import BUILDER_SCOPES
from lkap_api.db.models import MemoryEvent, SessionEvent
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database

SUBJECT = "e" * 64


async def _remembered_session(database: Database, agent_id: str) -> str:
    async with database.session() as db:
        row = SessionRow(
            agent_id=agent_id,
            config_version=1,
            room_name="room-memory",
            participant_identity="customer-42",
            participant_name="Customer",
            status="ended",
            pipeline_mode="cascaded",
            channel="web",
        )
        db.add(row)
        await db.flush()
        db.add(
            SessionEvent(
                session_id=row.id,
                ts=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
                type="memory_recalled",
                payload={
                    "status": "recalled",
                    "count": 1,
                    "memories": ["Prefers mornings."],
                    "forgotten": False,
                },
            )
        )
        db.add(
            MemoryEvent(subject_id=SUBJECT, session_id=row.id, agent_id=agent_id, kind="recalled", count=1)
        )
        return row.id


async def test_session_memory_wraps_memories_as_untrusted(
    key: Any, mcp_session: Any, database: Database
) -> None:
    raw = await key(BUILDER_SCOPES)
    async with mcp_session(raw) as mcp:
        agent = (await mcp.call("agent_create", name="Memory"))["data"]["agent"]
        session_id = await _remembered_session(database, agent["id"])
        result = await mcp.call("session_memory", session_id=session_id)

    assert result["ok"] is True, result
    data = result["data"]
    assert data["subject_id"] == SUBJECT and data["recall_status"] == "recalled"
    [recalled] = data["recalled"]
    assert recalled["untrusted"] is True
    assert (recalled["content"], recalled["source"]) == ("Prefers mornings.", f"memory:{session_id}")


async def test_memory_forget_and_purge_need_confirmation(key: Any, mcp_session: Any) -> None:
    raw = await key(["*"])
    async with mcp_session(raw) as mcp:
        forget = await mcp.call("memory_forget", subject_id=SUBJECT)
        purge = await mcp.call("memory_purge")
        sent = mcp.transport.calls("DELETE", f"/v1/memory/subjects/{SUBJECT}") + mcp.transport.calls(
            "POST", "/v1/memory/purge"
        )

    assert forget["ok"] is False and forget["error"]["code"] == "needs_confirmation"
    assert purge["ok"] is False and purge["error"]["code"] == "needs_confirmation"
    assert sent == []


async def test_memory_purge_with_nothing_stored(key: Any, mcp_session: Any) -> None:
    raw = await key(["*"])
    async with mcp_session(raw) as mcp:
        result = await mcp.call("memory_purge", confirm=True)

    assert result["ok"] is True, result
    assert result["data"] == {"status": "nothing_to_purge", "subjects": 0, "job_id": None}


async def test_memory_forget_of_an_unknown_caller_is_not_found(key: Any, mcp_session: Any) -> None:
    raw = await key(["*"])
    async with mcp_session(raw) as mcp:
        result = await mcp.call("memory_forget", subject_id=SUBJECT, confirm=True)

    assert result["ok"] is False and result["error"]["code"] == "not_found"
