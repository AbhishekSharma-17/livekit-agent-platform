"""Leaf types shared by several contract modules.

These live here rather than in their "documentation home" (``api_models`` for
:data:`SessionChannel` and :class:`Issue`, ``agent_config`` for
:class:`ProviderRef`) purely to keep the import graph acyclic: ``flow`` needs
:class:`ProviderRef` while ``agent_config`` needs ``flow``, and ``dispatch`` needs
:data:`SessionChannel` while ``api_models`` needs ``dispatch``. Every name is
re-exported from its documented module, so importers never see this one.
"""

from typing import Literal

from pydantic import BaseModel

#: How a session reached the platform (CONTRACTS-V2 §4.6).
SessionChannel = Literal["web", "test", "text", "sip_in", "sip_out", "widget", "api"]

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
