"""Who a connected account is signed in as (V6-35, docs/v5/COMPOSIO.md §2 "Account identity").

Several accounts of one app (R-V5-13) are only useful when a person can tell them
apart, so every account carries an ``identity``: the address, user name or
workspace name the app itself reports. It is learnt in this order, and never
guessed:

1. ``state.val.displayName`` on the connected account, which Composio fills "when
   available" once the account is active (no extra call).
2. The app's own "who am I" action, run once through Composio on *that* account
   (``connected_account_id`` is always pinned: an unpinned execute runs on the
   subject's most recently connected account, which would stamp the wrong
   identity on an older account). Only the configured fields of the answer are
   read, and each value must look like what it claims to be (an address has an
   ``@`` and a dot after it, a user name is one short token, nothing is a bare
   number or a link).
3. Nothing: the identity stays empty and the console offers "Check now".

Each lookup is bounded by :data:`IDENTITY_TIMEOUT_S` and every failure (vendor
error, timeout, an answer of an unexpected shape) leaves the identity empty; it
never fails a connect, a callback or a check. Nothing here logs the identity or
the vendor's answer, and no token is ever read or stored: the identity string is
the only thing kept.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from lkap_contracts.tool_providers import IdentityKind

from lkap_api.logging import get_logger
from lkap_api.tool_providers.adapter import ToolProviderAdapter, ToolProviderError, scrub_vendor_text

log = get_logger(__name__)

#: The longest identity kept (the contract's ``max_length``).
MAX_IDENTITY: Final = 200

#: Upper bound on one identity lookup (the callback ends in a browser redirect, so keep it short).
IDENTITY_TIMEOUT_S: Final = 6.0

#: A check does not ask again for an unidentified account more often than this (each ask is a
#: metered Composio tool call, and the console reads every account on each view).
IDENTITY_RETRY: Final = dt.timedelta(minutes=15)

#: A known identity is asked for again on a check after this long (it rarely changes).
IDENTITY_TTL: Final = dt.timedelta(hours=24)

_EMAIL_RE: Final = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


@dataclass(frozen=True)
class Identity:
    """An account's identity: a display string and what it is."""

    value: str
    kind: IdentityKind


@dataclass(frozen=True)
class IdentityField:
    """Where an identity sits in an answer (a path of keys) and what it must look like.

    ``template`` formats the value (``"@{}"`` for a user name); ``suffix`` is a second
    path whose value, when present and plain, is appended as `` · <suffix>`` (the Slack
    workspace after the user name).
    """

    path: tuple[str, ...]
    kind: IdentityKind
    template: str = "{}"
    suffix: tuple[str, ...] | None = None


@dataclass(frozen=True)
class IdentityProbe:
    """How to ask one app who the account is: a Composio action, or a GET through Composio's proxy."""

    fields: tuple[IdentityField, ...]
    tool: str | None = None
    arguments: Mapping[str, Any] = field(default_factory=dict)
    proxy_endpoint: str | None = None


def _email(*path: str) -> IdentityField:
    return IdentityField(path=path, kind="email")


#: Per toolkit slug, how to learn the account's identity (V6-35). A toolkit not listed relies
#: on ``state.val.displayName`` alone. Sources are recorded in docs/v5/COMPOSIO.md §2.
PROBES: Final[dict[str, IdentityProbe]] = {
    # GMAIL_GET_PROFILE answers {emailAddress, messagesTotal, threadsTotal, historyId}.
    "gmail": IdentityProbe(
        tool="GMAIL_GET_PROFILE", arguments={"user_id": "me"}, fields=(_email("emailAddress"),)
    ),
    # "Returns the account identifier, email when available, display name, timezone, and
    # primary calendar ID" (field names not documented; only an address-shaped value is taken).
    "googlecalendar": IdentityProbe(
        tool="GOOGLECALENDAR_GET_CURRENT_USER",
        fields=(_email("email"), _email("primary_calendar_id"), _email("calendar_id"), _email("id")),
    ),
    # Drive's about resource: user.emailAddress.
    "googledrive": IdentityProbe(tool="GOOGLEDRIVE_GET_ABOUT", fields=(_email("user", "emailAddress"),)),
    # GitHub's GET /user: login.
    "github": IdentityProbe(
        tool="GITHUB_GET_THE_AUTHENTICATED_USER",
        fields=(IdentityField(path=("login",), kind="username", template="@{}"),),
    ),
    # Slack's auth.test: user and team.
    "slack": IdentityProbe(
        tool="SLACK_TEST_AUTH",
        fields=(
            IdentityField(path=("user",), kind="username", template="@{}", suffix=("team",)),
            IdentityField(path=("team",), kind="workspace"),
        ),
    ),
    # Microsoft Graph GET /me through Composio's proxy: mail, else userPrincipalName.
    "outlook": IdentityProbe(
        proxy_endpoint="https://graph.microsoft.com/v1.0/me",
        fields=(_email("mail"), _email("userPrincipalName")),
    ),
    # Notion's bot user: the owner's address, else the workspace's name.
    "notion": IdentityProbe(
        tool="NOTION_GET_ABOUT_ME",
        fields=(
            _email("bot", "owner", "user", "person", "email"),
            IdentityField(path=("bot", "workspace_name"), kind="workspace"),
        ),
    ),
    # Linear's viewer: email, else name.
    "linear": IdentityProbe(
        tool="LINEAR_GET_CURRENT_USER",
        fields=(
            _email("viewer", "email"),
            _email("email"),
            IdentityField(path=("viewer", "name"), kind="other"),
        ),
    ),
    # Jira's myself: emailAddress (may be hidden by the user's privacy settings), else displayName.
    "jira": IdentityProbe(
        tool="JIRA_GET_CURRENT_USER",
        fields=(_email("emailAddress"), IdentityField(path=("displayName",), kind="other")),
    ),
}


# ============================================================================ validation
def clean(value: object, kind: IdentityKind) -> str | None:
    """``value`` as a safe identity of ``kind``, or ``None`` when it does not look like one."""
    if not isinstance(value, str):
        return None
    raw = " ".join(value.split())
    if not raw or len(raw) > MAX_IDENTITY or raw.isdigit():
        return None
    text = scrub_vendor_text(raw, limit=MAX_IDENTITY)
    if text != raw:  # a link, or something the scrubber had to change: not an identity
        return None
    match kind:
        case "email":
            return text if _EMAIL_RE.match(text) else None
        case "username":
            return text if _USERNAME_RE.match(text) else None
        case _:
            return text


def from_display_name(account: Mapping[str, Any]) -> Identity | None:
    """The identity Composio reports on the connected account (``state.val.displayName``)."""
    for key in ("state", "connectionData", "data"):
        section = account.get(key)
        if not isinstance(section, dict):
            continue
        val = section.get("val")
        value = val if isinstance(val, dict) else section
        name = value.get("displayName") or value.get("display_name")
        if (email := clean(name, "email")) is not None:
            return Identity(email, "email")
        if (other := clean(name, "other")) is not None:
            return Identity(other, "other")
    return None


def _walk(data: Mapping[str, Any], path: tuple[str, ...]) -> object:
    node: object = data
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _candidates(payload: object) -> list[Mapping[str, Any]]:
    """The objects an action's ``data`` may hold the answer in (it may arrive as a JSON string)."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return []
    if not isinstance(payload, dict):
        return []
    out: list[Mapping[str, Any]] = [payload]
    for key in ("response_data", "data"):
        inner = payload.get(key)
        if isinstance(inner, dict):
            out.append(inner)
    return out


def extract(probe: IdentityProbe, payload: object) -> Identity | None:
    """The first configured field of ``payload`` that is a valid identity, formatted."""
    candidates = _candidates(payload)
    for spec in probe.fields:
        for data in candidates:
            value = clean(_walk(data, spec.path), spec.kind)
            if value is None:
                continue
            text = spec.template.format(value)
            if spec.suffix is not None:
                suffix = clean(_walk(data, spec.suffix), "workspace")
                if suffix is not None:
                    text = f"{text} · {suffix}"
            return Identity(text[:MAX_IDENTITY], spec.kind)
    return None


# ============================================================================ lookup
async def _ask(
    adapter: ToolProviderAdapter, probe: IdentityProbe, *, subject: str, connected_account_id: str
) -> Identity | None:
    if probe.proxy_endpoint is not None:
        answer = await adapter.proxy(
            endpoint=probe.proxy_endpoint, method="GET", connected_account_id=connected_account_id
        )
        status = answer.get("status")
        if isinstance(status, int) and not 200 <= status < 300:
            return None
        return extract(probe, answer.get("data"))
    assert probe.tool is not None
    answer = await adapter.execute(
        probe.tool,
        subject=subject,
        connected_account_id=connected_account_id,
        arguments=dict(probe.arguments),
    )
    if answer.get("successful") is False or answer.get("error"):
        return None
    return extract(probe, answer.get("data"))


async def identify(
    adapter: ToolProviderAdapter,
    *,
    toolkit: str,
    subject: str,
    connected_account_id: str | None,
    account: Mapping[str, Any] | None = None,
    connection_id: str | None = None,
) -> Identity | None:
    """Who the account is: Composio's display name, else the app's own answer, else ``None``.

    Never raises: a vendor error, a timeout or an answer of another shape is ``None``.

    Args:
        adapter: The workspace's Composio adapter.
        toolkit: The account's toolkit slug.
        subject: The connection's Composio ``user_id``.
        connected_account_id: The account to ask about (pinned on the call).
        account: The connected account as Composio returned it, when already fetched.
        connection_id: For the log line only.

    Returns:
        The identity, or ``None`` when it could not be learnt.
    """
    found = from_display_name(account) if account else None
    if found is not None:
        log.info(
            "apps_identity", connection_id=connection_id, toolkit=toolkit, source="display_name", found=True
        )
        return found
    probe = PROBES.get(toolkit.lower())
    if probe is None or not connected_account_id:
        return None
    try:
        found = await asyncio.wait_for(
            _ask(adapter, probe, subject=subject, connected_account_id=connected_account_id),
            timeout=IDENTITY_TIMEOUT_S,
        )
    except TimeoutError:
        log.info("apps_identity_failed", connection_id=connection_id, toolkit=toolkit, reason="timeout")
        return None
    except ToolProviderError as exc:
        log.info("apps_identity_failed", connection_id=connection_id, toolkit=toolkit, reason=exc.reason)
        return None
    except Exception as exc:  # noqa: BLE001 - an identity lookup must never break a connect or a check
        log.info(
            "apps_identity_failed", connection_id=connection_id, toolkit=toolkit, reason=type(exc).__name__
        )
        return None
    log.info(
        "apps_identity",
        connection_id=connection_id,
        toolkit=toolkit,
        source="action",
        found=found is not None,
        kind=found.kind if found else None,
    )
    return found


def due(
    *,
    identity: str | None,
    checked_at: dt.datetime | None,
    now: dt.datetime,
    force: bool = False,
) -> bool:
    """Whether a check should ask the app again (activation always does; see the callers)."""
    if force or checked_at is None:
        return True
    age = now - checked_at
    return age >= (IDENTITY_TTL if identity else IDENTITY_RETRY)


__all__ = [
    "IDENTITY_RETRY",
    "IDENTITY_TIMEOUT_S",
    "IDENTITY_TTL",
    "MAX_IDENTITY",
    "PROBES",
    "Identity",
    "IdentityField",
    "IdentityProbe",
    "clean",
    "due",
    "extract",
    "from_display_name",
    "identify",
]
