"""Worker registration and heartbeat (CONTRACTS-V2 §5, D-V2-8): payload, clock, draining, re-register."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
from pathlib import Path
from typing import Any

import httpx
import livekit.agents
import pytest
import respx
import structlog
from lkap_contracts.fleet import WorkerHeartbeatIn, WorkerRegisterIn, WorkerRegisterOut
from test_main import _settings

from lkap_agent.registration import (
    FleetApiError,
    FleetClient,
    WorkerRegistration,
    default_instance_key,
    discover_installed_provider_ids,
    installed_provider_ids,
    sdk_version,
)

API = "http://api.test"


class FakeFleet:
    """Records register/heartbeat calls; `known=False` makes heartbeats 404."""

    def __init__(self, *, agent_name: str = "lkap-agent", fail_register: int = 0) -> None:
        self.agent_name = agent_name
        self.fail_register = fail_register
        self.known = True
        self.registrations: list[WorkerRegisterIn] = []
        self.heartbeats: list[tuple[str, WorkerHeartbeatIn]] = []

    async def register(self, payload: WorkerRegisterIn) -> WorkerRegisterOut:
        if self.fail_register:
            self.fail_register -= 1
            raise FleetApiError("api unreachable")
        self.registrations.append(payload)
        self.known = True
        return WorkerRegisterOut(
            connection_id=payload.connection_id or "conn-default", agent_name=self.agent_name
        )

    async def heartbeat(self, instance_key: str, payload: WorkerHeartbeatIn) -> bool:
        self.heartbeats.append((instance_key, payload))
        return self.known


class FakeServer:
    """The `draining` / `active_jobs` / `on` slice of `AgentServer`."""

    def __init__(self) -> None:
        self.draining = False
        self.active_jobs: list[Any] = []
        self.handlers: dict[str, Any] = {}

    def on(self, event: str, callback: Any = None) -> Any:
        self.handlers[event] = callback
        return callback


class Clock:
    """A fake `sleep` that advances virtual time and stops the loop after `limit_s`."""

    def __init__(self, registration: WorkerRegistration | None = None, *, limit_s: float = 95.0) -> None:
        self.now = 0.0
        self.limit_s = limit_s
        self.registration = registration
        self.on_tick: Any = None

    async def sleep(self, delay_s: float) -> None:
        self.now += delay_s
        if self.on_tick is not None:
            self.on_tick(self.now)
        if self.now >= self.limit_s and self.registration is not None:
            self.registration.close()
        await asyncio.sleep(0)


def _registration(
    fleet: FakeFleet, server: FakeServer, clock: Clock, **settings_overrides: Any
) -> WorkerRegistration:
    settings = _settings()
    for key, value in settings_overrides.items():
        setattr(settings, key, value)
    registration = WorkerRegistration(
        client=fleet,
        settings=settings,
        server=server,
        pack_ids=lambda: ["generic", "insurance_claim"],
        installed=lambda: ["livekit-inference-llm", "silero-vad"],
        sleep=clock.sleep,
        poll_s=1.0,
    )
    clock.registration = registration
    return registration


async def test_register_payload_carries_connection_image_sdk_providers_and_packs() -> None:
    fleet, server = FakeFleet(), FakeServer()
    registration = _registration(
        fleet, server, Clock(), connection_id="conn-b", instance_key="replica-0", image_flavor="full"
    )

    assert await registration.register()

    (payload,) = fleet.registrations
    assert payload.connection_id == "conn-b"
    assert payload.instance_key == "replica-0"
    assert payload.image == "full"
    assert payload.sdk_version == sdk_version() == livekit.agents.__version__
    assert payload.installed_provider_ids == ["livekit-inference-llm", "silero-vad"]
    assert payload.pack_ids == ["generic", "insurance_claim"]
    assert payload.managed_by == "external"
    assert registration.connection_id == "conn-b"


async def test_register_honours_lkap_managed_by() -> None:
    fleet = FakeFleet()
    registration = _registration(fleet, FakeServer(), Clock(), managed_by="supervisor")

    await registration.register()

    assert fleet.registrations[0].managed_by == "supervisor"


async def test_unset_connection_id_lets_the_api_pick_the_default_connection() -> None:
    fleet = FakeFleet()
    registration = _registration(fleet, FakeServer(), Clock())

    await registration.register()

    assert fleet.registrations[0].connection_id is None
    assert registration.connection_id == "conn-default"


async def test_heartbeat_posts_every_30_seconds_on_the_fake_clock() -> None:
    fleet, server = FakeFleet(), FakeServer()
    clock = Clock(limit_s=95.0)
    registration = _registration(fleet, server, clock)
    beat_times: list[float] = []
    record = fleet.heartbeat

    async def _timed(key: str, payload: WorkerHeartbeatIn) -> bool:
        beat_times.append(clock.now)
        return await record(key, payload)

    fleet.heartbeat = _timed  # type: ignore[method-assign]

    await registration.run()

    assert len(fleet.registrations) == 1
    assert beat_times == [30.0, 60.0, 90.0]
    assert [hb.status for _key, hb in fleet.heartbeats] == ["ready", "ready", "ready"]
    assert {key for key, _hb in fleet.heartbeats} == {registration.instance_key}


async def test_heartbeat_reports_active_jobs() -> None:
    fleet, server = FakeFleet(), FakeServer()
    server.active_jobs = [object(), object()]
    registration = _registration(fleet, server, Clock(limit_s=31.0))

    await registration.run()

    assert fleet.heartbeats[0][1].active_jobs == 2


async def test_draining_is_reported_at_once_not_at_the_next_beat() -> None:
    fleet, server = FakeFleet(), FakeServer()
    clock = Clock(limit_s=40.0)
    clock.on_tick = lambda now: setattr(server, "draining", now >= 5.0)
    registration = _registration(fleet, server, clock)

    await registration.run()

    statuses = [hb.status for _key, hb in fleet.heartbeats]
    assert statuses[0] == "draining"
    assert len(fleet.heartbeats) == 2, "one immediate draining beat, then the regular 30 s cadence"


async def test_an_unknown_instance_registers_again() -> None:
    fleet = FakeFleet()
    registration = _registration(fleet, FakeServer(), Clock())
    await registration.register()
    fleet.known = False

    await registration.heartbeat()

    assert len(fleet.registrations) == 2
    assert len(fleet.heartbeats) == 2, "the lost beat is re-sent after registering"


async def test_an_unreachable_api_is_retried_on_the_next_beat() -> None:
    fleet = FakeFleet(fail_register=1)
    registration = _registration(fleet, FakeServer(), Clock(limit_s=31.0))

    await registration.run()

    assert len(fleet.registrations) == 1 and len(fleet.heartbeats) == 1


async def test_an_agent_name_mismatch_is_logged_as_an_error() -> None:
    """The api dispatches under the connection's name; a different worker name gets no jobs."""
    fleet = FakeFleet(agent_name="lkap-agent-b")
    registration = _registration(fleet, FakeServer(), Clock())

    with structlog.testing.capture_logs() as logs:
        assert await registration.register()

    errors = [entry for entry in logs if entry["log_level"] == "error"]
    assert errors and errors[0]["connection_agent_name"] == "lkap-agent-b"
    assert errors[0]["worker_agent_name"] == "lkap-agent"


async def test_worker_registered_event_starts_one_loop_and_re_registration_keeps_it() -> None:
    fleet, server = FakeFleet(), FakeServer()
    registration = _registration(fleet, server, Clock(limit_s=3.0))
    registration.attach()

    server.handlers["worker_registered"]("AW_1", object())
    first = registration._task
    server.handlers["worker_registered"]("AW_1", object())
    assert registration._task is first
    assert first is not None
    with contextlib.suppress(asyncio.CancelledError):  # `close()` cancels the loop task
        await first

    assert len(fleet.registrations) == 1


def test_default_instance_key_is_hostname_pid_unless_overridden() -> None:
    settings = _settings()
    assert default_instance_key(settings) == f"{socket.gethostname()}:{os.getpid()}"
    settings.instance_key = " lkap-agent-conn-a-0 "
    assert default_instance_key(settings) == "lkap-agent-conn-a-0"


# -------------------------------------------------------------- installed providers


def test_discovered_providers_match_this_venv() -> None:
    """Under-reporting would make api validation reject providers the pool can build."""
    ids = set(discover_installed_provider_ids())

    slim_constructible = {
        "livekit-inference-stt",
        "livekit-inference-llm",
        "livekit-inference-tts",
        "google-realtime",
        "openai-realtime",
        "deepgram-stt",
        "openai-llm",
        "google-llm",
        "cartesia-tts",
        "elevenlabs-tts",
        "openai-tts",
        "bey-avatar",
        "tavus-avatar",
        "google-image-gen",
        "openai-image-gen",
    }
    assert slim_constructible <= ids
    assert {"silero-vad", "inference-vad", "inference-turn-detector"} <= ids
    assert "fastembed-embedding" not in ids, "embeddings are built by the api, not the worker"


@pytest.mark.parametrize(
    "content",
    [
        ["b", "a", "a"],
        {"installed_provider_ids": ["a", "b"]},
        {"flavor": "full", "provider_ids": ["b", "a"]},
    ],
)
def test_installed_providers_file_wins_when_present(tmp_path: Path, content: Any) -> None:
    path = tmp_path / "installed_providers.json"
    path.write_text(json.dumps(content))
    settings = _settings()
    settings.installed_providers_file = str(path)

    assert installed_provider_ids(settings) == ["a", "b"]


def test_a_missing_or_unreadable_file_falls_back_to_discovery(tmp_path: Path) -> None:
    settings = _settings()
    settings.installed_providers_file = str(tmp_path / "missing.json")
    assert installed_provider_ids(settings) == discover_installed_provider_ids()

    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    settings.installed_providers_file = str(bad)
    assert installed_provider_ids(settings) == discover_installed_provider_ids()


# ---------------------------------------------------------------- http client


@respx.mock
async def test_fleet_client_posts_with_the_service_token() -> None:
    route = respx.post(f"{API}/internal/v1/workers/register").mock(
        return_value=httpx.Response(200, json={"connection_id": "c1", "agent_name": "lkap-agent"})
    )
    client = FleetClient(API, "svc-token")

    out = await client.register(WorkerRegisterIn(instance_key="h:1"))

    assert out.connection_id == "c1"
    assert route.calls[0].request.headers["X-Service-Token"] == "svc-token"
    await client.aclose()


@respx.mock
@pytest.mark.parametrize(("status", "known"), [(204, True), (404, False)])
async def test_fleet_client_heartbeat_maps_404_to_unknown(status: int, known: bool) -> None:
    respx.post(f"{API}/internal/v1/workers/h:1/heartbeat").mock(return_value=httpx.Response(status))
    client = FleetClient(API, "svc")

    assert await client.heartbeat("h:1", WorkerHeartbeatIn()) is known
    await client.aclose()


@respx.mock
async def test_fleet_client_raises_on_server_errors() -> None:
    respx.post(f"{API}/internal/v1/workers/register").mock(return_value=httpx.Response(409))
    respx.post(f"{API}/internal/v1/workers/h:1/heartbeat").mock(side_effect=httpx.ConnectError("down"))
    client = FleetClient(API, "svc")

    with pytest.raises(FleetApiError):
        await client.register(WorkerRegisterIn(instance_key="h:1"))
    with pytest.raises(FleetApiError):
        await client.heartbeat("h:1", WorkerHeartbeatIn())
    await client.aclose()
