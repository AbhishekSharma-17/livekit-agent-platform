"""Connection capability flags and the ``test`` probe (ARCHITECTURE-V2 D-V2-4).

Capabilities come from two layers:

* **Static** — derived from ``deployment_type`` and the operator's
  ``use_inference`` toggle, always recomputed: ``inference_available``,
  ``cloud_hosting``, ``observability_dashboard``, ``noise_cancellation_tier``
  and ``turn_detector_mode`` (research §2.3/§2.4).
* **Probed** — ``sip_enabled``, ``egress_enabled`` and ``ingress_enabled`` from
  the secondary list calls of the last successful test. Until a connection has
  been probed, a Cloud connection is assumed to have all three (they ship with
  every Cloud project) and a self-hosted one none of them (each is a separate
  service there).

The probe is read-only: ``RoomService.list_rooms`` (primary, decides ``ok``),
then ``SipService.list_inbound_trunk``, ``EgressService.list_egress`` and
``IngressService.list_ingress`` (secondary, only set flags). Every call has a
5 s budget and Cloud region failover is disabled, so an unreachable url fails
within that budget.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Mapping
from typing import Any

import aiohttp
from livekit.api import (
    ListEgressRequest,
    ListIngressRequest,
    ListRoomsRequest,
    ListSIPInboundTrunkRequest,
    ServerError,
)
from lkap_contracts.api_models import ConnectionTestResult
from lkap_contracts.connections import ConnectionCapabilities

from lkap_api import net_guard
from lkap_api.connections.clients import ConnectionClientFactory, ConnectionRowLike
from lkap_api.logging import get_logger

log = get_logger(__name__)

#: Budget for each probe call (primary and each secondary).
PROBE_TIMEOUT_S = 5.0

#: Twirp code to report when the server sent none (verified live against LiveKit Cloud).
_STATUS_CODES: dict[int, str] = {401: "unauthenticated", 403: "permission_denied", 404: "not_found"}

#: The capability keys only a successful probe can set.
PROBED_KEYS: tuple[str, ...] = ("sip_enabled", "egress_enabled", "ingress_enabled")


def static_capabilities(deployment_type: str, use_inference: bool) -> ConnectionCapabilities:
    """Capabilities implied by the deployment type alone, before any probe.

    Args:
        deployment_type: ``"cloud"`` or ``"self_hosted"``.
        use_inference: The connection's "use LiveKit Inference" toggle.

    Returns:
        Capabilities with the probed flags at their unprobed assumption.
    """
    cloud = deployment_type == "cloud"
    inference = cloud and use_inference
    return ConnectionCapabilities(
        inference_available=inference,
        sip_enabled=cloud,
        egress_enabled=cloud,
        ingress_enabled=cloud,
        cloud_hosting=cloud,
        noise_cancellation_tier="krisp" if cloud else "none",
        observability_dashboard=cloud,
        turn_detector_mode="hosted" if inference else "local",
    )


def effective_capabilities(
    deployment_type: str, use_inference: bool, stored: Mapping[str, Any] | None
) -> ConnectionCapabilities:
    """Merge the static layer with the probed flags stored on the row.

    Args:
        deployment_type: The row's ``deployment_type``.
        use_inference: The row's ``use_inference``.
        stored: The row's ``capabilities`` JSON (empty when never probed).

    Returns:
        What validation and the worker should assume about this connection.
    """
    caps = static_capabilities(deployment_type, use_inference)
    if stored:
        updates = {key: bool(stored[key]) for key in PROBED_KEYS if key in stored}
        caps = caps.model_copy(update=updates)
    return caps


def _describe(exc: BaseException) -> str:
    """A short, secret-free description of a probe failure."""
    blocked = net_guard.blocked_cause(exc)
    if blocked is not None:
        return str(blocked)
    if isinstance(exc, ServerError):
        # LiveKit Cloud answers a bad key/secret with a bare 401 and no Twirp JSON
        # body, which the SDK reports as code "unknown"; name it from the status.
        code = exc.code if exc.code and exc.code != "unknown" else _STATUS_CODES.get(exc.status, "unknown")
        detail = f": {exc.message}" if exc.message else ""
        return f"{code}{detail} (HTTP {exc.status})"
    if isinstance(exc, TimeoutError):
        return "unreachable: timed out waiting for a response"
    if isinstance(exc, aiohttp.InvalidURL):
        return "invalid url"
    if isinstance(exc, aiohttp.ClientError | OSError):
        return f"unreachable: {type(exc).__name__}"
    return f"probe failed: {type(exc).__name__}"


async def _secondary(call: Awaitable[Any], timeout_s: float) -> bool:
    """Run one capability probe; any failure only means the feature is absent."""
    try:
        await asyncio.wait_for(call, timeout_s)
    except Exception as exc:  # noqa: BLE001 - a failed secondary probe is a `False` flag, not an error
        log.debug("connection_probe_secondary_failed", error=_describe(exc))
        return False
    return True


async def probe_connection(
    factory: ConnectionClientFactory,
    row: ConnectionRowLike,
    *,
    deployment_type: str,
    use_inference: bool,
    timeout_s: float = PROBE_TIMEOUT_S,
) -> ConnectionTestResult:
    """Test a connection's credentials and probe its capabilities.

    Args:
        factory: Client factory used to decrypt and build the LiveKit client.
        row: The connection (saved or not).
        deployment_type: ``"cloud"`` or ``"self_hosted"``.
        use_inference: The "use LiveKit Inference" toggle.
        timeout_s: Budget for each call.

    Returns:
        ``ok`` is the primary ``list_rooms`` outcome; ``message`` carries the
        Twirp error code (e.g. ``unauthenticated``) or the transport failure;
        ``capabilities`` holds the static flags plus the probed ones.
    """
    caps = static_capabilities(deployment_type, use_inference)
    started = time.perf_counter()
    try:
        async with factory.api(row, timeout_s=timeout_s, failover=False) as client:
            try:
                await asyncio.wait_for(client.room.list_rooms(ListRoomsRequest()), timeout_s)
            except Exception as exc:  # noqa: BLE001 - every failure becomes a readable test result
                return ConnectionTestResult(
                    ok=False,
                    message=_describe(exc),
                    capabilities=caps.model_copy(
                        update={"sip_enabled": False, "egress_enabled": False, "ingress_enabled": False}
                    ),
                    latency_ms=round((time.perf_counter() - started) * 1000, 1),
                )
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            sip, egress, ingress = await asyncio.gather(
                _secondary(client.sip.list_inbound_trunk(ListSIPInboundTrunkRequest()), timeout_s),
                _secondary(client.egress.list_egress(ListEgressRequest()), timeout_s),
                _secondary(client.ingress.list_ingress(ListIngressRequest()), timeout_s),
            )
    except net_guard.BlockedDestinationError as exc:
        # A private, loopback or metadata address (V2-21 / S1): nothing was sent.
        return ConnectionTestResult(
            ok=False,
            message=str(exc),
            capabilities=caps.model_copy(
                update={"sip_enabled": False, "egress_enabled": False, "ingress_enabled": False}
            ),
        )
    except ValueError as exc:
        # LiveKitAPI raises ValueError for an empty url or missing key/secret.
        return ConnectionTestResult(ok=False, message=f"invalid connection: {exc}", capabilities=caps)

    caps = caps.model_copy(update={"sip_enabled": sip, "egress_enabled": egress, "ingress_enabled": ingress})
    absent = [name for name, on in (("SIP", sip), ("Egress", egress), ("Ingress", ingress)) if not on]
    message = "connected"
    if absent:
        message += f"; not reachable: {', '.join(absent)}"
    return ConnectionTestResult(ok=True, message=message, capabilities=caps, latency_ms=latency_ms)
