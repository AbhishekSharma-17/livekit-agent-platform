"""Outbound webhook event type names (D-V2-16, ARCHITECTURE-V2 §1)."""

from __future__ import annotations

SESSION_STARTED = "session.started"
SESSION_ENDED = "session.ended"
SESSION_QA_COMPLETED = "session.qa_completed"
RECORDING_READY = "recording.ready"

#: `call.started` (a call is answered) and `call.ended` (it reaches a terminal
#: state) come from `advance()` in `api/src/lkap_api/telephony/calls.py`, the one
#: chokepoint every call-status change goes through, through a `call_event`
#: outbox job so they are emitted only after the transition commits (ask #76, V2-22).
CALL_STARTED = "call.started"
CALL_ENDED = "call.ended"

#: V5-16: an MCP server's sign-in can no longer be refreshed (the provider answered
#: `invalid_grant`, or the token expired with no refresh token); an admin must sign in
#: again from the console. `data` is `{tool_id, reason}` — never a token. Delivered to
#: endpoints subscribed to every event; it joins `KNOWN_EVENTS` (the subscription picker)
#: together with the console's mirror in `web/.../settings/api-types.ts`, whose parity
#: test reads this tuple (docs/v5/_asks.md, V5-16).
TOOL_NEEDS_REAUTH = "tool.needs_reauth"

#: Shown in the console's webhook subscription picker; an endpoint with an
#: empty `events` list is subscribed to every event (CONTRACTS-V2 §1.5).
KNOWN_EVENTS: tuple[str, ...] = (
    SESSION_STARTED,
    SESSION_ENDED,
    SESSION_QA_COMPLETED,
    RECORDING_READY,
    CALL_STARTED,
    CALL_ENDED,
    TOOL_NEEDS_REAUTH,
)
