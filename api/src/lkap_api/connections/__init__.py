"""LiveKit connections: one row per LiveKit deployment agents can be bound to.

CONTRACTS-V2 §1.2/§3.4, ARCHITECTURE-V2 D-V2-1…D-V2-6. The package is split by
concern so later packages can depend on one piece without pulling the rest:

* :mod:`~lkap_api.connections.clients` — :class:`ConnectionClientFactory`, the
  only place a connection's key/secret is decrypted for LiveKit calls and
  participant tokens.
* :mod:`~lkap_api.connections.probe` — capability flags and the ``test`` probe.
* :mod:`~lkap_api.connections.service` — CRUD, rotation, the default
  connection, agent → connection resolution and the ``fleet_desired`` writer
  the supervisor (V2-04) reads.
* :mod:`~lkap_api.connections.bundle` — worker-env templates and the
  ``cloud_hosted`` deploy bundle.
* :mod:`~lkap_api.connections.webhooks` — per-connection LiveKit webhook
  verification and the event-handler registry.
"""
