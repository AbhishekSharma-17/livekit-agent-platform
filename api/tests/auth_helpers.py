"""Helpers for the V2-02 auth, tenancy and limits tests.

They write users, workspaces, memberships and API keys straight into the test
database (the api has no "create workspace" route in Phase 1) and build clients
that authenticate the way the console and the public API will: a cookie jar
after `POST /v1/auth/login`, or `Authorization: Bearer lkap_…`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import update

from lkap_api.auth.api_keys import generate_api_key
from lkap_api.auth.passwords import hash_password
from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import Agent, ApiKey, User, Workspace, WorkspaceMember, new_id
from lkap_api.db.session import Database

PASSWORD = "correct horse battery staple"
WEB_ORIGIN = "http://localhost:3000"


async def make_workspace(database: Database, slug: str, name: str | None = None) -> str:
    """Insert a workspace and return its id."""
    workspace_id = new_id()
    async with database.session() as session:
        session.add(Workspace(id=workspace_id, slug=slug, name=name or slug.title(), settings={}))
    return workspace_id


async def make_user(
    database: Database,
    email: str,
    *,
    role: str | None = "viewer",
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    password: str | None = PASSWORD,
    name: str = "",
) -> str:
    """Insert a user (and a membership unless ``role`` is None); return the user id."""
    user_id = new_id()
    async with database.session() as session:
        session.add(
            User(
                id=user_id,
                email=email.lower(),
                name=name,
                password_hash=hash_password(password) if password else None,
            )
        )
        await session.flush()
        if role is not None:
            session.add(WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=role))
    return user_id


async def add_membership(database: Database, user_id: str, workspace_id: str, role: str) -> None:
    """Add an extra membership for an existing user."""
    async with database.session() as session:
        session.add(WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=role))


async def make_api_key(
    database: Database,
    scopes: Iterable[str],
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    **columns: Any,
) -> tuple[str, str]:
    """Insert an API key; return ``(key_id, raw_key)``."""
    raw, prefix, key_hash = generate_api_key()
    key_id = new_id()
    async with database.session() as session:
        session.add(
            ApiKey(
                id=key_id,
                workspace_id=workspace_id,
                name="test key",
                prefix=prefix,
                key_hash=key_hash,
                scopes=list(scopes),
                **columns,
            )
        )
    return key_id, raw


async def set_agent_columns(database: Database, agent_id: str, **values: Any) -> None:
    """Write agent columns the v1 update route does not expose (limits, origins, workspace)."""
    async with database.session() as session:
        await session.execute(update(Agent).where(Agent.id == agent_id).values(**values))


def client_for(app: FastAPI, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    """An in-process client for ``app``."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test", headers=headers
    )


async def login(app: FastAPI, email: str, password: str = PASSWORD) -> httpx.AsyncClient:
    """Sign in and return an unopened client whose cookie jar holds the session.

    Use it as ``async with await login(app, email) as client: ...``.
    """
    async with client_for(app) as signer:
        response = await signer.post("/v1/auth/login", json={"email": email, "password": password})
        assert response.status_code == 204, response.text
        cookies = httpx.Cookies(signer.cookies)
    client = client_for(app)
    client.cookies = cookies
    return client


def cookie_client(app: FastAPI, raw_session: str) -> httpx.AsyncClient:
    """A client presenting a given raw ``lkap_session`` cookie."""
    client = client_for(app)
    client.cookies.set("lkap_session", raw_session)
    return client


def key_client(app: FastAPI, raw_key: str, **headers: str) -> httpx.AsyncClient:
    """A client presenting an API key."""
    return client_for(app, {"Authorization": f"Bearer {raw_key}", **headers})
