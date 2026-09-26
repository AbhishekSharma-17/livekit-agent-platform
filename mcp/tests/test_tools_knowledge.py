"""Knowledge-base tools (§4.5): multipart upload with wait, url import via the api, untrusted hits."""

from __future__ import annotations

from typing import Any

import pytest
import respx
from conftest import BUILDER_SCOPES, READ_ONLY_SCOPES
from lkap_api.kb.embed import FakeEmbedder

POLICY = "# Flood coverage\n\nFlood damage to a basement is covered under the HO-4 policy line."


async def test_kb_add_document_text_uploads_markdown_and_wait_returns_ready(
    key: Any, mcp_session: Any
) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        kb = (await mcp.call("kb_create", name="Policies"))["data"]
        added = await mcp.call("kb_add_document", kb_id=kb["id"], text=POLICY, filename="policy.md")
        [upload] = mcp.transport.calls("POST", f"/v1/knowledge-bases/{kb['id']}/documents")
        hits = await mcp.call("kb_search", kb_id=kb["id"], query="basement flood")

    assert added["ok"] is True, added
    assert (added["data"]["status"], added["data"]["filename"], added["data"]["mime"]) == (
        "ready",
        "policy.md",
        "text/markdown",
    )
    assert added["data"]["chunk_count"] >= 1
    assert b"multipart/form-data" in mcp.transport.requests[upload].headers["content-type"].encode()
    top = hits["data"][0]["text"]
    assert top["untrusted"] is True and "HO-4" in top["content"] and top["source"] == f"kb:{kb['id']}"


async def test_kb_add_document_url_calls_the_import_route_and_never_fetches_itself(
    key: Any, mcp_session: Any
) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        kb = (await mcp.call("kb_create", name="Imported"))["data"]
        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://example.test/policy.md").respond(
                200, content=POLICY.encode(), headers={"content-type": "text/markdown"}
            )
            added = await mcp.call("kb_add_document", kb_id=kb["id"], url="https://example.test/policy.md")
        imports = mcp.transport.calls("POST", f"/v1/knowledge-bases/{kb['id']}/documents/import")

    assert added["ok"] is True, added
    assert added["data"]["status"] == "ready" and added["data"]["filename"] == "policy.md"
    assert len(imports) == 1
    assert mcp.transport.bodies[imports[0]] == {"url": "https://example.test/policy.md"}


async def test_kb_add_document_blocked_url_relays_the_guard_with_a_hint(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        kb = (await mcp.call("kb_create", name="Guarded"))["data"]
        added = await mcp.call(
            "kb_add_document", kb_id=kb["id"], url="http://169.254.169.254/latest/meta-data"
        )

    assert added["ok"] is False and added["error"]["status"] == 422
    assert added["error"]["details"]["reason"] == "blocked_destination"
    assert "public hosts only" in added["error"]["hint"]


async def test_kb_add_document_needs_exactly_one_source(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        none = await mcp.call("kb_add_document", kb_id="k1")
        both = await mcp.call("kb_add_document", kb_id="k1", text="a", url="https://example.test/a")

    assert none["error"]["code"] == both["error"]["code"] == "invalid_input"


# --------------------------------------------------------------------------- V5-05 eval harness
async def _fake_resolve_embedder(*args: object, **kwargs: object) -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def _fake_eval_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    # The `kb_evaluate` job resolves the embedder itself, like the ingest job.
    monkeypatch.setattr("lkap_api.kb.evals.resolve_embedder", _fake_resolve_embedder)


@pytest.mark.usefixtures("_fake_eval_embedder")
async def test_kb_evals_set_then_kb_evaluate_waits_for_recall_and_mrr(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        kb = (await mcp.call("kb_create", name="Policies"))["data"]
        document = (await mcp.call("kb_add_document", kb_id=kb["id"], text=POLICY, filename="policy.md"))[
            "data"
        ]
        stored = await mcp.call(
            "kb_evals_set",
            kb_id=kb["id"],
            items=[
                {"question": "Is a basement flood covered?", "expected_document_id": document["id"]},
                {
                    "question": "Which policy line covers flood?",
                    "expected_text": "HO-4 policy line",
                    "tags": ["id"],
                },
                {"question": "Is an earthquake covered?", "expected_text": "earthquake rider"},
            ],
        )
        run = await mcp.call("kb_evaluate", kb_id=kb["id"], mode="vector", k=2)
        latest = await mcp.call("kb_evaluate_result", kb_id=kb["id"])
        by_id = await mcp.call("kb_evaluate_result", kb_id=kb["id"], job_id=run["data"]["job_id"])
        [put] = mcp.transport.calls("PUT", f"/v1/knowledge-bases/{kb['id']}/evals")
        [post] = mcp.transport.calls("POST", f"/v1/knowledge-bases/{kb['id']}/evaluate")

    assert stored["ok"] is True and stored["data"]["total"] == 3
    assert "expected_text" not in mcp.transport.bodies[put]["items"][0]
    assert mcp.transport.bodies[post] == {"mode": "vector", "rerank": "none", "min_score": None, "k": 2}
    assert run["ok"] is True, run
    result = run["data"]["result"]
    assert run["data"]["status"] == "done"
    assert (result["scored"], result["found"]) == (3, 2)
    assert result["recall_at_k"] == 0.6667
    assert [item["status"] for item in result["items"]] == ["found", "found", "missed"]
    assert latest["data"] == by_id["data"] == run["data"]


async def test_kb_evaluate_plan_sends_nothing(key: Any, mcp_session: Any) -> None:
    raw = await key(BUILDER_SCOPES)

    async with mcp_session(raw) as mcp:
        planned = await mcp.call("kb_evaluate", kb_id="k1", rerank="local", plan=True)
        planned_set = await mcp.call(
            "kb_evals_set", kb_id="k1", items=[{"question": "q", "expected_text": "x"}], plan=True
        )
        sent = [r for r in mcp.transport.requests if "/evaluate" in r.url.path or "/evals" in r.url.path]

    assert [(step["method"], step["path"]) for step in planned["plan"]] == [
        ("POST", "/v1/knowledge-bases/k1/evaluate")
    ]
    assert planned["plan"][0]["body"] == {"mode": "hybrid", "rerank": "local", "min_score": None, "k": 4}
    assert planned_set["plan"][0]["method"] == "PUT"
    assert planned_set["plan"][0]["body"] == {"items": [{"question": "q", "expected_text": "x", "tags": []}]}
    assert sent == []


async def test_kb_evaluate_result_without_a_finished_run_relays_the_404(key: Any, mcp_session: Any) -> None:
    raw = await key(READ_ONLY_SCOPES)

    async with mcp_session(raw) as mcp:
        result = await mcp.call("kb_evaluate_result", kb_id="missing-kb")

    assert result["ok"] is False and result["error"]["status"] == 404
