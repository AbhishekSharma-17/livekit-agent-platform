# V5-13 live check: pgvector, default-by-database, `kb_reindex`

**Status: the compose check is deferred (needs Docker).** The card's live step is `docker compose -f deploy/docker-compose.prod.yml` with the pgvector image, then seed a knowledge base and search. The Docker daemon is not running on this machine, so the stack was not brought up. What could be checked without Docker was run instead on a scratch Postgres (below). The compose step stays open for whoever has Docker.

## What ran instead (2026-09-27)

A scratch **PostgreSQL 16.2 with pgvector 0.6.2** (the binaries of the `pgserver` wheel, in a session scratch directory, on a local port), its own databases, and its own data directory. It used throwaway tokens and no `.env*` file, and did not touch the dev api, its database or its data directory. The one exception is a read-only copy of the dev data directory's fastembed model cache, used with `HF_HUB_OFFLINE=1` so a cache miss fails instead of downloading.

1. **Migration** on an empty database: `upgrade head`, the CI sequence (`downgrade 4135323c6ecc → upgrade head → downgrade base → upgrade head`), and an idempotent re-run over an existing table. See `migration-rehearsal-v5.md`.
2. **Suites.** The api suite ran against this server the way the CI job runs it (`LKAP_TEST_DATABASE_URL`, `LKAP_VECTOR_STORE=lancedb`). `tests/test_kb_store_pgvector.py` and the Postgres test of `tests/test_kb_reindex.py` ran on `PgVectorStore`. The `halfvec` test is skipped below pgvector 0.7. The CI image (0.8.x) runs it and the `hnsw.iterative_scan` branch.
3. **The LanceDB → pgvector move, with the real embedder** (`BAAI/bge-small-en-v1.5`, 384 dimensions). A migrated database stood in for a pre-V5-13 deployment: `LKAP_VECTOR_STORE=lancedb`. The seven shipped seed knowledge bases (7 documents, 39 chunks, 32 golden questions) were imported the way agent creation does it: `import_kb_seeds`, then the `kb_ingest` jobs. Each knowledge base was evaluated with `kb_evaluate` jobs (`k = 4`, no rerank). Then `LKAP_VECTOR_STORE` was unset, which makes pgvector the store because the database is Postgres, and `reindex_command(None)` was run: the code behind `python -m lkap_api.kb.jobs reindex --all`, inline backend. Every knowledge base was evaluated again.

| Store | Mode | recall@1 | recall@4 | MRR |
|---|---|---|---|---|
| LanceDB (before) | `vector` | 0.938 | 1.000 | 0.961 |
| LanceDB (before) | `hybrid` | 0.906 | 1.000 | 0.940 |
| pgvector (after `kb_reindex`) | `vector` | 0.938 | 1.000 | 0.961 |
| pgvector (after `kb_reindex`) | `hybrid` | 0.906 | 1.000 | 0.940 |

After the re-index: 39 rows in `kb_vectors`, 7 per-knowledge-base HNSW indexes, and every knowledge base recorded as `(384, BAAI/bge-small-en-v1.5)`. **The top-4 hit lists and the rank of the expected passage are identical on every question in both modes**, before and after.

- **`vector` reproduces `KNOWLEDGE-BASELINE.md` exactly** (0.938 / 1.000 / 0.961).
- **`hybrid` on Postgres is 0.906 / 0.940, against the baseline's 0.938 / 0.961.** That baseline was measured on SQLite. The difference comes from the keyword index, not from the vector store. The same number appears with LanceDB on Postgres, because Postgres ranks keywords with `tsv` / `ts_rank_cd` (the `english` configuration), where SQLite uses FTS5 / `bm25`. In one knowledge base ("Insurance policy lines") hybrid MRR is 0.764 against 0.875 for vector. This is information for whoever tunes keyword search on Postgres. It is not a V5-13 regression.
- **The rerank modes were not re-measured.** The cross-encoder is not in any local cache, and the check does not download models.

**Found and fixed during this check.** On the first run, pgvector's hybrid lists differed from the service's own fusion only where two chunks had equal RRF sums, which is common (rank 1 + rank 2 against rank 2 + rank 1). The search pipeline re-sorted the store's fused list by `(score, chunk id)` and so undid `fuse_rrf`'s tie-break. The fan-out now keeps a hybrid store's order among equal scores. `test_the_service_keeps_a_hybrid_stores_fused_order_and_skips_its_own_keyword_stage` pins this.

## Still to do with Docker

```
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/prod.env up -d --build
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/prod.env run --rm jobs python -m lkap_api.kb.jobs reindex --all
```

Then, from the console: create a knowledge base, upload a document, run a test search. Check that `kb_vectors` has rows (`psql … -c "select count(*) from kb_vectors"`) and that `/data/lancedb` gained no table.
