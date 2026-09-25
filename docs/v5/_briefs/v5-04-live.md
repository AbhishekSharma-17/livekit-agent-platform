# V5-04 live check: knowledge search in four modes

**Status: rerank latency measured (below); the dev-stack queries are written, not run yet.** The package ran in an isolated worktree with no api of its own, and the dev api and database are the user's. Whoever runs steps 1–3 fills in the **Result** lines and flips the status. PLAN-V5 §6 records "live: deferred (needs the dev api restarted on the merged code)" until then.

V5-04 has **no migration**: the lexical index it reads was created (and back-filled from the existing chunks) by `v5_001_knowledge_p0`. So once `v5_001` is applied to the dev database and the api runs the merged code, the checks below need nothing else.

## Rerank latency (measured by V5-04)

Local cross-encoder `Xenova/ms-marco-MiniLM-L-6-v2` (fastembed `TextCrossEncoder`, the `LKAP_RERANK_MODEL` default), dev laptop (Apple silicon), CPU, passages of about 950 characters (one full 256-token chunk), 30 runs each after a warm-up:

| Passages | p50 | min | p90 |
|---|---|---|---|
| 10 | 95 ms | 89 ms | 106 ms |
| 20 | 193 ms | 179 ms | 211 ms |

Model load plus the first call: 522 ms, once per process. Cost is linear in the passage count, about 9.5 ms per full-length passage. This is about 1.7× the research note's 112 ms for 20 passages of the same length (`knowledge-and-memory.md` §2 P0-6); the cause was not investigated (ONNX thread count or execution provider are the likely knobs). With 256-token chunks the rerank pool should be 10, not 20, wherever it runs inside a voice turn (V5-06). The tool path can afford 20.

The opt-in test `tests/test_kb_search.py::test_local_cross_encoder_disagrees_with_cosine_on_the_fixture` (set `LKAP_TEST_RERANK_MODEL_DIR` to a cache directory) confirmed on the real model that the cross-encoder puts "Comprehensive cover pays out when your car is stolen…" first for "Is a stolen car covered?" where cosine put the collision passage first.

## Rules (HANDOFF rule 3, PLAN-V5 §0.1)

- Searches are read-only; nothing here writes. Use a **scratch api** on its own port and its own data dir if the dev api is not already on the merged code. Never start or stop the user's api on `:8080`, and never touch `api/data/lkap.db` directly.
- Read no `.env*` file. The admin token comes from the operator's shell, never pasted into this file.
- The first `rerank: "local"` call downloads the cross-encoder (~80 MB) into `LKAP_DATA_DIR/models`.

## 1. Find the three seed knowledge bases

```
curl -s -H "X-Admin-Token: $LKAP_ADMIN_TOKEN" http://127.0.0.1:8080/v1/knowledge-bases | jq '.items[] | {id, name, chunk_count}'
```

**Result:**

## 2. Query each in the four modes

For each seed KB id and at least three questions per KB (one natural question, one exact identifier such as a policy or form number, one follow-up-style fragment):

```
for body in \
  '{"query":"<q>","k":4}' \
  '{"query":"<q>","k":4,"mode":"hybrid"}' \
  '{"query":"<q>","k":4,"rerank":"local"}' \
  '{"query":"<q>","k":4,"mode":"hybrid","rerank":"local"}'; do
  curl -s -H "X-Admin-Token: $LKAP_ADMIN_TOKEN" -H 'content-type: application/json' \
    -X POST http://127.0.0.1:8080/v1/knowledge-bases/<kb_id>/search -d "$body" \
    | jq '{warnings, dropped, timings_ms, hits: [.hits[] | {filename, score, score_source, vector_score, lexical_rank, fused_score, rerank_score, page: .meta.page}]}'
done
```

Expect: `warnings` empty (a `lexical_unavailable` warning means `v5_001` is not applied); in hybrid mode the identifier question's chunk has `lexical_rank: 1` and is first; `timings_ms.rerank` close to the table above for the pool size.

**Result:** (per KB: the question, the top filename per mode, `timings_ms.total` per mode)

## 3. The worker path is unchanged

```
curl -s -H "X-Service-Token: $LKAP_SERVICE_TOKEN" -H 'content-type: application/json' \
  -X POST http://127.0.0.1:8080/internal/v1/kb/search -d '{"kb_ids":["<kb_id>"],"query":"<q>","k":4}' | jq '.hits[0] | {score, score_source}'
```

Expect `score_source: "vector"` and the same top hit as before V5-04 (the defaults are plain vector search; V5-06 turns hybrid on for agents).

**Result:**
