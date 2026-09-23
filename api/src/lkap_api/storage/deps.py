"""FastAPI dependency for the platform default `StorageBackend`."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from lkap_api.deps import SettingsDep
from lkap_api.storage.base import StorageBackend
from lkap_api.storage.resolve import default_storage


def get_storage(settings: SettingsDep) -> StorageBackend:
    """Return the platform default storage backend for this request."""
    return default_storage(settings)


StorageDep = Annotated[StorageBackend, Depends(get_storage)]
