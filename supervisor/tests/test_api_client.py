"""``HttpFleetApi`` against a mocked api (respx)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from lkap_contracts.fleet import ReplicaHandle

from fakes import SECRET, desired
from lkap_supervisor.api_client import SERVICE_HEADER, ApiClientError, HttpFleetApi

BASE = "http://api.test"


@respx.mock
async def test_fleet_desired_sends_the_service_token_and_parses_rows() -> None:
    route = respx.get(f"{BASE}/internal/v1/fleet/desired").mock(
        return_value=httpx.Response(200, json=[desired().model_dump(mode="json")])
    )
    api = HttpFleetApi(BASE, "svc-token")

    rows = await api.fleet_desired()
    await api.aclose()

    assert rows == [desired()]
    assert route.calls.last.request.headers[SERVICE_HEADER] == "svc-token"


@respx.mock
async def test_worker_env_errors_never_carry_the_body() -> None:
    respx.get(f"{BASE}/internal/v1/connections/conn-a/worker-env").mock(
        return_value=httpx.Response(500, text=f"boom {SECRET}")
    )
    api = HttpFleetApi(BASE, "svc-token")

    with pytest.raises(ApiClientError) as info:
        await api.worker_env("conn-a")
    await api.aclose()

    assert "HTTP 500" in str(info.value)
    assert SECRET not in str(info.value)


@respx.mock
async def test_malformed_worker_env_does_not_leak_through_validation_errors() -> None:
    respx.get(f"{BASE}/internal/v1/connections/conn-a/worker-env").mock(
        return_value=httpx.Response(200, json={"env": {"LIVEKIT_API_SECRET": [SECRET]}})
    )
    api = HttpFleetApi(BASE, "svc-token")

    with pytest.raises(ApiClientError) as info:
        await api.worker_env("conn-a")
    await api.aclose()

    assert SECRET not in str(info.value) and info.value.__cause__ is None


@respx.mock
async def test_an_unreachable_api_is_an_api_client_error() -> None:
    respx.get(f"{BASE}/internal/v1/fleet/desired").mock(side_effect=httpx.ConnectError("refused"))
    api = HttpFleetApi(BASE, "svc-token")

    with pytest.raises(ApiClientError, match="ConnectError"):
        await api.fleet_desired()
    await api.aclose()


@respx.mock
async def test_register_replica_registers_as_supervisor_then_marks_starting() -> None:
    register = respx.post(f"{BASE}/internal/v1/workers/register").mock(
        return_value=httpx.Response(200, json={"connection_id": "conn-a", "agent_name": "agent-conn-a"})
    )
    heartbeat = respx.post(f"{BASE}/internal/v1/workers/host:42/heartbeat").mock(
        return_value=httpx.Response(204)
    )
    api = HttpFleetApi(BASE, "svc-token")
    handle = ReplicaHandle(
        connection_id="conn-a", replica_index=0, instance_key="host:42", desired_hash="a" * 64
    )

    await api.register_replica(handle, desired())
    await api.report_status("host:42", "draining")
    await api.aclose()

    body = json.loads(register.calls.last.request.content)
    assert body["managed_by"] == "supervisor"
    assert body["connection_id"] == "conn-a"
    assert body["instance_key"] == "host:42"
    statuses = [json.loads(call.request.content)["status"] for call in heartbeat.calls]
    assert statuses == ["starting", "draining"]
