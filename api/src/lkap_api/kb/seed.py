"""Create/populate knowledge bases from `KbSeed` lists: a pack's or a starter template's.

Called from ``routers/agents.py`` when an agent is created from a pack or a
starter template with `kb_seeds` (docs/CONTRACTS.md §8, docs/v4/TEMPLATES.md
§4 step 4). :func:`import_kb_seeds` reads files under any ``root / "seeds"``
(a pack package or a catalogue directory); :func:`import_pack_kb_seeds` is the
pack wrapper that resolves the pack's package first. Idempotent: re-seeding
reuses an existing knowledge base by name and skips files it already holds a
document for, so seeding twice never double-imports content.

V4-18 (R-V4-65, ask #127): seeds take the **upload path**. Importing creates
the knowledge base rows and one ``pending`` document per seed file, writes the
file's bytes to the storage backend under the upload key scheme
(:func:`~lkap_api.kb.ingest.upload_storage_key`) and returns one
:data:`~lkap_api.jobs.kinds.KB_INGEST` payload per document. Nothing is
embedded here: the caller commits the agent row first and then enqueues the
payloads, so the create never holds the database's write lock through an
embedding-model load, and the ``jobs`` process stays the single vector-store
writer (V5-04 ask #29).

V5-05: a seeds directory may also hold ``evals.json``, the golden questions
of its knowledge bases keyed by ``kb_name``::

    {"<kb_name>": [{"question": "...", "expected_text": "...",
                    "expected_file": "<a seed file>", "tags": ["en"]}]}

``expected_file`` names one of the seed's files and is stored as that
document's id. A knowledge base's questions are loaded only while it has no
evaluation set at all, so re-seeding neither duplicates them nor overwrites a
set someone has since edited.
"""

from __future__ import annotations

import importlib
import importlib.resources
import mimetypes
from collections.abc import Sequence
from dataclasses import dataclass, field
from importlib.resources.abc import Traversable
from typing import Any

from lkap_contracts.api_models import KbEvalIn
from lkap_contracts.packs import KbSeed
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import KbDocument, KbEval, KnowledgeBase
from lkap_api.kb.embed import Embedder, record_kb_embedder
from lkap_api.kb.ingest import ChunkingConfig, ingest_payload, upload_storage_key
from lkap_api.logging import get_logger
from lkap_api.storage.base import StorageBackend

log = get_logger(__name__)

DEFAULT_SEED_EMBEDDER_ID = "fastembed-embedding"

#: The golden-question file of a seeds directory (V5-05), beside the seed files.
SEED_EVALS_FILE = "evals.json"


class SeedEval(BaseModel):
    """One golden question in a seeds directory's ``evals.json``."""

    model_config = ConfigDict(extra="forbid")

    question: str
    expected_file: str | None = Field(default=None, description="A seed file of the same knowledge base.")
    expected_text: str | None = None
    tags: list[str] = Field(default_factory=list)


SEED_EVALS_ADAPTER: TypeAdapter[dict[str, list[SeedEval]]] = TypeAdapter(dict[str, list[SeedEval]])


@dataclass(slots=True)
class SeedImport:
    """What importing a list of seeds produced.

    Attributes:
        kb_ids: Every knowledge base the seeds reference (created or reused),
            in seed order — suitable for `AgentConfig.knowledge.kb_ids`.
        ingest_payloads: One ``KB_INGEST`` job payload per newly created
            ``pending`` document, to enqueue **after** the caller commits.
        evals_loaded: How many golden questions were added from ``evals.json``
            (V5-05); already in the session, nothing to enqueue.
    """

    kb_ids: list[str] = field(default_factory=list)
    ingest_payloads: list[dict[str, Any]] = field(default_factory=list)
    evals_loaded: int = 0

    def extend(self, other: SeedImport) -> None:
        """Append ``other``'s knowledge bases and payloads to this one."""
        self.kb_ids.extend(other.kb_ids)
        self.ingest_payloads.extend(other.ingest_payloads)
        self.evals_loaded += other.evals_loaded


def resolve_pack_module_path(packs: Sequence[str], pack_id: str) -> str | None:
    """Return the dotted module path in ``packs`` whose manifest has this id.

    Args:
        packs: Dotted module paths from ``LKAP_PACKS`` (e.g. ``["packs.insurance_claim"]``).
        pack_id: The `PackManifest.id` to find.

    Returns:
        The matching dotted path, or `None` if no configured pack matches.
    """
    for path in packs:
        try:
            module = importlib.import_module(f"{path}.manifest")
        except ImportError:
            continue
        manifest = getattr(module, "MANIFEST", None)
        if manifest is not None and getattr(manifest, "id", None) == pack_id:
            return path
    return None


def _pack_seed_root(module_path: str) -> Traversable | None:
    """The package directory of a pack, or ``None`` when it cannot be located.

    Broad exception handling is deliberate: pack packaging can fail in ways
    importlib itself doesn't document (non-package stubs in tests, namespace
    packages, ...), and a missing seed root never blocks agent creation.
    """
    try:
        return importlib.resources.files(module_path)
    except Exception as exc:  # noqa: BLE001 - defensive; see docstring
        log.warning("pack_kb_seed_root_unreadable", module=module_path, error_type=type(exc).__name__)
        return None


def _read_seed_file(root: Traversable, file_name: str, *, source_label: str) -> bytes | None:
    """Read one seed file relative to ``root``'s ``seeds/`` directory.

    Returns `None` (logged by the caller) rather than raising, so a missing
    or unpackaged seed file never blocks agent creation.
    """
    try:
        resource = root.joinpath("seeds", file_name)
        if not resource.is_file():
            return None
        return resource.read_bytes()
    except Exception as exc:  # noqa: BLE001 - defensive; see docstring
        log.warning(
            "kb_seed_file_unreadable", source=source_label, file=file_name, error_type=type(exc).__name__
        )
        return None


def _guess_mime(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "text/plain"


async def _get_or_create_kb(
    db: AsyncSession, *, workspace_id: str, name: str, embedder_id: str, embedder: Embedder
) -> KnowledgeBase:
    """Reuse the workspace's knowledge base called ``name`` or create it there.

    A new knowledge base records ``embedder``'s model and width and the
    default chunking (V5-01); a reused one is left exactly as it is. Recording
    reads the embedder's metadata only; no model is loaded.
    """
    existing = (
        (
            await db.execute(
                select(KnowledgeBase).where(
                    KnowledgeBase.workspace_id == workspace_id, KnowledgeBase.name == name
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing
    row = KnowledgeBase(
        workspace_id=workspace_id,
        name=name,
        embedder_id=embedder_id,
        chunking=ChunkingConfig().to_json(),
    )
    record_kb_embedder(row, embedder)
    db.add(row)
    await db.flush()
    log.info("kb_seed_kb_created", kb_id=row.id, name=name)
    return row


async def _already_ingested(db: AsyncSession, *, kb_id: str, filename: str) -> bool:
    """Whether ``kb_id`` already holds a document called ``filename`` (in any status)."""
    existing = (
        await db.execute(
            select(KbDocument.id).where(KbDocument.kb_id == kb_id, KbDocument.filename == filename).limit(1)
        )
    ).scalar_one_or_none()
    return existing is not None


async def _stage_seed_file(
    db: AsyncSession,
    storage: StorageBackend,
    *,
    kb: KnowledgeBase,
    file_name: str,
    data: bytes,
    origin: str,
) -> dict[str, Any]:
    """Create the ``pending`` document, store its bytes like an upload, and return its job payload."""
    mime = _guess_mime(file_name)
    document = KbDocument(
        kb_id=kb.id, filename=file_name, mime=mime, bytes=len(data), status="pending", progress=0.0
    )
    db.add(document)
    await db.flush()
    storage_key = upload_storage_key(kb.id, document.id, file_name)
    await storage.put(storage_key, data, content_type=mime)
    log.info("kb_seed_document_staged", kb_id=kb.id, document_id=document.id, origin=origin, file=file_name)
    return ingest_payload(
        kb_id=kb.id,
        document_id=document.id,
        storage_key=storage_key,
        filename=file_name,
        mime=mime,
        origin=origin,
    )


def read_seed_evals(root: Traversable, *, source_label: str) -> dict[str, list[SeedEval]]:
    """Parse ``root / "seeds" / evals.json``; an absent file is ``{}``.

    A file that cannot be read or parsed is logged and treated as absent, so
    it never blocks agent creation (the shipped files are validated by tests).
    """
    try:
        resource = root.joinpath("seeds", SEED_EVALS_FILE)
        if not resource.is_file():
            return {}
        return SEED_EVALS_ADAPTER.validate_json(resource.read_bytes())
    except (ValidationError, OSError) as exc:
        log.warning("kb_seed_evals_unreadable", source=source_label, error_type=type(exc).__name__)
        return {}


async def _load_seed_evals(
    db: AsyncSession, *, kb: KnowledgeBase, entries: list[SeedEval], source_label: str
) -> int:
    """Add ``entries`` as ``kb``'s evaluation set unless it already has one; return how many were added."""
    existing = await db.execute(select(KbEval.id).where(KbEval.kb_id == kb.id).limit(1))
    if existing.scalar_one_or_none() is not None:
        return 0
    documents = {
        str(filename): str(document_id)
        for document_id, filename in (
            await db.execute(select(KbDocument.id, KbDocument.filename).where(KbDocument.kb_id == kb.id))
        ).all()
    }
    rows: list[KbEval] = []
    for entry in entries:
        document_id = documents.get(entry.expected_file) if entry.expected_file else None
        try:
            item = KbEvalIn(
                question=entry.question,
                expected_document_id=document_id,
                expected_text=entry.expected_text,
                tags=entry.tags,
            )
        except ValidationError:
            log.warning("kb_seed_eval_invalid", source=source_label, kb_id=kb.id, ordinal=len(rows))
            continue
        rows.append(
            KbEval(
                kb_id=kb.id,
                question=item.question,
                expected_document_id=item.expected_document_id,
                expected_text=item.expected_text,
                tags=list(item.tags),
                ordinal=len(rows),
            )
        )
    db.add_all(rows)
    await db.flush()
    log.info("kb_seed_evals_loaded", source=source_label, kb_id=kb.id, count=len(rows))
    return len(rows)


async def import_kb_seeds(
    *,
    db: AsyncSession,
    storage: StorageBackend,
    embedder: Embedder,
    root: Traversable | None,
    source_label: str,
    seeds: list[KbSeed],
    embedder_id: str = DEFAULT_SEED_EMBEDDER_ID,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> SeedImport:
    """Create/reuse knowledge bases from ``seeds`` and stage their files from ``root / "seeds"``.

    Nothing is embedded: each new file becomes a ``pending`` document whose
    bytes are in ``storage`` and whose ``KB_INGEST`` payload is returned. The
    caller commits, then enqueues the payloads (the job needs the committed row).
    The golden questions of ``root / "seeds" / evals.json`` are added to each
    knowledge base that has no evaluation set yet (V5-05).

    Args:
        db: The session the calling agent-creation transaction is using;
            not committed here (the caller controls the transaction).
        storage: The platform storage backend the seed bytes are written to
            (a validation failure that rolls the transaction back leaves these
            objects behind, unreferenced).
        embedder: The configured embedder; only its model id and width are
            recorded on new knowledge bases.
        root: The directory holding ``seeds/`` (a pack package or a template's
            catalogue directory); ``None`` creates the knowledge bases but
            stages nothing.
        source_label: The seed source (``pack:<id>`` or ``template:<id>``),
            logged and carried as the job payload's ``origin``.
        seeds: The seeds to import.
        embedder_id: Recorded on newly created knowledge bases (metadata only).
        workspace_id: The workspace of the agent being created; seed knowledge
            bases are created in (and reused only from) that workspace.

    Returns:
        The knowledge base ids (in seed order), one ingest payload per staged
        file and the number of golden questions loaded.
    """
    result = SeedImport()
    seed_evals = read_seed_evals(root, source_label=source_label) if root is not None else {}
    for seed in seeds:
        kb = await _get_or_create_kb(
            db, workspace_id=workspace_id, name=seed.kb_name, embedder_id=embedder_id, embedder=embedder
        )
        result.kb_ids.append(kb.id)
        if root is None:
            continue
        for file_name in seed.files:
            if await _already_ingested(db, kb_id=kb.id, filename=file_name):
                continue
            data = _read_seed_file(root, file_name, source_label=source_label)
            if data is None:
                log.warning("kb_seed_file_missing", source=source_label, file=file_name)
                continue
            result.ingest_payloads.append(
                await _stage_seed_file(
                    db, storage, kb=kb, file_name=file_name, data=data, origin=source_label
                )
            )
        entries = seed_evals.get(seed.kb_name)
        if entries:
            result.evals_loaded += await _load_seed_evals(
                db, kb=kb, entries=entries, source_label=source_label
            )
    return result


async def import_pack_kb_seeds(
    *,
    db: AsyncSession,
    storage: StorageBackend,
    embedder: Embedder,
    packs: Sequence[str],
    pack_id: str,
    seeds: list[KbSeed],
    embedder_id: str = DEFAULT_SEED_EMBEDDER_ID,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> SeedImport:
    """Create/reuse knowledge bases from a pack's `kb_seeds` and stage their files.

    A thin wrapper over :func:`import_kb_seeds` that locates the pack's package
    (its ``seeds/`` directory) from ``LKAP_PACKS``.

    Args:
        db: The session the calling agent-creation transaction is using;
            not committed here (the caller controls the transaction).
        storage: The platform storage backend the seed bytes are written to.
        embedder: The configured embedder (metadata only, see :func:`import_kb_seeds`).
        packs: `LKAP_PACKS`-configured dotted module paths (to locate the pack's
            `seeds/` directory on disk).
        pack_id: The pack's manifest id.
        seeds: `PackManifest.kb_seeds` to import.
        embedder_id: Recorded on newly created knowledge bases (metadata only).
        workspace_id: The workspace of the agent being created; seed knowledge
            bases are created in (and reused only from) that workspace.

    Returns:
        The knowledge base ids (in seed order) and one ingest payload per staged file.
    """
    module_path = resolve_pack_module_path(packs, pack_id)
    root: Traversable | None = None
    if module_path is None:
        log.warning("pack_kb_seed_module_missing", pack_id=pack_id, packs=list(packs))
    else:
        root = _pack_seed_root(module_path)
    return await import_kb_seeds(
        db=db,
        storage=storage,
        embedder=embedder,
        root=root,
        source_label=f"pack:{pack_id}",
        seeds=seeds,
        embedder_id=embedder_id,
        workspace_id=workspace_id,
    )
