"""``POST /v1/providers/{provider_id}/test-model`` (docs/v4/CUSTOM-MODELS.md D-V4-26, R-V4-26).

Gated by its own ``ROUTE_POLICY`` rule (the longest route-template prefix
wins): ``builder`` + ``agents:write`` to run, so a builder can test the model
they are configuring, as test chat spends vendor money under
``sessions:write``. The route depends on
:func:`lkap_api.auth.deps.route_policy_context` directly, not the v1
``AdminCtxDep``, so the only audit row is the service's own
``provider.test_model`` (provider, model, ok, latency).

The dependencies below (``get_ws_connector``, ``get_probe_timeouts``,
``get_in_flight``) exist so tests can replace the websocket transport, shrink
the budgets and hold concurrency slots.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from lkap_contracts.api_models import ModelTestRequest, ModelTestResult
from lkap_contracts.providers import ProviderSpec, get

from lkap_api import net_guard
from lkap_api.auth.deps import WorkspaceContext, route_policy_context
from lkap_api.auth.ratelimit import RateLimiterDep
from lkap_api.connections.clients import ClientFactoryDep
from lkap_api.custom_models.probes import AiohttpWsConnector, WsConnector
from lkap_api.custom_models.service import InFlight, Timeouts, run_model_test
from lkap_api.deps import DbDep, HttpClientDep, SettingsDep, VaultDep
from lkap_api.errors import NotFoundError

router = APIRouter(prefix="/v1/providers", tags=["providers"])

_IN_FLIGHT_ATTR = "model_test_in_flight"


def get_ws_connector(settings: SettingsDep) -> WsConnector:
    """The websocket transport of the realtime probes (the network guard's aiohttp session)."""
    return AiohttpWsConnector(net_guard.policy_from_settings(settings))


def get_probe_timeouts() -> Timeouts:
    """The per-kind probe budgets (15 s llm, 20 s stt/tts, 10 s the rest)."""
    return Timeouts()


def get_in_flight(request: Request) -> InFlight:
    """The process's per-workspace concurrency counter, created on first use."""
    counter: InFlight | None = getattr(request.app.state, _IN_FLIGHT_ATTR, None)
    if counter is None:
        counter = InFlight()
        setattr(request.app.state, _IN_FLIGHT_ATTR, counter)
    return counter


PolicyCtxDep = Annotated[WorkspaceContext, Depends(route_policy_context)]
WsConnectorDep = Annotated[WsConnector, Depends(get_ws_connector)]
TimeoutsDep = Annotated[Timeouts, Depends(get_probe_timeouts)]
InFlightDep = Annotated[InFlight, Depends(get_in_flight)]


def _spec_or_404(provider_id: str) -> ProviderSpec:
    try:
        return get(provider_id)
    except KeyError as exc:
        raise NotFoundError(f"unknown provider '{provider_id}'") from exc


@router.post(
    "/{provider_id}/test-model",
    response_model=ModelTestResult,
    summary="Test a model id against its vendor",
    description=(
        "One capped, real vendor call from the api (an LLM answers one word with at most 4 tokens, "
        "TTS speaks 'Hello.', STT transcribes a bundled 1 s clip, a realtime model completes a "
        "websocket handshake, an avatar id is read; images are never generated). Spends vendor "
        "money: at most 10 tests per workspace per minute and 2 at once (429 `rate_limited` with "
        "`retry_after`). A repeat within 10 minutes with the same key answers `cached=true` unless "
        "`force`. The model id is checked first (422, never echoed); `message` and `sample` are "
        "scrubbed vendor text and untrusted. The result is recorded on the workspace's model "
        "record. A pass proves the vendor accepts the id with this key, not that the LiveKit "
        "plugin builds it."
    ),
)
async def post_test_model(
    provider_id: str,
    payload: ModelTestRequest,
    db: DbDep,
    ctx: PolicyCtxDep,
    client: HttpClientDep,
    vault: VaultDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    factory: ClientFactoryDep,
    ws: WsConnectorDep,
    timeouts: TimeoutsDep,
    in_flight: InFlightDep,
) -> ModelTestResult:
    """Run the entry's probe once for this workspace and record the result."""
    return await run_model_test(
        db,
        ctx=ctx,
        spec=_spec_or_404(provider_id),
        request=payload,
        client=client,
        ws=ws,
        vault=vault,
        factory=factory,
        limiter=limiter,
        in_flight=in_flight,
        policy=net_guard.policy_from_settings(settings),
        timeouts=timeouts,
    )
