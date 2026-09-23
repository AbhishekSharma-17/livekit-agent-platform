"""A write is committed before its response starts (V2-20 live finding).

FastAPI's default ``scope="request"`` runs a ``yield`` dependency's exit code
*after* the response has been sent. `get_db` commits in that exit code, so a
client could receive ``204`` for ``DELETE /v1/api-keys/{id}`` and still
authenticate with the key on its very next request (reproduced live against
LiveKit Cloud: 4 of 12 immediate re-uses succeeded). The session dependencies
are now ``scope="function"``; this test observes the database at the exact
moment the ``http.response.start`` message leaves the app.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from starlette.types import Message

from lkap_api.db.models import ApiKey, AuditLog
from lkap_api.db.session import Database
from lkap_api.settings import Settings


async def _call(app: FastAPI, method: str, path: str, token: str, on_start: Any, body: bytes = b"") -> int:
    """Drive one raw ASGI request; ``on_start`` runs when the response starts."""
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"host", b"api.test"),
            (b"x-admin-token", token.encode()),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("api.test", 80),
        "state": {},
    }
    status: list[int] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            status.append(message["status"])
            await on_start()

    await app(scope, receive, send)
    return status[0]


async def test_revoke_api_key_is_committed_before_the_response_starts(
    app: FastAPI, admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    created = await admin_client.post("/v1/api-keys", json={"name": "race", "scopes": ["agents:read"]})
    assert created.status_code == 201, created.text
    key_id = created.json()["id"]
    seen: dict[str, bool] = {}

    async def check_committed() -> None:
        async with database.session() as session:
            row = await session.get(ApiKey, key_id)
            seen["revoked"] = row is not None and row.revoked_at is not None

    status = await _call(app, "DELETE", f"/v1/api-keys/{key_id}", settings.admin_token, check_committed)

    assert status == 204
    assert seen == {"revoked": True}


async def test_route_audit_row_is_committed_before_the_response_starts(
    app: FastAPI, admin_client: httpx.AsyncClient, database: Database, settings: Settings
) -> None:
    """`admin_context` writes the route's audit row after the handler; it must land first too."""
    created = await admin_client.post("/v1/agents", json={"name": "Race", "pack_id": "generic"})
    assert created.status_code == 201, created.text
    agent_id = created.json()["id"]
    seen: dict[str, bool] = {}

    async def check_audit() -> None:
        async with database.session() as session:
            rows = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "PUT /v1/agents/{agent_id}", AuditLog.target_id == agent_id
                    )
                )
            ).scalars()
            seen["audited"] = any(True for _ in rows)

    status = await _call(
        app, "PUT", f"/v1/agents/{agent_id}", settings.admin_token, check_audit, body=b'{"published": true}'
    )

    assert status == 200
    assert seen == {"audited": True}
