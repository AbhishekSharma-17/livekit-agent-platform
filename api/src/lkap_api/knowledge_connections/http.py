"""One vendor HTTP call for every knowledge connector (V5-20).

Every Qdrant, Pinecone, Weaviate, Cohere and Voyage request goes through
:class:`VendorHttp` on an ``httpx.AsyncClient`` the caller supplies: in the
api that is :func:`lkap_api.net_guard.guarded_http_client` (private and
metadata destinations refused at connect time against the resolved address,
redirects never followed, environment proxies off); tests pass one backed by
``httpx.MockTransport``.

* **Timeouts.** Every request has one (``timeout_s``, 10 s by default; the
  search path passes less so it fits the per-knowledge-base budget).
* **Retries.** Only when the caller asks (``retries``): writes are idempotent
  (upserts by id, deletes by filter) and retry on a timeout, a connection
  error, 429 and 502/503/504, with a short backoff that honours
  ``Retry-After`` up to :data:`MAX_BACKOFF_S`. Queries never retry: they must
  answer inside the search's per-knowledge-base timeout.
* **Redirects** are an error (a 3xx is never followed, and says so).
* **Errors** are :class:`ConnectorError` with a scrubbed message (no URLs, no
  key, capped), a ``status`` and flags the service maps to a connection
  ``status``/``last_error``. Nothing here logs a body, a key or a URL; the log
  line names the vendor, the method and the status only.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from typing import Any, Final

import httpx

from lkap_api.logging import get_logger
from lkap_api.net_guard import blocked_cause

log = get_logger(__name__)

#: Per-request timeout for control and write calls (CONTRACTS-V2 D-V2-9: vendor calls get 10 s).
TIMEOUT_S: Final = 10.0
#: The longest one backoff waits, whatever ``Retry-After`` says.
MAX_BACKOFF_S: Final = 2.0
#: The first backoff; doubled per attempt.
BASE_BACKOFF_S: Final = 0.25
#: Statuses a write retries on.
RETRY_STATUSES: Final = frozenset({429, 502, 503, 504})
#: The longest vendor text a message keeps.
MAX_MESSAGE_CHARS: Final = 200

_URL_RE = re.compile(r"(?:https?|wss?)://\S+", re.IGNORECASE)
#: Anything shaped like a key: long runs of key characters (vendor errors sometimes echo a prefix).
_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")


def scrub(text: object, *, limit: int = MAX_MESSAGE_CHARS) -> str:
    """Vendor text made safe to store and show: URLs and key-shaped tokens removed, capped."""
    cleaned = _URL_RE.sub("[link removed]", str(text or ""))
    cleaned = _TOKEN_RE.sub("[redacted]", cleaned)
    return " ".join(cleaned.split())[:limit]


class ConnectorError(Exception):
    """A knowledge connector call failed; ``message`` is safe to store in ``last_error`` and show."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        auth: bool = False,
        not_found: bool = False,
        retryable: bool = False,
    ) -> None:
        """Create the error (``message`` must already be scrubbed)."""
        super().__init__(message)
        self.message = message
        self.status = status
        self.auth = auth
        self.not_found = not_found
        self.retryable = retryable


def _vendor_message(vendor: str, response: httpx.Response) -> str:
    detail = ""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, Mapping):
        for key in ("message", "error", "detail", "status"):
            value = body.get(key)
            if isinstance(value, Mapping):
                value = value.get("message") or value.get("error")
            if isinstance(value, list) and value and isinstance(value[0], Mapping):
                value = value[0].get("message")
            if isinstance(value, str) and value.strip():
                detail = value
                break
    head = f"{vendor} answered HTTP {response.status_code}"
    return scrub(f"{head}: {detail}" if detail else head)


def error_for(vendor: str, response: httpx.Response) -> ConnectorError:
    """Map a vendor's error response to a :class:`ConnectorError`."""
    status = response.status_code
    if 300 <= status < 400:
        return ConnectorError(
            f"{vendor} answered with a redirect (HTTP {status}). Redirects are not followed. "
            "Use the service's final address",
            status=status,
        )
    message = _vendor_message(vendor, response)
    return ConnectorError(
        message,
        status=status,
        auth=status in (401, 403),
        not_found=status == 404,
        retryable=status in RETRY_STATUSES,
    )


def _retry_after(response: httpx.Response | None, attempt: int) -> float:
    backoff: float = BASE_BACKOFF_S * float(2**attempt)
    if response is not None:
        raw = response.headers.get("retry-after")
        if raw is not None:
            try:
                backoff = float(raw)
            except ValueError:
                pass
    return max(0.0, min(backoff, MAX_BACKOFF_S))


class VendorHttp:
    """JSON calls to one vendor base url with fixed headers (see the module docstring)."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        vendor: str,
        base_url: str,
        headers: Mapping[str, str] | None = None,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        """Bind the helper to a client, a base url and the auth headers.

        Args:
            client: A guarded client (or a mock-transport one in tests).
            vendor: The vendor's display name, used in messages.
            base_url: Every ``path`` is appended to it.
            headers: Sent on every call (the key header lives here, never in a log).
            timeout_s: The default per-request timeout.
        """
        self._client = client
        self.vendor = vendor
        self._base = base_url.rstrip("/")
        self._headers = {"accept": "application/json", **(headers or {})}
        self._timeout = timeout_s
        # V5-45: the exact header secrets, removed from any vendor message (a short key would
        # slip past `scrub`'s key-shaped pattern when a vendor echoes it back).
        self._secrets = sorted(
            {
                part
                for value in (headers or {}).values()
                for part in (value, value.removeprefix("Bearer ").strip())
                if len(part) >= 8
            },
            key=len,
            reverse=True,
        )

    def _redact(self, error: ConnectorError) -> ConnectorError:
        message = error.message
        for secret in self._secrets:
            message = message.replace(secret, "[redacted]")
        if message != error.message:
            error.message = message
            error.args = (message,)
        return error

    async def call(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        retries: int = 0,
        timeout_s: float | None = None,
        ok_statuses: frozenset[int] = frozenset(),
    ) -> Any:
        """Send one request and return the decoded JSON body (``{}`` when there is none).

        Args:
            method: The HTTP method.
            path: Appended to the base url (starts with ``/``).
            json: The JSON body.
            params: Query parameters.
            retries: How many times a retryable failure is retried (writes only).
            timeout_s: This request's timeout; defaults to the helper's.
            ok_statuses: Error statuses to treat as success with an empty body
                (a delete of something already gone answers 404).

        Raises:
            ConnectorError: The call failed, with a scrubbed message.
        """
        attempt = 0
        while True:
            response: httpx.Response | None = None
            try:
                response = await self._client.request(
                    method,
                    f"{self._base}{path}",
                    json=json,
                    params=dict(params) if params else None,
                    headers=self._headers,
                    timeout=timeout_s if timeout_s is not None else self._timeout,
                )
            except httpx.TimeoutException as exc:
                error = ConnectorError(f"{self.vendor} did not answer in time", retryable=True)
                error.__cause__ = exc
            except httpx.HTTPError as exc:
                blocked = blocked_cause(exc)
                if blocked is not None:
                    raise ConnectorError(
                        f"the address of this {self.vendor} service is not allowed ({scrub(blocked)})"
                    ) from exc
                error = ConnectorError(
                    f"could not reach {self.vendor} ({type(exc).__name__})", retryable=True
                )
                error.__cause__ = exc
            else:
                if response.status_code < 300 or response.status_code in ok_statuses:
                    if response.status_code >= 300 or response.status_code == 204 or not response.content:
                        return {}
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise ConnectorError(
                            f"{self.vendor} answered with something that is not JSON"
                        ) from exc
                error = self._redact(error_for(self.vendor, response))
            if error.retryable and attempt < retries:
                await asyncio.sleep(_retry_after(response, attempt))
                attempt += 1
                continue
            log.info(
                "knowledge_connector_call_failed",
                vendor=self.vendor,
                method=method,
                status=error.status,
                attempts=attempt + 1,
            )
            raise error
