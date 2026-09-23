"""SIP call handling on the worker side (CONTRACTS-V2 §1.6, stretch).

Placeholder created by V2-01 so **V2-17** can fill this module without any
other package having to add a file mid-wave. It is intentionally empty: nothing
imports it yet.

Inbound SIP rooms have no ``connect`` call, so the worker creates the session
through ``POST /internal/v1/sessions/start`` using the SIP participant
attributes, and emits ``dtmf``/``transfer`` events.
"""

from __future__ import annotations
