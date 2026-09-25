"""Document ingestion: text extraction, structure-aware chunking, embedding, vector upsert.

Every public function here takes an already-open
:class:`~sqlalchemy.ext.asyncio.AsyncSession` and never commits it — the caller
(a request handler or a background task owning its own session) controls the
transaction boundary.

PLAN-V2 V2-08 moves ingestion "onto jobs": the router
(:mod:`lkap_api.routers.knowledge`) uploads the raw bytes to the platform
:mod:`~lkap_api.storage` backend first (so the job payload stays JSON-only —
required for the ``arq`` backend, whose queue is Redis, not an in-process
call) and enqueues :data:`~lkap_api.jobs.kinds.KB_INGEST` with the storage key
instead of calling straight into :func:`ingest_into_session`. The `inline`
backend still runs it via the caller's ``BackgroundTasks`` when given one, so
the existing "ingestion is done by the time ``POST .../documents`` returns
under ``ASGITransport``" test behaviour is unchanged.

V5-01 (PLAN-V5, K §2 P0-3 and P0-8) replaces the 800-character windows:

* **Extraction** (:func:`extract_document`): PDF through ``pypdf`` with the
  page boundaries kept; DOCX, PPTX, XLSX and HTML through MarkItDown into
  Markdown (so an HTML import is text, never raw tags); anything else
  (Markdown, plain text, CSV, JSON) as UTF-8 text.
* **Chunking** (:func:`chunk_document`): Markdown headings are hard section
  boundaries (and so are PDF pages); inside a section the text is split into
  paragraphs, list items / table rows and sentences, and packed up to a token
  budget (default 256, measured with the embedder's own tokenizer) with an
  overlap of whole trailing sentences (default 32 tokens). A chunk never
  starts or ends mid-sentence, unless one sentence alone exceeds the budget.
* **Locators**: every chunk's ``KbChunk.meta`` carries ``filename``,
  ``heading_path``, ``page`` and ``char_start`` / ``char_end`` (offsets into
  the extracted text, which slice it back to the chunk text exactly).
* **Contextual embedding**: the embedded text is the heading path joined by
  ``" › "`` followed by the chunk, so a chunk under "Water damage" is found by
  a question about water damage even if its own text never says so.

Re-ingesting a document (the re-index route) replaces its chunks and vectors
only after the new ones are embedded, so a failure keeps the old ones.
"""

from __future__ import annotations

import asyncio
import functools
import io
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Final, Literal

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api import net_guard
from lkap_api.auth.audit import record as record_audit
from lkap_api.db.guard import CROSS_WORKSPACE_OPTION
from lkap_api.db.models import KbChunk, KbDocument, KnowledgeBase, new_id
from lkap_api.db.session import Database
from lkap_api.errors import ApiError, UnprocessableEntityError
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.kinds import KB_INGEST
from lkap_api.jobs.registry import job
from lkap_api.kb.embed import (
    Embedder,
    TokenCounter,
    check_kb_embedder,
    record_kb_embedder,
    resolve_embedder,
    token_counter_for,
)
from lkap_api.kb.store import VectorRecord, VectorStore, get_lancedb_store
from lkap_api.logging import get_logger
from lkap_api.storage.base import UploadTooLargeError
from lkap_api.storage.resolve import default_storage

log = get_logger(__name__)

#: Ingestion failures are stored on the row; truncate so one bad file can't
#: blow up the `kb_documents.error` column.
MAX_ERROR_CHARS = 500

#: The chunker's defaults (`knowledge_bases.chunking` NULL means these).
DEFAULT_MAX_TOKENS: Final = 256
DEFAULT_OVERLAP_TOKENS: Final = 32
#: Bounds a stored `chunking` value is clamped to: below 32 tokens a chunk is
#: a fragment; above 512 the default model truncates it.
MIN_MAX_TOKENS: Final = 32
MAX_MAX_TOKENS: Final = 512

#: Chunks embedded per batch; the document's `progress` is written after each.
PROGRESS_EVERY: Final = 50
#: Joins a heading path in the embedded text and in citations.
HEADING_SEPARATOR: Final = " › "

#: Formats MarkItDown converts to Markdown, by file extension and by media type.
MARKITDOWN_EXTENSIONS: Final[frozenset[str]] = frozenset({".docx", ".pptx", ".xlsx", ".html", ".htm"})
MARKITDOWN_MEDIA_TYPES: Final[dict[str, str]] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/html": ".html",
    "application/xhtml+xml": ".html",
}

#: Called with (chunks embedded so far, total chunks) after every embedding batch.
ProgressCallback = Callable[[int, int], Awaitable[None]]


# --------------------------------------------------------------------------- chunking config
@dataclass(slots=True, frozen=True)
class ChunkingConfig:
    """The structure-aware chunker's budget, stored as `knowledge_bases.chunking`."""

    max_tokens: int = DEFAULT_MAX_TOKENS
    overlap: int = DEFAULT_OVERLAP_TOKENS

    @classmethod
    def from_json(cls, value: Mapping[str, Any] | None) -> ChunkingConfig:
        """Read a stored value leniently: missing or malformed keys fall back to the defaults.

        ``max_tokens`` is clamped to ``[MIN_MAX_TOKENS, MAX_MAX_TOKENS]`` and
        ``overlap`` to ``[0, max_tokens // 2]`` so a bad row can never stall
        the chunker.
        """
        raw = value or {}
        max_tokens = _int_or(raw.get("max_tokens"), DEFAULT_MAX_TOKENS)
        overlap = _int_or(raw.get("overlap"), DEFAULT_OVERLAP_TOKENS)
        max_tokens = min(max(max_tokens, MIN_MAX_TOKENS), MAX_MAX_TOKENS)
        overlap = min(max(overlap, 0), max_tokens // 2)
        return cls(max_tokens=max_tokens, overlap=overlap)

    def to_json(self) -> dict[str, int]:
        """The stored form."""
        return {"max_tokens": self.max_tokens, "overlap": self.overlap}


def _int_or(value: object, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


# --------------------------------------------------------------------------- extraction
@dataclass(slots=True, frozen=True)
class PageSpan:
    """One page (PDF) or slide (PPTX) of the extracted text: ``text[start:end]``, 1-based ``number``."""

    number: int
    start: int
    end: int


@dataclass(slots=True, frozen=True)
class ExtractedDocument:
    """A document's text as the chunker sees it, plus its page boundaries when it has pages."""

    text: str
    pages: tuple[PageSpan, ...] = ()

    def page_at(self, offset: int) -> int | None:
        """The 1-based page holding character ``offset``, or ``None`` for a page-less document."""
        for page in self.pages:
            if page.start <= offset < page.end:
                return page.number
        return self.pages[-1].number if self.pages and offset >= self.pages[-1].start else None


DocumentKind = Literal["pdf", "markitdown", "text"]


def document_kind(filename: str, mime: str) -> tuple[DocumentKind, str]:
    """How a document is extracted, and the file extension MarkItDown is told.

    Upload media types are declared by the client, so the extension wins when
    it is one we know; the media type is the fallback (a url import without
    an extension).
    """
    extension = PurePosixPath(filename.lower()).suffix
    media_type = mime.split(";", 1)[0].strip().lower()
    if media_type == "application/pdf" or extension == ".pdf":
        return "pdf", ".pdf"
    if extension in MARKITDOWN_EXTENSIONS:
        return "markitdown", ".html" if extension == ".htm" else extension
    if media_type in MARKITDOWN_MEDIA_TYPES:
        return "markitdown", MARKITDOWN_MEDIA_TYPES[media_type]
    return "text", extension


def extract_document(*, filename: str, mime: str, data: bytes) -> ExtractedDocument:
    """Extract a document's text (and page boundaries) from its bytes.

    Synchronous and CPU-bound (MarkItDown, pypdf): the ingest path runs it in
    a worker thread.

    Args:
        filename: The original filename (extension sniffing).
        mime: The declared content type.
        data: The raw file bytes.

    Returns:
        The extracted text. PDFs keep one :class:`PageSpan` per page and PPTX
        one per slide; DOCX/PPTX/XLSX/HTML become Markdown (headings kept,
        tags gone); Markdown, plain text, CSV and JSON pass through as UTF-8.
    """
    kind, extension = document_kind(filename, mime)
    if kind == "pdf":
        return _extract_pdf(data)
    if kind == "markitdown":
        return _extract_with_markitdown(data, extension=extension)
    return ExtractedDocument(text=data.decode("utf-8", errors="replace"))


def extract_text(*, filename: str, mime: str, data: bytes) -> str:
    """The extracted text only (see :func:`extract_document`)."""
    return extract_document(filename=filename, mime=mime, data=data).text


def _extract_pdf(data: bytes) -> ExtractedDocument:
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(data))
    parts: list[str] = []
    pages: list[PageSpan] = []
    offset = 0
    for number, page in enumerate(reader.pages, start=1):
        if parts:
            parts.append("\n\n")
            offset += 2
        page_text = (page.extract_text() or "").strip()
        parts.append(page_text)
        pages.append(PageSpan(number=number, start=offset, end=offset + len(page_text)))
        offset += len(page_text)
    return ExtractedDocument(text="".join(parts), pages=tuple(pages))


@functools.cache
def _markitdown() -> Any:
    """One MarkItDown converter per process (built lazily: its import is heavy)."""
    from markitdown import MarkItDown

    return MarkItDown(enable_plugins=False)


_SLIDE_MARKER_RE = re.compile(r"<!--\s*Slide number:\s*(\d+)\s*-->[ \t]*\n?")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^<>\n]*>")


def _extract_with_markitdown(data: bytes, *, extension: str) -> ExtractedDocument:
    from markitdown import StreamInfo

    result = _markitdown().convert_stream(io.BytesIO(data), stream_info=StreamInfo(extension=extension))
    markdown = str(result.markdown or "")
    text, pages = _slides_to_pages(markdown) if extension == ".pptx" else (markdown, ())
    if not pages:
        text = _HTML_COMMENT_RE.sub("", text)
        if extension == ".html":
            # MarkItDown drops scripts and styles and converts markup; this
            # removes any tag it passed through verbatim (inline SVG, custom elements).
            text = _HTML_TAG_RE.sub("", text)
    return ExtractedDocument(text=text.strip(), pages=pages)


def _slides_to_pages(markdown: str) -> tuple[str, tuple[PageSpan, ...]]:
    """Turn MarkItDown's ``<!-- Slide number: N -->`` markers into page spans (the markers are removed)."""
    markers = list(_SLIDE_MARKER_RE.finditer(markdown))
    if not markers:
        return markdown, ()
    parts: list[str] = []
    pages: list[PageSpan] = []
    offset = 0
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(markdown)
        slide = _HTML_COMMENT_RE.sub("", markdown[marker.end() : end]).strip()
        if parts:
            parts.append("\n\n")
            offset += 2
        parts.append(slide)
        pages.append(PageSpan(number=int(marker.group(1)), start=offset, end=offset + len(slide)))
        offset += len(slide)
    return "".join(parts), tuple(pages)


# --------------------------------------------------------------------------- chunking
@dataclass(slots=True, frozen=True)
class Chunk:
    """One chunk: ``source[char_start:char_end] == text``, under ``heading_path``, on ``page``."""

    text: str
    heading_path: tuple[str, ...]
    char_start: int
    char_end: int
    page: int | None = None

    @property
    def embed_text(self) -> str:
        """The text that is embedded: the heading path, then the chunk (cheap contextual chunking)."""
        if not self.heading_path:
            return self.text
        return f"{HEADING_SEPARATOR.join(self.heading_path)}\n\n{self.text}"

    def meta(self, filename: str) -> dict[str, Any]:
        """The `KbChunk.meta` locators."""
        return {
            "filename": filename,
            "heading_path": list(self.heading_path),
            "page": self.page,
            "char_start": self.char_start,
            "char_end": self.char_end,
        }


@dataclass(slots=True)
class _Unit:
    """An unsplittable span (a heading, a sentence, a list item or a table row) and its tokens."""

    start: int
    end: int
    tokens: int
    atomic: bool = False


@dataclass(slots=True)
class _Segment:
    """A run of text between two hard boundaries (headings, pages)."""

    start: int
    end: int
    heading_path: tuple[str, ...]
    page: int | None
    heading_end: int | None = None  # end of the heading line this segment opens with, if any
    units: list[_Unit] = field(default_factory=list)


_LINE_RE = re.compile(r"[^\n]*\n?")
_HEADING_RE = re.compile(r"^[ ]{0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_FENCE_RE = re.compile(r"^[ ]{0,3}(```|~~~)")
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")
#: A list item or a table row (matched at a line start inside the text, hence MULTILINE).
_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+][ \t]+|\d{1,3}[.)][ \t]+|\|)", re.MULTILINE)
_WORD_RE = re.compile(r"\S+")
#: A sentence ends at ., ! or ? (plus closing quotes/brackets) followed by whitespace.
_SENTENCE_END_RE = re.compile(r"[.!?…][\"'”’)\]*_]*(?=\s)")
#: Words after which a full stop is not a sentence end.
_ABBREVIATIONS: Final[frozenset[str]] = frozenset(
    {"e.g", "i.e", "etc", "vs", "mr", "mrs", "ms", "dr", "st", "no", "nos", "approx", "incl", "dept", "inc"}
)


def chunk_document(
    document: ExtractedDocument, *, config: ChunkingConfig, count_tokens: TokenCounter
) -> list[Chunk]:
    """Split ``document`` into structure-aware chunks (see the module docstring).

    Args:
        document: The extracted text and its pages.
        config: The token budget and overlap.
        count_tokens: Measures a span in the embedder's tokens.

    Returns:
        Chunks in document order; blank input returns ``[]``.
    """
    text = document.text
    chunks: list[Chunk] = []
    for segment in _segments(document):
        _split_units(text, segment, count_tokens)
        for start, end in _pack(text, segment.units, config, count_tokens):
            chunks.append(
                Chunk(
                    text=text[start:end],
                    heading_path=segment.heading_path,
                    char_start=start,
                    char_end=end,
                    page=segment.page,
                )
            )
    return chunks


def _segments(document: ExtractedDocument) -> list[_Segment]:
    """Cut the text at every Markdown heading (outside code fences) and every page start."""
    text = document.text
    headings: dict[int, tuple[int, int, str]] = {}  # line start -> (line end, level, title)
    in_fence = False
    for line in _LINE_RE.finditer(text):
        if line.start() == line.end():
            break
        content = line.group().rstrip("\n")
        if _FENCE_RE.match(content):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(content)
        if match:
            headings[line.start()] = (
                line.start() + len(content),
                len(match.group(1)),
                match.group(2).strip(),
            )
    cuts = sorted({0, *headings, *(page.start for page in document.pages)} - {len(text)})
    cuts = [cut for cut in cuts if cut < len(text)]

    segments: list[_Segment] = []
    path: list[tuple[int, str]] = []
    for index, start in enumerate(cuts):
        end = cuts[index + 1] if index + 1 < len(cuts) else len(text)
        heading = headings.get(start)
        heading_end: int | None = None
        if heading is not None:
            heading_end, level, title = heading
            while path and path[-1][0] >= level:
                path.pop()
            path.append((level, title))
        segments.append(
            _Segment(
                start=start,
                end=end,
                heading_path=tuple(title for _, title in path),
                page=document.page_at(start),
                heading_end=heading_end,
            )
        )
    return segments


def _split_units(text: str, segment: _Segment, count_tokens: TokenCounter) -> None:
    """Fill ``segment.units``: its heading line, then list items / rows and sentences of each block."""
    spans: list[tuple[int, int, bool]] = []
    body_start = segment.start
    if segment.heading_end is not None:
        spans.append((segment.start, segment.heading_end, True))
        body_start = segment.heading_end
    block_start = body_start
    for gap in _BLANK_LINE_RE.finditer(text, body_start, segment.end):
        spans.extend((start, end, False) for start, end in _block_units(text, block_start, gap.start()))
        block_start = gap.end()
    spans.extend((start, end, False) for start, end in _block_units(text, block_start, segment.end))
    for start, end, atomic in spans:
        start, end = _strip_span(text, start, end)
        if start < end:
            segment.units.append(
                _Unit(start=start, end=end, tokens=count_tokens(text[start:end]), atomic=atomic)
            )


def _block_units(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """A paragraph's sentences, or a list's / table's items (each with its continuation lines)."""
    lines = [
        (line.start(), line.end())
        for line in _LINE_RE.finditer(text, start, end)
        if line.start() < line.end() and line.start() < end
    ]
    if not any(_ITEM_RE.match(text, line_start, line_end) for line_start, line_end in lines):
        return _sentences(text, start, end)
    units: list[tuple[int, int]] = []
    prose_start: int | None = None
    item: list[int] | None = None
    for line_start, line_end in lines:
        line_end = min(line_end, end)
        if _ITEM_RE.match(text, line_start, line_end):
            if prose_start is not None:
                units.extend(_sentences(text, prose_start, line_start))
                prose_start = None
            if item is not None:
                units.append((item[0], item[1]))
            item = [line_start, line_end]
        elif item is not None:
            item[1] = line_end
        elif prose_start is None:
            prose_start = line_start
    if item is not None:
        units.append((item[0], item[1]))
    if prose_start is not None:
        units.extend(_sentences(text, prose_start, end))
    return units


def _sentences(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Split ``text[start:end]`` into sentences; line breaks inside a paragraph are just spaces."""
    spans: list[tuple[int, int]] = []
    sentence_start = start
    for match in _SENTENCE_END_RE.finditer(text, start, end):
        boundary = match.end()
        if not _is_sentence_end(text, match.start(), boundary, end):
            continue
        spans.append((sentence_start, boundary))
        sentence_start = boundary
    if sentence_start < end:
        spans.append((sentence_start, end))
    return spans


def _is_sentence_end(text: str, mark: int, boundary: int, end: int) -> bool:
    following = text[boundary:end].lstrip()
    if not following:
        return False
    head = following[0]
    if not (head.isupper() or head.isdigit() or head in "\"'“‘([*_#>"):
        return False
    if text[mark] == ".":
        word = re.search(r"([A-Za-z.]+)$", text[max(mark - 12, 0) : mark])
        if word and (word.group(1).lower() in _ABBREVIATIONS or len(word.group(1)) == 1):
            return False
    return True


def _strip_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _pack(
    text: str, units: Sequence[_Unit], config: ChunkingConfig, count_tokens: TokenCounter
) -> list[tuple[int, int]]:
    """Greedily pack a segment's units into chunk spans, carrying whole trailing units as overlap."""
    spans: list[tuple[int, int]] = []
    current: list[_Unit] = []
    tokens = 0
    fresh = False  # does `current` hold anything beyond the carried overlap?

    def flush() -> None:
        nonlocal current, tokens, fresh
        if current and fresh:
            spans.append((current[0].start, current[-1].end))
            carried: list[_Unit] = []
            carried_tokens = 0
            for unit in reversed(current[1:]):
                if carried_tokens + unit.tokens > config.overlap:
                    break
                carried.insert(0, unit)
                carried_tokens += unit.tokens
            current, tokens = carried, carried_tokens
        elif not fresh:
            current, tokens = [], 0
        fresh = False

    for unit in units:
        if unit.tokens > config.max_tokens and not unit.atomic:
            flush()
            current, tokens = [], 0
            spans.extend(_split_long_unit(text, unit, config.max_tokens, count_tokens))
            continue
        if current and tokens + unit.tokens > config.max_tokens:
            flush()
            while current and tokens + unit.tokens > config.max_tokens:
                tokens -= current.pop(0).tokens
        current.append(unit)
        tokens += unit.tokens
        fresh = True
    flush()
    return spans


def _split_long_unit(
    text: str, unit: _Unit, max_tokens: int, count_tokens: TokenCounter
) -> list[tuple[int, int]]:
    """Cut one over-budget sentence at word boundaries (the only mid-sentence cut the chunker makes)."""
    spans: list[tuple[int, int]] = []
    window_start: int | None = None
    window_end = unit.start
    window_tokens = 0
    for word in _WORD_RE.finditer(text, unit.start, unit.end):
        word_tokens = count_tokens(word.group())
        if window_start is not None and window_tokens + word_tokens > max_tokens:
            spans.append((window_start, window_end))
            window_start, window_tokens = None, 0
        if window_start is None:
            window_start = word.start()
        window_end = word.end()
        window_tokens += word_tokens
    if window_start is not None:
        spans.append((window_start, window_end))
    return spans


# --------------------------------------------------------------------------- ingest
@dataclass(slots=True, frozen=True)
class IngestOutcome:
    """The result of ingesting one document, for logging/tests."""

    status: Literal["ready", "failed"]
    chunk_count: int
    error: str | None = None


async def _load_kb(session: AsyncSession, kb_id: str) -> KnowledgeBase | None:
    # Ingestion runs as a job that carries only the ids (deliberately cross-workspace).
    return (
        await session.execute(
            select(KnowledgeBase)
            .where(KnowledgeBase.id == kb_id)
            .execution_options(**{CROSS_WORKSPACE_OPTION: True})
        )
    ).scalar_one_or_none()


async def _document_chunk_count(session: AsyncSession, document_id: str) -> int:
    count = await session.scalar(
        select(func.count()).select_from(KbChunk).where(KbChunk.document_id == document_id)
    )
    return int(count or 0)


async def _finish_document(
    session: AsyncSession,
    *,
    document_id: str,
    kb_id: str,
    status: Literal["ready", "failed"],
    chunk_count: int,
    error: str | None,
) -> None:
    """Set a document's terminal status and recompute its KB's `chunk_count`."""
    document = await session.get(KbDocument, document_id)
    if document is None:
        log.warning("kb_document_missing_on_finish", document_id=document_id, kb_id=kb_id)
        return
    document.status = status
    document.chunk_count = chunk_count
    document.error = error
    if status == "ready":
        document.progress = 1.0
    await session.flush()
    kb = await _load_kb(session, kb_id)
    if kb is not None:
        total = (
            await session.execute(
                select(func.coalesce(func.sum(KbDocument.chunk_count), 0)).where(KbDocument.kb_id == kb_id)
            )
        ).scalar_one()
        kb.chunk_count = int(total)


def _check_vector_width(kb: KnowledgeBase | None, vectors: Sequence[Sequence[float]]) -> None:
    if kb is None or kb.dimension is None or not vectors:
        return
    width = len(vectors[0])
    if width != kb.dimension:
        raise UnprocessableEntityError(
            f"the embedder returned {width}-dimension vectors but knowledge base '{kb.name}' "
            f"holds {kb.dimension}-dimension vectors",
            details={"kb_id": kb.id, "kb_dimension": kb.dimension, "embedder_dimension": width},
        )


async def _replace_existing_chunks(
    session: AsyncSession, store: VectorStore, *, kb_id: str, document_id: str
) -> None:
    """Drop a re-ingested document's previous chunks and vectors (no-op for a new document)."""
    if await _document_chunk_count(session, document_id) == 0:
        return
    await session.execute(delete(KbChunk).where(KbChunk.document_id == document_id))
    await session.flush()
    await store.delete_document(kb_id, document_id)


async def ingest_into_session(
    session: AsyncSession,
    *,
    store: VectorStore,
    embedder: Embedder,
    kb_id: str,
    document_id: str,
    filename: str,
    mime: str,
    data: bytes,
    on_progress: ProgressCallback | None = None,
) -> IngestOutcome:
    """Extract, chunk, embed and upsert one document, using the caller's session/transaction.

    Never raises: any failure (bad file, embedder mismatch or error, vector
    store error) is caught, logged and recorded as the document's ``failed``
    status within the same session, so the caller's transaction still commits
    cleanly with that failure visible to callers polling the document.

    Order matters for SQLite: every chunk is embedded (and ``on_progress``
    called, which may write through another connection) before this session
    writes anything, and the previous chunks of a re-ingested document are
    replaced only once the new ones exist.

    Args:
        session: An open session; not committed here.
        store: The vector store the chunks' embeddings are upserted into.
        embedder: The embedder used to vectorise the chunks.
        kb_id: The owning knowledge base.
        document_id: The (already persisted) document row's id.
        filename: Original filename, used for extension sniffing and metadata.
        mime: Declared content type.
        data: Raw file bytes.
        on_progress: Awaited with ``(embedded, total)`` after every
            :data:`PROGRESS_EVERY` chunks.

    Returns:
        The terminal :class:`IngestOutcome`.
    """
    vectors_written = False
    try:
        kb = await _load_kb(session, kb_id)
        if kb is not None:
            check_kb_embedder(kb, embedder)
        config = ChunkingConfig.from_json(kb.chunking if kb is not None else None)
        document = await asyncio.to_thread(extract_document, filename=filename, mime=mime, data=data)
        count_tokens = await token_counter_for(embedder)
        chunks = await asyncio.to_thread(chunk_document, document, config=config, count_tokens=count_tokens)

        vectors: list[list[float]] = []
        texts = [chunk.embed_text for chunk in chunks]
        for batch_start in range(0, len(texts), PROGRESS_EVERY):
            vectors.extend(await embedder.embed(texts[batch_start : batch_start + PROGRESS_EVERY]))
            if on_progress is not None and len(vectors) < len(texts):
                await on_progress(len(vectors), len(texts))
        _check_vector_width(kb, vectors)

        await _replace_existing_chunks(session, store, kb_id=kb_id, document_id=document_id)
        rows = [
            KbChunk(
                id=new_id(),
                kb_id=kb_id,
                document_id=document_id,
                ordinal=ordinal,
                text=chunk.text,
                meta=chunk.meta(filename),
            )
            for ordinal, chunk in enumerate(chunks)
        ]
        if rows:
            records = [
                VectorRecord(id=row.id, vector=vector, document_id=document_id)
                for row, vector in zip(rows, vectors, strict=True)
            ]
            vectors_written = True
            await store.upsert(kb_id, records)
            session.add_all(rows)
            await session.flush()
            if kb is not None:
                record_kb_embedder(kb, embedder)
        outcome = IngestOutcome(status="ready", chunk_count=len(rows))
    except Exception as exc:  # noqa: BLE001 - persisted as a document-level failure, never raised
        log.warning(
            "kb_ingest_failed",
            kb_id=kb_id,
            document_id=document_id,
            filename=filename,
            error_type=type(exc).__name__,
        )
        if vectors_written:
            await _drop_orphan_vectors(store, kb_id=kb_id, document_id=document_id)
        try:
            remaining = await _document_chunk_count(session, document_id)
        except Exception:  # noqa: BLE001 - the session may be unusable after a failed flush
            remaining = 0
        message = exc.message if isinstance(exc, ApiError) else str(exc)
        outcome = IngestOutcome(status="failed", chunk_count=remaining, error=message[:MAX_ERROR_CHARS])

    await _finish_document(
        session,
        document_id=document_id,
        kb_id=kb_id,
        status=outcome.status,
        chunk_count=outcome.chunk_count,
        error=outcome.error,
    )
    log.info(
        "kb_ingest_finished",
        kb_id=kb_id,
        document_id=document_id,
        status=outcome.status,
        chunk_count=outcome.chunk_count,
    )
    return outcome


async def _drop_orphan_vectors(store: VectorStore, *, kb_id: str, document_id: str) -> None:
    """Best effort: a failed ingest must not leave vectors whose chunk rows never landed."""
    try:
        await store.delete_document(kb_id, document_id)
    except Exception as exc:  # noqa: BLE001 - cleanup of an already-failed ingest
        log.warning(
            "kb_ingest_cleanup_failed", kb_id=kb_id, document_id=document_id, error_type=type(exc).__name__
        )


def ingest_payload(
    *, kb_id: str, document_id: str, storage_key: str, filename: str, mime: str, origin: str | None = None
) -> dict[str, Any]:
    """The JSON payload of a :data:`~lkap_api.jobs.kinds.KB_INGEST` job.

    Args:
        kb_id: The owning knowledge base.
        document_id: The committed ``pending`` document row.
        storage_key: Where the source bytes are stored (:func:`upload_storage_key`).
        filename: The document's filename.
        mime: Its declared content type.
        origin: Set for platform-seeded documents (``template:<id>`` or
            ``pack:<id>``, V4-18); the job then writes a ``kb.seed_ingest``
            audit row naming it. ``None`` (an upload or a url import) adds nothing.

    Returns:
        ``{"kb_id", "document_id", "storage_key", "filename", "mime"}`` plus ``origin`` when given.
    """
    payload: dict[str, Any] = {
        "kb_id": kb_id,
        "document_id": document_id,
        "storage_key": storage_key,
        "filename": filename,
        "mime": mime,
    }
    if origin is not None:
        payload["origin"] = origin
    return payload


def upload_storage_key(kb_id: str, document_id: str, filename: str) -> str:
    """Return the storage key a KB source upload is written to.

    Shared by the router (writes the object) and :func:`run_ingestion_job`
    (reads it back), so the layout only needs to change in one place.
    """
    return f"kb/{kb_id}/{document_id}_{filename}"


def progress_writer(database: Database, document_id: str) -> ProgressCallback:
    """A :data:`ProgressCallback` that commits ``kb_documents.progress`` on its own short session.

    A failed write is logged and ignored: progress is informational and must
    never fail the ingest.
    """

    async def write(done: int, total: int) -> None:
        try:
            async with database.session() as session:
                await session.execute(
                    update(KbDocument)
                    .where(KbDocument.id == document_id)
                    .values(progress=round(done / total, 4) if total else 0.0)
                )
        except Exception as exc:  # noqa: BLE001 - progress is best effort
            log.warning(
                "kb_ingest_progress_write_failed", document_id=document_id, error_type=type(exc).__name__
            )

    return write


@job(KB_INGEST)
async def run_ingestion_job(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Job handler: fetch the uploaded bytes from storage, then ingest them.

    Also the re-index path: re-running it for a document that already has
    chunks replaces them (see :func:`ingest_into_session`).

    Args:
        ctx: The job context (``database``, ``settings``, ``vault``).
        payload: ``{"kb_id", "document_id", "storage_key", "filename", "mime"}``
            and, for a starter's knowledge seed, ``"origin"`` (see
            :func:`ingest_payload`); JSON-serialisable so this also works
            through the ``arq`` backend.

    The request handler that enqueued this must have already committed the
    document row (a separate connection needs it to exist before the chunk
    rows' foreign key can be inserted) and the uploaded bytes to storage (this
    handler cannot read a request body).

    V4-18 (R-V4-65): the embedder is resolved on a short read-only session and
    warmed (a first fastembed download is ~90 s on a cold cache) **before** the
    ingest session opens, so a model download never sits inside a database
    transaction.
    """
    kb_id = str(payload["kb_id"])
    document_id = str(payload["document_id"])
    storage_key = str(payload["storage_key"])
    filename = str(payload["filename"])
    mime = str(payload["mime"])
    raw_origin = payload.get("origin")
    origin = str(raw_origin) if raw_origin is not None else None

    storage = default_storage(ctx.settings)
    try:
        data = await storage.get(storage_key)
    except FileNotFoundError:
        log.warning("kb_ingest_upload_missing", kb_id=kb_id, document_id=document_id, storage_key=storage_key)
        async with ctx.database.session() as session:
            # A re-index of a document whose source is gone keeps its chunks.
            await _finish_document(
                session,
                document_id=document_id,
                kb_id=kb_id,
                status="failed",
                chunk_count=await _document_chunk_count(session, document_id),
                error="uploaded file missing from storage",
            )
        return

    async with ctx.database.session() as session:
        embedder = await resolve_embedder(ctx.settings, session, ctx.vault)
    try:
        await warm_embedder(embedder)
    except Exception as exc:  # noqa: BLE001 - recorded on the document, never raised
        log.warning(
            "kb_ingest_warmup_failed", kb_id=kb_id, document_id=document_id, error_type=type(exc).__name__
        )
        async with ctx.database.session() as session:
            await _finish_document(
                session,
                document_id=document_id,
                kb_id=kb_id,
                status="failed",
                chunk_count=await _document_chunk_count(session, document_id),
                error=f"the embedding model could not be loaded ({type(exc).__name__})",
            )
            await _audit_seed_ingest(session, origin, kb_id=kb_id, document_id=document_id, status="failed")
        return

    store = get_lancedb_store(ctx.settings.data_dir)
    async with ctx.database.session() as session:
        outcome = await ingest_into_session(
            session,
            store=store,
            embedder=embedder,
            kb_id=kb_id,
            document_id=document_id,
            filename=filename,
            mime=mime,
            data=data,
            on_progress=progress_writer(ctx.database, document_id),
        )
        await _audit_seed_ingest(session, origin, kb_id=kb_id, document_id=document_id, status=outcome.status)
    # V5-04: compact the table the ingest just appended to (and index it once large).
    await _optimize_after_ingest(store, kb_id)


async def warm_embedder(embedder: Embedder) -> None:
    """Load ``embedder``'s model now if it has one (``warm()``, duck-typed like ``token_counter``).

    Raises:
        Exception: Whatever the model load raised (a failed download).
    """
    warm = getattr(embedder, "warm", None)
    if warm is not None:
        await warm()


async def _audit_seed_ingest(
    session: AsyncSession, origin: str | None, *, kb_id: str, document_id: str, status: str
) -> None:
    """Add a ``kb.seed_ingest`` audit row for a platform-seeded document (a no-op without ``origin``)."""
    if origin is None:
        return
    kb = await _load_kb(session, kb_id)
    record_audit(
        session,
        workspace_id=kb.workspace_id if kb is not None else None,
        actor_type="system",
        actor_id=None,
        action="kb.seed_ingest",
        target_type="kb_document",
        target_id=document_id,
        payload={"origin": origin, "kb_id": kb_id, "status": status},
    )


async def _optimize_after_ingest(store: VectorStore, kb_id: str) -> None:
    """Best effort: a failed compaction never fails an ingest that already succeeded."""
    try:
        await store.optimize(kb_id)
    except Exception as exc:  # noqa: BLE001 - maintenance only; the next ingest retries it
        log.warning("kb_optimize_failed", kb_id=kb_id, error_type=type(exc).__name__)


# --------------------------------------------------------------------------- url import (v3)
#: Media types a url import accepts (``text/*`` covers markdown, plain text and HTML).
IMPORT_MEDIA_TYPES: Final[frozenset[str]] = frozenset({"application/pdf", "application/json"})
#: How long one url import may take end to end.
IMPORT_TIMEOUT_S: Final = 30.0
#: The extension a stored name gets when the url's last segment has none.
IMPORT_SUFFIX: Final[dict[str, str]] = {
    "text/markdown": ".md",
    "text/plain": ".txt",
    "text/html": ".html",
    "application/pdf": ".pdf",
    "application/json": ".json",
}


class UnsupportedMediaTypeError(ApiError):
    """415 — a url import answered with a content type the knowledge base cannot ingest."""

    status_code = 415
    code = "unsupported_media_type"


@dataclass(slots=True, frozen=True)
class ImportedSource:
    """The fetched body of a url import."""

    data: bytes
    mime: str


def import_policy() -> net_guard.NetPolicy:
    """The network policy of a knowledge-base url import: no private destination, ever.

    The fetched body is stored and later served back through search, so, like
    an HTTP-tool dry run, an import must never read a loopback, private or
    metadata address. That is why the offline request-time check ignores both
    the ``LKAP_ENV=dev`` loopback default and ``LKAP_NET_ALLOW_PRIVATE_HOSTS``
    (which exist for self-hosted LiveKit servers).
    """
    return net_guard.NetPolicy()


def media_type(content_type: str | None) -> str:
    """``text/markdown; charset=utf-8`` → ``text/markdown`` (lower-cased; ``""`` when absent)."""
    return (content_type or "").split(";", 1)[0].strip().lower()


def import_media_type_allowed(mime: str) -> bool:
    """Whether a url import may ingest a body of media type ``mime``."""
    return mime.startswith("text/") or mime in IMPORT_MEDIA_TYPES


async def fetch_import_source(client: httpx.AsyncClient, url: str, *, max_bytes: int) -> ImportedSource:
    """Fetch a url import's body through the guarded client, enforcing type and size.

    The caller has already run :func:`lkap_api.net_guard.validate_url` with
    :func:`import_policy`; the client's own transport re-checks every resolved
    address at connect time and never follows a redirect.

    Args:
        client: The outbound client (``deps.get_http_client``).
        url: The validated url.
        max_bytes: The size cap (the upload cap, 25 MB).

    Returns:
        The body and its bare media type.

    Raises:
        UnprocessableEntityError: The destination was refused at connect time
            (``reason="blocked_destination"``), or the fetch failed or answered
            with a non-2xx status (``reason="fetch_failed"``).
        UnsupportedMediaTypeError: The content type is not text, markdown, JSON or PDF.
        UploadTooLargeError: ``Content-Length`` or the streamed body exceeds ``max_bytes``.
    """
    too_large = UploadTooLargeError(
        f"the document exceeds the {max_bytes} byte limit", details={"max_bytes": max_bytes}
    )
    try:
        async with client.stream("GET", url, timeout=IMPORT_TIMEOUT_S) as response:
            if not 200 <= response.status_code < 300:
                raise UnprocessableEntityError(
                    f"the url answered HTTP {response.status_code}; redirects are not followed",
                    details={"field": "url", "reason": "fetch_failed", "status_code": response.status_code},
                )
            mime = media_type(response.headers.get("content-type"))
            if not import_media_type_allowed(mime):
                raise UnsupportedMediaTypeError(
                    f"cannot ingest content type {mime or 'unknown'!r}; "
                    "text, markdown, JSON and PDF documents are accepted",
                    details={"content_type": mime or None},
                )
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > max_bytes:
                raise too_large
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise too_large
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        blocked = net_guard.blocked_cause(exc)
        if blocked is not None:
            raise UnprocessableEntityError(
                f"{blocked}; outbound requests to private or local networks are refused",
                details={"field": "url", "reason": "blocked_destination"},
            ) from exc
        raise UnprocessableEntityError(
            f"could not fetch the url ({type(exc).__name__})",
            details={"field": "url", "reason": "fetch_failed"},
        ) from exc
    return ImportedSource(data=b"".join(chunks), mime=mime)
