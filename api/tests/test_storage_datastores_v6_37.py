"""V6-37: presigned links for browsers (`LKAP_STORAGE_PUBLIC_ENDPOINT_URL`) and the storage copy tool.

No network: the endpoint tests short-circuit every request with a botocore ``before-send``
handler that records the URL, and the copy tool runs against moto's in-process S3.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import pytest
from botocore.awsrequest import AWSResponse
from moto import mock_aws

from lkap_api.settings import Settings
from lkap_api.storage.resolve import default_storage
from lkap_api.storage.s3 import S3Storage, make_s3_client
from lkap_api.storage.settings import StorageSettings
from lkap_api.tools import copy_storage as cs

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

INTERNAL = "http://127.0.0.1:8333"
PUBLIC = "https://files.example-tailnet.ts.net:8448"
BUCKET = "lkap"


class _RawBody:
    """Just enough of a urllib3 response for botocore to read a body from."""

    def __init__(self, data: bytes) -> None:
        self._buffer = io.BytesIO(data)

    def read(self, amt: int | None = None) -> bytes:
        return self._buffer.read(amt)

    def stream(self, **_kwargs: Any) -> Iterator[bytes]:
        yield self._buffer.read()


def _record_requests(client: Any, body: bytes = b"stored") -> list[str]:
    """Answer every request of ``client`` locally with 200 and record its URL."""
    seen: list[str] = []

    def handler(request: Any, **_kwargs: Any) -> AWSResponse:
        seen.append(request.url)
        # Only a GET carries a body: botocore reads a non-XML body on a PUT as an error.
        data = body if request.method == "GET" else b""
        headers = {"Content-Length": str(len(body)), "ETag": '"etag"', "Content-Type": "image/png"}
        return AWSResponse(request.url, 200, headers, _RawBody(data))

    client.meta.events.register("before-send.s3", handler)
    return seen


def _s3(public: str | None) -> S3Storage:
    return S3Storage(
        bucket=BUCKET,
        access_key="test",
        secret_key="test",
        region="us-east-1",
        endpoint_url=INTERNAL,
        public_endpoint_url=public,
    )


# --------------------------------------------------------------------------- presigned links
async def test_signed_url_with_public_endpoint_is_signed_for_the_public_host() -> None:
    storage = _s3(PUBLIC)

    url = urlparse(await storage.signed_url("sessions/s1/a.png", expires_in_s=60))

    assert f"{url.scheme}://{url.netloc}" == PUBLIC
    assert url.path == f"/{BUCKET}/sessions/s1/a.png"  # path-style, the bucket is not in the host
    assert "X-Amz-Signature=" in url.query


async def test_put_get_exists_with_public_endpoint_still_use_the_internal_endpoint() -> None:
    storage = _s3(PUBLIC)
    seen = _record_requests(storage._client)  # noqa: SLF001 - the client every non-presign call uses

    await storage.put("sessions/s1/a.png", b"stored", content_type="image/png")
    assert await storage.get("sessions/s1/a.png") == b"stored"
    assert await storage.exists("sessions/s1/a.png")
    await storage.signed_url("sessions/s1/a.png")

    assert len(seen) == 3
    assert all(url.startswith(f"{INTERNAL}/{BUCKET}/sessions/s1/a.png") for url in seen)


async def test_signed_url_without_public_endpoint_is_unchanged() -> None:
    storage = _s3(None)

    url = urlparse(await storage.signed_url("kb/k1/d1_a.md", expires_in_s=60))

    assert f"{url.scheme}://{url.netloc}" == INTERNAL
    assert url.path == f"/{BUCKET}/kb/k1/d1_a.md"


def test_make_s3_client_uses_path_style_only_for_a_custom_endpoint() -> None:
    custom = make_s3_client(access_key="a", secret_key="b", region="us-east-1", endpoint_url=INTERNAL)
    aws = make_s3_client(access_key="a", secret_key="b", region="us-east-1")

    assert custom.meta.config.s3 == {"addressing_style": "path"}
    assert not (aws.meta.config.s3 or {}).get("addressing_style")
    assert custom.meta.config.signature_version == aws.meta.config.signature_version == "s3v4"


async def test_default_storage_passes_the_public_endpoint_from_settings(settings: Settings) -> None:
    storage_settings = StorageSettings(
        storage_kind="s3",
        storage_bucket=BUCKET,
        storage_endpoint_url=INTERNAL,
        storage_public_endpoint_url=PUBLIC,
        storage_access_key="test",
        storage_secret_key="test",
        storage_region="us-east-1",
    )

    url = urlparse(await default_storage(settings, storage_settings).signed_url("sessions/s1/a.png"))

    assert f"{url.scheme}://{url.netloc}" == PUBLIC


# --------------------------------------------------------------------------- copy-storage
@pytest.fixture
def s3_client() -> Iterator[S3Client]:
    with mock_aws():
        client = make_s3_client(access_key="test", secret_key="test", region="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    root = tmp_path / "data" / "storage"
    files = {
        "kb/kb1/doc1_handbook.md": b"# Handbook\n",
        "sessions/s1/abc.png": b"\x89PNG-bytes",
        "datasets/ws1/ds1/source.csv": b"id,name\n1,a\n",
        "f00dfeed/kb/kb2/doc2_x.md": b"from a workspace local config",
    }
    for key, data in files.items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def _keys(client: S3Client) -> set[str]:
    return {item["Key"] for item in client.list_objects_v2(Bucket=BUCKET).get("Contents", [])}


async def test_copy_storage_uploads_every_file_under_its_storage_key(
    s3_client: S3Client, storage_root: Path
) -> None:
    report = cs.copy_storage(s3_client, BUCKET, storage_root)

    assert (report.found, report.uploaded, report.skipped, report.failed) == (4, 4, 0, 0)
    assert _keys(s3_client) == {
        "kb/kb1/doc1_handbook.md",
        "sessions/s1/abc.png",
        "datasets/ws1/ds1/source.csv",
        "f00dfeed/kb/kb2/doc2_x.md",
    }
    assert s3_client.head_object(Bucket=BUCKET, Key="sessions/s1/abc.png")["ContentType"] == "image/png"
    # The api's own S3 backend finds the files under the keys the database holds.
    backend = S3Storage(bucket=BUCKET, access_key="test", secret_key="test", region="us-east-1")
    assert await backend.get("kb/kb1/doc1_handbook.md") == b"# Handbook\n"
    assert report.unknown_prefixes == ["f00dfeed"]


def test_copy_storage_second_run_skips_same_size_and_replaces_changed(
    s3_client: S3Client, storage_root: Path
) -> None:
    cs.copy_storage(s3_client, BUCKET, storage_root)
    (storage_root / "sessions/s1/abc.png").write_bytes(b"a longer replacement image")

    again = cs.copy_storage(s3_client, BUCKET, storage_root)

    assert (again.uploaded, again.replaced, again.skipped, again.failed) == (0, 1, 3, 0)
    body = s3_client.get_object(Bucket=BUCKET, Key="sessions/s1/abc.png")["Body"].read()
    assert body == b"a longer replacement image"


def test_copy_storage_dry_run_uploads_nothing(s3_client: S3Client, storage_root: Path) -> None:
    report = cs.copy_storage(s3_client, BUCKET, storage_root, dry_run=True)

    assert report.uploaded == 4 and report.bytes_uploaded == 0
    assert _keys(s3_client) == set()


def test_format_report_shows_counts_prefixes_and_warning_but_no_contents(
    s3_client: S3Client, storage_root: Path
) -> None:
    text = cs.format_report(cs.copy_storage(s3_client, BUCKET, storage_root))

    assert "files:     4" in text and "prefix:    kb/ 1" in text
    assert "warning:   f00dfeed/ is not a folder the api reads from the bucket" in text
    assert "Handbook" not in text and "PNG-bytes" not in text
    assert text.rstrip().endswith("result: ok")


@pytest.fixture
def storage_env(monkeypatch: pytest.MonkeyPatch, storage_root: Path) -> Iterator[None]:
    monkeypatch.setenv("LKAP_DATA_DIR", str(storage_root.parent))
    monkeypatch.setenv("LKAP_STORAGE_KIND", "s3")
    monkeypatch.setenv("LKAP_STORAGE_BUCKET", "lkap-new")
    monkeypatch.setenv("LKAP_STORAGE_ACCESS_KEY", "test")
    monkeypatch.setenv("LKAP_STORAGE_SECRET_KEY", "test")
    monkeypatch.setenv("LKAP_STORAGE_REGION", "us-east-1")
    monkeypatch.delenv("LKAP_STORAGE_ENDPOINT_URL", raising=False)
    with mock_aws():
        yield


def test_main_missing_bucket_is_refused_without_create_bucket(
    storage_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cs.main([]) == cs.EXIT_REFUSED
    assert "--create-bucket" in capsys.readouterr().err


def test_main_create_bucket_uploads_from_the_data_dir(
    storage_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cs.main(["--dry-run", "--create-bucket"]) == cs.EXIT_OK
    assert "would upload:  4 new" in capsys.readouterr().out

    assert cs.main(["--create-bucket"]) == cs.EXIT_OK
    client = make_s3_client(access_key="test", secret_key="test", region="us-east-1")
    listed = client.list_objects_v2(Bucket="lkap-new")["KeyCount"]
    assert listed == 4
    assert "uploaded:  4 new" in capsys.readouterr().out


def test_main_refuses_when_storage_kind_is_not_s3(
    storage_env: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LKAP_STORAGE_KIND", "local")
    assert cs.main([]) == cs.EXIT_REFUSED
    assert "LKAP_STORAGE_KIND=s3" in capsys.readouterr().err
