"""The ``audit_log`` writer (CONTRACTS-V2 §1.1: "written for every mutating admin call").

Rows are added to the request's own database session, so an audit entry
commits or rolls back together with the change it describes. Payloads carry
identifiers only — never request bodies, which may hold secrets.

``actor_type`` is ``user`` for a cookie session, ``api_key`` for a key and
``system`` for the break-glass admin token (``actor_id="break-glass"``) and for
work the platform does on its own.
"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import AuditLog, utcnow

ActorType = Literal["user", "api_key", "system"]

#: HTTP methods that change state and are therefore audited.
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def record(
    db: AsyncSession,
    *,
    workspace_id: str | None,
    actor_type: ActorType,
    actor_id: str | None,
    action: str,
    target_type: str = "",
    target_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditLog:
    """Add one audit row to ``db`` (flushed and committed with the request).

    Args:
        db: The request session.
        workspace_id: The workspace acted in, ``None`` for platform-level events.
        actor_type: Who acted (see module docstring).
        actor_id: The user id, API-key id or ``break-glass``.
        action: A stable verb, e.g. ``api_key.create`` or ``PUT /v1/agents/{agent_id}``.
        target_type: The kind of object acted on (``agent``, ``member`` …).
        target_id: Its id, when there is one.
        payload: Small, non-secret details.

    Returns:
        The pending row.
    """
    row = AuditLog(
        workspace_id=workspace_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action[:128],
        target_type=target_type[:64],
        target_id=target_id[:64] if target_id else None,
        payload=payload or {},
        ts=utcnow(),
    )
    db.add(row)
    return row


def route_target(route_path: str, path_params: dict[str, Any]) -> tuple[str, str | None]:
    """Derive ``(target_type, target_id)`` from a route template and its parameters.

    ``/v1/agents/{agent_id}`` with ``{"agent_id": "a1"}`` gives ``("agents", "a1")``.
    """
    parts = [part for part in route_path.split("/") if part and part != "v1"]
    target_type = parts[0] if parts else ""
    target_id = next((str(value) for value in path_params.values()), None)
    return target_type, target_id
