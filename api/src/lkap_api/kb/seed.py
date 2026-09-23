"""Create/populate knowledge bases from a pack's `PackManifest.kb_seeds`.

Called from ``routers/agents.py::_seed_config`` when an agent is created from
a pack with `kb_seeds` (docs/CONTRACTS.md §8). Idempotent: re-seeding an
agent from the same pack reuses an existing knowledge base by name and skips
files it has already ingested, so seeding twice never double-imports content.
"""

from __future__ import annotations

import importlib
import importlib.resources
import mimetypes
from collections.abc import Sequence

from lkap_contracts.packs import KbSeed
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lkap_api.db.constants import DEFAULT_WORKSPACE_ID
from lkap_api.db.models import KbDocument, KnowledgeBase
from lkap_api.kb.embed import Embedder
from lkap_api.kb.ingest import ingest_into_session
from lkap_api.kb.store import VectorStore
from lkap_api.logging import get_logger

log = get_logger(__name__)

DEFAULT_SEED_EMBEDDER_ID = "fastembed-embedding"


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


def _read_seed_file(module_path: str, file_name: str) -> bytes | None:
    """Read one seed file relative to a pack's ``seeds/`` directory.

    Returns `None` (logged by the caller) rather than raising, so a missing
    or unpackaged seed file never blocks agent creation. Broad exception
    handling is deliberate: pack packaging can fail in ways importlib itself
    doesn't document (non-package stubs in tests, namespace packages, ...).
    """
    try:
        resource = importlib.resources.files(module_path).joinpath("seeds", file_name)
        if not resource.is_file():
            return None
        return resource.read_bytes()
    except Exception as exc:  # noqa: BLE001 - defensive; see docstring
        log.warning(
            "pack_kb_seed_file_unreadable", module=module_path, file=file_name, error_type=type(exc).__name__
        )
        return None


def _guess_mime(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "text/plain"


async def _get_or_create_kb(
    db: AsyncSession, *, workspace_id: str, name: str, embedder_id: str
) -> KnowledgeBase:
    """Reuse the workspace's knowledge base called ``name`` or create it there."""
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
    row = KnowledgeBase(workspace_id=workspace_id, name=name, embedder_id=embedder_id)
    db.add(row)
    await db.flush()
    log.info("kb_seed_kb_created", kb_id=row.id, name=name)
    return row


async def _already_ingested(db: AsyncSession, *, kb_id: str, filename: str) -> bool:
    existing = (
        await db.execute(
            select(KbDocument.id).where(KbDocument.kb_id == kb_id, KbDocument.filename == filename)
        )
    ).scalar_one_or_none()
    return existing is not None


async def import_pack_kb_seeds(
    *,
    db: AsyncSession,
    store: VectorStore,
    embedder: Embedder,
    packs: Sequence[str],
    pack_id: str,
    seeds: list[KbSeed],
    embedder_id: str = DEFAULT_SEED_EMBEDDER_ID,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> list[str]:
    """Create/reuse knowledge bases from a pack's `kb_seeds` and ingest their files.

    Args:
        db: The session the calling agent-creation transaction is using;
            not committed here (the caller controls the transaction).
        store: Vector store the seed chunks are embedded into.
        embedder: Embedder used for the seed content.
        packs: `LKAP_PACKS`-configured dotted module paths (to locate the pack's
            `seeds/` directory on disk).
        pack_id: The pack's manifest id.
        seeds: `PackManifest.kb_seeds` to import.
        embedder_id: Recorded on newly created knowledge bases (metadata only).
        workspace_id: The workspace of the agent being created; seed knowledge
            bases are created in (and reused only from) that workspace.

    Returns:
        The ids of every knowledge base referenced by ``seeds`` (created or reused),
        in the same order as ``seeds`` — suitable for `AgentConfig.knowledge.kb_ids`.
    """
    module_path = resolve_pack_module_path(packs, pack_id)
    if module_path is None:
        log.warning("pack_kb_seed_module_missing", pack_id=pack_id, packs=list(packs))

    kb_ids: list[str] = []
    for seed in seeds:
        kb = await _get_or_create_kb(
            db, workspace_id=workspace_id, name=seed.kb_name, embedder_id=embedder_id
        )
        kb_ids.append(kb.id)
        if module_path is None:
            continue
        for file_name in seed.files:
            if await _already_ingested(db, kb_id=kb.id, filename=file_name):
                continue
            data = _read_seed_file(module_path, file_name)
            if data is None:
                log.warning("pack_kb_seed_file_missing", pack_id=pack_id, file=file_name)
                continue
            mime = _guess_mime(file_name)
            document = KbDocument(
                kb_id=kb.id, filename=file_name, mime=mime, bytes=len(data), status="pending"
            )
            db.add(document)
            await db.flush()
            await ingest_into_session(
                db,
                store=store,
                embedder=embedder,
                kb_id=kb.id,
                document_id=document.id,
                filename=file_name,
                mime=mime,
                data=data,
            )
    return kb_ids
