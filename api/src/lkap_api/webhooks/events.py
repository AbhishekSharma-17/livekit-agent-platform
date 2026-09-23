"""Outbound webhook event type names (D-V2-16, ARCHITECTURE-V2 §1)."""

from __future__ import annotations

SESSION_STARTED = "session.started"
SESSION_ENDED = "session.ended"
SESSION_QA_COMPLETED = "session.qa_completed"
RECORDING_READY = "recording.ready"
CALL_STARTED = "call.started"
CALL_ENDED = "call.ended"

#: Shown in the console's webhook subscription picker; an endpoint with an
#: empty `events` list is subscribed to every event (CONTRACTS-V2 §1.5).
KNOWN_EVENTS: tuple[str, ...] = (
    SESSION_STARTED,
    SESSION_ENDED,
    SESSION_QA_COMPLETED,
    RECORDING_READY,
    CALL_STARTED,
    CALL_ENDED,
)
