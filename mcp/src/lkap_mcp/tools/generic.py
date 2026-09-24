"""Generic tools (``AGENT-ACCESS.md`` §4.11): ``lkap_delete`` and the guarded ``api_request``.

``api_request`` is the fallback for ``/v1`` routes without a typed tool. It
refuses, before resolving or sending anything: any path outside ``/v1/``;
``/v1/api-keys*`` and ``/v1/auth/*``; every non-GET under ``/v1/workspaces``
(the telephony policy and membership are console-only, R-V3-7);
``/v1/connections/*/rotate``; non-GET ``/v1/credentials``, ``/v1/calls`` and
``/v1/telephony`` (typed tools or console-only); and any body whose
``api_key``/``api_secret``/``secrets``/``password``/``token`` value is not an
``env:``/``file:`` reference. Non-GET needs ``confirm=true``.
"""

from __future__ import annotations

import re
from contextlib import suppress
from typing import Annotated, Any, Literal

from pydantic import Field

from lkap_mcp.registry import DESTRUCTIVE, Registry, ServerContext
from lkap_mcp.results import ToolResult
from lkap_mcp.secrets import REF_PLACEHOLDER, SecretInputError, parse_secret, resolve_secret
from lkap_mcp.tools._common import forbidden, planned, request, seg

DeleteKind = Literal["agent", "kb", "kb_document", "tool", "webhook", "provider_key", "connection", "session"]
HttpVerb = Literal["GET", "POST", "PUT", "DELETE"]

#: Every write scope; ``lkap_delete`` is visible to a key with any of them.
WRITE_SCOPES = frozenset(
    {
        "agents:write",
        "sessions:write",
        "connections:write",
        "providers:write",
        "webhooks:write",
        "calls:write",
    }
)

#: kind → (path template, scope). ``{id}`` and ``{parent}`` are quoted segments.
DELETE_ROUTES: dict[str, tuple[str, str]] = {
    "agent": ("/v1/agents/{id}", "agents:write"),
    "kb": ("/v1/knowledge-bases/{id}", "agents:write"),
    "kb_document": ("/v1/knowledge-bases/{parent}/documents/{id}", "agents:write"),
    "tool": ("/v1/tools/{id}", "agents:write"),
    "webhook": ("/v1/webhooks/{id}", "webhooks:write"),
    "provider_key": ("/v1/credentials/{id}", "providers:write"),
    "connection": ("/v1/connections/{id}", "connections:write"),
    "session": ("/v1/sessions/{id}", "sessions:write"),
}

#: Body keys whose values must be references in ``api_request``.
SECRET_BODY_KEYS = frozenset({"api_key", "api_secret", "secrets", "password", "token"})

_BAD_PATH = re.compile(r"(\?|#|\\|//|%2e|%2f|%5c)", re.IGNORECASE)
_PARAM = re.compile(r"\{[^/{}]+\}")


class RequestRefused(Exception):
    """``api_request`` will not send this."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def check_path(method: str, path: str) -> None:
    """Refuse paths ``api_request`` never sends (see the module docstring)."""
    if not path.startswith("/v1/") or _BAD_PATH.search(path):
        raise RequestRefused(
            "path_refused", "api_request only sends plain /v1/... paths (no query, fragment or dot segments)"
        )
    segments = path.split("/")
    if any(part in {".", ".."} for part in segments):
        raise RequestRefused("path_refused", "dot segments are not allowed")
    write = method != "GET"
    rules: list[tuple[bool, str]] = [
        (
            path == "/v1/api-keys" or path.startswith("/v1/api-keys/"),
            "API keys are managed in the console only",
        ),
        (path.startswith("/v1/auth/") or path == "/v1/auth", "auth routes are not available to agents"),
        (
            write and (path == "/v1/workspaces" or path.startswith("/v1/workspaces/")),
            "workspace settings, members and the dialing policy are console-only",
        ),
        (bool(re.fullmatch(r"/v1/connections/[^/]+/rotate", path)), "use connection_rotate"),
        (
            write and (path == "/v1/credentials" or path.startswith("/v1/credentials/")),
            "use provider_key_create / lkap_delete(kind='provider_key')",
        ),
        (write and (path == "/v1/calls" or path.startswith("/v1/calls/")), "use call_place / call_control"),
        (
            write and (path == "/v1/telephony" or path.startswith("/v1/telephony/")),
            "telephony writes are console-only",
        ),
    ]
    for refused, reason in rules:
        if refused:
            raise RequestRefused("route_refused", f"api_request refuses {method} {path}: {reason}")


def secret_body(ctx: ServerContext, body: Any, *, resolve: bool) -> Any:
    """Check (and optionally resolve) every secret-named key of ``body``: only references pass."""
    if isinstance(body, list):
        return [secret_body(ctx, item, resolve=resolve) for item in body]
    if not isinstance(body, dict):
        return body
    out: dict[str, Any] = {}
    for key, value in body.items():
        if str(key).lower() in SECRET_BODY_KEYS and value is not None:
            out[key] = _reference_value(ctx, str(key), value, resolve=resolve)
        else:
            out[key] = secret_body(ctx, value, resolve=resolve)
    return out


def _reference_value(ctx: ServerContext, key: str, value: Any, *, resolve: bool) -> Any:
    if isinstance(value, dict):
        return {k: _reference_value(ctx, f"{key}.{k}", v, resolve=resolve) for k, v in value.items()}
    if not isinstance(value, str) or not value.startswith(("env:", "file:")):
        # Parse it anyway so an inline value is remembered and scrubbed from every output.
        if isinstance(value, str):
            with suppress(SecretInputError):
                parse_secret(value, field_name=key, inline_allowed=True)
        raise RequestRefused(
            "inline_secret_refused",
            f"api_request refuses an inline value for {key!r}; "
            "use the typed tool, or an env:/file: reference",
        )
    parsed = parse_secret(
        value, field_name=key, inline_allowed=False, file_allowed=not ctx.settings.http_mode
    )
    return resolve_secret(parsed).value if resolve else REF_PLACEHOLDER


def match_route(document: dict[str, Any], method: str, path: str) -> dict[str, Any] | None:
    """The OpenAPI operation for a concrete (or template) path; literal segments beat parameters."""
    best: tuple[int, dict[str, Any]] | None = None
    for template, operations in (document.get("paths") or {}).items():
        operation = (operations or {}).get(method.lower())
        if operation is None:
            continue
        if template != path:
            pattern = (
                "^" + _PARAM.sub("[^/]+", re.escape(template).replace(r"\{", "{").replace(r"\}", "}")) + "$"
            )
            if not re.match(pattern, path):
                continue
        params = len(_PARAM.findall(template)) if template != path else -1
        found = {
            "method": method.upper(),
            "path": template,
            "operation_id": operation.get("operationId"),
            "summary": operation.get("summary"),
            "description": operation.get("description"),
            "parameters": operation.get("parameters", []),
            "request_body": operation.get("requestBody"),
        }
        if best is None or params < best[0]:
            best = (params, found)
    return best[1] if best else None


def register(registry: Registry) -> None:
    """Declare the generic tools."""
    ctx = registry.ctx
    client = ctx.client

    def any_write(context: ServerContext) -> bool:
        return any(context.allows(scope) for scope in WRITE_SCOPES)

    @registry.tool(annotations=DESTRUCTIVE, gated_by=any_write, data="Deleted")
    async def lkap_delete(
        kind: DeleteKind,
        id: str,  # noqa: A002
        parent_id: Annotated[str | None, Field(description="The knowledge base of a kb_document")] = None,
        purge: Annotated[
            bool, Field(description="Agents only: cascade-delete the sessions of an archived agent")
        ] = False,
        confirm: bool = False,
        plan: bool = False,
    ) -> ToolResult:
        """Delete an agent, knowledge base, document, tool, webhook, provider key, connection or session
        (needs confirm).
        """
        template, scope = DELETE_ROUTES[kind]
        if not ctx.allows(scope):
            return forbidden(scope)
        if kind == "kb_document" and not parent_id:
            return ToolResult.fail("invalid_input", "kb_document needs parent_id (the knowledge base id)")
        path = template.replace("{id}", seg(id)).replace("{parent}", seg(parent_id or ""))
        query = {"purge": "true"} if kind == "agent" and purge else None
        if plan:
            return planned(request("DELETE", path, query=query))
        if not confirm:
            extra = " and all of its sessions" if query else ""
            return ToolResult.needs_confirmation(
                f"permanently delete {kind} {id}{extra}; this cannot be undone"
            )
        await client.delete(path, params=query)
        return ToolResult.success({"deleted": True, "kind": kind, "id": id})

    @registry.tool(annotations=DESTRUCTIVE, data="ApiResponse", write_scopes=WRITE_SCOPES)
    async def api_request(
        method: HttpVerb,
        path: Annotated[str, Field(description="A /v1/... path (no query string; use query)")],
        query: dict[str, Any] | None = None,
        body: Any = None,
        confirm: Annotated[bool, Field(description="Required for any method other than GET")] = False,
        plan: bool = False,
    ) -> ToolResult:
        """Call a /v1 route that has no typed tool (reads first; writes need confirm). Prefer the typed
        tools.
        """
        try:
            check_path(method, path)
            shown_body = secret_body(ctx, body, resolve=False)
        except RequestRefused as refused:
            return ToolResult.fail(refused.code, refused.message)
        if method != "GET" and not registry.can_write(registry.specs["api_request"]):
            return forbidden("a write scope")
        route = match_route(await client.openapi(), method, path)
        if route is None:
            return ToolResult.fail(
                "unknown_route", f"no api route matches {method} {path}", hint="lkap://openapi lists them"
            )
        if plan:
            return planned(request(method, path, shown_body, query=query))
        if method != "GET" and not confirm:
            return ToolResult.needs_confirmation(
                f"send {method} {path} ({route.get('summary') or 'no summary'})"
            )
        sent_body = secret_body(ctx, body, resolve=True) if body is not None else None
        response = await client.request(method, path, params=query, json=sent_body)
        return ToolResult.success(
            {
                "route": {k: route[k] for k in ("method", "path", "operation_id", "summary")},
                "response": response,
            }
        )
