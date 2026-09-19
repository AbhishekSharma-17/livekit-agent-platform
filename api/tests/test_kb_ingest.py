"""Unit tests for chunking and text extraction (`lkap_api.kb.ingest`)."""

from __future__ import annotations

import io

import pytest

from lkap_api.kb.ingest import chunk_text, extract_text


def test_chunk_text_splits_long_text_with_overlap() -> None:
    text = "abcdefghij" * 300  # 3000 chars
    chunks = chunk_text(text, size=800, overlap=120)
    assert len(chunks) >= 3
    assert all(len(chunk) <= 800 for chunk in chunks)
    # Consecutive chunks share the configured overlap window.
    assert chunks[0][-120:] == chunks[1][:120]


def test_chunk_text_short_text_is_a_single_chunk() -> None:
    assert chunk_text("hello world") == ["hello world"]


def test_chunk_text_blank_input_returns_no_chunks() -> None:
    assert chunk_text("   \n\t  ") == []


def test_chunk_text_rejects_non_positive_size() -> None:
    with pytest.raises(ValueError, match="size must be positive"):
        chunk_text("hello", size=0)


def test_extract_text_markdown_and_plain_text_pass_through() -> None:
    data = b"# Title\n\nSome body text."
    assert extract_text(filename="doc.md", mime="text/markdown", data=data) == data.decode()
    assert extract_text(filename="doc.txt", mime="text/plain", data=data) == data.decode()


def test_extract_text_decodes_unknown_mime_as_utf8() -> None:
    data = b"plain content"
    assert extract_text(filename="notes", mime="application/octet-stream", data=data) == "plain content"


def test_extract_text_pdf_extracts_without_raising() -> None:
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    text = extract_text(filename="doc.pdf", mime="application/pdf", data=buffer.getvalue())
    assert isinstance(text, str)
