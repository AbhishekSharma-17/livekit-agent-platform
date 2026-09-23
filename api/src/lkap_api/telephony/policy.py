"""The workspace's outbound dialing policy: default deny, one chokepoint (ruling R-V2-23).

The policy lives in ``workspaces.settings["telephony"]`` (no migration; an admin
sets it with ``PUT /v1/workspaces/{id}``, which merges ``settings``)::

    {"allowed_prefixes": ["+1", "+4420"],      # E.164 prefixes; empty = no dialing, no transfer
     "allowed_sip_hosts": ["pbx.example.com"],  # hosts a non-numeric sip: user may reach
     "max_calls_per_min": 10,                   # per-workspace bucket on POST /v1/calls
     "max_concurrent_outbound": 5}              # open outbound calls at once

Every exit to the phone network goes through :func:`check_destination`:
``POST /v1/calls``, ``POST /v1/calls/{id}/transfer``, the worker's
``POST /internal/v1/telephony/sessions/{id}/transfer`` and save-time
validation of ``config.telephony.transfer_targets`` and flow ``transfer``
nodes (:mod:`lkap_api.telephony.validation`). :data:`BLOCKED_PREFIXES`
(premium-rate and satellite ranges) always win over the allow list.

Never imports ``config_service``: that module loads the policy through
:func:`workspace_policy` for save-time validation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import Workspace
from lkap_api.errors import ApiError
from lkap_api.logging import get_logger

log = get_logger(__name__)

__all__ = [
    "BLOCKED_PREFIXES",
    "NOT_ALLOWED_TO_MODEL",
    "SETTINGS_KEY",
    "CallsBusyError",
    "DestinationNotAllowedError",
    "TelephonyPolicy",
    "check_destination",
    "destination_problem",
    "policy_of",
    "workspace_policy",
]

#: ``workspaces.settings`` key holding the policy.
SETTINGS_KEY = "telephony"

#: Ranges no policy can open: premium-rate and revenue-share numbers, and the
#: satellite / international-network codes toll fraud favours. Checked first.
BLOCKED_PREFIXES: tuple[str, ...] = (
    "+1900",  # NANP premium rate
    "+1976",  # NANP pay-per-call
    "+449",  # UK 09x premium rate
    "+4487",  # UK 087x revenue share
    "+4470",  # UK 070 "personal numbers" (premium redirect)
    "+870",  # Inmarsat SNAC
    "+871",  # Inmarsat (former ocean regions)
    "+872",
    "+873",
    "+874",
    "+881",  # Global Mobile Satellite System (Iridium, Globalstar, Thuraya)
    "+882",  # International Networks
    "+883",  # International Networks
    "+979",  # International Premium Rate Service
    "+991",  # ITPCS trial
)

#: What the worker's ``transfer_call`` tool is told when the policy refuses a target.
NOT_ALLOWED_TO_MODEL = "destination not allowed by the dialing policy"

_PREFIX = re.compile(r"^\+[0-9]{1,15}$")
_E164 = re.compile(r"^\+[1-9][0-9]{6,14}$")
_HOST = re.compile(r"^[a-z0-9.-]{1,253}$")


class DestinationNotAllowedError(ApiError):
    """422 — the destination is outside the workspace's dialing policy."""

    status_code = 422
    code = "destination_not_allowed"


class CallsBusyError(ApiError):
    """429 — the workspace (or the agent) already has its maximum of open calls."""

    status_code = 429
    code = "calls_busy"


class TelephonyPolicy(BaseModel):
    """``workspaces.settings["telephony"]``; the defaults are the default-deny policy."""

    allowed_prefixes: list[str] = Field(default_factory=list)
    allowed_sip_hosts: list[str] = Field(default_factory=list)
    max_calls_per_min: int = Field(default=10, ge=0, le=10_000)
    max_concurrent_outbound: int = Field(default=5, ge=0, le=10_000)

    @field_validator("allowed_prefixes", mode="before")
    @classmethod
    def _prefixes(cls, value: Any) -> list[str]:
        items = value if isinstance(value, list) else []
        cleaned = [re.sub(r"[\s().-]", "", str(item)) for item in items]
        return sorted({item for item in cleaned if _PREFIX.match(item)})

    @field_validator("allowed_sip_hosts", mode="before")
    @classmethod
    def _hosts(cls, value: Any) -> list[str]:
        items = value if isinstance(value, list) else []
        cleaned = [str(item).strip().lower().rstrip(".") for item in items]
        return sorted({item for item in cleaned if _HOST.match(item)})

    @property
    def dialing_enabled(self) -> bool:
        """Whether any outbound dial or transfer can pass (empty prefixes = none)."""
        return bool(self.allowed_prefixes)


def policy_of(settings: Mapping[str, Any] | None) -> TelephonyPolicy:
    """The dialing policy stored in a workspace's ``settings`` (default deny when absent or broken).

    Entries that are not an E.164 prefix / host name are dropped, never widened.
    """
    raw = (settings or {}).get(SETTINGS_KEY)
    if not isinstance(raw, Mapping):
        return TelephonyPolicy()
    try:
        return TelephonyPolicy.model_validate(dict(raw))
    except ValidationError:
        log.warning("telephony_policy_invalid", keys=sorted(str(k) for k in raw))
        return TelephonyPolicy(
            allowed_prefixes=raw.get("allowed_prefixes") or [],
            allowed_sip_hosts=raw.get("allowed_sip_hosts") or [],
        )


async def workspace_policy(db: AsyncSession, workspace_id: str) -> TelephonyPolicy:
    """Load the dialing policy of one workspace (default deny when unset)."""
    settings = await db.scalar(select(Workspace.settings).where(Workspace.id == workspace_id))
    return policy_of(settings if isinstance(settings, Mapping) else None)


def _number_and_host(target: str) -> tuple[str | None, str | None]:
    """Split a destination into ``(E.164 number, None)`` or ``(None, sip host)``.

    ``+E164`` and ``tel:+E164`` yield the number; ``sip(s):+E164@host`` yields
    the number (the prefix decides, whatever the host); ``sip(s):user@host`` with
    a non-numeric user yields the host. Anything else yields ``(None, None)``.
    """
    value = target.strip()
    lowered = value.lower()
    if lowered.startswith("tel:"):
        number = value[4:].split(";", 1)[0]
        return (number, None) if _E164.match(number) else (None, None)
    for scheme in ("sips:", "sip:"):
        if lowered.startswith(scheme):
            rest = value[len(scheme) :]
            if "@" not in rest:
                return None, None
            user, host = rest.split("@", 1)
            user = user.split(";", 1)[0]
            if _E164.match(user):
                return user, None
            host = host.split(";", 1)[0].split("?", 1)[0]
            host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
            return None, host.strip().lower().rstrip(".") or None
    return (value, None) if _E164.match(value) else (None, None)


def destination_problem(policy: TelephonyPolicy, target: str) -> str | None:
    """Why ``target`` may not be dialed or transferred to, or ``None`` when it may.

    Args:
        policy: The workspace's policy.
        target: An E.164 number or a ``tel:`` / ``sip:`` / ``sips:`` URI.
    """
    if not policy.dialing_enabled:
        return (
            "outbound calls and transfers are off for this workspace: an admin must list the "
            "allowed number prefixes in the dialing policy (Console → Telephony → Outbound dialing policy)"
        )
    number, host = _number_and_host(target)
    if number is not None:
        if any(number.startswith(prefix) for prefix in BLOCKED_PREFIXES):
            return f"{number} is in a premium-rate or satellite range that is always blocked"
        if not any(number.startswith(prefix) for prefix in policy.allowed_prefixes):
            return f"{number} does not start with an allowed prefix ({', '.join(policy.allowed_prefixes)})"
        return None
    if host is not None:
        if host in policy.allowed_sip_hosts:
            return None
        return f"SIP host '{host}' is not in the dialing policy's allowed SIP hosts"
    return "the destination is not an E.164 number (+15551234567), a tel:+… number or a sip: URI"


def check_destination(policy: TelephonyPolicy, target: str) -> None:
    """Refuse ``target`` unless the policy allows it.

    Raises:
        DestinationNotAllowedError: With ``details.allowed_prefixes`` (and the reason).
    """
    problem = destination_problem(policy, target)
    if problem is not None:
        raise DestinationNotAllowedError(
            problem,
            details={
                "reason": "no_policy" if not policy.dialing_enabled else "not_allowed",
                "allowed_prefixes": list(policy.allowed_prefixes),
                "allowed_sip_hosts": list(policy.allowed_sip_hosts),
            },
        )
