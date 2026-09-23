"""`POST /v1/agents/{id}/text-sessions` and `GET /v1/agents/{id}/embed-policy` (V2-18).

`start_text_session` reuses `routers.connect`'s protection helpers directly, so
this file proves the reuse (one origin-403 test, one channel/row-shape test)
rather than re-running every case `test_connect_limits.py` already covers.
"""

from __future__ import annotations

from typing import Any

import httpx
from auth_helpers import set_agent_columns
from conftest import create_agent
from sqlalchemy import select

from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database

WEB = {"Origin": "http://localhost:3000"}


async def _start(client: httpx.AsyncClient, agent: dict[str, Any], **kwargs: Any) -> httpx.Response:
    return await client.post(f"/v1/agents/{agent['id']}/text-sessions", json=kwargs.pop("json", {}), **kwargs)


# ------------------------------------------------------------------- text-sessions


async def test_start_text_session_mints_a_channel_text_session_row(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client)

    response = await _start(client, agent, headers=WEB)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["protocolVersion"] == 1
    assert body["agent"]["slug"] == agent["slug"]
    async with database.session() as session:
        row = (await session.execute(select(SessionRow))).scalar_one()
    assert row.id == body["sessionId"]
    assert row.channel == "text"
    assert row.status == "created"


async def test_start_text_session_from_a_disallowed_origin_is_403(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient
) -> None:
    agent = await create_agent(admin_client)

    response = await _start(client, agent, headers={"Origin": "https://evil.example"})

    assert response.status_code == 403
    assert response.json()["error"]["details"]["origin"] == "https://evil.example"


async def test_start_text_session_privileged_reaches_an_unpublished_agent(
    admin_client: httpx.AsyncClient,
) -> None:
    agent = await create_agent(admin_client, published=False)

    response = await _start(admin_client, agent)

    assert response.status_code == 200, response.text
    assert response.json()["sessionId"]


async def test_start_text_session_unpublished_is_403_for_an_unprivileged_caller(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient
) -> None:
    agent = await create_agent(admin_client, published=False)

    response = await _start(client, agent, headers=WEB)

    assert response.status_code == 403


# --------------------------------------------------------------------- embed-policy


async def test_embed_policy_returns_the_published_agents_allowed_origins(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client)
    await set_agent_columns(database, str(agent["id"]), allowed_origins=["https://shop.example"])

    response = await client.get(f"/v1/agents/{agent['id']}/embed-policy")

    assert response.status_code == 200, response.text
    assert response.json() == {"allowed_origins": ["https://shop.example"]}


async def test_embed_policy_default_allowed_origins_is_empty(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient
) -> None:
    agent = await create_agent(admin_client)

    response = await client.get(f"/v1/agents/{agent['id']}/embed-policy")

    assert response.status_code == 200, response.text
    assert response.json() == {"allowed_origins": []}


async def test_embed_policy_unpublished_agent_is_403_for_an_anonymous_caller(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient
) -> None:
    agent = await create_agent(admin_client, published=False)

    response = await client.get(f"/v1/agents/{agent['id']}/embed-policy")

    assert response.status_code == 403


async def test_embed_policy_unknown_agent_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/agents/does-not-exist/embed-policy")

    assert response.status_code == 404
