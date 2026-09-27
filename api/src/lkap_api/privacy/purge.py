"""Deleting a session removes its stored files and recording (V5-30, S5-36).

Before V5-30 a soft-deleted session kept its uploaded files and its recording
until the recording retention elapsed (``retention_days=None``: forever). A
delete now removes both right away, bytes first: the session row stays (a
direct fetch by id still works, the history row is soft-deleted) but nothing
the caller sent or said as audio is kept. A storage failure is logged and that
object is left for the retention sweep to retry; the delete itself succeeds.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.costs import config_for_session
from lkap_api.db.models import LiveKitConnection, SessionAsset
from lkap_api.db.models import Session as SessionRow
from lkap_api.logging import get_logger
from lkap_api.session_assets.service import purge_session_assets
from lkap_api.settings import Settings
from lkap_api.storage.resolve import default_storage, storage_from_config
from lkap_api.vault import Vault

__all__ = ["PurgeResult", "purge_session_files"]

log = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class PurgeResult:
    """How many files, and whether the recording, were removed."""

    files_removed: int
    recording_removed: bool


async def _purge_recording(db: AsyncSession, settings: Settings, vault: Vault, row: SessionRow) -> bool:
    if not row.recording_object_key or row.connection_id is None:
        return False
    # Deferred for the import cycle `sessions_sweep.sweep_recording_retention` documents.
    from lkap_api.recordings.storage import resolve_storage_row  # noqa: PLC0415

    connection = await db.scalar(
        select(LiveKitConnection).where(
            LiveKitConnection.id == row.connection_id, LiveKitConnection.workspace_id == row.workspace_id
        )
    )
    if connection is None:
        return False
    config = await config_for_session(db, row)
    storage_row = await resolve_storage_row(
        db,
        workspace_id=row.workspace_id,
        storage_config_id=config.recording.storage_config_id if config is not None else None,
        connection=connection,
    )
    if storage_row is None:
        return False
    try:
        await storage_from_config(storage_row, vault, settings=settings).delete(row.recording_object_key)
    except Exception:  # noqa: BLE001 - keep the key; the retention sweep tries again
        log.warning("session_recording_purge_failed", session_id=row.id, exc_info=True)
        return False
    row.recording_status = "none"
    row.recording_object_key = None
    row.recording_duration_s = None
    return True


async def purge_session_files(
    db: AsyncSession, settings: Settings, vault: Vault, row: SessionRow
) -> PurgeResult:
    """Delete the stored files and the recording of a session being deleted.

    Args:
        db: The request's session (the caller commits).
        settings: For the default storage backend (session files).
        vault: Decrypts the recording's storage config.
        row: The session, already scoped to the caller's workspace.

    Returns:
        What was removed; a failure is logged and leaves that object in place.
    """
    assets = (
        (
            await db.execute(
                select(SessionAsset).where(
                    SessionAsset.session_id == row.id, SessionAsset.workspace_id == row.workspace_id
                )
            )
        )
        .scalars()
        .all()
    )
    files = await purge_session_assets(db, default_storage(settings), assets) if assets else 0
    recording = await _purge_recording(db, settings, vault, row)
    if files or recording:
        log.info("session_files_purged", session_id=row.id, files=files, recording=recording)
    return PurgeResult(files_removed=files, recording_removed=recording)
