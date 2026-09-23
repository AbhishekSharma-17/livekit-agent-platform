"""Worker fleet contracts shared by the api, the supervisor and the worker.

CONTRACTS-V2 §5: the api publishes the desired state of every *supervised*
connection pool, the supervisor reconciles a backend against it and fetches the
per-connection worker environment just in time (secrets are never cached).
"""

from typing import Literal

from pydantic import BaseModel

#: Which worker image a pool runs.
WorkerImageFlavor = Literal["slim", "full"]

#: Lifecycle of a registered worker process.
WorkerStatus = Literal["starting", "ready", "draining", "gone"]

#: Who started a worker process.
WorkerManagedBy = Literal["external", "supervisor", "cloud"]

#: Lifecycle of a supervisor-owned replica.
ReplicaState = Literal["starting", "running", "draining", "stopped", "failed"]


class FleetDesired(BaseModel):
    """One row of ``GET /internal/v1/fleet/desired`` (supervised connections only)."""

    connection_id: str
    agent_name: str = "lkap-agent"
    desired_replicas: int = 0
    #: Bumped by ``POST /v1/connections/{id}/fleet {action: "restart"}``; part of
    #: ``desired_hash``, so a bump rolls the pool (R-V2-4).
    restart_generation: int = 0
    desired_hash: str
    image: WorkerImageFlavor = "slim"
    packs: list[str] = []
    deployment_mode: Literal["supervised"] = "supervised"


class WorkerEnv(BaseModel):
    """``GET /internal/v1/connections/{id}/worker-env`` — decrypted, supervisor only.

    ``env`` holds the complete child environment (``LIVEKIT_*``, ``LKAP_*``,
    ``OTEL_*``). Never log it.
    """

    env: dict[str, str] = {}
    image: str = "slim"
    agent_name: str = "lkap-agent"


class ReplicaHandle(BaseModel):
    """A replica a supervisor backend is responsible for."""

    connection_id: str
    replica_index: int
    instance_key: str
    desired_hash: str
    state: ReplicaState = "starting"
    started_at: float = 0.0
    pid_or_container: str = ""


class WorkerRegisterIn(BaseModel):
    """``POST /internal/v1/workers/register`` — sent once per worker process."""

    connection_id: str | None = None
    instance_key: str
    image: WorkerImageFlavor = "slim"
    sdk_version: str = ""
    installed_provider_ids: list[str] = []
    pack_ids: list[str] = []
    managed_by: WorkerManagedBy = "external"


class WorkerRegisterOut(BaseModel):
    """What the api tells a freshly registered worker about itself."""

    connection_id: str
    agent_name: str = "lkap-agent"


class WorkerHeartbeatIn(BaseModel):
    """``POST /internal/v1/workers/{instance_key}/heartbeat``."""

    status: WorkerStatus = "ready"
    active_jobs: int = 0
