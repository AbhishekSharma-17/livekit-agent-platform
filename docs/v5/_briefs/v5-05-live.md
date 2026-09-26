# V5-05 live check — the eval harness and the retrieval baseline

Status: **baseline measured** (2026-09-26) on an isolated scratch stack running this branch; the numbers are in `../KNOWLEDGE-BASELINE.md`. **The HTTP routes against the dev api are deferred** to the coordinator after merge (the dev api is the user's; it needs a restart on the merged code). No migration, no contracts change.

## What was run

A scratch stack, never the dev api or `api/data/lkap.db`:

1. A fresh data directory and SQLite database in the session scratchpad, migrated with `alembic -x url=<scratch db> upgrade head` (so the FTS5 keyword index exists; `create_all` would leave hybrid search vector-only with a `lexical_unavailable` warning).
2. The two local model caches copied into `<scratch>/models` (the embedder from the dev data directory's model cache, read only; the cross-encoder from V5-04's scratch cache), merging their shared `blobs/` store, and `HF_HUB_OFFLINE=1` so a cache miss fails instead of downloading. `Settings` built in code with `_env_file=None` and throwaway tokens; no `.env*` read.
3. Every seed source imported the way agent creation does it: `import_kb_seeds(root=<pack or template dir>, embedder=<the resolved fastembed embedder>)` for the `insurance_claim` pack and the four templates with seeds, then each returned `kb_ingest` payload run through `run_ingestion_job`. Seven knowledge bases, all documents `ready`; each got its `evals.json` questions (32).
4. The cross-encoder loaded once; then per knowledge base and per mode (`vector`, `hybrid`, `vector` + rerank, `hybrid` + rerank; `k = 4`), the query-embedding cache cleared and a `kb_evaluate` job enqueued through `JobsService` (inline backend, the production handler), its result read back with `get_run`. Every run asserted `status == "done"` and an empty `warnings` list.

The run takes about a minute on the dev laptop. The script lived in the session scratchpad and is not committed; the steps above are the whole of it.

## Findings

- The harness caught its own setup error on the first attempt: with the cross-encoder's blobs missing from the copied cache every rerank run reported `rerank_failed` in `warnings` and fell back to the fused order. Anyone repeating a baseline should treat a non-empty `warnings` as "the stack is not equivalent", never as a result.
- recall@4 is saturated (1.000 in every mode) because every seed knowledge base is one document of 4–7 chunks; recall@1 and MRR carry the signal (0.938 / 0.961 without rerank, 0.969 / 0.979 with it).
- Transliterated Hindi is the weak spot (recall@1 0.75 on 4 questions in every mode): the D-V5-15 gap, now measured.

## Still to do on the dev stack (coordinator, after merge and an api restart)

```
KB=<a seed knowledge base id from GET /v1/knowledge-bases>
curl -s -H "X-Admin-Token: $LKAP_ADMIN_TOKEN" http://127.0.0.1:8080/v1/knowledge-bases/$KB/evals | jq '.total'
```

Knowledge bases seeded before V5-05 have no evaluation set (seeding loads `evals.json` only at import time). Put one from the shipped file (map `expected_file` to a document id if any is used; the shipped sets use `expected_text` only), then:

```
for body in '{"mode":"vector"}' '{"mode":"hybrid"}' '{"mode":"vector","rerank":"local"}' '{"mode":"hybrid","rerank":"local"}'; do
  JOB=$(curl -s -H "X-Admin-Token: $LKAP_ADMIN_TOKEN" -H 'content-type: application/json' \
    -X POST http://127.0.0.1:8080/v1/knowledge-bases/$KB/evaluate -d "$body" | jq -r .job_id)
  sleep 5
  curl -s -H "X-Admin-Token: $LKAP_ADMIN_TOKEN" http://127.0.0.1:8080/v1/knowledge-bases/$KB/evaluate/$JOB \
    | jq '{status, error, r: .result | {recall_at_1, recall_at_k, mrr, latency_ms_p50, warnings}}'
done
```

Expect `status: "done"`, empty `warnings`, and figures equal to the baseline's row for that knowledge base (seed documents ingested before V5-01's chunker are measured on their old chunks; the dev figures then differ from the baseline for that reason, not a regression).

**Result:**
