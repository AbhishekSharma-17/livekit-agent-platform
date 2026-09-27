"""V5-27 hardening of the api's ingress (docs/v5/SECURITY-REVIEW-V5.md S5-12)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI
from starlette.types import Message

from lkap_api.body_limit import MAX_REQUEST_BODY_BYTES


async def test_oversized_content_length_is_413_before_auth_and_parsing(app: FastAPI) -> None:
    # Raw ASGI: a declared length over the cap is answered before one body byte is read.
    reads: list[Message] = []
    sent: list[Message] = []

    async def receive() -> Message:
        reads.append({"type": "http.request"})
        raise AssertionError("the body must not be read")

    async def send(message: Message) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/knowledge-bases/any/documents",
        "raw_path": b"/v1/knowledge-bases/any/documents",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"test"),
            (b"content-type", b"multipart/form-data; boundary=x"),
            (b"content-length", str(MAX_REQUEST_BODY_BYTES + 1).encode()),
        ],
        "client": ("203.0.113.9", 1234),
        "server": ("test", 80),
    }
    await app(scope, receive, send)
    assert reads == []
    assert sent[0]["status"] == 413  # not 401: no authentication ran
    assert b"payload_too_large" in sent[1]["body"]


async def test_a_body_at_the_cap_is_not_refused_by_the_ingress_check(client: httpx.AsyncClient) -> None:
    # Unauthenticated and small: the request reaches authentication (401), not the cap.
    response = await client.post("/v1/knowledge-bases", json={"name": "x"})
    assert response.status_code == 401


async def test_chunked_body_over_the_cap_is_cut_off(client: httpx.AsyncClient, app: FastAPI) -> None:
    sent_bytes = 0
    chunk = b"0" * (1024 * 1024)

    async def body() -> AsyncIterator[bytes]:
        nonlocal sent_bytes
        yield b'{"name": "'
        for _ in range(MAX_REQUEST_BODY_BYTES // len(chunk) + 4):
            sent_bytes += len(chunk)
            yield chunk
        yield b'"}'

    # No Content-Length: httpx streams it chunked, so only the running count can stop it.
    response = await client.post(
        "/v1/knowledge-bases", content=body(), headers={"content-type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert response.json()["error"]["details"] == {"max_bytes": MAX_REQUEST_BODY_BYTES}


async def test_chunked_multipart_upload_over_the_cap_is_cut_off_before_auth(
    client: httpx.AsyncClient,
) -> None:
    chunk = b"0" * (1024 * 1024)

    async def body() -> AsyncIterator[bytes]:
        yield (
            b'--x\r\nContent-Disposition: form-data; name="file"; filename="a.md"\r\n'
            b"Content-Type: text/markdown\r\n\r\n"
        )
        for _ in range(MAX_REQUEST_BODY_BYTES // len(chunk) + 4):
            yield chunk
        yield b"\r\n--x--\r\n"

    response = await client.post(
        "/v1/knowledge-bases/any/documents",
        content=body(),
        headers={"content-type": "multipart/form-data; boundary=x"},
    )
    assert response.status_code == 413  # not 401: the cap ran before authentication
