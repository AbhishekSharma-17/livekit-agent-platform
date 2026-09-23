"""Tests for `lkap_api.storage`: local filesystem backend + S3-compatible backend (moto)."""

from __future__ import annotations

import time
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from lkap_api.storage.local import LocalStorage, sign_local_key, verify_local_signature
from lkap_api.storage.s3 import S3Storage

MASTER_KEY = "TWk5rQ2mE4b3W6z8n1F0pQhV9xY7cJdKzL5aRtUvWo8="


async def test_local_storage_put_get_delete_roundtrip(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "storage", signing_key=MASTER_KEY)
    await storage.put("kb/x/doc.md", b"hello world", content_type="text/markdown")

    assert await storage.get("kb/x/doc.md") == b"hello world"

    await storage.delete("kb/x/doc.md")
    with pytest.raises(FileNotFoundError):
        await storage.get("kb/x/doc.md")


async def test_local_storage_get_missing_key_raises_file_not_found(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "storage", signing_key=MASTER_KEY)
    with pytest.raises(FileNotFoundError):
        await storage.get("does/not/exist.txt")


async def test_local_storage_signed_url_verifies(tmp_path: Path) -> None:
    storage = LocalStorage(
        tmp_path / "storage", signing_key=MASTER_KEY, public_base_url="https://api.example.com"
    )
    url = await storage.signed_url("kb/x/doc.md", expires_in_s=60)
    assert url.startswith("https://api.example.com/internal/v1/storage/local/")

    exp = int(time.time()) + 60
    sig = sign_local_key(MASTER_KEY.encode(), "kb/x/doc.md", exp)
    assert verify_local_signature(MASTER_KEY, "kb/x/doc.md", exp, sig)


def test_local_signature_rejects_wrong_secret_and_expired_url() -> None:
    exp = int(time.time()) + 60
    sig = sign_local_key(MASTER_KEY.encode(), "k", exp)
    assert not verify_local_signature("a-different-master-key", "k", exp, sig)
    assert not verify_local_signature(MASTER_KEY, "k", int(time.time()) - 1, sig)


def test_local_storage_rejects_a_key_escaping_root(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "storage", signing_key=MASTER_KEY)
    with pytest.raises(ValueError, match="escapes root"):
        storage._path("../../etc/passwd")  # noqa: SLF001 - exercising the guard directly


@pytest.fixture
def s3_bucket() -> str:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="lkap-test-bucket")
        yield "lkap-test-bucket"


async def test_s3_storage_put_get_delete_roundtrip(s3_bucket: str) -> None:
    storage = S3Storage(bucket=s3_bucket, access_key="test", secret_key="test", region="us-east-1")
    await storage.put("kb/x/doc.md", b"hello s3", content_type="text/markdown")

    assert await storage.get("kb/x/doc.md") == b"hello s3"

    await storage.delete("kb/x/doc.md")
    with pytest.raises(FileNotFoundError):
        await storage.get("kb/x/doc.md")


async def test_s3_storage_signed_url_is_a_real_presigned_url(s3_bucket: str) -> None:
    storage = S3Storage(bucket=s3_bucket, access_key="test", secret_key="test", region="us-east-1")
    await storage.put("kb/x/doc.md", b"hello s3")
    url = await storage.signed_url("kb/x/doc.md", expires_in_s=60)
    assert "kb/x/doc.md" in url
    assert "X-Amz-Signature" in url


async def test_s3_storage_prefix_scopes_every_key(s3_bucket: str) -> None:
    storage = S3Storage(
        bucket=s3_bucket, access_key="test", secret_key="test", region="us-east-1", prefix="ws-1"
    )
    await storage.put("a.txt", b"scoped")
    assert await storage.get("a.txt") == b"scoped"

    raw_client = boto3.client("s3", region_name="us-east-1")
    body = raw_client.get_object(Bucket=s3_bucket, Key="ws-1/a.txt")["Body"].read()
    assert body == b"scoped"
