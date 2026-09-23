"""LKAP worker supervisor (V2-04, CONTRACTS-V2 §5, ARCHITECTURE-V2 D-V2-2).

A separate service that reconciles the worker pools of every ``supervised``
LiveKit connection: desired state from the api (``GET /internal/v1/fleet/desired``),
actual state from a backend (``subprocess`` in dev, ``docker`` in prod), then
converge — start missing replicas with a just-in-time environment, drain
extras and stale-hash replicas one at a time (SIGINT, grace, then SIGKILL), and
restart failed replicas with exponential backoff.

Run it with ``python -m lkap_supervisor`` (``--once`` for a single pass).
"""

__version__ = "0.1.0"
