"""Resolve which `storage_configs` row an Egress recording writes to, and its S3 params.

Resolution order (`RecordingConfig.storage_config_id` -> connection -> workspace
default), per CONTRACTS-V2 §4.3's `storage_config_id: str | None = None  # None
-> connection/workspace default`.

Egress uploads directly from LiveKit's own Egress service to an S3-compatible
endpoint — it never goes through this api process — so the write path needs
raw `(bucket, region, endpoint_url, access_key, secret_key, prefix)`, not a
`StorageBackend`'s `put`/`get`/`signed_url` methods (`storage/base.py`'s
`Protocol`, built for objects *this* process reads and writes, like KB
uploads). `storage.resolve.storage_from_config` is the row-to-backend
resolver ask #41 says to reuse for recordings; its `local` branch is exactly
right for detecting "this workspace has no S3 storage configured" (a
`LocalStorage` never round-trips to Egress, which cannot write to this
process's filesystem), but its `s3` branch builds an opaque `S3Storage`
instance with no accessors for the fields Egress needs off it. Rather than
reach into `S3Storage`'s private attributes, this module decrypts the same
`access_key_ct`/`secret_key_ct` columns directly (ask #15's one-key vault-bag
scheme, identical to `storage.resolve.storage_from_config`'s own decryption)
and returns them as plain fields.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.models import LiveKitConnection, StorageConfig
from lkap_api.errors import UnprocessableEntityError
from lkap_api.vault import Vault


class NoEgressStorageError(UnprocessableEntityError):
    """422: no S3-compatible `storage_configs` row is reachable for this recording."""


@dataclass(slots=True, frozen=True)
class EgressUploadTarget:
    """Everything `RoomCompositeEgressRequest.file.s3` needs, decrypted."""

    bucket: str
    access_key: str
    secret_key: str
    region: str | None
    endpoint_url: str | None
    prefix: str
    storage_config_id: str


async def _candidate_rows(
    db: AsyncSession, *, workspace_id: str, storage_config_id: str | None, connection: LiveKitConnection
) -> list[StorageConfig]:
    ids = [i for i in (storage_config_id, connection.storage_config_id) if i]
    rows: list[StorageConfig] = []
    for row_id in ids:
        row = await db.scalar(
            select(StorageConfig).where(
                StorageConfig.id == row_id, StorageConfig.workspace_id == workspace_id
            )
        )
        if row is not None:
            rows.append(row)
    default_row = await db.scalar(
        select(StorageConfig).where(
            StorageConfig.workspace_id == workspace_id, StorageConfig.is_default.is_(True)
        )
    )
    if default_row is not None:
        rows.append(default_row)
    return rows


async def resolve_storage_row(
    db: AsyncSession, *, workspace_id: str, storage_config_id: str | None, connection: LiveKitConnection
) -> StorageConfig | None:
    """Return the first `s3`-kind candidate row, or `None` if only `local`/nothing resolves.

    Used both to build the Egress write target (:func:`resolve_egress_target`,
    which also decrypts it) and, at playback time, to pick the same row again
    for a signed GET URL (`routers/sessions.py`) — the session does not pin
    which row it recorded to (no such column exists), so playback re-runs
    this identical, deterministic order; it only disagrees with the original
    choice if the connection's or the agent's storage config was reassigned
    in between, a narrow edge case noted in the V2-12 report rather than
    solved with a new column.
    """
    for row in await _candidate_rows(
        db, workspace_id=workspace_id, storage_config_id=storage_config_id, connection=connection
    ):
        if row.kind == "s3":
            return row
    return None


async def resolve_egress_target(
    db: AsyncSession,
    vault: Vault,
    *,
    workspace_id: str,
    storage_config_id: str | None,
    connection: LiveKitConnection,
) -> EgressUploadTarget:
    """Resolve the S3-compatible target a recording's Egress uploads to.

    Args:
        storage_config_id: `AgentConfig.recording.storage_config_id`, if the
            agent overrides the connection's default.
        connection: The session's `LiveKitConnection` (its own `storage_config_id`
            is the second fallback).

    Raises:
        NoEgressStorageError: No candidate resolves to an `s3`-kind row —
            either none is configured, or the only one found is `local`
            (Egress cannot upload into this process's filesystem; ARCHITECTURE-V2
            D-V2-16 names S3-compatible storage, MinIO in dev, for exactly
            this reason).
    """
    row = await resolve_storage_row(
        db, workspace_id=workspace_id, storage_config_id=storage_config_id, connection=connection
    )
    if row is not None:
        access_key = vault.decrypt(row.access_key_ct)["access_key"] if row.access_key_ct else ""
        secret_key = vault.decrypt(row.secret_key_ct)["secret_key"] if row.secret_key_ct else ""
        return EgressUploadTarget(
            bucket=row.bucket,
            access_key=access_key,
            secret_key=secret_key,
            region=row.region,
            endpoint_url=row.endpoint_url,
            prefix=row.prefix,
            storage_config_id=row.id,
        )
    raise NoEgressStorageError(
        "recording needs an S3-compatible storage config (MinIO or S3) on the connection or the "
        "agent's recording.storage_config_id; a 'local' storage config cannot receive an Egress "
        "upload, which is written by LiveKit's own Egress service, not this api process",
        details={"connection_id": connection.id, "workspace_id": workspace_id},
    )
