"""Provider registry endpoints — the catalogue the console renders forms from."""

from __future__ import annotations

from fastapi import APIRouter
from lkap_contracts.api_models import ProvidersResponse
from lkap_contracts.providers import REGISTRY, ProviderSpec, get

from lkap_api.deps import AdminDep
from lkap_api.errors import NotFoundError

router = APIRouter(prefix="/v1/providers", tags=["providers"])


@router.get(
    "",
    response_model=ProvidersResponse,
    summary="List providers",
    description=(
        "The full provider registry, including `status='deferred'` entries so the console "
        "can show them as coming soon."
    ),
)
async def list_providers(_admin: AdminDep) -> ProvidersResponse:
    """Return every registry entry."""
    return ProvidersResponse(providers=list(REGISTRY))


@router.get(
    "/{provider_id}",
    response_model=ProviderSpec,
    summary="Get one provider",
    description="The spec for a single registry id, including its secret and config fields.",
)
async def get_provider(provider_id: str, _admin: AdminDep) -> ProviderSpec:
    """Return one provider spec.

    Raises:
        NotFoundError: If the id is not registered.
    """
    try:
        return get(provider_id)
    except KeyError as exc:
        raise NotFoundError(f"unknown provider '{provider_id}'") from exc
