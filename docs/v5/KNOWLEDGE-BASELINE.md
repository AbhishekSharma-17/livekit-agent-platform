# Knowledge retrieval baseline

**Measured 2026-09-26 by V5-05** (the eval harness). This is the figure every later knowledge package must not regress: V5-06 (retrieval gate), V5-10 (console eval runner), V5-13 (pgvector) and anything that changes chunking, embedding, fusion or rerank re-runs the seed evaluation sets and compares against the tables below. How the numbers were produced, and how to reproduce them, is in `_briefs/v5-05-live.md`.

## Setup

| | |
|---|---|
| Code | the V5-05 branch (V5-01 ingest, V5-04 search, V5-06 merged) |
| Stack | an isolated scratch stack: a fresh SQLite database at `alembic upgrade head` (so the FTS5 keyword index exists) and its own data directory; not the dev api or its database |
| Embedder | `BAAI/bge-small-en-v1.5` (fastembed, local; `LKAP_EMBED_MODEL` default), 384 dimensions |
| Reranker | `Xenova/ms-marco-MiniLM-L-6-v2` (fastembed cross-encoder, local; `LKAP_RERANK_MODEL` default), loaded before the runs |
| Chunking | the V5-01 default, `{max_tokens: 256, overlap: 32}` |
| Options | `k = 4` (an agent's `knowledge.top_k` default), `min_score = null` |
| Hardware | the dev laptop (Apple silicon, CPU only) |
| Path | the seed knowledge bases imported exactly as agent creation does (`import_kb_seeds`, then the `kb_ingest` jobs), each evaluation a `kb_evaluate` job enqueued through `JobsService` (inline backend); the query-embedding cache cleared before every run, so each run pays its own embedding cost |

Every run finished with no search warning (no `lexical_unavailable`, no `rerank_failed`).

## Seed evaluation sets

Seven seed knowledge bases ship with the pack and the starter templates (not three, as the plan's card assumed), 32 golden questions in all. Every question names an `expected_text` (a phrase quoted from the right passage); none names an `expected_file`, because each seed knowledge base holds one document and a document-level match would always be true. Tags: `en` or `hi-Latn` (Hindi written in Latin script), and `natural`, `identifier` (a code such as HO-4 or SIU) or `fragment` (a follow-up-style half question).

| Knowledge base | Source | Documents | Chunks | Questions |
|---|---|---|---|---|
| Insurance policy lines | pack `insurance_claim` | 1 | 6 | 6 (1 `hi-Latn`) |
| Intake playbook | pack `insurance_claim` | 1 | 5 | 5 (1 `hi-Latn`) |
| Knowledge assistant · Product FAQ | template `knowledge_assistant` | 1 | 6 | 6 |
| Knowledge assistant · Support playbook | template `knowledge_assistant` | 1 | 6 | 3 |
| Receptionist · Practice info | template `receptionist` | 1 | 7 | 5 (1 `hi-Latn`) |
| Phone agent · FAQ | template `phone_agent` | 1 | 4 | 4 (1 `hi-Latn`) |
| Lead qualification · Offer sheet | template `lead_qualification` | 1 | 5 | 3 |

## Results: all 32 questions

Micro-averaged over questions. `recall@4` is found in the top 4 / scored; `recall@1` found first; MRR the mean reciprocal rank. Latency is the median of the per-knowledge-base p50 of one search (embed, retrieve, fuse, rerank).

| Mode | recall@1 | recall@4 | MRR | p50 search latency |
|---|---|---|---|---|
| `vector` | 0.938 | 1.000 | 0.961 | 34 ms |
| `hybrid` | 0.938 | 1.000 | 0.961 | 34 ms |
| `vector` + rerank | 0.969 | 1.000 | 0.979 | 150 ms |
| `hybrid` + rerank | 0.969 | 1.000 | 0.979 | 121 ms |

By tag (the same four modes, in the same order):

| Tag | n | recall@1 | MRR |
|---|---|---|---|
| `en` | 28 | 0.964 · 0.964 · 1.000 · 1.000 | 0.982 · 0.982 · 1.000 · 1.000 |
| `hi-Latn` | 4 | 0.750 · 0.750 · 0.750 · 0.750 | 0.812 · 0.812 · 0.833 · 0.833 |
| `natural` | 22 | 0.909 · 0.909 · 0.955 · 0.955 | 0.943 · 0.943 · 0.970 · 0.970 |
| `identifier` | 3 | 1.000 in every mode | 1.000 in every mode |
| `fragment` | 7 | 1.000 in every mode | 1.000 in every mode |

## Results per knowledge base

`recall@1` / MRR per mode; recall@4 is 1.000 everywhere.

| Knowledge base | `vector` | `hybrid` | `vector` + rerank | `hybrid` + rerank |
|---|---|---|---|---|
| Insurance policy lines | 0.833 / 0.875 | 0.833 / 0.875 | 0.833 / 0.889 | 0.833 / 0.889 |
| Intake playbook | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| Knowledge assistant · Product FAQ | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| Knowledge assistant · Support playbook | 0.667 / 0.833 | 0.667 / 0.833 | 1.000 / 1.000 | 1.000 / 1.000 |
| Receptionist · Practice info | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| Phone agent · FAQ | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| Lead qualification · Offer sheet | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |

The two questions not answered first:

- "meri gaadi chori ho gayi, kya cover hoga?" (Insurance policy lines, `hi-Latn`): the comprehensive-cover passage is 4th without rerank and 3rd with it. The English embedder does not map the transliterated Hindi for "car stolen" onto "theft", and the keyword stage finds no distinctive word in common. This is the D-V5-15 gap measured, not assumed: a multilingual embedder is the fix to test against this row.
- "Can I tell the customer when the repair will happen?" (Support playbook): the "Never promise" passage is 2nd by cosine; the cross-encoder puts it first.

## How to read this baseline

- **recall@1 and MRR are the regression signals, not recall@4.** Every seed knowledge base is a single document of 4–7 chunks, so the right passage is almost always somewhere in the top 4; recall@4 is saturated at 1.000 and will only move on a severe regression.
- **The sets are small** (3–6 questions per knowledge base, 32 in all): one question moves a per-knowledge-base figure by 0.17–0.33 and the overall figures by about 0.03. Treat a change of one question as a signal to look at that question, not as a trend.
- **Hybrid equals vector here.** On knowledge bases this small the fused list rarely reorders the first hit; the keyword stage is expected to matter on larger sets with exact identifiers (V5-04's `AUTO-11111` fixture). Add identifier questions to a larger knowledge base before concluding anything about hybrid.
- **Latency is a laptop figure on tiny knowledge bases.** The rerank pool is at most the chunk count here (4–7 passages), so a 20-passage pool costs more (V5-04 measured p50 193 ms for 20 full-length passages). The four p50s come from 3–6 searches each.
