"""Leaf types shared by several contract modules.

These live here rather than in their "documentation home" (``api_models`` for
:data:`SessionChannel` and :class:`Issue`, ``agent_config`` for
:class:`ProviderRef`) purely to keep the import graph acyclic: ``flow`` needs
:class:`ProviderRef` while ``agent_config`` needs ``flow``, and ``dispatch`` needs
:data:`SessionChannel` while ``api_models`` needs ``dispatch``. Every name is
re-exported from its documented module, so importers never see this one.
"""

import zoneinfo
from functools import cache
from typing import Final, Literal

from pydantic import BaseModel

#: How a session reached the platform (CONTRACTS-V2 §4.6).
SessionChannel = Literal["web", "test", "text", "sip_in", "sip_out", "widget", "api"]

#: The participant attribute the api stamps with the caller's validated IANA timezone
#: (R-V5-10). The api drops any client-sent ``lkap.*`` attribute before minting, so the
#: worker can trust this one.
CALLER_TIMEZONE_ATTRIBUTE: Final[str] = "lkap.tz"


@cache
def _iana_timezones() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones())


def is_iana_timezone(value: object) -> bool:
    """Whether ``value`` names a timezone of this machine's IANA database (R-V5-10).

    The check is an exact match against :func:`zoneinfo.available_timezones`
    (read once and cached), so ``"Asia/Kolkata"`` and ``"UTC"`` pass while
    ``"asia/kolkata"``, ``"+05:30"``, ``""`` and non-strings do not.

    Args:
        value: The candidate name, typically ``Intl.DateTimeFormat().resolvedOptions().timeZone``.

    Returns:
        True for a known IANA name.
    """
    return isinstance(value, str) and value in _iana_timezones()


#: Severity of a single validation :class:`Issue`.
Severity = Literal["error", "warning"]


class Issue(BaseModel):
    """One addressable validation finding (UI_UX_SPEC §7.14).

    ``path`` is a dotted path into the validated document, for example
    ``"pipeline.tts"`` or ``"flow.nodes[2].instructions"``, so the console can
    focus the offending field.
    """

    path: str
    message: str
    severity: Severity = "error"


class ProviderRef(BaseModel):
    """Points at a registry provider plus the credential and options to use."""

    provider_id: str
    credential_id: str | None = None
    model: str | None = None
    fields: dict[str, str | int | float | bool] = {}
