"""Extraction, structure-aware chunking and ingest (`lkap_api.kb.ingest`, V5-01).

Every chunk test measures tokens with the dependency-free counter the
`FakeEmbedder` path uses (`approx_token_count`); the fastembed tokenizer
counts a little higher, so the pinned counts below are for that counter.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from sqlalchemy import select

from lkap_api.db.models import KbChunk, KbDocument, KnowledgeBase
from lkap_api.db.session import Database
from lkap_api.kb.embed import FakeEmbedder, approx_token_count
from lkap_api.kb.ingest import (
    HEADING_SEPARATOR,
    PROGRESS_EVERY,
    Chunk,
    ChunkingConfig,
    ExtractedDocument,
    chunk_document,
    document_kind,
    extract_document,
    extract_text,
    ingest_into_session,
)
from lkap_api.kb.store import LanceDBStore

FIXTURES = Path(__file__).parent / "fixtures" / "kb"
REPO = Path(__file__).resolve().parents[2]
SMALL = ChunkingConfig(max_tokens=64, overlap=8)


def _chunks(name: str, config: ChunkingConfig | None = None, *, mime: str = "") -> tuple[str, list[Chunk]]:
    document = extract_document(filename=name, mime=mime, data=(FIXTURES / name).read_bytes())
    chunks = chunk_document(document, config=config or ChunkingConfig(), count_tokens=approx_token_count)
    return document.text, chunks


def _starts_a_sentence(source: str, start: int) -> bool:
    before = source[:start].rstrip(" \t")
    return not before or before.endswith("\n") or before[-1] in ".!?"


def _ends_a_sentence(source: str, end: int) -> bool:
    after = source[end:]
    return not after or after.startswith("\n") or source[end - 1] in ".!?"


# --------------------------------------------------------------------------- chunking config
def test_chunking_config_defaults_are_256_tokens_with_32_overlap() -> None:
    assert ChunkingConfig.from_json(None).to_json() == {"max_tokens": 256, "overlap": 32}


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ({"max_tokens": 128, "overlap": 16}, {"max_tokens": 128, "overlap": 16}),
        ({"max_tokens": 4, "overlap": 99}, {"max_tokens": 32, "overlap": 16}),
        ({"max_tokens": 10_000}, {"max_tokens": 512, "overlap": 32}),
        ({"max_tokens": "big", "overlap": True}, {"max_tokens": 256, "overlap": 32}),
    ],
)
def test_chunking_config_from_json_clamps_bad_values(
    stored: dict[str, object], expected: dict[str, int]
) -> None:
    assert ChunkingConfig.from_json(stored).to_json() == expected


# --------------------------------------------------------------------------- markdown chunking
@pytest.mark.parametrize("config", [ChunkingConfig(), SMALL])
def test_chunk_markdown_never_starts_or_ends_mid_sentence(config: ChunkingConfig) -> None:
    source, chunks = _chunks("policy_guide.md", config)
    assert len(chunks) > 1
    for chunk in chunks:
        assert _starts_a_sentence(source, chunk.char_start), chunk.text[:60]
        assert _ends_a_sentence(source, chunk.char_end), chunk.text[-60:]


@pytest.mark.parametrize("config", [ChunkingConfig(), SMALL])
def test_chunk_markdown_offsets_slice_the_source_back_to_the_chunk(config: ChunkingConfig) -> None:
    source, chunks = _chunks("policy_guide.md", config)
    for chunk in chunks:
        assert source[chunk.char_start : chunk.char_end] == chunk.text
        assert chunk.text == chunk.text.strip()
    assert [chunk.char_start for chunk in chunks] == sorted(chunk.char_start for chunk in chunks)


def test_chunk_markdown_carries_the_heading_path() -> None:
    _, chunks = _chunks("policy_guide.md")
    paths = [chunk.heading_path for chunk in chunks]
    root = "Harbor Lane Mutual policy guide"
    assert all(path and path[0] == root for path in paths)
    assert (root, "Water damage", "Burst pipes in winter") in paths
    assert (root, "Water damage", "Sewer backup") in paths
    # A level-2 heading pops the previous level-3 one.
    assert (root, "Theft") in paths
    theft = next(chunk for chunk in chunks if chunk.heading_path == (root, "Theft"))
    assert "Bicycles are covered" in theft.text
    assert all(chunk.page is None for chunk in chunks)


def test_chunk_markdown_with_a_small_budget_overlaps_whole_sentences() -> None:
    source, chunks = _chunks("policy_guide.md", SMALL)
    water = [chunk for chunk in chunks if chunk.heading_path[-1] == "Water damage"]
    assert len(water) == 2
    first, second = water
    assert second.char_start < first.char_end  # the overlap
    overlap = source[second.char_start : first.char_end]
    assert overlap == "Gradual damage is not covered."
    assert approx_token_count(overlap) <= SMALL.overlap
    assert all(approx_token_count(chunk.text) <= SMALL.max_tokens for chunk in water)


def test_chunk_a_heading_longer_than_the_budget_is_still_one_chunk() -> None:
    source, chunks = _chunks("policy_guide.md", SMALL)
    heading_line = next(line for line in source.splitlines() if line.startswith("## A section with one"))
    assert approx_token_count(heading_line) > SMALL.max_tokens
    heading_chunks = [chunk for chunk in chunks if heading_line in chunk.text]
    assert len(heading_chunks) == 1
    assert heading_chunks[0].text == heading_line
    last = chunks[-1]
    assert last.text == "Call the claims desk for anything not listed above."
    assert last.heading_path[-1].startswith("A section with one very long heading")


def test_chunk_a_sentence_over_the_budget_is_cut_at_word_boundaries() -> None:
    sentence = " ".join(f"word{i}" for i in range(150)) + "."
    document = ExtractedDocument(text=f"# Title\n\nShort intro.\n\n{sentence}\n\nAfter it.")
    chunks = chunk_document(document, config=SMALL, count_tokens=approx_token_count)
    pieces = [chunk for chunk in chunks if "word" in chunk.text]
    assert len(pieces) == 3
    assert all(approx_token_count(piece.text) <= SMALL.max_tokens for piece in pieces)
    assert " ".join(piece.text for piece in pieces) == sentence
    assert chunks[-1].text == "After it."


def test_chunk_headings_inside_code_fences_are_not_sections() -> None:
    text = "# Real\n\nIntro text.\n\n```\n# not a heading\n```\n\n## Next\n\nMore text."
    chunks = chunk_document(
        ExtractedDocument(text=text), config=ChunkingConfig(), count_tokens=approx_token_count
    )
    assert [chunk.heading_path for chunk in chunks] == [("Real",), ("Real", "Next")]


def test_chunk_blank_input_returns_no_chunks() -> None:
    chunks = chunk_document(
        ExtractedDocument(text="   \n\t  "), config=ChunkingConfig(), count_tokens=approx_token_count
    )
    assert chunks == []


def test_chunk_embed_text_prefixes_the_heading_path() -> None:
    chunk = Chunk(text="Body.", heading_path=("Guide", "Water"), char_start=0, char_end=5)
    assert chunk.embed_text == f"Guide{HEADING_SEPARATOR}Water\n\nBody."
    assert Chunk(text="Body.", heading_path=(), char_start=0, char_end=5).embed_text == "Body."
    assert chunk.meta("guide.md") == {
        "filename": "guide.md",
        "heading_path": ["Guide", "Water"],
        "page": None,
        "char_start": 0,
        "char_end": 5,
    }


# --------------------------------------------------------------------------- PDF, DOCX, HTML
def test_chunk_pdf_fixture_carries_pages_one_to_three() -> None:
    source, chunks = _chunks("policy_summary.pdf")
    assert sorted({chunk.page for chunk in chunks}) == [1, 2, 3]
    by_page = {chunk.page: chunk.text for chunk in chunks}
    assert "HL-2024" in by_page[1]
    assert "Flood damage" in by_page[2]
    assert "claims@example.com" in by_page[3]
    for chunk in chunks:
        assert source[chunk.char_start : chunk.char_end] == chunk.text


def test_extract_pdf_records_one_page_span_per_page() -> None:
    document = extract_document(
        filename="policy_summary.pdf",
        mime="application/pdf",
        data=(FIXTURES / "policy_summary.pdf").read_bytes(),
    )
    assert [page.number for page in document.pages] == [1, 2, 3]
    assert document.page_at(document.pages[2].start) == 3


def test_chunk_docx_fixture_yields_its_headings_as_heading_path() -> None:
    _, chunks = _chunks("claims_handbook.docx")
    assert [chunk.heading_path for chunk in chunks] == [
        ("Claims handbook",),
        ("Claims handbook", "Reporting a loss"),
        ("Claims handbook", "Reporting a loss", "Water damage"),
        ("Claims handbook", "Payments"),
    ]
    assert "HO-3" in chunks[2].text


def test_chunk_html_fixture_contains_no_markup() -> None:
    source, chunks = _chunks("claims_faq.html", mime="text/html")
    assert chunks
    assert "<" not in source
    assert all("<" not in chunk.text for chunk in chunks)
    assert "window.analytics" not in source  # scripts are dropped
    assert any(chunk.heading_path == ("Claims FAQ", "How long does a payment take?") for chunk in chunks)


def test_extract_html_by_media_type_when_the_name_has_no_extension() -> None:
    data = b"<html><body><h1>Hours</h1><p>Open <b>daily</b>.</p></body></html>"
    assert (
        extract_text(filename="page", mime="text/html; charset=utf-8", data=data)
        == "# Hours\n\nOpen **daily**."
    )


@pytest.mark.parametrize(
    ("filename", "mime", "kind"),
    [
        ("a.pdf", "application/octet-stream", "pdf"),
        ("a.bin", "application/pdf", "pdf"),
        ("a.docx", "application/octet-stream", "markitdown"),
        ("a.PPTX", "", "markitdown"),
        ("a.xlsx", "", "markitdown"),
        ("a.htm", "text/plain", "markitdown"),
        ("a", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "markitdown"),
        ("a.md", "text/markdown", "text"),
        ("a.csv", "text/csv", "text"),
        ("a.json", "application/json", "text"),
    ],
)
def test_document_kind_prefers_the_extension_then_the_media_type(filename: str, mime: str, kind: str) -> None:
    assert document_kind(filename, mime)[0] == kind


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


# --------------------------------------------------------------------------- the shipped seeds
#: (seed file, chunks with the 800-character windows before V5-01, chunks now).
SEED_COUNTS = [
    ("packs/src/packs/insurance_claim/seeds/policy_lines.md", 4, 6),
    ("packs/src/packs/insurance_claim/seeds/intake_playbook.md", 3, 5),
    ("api/src/lkap_api/templates/catalog/knowledge_assistant/seeds/product_faq.md", 5, 6),
    ("api/src/lkap_api/templates/catalog/knowledge_assistant/seeds/support_playbook.md", 2, 6),
    ("api/src/lkap_api/templates/catalog/lead_qualification/seeds/offer_sheet.md", 2, 5),
    ("api/src/lkap_api/templates/catalog/phone_agent/seeds/phone_faq.md", 2, 4),
    ("api/src/lkap_api/templates/catalog/receptionist/seeds/practice_info.md", 2, 7),
]


@pytest.mark.parametrize(("path", "before", "now"), SEED_COUNTS)
def test_seed_files_rechunk_into_more_smaller_chunks(path: str, before: int, now: int) -> None:
    text = (REPO / path).read_text()
    chunks = chunk_document(
        ExtractedDocument(text=text), config=ChunkingConfig(), count_tokens=approx_token_count
    )
    assert len(chunks) == now
    assert now > before
    assert sum(len(chunk.text) for chunk in chunks) / now < len(text.strip()) / before
    assert all(chunk.heading_path for chunk in chunks)


def test_every_shipped_seed_file_is_pinned() -> None:
    seeds = sorted(
        str(path.relative_to(REPO))
        for pattern in ("packs/src/packs/*/seeds/*", "api/src/lkap_api/templates/catalog/*/seeds/*")
        for path in REPO.glob(pattern)
        if not re.search(r"evals?\.json$", path.name)
    )
    assert seeds == sorted(path for path, _, _ in SEED_COUNTS)


# --------------------------------------------------------------------------- ingest
async def _kb_with_document(database: Database, *, filename: str, **kb_columns: object) -> tuple[str, str]:
    async with database.session() as session:
        kb = KnowledgeBase(name="Ingest test", **kb_columns)
        session.add(kb)
        await session.flush()
        document = KbDocument(kb_id=kb.id, filename=filename, mime="text/markdown", bytes=1, status="pending")
        session.add(document)
        await session.flush()
        return kb.id, document.id


async def test_ingest_writes_locators_and_records_the_embedder(database: Database, tmp_path: Path) -> None:
    store = LanceDBStore(tmp_path)
    kb_id, document_id = await _kb_with_document(database, filename="policy_guide.md")
    async with database.session() as session:
        outcome = await ingest_into_session(
            session,
            store=store,
            embedder=FakeEmbedder(),
            kb_id=kb_id,
            document_id=document_id,
            filename="policy_guide.md",
            mime="text/markdown",
            data=(FIXTURES / "policy_guide.md").read_bytes(),
        )
    assert outcome.status == "ready"
    async with database.session() as session:
        chunks = (
            (await session.execute(select(KbChunk).where(KbChunk.document_id == document_id))).scalars().all()
        )
        kb = await session.get(KnowledgeBase, kb_id)
        document = await session.get(KbDocument, document_id)
    assert len(chunks) == outcome.chunk_count == 7
    for chunk in chunks:
        assert set(chunk.meta) == {"filename", "heading_path", "page", "char_start", "char_end"}
        assert chunk.meta["heading_path"][0] == "Harbor Lane Mutual policy guide"
    assert kb is not None and kb.dimension == 32 and kb.embedder_model == "fake-hashed-tokens-32"
    assert document is not None and document.progress == 1.0 and document.status == "ready"


async def test_ingest_reports_progress_every_50_chunks(database: Database, tmp_path: Path) -> None:
    body = "\n\n".join(f"## Section {i}\n\nSection {i} says one thing." for i in range(120))
    kb_id, document_id = await _kb_with_document(database, filename="many.md")
    calls: list[tuple[int, int]] = []

    async def record(done: int, total: int) -> None:
        calls.append((done, total))

    async with database.session() as session:
        outcome = await ingest_into_session(
            session,
            store=LanceDBStore(tmp_path),
            embedder=FakeEmbedder(),
            kb_id=kb_id,
            document_id=document_id,
            filename="many.md",
            mime="text/markdown",
            data=body.encode(),
            on_progress=record,
        )
    assert outcome.chunk_count == 120
    assert calls == [(PROGRESS_EVERY, 120), (2 * PROGRESS_EVERY, 120)]


async def test_ingest_refuses_a_kb_built_by_another_embedder(database: Database, tmp_path: Path) -> None:
    store = LanceDBStore(tmp_path)
    kb_id, document_id = await _kb_with_document(
        database, filename="doc.md", dimension=1536, embedder_model="text-embedding-3-small"
    )
    async with database.session() as session:
        outcome = await ingest_into_session(
            session,
            store=store,
            embedder=FakeEmbedder(),
            kb_id=kb_id,
            document_id=document_id,
            filename="doc.md",
            mime="text/markdown",
            data=b"# Doc\n\nSome text.",
        )
    assert outcome.status == "failed"
    assert outcome.error is not None and "Ingest test" in outcome.error and "1536" in outcome.error
    assert await store.query(kb_id, [0.0] * 32, 4) == []


async def test_reingest_replaces_the_previous_chunks_and_vectors(database: Database, tmp_path: Path) -> None:
    store = LanceDBStore(tmp_path)
    kb_id, document_id = await _kb_with_document(database, filename="doc.md")

    async def ingest(data: bytes) -> None:
        async with database.session() as session:
            outcome = await ingest_into_session(
                session,
                store=store,
                embedder=FakeEmbedder(),
                kb_id=kb_id,
                document_id=document_id,
                filename="doc.md",
                mime="text/markdown",
                data=data,
            )
        assert outcome.status == "ready"

    await ingest(b"# Old\n\nOld text about bicycles.\n\n## More\n\nAnd more.")
    await ingest(b"# New\n\nNew text about boats.")
    async with database.session() as session:
        chunks = (
            (await session.execute(select(KbChunk).where(KbChunk.document_id == document_id))).scalars().all()
        )
        kb = await session.get(KnowledgeBase, kb_id)
    assert [chunk.text for chunk in chunks] == ["# New\n\nNew text about boats."]
    assert kb is not None and kb.chunk_count == 1
    [vector] = await FakeEmbedder().embed(["boats"])
    hits = await store.query(kb_id, vector, 10)
    assert [hit.id for hit in hits] == [chunks[0].id]


async def test_ingest_failure_after_a_previous_ingest_keeps_the_old_chunks(
    database: Database, tmp_path: Path
) -> None:
    store = LanceDBStore(tmp_path)
    kb_id, document_id = await _kb_with_document(database, filename="doc.md")
    async with database.session() as session:
        await ingest_into_session(
            session,
            store=store,
            embedder=FakeEmbedder(),
            kb_id=kb_id,
            document_id=document_id,
            filename="doc.md",
            mime="text/markdown",
            data=b"# Doc\n\nFirst version.",
        )

    class BrokenEmbedder(FakeEmbedder):
        async def embed(self, texts: object) -> list[list[float]]:
            raise RuntimeError("embedder down")

    async with database.session() as session:
        outcome = await ingest_into_session(
            session,
            store=store,
            embedder=BrokenEmbedder(),
            kb_id=kb_id,
            document_id=document_id,
            filename="doc.md",
            mime="text/markdown",
            data=b"# Doc\n\nSecond version.",
        )
    assert outcome.status == "failed"
    assert outcome.chunk_count == 1
    async with database.session() as session:
        texts = (
            await session.execute(select(KbChunk.text).where(KbChunk.document_id == document_id))
        ).scalars()
        assert list(texts) == ["# Doc\n\nFirst version."]
