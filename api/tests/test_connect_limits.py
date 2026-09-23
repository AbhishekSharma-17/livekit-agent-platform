"""Public-connect protection: origins, buckets, concurrency, TTL, F-13 (CONTRACTS-V2 §3.3)."""

from __future__ import annotations

from typing import Any

import httpx
import jwt
import pytest
from auth_helpers import WEB_ORIGIN, key_client, login, make_api_key, make_user, set_agent_columns
from conftest import create_agent
from fastapi import FastAPI
from sqlalchemy import select

from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import Agent, LiveKitConnection
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.settings import Settings, get_settings

WEB = {"Origin": WEB_ORIGIN}


def _claims(token: str, settings: Settings) -> dict[str, Any]:
    decoded: dict[str, Any] = jwt.decode(
        token, settings.livekit_api_secret, algorithms=["HS256"], issuer=settings.livekit_api_key
    )
    return decoded


async def _connect(client: httpx.AsyncClient, agent: dict[str, Any], **kwargs: Any) -> httpx.Response:
    return await client.post(f"/v1/agents/{agent['id']}/connect", json=kwargs.pop("json", {}), **kwargs)


# --------------------------------------------------------------------- origins
async def test_connect_from_a_disallowed_origin_is_403(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient
) -> None:
    agent = await create_agent(admin_client)

    response = await _connect(client, agent, headers={"Origin": "https://evil.example"})

    assert response.status_code == 403
    assert response.json()["error"]["details"]["origin"] == "https://evil.example"


async def test_connect_with_no_origin_is_403_unless_any_origin_is_allowed(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client)

    refused = await _connect(client, agent)
    await set_agent_columns(database, str(agent["id"]), allowed_origins=["*"])
    allowed = await _connect(client, agent)

    assert refused.status_code == 403
    assert allowed.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [{"Origin": "https://shop.example"}, {"Referer": "https://shop.example/pricing?x=1"}],
)
async def test_connect_from_an_allowed_origin_or_referer_passes(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, database: Database, headers: dict[str, str]
) -> None:
    agent = await create_agent(admin_client)
    await set_agent_columns(database, str(agent["id"]), allowed_origins=["https://shop.example/"])

    response = await _connect(client, agent, headers=headers)

    assert response.status_code == 200


async def test_connect_from_the_platform_web_origin_passes_with_empty_allowed_origins(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient
) -> None:
    agent = await create_agent(admin_client)

    response = await _connect(client, agent, headers=WEB)

    assert response.status_code == 200


# ----------------------------------------------------------------------- rates
async def test_seventh_connect_per_ip_within_a_minute_is_429(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client)
    await set_agent_columns(database, str(agent["id"]), limits={"max_concurrent_sessions": 50})

    statuses = [(await _connect(client, agent, headers=WEB)).status_code for _ in range(7)]

    assert statuses == [200] * 6 + [429]
    last = await _connect(client, agent, headers=WEB)
    assert last.json()["error"]["code"] == "rate_limited"
    assert last.json()["error"]["details"]["retry_after_s"] > 0


async def test_per_agent_bucket_applies_to_privileged_callers_too(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client)
    await set_agent_columns(
        database, str(agent["id"]), limits={"rate_per_agent_per_min": 2, "max_concurrent_sessions": 50}
    )

    statuses = [(await _connect(admin_client, agent)).status_code for _ in range(3)]

    assert statuses == [200, 200, 429]


async def test_rate_limits_can_be_disabled(
    client: httpx.AsyncClient,
    admin_client: httpx.AsyncClient,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LKAP_RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    agent = await create_agent(admin_client)
    await set_agent_columns(database, str(agent["id"]), limits={"max_concurrent_sessions": 50})

    statuses = {(await _connect(client, agent, headers=WEB)).status_code for _ in range(8)}

    assert statuses == {200}


# ----------------------------------------------------------------- concurrency
async def test_sixth_concurrent_session_is_429_agent_busy(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client)

    statuses = [(await _connect(admin_client, agent)).status_code for _ in range(6)]
    busy = await _connect(admin_client, agent)

    assert statuses[:5] == [200] * 5
    assert statuses[5] == 429
    assert busy.json()["error"]["code"] == "agent_busy"


async def test_ended_sessions_free_their_slot(admin_client: httpx.AsyncClient, database: Database) -> None:
    agent = await create_agent(admin_client)
    await set_agent_columns(database, str(agent["id"]), limits={"max_concurrent_sessions": 1})

    first = await _connect(admin_client, agent)
    blocked = await _connect(admin_client, agent)
    async with database.session() as session:
        row = await session.get(SessionRow, first.json()["sessionId"])
        assert row is not None
        row.status = "ended"
    freed = await _connect(admin_client, agent)

    assert (first.status_code, blocked.status_code, freed.status_code) == (200, 429, 200)


# ------------------------------------------------------------- token and F-13
async def test_token_lifetime_is_max_session_duration(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    agent = await create_agent(admin_client)
    await set_agent_columns(database, str(agent["id"]), limits={"max_session_duration_s": 900})

    body = (await _connect(admin_client, agent)).json()

    claims = _claims(body["participantToken"], settings)
    assert claims["exp"] - claims["nbf"] == 900


async def test_default_token_lifetime_is_1800_seconds(
    admin_client: httpx.AsyncClient, settings: Settings
) -> None:
    agent = await create_agent(admin_client)

    claims = _claims((await _connect(admin_client, agent)).json()["participantToken"], settings)

    assert claims["exp"] - claims["nbf"] == 1800


async def test_participant_metadata_over_2kb_is_422(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient
) -> None:
    agent = await create_agent(admin_client)

    response = await _connect(client, agent, headers=WEB, json={"participant_metadata": {"blob": "x" * 2100}})

    assert response.status_code == 422
    assert response.json()["error"]["details"]["limit_bytes"] == 2048


async def test_session_rows_record_workspace_connection_and_channel(
    client: httpx.AsyncClient, admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client)

    public = (await _connect(client, agent, headers=WEB)).json()
    test = (await _connect(admin_client, agent)).json()

    async with database.session() as session:
        rows = {row.id: row for row in (await session.execute(select(SessionRow))).scalars().all()}
        agent_row = await session.get(Agent, agent["id"])
        default_connection = await session.scalar(
            select(LiveKitConnection.id).where(LiveKitConnection.is_default == 1)
        )
    assert agent_row is not None
    assert rows[public["sessionId"]].channel == "web"
    assert rows[test["sessionId"]].channel == "test"
    assert rows[public["sessionId"]].workspace_id == agent_row.workspace_id == DEFAULT_WORKSPACE_ID
    # Unbound agents mint on the workspace's default connection (V2-03's resolver).
    assert rows[public["sessionId"]].connection_id == (agent_row.connection_id or default_connection)
    assert default_connection is not None


# --------------------------------------------------------- privileged callers
async def test_builder_cookie_may_test_an_unpublished_agent_but_viewer_may_not(
    app: FastAPI, admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    agent = await create_agent(admin_client, published=False)
    await make_user(database, "b@example.com", role="builder")
    await make_user(database, "v@example.com", role="viewer")

    async with await login(app, "b@example.com") as builder:
        built = await _connect(builder, agent, json={"participant_identity": "tester-1"})
    async with await login(app, "v@example.com") as viewer:
        viewed = await _connect(viewer, agent, headers=WEB)

    assert built.status_code == 200
    assert _claims(built.json()["participantToken"], settings)["sub"] == "tester-1"
    assert viewed.status_code == 403


async def test_api_key_needs_sessions_write_to_be_privileged(
    app: FastAPI, admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client, published=False)
    _, read_only = await make_api_key(database, ["agents:read"])
    _, writer = await make_api_key(database, ["sessions:write"])

    async with key_client(app, read_only) as client:
        refused = await _connect(client, agent)
    async with key_client(app, writer) as client:
        allowed = await _connect(client, agent)

    assert refused.status_code == 403
    assert allowed.status_code == 200


# ---------------------------------------------------------------- limits route
async def test_limits_route_reads_defaults_and_builder_can_replace_them(
    app: FastAPI, admin_client: httpx.AsyncClient, database: Database
) -> None:
    agent = await create_agent(admin_client)
    await make_user(database, "v@example.com", role="viewer")
    new = {
        "max_concurrent_sessions": 2,
        "max_session_duration_s": 600,
        "rate_per_ip_per_min": 3,
        "rate_per_agent_per_min": 30,
    }

    defaults = (await admin_client.get(f"/v1/agents/{agent['id']}/limits")).json()
    async with await login(app, "v@example.com") as viewer:
        refused = await viewer.put(f"/v1/agents/{agent['id']}/limits", json=new)
        readable = await viewer.get(f"/v1/agents/{agent['slug']}/limits")
    replaced = await admin_client.put(f"/v1/agents/{agent['id']}/limits", json=new)
    zero = await admin_client.put(f"/v1/agents/{agent['id']}/limits", json={**new, "rate_per_ip_per_min": 0})

    assert defaults == {
        "max_concurrent_sessions": 5,
        "max_session_duration_s": 1800,
        "rate_per_ip_per_min": 6,
        "rate_per_agent_per_min": 60,
    }
    assert refused.status_code == 403
    assert readable.status_code == 200
    assert replaced.json() == new
    assert (await admin_client.get(f"/v1/agents/{agent['id']}/limits")).json() == new
    assert zero.status_code == 422
