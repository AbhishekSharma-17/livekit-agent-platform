"""The request-body ingress cap (V5-27, S5-12).

FastAPI parses a multipart body (spooling files to temporary disk) before any
dependency runs, so authentication and the per-route size checks come after the
bytes are already in. :class:`BodySizeLimitMiddleware` bounds every HTTP request
body before routing: a declared ``Content-Length`` over the cap is answered 413
without reading a byte, and a body without one (chunked) is counted as it
streams and cut off with 413 at the first byte past the cap. The proxy
(``deploy/Caddyfile``) enforces the same cap in front of the api.
"""

from __future__ import annotations

import json
from typing import Final

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: The largest request body the api accepts: a 25 MB upload plus multipart overhead.
MAX_REQUEST_BODY_BYTES: Final = 26 * 1024 * 1024

_MESSAGE: Final = "the request body is larger than the api accepts"


class RequestBodyTooLargeError(HTTPException):
    """413: a request body passed :data:`MAX_REQUEST_BODY_BYTES` while it was read."""

    def __init__(self, max_bytes: int) -> None:
        """Remember the cap for the error envelope."""
        super().__init__(status_code=413, detail=_MESSAGE)
        self.max_bytes = max_bytes


def error_body(max_bytes: int) -> dict[str, object]:
    """The CONTRACTS §7 error envelope of a 413 from this cap."""
    return {"error": {"code": "payload_too_large", "message": _MESSAGE, "details": {"max_bytes": max_bytes}}}


class BodySizeLimitMiddleware:
    """Refuse request bodies over ``max_bytes`` before routing, authentication or parsing."""

    def __init__(self, app: ASGIApp, *, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        """Wrap ``app``."""
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Check the declared length, then count what is actually received."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _content_length(scope)
        if declared is not None and declared > self.max_bytes:
            await _send_413(send, self.max_bytes)
            return

        received = 0

        async def counted_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise RequestBodyTooLargeError(self.max_bytes)
            return message

        await self.app(scope, counted_receive, send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", ()):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _send_413(send: Send, max_bytes: int) -> None:
    body = json.dumps(error_body(max_bytes)).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


__all__ = [
    "MAX_REQUEST_BODY_BYTES",
    "BodySizeLimitMiddleware",
    "RequestBodyTooLargeError",
    "error_body",
]
