"""Vendor charge lookups for cost reconciliation (V4-17, docs/v4/COSTS.md D-V4-45, R-V4-48).

Reconciliation compares LKAP's list-price cost with what the vendor itself
charged. It is used **only** where a regular (non-admin) key returns a charge
per request, and only when the workspace opted in:

* :mod:`lkap_api.costs.vendors.openrouter` — built: ``GET /api/v1/generation?id=``
  per ``gen-…`` id the worker reported.
* :mod:`lkap_api.costs.vendors.deepgram` — designed, not built: the per-request
  lookup needs a project id the credential form does not collect (ask #94).

The opt-in lives in ``workspaces.settings["cost"]["reconcile"]`` (a list of vendor
names) and reaches the worker as ``ResolvedAgentConfig.cost_reconcile``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import SessionEvent, Workspace

__all__ = [
    "BUILT_VENDORS",
    "PROVIDER_REQUESTS_KIND",
    "RECONCILE_KEY",
    "RECONCILE_VENDORS",
    "ReconcileSettingError",
    "provider_requests_of",
    "reconcile_vendors",
    "request_ids",
    "validate_reconcile",
    "workspace_reconcile_vendors",
]

#: The ``metrics`` event kind the worker posts the per-request ids under (docs/CONTRACTS.md §7).
PROVIDER_REQUESTS_KIND: Final = "provider_requests"

#: The key under ``workspaces.settings["cost"]`` that holds the opt-in list.
RECONCILE_KEY: Final = "reconcile"

#: Every vendor name the opt-in may name (D-V4-45).
RECONCILE_VENDORS: Final[frozenset[str]] = frozenset({"openrouter", "deepgram"})

#: Vendors with a working client today. ``deepgram`` waits for ask #94 (a ``project_id`` field).
BUILT_VENDORS: Final[frozenset[str]] = frozenset({"openrouter"})


class ReconcileSettingError(ValueError):
    """A ``settings["cost"]["reconcile"]`` value that cannot be stored."""


def reconcile_vendors(settings: Mapping[str, Any] | None) -> list[str]:
    """The vendors a workspace reconciles, read leniently from its ``settings``.

    Anything malformed (not a list, not a string, an unknown vendor) is ignored
    rather than failing a session start.

    Args:
        settings: ``workspaces.settings`` (may be ``None``).

    Returns:
        The opted-in vendor names, deduplicated, in stored order.
    """
    cost = (settings or {}).get("cost")
    raw = cost.get(RECONCILE_KEY) if isinstance(cost, Mapping) else None
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(v for v in raw if isinstance(v, str) and v in RECONCILE_VENDORS))


def validate_reconcile(value: Any) -> list[str]:
    """Validate an admin's opt-in list before it is stored.

    Raises:
        ReconcileSettingError: Not a list of known vendor names, or a vendor whose
            client is not built yet (``deepgram``, ask #94).
    """
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ReconcileSettingError("cost.reconcile must be a list of vendor names")
    unknown = sorted(set(value) - RECONCILE_VENDORS)
    if unknown:
        raise ReconcileSettingError(
            f"cost.reconcile names unknown vendors {unknown}; allowed: {sorted(RECONCILE_VENDORS)}"
        )
    unbuilt = sorted(set(value) - BUILT_VENDORS)
    if unbuilt:
        raise ReconcileSettingError(
            f"cost.reconcile: {unbuilt} cannot be reconciled yet "
            "(Deepgram needs a project id on the credential); only 'openrouter' is available"
        )
    return list(dict.fromkeys(value))


async def workspace_reconcile_vendors(db: AsyncSession, workspace_id: str) -> list[str]:
    """:func:`reconcile_vendors` for one workspace row (``[]`` when it does not exist)."""
    workspace = await db.get(Workspace, workspace_id)
    return reconcile_vendors(workspace.settings if workspace is not None else None)


async def provider_requests_of(db: AsyncSession, session_id: str) -> dict[str, Any] | None:
    """The ``data`` of a session's last ``metrics {kind: "provider_requests"}`` event, or ``None``."""
    payloads = (
        await db.execute(
            select(SessionEvent.payload)
            .where(SessionEvent.session_id == session_id, SessionEvent.type == "metrics")
            .order_by(SessionEvent.id.desc())
        )
    ).scalars()
    for payload in payloads:
        if isinstance(payload, dict) and payload.get("kind") == PROVIDER_REQUESTS_KIND:
            data = payload.get("data")
            return data if isinstance(data, dict) else None
    return None


def request_ids(data: Mapping[str, Any] | None, kind: str) -> list[str]:
    """The non-empty ``request_id`` strings of one kind (``llm``/``stt``/``tts``), deduplicated."""
    rows = (data or {}).get(kind)
    if not isinstance(rows, list):
        return []
    ids = (row.get("request_id") for row in rows if isinstance(row, Mapping))
    return list(dict.fromkeys(i for i in ids if isinstance(i, str) and i))
