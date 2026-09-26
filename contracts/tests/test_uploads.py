"""V5-19: the upload block, caller files, stored session assets and the richer form fields."""

from typing import Any

import pytest
from pydantic import ValidationError

from lkap_contracts.api_models import SessionAssetOut
from lkap_contracts.blocks import (
    DEFAULT_UPLOAD_ACCEPT,
    UPLOAD_EXTENSIONS,
    UPLOAD_MIME_TYPES,
    UploadBlockConfig,
    accept_allows,
    safe_filename,
    sniff_mime,
    validate_block_config,
)
from lkap_contracts.tools import (
    ASSET_TOOL_NAMES,
    BLOCK_TOOL_NAMES,
    BLOCK_TOOL_TYPES,
    BUILTIN_TOOL_NAMES,
    NEVER_BACKGROUND_TOOLS,
    UPDATABLE_BLOCK_TYPES,
    VISION_TOOL_NAMES,
)
from lkap_contracts.ui_protocol import (
    FORM_FIELD_TYPES,
    MAX_UPLOAD_BYTES,
    AssetRef,
    BlockSpec,
    FormUploadSpec,
    UiState,
    UploadBlockState,
    UploadedFile,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 8
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 8
HEIF = b"\x00\x00\x00\x18ftypmif1" + b"\x00" * 8
PDF = b"%PDF-1.7\n" + b"\x00" * 16


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (PNG, "image/png"),
        (JPEG, "image/jpeg"),
        (GIF, "image/gif"),
        (WEBP, "image/webp"),
        (HEIC, "image/heic"),
        (HEIF, "image/heif"),
        (PDF, "application/pdf"),
    ],
)
def test_sniff_mime_recognises_the_allowed_types(data: bytes, mime: str) -> None:
    assert sniff_mime(data) == mime
    assert mime in UPLOAD_MIME_TYPES


@pytest.mark.parametrize(
    "data",
    [
        b"<html><script>alert(1)</script></html>",
        b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>',
        b"<svg onload=alert(1)>",
        b"PK\x03\x04zip",
        b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8,  # an MP4, not an image brand
        b"MZ\x90\x00",
        b"",
        b"hello %PDF-1.7",  # PDF magic not at offset 0
    ],
)
def test_sniff_mime_refuses_everything_else(data: bytes) -> None:
    assert sniff_mime(data) is None


def test_accept_allows_wildcard_images_and_exact_types() -> None:
    assert accept_allows(["image/*"], "image/png")
    assert not accept_allows(["image/*"], "application/pdf")
    assert accept_allows(["application/pdf"], "application/pdf")
    assert accept_allows([], "image/jpeg")  # empty = the default (images and PDFs)
    assert not accept_allows(["image/*"], "image/svg+xml")  # never allowed, whatever accept says
    assert not accept_allows(["image/*", "application/pdf"], "text/html")


def test_upload_config_defaults_and_limits() -> None:
    config = UploadBlockConfig()
    assert config.accept == list(DEFAULT_UPLOAD_ACCEPT)
    assert config.max_files == 3
    assert config.max_bytes == 10 * 1024 * 1024
    assert config.camera_capture is False
    assert UploadBlockConfig(accept=["IMAGE/PNG", "image/png"]).accept == ["image/png"]


@pytest.mark.parametrize(
    ("config", "key"),
    [
        ({"accept": ["text/html"]}, "accept"),
        ({"accept": ["image/svg+xml"]}, "accept"),
        ({"accept": ["*/*"]}, "accept"),
        ({"accept": []}, "accept"),
        ({"max_bytes": MAX_UPLOAD_BYTES + 1}, "max_bytes"),
        ({"max_bytes": 0}, "max_bytes"),
        ({"max_files": 11}, "max_files"),
        ({"max_files": 0}, "max_files"),
        ({"folder": "x"}, "folder"),
    ],
)
def test_upload_config_rejects_unsafe_or_out_of_range_values(config: dict[str, Any], key: str) -> None:
    issues = validate_block_config(BlockSpec(id="docs", type="upload", config=config))
    assert [i.path for i in issues] == [f"config.{key}"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("photo.jpg", "photo.jpg"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\x\\scan.pdf", "scan.pdf"),
        ("..", "file"),
        ("", "file"),
        (None, "file"),
        ("bad\x00name<>.png", "badname.png"),
        ("\uff46\uff55\uff4c\uff4c.png", "full.png"),  # NFKC folds full-width letters
    ],
)
def test_safe_filename_drops_paths_and_unsafe_characters(name: str | None, expected: str) -> None:
    assert safe_filename(name) == expected


def test_safe_filename_caps_the_length_and_keeps_the_extension() -> None:
    name = safe_filename("a" * 300 + ".pdf")
    assert len(name) == 120
    assert name.endswith(".pdf")


def test_every_allowed_type_has_a_storage_extension() -> None:
    assert set(UPLOAD_MIME_TYPES) <= set(UPLOAD_EXTENSIONS)


def test_upload_state_is_requestable_and_validates_its_files() -> None:
    state = UploadBlockState()
    assert state.status == "idle"
    assert state.files == [] and state.rejected == [] and state.progress is None
    file = UploadedFile(asset_id="a1", name="x.png", mime="image/png", size=3, sha256="0" * 64)
    assert UploadBlockState(files=[file]).files[0].asset_id == "a1"
    with pytest.raises(ValidationError):
        UploadedFile(asset_id="a1", name="x", mime="image/png", size=1, sha256="not-a-hash")
    with pytest.raises(ValidationError):
        UploadBlockState(progress=1.5)


def test_asset_ref_is_backward_compatible() -> None:
    """An envelope written before V5-19 (no `stored`, `name`, `size`) still validates."""
    ref = AssetRef(asset_id="a", kind="photo", mime="image/jpeg", ts=1.0)
    assert ref.stored is False and ref.name is None and ref.size is None
    state = UiState.model_validate(
        {"assets": [{"asset_id": "a", "kind": "photo", "mime": "image/jpeg", "ts": 1}]}
    )
    assert state.assets[0].stored is False


def test_the_upload_tools_are_registered_in_the_shared_lists() -> None:
    assert "request_upload" in BLOCK_TOOL_NAMES
    assert BLOCK_TOOL_TYPES["request_upload"] == frozenset({"upload"})
    assert "request_upload" in NEVER_BACKGROUND_TOOLS
    assert "upload" not in UPDATABLE_BLOCK_TYPES  # the request owns the block's status
    assert "describe_asset" in BUILTIN_TOOL_NAMES
    assert "describe_asset" in ASSET_TOOL_NAMES
    assert "describe_asset" not in VISION_TOOL_NAMES


def test_form_field_types_include_the_v5_19_additions() -> None:
    assert {"date", "phone", "email", "select", "textarea", "file"} <= set(FORM_FIELD_TYPES)
    spec = FormUploadSpec()
    assert spec.max_files == 1 and spec.accept == []
    with pytest.raises(ValidationError):
        FormUploadSpec(max_bytes=MAX_UPLOAD_BYTES + 1)


def test_session_asset_out_requires_a_sha256() -> None:
    row = {
        "id": "a",
        "session_id": "s",
        "kind": "upload",
        "name": "x.png",
        "mime": "image/png",
        "size": 3,
        "sha256": "f" * 64,
        "created_at": "2026-09-27T00:00:00Z",
    }
    assert SessionAssetOut.model_validate(row).url is None
    with pytest.raises(ValidationError):
        SessionAssetOut.model_validate({**row, "kind": "script"})
