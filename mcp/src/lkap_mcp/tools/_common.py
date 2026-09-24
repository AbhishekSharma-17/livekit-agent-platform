"""Helpers shared by the tool modules: secrets, plans, merge patches, confirmation."""

from __future__ import annotations

import copy
import re
from typing import Any
from urllib.parse import quote

from lkap_mcp.client import LkapClient
from lkap_mcp.registry import ServerContext
from lkap_mcp.results import PlannedRequest, ToolResult
from lkap_mcp.secrets import SecretInput, parse_secret, resolve_secret

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def seg(value: str) -> str:
    """Quote one path segment (ids and slugs never smuggle a ``/``)."""
    return quote(value, safe="")


def slugify(name: str) -> str:
    """A url-safe slug from a display name."""
    return _SLUG_RE.sub("-", name.lower()).strip("-")[:48] or "item"


def parse(ctx: ServerContext, raw: str, field_name: str) -> SecretInput:
    """Parse a ``SecretInput`` argument under this process's inline/file policy."""
    return parse_secret(
        raw,
        field_name=field_name,
        inline_allowed=ctx.settings.inline_allowed,
        file_allowed=not ctx.settings.http_mode,
    )


def resolve_all(secrets: dict[str, SecretInput]) -> tuple[dict[str, str], list[str]]:
    """Resolve parsed secrets to values, collecting warnings (never the values)."""
    values: dict[str, str] = {}
    warnings: list[str] = []
    for key, secret in secrets.items():
        resolved = resolve_secret(secret)
        values[key] = resolved.value
        warnings.extend(resolved.warnings)
    return values, warnings


def placeholders(secrets: dict[str, SecretInput]) -> dict[str, str]:
    """``{field: "<inline secret>" | "<ref>"}`` for a plan."""
    return {key: secret.placeholder for key, secret in secrets.items()}


def planned(*requests: PlannedRequest, warnings: list[str] | None = None) -> ToolResult:
    """The ``plan=true`` answer."""
    return ToolResult.planned(list(requests), warnings=warnings)


def request(
    method: str, path: str, body: Any = None, *, query: dict[str, Any] | None = None, note: str | None = None
) -> PlannedRequest:
    """One planned request."""
    clean_query = {k: v for k, v in (query or {}).items() if v is not None} or None
    return PlannedRequest.model_validate(
        {"method": method, "path": path, "body": body, "query": clean_query, "note": note}
    )


def merge_patch(target: Any, patch: Any) -> Any:
    """RFC 7386 JSON merge patch: objects merge recursively, ``null`` removes a key."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result: dict[str, Any] = copy.deepcopy(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


def forbidden(scope: str) -> ToolResult:
    """The result of an action the key's scopes (or read-only mode) do not allow."""
    return ToolResult.fail(
        "forbidden",
        f"this action needs the '{scope}' scope, which this key does not have (or the server is read-only)",
        hint="Ask the workspace admin for a key with that scope (console: Settings -> AI agents).",
    )


async def resolve_agent(client: LkapClient, id_or_slug: str) -> dict[str, Any]:
    """``GET /v1/agents/{id_or_slug}`` (routes keyed by id need the id)."""
    body = await client.get(f"/v1/agents/{seg(id_or_slug)}")
    return dict(body) if isinstance(body, dict) else {}


def matches(query: str | None, *values: Any) -> bool:
    """Case-insensitive substring match of ``query`` over ``values``."""
    if not query:
        return True
    needle = query.lower()
    return any(needle in str(value).lower() for value in values if value is not None)
