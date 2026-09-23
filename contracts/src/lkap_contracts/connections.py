"""LiveKit connections: what a deployment target can do (CONTRACTS-V2 §1.2, §4.3).

A *connection* is one LiveKit deployment (a Cloud project or a self-hosted
server) that agents are bound to. Its capabilities are probed by the api and
travel to the worker inside :class:`~lkap_contracts.agent_config.ResolvedAgentConfig`
so the worker can adapt (for example: use a local turn detector when the hosted
one is unavailable).
"""

from typing import Literal

from pydantic import BaseModel

#: Which noise-cancellation plugins the connection can serve.
NoiseCancellationTier = Literal["none", "ai_coustics", "krisp"]

#: Where end-of-turn detection runs for this connection.
TurnDetectorMode = Literal["hosted", "local"]

#: How a connection was deployed.
DeploymentType = Literal["cloud", "self_hosted"]

#: Who runs the worker pool of a connection.
DeploymentMode = Literal["external", "supervised", "cloud_hosted"]


class ConnectionCapabilities(BaseModel):
    """What a probed LiveKit deployment supports (CONTRACTS-V2 §4.3).

    Defaults describe the most restrictive target (a bare self-hosted server), so
    an unprobed connection never advertises a feature it may not have.
    """

    inference_available: bool = False
    sip_enabled: bool = False
    egress_enabled: bool = False
    ingress_enabled: bool = False
    cloud_hosting: bool = False
    noise_cancellation_tier: NoiseCancellationTier = "none"
    observability_dashboard: bool = False
    turn_detector_mode: TurnDetectorMode = "local"


class ConnectionInfo(BaseModel):
    """The connection facts the worker needs, attached to the resolved config."""

    connection_id: str = ""
    deployment_type: DeploymentType = "cloud"
    capabilities: ConnectionCapabilities = ConnectionCapabilities()
