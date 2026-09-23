"""Resolve a `StorageBackend` from settings or a `storage_configs` row."""

from __future__ import annotations

from pathlib import Path

from lkap_api.db.models import StorageConfig
from lkap_api.settings import Settings
from lkap_api.storage.base import StorageBackend
from lkap_api.storage.local import LocalStorage
from lkap_api.storage.s3 import S3Storage
from lkap_api.storage.settings import StorageSettings, get_storage_settings
from lkap_api.vault import Vault


def default_storage(settings: Settings, storage_settings: StorageSettings | None = None) -> StorageBackend:
    """Return the platform default backend (`LKAP_STORAGE_*`, `local` in dev).

    Args:
        settings: The shared api `Settings` (for `data_dir`/`master_key`/`public_base_url`).
        storage_settings: This package's own settings; defaults to the cached instance.
    """
    resolved = storage_settings or get_storage_settings()
    if resolved.storage_kind == "s3":
        return S3Storage(
            bucket=resolved.storage_bucket,
            access_key=resolved.storage_access_key or "",
            secret_key=resolved.storage_secret_key or "",
            region=resolved.storage_region,
            endpoint_url=resolved.storage_endpoint_url,
        )
    return LocalStorage(
        Path(settings.data_dir) / "storage",
        signing_key=settings.master_key,
        public_base_url=resolved.storage_public_base_url or settings.public_base_url,
    )


def storage_from_config(row: StorageConfig, vault: Vault, *, settings: Settings) -> StorageBackend:
    """Return the `StorageBackend` a workspace `storage_configs` row describes.

    Args:
        row: The `StorageConfig` row (`kind`, `bucket`, encrypted keys, ...).
        vault: Decrypts `access_key_ct`/`secret_key_ct` (a one-key `Vault` secret
            bag per ask #15, `{"access_key": ...}` / `{"secret_key": ...}`).
        settings: Used only for the `local` kind's `data_dir`/`master_key`.
    """
    if row.kind == "local":
        return LocalStorage(
            Path(settings.data_dir) / "storage" / row.id,
            signing_key=settings.master_key,
            public_base_url=row.public_base_url or settings.public_base_url,
        )
    access_key = vault.decrypt(row.access_key_ct)["access_key"] if row.access_key_ct else ""
    secret_key = vault.decrypt(row.secret_key_ct)["secret_key"] if row.secret_key_ct else ""
    return S3Storage(
        bucket=row.bucket,
        access_key=access_key,
        secret_key=secret_key,
        region=row.region,
        endpoint_url=row.endpoint_url,
        prefix=row.prefix,
    )
