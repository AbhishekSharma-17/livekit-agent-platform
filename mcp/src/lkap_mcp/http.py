"""Remote streamable-HTTP mode (V3-06, ``AGENT-ACCESS.md`` §9, R-V3-15, R-V3-24).

``lkap-mcp --http`` serves the same MCP server over streamable HTTP at ``/mcp``,
as its own process (never mounted in the api, R-V3-4). The service holds **no
API key of its own**: every request carries ``Authorization: Bearer lkap_…``,
and the key on the ``initialize`` request is the key that MCP session uses for
all of its api calls.

Per session (one :class:`~lkap_mcp.server.LkapServer` each, built with that
session's key, so the tool list is shaped by that key's scopes, R-V3-13):

* the session id is 32 random bytes (hex) and is bound to the sha256 of the
  key that opened it; a request on it with any other key is ``403``;
* ``GET /v1/api-keys/self`` runs once on ``initialize``: an unknown, revoked or
  expired key is ``401`` before a session exists;
* a key revoked or expired mid-session makes the next tool call relay
  ``ok=false, code="unauthorized"`` and then the session is dropped (§9.5 item 9);
* chats are owned by ``session:<registry id>`` (R-V3-24): ``chat_tools.OWNER_KEY``
  is replaced while the service runs and raises :class:`NoSession` (relayed as
  ``code="no_session"``) when the call's session cannot be resolved; every
  session end (``DELETE``, idle, revocation, shutdown) closes its chats first.

Checks on every ``/mcp`` request, in this order: ``Host``/``Origin`` (``403``;
§9.5 item 3), the bearer (``401``), the 1 MB body (``413``), the session binding
(unknown ``404``, other key ``403``), then the limits (``429`` with
``retry_after_s``): 5 sessions per key, 120 ``tools/call`` per minute per
session, 10 requests in flight per session. Tool calls are capped at 60 s (or the
call's own ``timeout_s`` plus a grace period, for ``chat_send`` and
``kb_add_document(wait=true)``), and at most 20 chats run per process. Sessions
idle for 30 min (no request; an open ``GET`` stream does not count) are closed.

The ``Authorization`` header and the key are never logged: this module logs the
key's id (from ``/v1/api-keys/self``) and an 8-character session id prefix only.
``file:`` references, ``kb_add_document(file_path=)`` and ``webhook_create`` are
refused in this mode (``McpSettings.transport == "http"``, D-V3-4).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import math
import secrets
import sys
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import urlsplit

import anyio
import httpx
from anyio.abc import TaskGroup, TaskStatus
from mcp.server.streamable_http import MCP_SESSION_ID_HEADER, StreamableHTTPServerTransport
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Message, Receive, Scope, Send

from lkap_mcp import server as server_module
from lkap_mcp.chat import tools as chat_tools
from lkap_mcp.client import ERROR_HINTS, ApiFailure, LkapClient, Method
from lkap_mcp.registry import Registry, ServerContext, ToolSpec
from lkap_mcp.results import ToolResult, sanitize
from lkap_mcp.server import LkapServer, load_tool_modules
from lkap_mcp.settings import McpSettings

log = logging.getLogger(__name__)

#: The MCP endpoint (Caddy proxies ``https://<api origin>/mcp`` here unchanged).
MCP_PATH: Final = "/mcp"
#: The only unauthenticated path.
HEALTH_PATH: Final = "/healthz"
#: Every LKAP API key starts with this; anything else is refused at the transport.
KEY_PREFIX: Final = "lkap_"
#: Extra seconds a tool with its own ``timeout_s`` gets on top of it.
TIMEOUT_GRACE_S: Final = 15.0
#: Environment variables the service refuses to start with: it has no key or
#: token of its own, and ``env:`` references resolve in this process's
#: environment, so any value here would be readable by every key holder.
FORBIDDEN_ENV: Final = ("LKAP_API_KEY", "LKAP_SERVICE_TOKEN", "LKAP_ADMIN_TOKEN", "LKAP_MASTER_KEY")

_LOOPBACK_HOSTS: Final = frozenset({"127.0.0.1", "localhost", "::1"})
_DEFAULT_PORTS: Final = {"http": 80, "https": 443}


class HttpSettings(BaseSettings):
    """The HTTP-mode knobs ``McpSettings`` does not declare (§9.3 limits, ``LKAP_ENV``)."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore", populate_by_name=True)

    #: ``prod`` requires an ``https`` public url (§9.5 item 1) and drops the loopback allowance.
    env: str = Field("dev", alias="LKAP_ENV")
    max_in_flight: int = Field(10, alias="LKAP_MCP_MAX_IN_FLIGHT_PER_SESSION", ge=1, le=100)
    call_timeout_s: float = Field(60.0, alias="LKAP_MCP_CALL_TIMEOUT_S", gt=0)
    idle_timeout_s: float = Field(1800.0, alias="LKAP_MCP_SESSION_IDLE_S", gt=0)
    max_body_bytes: int = Field(1_048_576, alias="LKAP_MCP_MAX_BODY_BYTES", ge=1024)
    max_chats_total: int = Field(20, alias="LKAP_MCP_MAX_CHATS_TOTAL", ge=1)
    sweep_interval_s: float = Field(30.0, alias="LKAP_MCP_SWEEP_INTERVAL_S", gt=0)

    @property
    def prod(self) -> bool:
        """True under ``LKAP_ENV=prod``."""
        return self.env.strip().lower() == "prod"


class StartupRefused(Exception):
    """The HTTP service must not start with this configuration (the message says why)."""


class NoSession(ApiFailure):
    """No registry session resolves for the current tool call (R-V3-24: fail closed).

    A subclass of :class:`~lkap_mcp.client.ApiFailure` so the registry's call
    wrapper relays it as ``ok=false, code="no_session"``.
    """

    def __init__(self) -> None:
        super().__init__(
            None,
            "no_session",
            "this call does not belong to a live MCP session; reconnect (initialize a new session)",
        )


# ---------------------------------------------------------------------------- configuration
def service_settings(settings: McpSettings) -> McpSettings:
    """The process settings as HTTP mode uses them.

    ``transport="http"`` switches ``file:`` references, ``kb_add_document(file_path=)``
    and ``webhook_create`` to refusals; no process-wide key and no ``X-Workspace``
    (each session's key decides its workspace).
    """
    return settings.model_copy(update={"transport": "http", "api_key": None, "workspace": None})


def check_startup(settings: McpSettings, http: HttpSettings, environ: dict[str, str]) -> None:
    """Refuse a configuration the service must not run with.

    Raises:
        StartupRefused: ``LKAP_ENV=prod`` without an ``https`` ``LKAP_MCP_PUBLIC_URL``; a
            malformed public url; a key or platform token in the service environment.
    """
    present = [name for name in FORBIDDEN_ENV if environ.get(name)]
    if present:
        raise StartupRefused(
            f"{', '.join(present)} must not be set for the HTTP service: it has no key of its own "
            "(each MCP session uses its own bearer key) and env: references resolve in this "
            "process's environment"
        )
    public = settings.public_url
    if public:
        parts = urlsplit(public)
        if parts.scheme not in _DEFAULT_PORTS or not parts.hostname:
            raise StartupRefused(
                "LKAP_MCP_PUBLIC_URL must be an absolute http(s) url, e.g. https://<host>/mcp"
            )
    if http.prod and (not public or urlsplit(public).scheme != "https"):
        raise StartupRefused(
            "LKAP_ENV=prod requires LKAP_MCP_PUBLIC_URL to be an https:// url: the bearer key "
            "travels only over TLS (AGENT-ACCESS.md §9.5 item 1)"
        )


def _split_host(value: str) -> tuple[str, int | None] | None:
    """``host[:port]`` (IPv6 in brackets) → ``(lowercase host, port)``; ``None`` if malformed."""
    try:
        parts = urlsplit(f"//{value.strip()}")
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    if not host:
        return None
    return host.lower(), port


@dataclass(frozen=True)
class OriginPolicy:
    """Which ``Host`` and ``Origin`` values the service accepts (§9.5 item 3, DNS rebinding)."""

    #: ``(host, port)`` pairs from ``LKAP_MCP_PUBLIC_URL``; a ``None`` port is the scheme's default.
    public: frozenset[tuple[str, int | None]]
    #: ``scheme://host[:port]`` of the public url, the one allowed ``Origin``.
    public_origin: str | None
    #: Dev only: loopback hosts with any port.
    loopback: bool

    @classmethod
    def build(cls, settings: McpSettings, http: HttpSettings) -> OriginPolicy:
        """From ``LKAP_MCP_PUBLIC_URL`` (and loopback unless ``LKAP_ENV=prod``)."""
        public: set[tuple[str, int | None]] = set()
        origin: str | None = None
        if settings.public_url:
            parts = urlsplit(settings.public_url)
            host = (parts.hostname or "").lower()
            default = _DEFAULT_PORTS.get(parts.scheme)
            port = parts.port or default
            public.add((host, port))
            if port == default:
                public.add((host, None))
            netloc = f"[{host}]" if ":" in host else host
            origin = f"{parts.scheme}://{netloc}" + ("" if port == default else f":{port}")
        return cls(public=frozenset(public), public_origin=origin, loopback=not http.prod)

    def host_ok(self, value: str | None) -> bool:
        """The ``Host`` header names the public url's host, or loopback in dev."""
        split = _split_host(value) if value else None
        if split is None:
            return False
        host, port = split
        if (host, port) in self.public:
            return True
        return self.loopback and host in _LOOPBACK_HOSTS

    def origin_ok(self, value: str | None) -> bool:
        """An absent ``Origin`` is fine; a present one must be the public origin (or loopback in dev)."""
        if value is None:
            return True
        normalized = value.strip().rstrip("/").lower()
        if self.public_origin is not None and normalized == self.public_origin.lower():
            return True
        if not self.loopback:
            return False
        parts = urlsplit(normalized)
        return parts.scheme in _DEFAULT_PORTS and (parts.hostname or "") in _LOOPBACK_HOSTS


# ---------------------------------------------------------------------------- per-session pieces
class SessionClient(LkapClient):
    """The api client of one MCP session; remembers a ``401`` (revoked or expired key)."""

    unauthorized: bool = False

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
        try:
            return await super().request(method, path, params=params, json=json, files=files, data=data)
        except ApiFailure as failure:
            if failure.status == 401:
                self.unauthorized = True
            raise


@dataclass
class CallLimits:
    """Process-wide state the per-session registries share (timeouts, the chat cap)."""

    call_timeout_s: float
    max_chats_total: int
    chats_open: Callable[[], int]
    chat_starts_pending: int = 0


@dataclass
class SessionRegistry(Registry):
    """A :class:`~lkap_mcp.registry.Registry` whose tool calls carry the HTTP-mode limits."""

    limits: CallLimits | None = None

    def wrapped(self, spec: ToolSpec) -> Callable[..., Awaitable[ToolResult]]:
        inner = super().wrapped(spec)
        limits = self.limits
        client = self.ctx.client

        async def run(**kwargs: Any) -> ToolResult:
            result = await (inner(**kwargs) if limits is None else _limited_call(spec, kwargs, inner, limits))
            if result.ok and isinstance(client, SessionClient) and client.unauthorized:
                # A best-effort sub-request (e.g. chat events) saw the 401: the key is gone, and
                # the session ends after this call, so say so rather than report success.
                return ToolResult.fail(
                    "unauthorized",
                    "the API key was revoked or expired during this call; this MCP session is closed",
                    status=401,
                    hint=ERROR_HINTS["unauthorized"],
                )
            return result

        return run


async def _limited_call(
    spec: ToolSpec,
    kwargs: dict[str, Any],
    inner: Callable[..., Awaitable[ToolResult]],
    limits: CallLimits,
) -> ToolResult:
    timeout = limits.call_timeout_s
    own = kwargs.get("timeout_s")
    if isinstance(own, int | float) and not isinstance(own, bool):
        timeout = max(timeout, float(own) + TIMEOUT_GRACE_S)
    starting = spec.name == "chat_start"
    if starting:
        if limits.chats_open() + limits.chat_starts_pending >= limits.max_chats_total:
            return ToolResult.fail(
                "too_many_chats",
                f"this MCP service already runs {limits.max_chats_total} test chats (the per-process cap)",
                details={"limit": limits.max_chats_total},
                next_steps=["chat_end a chat you no longer need, or retry later"],
            )
        limits.chat_starts_pending += 1
    try:
        with anyio.move_on_after(timeout) as scope:
            result = await inner(**kwargs)
    finally:
        if starting:
            limits.chat_starts_pending -= 1
    if scope.cancelled_caught:
        return sanitize(
            ToolResult.fail(
                "call_timeout",
                f"{spec.name} did not finish within {timeout:g}s (the remote service's per-call cap)",
                next_steps=[
                    "retry with a smaller request, or read the result later (e.g. kb_get, session_get)"
                ],
            )
        )
    return result


def build_session_server(settings: McpSettings, client: SessionClient, limits: CallLimits) -> LkapServer:
    """The server of one MCP session: ``build_server``'s composition with a :class:`SessionRegistry`.

    Mirrors :func:`lkap_mcp.server.build_server` (the same ``TOOL_MODULES``, resources
    and prompts); only the registry class differs (``_asks.md`` V3-06-1).
    """
    from lkap_mcp.prompts import register_prompts
    from lkap_mcp.resources import register_resources

    registry = SessionRegistry(ServerContext(settings=settings, client=client), limits=limits)
    for module in load_tool_modules(server_module.TOOL_MODULES):
        register = getattr(module, "register", None)
        if register is None:
            raise TypeError(f"tool module {module.__name__} has no register(registry) function")
        register(registry)
    server = LkapServer(registry)
    register_resources(server, registry.ctx)
    register_prompts(server, registry.ctx)
    return server


@dataclass(eq=False)
class HttpSession:
    """One live MCP session."""

    id: str
    key_hash: str
    key_id: str
    server: LkapServer
    client: SessionClient
    transport: StreamableHTTPServerTransport
    last_seen: float
    in_flight: int = 0
    calls: deque[float] = field(default_factory=deque)
    closing: bool = False
    ended: anyio.Event = field(default_factory=anyio.Event)

    @property
    def owner(self) -> str:
        """The chat owner of this session (R-V3-24)."""
        return f"session:{self.id}"

    @property
    def short(self) -> str:
        """A log-safe prefix of the session id."""
        return self.id[:8]


class Refusal(Exception):
    """An HTTP refusal: status, machine code, message, extra ``data`` and headers."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        request_id: Any = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.data = data or {}
        self.headers = headers or {}
        self.request_id = request_id

    def response(self) -> Response:
        """A JSON-RPC error body (``error.data.code`` is the machine code) with the HTTP status."""
        body = {
            "jsonrpc": "2.0",
            "id": self.request_id if self.request_id is not None else "server-error",
            "error": {"code": -32001, "message": self.message, "data": {"code": self.code, **self.data}},
        }
        return JSONResponse(body, status_code=self.status, headers=self.headers)


def _rate_limited(message: str, retry_after_s: float, request_id: Any = None) -> Refusal:
    wait = max(0.1, round(retry_after_s, 1))
    return Refusal(
        429,
        "rate_limited",
        message,
        data={"retry_after_s": wait},
        headers={"Retry-After": str(max(1, math.ceil(wait)))},
        request_id=request_id,
    )


# ---------------------------------------------------------------------------- the session registry
class HttpSessions:
    """Every live MCP session of the process, their limits and their lifecycle."""

    def __init__(
        self,
        settings: McpSettings,
        http: HttpSettings,
        *,
        api_transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.http = http
        self._api_transport = api_transport
        self._clock = clock
        self._sessions: dict[str, HttpSession] = {}
        self._by_ctx: dict[int, HttpSession] = {}
        self._opening: dict[str, int] = {}
        self._tg: TaskGroup | None = None
        self.limits = CallLimits(
            call_timeout_s=http.call_timeout_s,
            max_chats_total=http.max_chats_total,
            chats_open=self.chats_open,
        )

    # ------------------------------------------------------------------ lifecycle
    @contextlib.asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        """Own the session tasks and the idle sweeper; install the chat owner hook."""
        previous = chat_tools.OWNER_KEY
        chat_tools.OWNER_KEY = self.owner_key
        try:
            async with anyio.create_task_group() as tg:
                self._tg = tg
                tg.start_soon(self._sweeper)
                try:
                    yield
                finally:
                    for session in list(self._sessions.values()):
                        await self.end(session, "shutdown")
                    tg.cancel_scope.cancel()
                    self._tg = None
        finally:
            chat_tools.OWNER_KEY = previous

    async def _sweeper(self) -> None:
        while True:
            await anyio.sleep(self.http.sweep_interval_s)
            await self.sweep_idle()

    async def sweep_idle(self) -> list[str]:
        """End every session with no request for ``LKAP_MCP_SESSION_IDLE_S``; return their ids."""
        now = self._clock()
        idle = [
            s
            for s in list(self._sessions.values())
            if not s.closing and s.in_flight == 0 and now - s.last_seen >= self.http.idle_timeout_s
        ]
        for session in idle:
            await self.end(session, "idle")
        return [s.id for s in idle]

    # ------------------------------------------------------------------ lookups
    @property
    def sessions(self) -> list[HttpSession]:
        """The live sessions."""
        return [s for s in self._sessions.values() if not s.closing]

    def get(self, session_id: str) -> HttpSession | None:
        """A live session by id."""
        session = self._sessions.get(session_id)
        return None if session is None or session.closing else session

    def owner_key(self, ctx: ServerContext) -> str:
        """``chat_tools.OWNER_KEY`` in HTTP mode: ``session:<registry id>``, or raise (fail closed)."""
        session = self._by_ctx.get(id(ctx))
        if session is None or session.closing or session.server.ctx is not ctx:
            raise NoSession()
        return session.owner

    def chats_open(self) -> int:
        """Open chats across every session (the per-process cap)."""
        total = 0
        for session in self._sessions.values():
            try:
                total += len(chat_tools.manager_for(session.server.registry).chat_ids())
            except KeyError:
                continue
        return total

    def _key_sessions(self, key_hash: str) -> int:
        live = sum(1 for s in self._sessions.values() if s.key_hash == key_hash and not s.closing)
        return live + self._opening.get(key_hash, 0)

    # ------------------------------------------------------------------ opening
    async def open(
        self, key: str, key_hash: str, scope: Scope, receive: Receive, send: Send, request_id: Any
    ) -> None:
        """Serve an ``initialize`` request: admit, verify the key, start the session, answer."""
        if self._tg is None:
            raise RuntimeError("HttpSessions.run() is not active")
        if self._key_sessions(key_hash) >= self.settings.max_sessions_per_key:
            raise _rate_limited(
                f"this API key already has {self.settings.max_sessions_per_key} open MCP sessions; "
                "close one (or wait for it to go idle) first",
                retry_after_s=60.0,
                request_id=request_id,
            )
        self._opening[key_hash] = self._opening.get(key_hash, 0) + 1
        try:
            session = await self._start(key, key_hash, request_id)
        finally:
            self._opening[key_hash] -= 1
            if not self._opening[key_hash]:
                del self._opening[key_hash]
        status = await _serve(session.transport.handle_request, scope, receive, send)
        session.last_seen = self._clock()
        if status is None or status >= 400:
            await self.end(session, "initialize_failed")
            return
        log.info(
            "mcp_session_opened",
            extra={
                "session": session.short,
                "key_id": session.key_id,
                "tools": len(session.server.registered_tool_names),
            },
        )

    async def _start(self, key: str, key_hash: str, request_id: Any) -> HttpSession:
        client = SessionClient(self.settings, api_key=key, transport=self._api_transport)
        server = build_session_server(self.settings, client, self.limits)
        identity = await server.refresh_identity()
        if identity is None:
            failure = server.ctx.identity_error
            await server.aclose()
            if failure is not None and failure.status == 401:
                raise Refusal(
                    401,
                    "unauthorized",
                    "the API key is unknown, revoked or expired; mint an agent key in the console",
                    headers={"WWW-Authenticate": 'Bearer realm="lkap"'},
                    request_id=request_id,
                )
            raise Refusal(
                502,
                "api_unavailable",
                "the LKAP api did not confirm this key; retry shortly",
                request_id=request_id,
            )
        await server.ensure_registered()
        transport = StreamableHTTPServerTransport(
            mcp_session_id=secrets.token_hex(32),
            is_json_response_enabled=False,
            event_store=None,
            security_settings=None,  # Host/Origin are checked before the transport (403)
        )
        assert transport.mcp_session_id is not None
        session = HttpSession(
            id=transport.mcp_session_id,
            key_hash=key_hash,
            key_id=identity.id,
            server=server,
            client=client,
            transport=transport,
            last_seen=self._clock(),
        )
        self._sessions[session.id] = session
        self._by_ctx[id(server.ctx)] = session
        assert self._tg is not None
        await self._tg.start(self._run_session, session)
        return session

    async def _run_session(
        self, session: HttpSession, *, task_status: TaskStatus[None] = anyio.TASK_STATUS_IGNORED
    ) -> None:
        # FastMCP keeps its low-level server private; run_stdio_async uses it the same way.
        lowlevel = session.server._mcp_server
        try:
            async with session.transport.connect() as (read, write):
                task_status.started()
                try:
                    await lowlevel.run(read, write, lowlevel.create_initialization_options(), stateless=False)
                except Exception as error:
                    log.warning(
                        "mcp_session_crashed", extra={"session": session.short, "error": type(error).__name__}
                    )
        finally:
            await self.end(session, "closed")

    # ------------------------------------------------------------------ serving
    async def serve(
        self,
        session: HttpSession,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        requests: list[tuple[str, Any]],
    ) -> None:
        """Serve one request on an existing session, applying the per-session limits."""
        now = self._clock()
        calls = [rid for method, rid in requests if method == "tools/call"]
        if requests and session.in_flight >= self.http.max_in_flight:
            raise _rate_limited(
                f"{self.http.max_in_flight} requests are already in flight on this session",
                retry_after_s=1.0,
                request_id=requests[0][1],
            )
        if calls:
            window = session.calls
            while window and now - window[0] >= 60.0:
                window.popleft()
            if len(window) + len(calls) > self.settings.calls_per_min:
                retry = 60.0 - (now - window[0]) if window else 60.0
                raise _rate_limited(
                    f"more than {self.settings.calls_per_min} tool calls in a minute on this session",
                    retry_after_s=retry,
                    request_id=calls[0],
                )
            window.extend([now] * len(calls))
        counted = bool(requests)
        if counted:
            session.in_flight += 1
        session.last_seen = now
        try:
            await session.transport.handle_request(scope, receive, send)
        finally:
            if counted:
                session.in_flight -= 1
            if scope.get("method") != "GET":
                session.last_seen = self._clock()
        if session.transport.is_terminated:
            await self.end(session, "deleted")
        elif session.client.unauthorized:
            await self.end(session, "unauthorized")

    # ------------------------------------------------------------------ ending
    async def end(self, session: HttpSession, reason: str) -> None:
        """End a session: close its chats, drop it, close its server and transport (idempotent)."""
        if session.closing:
            await session.ended.wait()
            return
        session.closing = True
        with anyio.CancelScope(shield=True):
            try:
                try:
                    manager = chat_tools.manager_for(session.server.registry)
                except KeyError:
                    manager = None
                if manager is not None:
                    await manager.close_owner(session.owner)
            except Exception as error:  # a failing chat close must not keep the session alive
                log.warning(
                    "mcp_session_chat_close_failed",
                    extra={"session": session.short, "error": type(error).__name__},
                )
            finally:
                self._sessions.pop(session.id, None)
                self._by_ctx.pop(id(session.server.ctx), None)
                await session.server.aclose()
                if not session.transport.is_terminated:
                    await session.transport.terminate()
                session.ended.set()
        log.info(
            "mcp_session_ended", extra={"session": session.short, "key_id": session.key_id, "reason": reason}
        )


async def _serve(
    app: Callable[[Scope, Receive, Send], Awaitable[None]], scope: Scope, receive: Receive, send: Send
) -> int | None:
    """Run ``app`` for one request and return the HTTP status it answered with."""
    status: int | None = None

    async def watch(message: Message) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = int(message["status"])
        await send(message)

    await app(scope, receive, watch)
    return status


# ---------------------------------------------------------------------------- the ASGI endpoint
def key_hash(key: str) -> str:
    """sha256 of the bearer key (what a session is bound to)."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def bearer_key(headers: Headers) -> str | None:
    """The ``lkap_`` key of ``Authorization: Bearer …``, or ``None``."""
    value = headers.get("authorization")
    if not value:
        return None
    scheme, _, token = value.strip().partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token.startswith(KEY_PREFIX) or len(token) > 512:
        return None
    return token


def _jsonrpc_requests(body: bytes) -> list[tuple[str, Any]] | None:
    """``(method, id)`` of every JSON-RPC request in a POST body; ``None`` if it is not JSON."""
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    items: Iterable[Any] = parsed if isinstance(parsed, list) else [parsed]
    return [
        (str(item["method"]), item["id"])
        for item in items
        if isinstance(item, dict) and "method" in item and "id" in item
    ]


async def _read_body(receive: Receive, headers: Headers, limit: int) -> tuple[bytes, list[Message]]:
    """Read the whole body (at most ``limit`` bytes) and the messages to replay; ``413`` beyond it."""
    declared = headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise Refusal(413, "payload_too_large", f"the request body exceeds {limit} bytes")
        except ValueError:
            pass
    body = bytearray()
    replay: list[Message] = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            replay.append(message)
            break
        body.extend(message.get("body", b""))
        if len(body) > limit:
            raise Refusal(413, "payload_too_large", f"the request body exceeds {limit} bytes")
        if not message.get("more_body", False):
            break
    return bytes(body), [{"type": "http.request", "body": bytes(body), "more_body": False}, *replay]


def _replaying(messages: list[Message], receive: Receive) -> Receive:
    queue = deque(messages)

    async def replay() -> Message:
        if queue:
            return queue.popleft()
        return await receive()

    return replay


class McpEndpoint:
    """The ASGI app at ``/mcp``: the checks of §9.1/§9.3/§9.5, then the session's transport."""

    def __init__(self, sessions: HttpSessions, policy: OriginPolicy) -> None:
        self.sessions = sessions
        self.policy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await self._handle(scope, receive, send)
        except Refusal as refusal:
            log.info(
                "mcp_request_refused",
                extra={"status": refusal.status, "code": refusal.code, "method": scope.get("method")},
            )
            await refusal.response()(scope, receive, send)

    async def _handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = Headers(scope=scope)
        if not self.policy.host_ok(headers.get("host")):
            raise Refusal(403, "forbidden_host", "the Host header does not name this service")
        if not self.policy.origin_ok(headers.get("origin")):
            raise Refusal(403, "forbidden_origin", "cross-origin requests are not accepted")
        key = bearer_key(headers)
        if key is None:
            raise Refusal(
                401,
                "unauthorized",
                "send the LKAP API key as 'Authorization: Bearer lkap_…'",
                headers={"WWW-Authenticate": 'Bearer realm="lkap"'},
            )
        hashed = key_hash(key)
        method = scope.get("method", "GET")
        requests: list[tuple[str, Any]] = []
        if method == "POST":
            body, messages = await _read_body(receive, headers, self.sessions.http.max_body_bytes)
            receive = _replaying(messages, receive)
            requests = _jsonrpc_requests(body) or []
        session_id = headers.get(MCP_SESSION_ID_HEADER)
        if session_id is None:
            initialize = [rid for name, rid in requests if name == "initialize"]
            if method != "POST" or not initialize:
                raise Refusal(
                    400, "bad_request", "no Mcp-Session-Id: only an initialize request opens a session"
                )
            await self.sessions.open(key, hashed, scope, receive, send, initialize[0])
            return
        session = self.sessions.get(session_id)
        if session is None:
            raise Refusal(404, "session_not_found", "unknown or ended MCP session; initialize a new one")
        if not hmac.compare_digest(session.key_hash, hashed):
            raise Refusal(403, "session_key_mismatch", "this MCP session was opened with a different API key")
        await self.sessions.serve(session, scope, receive, send, requests=requests)


async def healthz(request: Request) -> Response:
    """Liveness: unauthenticated, no session data."""
    return JSONResponse({"status": "ok"})


def build_app(
    settings: McpSettings,
    http: HttpSettings | None = None,
    *,
    api_transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Starlette:
    """The HTTP service: ``/mcp`` and ``/healthz``; sessions live in the app's lifespan.

    Args:
        settings: Process settings (made HTTP-mode by :func:`service_settings`).
        http: The HTTP-mode knobs (read from the environment when omitted).
        api_transport: An httpx transport for every session's api client (tests pass
            an in-process ``ASGITransport``).
        clock: Monotonic clock for idle and rate windows (tests pass a fake one).
    """
    settings = service_settings(settings)
    http = http or HttpSettings()
    sessions = HttpSessions(settings, http, api_transport=api_transport, clock=clock)
    endpoint = McpEndpoint(sessions, OriginPolicy.build(settings, http))

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with sessions.run():
            yield

    app = Starlette(
        routes=[Route(HEALTH_PATH, healthz, methods=["GET"]), Route(MCP_PATH, endpoint=endpoint)],
        lifespan=lifespan,
    )
    app.state.sessions = sessions
    return app


def quiet_library_logs() -> None:
    """The SDK logs session ids at INFO and raw messages at DEBUG; keep both out of the service log."""
    logging.getLogger("mcp").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def run_http(settings: McpSettings, environ: dict[str, str]) -> int:
    """``lkap-mcp --http``: check the configuration, then serve until stopped.

    Returns:
        ``2`` when the configuration is refused (the reason on stderr), else ``0``.
    """
    import uvicorn

    http = HttpSettings()
    try:
        check_startup(settings, http, environ)
    except StartupRefused as refused:
        sys.stderr.write(f"lkap-mcp: refusing to start the HTTP service: {refused}\n")
        return 2
    quiet_library_logs()
    app = build_app(settings, http)
    log.info(
        "lkap_mcp_starting",
        extra={
            "transport": "http",
            "api_url": settings.api_url,
            "bind": f"{settings.http_host}:{settings.http_port}",
            "public_url": settings.public_url,
        },
    )
    config = uvicorn.Config(
        app,
        host=settings.http_host,
        port=settings.http_port,
        log_config=None,
        access_log=False,
        lifespan="on",
        proxy_headers=False,
        server_header=False,
        # Open SSE streams would otherwise hold a shutdown forever; sessions end in the lifespan.
        timeout_graceful_shutdown=10,
    )
    uvicorn.Server(config).run()
    return 0
