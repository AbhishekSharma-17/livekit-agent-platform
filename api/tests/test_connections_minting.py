"""Per-connection token minting and the client factory cache (V2-03, D-V2-5)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import jwt
import pytest
from conftest import inference_config
from connection_fakes import KEY_B, SECRET_B, add_agent, connection_row
from lkap_contracts.dispatch import DispatchMetadata
from sqlalchemy import update

from lkap_api.connections.clients import ConnectionClientFactory
from lkap_api.connections.service import default_connection, mint_session_token, resolve_agent_connection
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import LiveKitConnection
from lkap_api.db.models import Session as SessionRow
from lkap_api.db.session import Database
from lkap_api.errors import ConflictError
from lkap_api.settings import Settings
from lkap_api.vault import Vault


def _claims(token: str, secret: str, issuer: str) -> dict[str, Any]:
    claims: dict[str, Any] = jwt.decode(token, secret, algorithms=["HS256"], issuer=issuer)
    return claims


async def test_mint_session_token_for_agent_bound_to_b_uses_b_secret_url_and_agent_name(
    database: Database, settings: Settings
) -> None:
    vault = Vault(settings.master_key)
    factory = ConnectionClientFactory(vault)
    async with database.session() as session:
        conn_b = connection_row(vault, url="wss://project-b.livekit.cloud")
        session.add(conn_b)
        await session.flush()
        agent = await add_agent(session, inference_config(), connection_id=conn_b.id)
        minted = await mint_session_token(
            session,
            factory,
            agent,
            session_id="s" * 32,
            room_name="lkap-ssssssss",
            identity="user-1",
            participant_name="Ada",
        )

    claims = _claims(minted.participant_token, SECRET_B, KEY_B)
    assert claims["iss"] == KEY_B
    assert minted.server_url == "wss://project-b.livekit.cloud"
    assert minted.connection_id == conn_b.id
    dispatch = claims["roomConfig"]["agents"]
    assert [d["agentName"] for d in dispatch] == ["agent-conn-b"]
    metadata = DispatchMetadata.model_validate(json.loads(dispatch[0]["metadata"]))
    assert metadata.connection_id == conn_b.id
    assert metadata.channel == "web"
    with pytest.raises(jwt.InvalidSignatureError):
        _claims(minted.participant_token, settings.livekit_api_secret, KEY_B)


async def test_mint_session_token_unbound_agent_uses_the_default_connection(
    database: Database, settings: Settings
) -> None:
    factory = ConnectionClientFactory(Vault(settings.master_key))
    async with database.session() as session:
        agent = await add_agent(session, inference_config(), connection_id=None)
        minted = await mint_session_token(
            session,
            factory,
            agent,
            session_id="t" * 32,
            room_name="lkap-tttttttt",
            identity="user-2",
            participant_name="Bo",
            channel="test",
        )
        default = await default_connection(session, DEFAULT_WORKSPACE_ID)

    assert default is not None and minted.connection_id == default.id
    assert minted.server_url == settings.livekit_url
    claims = _claims(minted.participant_token, settings.livekit_api_secret, settings.livekit_api_key)
    assert json.loads(claims["roomConfig"]["agents"][0]["metadata"])["channel"] == "test"


async def test_resolve_agent_connection_without_binding_or_default_conflicts(database: Database) -> None:
    async with database.session() as session:
        agent = await add_agent(session, inference_config(), connection_id=None)
        await session.execute(
            update(LiveKitConnection)
            .where(LiveKitConnection.workspace_id == DEFAULT_WORKSPACE_ID)
            .values(is_default=0)
        )
        with pytest.raises(ConflictError):
            await resolve_agent_connection(session, agent)


def test_client_factory_credentials_follow_credentials_version(settings: Settings) -> None:
    vault = Vault(settings.master_key)
    now = [0.0]
    factory = ConnectionClientFactory(vault, ttl_s=60, clock=lambda: now[0])
    row = connection_row(vault)

    first = factory.credentials(row)
    row.api_secret_ct = vault.encrypt({"api_secret": "rotated-secret-value-long-enough-0000"})
    cached = factory.credentials(row)
    row.credentials_version = 2
    rotated = factory.credentials(row)

    assert first.api_secret == cached.api_secret == SECRET_B
    assert rotated.api_secret == "rotated-secret-value-long-enough-0000"
    assert SECRET_B not in repr(first)


def test_client_factory_cache_expires_and_invalidates(settings: Settings) -> None:
    vault = Vault(settings.master_key)
    now = [0.0]
    factory = ConnectionClientFactory(vault, ttl_s=10, clock=lambda: now[0])
    row = connection_row(vault)
    factory.credentials(row)
    row.api_secret_ct = vault.encrypt({"api_secret": "second"})

    now[0] = 11.0
    expired = factory.credentials(row)
    row.api_secret_ct = vault.encrypt({"api_secret": "third"})
    factory.invalidate(row.id)
    invalidated = factory.credentials(row)

    assert (expired.api_secret, invalidated.api_secret) == ("second", "third")


def test_client_factory_reads_url_and_agent_name_from_the_row(settings: Settings) -> None:
    vault = Vault(settings.master_key)
    factory = ConnectionClientFactory(vault)
    row = connection_row(vault)
    factory.credentials(row)
    row.url = "wss://moved.livekit.cloud"
    row.agent_name = "renamed"

    creds = factory.credentials(row)

    assert (creds.url, creds.agent_name) == ("wss://moved.livekit.cloud", "renamed")


async def test_connect_route_for_agent_bound_to_b_mints_with_b_and_returns_b_url(
    admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    vault = Vault(settings.master_key)
    async with database.session() as session:
        conn_b = connection_row(vault, url="wss://project-b.livekit.cloud")
        session.add(conn_b)
        await session.flush()
        agent = await add_agent(session, inference_config(), connection_id=conn_b.id)

    response = await admin_client.post(f"/v1/agents/{agent.id}/connect", json={"participant_name": "Ada"})

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["serverUrl"] == "wss://project-b.livekit.cloud"
    claims = _claims(body["participantToken"], SECRET_B, KEY_B)
    assert claims["roomConfig"]["agents"][0]["agentName"] == "agent-conn-b"
    async with database.session() as session:
        row = await session.get(SessionRow, body["sessionId"])
    assert row is not None and row.connection_id == conn_b.id
