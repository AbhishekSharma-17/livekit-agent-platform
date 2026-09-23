"""Prometheus metrics (CONTRACTS-V2 §5), served on ``:9105/metrics`` by default.

* ``lkap_supervisor_replicas{connection,state}`` — replicas per pool and state.
* ``lkap_supervisor_reconcile_seconds`` — duration of each reconcile pass.
* ``lkap_supervisor_restarts_total{connection}`` — restarts of failed replicas.
"""

from __future__ import annotations

from collections import Counter as Tally
from collections.abc import Iterable

from lkap_contracts.fleet import ReplicaHandle
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server


class Metrics:
    """The supervisor's metrics, on their own registry (tests build as many as they like)."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        """Create the metric families on ``registry`` (a fresh one by default)."""
        self.registry = registry or CollectorRegistry()
        self.replicas = Gauge(
            "lkap_supervisor_replicas",
            "Replicas per connection pool and state.",
            ["connection", "state"],
            registry=self.registry,
        )
        self.reconcile_seconds = Histogram(
            "lkap_supervisor_reconcile_seconds",
            "Duration of one reconcile pass.",
            registry=self.registry,
        )
        self.restarts = Counter(
            "lkap_supervisor_restarts",
            "Restarts of failed replicas.",
            ["connection"],
            registry=self.registry,
        )

    def set_replicas(self, handles: Iterable[ReplicaHandle]) -> None:
        """Replace the replica gauge with the current handle states."""
        counts = Tally((h.connection_id, h.state) for h in handles)
        self.replicas.clear()
        for (connection, state), count in counts.items():
            self.replicas.labels(connection=connection, state=state).set(count)

    def serve(self, port: int) -> None:
        """Expose ``/metrics`` on ``port`` (background thread)."""
        start_http_server(port, registry=self.registry)
