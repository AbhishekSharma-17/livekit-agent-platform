"""``telephony_overview`` passes LiveKit-hosted numbers through (V4-05, D-V4-21)."""

from __future__ import annotations

from typing import Any

from conftest import OPERATOR_SCOPES
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import LiveKitConnection, PhoneNumber, new_id
from lkap_api.db.session import Database
from sqlalchemy import select

from lkap_mcp.tools.telephony import hosted_number_warnings


def test_hosted_number_warnings_name_every_number_that_is_not_routed() -> None:
    numbers = [
        {"e164": "+15550100001", "attach_state": "routed"},
        {"e164": "+15550100002", "attach_state": "detached"},
        {"e164": "+15550100003", "attach_state": "offline"},
    ]

    assert hosted_number_warnings(numbers) == [
        "number +15550100002: detached",
        "number +15550100003: offline",
    ]


async def test_telephony_overview_counts_hosted_numbers_and_warns(
    key: Any, mcp_session: Any, database: Database
) -> None:
    async with database.session() as session:
        connection_id = await session.scalar(
            select(LiveKitConnection.id).where(LiveKitConnection.workspace_id == DEFAULT_WORKSPACE_ID)
        )
        session.add_all(
            [
                PhoneNumber(
                    id=new_id(),
                    workspace_id=DEFAULT_WORKSPACE_ID,
                    e164="+15550100002",
                    source="livekit",
                    connection_id=connection_id,
                    lk_number_id="PN_2",
                    lk_status="offline",
                    lk_rule_ids=[],
                    region="San Francisco, CA",
                ),
                PhoneNumber(
                    id=new_id(),
                    workspace_id=DEFAULT_WORKSPACE_ID,
                    e164="+15550100009",
                    lk_rule_ids=[],
                    region="",
                ),
            ]
        )
    raw = await key(OPERATOR_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call("telephony_overview")

    assert result["ok"] is True, result
    data = result["data"]
    assert data["livekit_numbers"] == 1
    hosted = next(n for n in data["numbers"] if n["source"] == "livekit")
    assert (hosted["attach_state"], hosted["lk_number_id"], hosted["region"]) == (
        "offline",
        "PN_2",
        "San Francisco, CA",
    )
    assert "number +15550100002: offline" in result["warnings"]
    assert not any("+15550100009" in warning for warning in result["warnings"])
