"""V5-05 eval harness: scoring (recall@k, MRR, skipped), the evaluate job and routes, seed evals.

The scoring function is exercised with a scripted search so the figures are
exact; the routes run the real job (inline backend, drained by the ASGI test
client) over LanceDB with the ``FakeEmbedder``; the seed tests read both a
temporary seeds directory and every ``evals.json`` this repository ships.
"""

from __future__ import annotations

import importlib.resources
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
from auth_helpers import key_client, make_api_key, make_workspace
from fastapi import FastAPI
from lkap_contracts.api_models import KbEvalIn, KbHit, KbSearchResponse, KbSearchWarning
from lkap_contracts.packs import KbSeed
from sqlalchemy import select, update

from lkap_api.db.models import Job, KbDocument, KbEval, KnowledgeBase, new_id
from lkap_api.db.session import Database
from lkap_api.jobs.context import JobContext
from lkap_api.jobs.deps import build_jobs_service
from lkap_api.jobs.handlers import load_all_handlers
from lkap_api.jobs.kinds import KB_EVALUATE
from lkap_api.kb.embed import FakeEmbedder
from lkap_api.kb.evals import (
    KbEvaluateIn,
    kb_evaluate_payload,
    normalise_text,
    run_kb_evaluate_job,
    score_evals,
)
from lkap_api.kb.seed import SEED_EVALS_FILE, import_kb_seeds, read_seed_evals
from lkap_api.routers.knowledge import get_embedder
from lkap_api.settings import Settings
from lkap_api.storage.resolve import default_storage
from lkap_api.templates.catalog import load_catalog, template_root
from lkap_api.vault import Vault

POLICY = (
    "# Water damage\n\nA burst pipe is covered when the water loss was sudden and accidental.\n\n"
    "# Theft\n\nA police report number is required before a theft claim moves on."
)


async def _fake_resolve_embedder(*args: object, **kwargs: object) -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture(autouse=True)
def _fake_embedder(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    # The ingest and evaluate jobs resolve the embedder themselves (no request in flight).
    monkeypatch.setattr("lkap_api.kb.ingest.resolve_embedder", _fake_resolve_embedder)
    monkeypatch.setattr("lkap_api.kb.evals.resolve_embedder", _fake_resolve_embedder)
    yield
    app.dependency_overrides.pop(get_embedder, None)


# --------------------------------------------------------------------------- scoring
def _eval(
    question: str,
    *,
    document_id: str | None = None,
    text: str | None = None,
    tags: Sequence[str] = (),
) -> KbEval:
    return KbEval(
        id=new_id(),
        kb_id="kb1",
        question=question,
        expected_document_id=document_id,
        expected_text=text,
        tags=list(tags),
        ordinal=0,
    )


def _hit(document_id: str, text: str, score: float = 0.5) -> KbHit:
    return KbHit(
        chunk_id=new_id(), document_id=document_id, filename=f"{document_id}.md", score=score, text=text
    )


class ScriptedSearch:
    """Returns a fixed hit list per question and records what it was asked."""

    def __init__(self, answers: dict[str, list[KbHit]], warnings: Sequence[str] = ()) -> None:
        self.answers = answers
        self.warnings = list(warnings)
        self.asked: list[str] = []

    async def __call__(self, query: str) -> KbSearchResponse:
        self.asked.append(query)
        return KbSearchResponse(
            hits=self.answers.get(query, []),
            warnings=[KbSearchWarning(code="lexical_unavailable", message=w) for w in self.warnings],
        )


async def test_score_evals_two_of_three_found_reports_recall_and_mrr() -> None:
    evals = [
        _eval("burst pipe?", document_id="d1", tags=["water"]),
        _eval("theft report?", text="police   report\nNUMBER", tags=["theft"]),
        _eval("hail?", text="hail damage", tags=["water"]),
    ]
    search = ScriptedSearch(
        {
            "burst pipe?": [_hit("d1", "burst pipe"), _hit("d2", "other")],
            "theft report?": [
                _hit("d2", "a"),
                _hit("d2", "b"),
                _hit("d3", "A Police report number is required"),
            ],
            "hail?": [_hit("d2", "wind"), _hit("d3", "snow")],
        }
    )

    result = await score_evals(
        evals, kb_id="kb1", present_document_ids={"d1", "d2", "d3"}, search=search, options=KbEvaluateIn(k=4)
    )

    assert (result.total, result.scored, result.found, result.skipped) == (3, 3, 2, 0)
    assert result.recall_at_k == pytest.approx(0.6667, abs=1e-4)
    assert result.recall_at_1 == pytest.approx(0.3333, abs=1e-4)
    assert result.mrr == pytest.approx((1 + 1 / 3 + 0) / 3, abs=1e-4)
    assert [(item.status, item.rank) for item in result.items] == [
        ("found", 1),
        ("found", 3),
        ("missed", None),
    ]
    assert [item.reciprocal_rank for item in result.items] == pytest.approx([1.0, 1 / 3, 0.0])
    assert result.items[1].top_hits[2].document_id == "d3" and len(result.items[0].top_hits) == 2
    by_tag = {score.tag: score for score in result.by_tag}
    assert (by_tag["water"].scored, by_tag["water"].found, by_tag["water"].mrr) == (2, 1, 0.5)
    assert (by_tag["theft"].recall_at_k, by_tag["theft"].recall_at_1) == (1.0, 0.0)
    assert (result.mode, result.rerank, result.k) == ("hybrid", "none", 4)
    assert result.latency_ms_p50 is not None


async def test_score_evals_skips_a_deleted_expected_document_and_leaves_it_out_of_the_averages() -> None:
    evals = [_eval("gone?", document_id="deleted", text="anything"), _eval("here?", document_id="d1")]
    search = ScriptedSearch({"here?": [_hit("d1", "x")]})

    result = await score_evals(
        evals, kb_id="kb1", present_document_ids={"d1"}, search=search, options=KbEvaluateIn()
    )

    assert search.asked == ["here?"]
    assert (result.items[0].status, result.items[0].skip_reason) == ("skipped", "expected_document_deleted")
    assert (result.total, result.scored, result.skipped, result.recall_at_k, result.mrr) == (
        2,
        1,
        1,
        1.0,
        1.0,
    )


async def test_score_evals_only_counts_matches_inside_the_top_k() -> None:
    hits = [_hit("d2", "no")] * 4 + [_hit("d1", "yes")]
    search = ScriptedSearch({"q": hits})

    at_4 = await score_evals(
        [_eval("q", document_id="d1")],
        kb_id="kb1",
        present_document_ids={"d1", "d2"},
        search=search,
        options=KbEvaluateIn(k=4),
    )
    at_5 = await score_evals(
        [_eval("q", document_id="d1")],
        kb_id="kb1",
        present_document_ids={"d1", "d2"},
        search=search,
        options=KbEvaluateIn(k=5),
    )

    assert (at_4.items[0].status, at_4.recall_at_k) == ("missed", 0.0)
    assert (at_5.items[0].rank, at_5.mrr) == (5, 0.2)


async def test_score_evals_with_nothing_scorable_reports_null_scores_and_collects_warnings() -> None:
    empty = await score_evals(
        [], kb_id="kb1", present_document_ids=set(), search=ScriptedSearch({}), options=KbEvaluateIn()
    )
    warned = await score_evals(
        [_eval("a", text="x"), _eval("b", text="y")],
        kb_id="kb1",
        present_document_ids=set(),
        search=ScriptedSearch({}, warnings=["keyword search is unavailable"]),
        options=KbEvaluateIn(),
    )

    assert (empty.total, empty.recall_at_k, empty.recall_at_1, empty.mrr, empty.latency_ms_p50) == (
        0,
        None,
        None,
        None,
        None,
    )
    assert warned.warnings == ["keyword search is unavailable"]
    assert warned.recall_at_k == 0.0


def test_normalise_text_collapses_whitespace_and_case() -> None:
    assert normalise_text("  A police\n\treport  ") == "a police report"


# --------------------------------------------------------------------------- routes and job
async def _kb_with_document(
    admin_client: httpx.AsyncClient, name: str = "Harbor Lane policies"
) -> tuple[str, str]:
    kb = (await admin_client.post("/v1/knowledge-bases", json={"name": name})).json()
    upload = await admin_client.post(
        f"/v1/knowledge-bases/{kb['id']}/documents",
        files={"file": ("policy.md", POLICY.encode(), "text/markdown")},
    )
    assert upload.status_code == 202, upload.text
    return kb["id"], upload.json()["id"]


async def _put_evals(admin_client: httpx.AsyncClient, kb_id: str, items: list[dict[str, Any]]) -> None:
    response = await admin_client.put(f"/v1/knowledge-bases/{kb_id}/evals", json={"items": items})
    assert response.status_code == 200, response.text


async def test_evaluate_runs_the_set_as_a_job_and_both_reads_return_the_result(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    kb_id, document_id = await _kb_with_document(admin_client)
    await _put_evals(
        admin_client,
        kb_id,
        [
            {"question": "Is a burst pipe covered?", "expected_document_id": document_id, "tags": ["water"]},
            {"question": "What does a theft claim need?", "expected_text": "police report number"},
            {"question": "Is earthquake damage covered?", "expected_text": "earthquake rider"},
        ],
    )

    started = await admin_client.post(
        f"/v1/knowledge-bases/{kb_id}/evaluate", json={"mode": "vector", "k": 3}
    )
    assert started.status_code == 202, started.text
    run = started.json()
    assert (run["status"], run["kb_id"], run["result"]) == ("pending", kb_id, None)
    assert run["options"] == {"mode": "vector", "rerank": "none", "min_score": None, "k": 3}

    fetched = await admin_client.get(f"/v1/knowledge-bases/{kb_id}/evaluate/{run['job_id']}")
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["status"] == "done" and body["error"] is None
    result = body["result"]
    assert (result["total"], result["scored"], result["found"], result["skipped"]) == (3, 3, 2, 0)
    assert result["recall_at_k"] == pytest.approx(0.6667, abs=1e-4)
    assert [item["status"] for item in result["items"]] == ["found", "found", "missed"]
    assert result["embedder_model"] == FakeEmbedder.model_id
    assert all("text" not in hit for item in result["items"] for hit in item["top_hits"])

    latest = await admin_client.get(f"/v1/knowledge-bases/{kb_id}/evaluate/latest")
    assert latest.status_code == 200
    assert latest.json() == body

    async with database.session() as session:
        job = await session.get(Job, run["job_id"])
    assert job is not None and job.kind == KB_EVALUATE
    assert job.payload["result"]["found"] == 2 and job.payload["kb_id"] == kb_id


async def test_evaluate_defaults_to_the_agent_knowledge_defaults(admin_client: httpx.AsyncClient) -> None:
    kb_id, _ = await _kb_with_document(admin_client)
    await _put_evals(admin_client, kb_id, [{"question": "burst pipe?", "expected_text": "burst pipe"}])

    started = await admin_client.post(f"/v1/knowledge-bases/{kb_id}/evaluate")

    assert started.status_code == 202, started.text
    assert started.json()["options"] == {"mode": "hybrid", "rerank": "none", "min_score": None, "k": 4}
    done = (await admin_client.get(f"/v1/knowledge-bases/{kb_id}/evaluate/latest")).json()
    assert done["result"]["mode"] == "hybrid" and done["result"]["found"] == 1


async def test_an_eval_whose_document_was_deleted_is_reported_skipped(
    admin_client: httpx.AsyncClient,
) -> None:
    kb_id, document_id = await _kb_with_document(admin_client)
    await _put_evals(
        admin_client,
        kb_id,
        [
            {"question": "Is a burst pipe covered?", "expected_document_id": document_id},
            {"question": "What does a theft claim need?", "expected_text": "police report number"},
        ],
    )
    assert (
        await admin_client.delete(f"/v1/knowledge-bases/{kb_id}/documents/{document_id}")
    ).status_code == 204

    run = (await admin_client.post(f"/v1/knowledge-bases/{kb_id}/evaluate", json={"mode": "vector"})).json()
    result = (await admin_client.get(f"/v1/knowledge-bases/{kb_id}/evaluate/{run['job_id']}")).json()[
        "result"
    ]

    assert [item["status"] for item in result["items"]] == ["skipped", "missed"]
    assert result["items"][0]["skip_reason"] == "expected_document_deleted"
    assert (result["scored"], result["skipped"], result["recall_at_k"]) == (1, 1, 0.0)


async def test_latest_is_the_most_recent_finished_run(admin_client: httpx.AsyncClient) -> None:
    kb_id, _ = await _kb_with_document(admin_client)
    await _put_evals(admin_client, kb_id, [{"question": "burst pipe?", "expected_text": "burst pipe"}])
    assert (await admin_client.get(f"/v1/knowledge-bases/{kb_id}/evaluate/latest")).status_code == 404

    await admin_client.post(f"/v1/knowledge-bases/{kb_id}/evaluate", json={"mode": "vector", "k": 1})
    second = (
        await admin_client.post(f"/v1/knowledge-bases/{kb_id}/evaluate", json={"mode": "vector", "k": 2})
    ).json()

    latest = (await admin_client.get(f"/v1/knowledge-bases/{kb_id}/evaluate/latest")).json()
    assert latest["job_id"] == second["job_id"] and latest["result"]["k"] == 2


async def test_evaluate_refuses_an_empty_set_and_a_mismatched_embedder(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    kb_id, _ = await _kb_with_document(admin_client)
    empty = await admin_client.post(f"/v1/knowledge-bases/{kb_id}/evaluate")
    assert empty.status_code == 422 and empty.json()["error"]["details"]["field"] == "evals"

    await _put_evals(admin_client, kb_id, [{"question": "q", "expected_text": "x"}])
    async with database.session() as session:
        await session.execute(
            update(KnowledgeBase).where(KnowledgeBase.id == kb_id).values(embedder_model="another-model")
        )
    mismatched = await admin_client.post(f"/v1/knowledge-bases/{kb_id}/evaluate")
    assert mismatched.status_code == 422
    async with database.session() as session:
        assert (await session.execute(select(Job).where(Job.kind == KB_EVALUATE))).first() is None


@pytest.mark.parametrize("body", [{"k": 0}, {"k": 21}, {"mode": "keyword"}, {"min_score": 2}, {"extra": 1}])
async def test_evaluate_validates_its_options(admin_client: httpx.AsyncClient, body: dict[str, Any]) -> None:
    kb_id, _ = await _kb_with_document(admin_client)
    await _put_evals(admin_client, kb_id, [{"question": "q", "expected_text": "x"}])
    response = await admin_client.post(f"/v1/knowledge-bases/{kb_id}/evaluate", json=body)
    assert response.status_code == 422


async def test_evaluate_routes_are_workspace_scoped(
    admin_client: httpx.AsyncClient, database: Database
) -> None:
    kb_id, _ = await _kb_with_document(admin_client)
    await _put_evals(admin_client, kb_id, [{"question": "burst pipe?", "expected_text": "burst pipe"}])
    run = (await admin_client.post(f"/v1/knowledge-bases/{kb_id}/evaluate", json={"mode": "vector"})).json()

    other_workspace = await make_workspace(database, "globex")
    async with database.session() as session:
        foreign = KnowledgeBase(workspace_id=other_workspace, name="Globex policies")
        session.add(foreign)
        await session.flush()
        session.add(KbEval(kb_id=foreign.id, question="secret?", expected_text="x", tags=[]))
        foreign_id = foreign.id
    other_kb_id, _ = await _kb_with_document(admin_client, name="Other")

    assert (await admin_client.post(f"/v1/knowledge-bases/{foreign_id}/evaluate")).status_code == 404
    assert (await admin_client.get(f"/v1/knowledge-bases/{foreign_id}/evaluate/latest")).status_code == 404
    assert (
        await admin_client.get(f"/v1/knowledge-bases/{foreign_id}/evaluate/{run['job_id']}")
    ).status_code == 404
    # A run is only readable under its own knowledge base, and only a `kb_evaluate` job is a run.
    assert (
        await admin_client.get(f"/v1/knowledge-bases/{other_kb_id}/evaluate/{run['job_id']}")
    ).status_code == 404
    async with database.session() as session:
        other_job = Job(kind="kb_delete", payload={"kb_id": kb_id}, status="done")
        session.add(other_job)
        await session.flush()
        other_job_id = other_job.id
    assert (await admin_client.get(f"/v1/knowledge-bases/{kb_id}/evaluate/{other_job_id}")).status_code == 404


async def test_evaluate_needs_write_access_and_reading_needs_read(
    app: FastAPI, admin_client: httpx.AsyncClient, client: httpx.AsyncClient, database: Database
) -> None:
    kb_id, _ = await _kb_with_document(admin_client)
    await _put_evals(admin_client, kb_id, [{"question": "burst pipe?", "expected_text": "burst pipe"}])
    _, reader_key = await make_api_key(database, ["agents:read"])

    assert (await client.post(f"/v1/knowledge-bases/{kb_id}/evaluate")).status_code == 401
    async with key_client(app, reader_key) as reader:
        assert (await reader.post(f"/v1/knowledge-bases/{kb_id}/evaluate")).status_code == 403
        assert (await reader.get(f"/v1/knowledge-bases/{kb_id}/evaluate/latest")).status_code == 404


async def test_the_job_fails_when_the_knowledge_base_is_gone(database: Database, settings: Settings) -> None:
    assert KB_EVALUATE in load_all_handlers()
    vault = Vault(settings.master_key)
    jobs = build_jobs_service(database, settings, vault)
    async with httpx.AsyncClient() as http:
        ctx = JobContext(database=database, settings=settings, vault=vault, http=http, jobs=jobs)
        payload = kb_evaluate_payload(kb_id="missing", workspace_id="default", options=KbEvaluateIn())
        with pytest.raises(LookupError, match="no longer exists"):
            await run_kb_evaluate_job(ctx, payload)
    await jobs.aclose()


# --------------------------------------------------------------------------- seed evals
def _seeds_dir(tmp_path: Path, evals: str | None) -> Path:
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    (seeds / "intake.md").write_text("# Intake\n\nEvery claim needs a policy number and a loss date.")
    (seeds / "coverage.md").write_text("# Coverage\n\nFlood damage is covered under the HO-4 line.")
    if evals is not None:
        (seeds / SEED_EVALS_FILE).write_text(evals)
    return tmp_path


SEED_EVALS = """{
  "Claim intake reference": [
    {"question": "What does a claim need?", "expected_file": "intake.md", "tags": ["en"]},
    {"question": "Is flood covered?", "expected_text": "covered under the HO-4 line"},
    {"question": "Nothing to expect", "expected_file": "not-a-seed-file.md"}
  ],
  "Some other knowledge base": [{"question": "ignored", "expected_text": "x"}]
}"""


async def test_import_kb_seeds_loads_the_evals_once_and_resolves_files_to_documents(
    tmp_path: Path, settings: Settings, database: Database
) -> None:
    root = _seeds_dir(tmp_path, SEED_EVALS)
    seeds = [KbSeed(kb_name="Claim intake reference", files=["intake.md", "coverage.md"])]
    storage = default_storage(settings)

    async with database.session() as session:
        first = await import_kb_seeds(
            db=session, storage=storage, embedder=FakeEmbedder(), root=root, source_label="test", seeds=seeds
        )
    async with database.session() as session:
        again = await import_kb_seeds(
            db=session, storage=storage, embedder=FakeEmbedder(), root=root, source_label="test", seeds=seeds
        )

    assert (first.evals_loaded, again.evals_loaded) == (2, 0)
    assert again.kb_ids == first.kb_ids
    kb_id = first.kb_ids[0]
    async with database.session() as session:
        rows = (
            (await session.execute(select(KbEval).where(KbEval.kb_id == kb_id).order_by(KbEval.ordinal)))
            .scalars()
            .all()
        )
        intake_id = (
            await session.execute(
                select(KbDocument.id).where(KbDocument.kb_id == kb_id, KbDocument.filename == "intake.md")
            )
        ).scalar_one()
    assert [(row.question, row.ordinal) for row in rows] == [
        ("What does a claim need?", 0),
        ("Is flood covered?", 1),
    ]
    assert (rows[0].expected_document_id, rows[0].tags) == (intake_id, ["en"])
    assert (rows[1].expected_document_id, rows[1].expected_text) == (None, "covered under the HO-4 line")


async def test_seed_evals_never_overwrite_an_existing_set(
    tmp_path: Path, settings: Settings, database: Database
) -> None:
    storage = default_storage(settings)
    seeds = [KbSeed(kb_name="Claim intake reference", files=["intake.md"])]
    async with database.session() as session:
        first = await import_kb_seeds(
            db=session,
            storage=storage,
            embedder=FakeEmbedder(),
            root=_seeds_dir(tmp_path, None),
            source_label="test",
            seeds=seeds,
        )
        session.add(KbEval(kb_id=first.kb_ids[0], question="mine", expected_text="x", tags=[], ordinal=0))
    later = tmp_path / "later"
    later.mkdir()
    async with database.session() as session:
        again = await import_kb_seeds(
            db=session,
            storage=storage,
            embedder=FakeEmbedder(),
            root=_seeds_dir(later, SEED_EVALS),
            source_label="test",
            seeds=seeds,
        )
        questions = (
            await session.execute(select(KbEval.question).where(KbEval.kb_id == first.kb_ids[0]))
        ).scalars()
        assert list(questions) == ["mine"]
    assert (first.evals_loaded, again.evals_loaded) == (0, 0)


def test_an_unreadable_evals_file_is_treated_as_absent(tmp_path: Path) -> None:
    assert (
        read_seed_evals(_seeds_dir(tmp_path, '{"kb": [{"question": 1, "bogus": true}]}'), source_label="t")
        == {}
    )


def _shipped_seed_sources() -> list[tuple[str, Any, list[KbSeed]]]:
    from packs.insurance_claim.manifest import MANIFEST

    sources: list[tuple[str, Any, list[KbSeed]]] = [
        ("pack:insurance_claim", importlib.resources.files("packs.insurance_claim"), list(MANIFEST.kb_seeds))
    ]
    for template in load_catalog():
        if template.kb_seeds:
            sources.append((f"template:{template.id}", template_root(template.id), list(template.kb_seeds)))
    return sources


@pytest.mark.parametrize("source", _shipped_seed_sources(), ids=lambda source: source[0])
def test_every_shipped_seed_kb_has_valid_evals_that_quote_its_files(
    source: tuple[str, Any, list[KbSeed]],
) -> None:
    label, root, seeds = source
    evals = read_seed_evals(root, source_label=label)
    by_name = {seed.kb_name: seed for seed in seeds}

    assert set(evals) == set(by_name), f"{label}: evals.json must cover exactly its seed knowledge bases"
    for kb_name, entries in evals.items():
        seed = by_name[kb_name]
        corpus = [
            normalise_text(root.joinpath("seeds", name).read_text(encoding="utf-8")) for name in seed.files
        ]
        assert len(entries) >= 3, f"{label} · {kb_name}: at least three golden questions"
        for entry in entries:
            KbEvalIn(
                question=entry.question,
                expected_document_id="d" if entry.expected_file else None,
                expected_text=entry.expected_text,
                tags=entry.tags,
            )
            assert entry.expected_file is None or entry.expected_file in seed.files
            if entry.expected_text is not None:
                assert any(normalise_text(entry.expected_text) in text for text in corpus), entry.question
