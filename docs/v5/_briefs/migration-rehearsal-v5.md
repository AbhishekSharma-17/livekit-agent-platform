# Migration rehearsals, v5

One section per V5 migration (`PLAN-V5.md` §0.3). Each is rehearsed `upgrade head → downgrade <previous> → upgrade head` on a `.backup` copy of the dev database and on a fresh database, and is **never applied to `api/data/lkap.db` by the package**: the coordinator applies it after its own `sqlite3 api/data/lkap.db ".backup <scratchpad>/…"` (HANDOFF rule 4).

## `v5_001_knowledge_p0` (V5-01)

Rehearsed 2026-09-25 by V5-01. **Not applied** to the dev database, which is still at `v4_002_provider_models`. The only access to it was one `sqlite3 … ".backup <scratchpad>/v501/rehearsal/lkap-copy.db"`.

**Chain.** `down_revision = "v4_002_provider_models"`. §0.3 chains V5 after the costs package's `v4_003`, which has not landed; if it lands first, the coordinator re-chains `v5_001` onto it (`_asks.md` #8) and re-runs this rehearsal.

### What the revision does

| Object | Change |
|---|---|
| `knowledge_bases` | `+ dimension INTEGER NULL`, `+ embedder_model VARCHAR(200) NULL`, `+ chunking JSON NULL` (`ALTER TABLE ADD COLUMN` on SQLite, no table rebuild). Existing rows stay `NULL` = "not recorded, never refused". Nothing is backfilled. |
| `kb_documents` | `+ progress FLOAT NULL`. Existing rows stay `NULL`. |
| `kb_chunks` | `+ ix_kb_chunks_kb_document (kb_id, document_id)`. `meta` is unchanged (already JSON); old chunks keep `{"filename"}` until re-indexed. |
| SQLite only | `kb_chunks_fts`, an FTS5 table `(chunk_id, kb_id UNINDEXED, text)` with the `porter unicode61 remove_diacritics 2` tokenizer, plus the triggers `kb_chunks_fts_ai` / `_ad` / `_au` on `kb_chunks`, backfilled with one `INSERT … SELECT`. It keeps its own copy of the text (not `content='kb_chunks'`), because `kb_chunks` has no INTEGER PRIMARY KEY and its implicit rowids can change on `VACUUM`. The delete trigger finds the row by `MATCH 'chunk_id:"<id>"'` on the indexed column, so it never scans. |
| Postgres only | `kb_chunks.tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED` plus `ix_kb_chunks_tsv USING gin (tsv)`. |
| `kb_evals` (new) | `id PK, kb_id → knowledge_bases ON DELETE CASCADE, question TEXT, expected_document_id VARCHAR(32) NULL (no FK), expected_text TEXT NULL, tags JSON, ordinal INTEGER, created_at` plus `ix_kb_evals_kb (kb_id)`. |

**Downgrade** drops the FTS objects of whichever dialect is running, `kb_evals`, the chunk index and the four columns (SQLite column drops go through `batch_alter_table`, which rebuilds `kb_documents` and `knowledge_bases`).

`alembic/env.py` gains `include_object`, so autogenerate skips the FTS table, its shadow tables, `kb_chunks.tsv` and `ix_kb_chunks_tsv` (`_asks.md` #9).

### Rehearsal on a copy of the dev database

The copy was made at `v4_002_provider_models` with `sqlite3 api/data/lkap.db ".backup <scratchpad>/v501/rehearsal/lkap-copy.db"`. The scripted run (`<scratchpad>/v501/rehearse.py`) printed the following, with alembic's INFO lines dropped:

```
== lkap-copy.db at v4_002_provider_models
  before: {'workspaces': 1, 'agents': 19, 'knowledge_bases': 18, 'kb_documents': 18, 'kb_chunks': 154,
           'agent_knowledge_bases': 0, 'sessions': 63, 'tools': 12, 'provider_models': 14}
  knowledge bases (name, chunk_count, chunk rows): 18
  demo agents (name, attached KBs): 8 "Demo — …" agents (their KBs are referenced from config.knowledge.kb_ids / flow nodes)
  upgrade head: ok (0.29s)
  after up: version=v5_001_knowledge_p0 kb_chunks=154 kb_chunks_fts=154 fts objects=[kb_chunks_fts, _ad, _ai, _au,
            _config, _content, _data, _docsize, _idx]
  rows unchanged by upgrade: yes; every KB unrecorded (NULL dimension/model/chunking): yes
  alembic check (no drift): ok — "No new upgrade operations detected."
  downgrade v4_002_provider_models: ok (0.05s)
  rows unchanged by downgrade: yes
  upgrade head again: ok (0.09s)
  integrity_check=ok foreign_key_check rows=0
  FTS sample MATCH 'text:claim' -> 14 rows
```

"Rows unchanged" compares the table counts above, and every knowledge base's `(name, chunk_count, chunk rows)`, before the upgrade, after it, after the downgrade and after the second upgrade.

**Demo agents on the migrated copy** (`<scratchpad>/v501/demo_compat.py`). For each of the eight `Demo — ` agents, every knowledge-base id in its config (`knowledge.kb_ids` and the flow nodes') loads through the ORM at head. `kb_embedder_mismatch` against the default embedder (`BAAI/bge-small-en-v1.5`, read from the fastembed registry only) refuses none of them, and all of their chunks are still pre-V5-01 (`meta == {"filename"}`):

| Agent | KBs | Refused | Chunks (all legacy meta) |
|---|---|---|---|
| Demo — Blank agent | 2 | 0 | 12 |
| Demo — Insurance claim intake | 3 | 0 | 14 |
| Demo — Knowledge assistant | 4 | 0 | 87 |
| Demo — Lead qualification | 2 | 0 | 9 |
| Demo — Phone agent | 2 | 0 | 9 |
| Demo — Receptionist | 2 | 0 | 7 |
| Demo — Survey / intake form | 1 | 0 | 6 |
| Demo — Vision assistant | 1 | 0 | 6 |

These chunks gain locators only through an explicit `POST /v1/knowledge-bases/{id}/reindex`. Documents seeded from a pack or template were never stored and are skipped (`source_not_stored`); uploaded and url-imported ones are re-chunked.

### Rehearsal on a fresh database

Same script, empty file:

```
upgrade head: ok (0.18s) — kb_chunks=0 kb_chunks_fts=0, all nine FTS objects present
alembic check (no drift): ok
downgrade v4_002_provider_models: ok (0.04s)
upgrade head again: ok (0.02s)
integrity_check=ok foreign_key_check rows=0
```

### Tests

- `api/tests/test_migration_v5_001.py` (SQLite):
  - upgrade on a V4 database keeps the rows, leaves them unrecorded and backfills the index (including an exact `"AUTO-11111"` phrase and a porter-stemmed `flooding → flood` match);
  - the index follows insert, update, delete and the cascading knowledge-base delete;
  - downgrade removes every object and keeps the rows, and the next upgrade reaches head again;
  - a fresh database goes up, down and up.
- `api/tests/test_migrations.py` (existing): fresh `upgrade head` matches the models, and `alembic check` finds no drift.
- `api/tests/test_migrations_v2.py` (existing): a v1 database migrates to head (now including `v5_001`), downgrades to the v1 head and comes back.

**Postgres: not executed yet.** Docker is not running on this machine, and nothing is pushed. The branch has been reviewed:

- the generated column needs Postgres 12 or later (CI uses `postgres:16`);
- the two-argument `to_tsvector('english', text)` is immutable, which a generated column requires;
- the downgrade drops the index, then the column, both `IF EXISTS`.

It first runs in the CI `test-postgres` job's step "Migrate up, down and up again on Postgres" (`upgrade head → downgrade 4135323c6ecc → upgrade head → downgrade base`), which executes both `v5_001` branches. That job's pytest run builds its schema with `create_all`, so it never sees `tsv`. If that step fails on the first push, the fix belongs to this revision.

### To apply (coordinator)

```
sqlite3 api/data/lkap.db ".backup <scratchpad>/lkap-before-v5_001.db"
cd api && uv run alembic upgrade head      # v4_002_provider_models -> v5_001_knowledge_p0
```

It takes well under a second on the dev database. The api needs no restart to stay correct, but the new routes and columns are only served after the api reloads on the V5-01 code.

## Re-rehearsal after the re-chain (coordinator, 2026-09-25)

`v5_001_knowledge_p0` now has `down_revision = "v4_003_session_estimates"` (ask #8). On a fresh `.backup` copy of the dev DB at `v4_002_provider_models`: `upgrade head` ran `v4_003` then `v5_001`; `downgrade v4_002_provider_models` ran both down; `upgrade head` again; row counts (agents 19, sessions 63, kb_chunks 154, tools 12) unchanged at every step; `kb_chunks_fts` holds 154 rows; `integrity_check` ok, `foreign_key_check` clean; `alembic check` reports no drift.
