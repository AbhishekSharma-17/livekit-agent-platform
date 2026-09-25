"""Which tool definitions may bind a Composio credential (docs/v5/COMPOSIO.md §4, D-V5-C10).

``routers/tools.py::_check_payload`` asks :func:`composio_binding_problem`
whenever a tool binds a ``composio`` key or a connected-app row. Two bindings
are allowed:

* an ``mcp`` definition tagged ``origin.provider == "composio"`` (the app
  server or tool finder the api provisions, V5-47) whose url is ``https`` on
  the Composio host may bind the ``composio`` key for its ``x-api-key``
  header;
* a ``provider`` definition (V5-47) may bind the ``composio`` key as
  ``credential_id`` and a connected-app row as ``connection_id``; its
  ``subject`` must be the connection's, and a connection made for one agent
  serves only that agent's tools (:func:`provider_connection_problem`).

Anything else — above all an ``http`` tool, which could send the key to any
host — is refused.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from lkap_contracts.tool_providers import COMPOSIO_HOST, COMPOSIO_PROVIDER_ID, TOOL_PROVIDER_ACCOUNT


def is_composio_url(url: str) -> bool:
    """Whether ``url`` is ``https`` on the Composio host (no userinfo, no other port)."""
    parts = urlsplit(url.strip())
    return (
        parts.scheme.lower() == "https"
        and (parts.hostname or "").lower() == COMPOSIO_HOST
        and parts.port in (None, 443)
        and not parts.username
        and not parts.password
    )


def is_composio_credential(provider_id: str) -> bool:
    """Whether a credential row belongs to Composio (the key or a connected app)."""
    return provider_id in (COMPOSIO_PROVIDER_ID, TOOL_PROVIDER_ACCOUNT)


def composio_binding_problem(
    definition: Any, provider_id: str, *, field: str = "credential_id"
) -> str | None:
    """Why ``definition`` may not bind a Composio credential of ``provider_id``, or ``None``.

    Args:
        definition: A tool definition (``HttpToolDefinition``, ``McpServerDefinition``, or
            V5-47's ``provider`` definition); read by attribute so later kinds need no change here.
        provider_id: The bound credential row's ``provider_id``.
        field: Which field binds it: ``credential_id`` or ``connection_id``.

    Returns:
        A value-free reason, or ``None`` when the binding is allowed.
    """
    kind = getattr(definition, "kind", None)
    if kind == "provider":
        expected = COMPOSIO_PROVIDER_ID if field == "credential_id" else TOOL_PROVIDER_ACCOUNT
        if provider_id != expected:
            return (
                "a Composio action tool binds the Composio key as credential_id and an app as connection_id"
            )
        return None
    if kind == "mcp" and field == "credential_id" and provider_id == COMPOSIO_PROVIDER_ID:
        origin = getattr(definition, "origin", None)
        origin_provider = getattr(origin, "provider", None) if origin is not None else None
        if origin_provider != "composio":
            return "only a Composio app server (origin provider 'composio') may use the Composio key"
        if not is_composio_url(str(getattr(definition, "url", ""))):
            return f"the Composio key may only be sent to https://{COMPOSIO_HOST}"
        return None
    if provider_id == TOOL_PROVIDER_ACCOUNT:
        return "a connected app is used through its actions, not bound to a tool directly"
    return "the Composio key can only be used by Composio app servers and actions, never an HTTP tool"


def provider_connection_problem(
    *, definition_subject: str, connection_subject: str, tool_agent_id: str | None
) -> str | None:
    """Why a ``provider`` tool may not use a connection, or ``None`` (checklist §4 item 6).

    Args:
        definition_subject: The tool's ``subject``.
        connection_subject: The connection row's subject (``ws:<id>`` or ``agent:<id>``).
        tool_agent_id: The tool row's owning agent (``None`` = shared).

    Returns:
        A value-free reason, or ``None``.
    """
    if definition_subject != connection_subject:
        return "the tool's subject must be its connection's subject"
    if connection_subject.startswith("agent:"):
        owner = connection_subject.removeprefix("agent:")
        if tool_agent_id != owner:
            return "an app connected for one agent can only be used by that agent's own tools"
    return None


__all__ = [
    "composio_binding_problem",
    "is_composio_credential",
    "is_composio_url",
    "provider_connection_problem",
]
