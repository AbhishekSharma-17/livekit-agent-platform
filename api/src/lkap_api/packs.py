"""Pack manifest discovery and the ``GET /v1/packs`` endpoint.

The api imports ``<pack>.manifest`` only — never ``<pack>.pack`` — so it never
pulls ``livekit`` into the control plane (CONTRACTS §8). A pack that is missing
or fails to import is logged and skipped: the console must still work while a
pack is being developed in another work package.
"""

from __future__ import annotations

import importlib
from functools import lru_cache

from fastapi import APIRouter
from lkap_contracts.api_models import PackOut, PacksResponse
from lkap_contracts.packs import PackManifest

from lkap_api.deps import AdminDep, SettingsDep
from lkap_api.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/v1", tags=["packs"])


@lru_cache(maxsize=8)
def _load(module_paths: tuple[str, ...]) -> tuple[PackManifest, ...]:
    manifests: list[PackManifest] = []
    for path in module_paths:
        module_name = f"{path}.manifest"
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            log.warning("pack_manifest_import_failed", module=module_name, error=str(exc))
            continue
        manifest = getattr(module, "MANIFEST", None)
        if not isinstance(manifest, PackManifest):
            log.warning("pack_manifest_missing", module=module_name)
            continue
        manifests.append(manifest)
    return tuple(manifests)


def discover_manifests(packs: list[str]) -> list[PackManifest]:
    """Import every configured pack module and return its manifest.

    Args:
        packs: Dotted module paths from ``LKAP_PACKS``, e.g. ``["packs.generic"]``.

    Returns:
        The manifests that imported cleanly, in configuration order.
    """
    return list(_load(tuple(packs)))


def get_manifest(packs: list[str], pack_id: str) -> PackManifest | None:
    """Return the manifest whose ``id`` is ``pack_id``, or ``None``."""
    for manifest in discover_manifests(packs):
        if manifest.id == pack_id:
            return manifest
    return None


def clear_manifest_cache() -> None:
    """Drop the discovery cache (used by tests and after a pack reload)."""
    _load.cache_clear()


@router.get(
    "/packs",
    response_model=PacksResponse,
    summary="List installed packs",
    description="Every pack manifest discovered from `LKAP_PACKS`, for the console pack picker.",
)
async def list_packs(settings: SettingsDep, _admin: AdminDep) -> PacksResponse:
    """Return all discovered pack manifests."""
    return PacksResponse(items=[PackOut(manifest=m) for m in discover_manifests(settings.packs_list)])
