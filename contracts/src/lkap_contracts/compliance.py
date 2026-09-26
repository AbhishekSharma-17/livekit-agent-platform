"""Consent and AI disclosure (V5-15, D-V5-22): workspace presets, the ``consent`` event, the session state.

Three pieces live here so the worker, the api and the console share one wording
and one hash:

* **Workspace compliance settings** (``workspaces.settings.compliance``,
  :class:`ComplianceSettings`): a jurisdiction (``eu``, ``in``, ``us``; default
  ``in``) whose :data:`COMPLIANCE_PRESETS` supply the AI disclosure line and the
  recording consent question, each of which the workspace may rewrite.
  :func:`resolve_compliance` turns the stored settings into the effective texts
  (:class:`ResolvedCompliance`) the api hands the worker. The presets are plain
  wording, not legal advice: each carries a "confirm with counsel" note and no
  state or country list is encoded (D-V5-22).
* **The ``consent`` session event** (:class:`ConsentEvent`): what the caller
  agreed or declined, how (a tap or their voice) and the SHA-256 of the exact
  text they were shown or read (:func:`consent_text_hash`), so an audit can tie
  the answer to the wording.
* **``sessions.consent_state``** (:class:`ConsentState`): the latest answer per
  consent kind, merged by the api from the events; the recording start refuses
  to run on an agent that requires consent until ``recording`` is accepted.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

__all__ = [
    "COMPLIANCE_KEY",
    "COMPLIANCE_PRESETS",
    "CONSENT_EVENT",
    "DEFAULT_JURISDICTION",
    "MAX_CONSENT_TEXT_CHARS",
    "TEXT_HASH_PATTERN",
    "ComplianceOut",
    "CompliancePreset",
    "ComplianceSettings",
    "ConsentDeclineAction",
    "ConsentEvent",
    "ConsentKind",
    "ConsentMethod",
    "ConsentRecord",
    "ConsentState",
    "DisclosurePosition",
    "Jurisdiction",
    "ResolvedCompliance",
    "consent_text_hash",
    "resolve_compliance",
]

#: Where a workspace's preset applies (D-V5-22). ``in`` (India) is the default market.
Jurisdiction = Literal["eu", "in", "us"]
DEFAULT_JURISDICTION: Final[Jurisdiction] = "in"

#: What a consent covers: being recorded, talking to an AI, terms of service, or anything else.
ConsentKind = Literal["recording", "ai_disclosure", "terms", "custom"]
#: How the caller answered: a tap on the screen, or out loud (``record_consent``).
ConsentMethod = Literal["tap", "voice"]
#: What a declined *required* consent does: carry on, or say goodbye and end the call.
ConsentDeclineAction = Literal["continue", "end_call"]
#: Where the AI disclosure appears: spoken in the greeting, shown as a banner on screen, or both.
DisclosurePosition = Literal["greeting", "banner", "both"]

#: ``workspaces.settings`` key of the compliance settings.
COMPLIANCE_KEY: Final[str] = "compliance"
#: The session event type the worker records for every answer (docs/CONTRACTS.md §7).
CONSENT_EVENT: Final[str] = "consent"
#: Longest disclosure or consent text anywhere (a block, an agent, a workspace).
MAX_CONSENT_TEXT_CHARS: Final[int] = 2000
#: A lowercase hex SHA-256 digest.
TEXT_HASH_PATTERN: Final[str] = r"^[0-9a-f]{64}$"


def consent_text_hash(text: str) -> str:
    """The SHA-256 of ``text`` as UTF-8, lowercase hex: what a ``consent`` event carries.

    The text is hashed exactly as shown or read (no trimming or normalisation),
    so the worker and anyone auditing later must hash the same string.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CompliancePreset(BaseModel):
    """One jurisdiction's starting wording (Settings → Compliance), used where nothing is rewritten."""

    jurisdiction: Jurisdiction
    label: str
    disclosure_text: str
    """Said or shown so callers know they are talking to an AI."""
    recording_text: str
    """The question the agent asks before a recording starts."""
    counsel_note: str
    """Why the wording is only a starting point; the console asks the builder to acknowledge it."""


#: The three presets (D-V5-22). Plain wording; nothing here is legal advice.
COMPLIANCE_PRESETS: Final[dict[Jurisdiction, CompliancePreset]] = {
    "eu": CompliancePreset(
        jurisdiction="eu",
        label="European Union",
        disclosure_text="Just so you know, you're talking to an AI assistant, not a person.",
        recording_text=(
            "We'd like to record this call so we keep an accurate record of it. Is that okay with you?"
        ),
        counsel_note=(
            "EU law requires telling people when they are talking to an AI. "
            "This wording is a starting point: confirm it with counsel."
        ),
    ),
    "in": CompliancePreset(
        jurisdiction="in",
        label="India",
        disclosure_text="Just so you know, you're talking to an AI assistant.",
        recording_text="This call can be recorded so we have a record of it. Is it okay if we record it?",
        counsel_note=(
            "India's data protection rules on notice and consent are being phased in. "
            "This wording is a starting point: confirm it with counsel."
        ),
    ),
    "us": CompliancePreset(
        jurisdiction="us",
        label="United States",
        disclosure_text="Just so you know, you're talking to an automated AI assistant.",
        recording_text=(
            "We'd like to record this call for our records. Do we have your permission to record it?"
        ),
        counsel_note=(
            "Some states require everyone on a call to agree before it is recorded: "
            "confirm with counsel for two-party-consent states."
        ),
    ),
}


def _blank_to_none(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value


class ComplianceSettings(BaseModel):
    """``workspaces.settings.compliance``: the jurisdiction and any rewritten wording (Settings → Compliance).

    A text left empty (``None`` or blank) uses the jurisdiction's preset.
    """

    model_config = ConfigDict(extra="forbid")

    jurisdiction: Jurisdiction = DEFAULT_JURISDICTION
    disclosure_text: str | None = Field(
        default=None,
        max_length=MAX_CONSENT_TEXT_CHARS,
        description="The AI disclosure line; empty uses the jurisdiction's preset.",
    )
    recording_text: str | None = Field(
        default=None,
        max_length=MAX_CONSENT_TEXT_CHARS,
        description="The question asked before recording; empty uses the jurisdiction's preset.",
    )
    counsel_note_ack: bool = Field(
        default=False, description="The builder acknowledged that the wording must be checked with counsel."
    )

    @field_validator("disclosure_text", "recording_text")
    @classmethod
    def _blank_is_preset(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class ResolvedCompliance(BaseModel):
    """The effective wording for one workspace: its rewrites, else its jurisdiction's preset."""

    jurisdiction: Jurisdiction = DEFAULT_JURISDICTION
    disclosure_text: str = COMPLIANCE_PRESETS[DEFAULT_JURISDICTION].disclosure_text
    recording_text: str = COMPLIANCE_PRESETS[DEFAULT_JURISDICTION].recording_text


def resolve_compliance(settings: ComplianceSettings | Mapping[str, Any] | None) -> ResolvedCompliance:
    """The effective compliance wording of a workspace.

    Args:
        settings: ``workspaces.settings.compliance`` (a model or the stored dict);
            ``None`` or a dict that does not validate means the default preset.

    Returns:
        The workspace's rewrites where set, else its jurisdiction's preset texts.
    """
    parsed: ComplianceSettings
    if isinstance(settings, ComplianceSettings):
        parsed = settings
    else:
        try:
            parsed = ComplianceSettings.model_validate(dict(settings or {}))
        except ValidationError:
            parsed = ComplianceSettings()
    preset = COMPLIANCE_PRESETS[parsed.jurisdiction]
    return ResolvedCompliance(
        jurisdiction=parsed.jurisdiction,
        disclosure_text=parsed.disclosure_text or preset.disclosure_text,
        recording_text=parsed.recording_text or preset.recording_text,
    )


class ComplianceOut(BaseModel):
    """``GET /v1/workspaces/{id}/compliance``: the stored settings, the effective texts and every preset."""

    settings: ComplianceSettings
    effective: ResolvedCompliance
    presets: list[CompliancePreset]


class ConsentEvent(BaseModel):
    """Payload of the ``consent`` session event: one answer to one consent question."""

    kind: ConsentKind
    accepted: bool
    method: ConsentMethod
    text_hash: str = Field(pattern=TEXT_HASH_PATTERN, description="SHA-256 of the exact text, lowercase hex.")
    block_id: str | None = Field(default=None, description="The consent block answered, if there is one.")


class ConsentRecord(ConsentEvent):
    """One answer as ``sessions.consent_state`` keeps it: the event plus when it happened (epoch seconds)."""

    at: float


class ConsentState(BaseModel):
    """``sessions.consent_state``: the latest answer per consent kind (a later answer replaces it)."""

    latest: dict[ConsentKind, ConsentRecord] = {}

    def answer(self, kind: ConsentKind) -> bool | None:
        """``True``/``False`` when the caller answered ``kind``, ``None`` when they never did."""
        record = self.latest.get(kind)
        return record.accepted if record is not None else None

    def with_event(self, event: ConsentEvent, *, at: float) -> ConsentState:
        """A copy with ``event`` recorded as the latest answer of its kind."""
        latest = dict(self.latest)
        latest[event.kind] = ConsentRecord(**event.model_dump(), at=at)
        return ConsentState(latest=latest)
