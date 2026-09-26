"""The internal, typed ``/v1`` client (D-V3-2). Not a published SDK.

* bearer API key; ``X-Workspace`` only from ``LKAP_WORKSPACE`` (R-V3-5);
* ``X-LKAP-Client: lkap-mcp/<version>; client=<name>; tool=<tool>; call=<uuid>``
  on every request (D-V3-9, R-V3-11), from the :data:`CURRENT_CALL` context;
* at most ``LKAP_MCP_MAX_IN_FLIGHT`` (4) requests in flight;
* one retry on ``429``, honouring ``details.retry_after_s``;
* api errors raise :class:`ApiFailure`, which the registry turns into an
  ``ok=false`` :class:`~lkap_mcp.results.ToolResult`;
* logs only method, path, status and duration: never a header, body or query.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

import httpx
from lkap_contracts.common import Issue
from pydantic import BaseModel

from lkap_mcp import __version__
from lkap_mcp.results import ToolResult
from lkap_mcp.settings import McpSettings

log = logging.getLogger(__name__)

Method = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
ModelT = TypeVar("ModelT", bound=BaseModel)

PRODUCT = "lkap-mcp"
_HEADER_VALUE = re.compile(r"[^A-Za-z0-9._@+:/-]")

#: Hints the tools add to well-known api error codes (§4 conventions).
ERROR_HINTS: dict[str, str] = {
    "blocked_destination": "the api's network guard refused this host; public hosts only",
    "unauthorized": (
        "the API key is missing, revoked or expired; mint a new agent key in the console "
        "(Settings -> AI agents)"
    ),
    "rate_limited": "the api rate-limited this key; wait and retry",
    "api_unreachable": "check LKAP_API_URL and that the api is running",
    "unexpected_redirect": "nothing was changed; check the id or path (an empty id ends the path in '/')",
}


@dataclass(frozen=True)
class CallInfo:
    """Attribution of the tool call in progress (read by every request it makes)."""

    client: str | None = None
    tool: str | None = None
    call: str | None = None


#: Set by the registry around each tool call.
CURRENT_CALL: ContextVar[CallInfo | None] = ContextVar("lkap_mcp_current_call", default=None)


def header_safe(value: str, *, limit: int = 64) -> str:
    """Make a value safe inside ``X-LKAP-Client`` (no ``;``/``=``, ≤ 64 chars)."""
    return _HEADER_VALUE.sub("-", value.strip())[:limit] or "unknown"


def client_header(info: CallInfo | None) -> str:
    """Render ``X-LKAP-Client`` for a call."""
    parts = [f"{PRODUCT}/{__version__}"]
    if info is not None:
        if info.client:
            parts.append(f"client={header_safe(info.client)}")
        if info.tool:
            parts.append(f"tool={header_safe(info.tool)}")
        if info.call:
            parts.append(f"call={header_safe(info.call)}")
    return "; ".join(parts)


class ApiFailure(Exception):
    """An api error (or an unreachable api), already in the api's ``{code, message, details}`` shape."""

    def __init__(self, status: int | None, code: str, message: str, details: Any = None) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    @property
    def hint(self) -> str | None:
        """A short, static next step for well-known codes."""
        details = self.details if isinstance(self.details, dict) else {}
        if self.status == 403 and details.get("required_scope"):
            return f"this API key lacks the '{details['required_scope']}' scope"
        return ERROR_HINTS.get(self.code) or ERROR_HINTS.get(str(details.get("reason") or ""))

    def to_result(self) -> ToolResult:
        """The ``ok=false`` result for this failure (issues lifted from ``details``)."""
        issues: list[Issue] = []
        if isinstance(self.details, dict):
            for raw in self.details.get("issues") or []:
                try:
                    issues.append(Issue.model_validate(raw))
                except ValueError:
                    continue
        return ToolResult.fail(
            self.code,
            self.message,
            status=self.status,
            details=self.details,
            hint=self.hint,
            issues=issues,
        )


def _status_code(status: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        413: "payload_too_large",
        415: "unsupported_media_type",
        422: "unprocessable_entity",
        429: "rate_limited",
    }.get(status, "api_error")


def failure_from_response(response: httpx.Response) -> ApiFailure:
    """Map an error response to :class:`ApiFailure` (CONTRACTS §7 envelope, or FastAPI's ``detail``)."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        error = body["error"]
        return ApiFailure(
            response.status_code,
            str(error.get("code") or _status_code(response.status_code)),
            str(error.get("message") or response.reason_phrase),
            error.get("details"),
        )
    if isinstance(body, dict) and "detail" in body:
        detail = body["detail"]
        message = detail if isinstance(detail, str) else "request validation failed"
        return ApiFailure(response.status_code, _status_code(response.status_code), message, detail)
    return ApiFailure(response.status_code, _status_code(response.status_code), response.reason_phrase)


def redirect_failure(method: str, path: str, response: httpx.Response) -> ApiFailure:
    """A ``3xx`` answer to a write: the api did not perform it (ask #103).

    The client never follows redirects, so a write answered with a redirect (for
    example FastAPI's ``307`` from ``DELETE /v1/tools/`` to ``/v1/tools``) changed
    nothing, and must not read as success.
    """
    location = response.headers.get("location")
    where = f" to {location}" if location else ""
    return ApiFailure(
        response.status_code,
        "unexpected_redirect",
        f"the api answered {method} {path} with {response.status_code} {response.reason_phrase}{where}; "
        "the request was not carried out",
        {"location": location} if location else None,
    )


class LkapClient:
    """An ``httpx.AsyncClient`` wrapper for the LKAP ``/v1`` api."""

    def __init__(
        self,
        settings: McpSettings,
        *,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        key = (
            api_key
            if api_key is not None
            else (settings.api_key.get_secret_value() if settings.api_key else None)
        )
        headers = {"Accept": "application/json", "User-Agent": f"{PRODUCT}/{__version__}"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if settings.workspace:
            headers["X-Workspace"] = settings.workspace
        self._http = httpx.AsyncClient(
            base_url=settings.api_url,
            headers=headers,
            timeout=settings.request_timeout_s,
            transport=transport,
            # Never follow a redirect: a write re-sent to another URL is not the write
            # that was asked for, and a 3xx to a write is reported as a failure (#103).
            follow_redirects=False,
        )
        self._slots = asyncio.Semaphore(settings.max_in_flight)
        self._openapi: dict[str, Any] | None = None

    @property
    def base_url(self) -> str:
        """The api origin this client talks to."""
        return self.settings.api_url

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def request(
        self,
        method: Method,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        data: dict[str, str] | None = None,
    ) -> Any:
        """Send one request; return the parsed JSON body (``None`` for an empty body).

        Raises:
            ApiFailure: A ``4xx``/``5xx`` answer (after one ``429`` retry), a ``3xx``
                answer to a write (``POST``/``PUT``/``PATCH``/``DELETE``), or an unreachable api.
        """
        clean_params = {k: v for k, v in (params or {}).items() if v is not None} or None
        response = await self._send(method, path, clean_params, json, files, data)
        if response.status_code == 429:
            failure = failure_from_response(response)
            await asyncio.sleep(self._retry_after(failure.details))
            response = await self._send(method, path, clean_params, json, files, data)
        if response.status_code >= 400:
            raise failure_from_response(response)
        if method != "GET" and 300 <= response.status_code < 400:
            raise redirect_failure(method, path, response)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    def _retry_after(self, details: Any) -> float:
        value: Any = details.get("retry_after_s") if isinstance(details, dict) else None
        try:
            wait = float(value) if value is not None else 1.0
        except (TypeError, ValueError):
            wait = 1.0
        return max(0.0, min(wait, self.settings.max_retry_after_s))

    async def _send(
        self,
        method: Method,
        path: str,
        params: dict[str, Any] | None,
        json: Any,
        files: dict[str, tuple[str, bytes, str]] | None,
        data: dict[str, str] | None,
    ) -> httpx.Response:
        headers = {"X-LKAP-Client": client_header(CURRENT_CALL.get())}
        started = time.monotonic()
        async with self._slots:
            try:
                response = await self._http.request(
                    method, path, params=params, json=json, files=files, data=data, headers=headers
                )
            except httpx.HTTPError as exc:
                log.debug(
                    "api_request_failed",
                    extra={"method": method, "path": path, "error": type(exc).__name__},
                )
                raise ApiFailure(
                    None,
                    "api_unreachable",
                    f"cannot reach the LKAP api at {self.base_url} ({type(exc).__name__})",
                ) from None
        log.debug(
            "api_request",
            extra={
                "method": method,
                "path": path,
                "status": response.status_code,
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            },
        )
        return response

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        """``GET`` a path."""
        return await self.request("GET", path, params=params)

    async def post(self, path: str, json: Any = None, *, params: dict[str, Any] | None = None) -> Any:
        """``POST`` a JSON body."""
        return await self.request("POST", path, json=json, params=params)

    async def put(self, path: str, json: Any = None) -> Any:
        """``PUT`` a JSON body."""
        return await self.request("PUT", path, json=json)

    async def patch(self, path: str, json: Any = None) -> Any:
        """``PATCH`` a JSON body."""
        return await self.request("PATCH", path, json=json)

    async def delete(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        """``DELETE`` a path."""
        return await self.request("DELETE", path, params=params)

    async def get_model(
        self, path: str, model: type[ModelT], *, params: dict[str, Any] | None = None
    ) -> ModelT:
        """``GET`` a path and validate it as ``model``."""
        return model.model_validate(await self.get(path, params=params))

    async def items(self, path: str, *, params: dict[str, Any] | None = None) -> list[Any]:
        """``GET`` a ``Page[T]`` route and return its ``items``."""
        body = await self.get(path, params=params)
        if isinstance(body, dict) and isinstance(body.get("items"), list):
            return list(body["items"])
        return list(body) if isinstance(body, list) else []

    async def openapi(self) -> dict[str, Any]:
        """The api's OpenAPI document (cached for the process)."""
        if self._openapi is None:
            body = await self.get("/openapi.json")
            self._openapi = body if isinstance(body, dict) else {}
        return self._openapi
