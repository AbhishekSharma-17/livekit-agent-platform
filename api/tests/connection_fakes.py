"""Test doubles for the LiveKit server API boundary (V2-03 tests).

`livekit-api` talks Twirp over `aiohttp`, so `respx`/`httpx.MockTransport`
cannot intercept it. :func:`fake_livekit` runs a real in-process Twirp server
on `127.0.0.1` instead: it verifies each request's JWT against the key/secret it
was given (exactly what LiveKit does) and answers with an empty protobuf body,
which parses as an empty response for every list method.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import jwt
from aiohttp import web
from aiohttp.test_utils import TestServer
from lkap_contracts.agent_config import AgentConfig
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import Agent, LiveKitConnection, new_id
from lkap_api.vault import Vault

KEY_B = "APIconnB1234"
SECRET_B = "connection-b-secret-long-enough-for-hs256-signing"


@dataclass
class FakeLiveKit:
    """A minimal LiveKit Twirp server."""

    api_key: str
    api_secret: str
    sip: bool = True
    egress: bool = True
    ingress: bool = True
    delay_s: float = 0.0
    bare_401: bool = False
    calls: list[str] = field(default_factory=list)

    def app(self) -> web.Application:
        """The aiohttp application serving every `/twirp/livekit.*` route."""
        app = web.Application()
        app.router.add_post("/twirp/{method:.*}", self._handle)
        return app

    async def _handle(self, request: web.Request) -> web.Response:
        self.calls.append(request.path)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        try:
            jwt.decode(token, self.api_secret, algorithms=["HS256"], issuer=self.api_key)
        except jwt.InvalidTokenError:
            if self.bare_401:  # what LiveKit Cloud actually sends (no Twirp JSON body)
                return web.Response(status=401, text="unauthorized")
            return web.json_response(
                {"code": "unauthenticated", "msg": "invalid API key or secret"}, status=401
            )
        service = request.path.split("/")[2]
        disabled = {"livekit.SIP": not self.sip, "livekit.Egress": not self.egress}
        disabled["livekit.Ingress"] = not self.ingress
        if disabled.get(service, False):
            return web.json_response({"code": "not_found", "msg": "service not deployed"}, status=404)
        return web.Response(body=b"", content_type="application/protobuf")


@asynccontextmanager
async def fake_livekit(**kwargs: Any) -> AsyncIterator[tuple[FakeLiveKit, str]]:
    """Run a :class:`FakeLiveKit`; yields it and its `http://127.0.0.1:<port>` url."""
    kwargs.setdefault("api_key", KEY_B)
    kwargs.setdefault("api_secret", SECRET_B)
    fake = FakeLiveKit(**kwargs)
    server = TestServer(fake.app(), host="127.0.0.1")
    await server.start_server()
    try:
        yield fake, f"http://127.0.0.1:{server.port}"
    finally:
        await server.close()


def closed_port_url() -> str:
    """A localhost url nothing listens on (connection refused immediately)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def connection_row(
    vault: Vault,
    *,
    slug: str = "conn-b",
    url: str = "wss://project-b.livekit.cloud",
    api_key: str = KEY_B,
    api_secret: str = SECRET_B,
    workspace_id: str = "00000000000000000000000000000001",
    **overrides: Any,
) -> LiveKitConnection:
    """An unsaved `livekit_connections` row with encrypted credentials."""
    values: dict[str, Any] = {
        "id": new_id(),
        "workspace_id": workspace_id,
        "slug": slug,
        "name": slug.upper(),
        "deployment_type": "cloud",
        "url": url,
        "api_key_ct": vault.encrypt({"api_key": api_key}),
        "api_secret_ct": vault.encrypt({"api_secret": api_secret}),
        "credentials_version": 1,
        "agent_name": f"agent-{slug}",
        "deployment_mode": "external",
        "replicas": 1,
        "worker_image": "slim",
        "use_inference": 1,
        "is_default": 0,
        "status": "unverified",
        "capabilities": {},
    }
    values.update(overrides)
    return LiveKitConnection(**values)


async def add_agent(
    session: AsyncSession, config: AgentConfig, *, connection_id: str | None, slug: str | None = None
) -> Agent:
    """Insert an agent row bound to `connection_id` (bypassing the agents router)."""
    agent_id = new_id()
    agent = Agent(
        id=agent_id,
        slug=slug or f"agent-{agent_id[:8]}",
        name="Agent",
        pack_id="generic",
        ui_panel_id="generic",
        published=1,
        config=config.model_dump(mode="json"),
        config_version=1,
        connection_id=connection_id,
    )
    session.add(agent)
    await session.flush()
    return agent
