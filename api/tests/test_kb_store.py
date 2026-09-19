"""Tests for `lkap_api.kb.store.LanceDBStore` against a temp on-disk table."""

from __future__ import annotations

from pathlib import Path

from lkap_api.kb.store import LanceDBStore, VectorRecord


async def test_upsert_then_query_returns_the_closest_vector(tmp_path: Path) -> None:
    store = LanceDBStore(tmp_path)
    await store.upsert(
        "kb1",
        [
            VectorRecord(id="c1", vector=[1.0, 0.0, 0.0], document_id="d1"),
            VectorRecord(id="c2", vector=[0.0, 1.0, 0.0], document_id="d1"),
        ],
    )
    hits = await store.query("kb1", [1.0, 0.0, 0.0], k=2)
    assert hits[0].id == "c1"
    assert hits[0].score > hits[1].score


async def test_query_on_a_table_that_does_not_exist_returns_no_hits(tmp_path: Path) -> None:
    store = LanceDBStore(tmp_path)
    assert await store.query("unknown-kb", [0.1, 0.2], k=3) == []


async def test_upsert_is_idempotent_by_id(tmp_path: Path) -> None:
    store = LanceDBStore(tmp_path)
    await store.upsert("kb1", [VectorRecord(id="c1", vector=[1.0, 0.0], document_id="d1")])
    await store.upsert("kb1", [VectorRecord(id="c1", vector=[0.0, 1.0], document_id="d1")])
    hits = await store.query("kb1", [0.0, 1.0], k=5)
    assert len(hits) == 1
    assert hits[0].id == "c1"


async def test_delete_document_removes_only_its_vectors(tmp_path: Path) -> None:
    store = LanceDBStore(tmp_path)
    await store.upsert(
        "kb1",
        [
            VectorRecord(id="c1", vector=[1.0, 0.0], document_id="d1"),
            VectorRecord(id="c2", vector=[0.0, 1.0], document_id="d2"),
        ],
    )
    await store.delete_document("kb1", "d1")
    hits = await store.query("kb1", [1.0, 0.0], k=5)
    assert [h.id for h in hits] == ["c2"]


async def test_delete_kb_drops_the_table(tmp_path: Path) -> None:
    store = LanceDBStore(tmp_path)
    await store.upsert("kb1", [VectorRecord(id="c1", vector=[1.0, 0.0], document_id="d1")])
    await store.delete_kb("kb1")
    assert await store.query("kb1", [1.0, 0.0], k=5) == []


async def test_upsert_rejects_unsafe_ids(tmp_path: Path) -> None:
    store = LanceDBStore(tmp_path)
    try:
        await store.upsert("kb1", [VectorRecord(id="bad id'; drop", vector=[1.0], document_id="d1")])
    except ValueError:
        return
    raise AssertionError("expected upsert to reject an id with unsafe characters")
