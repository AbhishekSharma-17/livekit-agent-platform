"""Self-hosted LiveKit connections may reach private networks and tailnets; nothing else may.

A self-hosted connection is an admin-configured destination: its url may name a
loopback, RFC 1918, IPv6 ULA or CGNAT (``100.64.0.0/10``, every Tailscale node)
address, at save time and at connect time. Cloud metadata addresses, link-local,
multicast, unspecified and reserved addresses stay refused. A Cloud connection,
webhooks and tools keep the strict public-only rule; a Cloud connection's
refusal says to mark the connection self-hosted.

The network is faked at the boundary: resolvers are injected, and the only
socket opened is the local fake LiveKit server's.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any

import aiohttp
import httpx
import pytest
from connection_fakes import KEY_B, SECRET_B, fake_livekit
from lkap_contracts.api_models import ConnectionCreate

from lkap_api import net_guard
from lkap_api.connections.clients import ConnectionClientFactory
from lkap_api.connections.probe import probe_connection
from lkap_api.connections.service import UnsavedConnection
from lkap_api.errors import UnprocessableEntityError
from lkap_api.settings import Settings
from lkap_api.vault import Vault

PROD = net_guard.NetPolicy.from_entries(())
SELF_HOSTED = PROD.for_connection("self_hosted")
CLOUD = PROD.for_connection("cloud")
LK = net_guard.LIVEKIT_SCHEMES

#: An example tailnet name and a CGNAT address like the one Tailscale hands out.
TAILNET_HOST = "livekit.example-tailnet.ts.net"
TAILNET_IP = "100.101.102.103"


def _resolver(answers: dict[str, list[str]]) -> Any:
    async def resolve(host: str, port: int) -> list[Any]:
        return [ipaddress.ip_address(a) for a in answers[host]]

    return resolve


class _FakeResolver(aiohttp.abc.AbstractResolver):
    def __init__(self, answers: dict[str, list[str]]) -> None:
        self._answers = answers

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[aiohttp.abc.ResolveResult]:
        return [
            {"hostname": host, "host": a, "port": port, "family": family, "proto": 0, "flags": 0}
            for a in self._answers[host]
        ]

    async def close(self) -> None:
        return None


# ================================================================ policy, save time
@pytest.mark.parametrize(
    "url",
    [
        f"wss://{TAILNET_IP}",
        f"wss://{TAILNET_HOST}",
        "ws://10.0.0.5:7880",
        "ws://172.20.1.1:7880",
        "ws://192.168.1.20:7880",
        "ws://127.0.0.1:7880",
        "ws://[::1]:7880",
        "ws://[fd12:3456::1]:7880",
        "ws://[::ffff:10.0.0.1]:7880",
        "ws://localhost:7880",
        "ws://livekit.localhost:7880",
    ],
)
def test_check_url_self_hosted_private_destination_is_allowed_without_an_allowlist(url: str) -> None:
    assert net_guard.check_url(url, SELF_HOSTED, schemes=LK) is None


@pytest.mark.parametrize(
    "url",
    [
        "ws://169.254.169.254",
        "ws://100.100.100.200",  # Alibaba metadata, inside 100.64.0.0/10
        "ws://[fd00:ec2::254]",  # AWS IMDS over IPv6, inside fc00::/7
        "ws://169.254.170.2",  # ECS task metadata
        "ws://169.254.10.10",
        "ws://[fe80::1]",
        "ws://0.0.0.0:7880",
        "ws://224.0.0.1",
        "ws://240.0.0.1",
        "ws://192.0.2.1",  # TEST-NET: `is_private`, but not RFC 1918
        "ws://metadata.google.internal",
        "ws://2130706433",
        "ws://127.1",
    ],
)
def test_check_url_self_hosted_metadata_and_reserved_destinations_stay_refused(url: str) -> None:
    assert net_guard.check_url(url, SELF_HOSTED, schemes=LK) is not None


@pytest.mark.parametrize(
    "url", [f"wss://{TAILNET_IP}", "ws://10.0.0.5:7880", "ws://[fd12::1]", "ws://localhost:7880"]
)
def test_check_url_cloud_private_destination_is_refused_with_the_self_hosted_hint(url: str) -> None:
    problem = net_guard.check_url(url, CLOUD, schemes=LK)

    assert problem is not None
    assert net_guard.SELF_HOSTED_HINT in problem


def test_check_url_cloud_metadata_refusal_does_not_suggest_self_hosted() -> None:
    problem = net_guard.check_url("wss://100.100.100.200", CLOUD, schemes=LK)

    assert problem is not None and "metadata" in problem
    assert net_guard.SELF_HOSTED_HINT not in problem


def test_for_connection_keeps_the_operator_allowlist() -> None:
    base = net_guard.NetPolicy.from_entries(["10.20.0.0/16"])

    assert net_guard.check_url("ws://10.20.1.1", base.for_connection("cloud"), schemes=LK) is None
    assert net_guard.check_url("ws://10.30.1.1", base.for_connection("cloud"), schemes=LK) is not None
    assert base.for_connection("self_hosted").for_connection("cloud").trusted_private is False


@pytest.mark.parametrize(("deployment_type", "allowed"), [("self_hosted", True), ("cloud", False)])
@pytest.mark.parametrize("url", [f"ws://{TAILNET_IP}:7880", "ws://10.0.0.5:7880", "ws://[fd12::5]:7880"])
def test_unsaved_connection_from_create_uses_the_deployment_type(
    settings: Settings, url: str, deployment_type: str, allowed: bool
) -> None:
    """The pre-save ``POST /v1/connections/test`` path: validated by the body's deployment type."""
    payload = ConnectionCreate(
        slug="lk", name="lk", deployment_type=deployment_type, url=url, api_key="k", api_secret="s" * 32
    )
    vault = Vault(settings.master_key)

    if allowed:
        assert UnsavedConnection.from_create(vault, payload).deployment_type == "self_hosted"
        return
    with pytest.raises(UnprocessableEntityError, match="mark the connection self-hosted"):
        UnsavedConnection.from_create(vault, payload)


# ====================================================================== connect time
@pytest.mark.parametrize("answer", [TAILNET_IP, "10.0.0.5", "127.0.0.1", "fd12::7"])
async def test_checked_addresses_self_hosted_name_resolving_privately_passes(answer: str) -> None:
    ok = await net_guard.checked_addresses(
        TAILNET_HOST, 443, SELF_HOSTED, resolve=_resolver({TAILNET_HOST: [answer]})
    )

    assert ok == [ipaddress.ip_address(answer)]


@pytest.mark.parametrize(
    "answers", [["169.254.169.254"], ["100.100.100.200"], [TAILNET_IP, "169.254.169.254"], ["192.0.2.1"]]
)
async def test_checked_addresses_self_hosted_never_reaches_metadata_or_reserved(answers: list[str]) -> None:
    with pytest.raises(net_guard.BlockedDestinationError):
        await net_guard.checked_addresses(
            TAILNET_HOST, 443, SELF_HOSTED, resolve=_resolver({TAILNET_HOST: answers})
        )


async def test_self_hosted_localhost_name_must_still_resolve_to_loopback() -> None:
    ok = await net_guard.checked_addresses(
        "localhost", 7880, SELF_HOSTED, resolve=_resolver({"localhost": ["127.0.0.1"]})
    )

    assert ok == [ipaddress.ip_address("127.0.0.1")]
    with pytest.raises(net_guard.BlockedDestinationError, match="metadata"):
        await net_guard.checked_addresses(
            "localhost", 7880, SELF_HOSTED, resolve=_resolver({"localhost": ["169.254.169.254"]})
        )


async def test_guarded_resolver_tailnet_answer_passes_self_hosted_and_fails_cloud() -> None:
    answers = {TAILNET_HOST: [TAILNET_IP]}

    passed = await net_guard.GuardedResolver(SELF_HOSTED, inner=_FakeResolver(answers)).resolve(TAILNET_HOST)
    assert [r["host"] for r in passed] == [TAILNET_IP]
    with pytest.raises(net_guard.BlockedDestinationError, match="carrier-grade NAT") as refused:
        await net_guard.GuardedResolver(CLOUD, inner=_FakeResolver(answers)).resolve(TAILNET_HOST)
    assert net_guard.SELF_HOSTED_HINT in str(refused.value)


@pytest.mark.parametrize(("literal", "allowed"), [(TAILNET_IP, True), ("100.100.100.200", False)])
async def test_guarded_connector_self_hosted_literal(literal: str, allowed: bool) -> None:
    connector = net_guard.GuardedTCPConnector(SELF_HOSTED, resolver=net_guard.GuardedResolver(SELF_HOSTED))
    try:
        if allowed:
            assert [r["host"] for r in await connector._resolve_host(literal, 7880)] == [literal]
        else:
            with pytest.raises(net_guard.BlockedDestinationError, match="metadata"):
                await connector._resolve_host(literal, 7880)
    finally:
        await connector.close()


def _unsaved(vault: Vault, url: str, deployment_type: str) -> UnsavedConnection:
    return UnsavedConnection(
        id=f"c-{deployment_type}",
        url=url,
        agent_name="x",
        credentials_version=1,
        api_key_ct=vault.encrypt({"api_key": KEY_B}),
        api_secret_ct=vault.encrypt({"api_secret": SECRET_B}),
        deployment_type=deployment_type,
    )


async def test_probe_self_hosted_connection_on_a_private_name_connects_under_the_prod_policy(
    settings: Settings,
) -> None:
    """Factory → guarded session → resolver → a real (local) LiveKit: the tailnet case end to end."""
    vault = Vault(settings.master_key)
    async with fake_livekit() as (fake, url):
        port = url.rsplit(":", 1)[1]
        factory = ConnectionClientFactory(
            vault, net_policy=PROD, net_resolver=lambda: _FakeResolver({TAILNET_HOST: ["127.0.0.1"]})
        )
        self_hosted = await probe_connection(
            factory,
            _unsaved(vault, f"http://{TAILNET_HOST}:{port}", "self_hosted"),
            deployment_type="self_hosted",
            use_inference=False,
        )
        cloud = await probe_connection(
            factory,
            _unsaved(vault, f"http://{TAILNET_HOST}:{port}", "cloud"),
            deployment_type="cloud",
            use_inference=False,
        )

    assert self_hosted.ok, self_hosted.message
    assert fake.calls[0] == "/twirp/livekit.RoomService/ListRooms"
    assert cloud.ok is False
    assert "blocked destination" in cloud.message
    assert net_guard.SELF_HOSTED_HINT in cloud.message


async def test_probe_self_hosted_connection_to_metadata_literal_sends_nothing(settings: Settings) -> None:
    vault = Vault(settings.master_key)
    factory = ConnectionClientFactory(vault, net_policy=PROD)

    result = await probe_connection(
        factory,
        _unsaved(vault, "ws://100.100.100.200:7880", "self_hosted"),
        deployment_type="self_hosted",
        use_inference=False,
    )

    assert result.ok is False
    assert "blocked destination" in result.message and "metadata" in result.message


# ============================================================================== api
def _body(url: str, deployment_type: str, slug: str) -> dict[str, Any]:
    return {
        "slug": slug,
        "name": slug,
        "deployment_type": deployment_type,
        "url": url,
        "api_key": "k",
        "api_secret": "s" * 32,
        "agent_name": f"lkap-{slug}",
    }


@pytest.mark.parametrize(
    "url", [f"wss://{TAILNET_HOST}", f"ws://{TAILNET_IP}:7880", "ws://10.0.0.5:7880", "ws://[fd12::5]:7880"]
)
async def test_create_self_hosted_connection_on_a_private_network_is_201(
    admin_client: httpx.AsyncClient, url: str
) -> None:
    response = await admin_client.post("/v1/connections", json=_body(url, "self_hosted", "tailnet"))

    assert response.status_code == 201, response.text
    assert response.json()["url"] == url


@pytest.mark.parametrize("url", [f"ws://{TAILNET_IP}:7880", "ws://10.0.0.5:7880"])
async def test_create_cloud_connection_on_a_private_network_is_422_with_the_hint(
    admin_client: httpx.AsyncClient, url: str
) -> None:
    response = await admin_client.post("/v1/connections", json=_body(url, "cloud", "cloud-private"))

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["details"]["reason"] == "blocked_destination"
    assert "mark the connection self-hosted" in error["message"]


async def test_update_self_hosted_connection_to_a_tailnet_address_is_200(
    admin_client: httpx.AsyncClient,
) -> None:
    created = await admin_client.post(
        "/v1/connections", json=_body("ws://10.0.0.5:7880", "self_hosted", "lan")
    )
    assert created.status_code == 201, created.text

    response = await admin_client.put(
        f"/v1/connections/{created.json()['id']}", json={"url": f"ws://{TAILNET_IP}:7880"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "unverified"


# ================================================================ other surfaces unchanged
@pytest.mark.parametrize("url", [f"http://{TAILNET_IP}/hook", "http://10.0.0.8/hook"])
async def test_webhook_to_a_cgnat_or_private_address_stays_422(
    admin_client: httpx.AsyncClient, url: str
) -> None:
    response = await admin_client.post("/v1/webhooks", json={"url": url, "events": []})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "blocked_destination"


async def test_tool_dry_run_to_a_cgnat_address_stays_refused(
    admin_client: httpx.AsyncClient, mock_http: list[httpx.Request]
) -> None:
    definition = {
        "kind": "http",
        "name": "tailnet",
        "description": "x",
        "parameters": {"type": "object", "properties": {}},
        "method": "GET",
        "url": f"http://{TAILNET_IP}/internal",
        "allowed_hosts": [TAILNET_IP],
    }
    tool = await admin_client.post(
        "/v1/tools", json={"kind": "http", "name": "tailnet", "definition": definition}
    )
    assert tool.status_code == 201, tool.text

    response = await admin_client.post(f"/v1/tools/{tool.json()['id']}/dry-run", json={"arguments": {}})

    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "blocked_destination"
    assert mock_http == []


def test_process_policy_is_never_self_hosted(settings: Settings) -> None:
    """Webhooks, tools, QA judges, MCP and vendor calls use this policy as it is."""
    for env in ("dev", "prod"):
        policy = net_guard.policy_from_settings(settings.model_copy(update={"env": env}))
        assert policy.trusted_private is False
        assert net_guard.check_url(f"http://{TAILNET_IP}/", policy) is not None
