"""Worker fleet bookkeeping on the api side (V2-04).

* :mod:`lkap_api.fleet.registry` — ``worker_instances`` writes (register,
  heartbeat) and the per-connection :class:`~lkap_contracts.api_models.FleetStatus`.
* :mod:`lkap_api.fleet.sweep` — marks silent workers ``gone`` and prunes old rows.

The desired state itself (``fleet_desired``) belongs to V2-03's
:mod:`lkap_api.connections.service`; the supervisor that acts on it is the
separate ``lkap_supervisor`` package.
"""
