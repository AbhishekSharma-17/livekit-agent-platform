"""Outbound HTTP guard (REVIEW-FINAL F-14): private-range deny-list and intersection semantics."""

from __future__ import annotations

from typing import Any

import httpcore
import httpx
import pytest
import respx
from livekit.agents import ToolError

from lkap_agent.tools._http_safety import (
    GuardedNetworkBackend,
    GuardedTransport,
    HttpToolSecurityError,
    check_url_allowed,
    effective_allowlist,
    is_private_host,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.1/admin",
        "http://172.16.4.2/",
        "http://192.168.1.10:8080/x",
        "http://127.0.0.1:8080/internal/v1/sessions",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.7/",
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[fd00::1]/",
        "http://[::ffff:10.0.0.1]/",
        "http://localhost:8080/",
        "http://api.localhost/",
        "http://metadata.google.internal/computeMetadata/v1/",
    ],
)
def test_private_targets_are_refused_even_when_allowlisted(url: str) -> None:
    host = httpx.URL(url).host
    with pytest.raises(HttpToolSecurityError, match="private or local"):
        check_url_allowed(url, tool_allowed_hosts=[host], platform_allowed_hosts=[host])


@pytest.mark.parametrize("host", ["8.8.8.8", "api.example.com", "10.example.com", "0xabc.example.com"])
def test_public_hosts_are_not_private(host: str) -> None:
    assert is_private_host(host) is False


@pytest.mark.parametrize("host", ["2130706433", "0x7f000001", "127.1", "0", "127.0.0.0x1", "2130706433."])
def test_numeric_hosts_are_refused_before_any_resolution(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """V2-22 / R2-39: non-canonical numeric forms never reach `getaddrinfo`, even when allowlisted."""

    def _no_dns(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the offline check must not resolve anything")

    monkeypatch.setattr("socket.getaddrinfo", _no_dns)
    url = f"http://{host}/latest/meta-data/"

    with pytest.raises(HttpToolSecurityError, match="private or local"):
        check_url_allowed(url, tool_allowed_hosts=[host], platform_allowed_hosts=[host])


@pytest.mark.parametrize(
    ("tool", "platform", "expected"),
    [
        (["a.com", "b.com"], ["b.com", "c.com"], {"b.com"}),
        (["A.com"], [], {"a.com"}),
        ([], ["b.com"], {"b.com"}),
        (None, ["b.com"], {"b.com"}),
        ([], [], set()),
        (None, None, set()),
    ],
)
def test_effective_allowlist_is_the_intersection_of_every_set_list(
    tool: list[str] | None, platform: list[str] | None, expected: set[str]
) -> None:
    assert effective_allowlist(tool, platform) == expected


def test_a_platform_list_is_a_ceiling_a_tool_cannot_widen() -> None:
    """v1's union let an admin tool reach any host; v2 refuses what the platform did not allow."""
    with pytest.raises(HttpToolSecurityError, match="allowlist"):
        check_url_allowed(
            "https://evil.example.net/x",
            tool_allowed_hosts=["evil.example.net"],
            platform_allowed_hosts=["api.example.com"],
        )


def test_a_host_on_both_lists_is_allowed() -> None:
    check_url_allowed(
        "https://api.example.com/x",
        tool_allowed_hosts=["api.example.com"],
        platform_allowed_hosts=["api.example.com"],
    )


@respx.mock
async def test_builtin_http_request_to_a_private_address_is_refused_even_if_allowlisted() -> None:
    """PLAN-V2 V2-07 acceptance: `http_request` to 10.0.0.1 is refused even if allowlisted."""
    from types import SimpleNamespace

    from lkap_agent.tools.builtin.http_request import build_http_request_tool

    route = respx.get("http://10.0.0.1/status").mock(return_value=httpx.Response(200, text="up"))
    ctx: Any = SimpleNamespace(log=SimpleNamespace(debug=lambda *a, **k: None))
    tool = build_http_request_tool(ctx, platform_allowed_hosts=["10.0.0.1"])
    run_ctx: Any = SimpleNamespace(function_call=SimpleNamespace(call_id="c1"))

    with pytest.raises(ToolError, match="private or local"):
        await tool(context=run_ctx, method="GET", url="http://10.0.0.1/status")
    assert not route.called


# ------------------------------------------------ V2-21: resolve-and-pin transport
class _RecordingBackend(httpcore.AsyncNetworkBackend):
    """Stands in for httpcore's socket backend; records where a connection would go."""

    def __init__(self) -> None:
        self.connected: list[tuple[str, int]] = []

    async def connect_tcp(self, host: str, port: int, **_: Any) -> httpcore.AsyncNetworkStream:
        self.connected.append((host, port))
        raise httpcore.ConnectError("recorded, not connected")

    async def sleep(self, seconds: float) -> None:  # pragma: no cover - unused
        return None


def _resolver(answers: dict[str, list[str]]) -> Any:
    async def resolve(host: str, port: int) -> list[str]:
        return answers[host]

    return resolve


@pytest.mark.parametrize(
    "answers",
    [
        ["169.254.169.254"],  # a public-looking name that points at cloud metadata
        ["93.184.216.34", "10.0.0.5"],  # one inward answer poisons the whole name
        ["::ffff:127.0.0.1"],  # IPv4-mapped loopback
        ["fd00:ec2::254"],
    ],
)
async def test_guarded_backend_refuses_names_that_resolve_inward(answers: list[str]) -> None:
    inner = _RecordingBackend()
    backend = GuardedNetworkBackend(resolve=_resolver({"rebind.example": answers}), inner=inner)

    with pytest.raises(httpcore.ConnectError, match="private or local"):
        await backend.connect_tcp("rebind.example", 443)

    assert inner.connected == []


async def test_guarded_backend_connects_to_the_checked_address_not_the_name() -> None:
    inner = _RecordingBackend()
    backend = GuardedNetworkBackend(resolve=_resolver({"api.example": ["93.184.216.34"]}), inner=inner)

    with pytest.raises(httpcore.ConnectError, match="recorded"):
        await backend.connect_tcp("api.example", 443)

    assert inner.connected == [("93.184.216.34", 443)]


async def test_guarded_transport_turns_a_rebound_name_into_a_tool_error() -> None:
    client = httpx.AsyncClient(
        transport=GuardedTransport(
            resolve=_resolver({"allowed.example": ["127.0.0.1"]}), inner=_RecordingBackend()
        )
    )

    with pytest.raises(httpx.ConnectError, match="private or local"):
        await client.get("http://allowed.example/status")
    await client.aclose()


def test_tool_clients_are_built_with_the_guarded_transport() -> None:
    import inspect

    from lkap_agent.tools import declarative
    from lkap_agent.tools.builtin import http_request

    for module in (declarative, http_request):
        assert "transport=guarded_transport()" in inspect.getsource(module), module.__name__
