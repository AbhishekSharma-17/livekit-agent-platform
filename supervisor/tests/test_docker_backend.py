"""``DockerBackend`` against a fake docker client (no daemon needed)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fakes import SECRET, desired, worker_env
from lkap_supervisor.backends.base import BackendError
from lkap_supervisor.backends.docker import DockerBackend, is_loopback_url

IMAGES = {"slim": "registry.example/lkap-agent:slim-1", "full": "registry.example/lkap-agent:full-1"}


class NotFound(Exception):
    """Stands in for ``docker.errors.NotFound`` (matched by class name)."""


class FakeContainer:
    def __init__(self, cid: str, name: str, labels: dict[str, str], *, stubborn: bool = False) -> None:
        self.id = cid
        self.name = name
        self.labels = labels
        self.status = "running"
        self.attrs: dict[str, Any] = {"State": {}}
        self.stubborn = stubborn
        self.calls: list[str] = []
        self.removed = False

    def reload(self) -> None:
        self.calls.append("reload")

    def kill(self, signal: str | int | None = None) -> None:
        self.calls.append(f"kill:{signal}")
        if signal == "SIGKILL" or (signal == "SIGINT" and not self.stubborn):
            self.status = "exited"

    def wait(self, *, timeout: float | None = None) -> Any:
        self.calls.append(f"wait:{timeout}")
        if self.status == "running":
            raise TimeoutError("Read timed out")  # requests raises ReadTimeout
        return {"StatusCode": 0}

    def remove(self, *, force: bool = False) -> None:
        self.calls.append("remove")
        self.removed = True


class FakeContainers:
    def __init__(self) -> None:
        self.items: dict[str, FakeContainer] = {}
        self.run_calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_run: Exception | None = None
        self.stubborn = False

    def run(self, image: str, **kwargs: Any) -> FakeContainer:
        if self.fail_run is not None:
            raise self.fail_run
        self.run_calls.append((image, kwargs))
        container = FakeContainer(
            f"cid{len(self.items)}", kwargs["name"], kwargs["labels"], stubborn=self.stubborn
        )
        self.items[container.id] = container
        return container

    def get(self, container_id: str) -> FakeContainer:
        for container in self.items.values():
            if container_id in (container.id, container.name) and not container.removed:
                return container
        raise NotFound(container_id)

    def list(self, *, all: bool = False, filters: dict[str, Any] | None = None) -> list[FakeContainer]:
        return [c for c in self.items.values() if not c.removed]


class FakeClient:
    def __init__(self) -> None:
        self.containers = FakeContainers()
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _backend(tmp_path: Path, client: FakeClient, **kwargs: Any) -> DockerBackend:
    return DockerBackend(
        image_refs=IMAGES, state_dir=tmp_path / "state", client_factory=lambda: client, **kwargs
    )


def _public_env() -> Any:
    env = worker_env()
    env.env["LKAP_API_BASE_URL"] = "https://api.example.com"
    return env


async def test_start_runs_a_labelled_container_with_env_in_the_config_only(tmp_path: Path) -> None:
    client = FakeClient()
    backend = _backend(tmp_path, client, network="lkap_default")

    handle = await backend.start(desired(replicas=1), 0, _public_env())

    image, kwargs = client.containers.run_calls[0]
    assert image == IMAGES["slim"]
    assert kwargs["detach"] is True
    assert kwargs["stop_signal"] == "SIGINT"
    assert kwargs["restart_policy"] == {"Name": "no"}
    assert kwargs["network"] == "lkap_default"
    assert kwargs["labels"]["lkap.managed_by"] == "supervisor"
    assert kwargs["labels"]["lkap.connection_id"] == "conn-a"
    assert kwargs["labels"]["lkap.hash"] == "a" * 64
    assert kwargs["labels"]["lkap.replica_index"] == "0"
    assert kwargs["environment"]["LIVEKIT_API_SECRET"] == SECRET
    assert kwargs["environment"]["LKAP_MANAGED_BY"] == "supervisor"
    assert kwargs["environment"]["LKAP_INSTANCE_KEY"] == kwargs["name"] == handle.instance_key
    assert SECRET not in str(kwargs["labels"])
    assert handle.pid_or_container == "cid0"
    assert not list((tmp_path / "state").glob("*")) or SECRET not in "".join(
        p.read_text() for p in (tmp_path / "state").glob("*")
    )


async def test_a_loopback_api_url_is_refused_before_anything_starts(tmp_path: Path) -> None:
    client = FakeClient()
    backend = _backend(tmp_path, client)

    with pytest.raises(BackendError, match="loopback") as info:
        await backend.start(desired(replicas=1), 0, worker_env())  # LKAP_API_BASE_URL=http://127.0.0.1:8080

    assert client.containers.run_calls == []
    assert SECRET not in str(info.value)


async def test_the_worker_api_url_override_replaces_a_loopback_url(tmp_path: Path) -> None:
    client = FakeClient()
    backend = _backend(tmp_path, client, worker_api_base_url="http://api:8080")

    await backend.start(desired(replicas=1), 0, worker_env())

    assert client.containers.run_calls[0][1]["environment"]["LKAP_API_BASE_URL"] == "http://api:8080"


async def test_a_daemon_error_never_echoes_the_payload(tmp_path: Path) -> None:
    client = FakeClient()
    client.containers.fail_run = RuntimeError(f"500 Server Error: bad env {SECRET}")
    backend = _backend(tmp_path, client)

    with pytest.raises(BackendError) as info:
        await backend.start(desired(replicas=1), 0, _public_env())

    assert SECRET not in str(info.value)


async def test_an_unreachable_daemon_is_a_backend_error(tmp_path: Path) -> None:
    def boom() -> FakeClient:
        raise ConnectionRefusedError("docker.sock")

    backend = DockerBackend(image_refs=IMAGES, state_dir=tmp_path, client_factory=boom)

    with pytest.raises(BackendError, match="Docker daemon"):
        await backend.list()


async def test_list_maps_container_states(tmp_path: Path) -> None:
    client = FakeClient()
    backend = _backend(tmp_path, client)
    running = await backend.start(desired(replicas=3), 0, _public_env())
    crashed = await backend.start(desired(replicas=3), 1, _public_env())
    created = await backend.start(desired(replicas=3), 2, _public_env())
    client.containers.get(crashed.instance_key).status = "exited"
    client.containers.get(created.instance_key).status = "created"

    states = {h.instance_key: h.state for h in await backend.list()}

    assert states == {
        running.instance_key: "running",
        crashed.instance_key: "failed",
        created.instance_key: "starting",
    }
    assert {h.replica_index for h in await backend.list()} == {0, 1, 2}


async def test_drain_sends_sigint_waits_and_removes(tmp_path: Path) -> None:
    client = FakeClient()
    backend = _backend(tmp_path, client)
    handle = await backend.start(desired(replicas=1), 0, _public_env())
    container = client.containers.get(handle.instance_key)

    await backend.drain(handle, grace_s=20.0)

    assert container.calls[:2] == ["kill:SIGINT", "wait:20.0"]
    assert "kill:SIGKILL" not in container.calls
    assert container.removed
    assert await backend.list() == []


async def test_drain_sigkills_only_after_the_grace(tmp_path: Path) -> None:
    client = FakeClient()
    client.containers.stubborn = True
    backend = _backend(tmp_path, client)
    handle = await backend.start(desired(replicas=1), 0, _public_env())
    container = client.containers.get(handle.instance_key)

    await backend.drain(handle, grace_s=15.0)

    kills = [c for c in container.calls if c.startswith("kill")]
    assert kills == ["kill:SIGINT", "kill:SIGKILL"]
    assert container.calls.index("wait:15.0") < container.calls.index("kill:SIGKILL")
    assert container.removed


async def test_a_restarted_supervisor_resumes_a_drain_without_a_second_sigint(tmp_path: Path) -> None:
    client = FakeClient()
    client.containers.stubborn = True
    first = _backend(tmp_path, client)
    handle = await first.start(desired(replicas=1), 0, _public_env())
    container = client.containers.get(handle.instance_key)
    first._draining.add(handle.instance_key)  # SIGINT was sent, then the supervisor died
    first._save_draining()
    container.kill("SIGINT")

    second = _backend(tmp_path, client)
    listed = await second.list()
    await second.drain(listed[0], grace_s=15.0)

    assert [h.state for h in listed] == ["draining"]
    assert [c for c in container.calls if c.startswith("kill")] == ["kill:SIGINT", "kill:SIGKILL"]
    assert await second.health(handle) is False


async def test_health_reports_unhealthy_and_draining_containers(tmp_path: Path) -> None:
    client = FakeClient()
    backend = _backend(tmp_path, client)
    handle = await backend.start(desired(replicas=1), 0, _public_env())
    container = client.containers.get(handle.instance_key)

    assert await backend.health(handle) is True
    container.attrs = {"State": {"Health": {"Status": "unhealthy"}}}
    assert await backend.health(handle) is False


@pytest.mark.parametrize(
    ("url", "loopback"),
    [
        ("http://127.0.0.1:8080", True),
        ("http://localhost:8080", True),
        ("http://[::1]:8080", True),
        ("https://api.example.com", False),
        ("http://api:8080", False),
        ("http://10.0.0.5:8080", False),
    ],
)
def test_is_loopback_url(url: str, loopback: bool) -> None:
    assert is_loopback_url(url) is loopback
