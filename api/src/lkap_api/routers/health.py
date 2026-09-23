"""Public health endpoint.

``db`` is ``ok`` only when the database answers **and** its ``alembic_version``
equals the head of ``api/alembic/versions`` — a schema that was never migrated,
or stopped half-way, reports ``error`` (asks #11/#17). ``agents_unbound``
counts agents with no LiveKit connection (CONTRACTS-V2 §1.3); the api refuses
to connect or resolve those, and bootstrap binds them to the default connection.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter
from lkap_contracts.api_models import HealthResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import __version__
from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import Agent
from lkap_api.deps import DbDep, SettingsDep
from lkap_api.logging import get_logger
from lkap_api.packs import discover_manifests

log = get_logger(__name__)

router = APIRouter(prefix="/v1", tags=["health"])

#: Where ``alembic.ini`` may live: next to the source tree (dev, editable
#: install) or the working directory (the container runs from ``/app/api``).
_ALEMBIC_ROOTS = (Path(__file__).resolve().parents[3], Path.cwd(), Path.cwd() / "api")


@lru_cache(maxsize=1)
def migration_head() -> str | None:
    """Return the Alembic head revision shipped with this build, or ``None`` if not found."""
    for root in _ALEMBIC_ROOTS:
        ini = root / "alembic.ini"
        if not ini.is_file():
            continue
        config = Config(str(ini))
        config.set_main_option("script_location", str(root / "alembic"))
        try:
            return ScriptDirectory.from_config(config).get_current_head()
        except Exception as exc:  # noqa: BLE001 - health must never raise
            log.warning("health_migration_head_unreadable", path=str(root), error_type=type(exc).__name__)
    log.warning("health_migration_head_not_found", searched=[str(r) for r in _ALEMBIC_ROOTS])
    return None


async def _schema_revision(db: AsyncSession) -> str | None:
    """Return the stored ``alembic_version``, or ``None`` when there is none."""
    rows = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
    return str(rows[0]) if len(rows) == 1 else None


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health",
    description=(
        "Liveness plus the configured LiveKit url, discovered packs, database state and the "
        "number of agents without a LiveKit connection. `db` is `ok` only when the schema is "
        "migrated to the head revision this build ships."
    ),
)
async def health(settings: SettingsDep, db: DbDep) -> HealthResponse:
    """Report service health; never requires a token."""
    db_ok = False
    unbound = 0
    try:
        head = migration_head()
        current = await _schema_revision(db)
        unbound = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(Agent)
                    .where(Agent.connection_id.is_(None))
                    .execution_options(**{CROSS_WORKSPACE_OPTION: True})
                )
            ).scalar_one()
        )
        db_ok = head is not None and current == head
        if not db_ok:
            log.warning("health_schema_not_at_head", revision=current, head=head)
    except Exception as exc:  # noqa: BLE001 - health must never raise
        log.warning("health_db_check_failed", error=str(exc))
        await db.rollback()  # Postgres aborts the transaction on error; leave the session usable
    return HealthResponse(
        ok=db_ok,
        version=__version__,
        livekit_url=settings.livekit_url,
        packs=[m.id for m in discover_manifests(settings.packs_list)],
        db="ok" if db_ok else "error",
        agents_unbound=unbound,
    )
