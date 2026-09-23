"""S3-compatible storage backend (MinIO in dev, real S3/R2/etc. in prod).

`boto3` has no supported async API, so every call runs in a worker thread via
`anyio.to_thread.run_sync` — acceptable here because KB uploads are capped at
25 MB (F-29) and this is not a hot path. `mypy --strict` is satisfied with
`boto3-stubs[s3]`.
"""

from __future__ import annotations

from typing import Any

import anyio.to_thread
import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from lkap_api.storage.base import StorageBackend


class S3Storage(StorageBackend):
    """Stores objects in one S3-compatible bucket under an optional key prefix."""

    def __init__(
        self,
        *,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str | None = None,
        endpoint_url: str | None = None,
        prefix: str = "",
    ) -> None:
        """Build the backend.

        Args:
            bucket: The S3 bucket name.
            access_key: The access key id.
            secret_key: The secret access key.
            region: The bucket's region; required by AWS S3, optional for MinIO.
            endpoint_url: A non-AWS endpoint (MinIO, R2, ...); `None` uses AWS S3.
            prefix: A key prefix prepended to every object key (a workspace scope).
        """
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        client_kwargs: dict[str, Any] = {
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "config": BotoConfig(signature_version="s3v4"),
        }
        if region:
            client_kwargs["region_name"] = region
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        self._client = boto3.client("s3", **client_kwargs)

    def _full_key(self, key: str) -> str:
        return f"{self._prefix}/{key}" if self._prefix else key

    async def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> None:
        """Upload `data` as the object at `key`."""
        await anyio.to_thread.run_sync(self._put_sync, key, data, content_type)

    def _put_sync(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(
            Bucket=self._bucket, Key=self._full_key(key), Body=data, ContentType=content_type
        )

    async def get(self, key: str) -> bytes:
        """Download the object at `key`.

        Raises:
            FileNotFoundError: If no object exists at `key`.
        """
        return await anyio.to_thread.run_sync(self._get_sync, key)

    def _get_sync(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._full_key(key))
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404"):
                raise FileNotFoundError(key) from exc
            raise
        body: bytes = response["Body"].read()
        return body

    async def delete(self, key: str) -> None:
        """Remove the object at `key`; a missing object is not an error."""
        await anyio.to_thread.run_sync(self._delete_sync, key)

    def _delete_sync(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=self._full_key(key))

    async def signed_url(self, key: str, *, expires_in_s: int = 3600) -> str:
        """Return a real S3 presigned GET URL for `key`."""
        return await anyio.to_thread.run_sync(self._signed_url_sync, key, expires_in_s)

    def _signed_url_sync(self, key: str, expires_in_s: int) -> str:
        url: str = self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": self._full_key(key)},
            ExpiresIn=expires_in_s,
        )
        return url
