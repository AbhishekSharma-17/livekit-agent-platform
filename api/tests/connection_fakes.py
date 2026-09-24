"""Test doubles for the LiveKit server API boundary (V2-03 tests).

`livekit-api` talks Twirp over `aiohttp`, so `respx`/`httpx.MockTransport`
cannot intercept it. :func:`fake_livekit` runs a real in-process Twirp server
on `127.0.0.1` instead: it verifies each request's JWT against the key/secret it
was given (exactly what LiveKit does) and answers with an empty protobuf body,
which parses as an empty response for every list method.

V4-05: ``livekit.PhoneNumberService/*`` is answered in proto3-JSON (what LiveKit
Cloud does for ``Content-Type: application/json``) from :attr:`FakeLiveKit.phone_numbers`,
with LiveKit's snake_case keys (or lowerCamelCase with ``phone_camel``). Only
List/Get/Update exist; every other method of the service answers 500, so a
test proves the api never searches for, buys or gives back a number.
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
    #: Hosted numbers as LiveKit returns them (see :func:`hosted_number`).
    phone_numbers: list[dict[str, Any]] = field(default_factory=list)
    phone_page_size: int = 50
    phone_camel: bool = False
    #: ``UpdatePhoneNumber`` with an empty rule id answers 400 (one possible LiveKit behaviour).
    phone_reject_empty_rule: bool = False
    #: Method name → (HTTP status, Twirp error body) answered instead of the normal reply.
    phone_fail: dict[str, tuple[int, dict[str, Any]]] = field(default_factory=dict)
    #: ``(method, json body, content type, jwt claims)`` of every PhoneNumberService request.
    phone_requests: list[tuple[str, dict[str, Any], str, dict[str, Any]]] = field(default_factory=list)
    #: Shared, ordered log of calls across fakes (tests append SIP calls to it too).
    timeline: list[str] = field(default_factory=list)

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
            claims = jwt.decode(token, self.api_secret, algorithms=["HS256"], issuer=self.api_key)
        except jwt.InvalidTokenError:
            if self.bare_401:  # what LiveKit Cloud actually sends (no Twirp JSON body)
                return web.Response(status=401, text="unauthorized")
            return web.json_response(
                {"code": "unauthenticated", "msg": "invalid API key or secret"}, status=401
            )
        service = request.path.split("/")[2]
        if service == "livekit.PhoneNumberService":
            return await self._phone_number_service(request, request.path.split("/")[3], claims)
        disabled = {"livekit.SIP": not self.sip, "livekit.Egress": not self.egress}
        disabled["livekit.Ingress"] = not self.ingress
        if disabled.get(service, False):
            return web.json_response({"code": "not_found", "msg": "service not deployed"}, status=404)
        return web.Response(body=b"", content_type="application/protobuf")

    # PhoneNumberService (V4-05) ------------------------------------------------
    def _phone_out(self, number: dict[str, Any]) -> dict[str, Any]:
        if not self.phone_camel:
            return dict(number)
        return {_camel(key): value for key, value in number.items()}

    def _find_phone(self, body: dict[str, Any]) -> dict[str, Any] | None:
        wanted = body.get("id")
        return next((n for n in self.phone_numbers if n["id"] == wanted), None)

    async def _phone_number_service(
        self, request: web.Request, method: str, claims: dict[str, Any]
    ) -> web.Response:
        body: dict[str, Any] = await request.json() if request.can_read_body else {}
        self.phone_requests.append((method, body, request.content_type, claims))
        self.timeline.append(f"phone.{method}")
        if method in self.phone_fail:
            status, payload = self.phone_fail[method]
            return web.json_response(payload, status=status)
        if method == "ListPhoneNumbers":
            token = body.get("pageToken") or body.get("page_token") or {}
            offset = int(token.get("token") or 0) if isinstance(token, dict) else int(token or 0)
            statuses = set(body.get("statuses") or [])
            items = [n for n in self.phone_numbers if not statuses or n.get("status") in statuses]
            page = items[offset : offset + self.phone_page_size]
            following = offset + self.phone_page_size
            return web.json_response(
                {
                    "items": [self._phone_out(n) for n in page],
                    "next_page_token": {"token": str(following)} if following < len(items) else None,
                    "total_count": len(items),
                    "offline_count": sum(1 for n in items if n.get("status") == OFFLINE),
                }
            )
        if method in ("GetPhoneNumber", "UpdatePhoneNumber"):
            number = self._find_phone(body)
            if number is None:
                return web.json_response({"code": "not_found", "msg": "phone number not found"}, status=404)
            if method == "UpdatePhoneNumber":
                rule = body.get("sipDispatchRuleId", body.get("sip_dispatch_rule_id"))
                if rule == "" and self.phone_reject_empty_rule:
                    return web.json_response(
                        {"code": "invalid_argument", "msg": "sip_dispatch_rule_id is required"}, status=400
                    )
                if rule is not None:
                    number["sip_dispatch_rule_id"] = rule
                    number["sip_dispatch_rule_ids"] = [rule] if rule else []
                    number["inbound_status"] = (
                        "PHONE_NUMBER_IN_STATUS_ACTIVE" if rule else "PHONE_NUMBER_IN_STATUS_DETACHED"
                    )
            return web.json_response({"phone_number": self._phone_out(number)})
        return web.json_response({"code": "internal", "msg": f"{method} must never be called"}, status=500)


OFFLINE = "PHONE_NUMBER_STATUS_OFFLINE"


def _camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(part.title() for part in rest)


def hosted_number(
    e164: str,
    number_id: str,
    *,
    status: str = "PHONE_NUMBER_STATUS_ACTIVE",
    inbound_status: str = "PHONE_NUMBER_IN_STATUS_DETACHED",
    rule_ids: list[str] | None = None,
    name: str = "",
) -> dict[str, Any]:
    """One ``PhoneNumber`` exactly as LiveKit Cloud's Twirp-JSON returns it (V4-05 live check shape)."""
    rules = list(rule_ids or [])
    return {
        "id": number_id,
        "name": name,
        "e164_format": e164,
        "country_code": "US",
        "area_code": e164[2:5],
        "number_type": "PHONE_NUMBER_TYPE_LOCAL",
        "locality": "SAN FRANCISCO",
        "region": "CA",
        "spam_score": 0,
        "created_at": None,
        "updated_at": None,
        "capabilities": ["voice"],
        "status": status,
        "inbound_status": inbound_status,
        "outbound_status": "PHONE_NUMBER_OUT_STATUS_UNSPECIFIED",
        "assigned_at": "2026-09-24T19:39:32.739773Z",
        "released_at": None,
        "sip_dispatch_rule_id": rules[0] if rules else "",
        "sip_dispatch_rule_ids": rules,
    }


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
        "use_inference": True,
        "is_default": False,
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
        published=True,
        config=config.model_dump(mode="json"),
        config_version=1,
        connection_id=connection_id,
    )
    session.add(agent)
    await session.flush()
    return agent
