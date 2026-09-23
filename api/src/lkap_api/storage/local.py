"""Local filesystem storage backend (dev only, CONTRACTS-V2 §1.2 `storage_configs.kind="local"`).

Objects live under `<root>/<key>`, `key` being a `/`-separated relative path
(`kb/{kb_id}/{document_id}_{filename}`, later `recordings/{session_id}.ogg`).
`signed_url` mints an HMAC over `key:exp` rather than a real presigned URL —
**no route in this codebase serves it yet** (there is no admin-facing "GET a
KB source file" endpoint, and recording playback is V2-12's route to add);
`verify_local_signature` is exported so that future route can check it
without duplicating the scheme. This is flagged in the V2-08 report as
something V2-12 needs to know.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import quote, urlencode

import anyio.to_thread

from lkap_api.storage.base import StorageBackend


class LocalStorage(StorageBackend):
    """Writes/reads objects as plain files under `root`."""

    def __init__(self, root: Path, *, signing_key: str, public_base_url: str | None = None) -> None:
        """Build the backend.

        Args:
            root: Directory objects are written under (created lazily).
            signing_key: HMAC key for `signed_url`/`verify_local_signature`
                (the process `LKAP_MASTER_KEY` — reused, not a new secret).
            public_base_url: Base URL a browser would use to fetch a signed
                URL; `LKAP_PUBLIC_BASE_URL` when unset upstream.
        """
        self._root = root
        self._signing_key = signing_key.encode("utf-8")
        self._public_base_url = (public_base_url or "").rstrip("/")

    def _path(self, key: str) -> Path:
        # `key` is always server-generated (never taken verbatim from a
        # request path parameter), but resolve-and-check anyway so a future
        # caller can't escape `root` with a `..` segment.
        candidate = (self._root / key).resolve()
        root_resolved = self._root.resolve()
        if candidate != root_resolved and root_resolved not in candidate.parents:
            raise ValueError(f"storage key escapes root: {key!r}")
        return candidate

    async def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> None:
        """Write `data` to `<root>/<key>`, creating parent directories as needed."""
        path = self._path(key)
        await anyio.to_thread.run_sync(self._write, path, data)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def get(self, key: str) -> bytes:
        """Read `<root>/<key>` back into memory.

        Raises:
            FileNotFoundError: If the object does not exist.
        """
        path = self._path(key)
        return await anyio.to_thread.run_sync(path.read_bytes)

    async def delete(self, key: str) -> None:
        """Remove `<root>/<key>` if it exists."""
        path = self._path(key)
        await anyio.to_thread.run_sync(_unlink_if_exists, path)

    async def signed_url(self, key: str, *, expires_in_s: int = 3600) -> str:
        """Return `{public_base_url}/internal/v1/storage/local/{key}?exp=&sig=`.

        No route currently serves this path (see module docstring); the URL
        is well-formed and its signature verifiable, but 404s until one is
        added.
        """
        exp = int(time.time()) + expires_in_s
        sig = sign_local_key(self._signing_key, key, exp)
        query = urlencode({"exp": exp, "sig": sig})
        return f"{self._public_base_url}/internal/v1/storage/local/{quote(key)}?{query}"


def _unlink_if_exists(path: Path) -> None:
    path.unlink(missing_ok=True)


def sign_local_key(signing_key: bytes, key: str, exp: int) -> str:
    """Return the hex HMAC-SHA256 of `f"{key}:{exp}"` under `signing_key`."""
    return hmac.new(signing_key, f"{key}:{exp}".encode(), hashlib.sha256).hexdigest()


def verify_local_signature(signing_key: str, key: str, exp: int, sig: str) -> bool:
    """Return whether `sig` is a valid, unexpired signature for `key`.

    Args:
        signing_key: The same key `LocalStorage` was built with (`master_key`).
        key: The storage key the URL claims to grant access to.
        exp: The unix timestamp the URL expires at.
        sig: The `sig` query parameter from the URL.
    """
    if time.time() > exp:
        return False
    expected = sign_local_key(signing_key.encode("utf-8"), key, exp)
    return hmac.compare_digest(expected, sig)
