"""R-V5-10: `session_get` shows the caller's timezone the session used."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import BUILDER_SCOPES
from lkap_api.db.session import Database


@pytest.mark.parametrize(
    ("usage", "expected"),
    [({"caller_timezone": "Asia/Kolkata"}, "Asia/Kolkata"), ({"turns": 2}, None), (None, None)],
    ids=["from-summary", "no-zone", "no-summary"],
)
async def test_session_get_shows_caller_timezone(
    key: Any, mcp_session: Any, database: Database, usage: dict[str, Any] | None, expected: str | None
) -> None:
    from lkap_api.db.models import Session as SessionRow

    raw = await key(BUILDER_SCOPES)
    async with mcp_session(raw) as mcp:
        agent = (await mcp.call("agent_create", name="Timezones"))["data"]["agent"]
        async with database.session() as session:
            row = SessionRow(
                agent_id=agent["id"],
                config_version=1,
                room_name="room-tz",
                participant_identity="c",
                participant_name="C",
                status="ended",
                pipeline_mode="cascaded",
                channel="text",
                usage=usage,
            )
            session.add(row)
            await session.flush()
            session_id = row.id
        detail = await mcp.call("session_get", session_id=session_id)

    assert detail["ok"] is True, detail
    assert detail["data"]["caller_timezone"] == expected
