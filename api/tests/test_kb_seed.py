"""Tests for `lkap_api.kb.seed`: importing a pack's `kb_seeds` into knowledge bases.

Uses a real on-disk fake pack package (not the `fake_packs` conftest fixture,
which stubs the whole module tree and would break `importlib.resources`) so
seed-file discovery exercises the same code path a real pack does.

V4-18 (R-V4-65): importing stages the files (``pending`` documents, bytes in
storage, one ``KB_INGEST`` payload each) and embeds nothing; running the
payloads through the job handler makes them ``ready``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from lkap_contracts.packs import KbSeed
from sqlalchemy import select

from lkap_api.db.models import KbDocument, KnowledgeBase
from lkap_api.db.session import Database
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.deps import build_jobs_service
from lkap_api.kb.embed import FakeEmbedder
from lkap_api.kb.ingest import run_ingestion_job, upload_storage_key
from lkap_api.kb.seed import import_pack_kb_seeds, resolve_pack_module_path
from lkap_api.kb.store import get_lancedb_store
from lkap_api.settings import Settings
from lkap_api.storage.base import StorageBackend
from lkap_api.storage.resolve import default_storage
from lkap_api.vault import Vault

_MANIFEST_SOURCE = """
from lkap_contracts.agent_config import CapabilitiesConfig, PipelineConfig, ProviderRef
from lkap_contracts.packs import KbSeed, PackManifest

MANIFEST = PackManifest(
    id="acme_pack",
    version="0.1.0",
    name="Acme",
    description="A fake pack for kb-seed tests.",
    ui_panel_id="generic",
    default_instructions="You are a helpful assistant.",
    default_greeting="Hi!",
    recommended_pipeline=PipelineConfig(
        mode="cascaded",
        stt=ProviderRef(provider_id="livekit-inference-stt"),
        llm=ProviderRef(provider_id="livekit-inference-llm"),
        tts=ProviderRef(provider_id="livekit-inference-tts"),
    ),
    capabilities=CapabilitiesConfig(),
    tool_names=[],
    state_schema={"type": "object"},
    kb_seeds=[KbSeed(kb_name="Claim intake reference", files=["intake.md", "coverage.md"])],
)
"""


@pytest.fixture
def fake_pack_on_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Install a real importable package `fake_seed_pack` with a manifest and seed files."""
    package_root = tmp_path / "fake_pack_src"
    package_dir = package_root / "fake_seed_pack"
    seeds_dir = package_dir / "seeds"
    seeds_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("")
    (package_dir / "manifest.py").write_text(_MANIFEST_SOURCE)
    (seeds_dir / "intake.md").write_text("# Intake\n\nEvery claim needs a policy number and a loss date.")
    (seeds_dir / "coverage.md").write_text("# Coverage\n\nFlood damage is covered under the HO-4 line.")
    monkeypatch.syspath_prepend(str(package_root))
    return "fake_seed_pack"


@pytest.fixture
def storage(settings: Settings) -> StorageBackend:
    return default_storage(settings)


@pytest.fixture
async def job_context(
    database: Database, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[JobContext]:
    """A `kb_ingest` job context whose embedder is the `FakeEmbedder` (never the real model)."""

    async def _fake_resolve_embedder(*_args: object, **_kwargs: object) -> FakeEmbedder:
        return FakeEmbedder()

    monkeypatch.setattr("lkap_api.kb.ingest.resolve_embedder", _fake_resolve_embedder)
    vault = Vault(settings.master_key)
    jobs = build_jobs_service(database, settings, vault)
    async with httpx.AsyncClient() as http:
        yield JobContext(database=database, settings=settings, vault=vault, http=http, jobs=jobs)
    await jobs.aclose()


async def test_resolve_pack_module_path_matches_by_manifest_id(fake_pack_on_disk: str) -> None:
    assert resolve_pack_module_path([fake_pack_on_disk], "acme_pack") == fake_pack_on_disk


async def test_resolve_pack_module_path_returns_none_for_unknown_pack(fake_pack_on_disk: str) -> None:
    assert resolve_pack_module_path([fake_pack_on_disk], "some_other_pack") is None


async def test_import_pack_kb_seeds_stages_pending_documents_in_storage_without_embedding(
    fake_pack_on_disk: str, settings: Settings, database: Database, storage: StorageBackend
) -> None:
    seeds = [KbSeed(kb_name="Claim intake reference", files=["intake.md", "coverage.md"])]

    async with database.session() as session:
        result = await import_pack_kb_seeds(
            db=session,
            storage=storage,
            embedder=FakeEmbedder(),
            packs=[fake_pack_on_disk],
            pack_id="acme_pack",
            seeds=seeds,
        )

    assert len(result.kb_ids) == 1
    kb_id = result.kb_ids[0]
    async with database.session() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        documents = (
            (await session.execute(select(KbDocument).where(KbDocument.kb_id == kb_id))).scalars().all()
        )
    assert kb is not None and kb.name == "Claim intake reference" and kb.chunk_count == 0
    assert {d.filename for d in documents} == {"intake.md", "coverage.md"}
    assert {d.status for d in documents} == {"pending"}
    by_name = {d.filename: d for d in documents}
    assert [p["filename"] for p in result.ingest_payloads] == ["intake.md", "coverage.md"]
    for payload in result.ingest_payloads:
        document = by_name[payload["filename"]]
        assert payload == {
            "kb_id": kb_id,
            "document_id": document.id,
            "storage_key": upload_storage_key(kb_id, document.id, document.filename),
            "filename": document.filename,
            "mime": "text/markdown",
            "origin": "pack:acme_pack",
        }
        assert (await storage.get(payload["storage_key"])).startswith(b"# ")


async def test_import_pack_kb_seeds_payloads_ingest_through_the_job(
    fake_pack_on_disk: str,
    settings: Settings,
    database: Database,
    storage: StorageBackend,
    job_context: JobContext,
) -> None:
    seeds = [KbSeed(kb_name="Claim intake reference", files=["intake.md", "coverage.md"])]
    async with database.session() as session:
        result = await import_pack_kb_seeds(
            db=session,
            storage=storage,
            embedder=FakeEmbedder(),
            packs=[fake_pack_on_disk],
            pack_id="acme_pack",
            seeds=seeds,
        )

    for payload in result.ingest_payloads:
        await run_ingestion_job(job_context, payload)

    kb_id = result.kb_ids[0]
    async with database.session() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        documents = (
            (await session.execute(select(KbDocument).where(KbDocument.kb_id == kb_id))).scalars().all()
        )
    assert kb is not None and kb.chunk_count >= 2  # at least one chunk per file
    assert {d.status for d in documents} == {"ready"}
    store = get_lancedb_store(settings.data_dir)
    hits = await store.query(kb_id, (await FakeEmbedder().embed(["flood damage"]))[0], k=5)
    assert hits


async def test_import_pack_kb_seeds_is_idempotent(
    fake_pack_on_disk: str, settings: Settings, database: Database, storage: StorageBackend
) -> None:
    seeds = [KbSeed(kb_name="Claim intake reference", files=["intake.md"])]

    async with database.session() as session:
        first = await import_pack_kb_seeds(
            db=session,
            storage=storage,
            embedder=FakeEmbedder(),
            packs=[fake_pack_on_disk],
            pack_id="acme_pack",
            seeds=seeds,
        )
    async with database.session() as session:
        second = await import_pack_kb_seeds(
            db=session,
            storage=storage,
            embedder=FakeEmbedder(),
            packs=[fake_pack_on_disk],
            pack_id="acme_pack",
            seeds=seeds,
        )

    assert first.kb_ids == second.kb_ids
    assert (len(first.ingest_payloads), len(second.ingest_payloads)) == (1, 0)
    async with database.session() as session:
        stmt = select(KbDocument).where(KbDocument.kb_id == first.kb_ids[0])
        documents = (await session.execute(stmt)).scalars().all()
    assert len(documents) == 1  # the second pass reused, not re-staged, the file


async def test_import_pack_kb_seeds_missing_module_still_creates_the_kb(
    settings: Settings, database: Database, storage: StorageBackend
) -> None:
    seeds = [KbSeed(kb_name="Orphan KB", files=["missing.md"])]

    async with database.session() as session:
        result = await import_pack_kb_seeds(
            db=session,
            storage=storage,
            embedder=FakeEmbedder(),
            packs=["packs.nonexistent"],
            pack_id="nonexistent",
            seeds=seeds,
        )

    assert len(result.kb_ids) == 1 and result.ingest_payloads == []
    async with database.session() as session:
        kb = await session.get(KnowledgeBase, result.kb_ids[0])
        assert kb is not None
        assert kb.chunk_count == 0


async def test_seed_config_populates_agent_config_kb_ids(
    fake_pack_on_disk: str, settings: Settings, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end through `routers.agents._seed_config`, the seam W1 left for this package."""
    from lkap_api.packs import clear_manifest_cache
    from lkap_api.routers import agents as agents_router
    from lkap_api.routers.agents import _seed_config

    # Never the real fastembed model: it downloads ~90 MB (this test once took 1211 s).
    async def _fake_resolve_embedder(*_args: object, **_kwargs: object) -> FakeEmbedder:
        return FakeEmbedder()

    monkeypatch.setattr(agents_router, "resolve_embedder", _fake_resolve_embedder)

    settings.packs = fake_pack_on_disk
    clear_manifest_cache()
    try:
        async with database.session() as session:
            config, panel_id, seeds = await _seed_config(
                session, settings, Vault(settings.master_key), "acme_pack"
            )
    finally:
        clear_manifest_cache()

    assert panel_id == "generic"
    assert config.knowledge.kb_ids == seeds.kb_ids and len(seeds.kb_ids) == 1
    assert len(seeds.ingest_payloads) == 2

    async with database.session() as session:
        kb = await session.get(KnowledgeBase, config.knowledge.kb_ids[0])
        assert kb is not None
        assert kb.name == "Claim intake reference"
        assert kb.chunk_count == 0  # nothing is embedded until the jobs run
