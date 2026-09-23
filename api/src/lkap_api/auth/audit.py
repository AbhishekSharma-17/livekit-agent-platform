"""The ``audit_log`` writer (CONTRACTS-V2 §1.1: "written for every mutating admin call").

Rows are added to the request's own database session, so an audit entry
commits or rolls back together with the change it describes. Payloads carry
identifiers only — never request bodies, which may hold secrets.

``actor_type`` is ``user`` for a cookie session, ``api_key`` for a key and
``system`` for the break-glass admin token (``actor_id="break-glass"``) and for
work the platform does on its own.

Client attribution (v3, D-V3-9, R-V3-11): an AI coding agent's MCP server sends
``X-LKAP-Client: lkap-mcp/0.1; client=claude-code; tool=agent_create; call=<id>``
on every request. :func:`lkap_api.auth.deps.resolve_principal` parses it with
:func:`parse_client_header` into :data:`CLIENT_INFO`, and :func:`record` merges
``{"client": {"product", "name", "tool", "call"}}`` into the payload of every
row written while it is set. A missing or malformed header sets nothing.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Final, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import AuditLog, utcnow

ActorType = Literal["user", "api_key", "system"]

#: HTTP methods that change state and are therefore audited.
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: The attribution header an agent client sends (R-V3-11).
CLIENT_HEADER: Final = "X-LKAP-Client"
#: Longest value any one field of the header may carry.
CLIENT_VALUE_MAX: Final = 64
#: Longest header the parser looks at; anything longer is ignored whole.
_CLIENT_HEADER_MAX: Final = 512
_PRODUCT = re.compile(r"^(?P<product>[A-Za-z0-9._-]{1,64})(?:/(?P<version>[A-Za-z0-9._+-]{1,64}))?$")
_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
#: Printable, no separators: a value can never forge another field or a log line.
_VALUE = re.compile(r"^[^\x00-\x1f\x7f;=]{1,64}$")
#: Header keys and the :class:`ClientInfo` field each one fills.
_CLIENT_FIELDS: Final[dict[str, str]] = {"client": "name", "tool": "tool", "call": "call"}


@dataclass(frozen=True, slots=True)
class ClientInfo:
    """The parsed ``X-LKAP-Client`` header of the current request."""

    product: str
    version: str | None = None
    name: str | None = None
    tool: str | None = None
    call: str | None = None

    def as_payload(self) -> dict[str, str | None]:
        """The ``payload.client`` object of an audit row."""
        return {"product": self.product, "name": self.name, "tool": self.tool, "call": self.call}


#: The current request's client attribution; ``None`` when absent or malformed.
CLIENT_INFO: ContextVar[ClientInfo | None] = ContextVar("lkap_client_info", default=None)


def parse_client_header(value: str | None) -> ClientInfo | None:
    """Parse ``product/version; client=…; tool=…; call=…``.

    Unknown keys are ignored. Anything malformed — an empty or invalid product,
    a segment that is not ``key=value``, a value over :data:`CLIENT_VALUE_MAX`
    characters or holding a control character — makes the whole header count
    as absent, so a bad header never changes what is recorded.

    Args:
        value: The raw header value, or ``None``.

    Returns:
        The parsed :class:`ClientInfo`, or ``None``.
    """
    if not value or len(value) > _CLIENT_HEADER_MAX:
        return None
    head, *rest = value.split(";")
    match = _PRODUCT.match(head.strip())
    if match is None:
        return None
    fields: dict[str, str] = {}
    for segment in rest:
        if not segment.strip():
            continue
        key, sep, raw = segment.partition("=")
        key, item = key.strip().lower(), raw.strip()
        if not sep or not _KEY.match(key) or not _VALUE.match(item):
            return None
        target = _CLIENT_FIELDS.get(key)
        if target is not None and target not in fields:
            fields[target] = item
    return ClientInfo(product=match["product"], version=match["version"], **fields)


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
        payload: Small, non-secret details. While :data:`CLIENT_INFO` is set,
            its ``client`` object is merged in (and wins over a ``client`` key).

    Returns:
        The pending row.
    """
    body = dict(payload or {})
    client = CLIENT_INFO.get()
    if client is not None:
        body["client"] = client.as_payload()
    row = AuditLog(
        workspace_id=workspace_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action[:128],
        target_type=target_type[:64],
        target_id=target_id[:64] if target_id else None,
        payload=body,
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
