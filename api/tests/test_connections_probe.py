"""Connection test + capability probes against a fake Twirp server (V2-03)."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
from connection_fakes import KEY_B, SECRET_B, closed_port_url, connection_row, fake_livekit

from lkap_api.connections.clients import ConnectionClientFactory
from lkap_api.connections.probe import effective_capabilities, probe_connection, static_capabilities
from lkap_api.db.models import LiveKitConnection
from lkap_api.db.session import Database
from lkap_api.settings import Settings
from lkap_api.vault import Vault


def _factory(settings: Settings) -> tuple[ConnectionClientFactory, Vault]:
    vault = Vault(settings.master_key)
    return ConnectionClientFactory(vault), vault


async def test_probe_connection_valid_credentials_is_ok_with_capabilities(settings: Settings) -> None:
    factory, vault = _factory(settings)
    async with fake_livekit() as (fake, url):
        result = await probe_connection(
            factory, connection_row(vault, url=url), deployment_type="cloud", use_inference=True
        )

    assert result.ok, result.message
    assert result.message == "connected"
    assert result.latency_ms is not None and result.latency_ms >= 0
    caps = result.capabilities
    assert (caps.sip_enabled, caps.egress_enabled, caps.ingress_enabled) == (True, True, True)
    assert caps.inference_available and caps.turn_detector_mode == "hosted"
    assert fake.calls[0] == "/twirp/livekit.RoomService/ListRooms"
    assert set(fake.calls[1:]) == {
        "/twirp/livekit.SIP/ListSIPInboundTrunk",
        "/twirp/livekit.Egress/ListEgress",
        "/twirp/livekit.Ingress/ListIngress",
    }


@pytest.mark.parametrize("bare_401", [False, True])
async def test_probe_connection_bad_secret_reports_unauthenticated(
    settings: Settings, bare_401: bool
) -> None:
    factory, vault = _factory(settings)
    async with fake_livekit(bare_401=bare_401) as (fake, url):
        row = connection_row(vault, url=url, api_secret="not-the-right-secret-at-all-000000")
        result = await probe_connection(factory, row, deployment_type="cloud", use_inference=True)

    assert not result.ok
    assert "unauthenticated" in result.message
    assert fake.calls == ["/twirp/livekit.RoomService/ListRooms"]
    assert not result.capabilities.sip_enabled


async def test_probe_connection_unreachable_url_fails_within_five_seconds(settings: Settings) -> None:
    factory, vault = _factory(settings)
    started = time.monotonic()

    result = await probe_connection(
        factory,
        connection_row(vault, url=closed_port_url()),
        deployment_type="self_hosted",
        use_inference=False,
    )

    assert not result.ok
    assert result.message.startswith("unreachable")
    assert time.monotonic() - started < 5


async def test_probe_connection_slow_server_times_out_within_budget(settings: Settings) -> None:
    factory, vault = _factory(settings)
    async with fake_livekit(delay_s=3.0) as (_fake, url):
        started = time.monotonic()
        result = await probe_connection(
            factory,
            connection_row(vault, url=url),
            deployment_type="cloud",
            use_inference=True,
            timeout_s=0.3,
        )
        elapsed = time.monotonic() - started

    assert not result.ok
    assert result.message.startswith("unreachable")
    assert elapsed < 2


async def test_probe_connection_missing_services_only_clear_their_flags(settings: Settings) -> None:
    factory, vault = _factory(settings)
    async with fake_livekit(sip=False, ingress=False) as (_fake, url):
        result = await probe_connection(
            factory, connection_row(vault, url=url), deployment_type="self_hosted", use_inference=False
        )

    assert result.ok
    caps = result.capabilities
    assert (caps.sip_enabled, caps.egress_enabled, caps.ingress_enabled) == (False, True, False)
    assert result.message == "connected; not reachable: SIP, Ingress"


@pytest.mark.parametrize(
    ("deployment_type", "use_inference", "expected"),
    [
        ("cloud", True, {"inference_available": True, "turn_detector_mode": "hosted", "sip_enabled": True}),
        (
            "cloud",
            False,
            {"inference_available": False, "turn_detector_mode": "local", "cloud_hosting": True},
        ),
        (
            "self_hosted",
            True,
            {
                "inference_available": False,
                "cloud_hosting": False,
                "noise_cancellation_tier": "none",
                "observability_dashboard": False,
                "sip_enabled": False,
            },
        ),
    ],
)
def test_static_capabilities_follow_deployment_type_and_toggle(
    deployment_type: str, use_inference: bool, expected: dict[str, Any]
) -> None:
    caps = static_capabilities(deployment_type, use_inference).model_dump()

    assert {key: caps[key] for key in expected} == expected


def test_effective_capabilities_overlay_only_probed_flags() -> None:
    stored = {"sip_enabled": False, "egress_enabled": True, "inference_available": True}

    caps = effective_capabilities("self_hosted", True, stored)

    assert caps.sip_enabled is False
    assert caps.egress_enabled is True
    assert caps.inference_available is False  # static layer wins: self-hosted never has Inference


async def _create(admin_client: httpx.AsyncClient, url: str, **overrides: Any) -> dict[str, Any]:
    body = {
        "slug": "probe-me",
        "name": "Probe me",
        "url": url,
        "api_key": KEY_B,
        "api_secret": SECRET_B,
        **overrides,
    }
    response = await admin_client.post("/v1/connections", json=body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def test_test_endpoint_records_ok_status_and_capabilities(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    async with fake_livekit(sip=False) as (_fake, url):
        created = await _create(admin_client, url, deployment_type="self_hosted")
        response = await admin_client.post(f"/v1/connections/{created['id']}/test")

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["ok"] is True
    stored = (await admin_client.get(f"/v1/connections/{created['id']}")).json()
    assert stored["status"] == "ok"
    assert stored["last_checked_at"] is not None
    assert stored["capabilities"]["sip_enabled"] is False
    assert stored["capabilities"]["egress_enabled"] is True
    async with database.session() as session:
        row = await session.get(LiveKitConnection, created["id"])
    assert row is not None and row.capabilities["sip_enabled"] is False


async def test_test_endpoint_records_error_and_keeps_previous_flags(admin_client: httpx.AsyncClient) -> None:
    async with fake_livekit(api_secret="the-server-has-another-secret-000000") as (_fake, url):
        created = await _create(admin_client, url)
        response = await admin_client.post(f"/v1/connections/{created['id']}/test")

    stored = (await admin_client.get(f"/v1/connections/{created['id']}")).json()
    assert response.json()["ok"] is False
    assert response.json()["capabilities"] == stored["capabilities"]
    assert stored["status"] == "error"
    assert "unauthenticated" in stored["last_error"]
    assert stored["capabilities"]["sip_enabled"] is True  # unprobed cloud assumption unchanged


async def test_test_unsaved_endpoint_probes_without_storing(admin_client: httpx.AsyncClient) -> None:
    async with fake_livekit() as (_fake, url):
        response = await admin_client.post(
            "/v1/connections/test",
            json={"slug": "draft", "name": "Draft", "url": url, "api_key": KEY_B, "api_secret": SECRET_B},
        )

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert SECRET_B not in response.text
    assert (await admin_client.get("/v1/connections")).json()["total"] == 1
