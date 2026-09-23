"""Outbound HTTP guard (REVIEW-FINAL F-14): private-range deny-list and intersection semantics."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from livekit.agents import ToolError

from lkap_agent.tools._http_safety import (
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


@pytest.mark.parametrize("host", ["8.8.8.8", "api.example.com", "10.example.com"])
def test_public_hosts_are_not_private(host: str) -> None:
    assert is_private_host(host) is False


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
