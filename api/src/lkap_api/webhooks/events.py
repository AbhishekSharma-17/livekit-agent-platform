"""Outbound webhook event type names (D-V2-16, ARCHITECTURE-V2 §1)."""

from __future__ import annotations

SESSION_STARTED = "session.started"
SESSION_ENDED = "session.ended"
SESSION_QA_COMPLETED = "session.qa_completed"
RECORDING_READY = "recording.ready"

#: `call.started`/`call.ended` are named in ARCHITECTURE-V2 §1 but have no
#: emitter (docs/v2/_asks.md V2-20-1) and are deliberately left out of
#: `KNOWN_EVENTS` below rather than faked. Every call-status transition
#: ("answered", "completed"/"failed"/...) happens through the single
#: `advance()` chokepoint in `api/src/lkap_api/telephony/calls.py` — called
#: from `run_dial` (background task, outbound), `apply_report` (worker
#: report, either direction) and `hangup`/`transfer`/`sweep_stuck_calls` — but
#: that module is V2-21's (security review) exclusive file this wave, so
#: V2-20F could not add the `webhooks.emit(..., CALL_STARTED/CALL_ENDED, ...)`
#: calls there; a one-line ask is filed in `docs/v2/_asks.md` under "Open —
#: left by V2-20F" for whoever next owns `telephony/**`. The constants stay
#: defined so that follow-up is a pure addition, not a rename.
CALL_STARTED = "call.started"
CALL_ENDED = "call.ended"

#: Shown in the console's webhook subscription picker; an endpoint with an
#: empty `events` list is subscribed to every event (CONTRACTS-V2 §1.5).
#: `CALL_STARTED`/`CALL_ENDED` are intentionally absent — see the comment above.
KNOWN_EVENTS: tuple[str, ...] = (
    SESSION_STARTED,
    SESSION_ENDED,
    SESSION_QA_COMPLETED,
    RECORDING_READY,
)
