"""Console session history: list filters, detail and the event timeline."""

from __future__ import annotations

import httpx
from conftest import create_agent

from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database


async def _session(admin_client: httpx.AsyncClient, agent_id: str) -> str:
    response = await admin_client.post(f"/v1/agents/{agent_id}/connect", json={})
    return str(response.json()["sessionId"])


async def test_list_returns_sessions_with_their_agent_name(
    admin_client: httpx.AsyncClient,
) -> None:
    agent = await create_agent(admin_client, name="Claims desk")
    await _session(admin_client, str(agent["id"]))

    body = (await admin_client.get("/v1/sessions")).json()

    assert body["total"] == 1
    assert body["items"][0]["created_at"].endswith("Z")
    assert body["items"][0]["agent_name"] == "Claims desk"
    assert body["items"][0]["status"] == "created"
    assert body["items"][0]["pipeline_mode"] == "cascaded"


async def test_list_filters_by_agent_and_status(admin_client: httpx.AsyncClient) -> None:
    first = await create_agent(admin_client, name="One")
    second = await create_agent(admin_client, name="Two")
    await _session(admin_client, str(first["id"]))
    await _session(admin_client, str(second["id"]))

    by_agent = (await admin_client.get("/v1/sessions", params={"agent_id": first["id"]})).json()
    by_status = (await admin_client.get("/v1/sessions", params={"status": "ended"})).json()

    assert by_agent["total"] == 1
    assert by_status["total"] == 0


async def test_list_paginates(admin_client: httpx.AsyncClient) -> None:
    agent = await create_agent(admin_client)
    for _ in range(3):
        await _session(admin_client, str(agent["id"]))

    page = (await admin_client.get("/v1/sessions", params={"limit": 2, "offset": 1})).json()

    assert page["total"] == 3
    assert len(page["items"]) == 2


async def test_detail_includes_transcript_and_final_state(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    agent = await create_agent(admin_client)
    session_id = await _session(admin_client, str(agent["id"]))
    await service_client.put(
        f"/internal/v1/sessions/{session_id}/summary",
        json={
            "status": "ended",
            "usage": {"llm_completion_tokens": 42},
            "transcript": [
                {"role": "user", "text": "hello", "ts": 1758000000.0},
                {"role": "assistant", "text": "hi", "ts": 1758000001.0, "interrupted": True},
            ],
            "final_ui_state": {"v": 1, "progress": 100, "notes": []},
        },
    )

    body = (await admin_client.get(f"/v1/sessions/{session_id}")).json()

    assert body["status"] == "ended"
    assert body["usage"] == {"llm_completion_tokens": 42}
    assert [turn["role"] for turn in body["transcript"]] == ["user", "assistant"]
    assert body["transcript"][1]["interrupted"] is True
    assert body["final_ui_state"]["progress"] == 100


async def test_detail_of_a_fresh_session_has_no_transcript(
    admin_client: httpx.AsyncClient,
) -> None:
    agent = await create_agent(admin_client)
    session_id = await _session(admin_client, str(agent["id"]))

    body = (await admin_client.get(f"/v1/sessions/{session_id}")).json()

    assert body["transcript"] is None
    assert body["final_ui_state"] is None
    assert body["ended_at"] is None


async def test_events_can_be_polled_with_after_id(
    admin_client: httpx.AsyncClient, service_client: httpx.AsyncClient
) -> None:
    agent = await create_agent(admin_client)
    session_id = await _session(admin_client, str(agent["id"]))
    await service_client.post(
        f"/internal/v1/sessions/{session_id}/events",
        json={
            "events": [
                {"ts": 1758000000.0, "type": "session_started", "payload": {}},
                {"ts": 1758000001.0, "type": "agent_state", "payload": {"state": "listening"}},
            ]
        },
    )

    everything = (await admin_client.get(f"/v1/sessions/{session_id}/events")).json()
    tail = (
        await admin_client.get(
            f"/v1/sessions/{session_id}/events",
            params={"after_id": everything["items"][0]["id"]},
        )
    ).json()

    assert [e["type"] for e in everything["items"]] == ["session_started", "agent_state"]
    assert everything["items"][0]["ts"] == "2025-09-16T05:20:00Z"
    assert [e["type"] for e in tail["items"]] == ["agent_state"]
    assert tail["items"][0]["payload"] == {"state": "listening"}


async def test_unknown_session_is_404(admin_client: httpx.AsyncClient) -> None:
    assert (await admin_client.get("/v1/sessions/ghost")).status_code == 404
    assert (await admin_client.get("/v1/sessions/ghost/events")).status_code == 404


async def test_sessions_require_an_admin_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/sessions")).status_code == 401


async def test_room_names_are_unique_per_session(admin_client: httpx.AsyncClient, database: Database) -> None:
    agent = await create_agent(admin_client)
    await _session(admin_client, str(agent["id"]))
    await _session(admin_client, str(agent["id"]))

    from sqlalchemy import select

    async with database.session() as session:
        rooms = list((await session.execute(select(SessionRow.room_name))).scalars())
    assert len(set(rooms)) == 2
