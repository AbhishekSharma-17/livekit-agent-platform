"""S3-compatible / local object storage (CONTRACTS-V2 §1.2, §6).

Used for KB source uploads and (later, by V2-12) recordings. `resolve.py`
picks a `StorageBackend` from either a workspace `StorageConfig` row or the
platform default (`LKAP_STORAGE_*`, this package's own settings — see
`storage/settings.py` for why they are not in `lkap_api.settings`).
"""

from __future__ import annotations

from lkap_api.storage.base import StorageBackend, UploadTooLargeError
from lkap_api.storage.resolve import default_storage, storage_from_config

__all__ = ["StorageBackend", "UploadTooLargeError", "default_storage", "storage_from_config"]
