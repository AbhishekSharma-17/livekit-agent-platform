"""The typed api client: attribution header, 429 retry, error mapping, what it logs."""

from __future__ import annotations

import logging

import httpx
import pytest

from lkap_mcp.client import CURRENT_CALL, ApiFailure, CallInfo, LkapClient, client_header
from lkap_mcp.settings import McpSettings

RAW_KEY = "lkap_test_0123456789abcdef"


def _client(handler: httpx.MockTransport, **overrides: object) -> LkapClient:
    return LkapClient(McpSettings(api_url="http://api.test", **overrides), api_key=RAW_KEY, transport=handler)


async def test_request_429_with_retry_after_is_retried_once() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(
                429,
                json={
                    "error": {
                        "code": "rate_limited",
                        "message": "slow down",
                        "details": {"retry_after_s": 0.1},
                    }
                },
            )
        return httpx.Response(200, json={"ok": True})

    client = _client(httpx.MockTransport(handler))
    try:
        assert await client.get("/v1/health") == {"ok": True}
    finally:
        await client.aclose()

    assert len(seen) == 2


async def test_request_second_429_raises_rate_limited() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"error": {"code": "rate_limited", "message": "slow", "details": {"retry_after_s": 0}}}
        )

    client = _client(httpx.MockTransport(handler))
    try:
        with pytest.raises(ApiFailure) as caught:
            await client.get("/v1/health")
    finally:
        await client.aclose()

    assert (caught.value.status, caught.value.code) == (429, "rate_limited")


async def test_request_sends_bearer_workspace_and_client_header_for_the_current_call() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    client = _client(httpx.MockTransport(handler), workspace="ws-a")
    token = CURRENT_CALL.set(CallInfo(client="claude code;x=1", tool="agent_create", call="abc"))
    try:
        assert await client.delete("/v1/agents/a") is None
    finally:
        CURRENT_CALL.reset(token)
        await client.aclose()

    headers = seen[0].headers
    assert headers["Authorization"] == f"Bearer {RAW_KEY}"
    assert headers["X-Workspace"] == "ws-a"
    assert headers["X-LKAP-Client"] == "lkap-mcp/0.1.0; client=claude-code-x-1; tool=agent_create; call=abc"


def test_client_header_without_a_call_names_only_the_product() -> None:
    assert client_header(None) == "lkap-mcp/0.1.0"


async def test_request_error_envelope_and_fastapi_detail_map_to_api_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a":
            return httpx.Response(
                403,
                json={
                    "error": {
                        "code": "forbidden",
                        "message": "no",
                        "details": {"required_scope": "audit:read"},
                    }
                },
            )
        return httpx.Response(422, json={"detail": [{"loc": ["body"], "msg": "bad"}]})

    client = _client(httpx.MockTransport(handler))
    try:
        with pytest.raises(ApiFailure) as first:
            await client.get("/a")
        with pytest.raises(ApiFailure) as second:
            await client.get("/b")
    finally:
        await client.aclose()

    assert first.value.hint == "this API key lacks the 'audit:read' scope"
    assert (second.value.status, second.value.code) == (422, "unprocessable_entity")


async def test_request_unreachable_api_is_api_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = _client(httpx.MockTransport(handler))
    try:
        with pytest.raises(ApiFailure) as caught:
            await client.get("/v1/health")
    finally:
        await client.aclose()

    assert caught.value.code == "api_unreachable" and caught.value.status is None


async def test_request_logs_method_path_status_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="lkap_mcp")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"api_secret": "never-logged-value"})

    client = _client(httpx.MockTransport(handler))
    try:
        await client.post("/v1/connections", {"api_secret": "never-logged-value"}, params={"q": "x"})
    finally:
        await client.aclose()

    records = [r for r in caplog.records if r.name == "lkap_mcp.client"]
    assert records and records[0].__dict__["path"] == "/v1/connections"
    text = " ".join(repr(r.__dict__) for r in caplog.records)
    assert "never-logged-value" not in text and RAW_KEY not in text


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
@pytest.mark.parametrize("status", [300, 301, 302, 303, 307, 308])
async def test_request_a_3xx_to_a_write_is_a_failure_and_is_not_followed(method: str, status: int) -> None:
    """Ask #103: `DELETE /v1/tools/` got a 307 and `lkap_delete` reported a deletion."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, headers={"location": "http://api.test/v1/tools"})

    client = _client(httpx.MockTransport(handler))
    try:
        with pytest.raises(ApiFailure) as caught:
            await client.request(method, "/v1/tools/")  # type: ignore[arg-type]
    finally:
        await client.aclose()

    assert (caught.value.status, caught.value.code) == (status, "unexpected_redirect")
    assert "not carried out" in caught.value.message
    assert caught.value.details == {"location": "http://api.test/v1/tools"}
    assert caught.value.to_result().ok is False
    assert [r.url.path for r in seen] == ["/v1/tools/"], "the redirect is never followed"
