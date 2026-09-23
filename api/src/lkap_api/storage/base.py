"""`StorageBackend` protocol shared by the `local` and `s3` implementations."""

from __future__ import annotations

from typing import Protocol

from lkap_api.errors import ApiError


class UploadTooLargeError(ApiError):
    """413 — an upload exceeded its configured byte cap (REVIEW-FINAL F-29)."""

    status_code = 413
    code = "payload_too_large"


class StorageBackend(Protocol):
    """Where an object lives and how it is read back (bytes or a signed URL)."""

    async def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> None:
        """Write `data` under `key`, overwriting any existing object."""
        ...

    async def get(self, key: str) -> bytes:
        """Read the object at `key` back into memory.

        Raises:
            FileNotFoundError: If no object exists at `key`.
        """
        ...

    async def delete(self, key: str) -> None:
        """Remove the object at `key`; a missing object is not an error."""
        ...

    async def signed_url(self, key: str, *, expires_in_s: int = 3600) -> str:
        """Return a time-limited URL a browser can fetch `key` from directly."""
        ...
