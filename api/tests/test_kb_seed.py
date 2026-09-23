"""Tests for `lkap_api.kb.seed`: importing a pack's `kb_seeds` into knowledge bases.

Uses a real on-disk fake pack package (not the `fake_packs` conftest fixture,
which stubs the whole module tree and would break `importlib.resources`) so
seed-file discovery exercises the same code path a real pack does.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lkap_contracts.packs import KbSeed
from sqlalchemy import select

from lkap_api.db.models import KbDocument, KnowledgeBase
from lkap_api.db.session import Database
from lkap_api.kb.embed import FakeEmbedder
from lkap_api.kb.seed import import_pack_kb_seeds, resolve_pack_module_path
from lkap_api.kb.store import LanceDBStore
from lkap_api.settings import Settings

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


async def test_resolve_pack_module_path_matches_by_manifest_id(fake_pack_on_disk: str) -> None:
    assert resolve_pack_module_path([fake_pack_on_disk], "acme_pack") == fake_pack_on_disk


async def test_resolve_pack_module_path_returns_none_for_unknown_pack(fake_pack_on_disk: str) -> None:
    assert resolve_pack_module_path([fake_pack_on_disk], "some_other_pack") is None


async def test_import_pack_kb_seeds_creates_kb_and_ingests_files(
    fake_pack_on_disk: str, settings: Settings, database: Database, tmp_path: Path
) -> None:
    store = LanceDBStore(tmp_path / "lancedb")
    embedder = FakeEmbedder()
    seeds = [KbSeed(kb_name="Claim intake reference", files=["intake.md", "coverage.md"])]

    async with database.session() as session:
        kb_ids = await import_pack_kb_seeds(
            db=session,
            store=store,
            embedder=embedder,
            packs=[fake_pack_on_disk],
            pack_id="acme_pack",
            seeds=seeds,
        )

    assert len(kb_ids) == 1
    async with database.session() as session:
        kb = await session.get(KnowledgeBase, kb_ids[0])
        assert kb is not None
        assert kb.name == "Claim intake reference"
        assert kb.chunk_count >= 2  # at least one chunk per file

        documents = (
            (await session.execute(select(KbDocument).where(KbDocument.kb_id == kb_ids[0]))).scalars().all()
        )
        assert {d.filename for d in documents} == {"intake.md", "coverage.md"}
        assert all(d.status == "ready" for d in documents)

    hits = await store.query(kb_ids[0], (await embedder.embed(["flood damage"]))[0], k=5)
    assert hits


async def test_import_pack_kb_seeds_is_idempotent(
    fake_pack_on_disk: str, settings: Settings, database: Database, tmp_path: Path
) -> None:
    store = LanceDBStore(tmp_path / "lancedb")
    embedder = FakeEmbedder()
    seeds = [KbSeed(kb_name="Claim intake reference", files=["intake.md"])]

    kwargs = {
        "store": store,
        "embedder": embedder,
        "packs": [fake_pack_on_disk],
        "pack_id": "acme_pack",
        "seeds": seeds,
    }
    async with database.session() as session:
        first_ids = await import_pack_kb_seeds(db=session, **kwargs)
    async with database.session() as session:
        second_ids = await import_pack_kb_seeds(db=session, **kwargs)

    assert first_ids == second_ids
    async with database.session() as session:
        stmt = select(KbDocument).where(KbDocument.kb_id == first_ids[0])
        documents = (await session.execute(stmt)).scalars().all()
    assert len(documents) == 1  # the second pass reused, not re-ingested, the file


async def test_import_pack_kb_seeds_missing_module_still_creates_the_kb(
    settings: Settings, database: Database, tmp_path: Path
) -> None:
    store = LanceDBStore(tmp_path / "lancedb")
    embedder = FakeEmbedder()
    seeds = [KbSeed(kb_name="Orphan KB", files=["missing.md"])]

    async with database.session() as session:
        kb_ids = await import_pack_kb_seeds(
            db=session,
            store=store,
            embedder=embedder,
            packs=["packs.nonexistent"],
            pack_id="nonexistent",
            seeds=seeds,
        )

    assert len(kb_ids) == 1
    async with database.session() as session:
        kb = await session.get(KnowledgeBase, kb_ids[0])
        assert kb is not None
        assert kb.chunk_count == 0


async def test_seed_config_populates_agent_config_kb_ids(
    fake_pack_on_disk: str, settings: Settings, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end through `routers.agents._seed_config`, the seam W1 left for this package."""
    from lkap_api.packs import clear_manifest_cache
    from lkap_api.routers import agents as agents_router
    from lkap_api.routers.agents import _seed_config
    from lkap_api.vault import Vault

    # Never the real fastembed model: it downloads ~90 MB (this test once took 1211 s).
    async def _fake_resolve_embedder(*_args: object, **_kwargs: object) -> FakeEmbedder:
        return FakeEmbedder()

    monkeypatch.setattr(agents_router, "resolve_embedder", _fake_resolve_embedder)

    settings.packs = fake_pack_on_disk
    clear_manifest_cache()
    try:
        async with database.session() as session:
            config, panel_id = await _seed_config(session, settings, Vault(settings.master_key), "acme_pack")
    finally:
        clear_manifest_cache()

    assert panel_id == "generic"
    assert len(config.knowledge.kb_ids) == 1

    async with database.session() as session:
        kb = await session.get(KnowledgeBase, config.knowledge.kb_ids[0])
        assert kb is not None
        assert kb.name == "Claim intake reference"
        assert kb.chunk_count >= 2
