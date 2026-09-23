"""The role matrix, API-key scopes and the policy table for v1 admin routes.

CONTRACTS-V2 §3.2 orders the roles ``viewer < builder < admin < owner``. An
API key carries no role of its own; it acts as ``admin`` inside its workspace
but only for the routes its ``scopes`` cover (§3.1).

The v1 routers (agents, tools, knowledge bases, credentials, providers, packs,
sessions) still declare the single ``AdminDep`` guard. Rather than editing each
of them, :data:`ROUTE_POLICY` maps a route template and method to the role and
scope it needs, and :func:`policy_for` is consulted by that guard. Anything the
table does not match needs ``admin`` + ``*`` (fail closed). New v2 routers
declare their requirement explicitly with :func:`lkap_api.auth.deps.require`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

Role = Literal["viewer", "builder", "admin", "owner"]
ROLES: tuple[Role, ...] = get_args(Role)

_RANK: dict[str, int] = {role: rank for rank, role in enumerate(ROLES)}

#: Every scope an API key may carry (CONTRACTS-V2 §3.1); ``*`` grants all.
Scope = Literal[
    "agents:read",
    "agents:write",
    "sessions:read",
    "sessions:write",
    "calls:write",
    "connections:read",
    "connections:write",
    "providers:read",
    "providers:write",
    "webhooks:write",
    "*",
]
SCOPES: frozenset[str] = frozenset(get_args(Scope))

#: The role an API key acts with inside its own workspace.
API_KEY_ROLE: Role = "admin"


def role_at_least(role: str | None, minimum: Role) -> bool:
    """Return whether ``role`` ranks at or above ``minimum``.

    Args:
        role: The caller's role, or ``None`` for no membership.
        minimum: The role the action needs.

    Returns:
        ``True`` when the caller is allowed.
    """
    if role is None or role not in _RANK:
        return False
    return _RANK[role] >= _RANK[minimum]


def scope_allows(scopes: list[str] | tuple[str, ...] | frozenset[str], needed: str) -> bool:
    """Return whether an API key's ``scopes`` cover ``needed``.

    ``*`` covers everything and ``x:write`` implies ``x:read``.
    """
    if "*" in scopes or needed in scopes:
        return True
    if needed.endswith(":read"):
        return needed.removesuffix(":read") + ":write" in scopes
    return False


@dataclass(frozen=True)
class Requirement:
    """What one route needs: a minimum member role and an API-key scope."""

    role: Role
    scope: str


@dataclass(frozen=True)
class _Rule:
    prefix: str
    read: Requirement
    write: Requirement


_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: Fail-closed default for a route no rule matches.
DEFAULT_REQUIREMENT = Requirement("admin", "*")

#: Route-template prefixes of the admin surface; the longest match wins.
ROUTE_POLICY: tuple[_Rule, ...] = (
    _Rule("/v1/agents", Requirement("viewer", "agents:read"), Requirement("builder", "agents:write")),
    _Rule("/v1/tools", Requirement("viewer", "agents:read"), Requirement("builder", "agents:write")),
    _Rule(
        "/v1/knowledge-bases",
        Requirement("viewer", "agents:read"),
        Requirement("builder", "agents:write"),
    ),
    _Rule("/v1/packs", Requirement("viewer", "agents:read"), Requirement("builder", "agents:write")),
    _Rule("/v1/flows", Requirement("viewer", "agents:read"), Requirement("builder", "agents:write")),
    _Rule(
        "/v1/sessions",
        Requirement("viewer", "sessions:read"),
        Requirement("builder", "sessions:write"),
    ),
    _Rule("/v1/analytics", Requirement("viewer", "sessions:read"), Requirement("admin", "sessions:write")),
    _Rule("/v1/calls", Requirement("viewer", "sessions:read"), Requirement("builder", "calls:write")),
    # Builders pick credentials in the agent editor; only admins create or change them.
    _Rule(
        "/v1/credentials",
        Requirement("builder", "providers:read"),
        Requirement("admin", "providers:write"),
    ),
    _Rule(
        "/v1/providers",
        Requirement("viewer", "providers:read"),
        Requirement("admin", "providers:write"),
    ),
    _Rule(
        "/v1/connections",
        Requirement("viewer", "connections:read"),
        Requirement("admin", "connections:write"),
    ),
    _Rule(
        "/v1/telephony",
        Requirement("viewer", "connections:read"),
        Requirement("admin", "connections:write"),
    ),
    _Rule("/v1/webhooks", Requirement("admin", "webhooks:write"), Requirement("admin", "webhooks:write")),
)


def policy_for(method: str, route_path: str) -> Requirement:
    """Return the requirement of a v1 admin route.

    Args:
        method: The HTTP method.
        route_path: The route *template* (``/v1/agents/{agent_id}``), not the
            concrete URL, so path parameters can never smuggle a prefix.

    Returns:
        The matching :class:`Requirement`, or :data:`DEFAULT_REQUIREMENT`.
    """
    matches = [
        rule for rule in ROUTE_POLICY if route_path == rule.prefix or route_path.startswith(rule.prefix + "/")
    ]
    if not matches:
        return DEFAULT_REQUIREMENT
    rule = max(matches, key=lambda r: len(r.prefix))
    return rule.read if method.upper() in _READ_METHODS else rule.write
