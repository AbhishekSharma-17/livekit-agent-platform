"""Worker registration and heartbeat against the api (CONTRACTS-V2 §3.4, D-V2-9).

Placeholder created by V2-01 so **V2-07** can fill this module without any
other package having to add a file mid-wave. It is intentionally empty: nothing
imports it yet.

On start the worker posts ``/internal/v1/workers/register`` with its
``LKAP_CONNECTION_ID`` (unset ⇒ the default connection), image flavour, SDK
version and installed provider ids, then heartbeats every 30 s.
"""

from __future__ import annotations
