"""Recording consent (V5-15, D-V5-22): the session's consent state, the start gate, the "not recorded" note.

The worker records every consent answer as a ``consent`` session event
(``lkap_contracts.compliance.ConsentEvent``). ``routers/internal.py`` folds
each one into ``sessions.consent_state`` (:func:`apply_consent_event`: the
latest answer per kind wins), so:

* ``recordings.service.start_recording`` refuses (409) an agent that records
  only after consent (``recording.require_consent``) until a ``recording``
  consent was accepted (:func:`ensure_recording_consent`). The worker holds
  the start back itself and flushes its events first; this is the api's own
  guarantee that no Egress starts without the caller's agreement.
* When the session finishes, a consent-gated recording that never started is
  given a plain reason (:func:`note_unrecorded`): ``recording_status`` stays
  ``none`` (no new status, so no migration of its CHECK) and
  ``recording_error`` says why, which the session detail's recording block
  already carries on every branch.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from lkap_contracts.agent_config import AgentConfig
from lkap_contracts.compliance import ConsentEvent, ConsentState
from pydantic import ValidationError

from lkap_api.db.models import Session as SessionRow
from lkap_api.errors import ConflictError
from lkap_api.logging import get_logger

__all__ = [
    "NOT_RECORDED_DECLINED",
    "NOT_RECORDED_NO_ANSWER",
    "RecordingConsentMissingError",
    "apply_consent_event",
    "consent_state_of",
    "ensure_recording_consent",
    "note_unrecorded",
    "recording_waits_for_consent",
]

log = get_logger(__name__)

#: `recording_error` of a consent-gated session whose caller declined the recording.
NOT_RECORDED_DECLINED: Final[str] = "Not recorded: consent declined"
#: `recording_error` of a consent-gated session whose caller never answered.
NOT_RECORDED_NO_ANSWER: Final[str] = "Not recorded: the caller did not agree to be recorded"


class RecordingConsentMissingError(ConflictError):
    """409: the agent records only after consent, and the caller has not agreed (yet)."""


def recording_waits_for_consent(config: AgentConfig) -> bool:
    """Whether the agent records only after the caller agrees."""
    return bool(config.recording.enabled and config.recording.require_consent)


def consent_state_of(session: SessionRow) -> ConsentState:
    """The session's consent state (empty when none was recorded or the stored JSON is unreadable)."""
    raw = session.consent_state
    if not isinstance(raw, dict):
        return ConsentState()
    try:
        return ConsentState.model_validate(raw)
    except ValidationError:
        log.warning("consent_state_unreadable", session_id=session.id)
        return ConsentState()


def apply_consent_event(session: SessionRow, payload: Mapping[str, Any], *, at: float) -> bool:
    """Fold one ``consent`` event into ``session.consent_state``.

    Args:
        session: The session row (changed in place; the caller flushes).
        payload: The event's payload.
        at: The event's time (epoch seconds).

    Returns:
        Whether the payload was a valid consent answer (an invalid one is
        logged and ignored; the event row itself is still stored).
    """
    try:
        event = ConsentEvent.model_validate(dict(payload))
    except ValidationError:
        log.warning("consent_event_ignored", session_id=session.id, reason="invalid payload")
        return False
    session.consent_state = consent_state_of(session).with_event(event, at=at).model_dump(mode="json")
    log.info(
        "consent_recorded",
        session_id=session.id,
        kind=event.kind,
        accepted=event.accepted,
        method=event.method,
    )
    return True


def ensure_recording_consent(session: SessionRow, config: AgentConfig) -> None:
    """Refuse a consent-gated recording start until the caller accepted the recording.

    Raises:
        RecordingConsentMissingError: ``recording.require_consent`` is on and the
            latest ``recording`` answer is not an acceptance.
    """
    if not recording_waits_for_consent(config):
        return
    if consent_state_of(session).answer("recording") is True:
        return
    raise RecordingConsentMissingError(
        "this agent records only after the caller agrees, and they have not agreed",
        details={"session_id": session.id},
    )


def note_unrecorded(session: SessionRow, config: AgentConfig) -> None:
    """Say why a consent-gated recording never started (at the session's end).

    Only for a voice session of an agent that records after consent, whose
    recording never started (``recording_status == "none"`` and no Egress
    id); anything else is left as it is.
    """
    if not recording_waits_for_consent(config) or session.channel == "text":
        return
    if session.recording_status != "none" or session.recording_egress_id is not None:
        return
    declined = consent_state_of(session).answer("recording") is False
    session.recording_error = NOT_RECORDED_DECLINED if declined else NOT_RECORDED_NO_ANSWER
