"""Scope-gated tool registration (D-V3-6, R-V3-13) and the tool-call wrapper.

Tool modules (``server.TOOL_MODULES``) each expose::

    def register(registry: Registry) -> None: ...

and call :meth:`Registry.register` (or the :meth:`Registry.tool` decorator) for
every tool they define. Registration only *declares* a tool: which tools a
client actually sees is decided once the key's identity is known
(``GET /v1/api-keys/self``), on the first ``tools/list`` or ``tools/call``:
a tool is exposed when the key's scopes cover every scope it declares
(``x:write`` implies ``x:read``, ``*`` covers all), its ``gated_by`` predicate
passes, and — under ``LKAP_MCP_READ_ONLY=1`` — it is read-only.

Every exposed tool runs inside :meth:`Registry._invoke`, which sets the
attribution context the api client reads, converts every exception into an
``ok=false`` result, and passes the result through
:func:`~lkap_mcp.results.sanitize` (redaction and value scrubbing). This is the
secret boundary: nothing leaves a tool any other way.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from mcp.server.lowlevel.server import request_ctx
from mcp.types import ToolAnnotations
from pydantic import ValidationError

from lkap_mcp.client import CURRENT_CALL, ApiFailure, CallInfo, LkapClient
from lkap_mcp.results import ToolResult, hide_input_values, sanitize, sanitize_text
from lkap_mcp.secrets import SecretInputError
from lkap_mcp.settings import McpSettings

log = logging.getLogger(__name__)

ToolFn = Callable[..., Awaitable[ToolResult]]
ShutdownHook = Callable[[], Awaitable[None]]

#: Annotation presets (``AGENT-ACCESS.md`` §4 conventions).
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
)


def scope_allows(scopes: Iterable[str], needed: str) -> bool:
    """The api's rule (``auth/roles.py``): ``*`` covers all and ``x:write`` implies ``x:read``."""
    held = set(scopes)
    if "*" in held or needed in held:
        return True
    return needed.endswith(":read") and needed.removesuffix(":read") + ":write" in held


@dataclass(frozen=True)
class KeyIdentity:
    """The API key the process acts as (``GET /v1/api-keys/self``)."""

    id: str
    name: str
    prefix: str
    kind: str
    client: str | None
    scopes: frozenset[str]
    expires_at: str | None
    workspace: dict[str, Any]
    read_only: bool = False

    @classmethod
    def from_api(cls, body: dict[str, Any], *, read_only: bool) -> KeyIdentity:
        """Build from the ``ApiKeySelfOut`` body."""
        return cls(
            id=str(body.get("id", "")),
            name=str(body.get("name", "")),
            prefix=str(body.get("prefix", "")),
            kind=str(body.get("kind", "standard")),
            client=body.get("client"),
            scopes=frozenset(str(s) for s in body.get("scopes") or []),
            expires_at=body.get("expires_at"),
            workspace=dict(body.get("workspace") or {}),
            read_only=read_only,
        )

    def allows(self, scope: str) -> bool:
        """Whether this key may use ``scope`` (always ``False`` for writes in read-only mode)."""
        if self.read_only and not scope.endswith(":read"):
            return False
        return scope_allows(self.scopes, scope)


@dataclass
class ServerContext:
    """What every tool needs: settings, the api client and the key identity."""

    settings: McpSettings
    client: LkapClient
    identity: KeyIdentity | None = None
    identity_error: ApiFailure | None = None

    def allows(self, scope: str) -> bool:
        """Whether the key (as known) may use ``scope``; ``False`` before the identity is known."""
        return self.identity is not None and self.identity.allows(scope)


@dataclass(frozen=True)
class ToolSpec:
    """One declared tool."""

    name: str
    fn: ToolFn
    scopes: frozenset[str]
    annotations: ToolAnnotations
    description: str
    gated_by: Callable[[ServerContext], bool] | None = None
    data: str | None = None
    #: For a mixed read/write tool: the scopes of its write path. Without any of
    #: them (or in read-only mode) the tool is exposed as a read tool and its
    #: write path refuses (``tools._common.forbidden``).
    write_scopes: frozenset[str] = frozenset()

    @property
    def read_only(self) -> bool:
        """True for read tools (``readOnlyHint``)."""
        return bool(self.annotations.readOnlyHint)

    @property
    def mixed(self) -> bool:
        """True for a read tool with an optional write path."""
        return bool(self.write_scopes)


@dataclass
class Registry:
    """Declared tools plus the shutdown hooks of the modules that declared them."""

    ctx: ServerContext
    specs: dict[str, ToolSpec] = field(default_factory=dict)
    shutdown_hooks: list[ShutdownHook] = field(default_factory=list)

    # ------------------------------------------------------------------ declaring
    def register(
        self,
        fn: ToolFn,
        *,
        scopes: Iterable[str] = (),
        annotations: ToolAnnotations = WRITE,
        gated_by: Callable[[ServerContext], bool] | None = None,
        name: str | None = None,
        description: str | None = None,
        data: str | None = None,
        write_scopes: Iterable[str] = (),
    ) -> ToolSpec:
        """Declare a tool.

        Args:
            fn: An ``async def`` returning :class:`~lkap_mcp.results.ToolResult`. Its
                signature (typed parameters, pydantic models) becomes the input schema.
            scopes: Every api scope the tool needs; empty = any key.
            annotations: :data:`READ`, :data:`WRITE`, :data:`IDEMPOTENT_WRITE` or :data:`DESTRUCTIVE`.
            gated_by: An extra predicate on the context (e.g. the dial gate).
            name: The tool name; defaults to ``fn.__name__``.
            description: One static line; defaults to the docstring's first paragraph.
                Never interpolate platform data into it (R-V3-12).
            data: The name of the ``data`` model (``AgentOut`` …), recorded in the tool's ``_meta``.
            write_scopes: For a read tool with an optional write path (``connection_fleet``
                actions, ``agent_versions`` restore …): the scopes that path needs.
        """
        tool_name = name or fn.__name__
        if tool_name in self.specs:
            raise ValueError(f"tool {tool_name!r} is declared twice")
        doc = description or " ".join((fn.__doc__ or "").strip().split("\n\n")[0].split())
        spec = ToolSpec(
            name=tool_name,
            fn=fn,
            scopes=frozenset(scopes),
            annotations=annotations,
            description=doc,
            gated_by=gated_by,
            data=data,
            write_scopes=frozenset(write_scopes),
        )
        self.specs[tool_name] = spec
        return spec

    def tool(
        self,
        *,
        scopes: Iterable[str] = (),
        annotations: ToolAnnotations = WRITE,
        gated_by: Callable[[ServerContext], bool] | None = None,
        name: str | None = None,
        description: str | None = None,
        data: str | None = None,
        write_scopes: Iterable[str] = (),
    ) -> Callable[[ToolFn], ToolFn]:
        """Decorator form of :meth:`register`."""

        def decorate(fn: ToolFn) -> ToolFn:
            self.register(
                fn,
                scopes=scopes,
                annotations=annotations,
                gated_by=gated_by,
                name=name,
                description=description,
                data=data,
                write_scopes=write_scopes,
            )
            return fn

        return decorate

    def on_shutdown(self, hook: ShutdownHook) -> None:
        """Run ``hook`` when the server stops (e.g. close open chats)."""
        self.shutdown_hooks.append(hook)

    # ------------------------------------------------------------------ exposing
    def exposed(self, spec: ToolSpec) -> bool:
        """Whether ``spec`` is visible for the current identity and settings."""
        settings = self.ctx.settings
        if settings.read_only and not (spec.read_only or spec.mixed):
            return False
        if spec.scopes:
            if self.ctx.identity is None:
                return False
            if not all(self.ctx.identity.allows(scope) for scope in spec.scopes):
                return False
        return spec.gated_by is None or spec.gated_by(self.ctx)

    def can_write(self, spec: ToolSpec) -> bool:
        """Whether a mixed tool's write path is open for this key."""
        return any(self.ctx.allows(scope) for scope in spec.write_scopes)

    def effective_annotations(self, spec: ToolSpec) -> ToolAnnotations:
        """A mixed tool whose write path is closed presents as a read tool."""
        if spec.mixed and not self.can_write(spec):
            return READ
        return spec.annotations

    def exposed_specs(self) -> list[ToolSpec]:
        """Every visible tool, in declaration order."""
        return [spec for spec in self.specs.values() if self.exposed(spec)]

    def wrapped(self, spec: ToolSpec) -> Callable[..., Awaitable[ToolResult]]:
        """The callable FastMCP runs for ``spec`` (validated kwargs in, sanitized result out)."""

        async def run(**kwargs: Any) -> ToolResult:
            return await self._invoke(spec, kwargs)

        return run

    async def _invoke(self, spec: ToolSpec, kwargs: dict[str, Any]) -> ToolResult:
        info = CallInfo(client=current_client_name(self.ctx.settings), tool=spec.name, call=uuid.uuid4().hex)
        token = CURRENT_CALL.set(info)
        started = time.monotonic()
        try:
            result = await spec.fn(**kwargs)
        except ApiFailure as failure:
            result = failure.to_result()
        except SecretInputError as error:
            result = ToolResult.fail(error.code, error.message, hint=error.hint)
        except ValidationError as error:
            result = ToolResult.fail("invalid_input", hide_input_values(str(error)))
        except Exception as error:  # the boundary: nothing escapes unscrubbed
            log.warning("tool_crashed", extra={"tool": spec.name, "error": type(error).__name__})
            result = ToolResult.fail("internal_error", sanitize_text(f"{type(error).__name__}: {error}"))
        finally:
            CURRENT_CALL.reset(token)
        clean = sanitize(result)
        log.debug(
            "tool_call",
            extra={
                "tool": spec.name,
                "call": info.call,
                "ok": clean.ok,
                "code": clean.error.code if clean.error else None,
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            },
        )
        return clean


def current_client_name(settings: McpSettings) -> str | None:
    """``LKAP_MCP_CLIENT``, else the MCP ``initialize`` handshake's ``clientInfo.name``."""
    if settings.client_name:
        return settings.client_name
    try:
        ctx = request_ctx.get()
    except LookupError:
        return None
    params = getattr(ctx.session, "client_params", None)
    info = getattr(params, "clientInfo", None)
    name = getattr(info, "name", None)
    return str(name) if name else None
