"""``python -m lkap_api.tools.copy_storage``: upload the local storage folder to S3 (V6-37).

Usage::

    python -m lkap_api.tools.copy_storage [--source-dir <dir>] [--dry-run] [--create-bucket]

The local backend keeps every object at ``<LKAP_DATA_DIR>/storage/<key>``, and the S3
backend keeps it at ``<key>`` in ``LKAP_STORAGE_BUCKET`` (the platform default has no key
prefix). So each file is uploaded under its path relative to the source folder, and every
key the database holds stays valid:

* ``kb/<kb id>/<document id>_<filename>``: knowledge-base source files (``kb_documents``,
  the key is derived from the row, never stored).
* ``sessions/<session id>/<asset id><ext>``: session files (``session_assets.storage_key``).
* ``datasets/<workspace id>/<dataset id>/source<ext>``: lookup-table uploads
  (``datasets.storage_key``).

Recordings (``sessions.recording_object_key``) are written by LiveKit Egress straight to an
S3 store, never to the local folder, so there is nothing to copy for them.

The destination comes from the same ``LKAP_STORAGE_*`` settings the api reads
(``LKAP_STORAGE_KIND=s3``, ``LKAP_STORAGE_BUCKET``, ``LKAP_STORAGE_ENDPOINT_URL``,
``LKAP_STORAGE_ACCESS_KEY``, ``LKAP_STORAGE_SECRET_KEY``, ``LKAP_STORAGE_REGION``), so the
keys never appear on the command line. The source defaults to ``<LKAP_DATA_DIR>/storage``.

It is idempotent: an object that already exists with the same size is skipped, one with a
different size is uploaded again. It prints counts only, never file contents. A top-level
folder that is not one of the known ones (for example a workspace storage config of kind
``local``, which keeps its files under ``storage/<config id>/``) is named in a warning,
because its files land under keys the platform default never reads. Exit codes: ``0`` done,
``1`` some uploads failed, ``2`` refused (no S3 settings, no source folder, no bucket).
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from botocore.exceptions import BotoCoreError, ClientError

from lkap_api.storage.s3 import make_s3_client
from lkap_api.storage.settings import StorageSettings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

#: Top-level key folders the api writes (``recordings`` only through Egress).
KNOWN_PREFIXES: Final = frozenset({"kb", "sessions", "datasets", "recordings"})
DEFAULT_DATA_DIR: Final = "./data"
EXIT_OK: Final = 0
EXIT_FAILED: Final = 1
EXIT_REFUSED: Final = 2


class CopyStorageRefusedError(Exception):
    """A precondition failed before anything was uploaded."""


@dataclass(slots=True)
class StorageCopyReport:
    """What :func:`copy_storage` did (counts only)."""

    source_dir: str
    bucket: str
    dry_run: bool
    found: int = 0
    uploaded: int = 0
    replaced: int = 0
    skipped: int = 0
    failed: int = 0
    bytes_uploaded: int = 0
    prefixes: Counter[str] = field(default_factory=Counter)
    failures: list[str] = field(default_factory=list)

    @property
    def unknown_prefixes(self) -> list[str]:
        """Top-level folders the api does not write to."""
        return sorted(prefix for prefix in self.prefixes if prefix not in KNOWN_PREFIXES)

    @property
    def ok(self) -> bool:
        """Whether every file is now in the bucket (or would be, on a dry run)."""
        return self.failed == 0


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def _remote_size(client: S3Client, bucket: str, key: str) -> int | None:
    """The object's size, or ``None`` when it does not exist."""
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if _error_code(exc) in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    return int(head["ContentLength"])


def bucket_exists(client: S3Client, bucket: str) -> bool:
    """Whether ``bucket`` exists (any error other than "not found" is raised)."""
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        if _error_code(exc) in ("404", "NoSuchBucket", "NotFound"):
            return False
        raise
    return True


def create_bucket(client: S3Client, bucket: str, region: str | None) -> None:
    """Create ``bucket`` (with a location constraint outside ``us-east-1``, as AWS wants)."""
    if region and region != "us-east-1":
        # The stubs list AWS's own region names only, and an S3-compatible store may use others.
        configuration: Any = {"LocationConstraint": region}
        client.create_bucket(Bucket=bucket, CreateBucketConfiguration=configuration)
    else:
        client.create_bucket(Bucket=bucket)


def iter_files(source_dir: Path) -> list[tuple[str, Path]]:
    """Every regular file under ``source_dir`` with its storage key, sorted by key."""
    files = [
        (path.relative_to(source_dir).as_posix(), path)
        for path in source_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    ]
    return sorted(files)


def copy_storage(
    client: S3Client, bucket: str, source_dir: Path, *, dry_run: bool = False, bucket_is_new: bool = False
) -> StorageCopyReport:
    """Upload every file under ``source_dir`` to ``bucket`` under its relative path.

    Args:
        client: An S3 client (:func:`lkap_api.storage.s3.make_s3_client`).
        bucket: The destination bucket (it must exist unless this is a dry run).
        source_dir: The local storage root.
        dry_run: Count what would be uploaded without uploading.
        bucket_is_new: The bucket does not exist yet (a dry run before ``--create-bucket``),
            so every file counts as new without asking the store.

    Returns:
        The counts.
    """
    report = StorageCopyReport(source_dir=str(source_dir), bucket=bucket, dry_run=dry_run)
    for key, path in iter_files(source_dir):
        report.found += 1
        report.prefixes[key.split("/", 1)[0] if "/" in key else "(top level)"] += 1
        size = path.stat().st_size
        try:
            remote = None if bucket_is_new else _remote_size(client, bucket, key)
            if remote == size:
                report.skipped += 1
                continue
            if not dry_run:
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                client.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": content_type})
                report.bytes_uploaded += size
            if remote is None:
                report.uploaded += 1
            else:
                report.replaced += 1
        except (ClientError, BotoCoreError, OSError) as exc:
            report.failed += 1
            report.failures.append(f"{key}: {type(exc).__name__}")
    return report


def format_report(report: StorageCopyReport) -> str:
    """The report as plain text."""
    verb = "would upload" if report.dry_run else "uploaded"
    lines = [
        f"source:    {report.source_dir}",
        f"bucket:    {report.bucket}",
        f"files:     {report.found}",
        f"{verb}:  {report.uploaded} new, {report.replaced} with a different size",
        f"skipped:   {report.skipped} already there with the same size",
        f"failed:    {report.failed}",
    ]
    if not report.dry_run:
        lines.append(f"bytes:     {report.bytes_uploaded}")
    lines += [f"prefix:    {prefix}/ {count}" for prefix, count in sorted(report.prefixes.items())]
    lines += [
        f"warning:   {prefix}/ is not a folder the api reads from the bucket"
        for prefix in report.unknown_prefixes
    ]
    lines += [f"failure:   {failure}" for failure in report.failures]
    lines.append("result: ok" if report.ok else "result: some uploads failed")
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lkap_api.tools.copy_storage",
        description="Upload the local storage folder to the LKAP_STORAGE_* S3 bucket under the same keys.",
    )
    parser.add_argument(
        "--source-dir", type=Path, default=None, help="local storage root (default: <LKAP_DATA_DIR>/storage)"
    )
    parser.add_argument("--dry-run", action="store_true", help="count what would be uploaded")
    parser.add_argument("--create-bucket", action="store_true", help="create the bucket when it is missing")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the storage copy CLI and return a process exit code (0 done, 1 failures, 2 refused)."""
    try:
        args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_REFUSED
    settings = StorageSettings()
    source_dir: Path = args.source_dir or Path(os.environ.get("LKAP_DATA_DIR", DEFAULT_DATA_DIR)) / "storage"
    try:
        if settings.storage_kind != "s3" or not settings.storage_bucket:
            raise CopyStorageRefusedError("set LKAP_STORAGE_KIND=s3 and LKAP_STORAGE_BUCKET first")
        if not source_dir.is_dir():
            raise CopyStorageRefusedError(f"the source folder {source_dir} does not exist")
        client = make_s3_client(
            access_key=settings.storage_access_key or "",
            secret_key=settings.storage_secret_key or "",
            region=settings.storage_region,
            endpoint_url=settings.storage_endpoint_url,
        )
        exists = bucket_exists(client, settings.storage_bucket)
        if not exists and not args.create_bucket:
            raise CopyStorageRefusedError(
                f"bucket {settings.storage_bucket} does not exist. Pass --create-bucket to create it"
            )
        if not exists and not args.dry_run:
            create_bucket(client, settings.storage_bucket, settings.storage_region)
        report = copy_storage(
            client,
            settings.storage_bucket,
            source_dir,
            dry_run=args.dry_run,
            bucket_is_new=not exists and args.dry_run,
        )
    except CopyStorageRefusedError as exc:
        sys.stderr.write(f"refused: {exc}\n")
        return EXIT_REFUSED
    except (ClientError, BotoCoreError) as exc:
        sys.stderr.write(f"failed: {type(exc).__name__}\n")
        return EXIT_FAILED
    sys.stdout.write(format_report(report))
    return EXIT_OK if report.ok else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
