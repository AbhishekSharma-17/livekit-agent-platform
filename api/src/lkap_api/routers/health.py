"""Public health endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from lkap_contracts.api_models import HealthResponse
from sqlalchemy import select

from lkap_api import __version__
from lkap_api.db.models import Agent
from lkap_api.deps import DbDep, SettingsDep
from lkap_api.logging import get_logger
from lkap_api.packs import discover_manifests

log = get_logger(__name__)

router = APIRouter(prefix="/v1", tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health",
    description="Liveness plus the configured LiveKit url, discovered packs and database state.",
)
async def health(settings: SettingsDep, db: DbDep) -> HealthResponse:
    """Report service health; never requires a token."""
    db_state: str = "ok"
    try:
        # Querying a real table (not `SELECT 1`) makes `db: "error"` also catch the
        # "migrations were never run" case an operator actually needs to see.
        await db.execute(select(Agent.id).limit(1))
    except Exception as exc:  # noqa: BLE001 - health must never raise
        log.warning("health_db_check_failed", error=str(exc))
        db_state = "error"
    return HealthResponse(
        ok=db_state == "ok",
        version=__version__,
        livekit_url=settings.livekit_url,
        packs=[m.id for m in discover_manifests(settings.packs_list)],
        db="ok" if db_state == "ok" else "error",
    )
