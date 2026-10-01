# Migration rehearsals, v5

One section per V5 migration (`PLAN-V5.md` §0.3). Each is rehearsed `upgrade head → downgrade <previous> → upgrade head` on a `.backup` copy of the dev database and on a fresh database, and is **never applied to `api/data/lkap.db` by the package**. The coordinator applies it after its own `sqlite3 api/data/lkap.db ".backup <scratchpad>/…"` (HANDOFF rule 4).

## `v5_001_knowledge_p0` (V5-01)

Rehearsed 2026-09-25 by V5-01. **Not applied** to the dev database, which is still at `v4_002_provider_models`. The only access to it was one `sqlite3 … ".backup <scratchpad>/v501/rehearsal/lkap-copy.db"`.

**Chain.** `down_revision = "v4_002_provider_models"`. §0.3 chains V5 after the costs package's `v4_003`, which has not landed, if it lands first, the coordinator re-chains `v5_001` onto it (`_asks.md` #8) and re-runs this rehearsal.

### What the revision does

| Object | Change |
|---|---|
| `knowledge_bases` | `+ dimension INTEGER NULL`, `+ embedder_model VARCHAR(200) NULL`, `+ chunking JSON NULL` (`ALTER TABLE ADD COLUMN` on SQLite, no table rebuild). Existing rows stay `NULL` = "not recorded, never refused". Nothing is backfilled. |
| `kb_documents` | `+ progress FLOAT NULL`. Existing rows stay `NULL`. |
| `kb_chunks` | `+ ix_kb_chunks_kb_document (kb_id, document_id)`. `meta` is unchanged (already JSON), old chunks keep `{"filename"}` until re-indexed. |
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
| Demo: Blank agent | 2 | 0 | 12 |
| Demo: Insurance claim intake | 3 | 0 | 14 |
| Demo: Knowledge assistant | 4 | 0 | 87 |
| Demo: Lead qualification | 2 | 0 | 9 |
| Demo: Phone agent | 2 | 0 | 9 |
| Demo: Receptionist | 2 | 0 | 7 |
| Demo: Survey / intake form | 1 | 0 | 6 |
| Demo: Vision assistant | 1 | 0 | 6 |

These chunks gain locators only through an explicit `POST /v1/knowledge-bases/{id}/reindex`. Documents seeded from a pack or template were never stored and are skipped (`source_not_stored`). Uploaded and url-imported ones are re-chunked.

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
  - upgrade on a V4 database keeps the rows, leaves them unrecorded and backfills the index (including an exact `"AUTO-11111"` phrase and a porter-stemmed `flooding → flood` match).
  - the index follows insert, update, delete and the cascading knowledge-base delete.
  - downgrade removes every object and keeps the rows, and the next upgrade reaches head again.
  - a fresh database goes up, down and up.
- `api/tests/test_migrations.py` (existing): fresh `upgrade head` matches the models, and `alembic check` finds no drift.
- `api/tests/test_migrations_v2.py` (existing): a v1 database migrates to head (now including `v5_001`), downgrades to the v1 head and comes back.

**Postgres: not executed yet.** Docker is not running on this machine, and nothing is pushed. The branch has been reviewed:

- the generated column needs Postgres 12 or later (CI uses `postgres:16`).
- the two-argument `to_tsvector('english', text)` is immutable, which a generated column requires.
- the downgrade drops the index, then the column, both `IF EXISTS`.

It first runs in the CI `test-postgres` job's step "Migrate up, down and up again on Postgres" (`upgrade head → downgrade 4135323c6ecc → upgrade head → downgrade base`), which executes both `v5_001` branches. That job's pytest run builds its schema with `create_all`, so it never sees `tsv`. If that step fails on the first push, the fix belongs to this revision.

### To apply (coordinator)

```
sqlite3 api/data/lkap.db ".backup <scratchpad>/lkap-before-v5_001.db"
cd api && uv run alembic upgrade head      # v4_002_provider_models -> v5_001_knowledge_p0
```

It takes well under a second on the dev database. The api needs no restart to stay correct, but the new routes and columns are only served after the api reloads on the V5-01 code.

## Re-rehearsal after the re-chain (coordinator, 2026-09-25)

`v5_001_knowledge_p0` now has `down_revision = "v4_003_session_estimates"` (ask #8). On a fresh `.backup` copy of the dev DB at `v4_002_provider_models`: `upgrade head` ran `v4_003` then `v5_001`, `downgrade v4_002_provider_models` ran both down, `upgrade head` again. Row counts (agents 19, sessions 63, kb_chunks 154, tools 12) unchanged at every step, `kb_chunks_fts` holds 154 rows, `integrity_check` ok, `foreign_key_check` clean, `alembic check` reports no drift.

## `v5_003_pgvector` (V5-13)

`down_revision = "v5_010_tool_provider_kind"`. The ledger numbers this revision 003, but the chain puts it after the current head.

### What the revision does

- **SQLite:** nothing, in both directions. LanceDB stays the store. `KbVector` is outside `Base.metadata`, so `create_all` and `alembic check` on SQLite are unchanged.
- **Postgres, upgrade:** `CREATE EXTENSION IF NOT EXISTS vector`, then `kb_vectors` (`chunk_id` PK and FK to `kb_chunks.id ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED`, `kb_id` with a btree index, and `embedding vector` with no fixed width). It also creates one partial HNSW index per existing knowledge base whose `dimension` is recorded: `kbv_hnsw_<id>_<d>` on `(embedding::vector(<d>)) vector_cosine_ops WHERE kb_id = '<id>' AND vector_dims(embedding) = <d>`. That is `halfvec` from 2,001 to 4,000 dimensions on pgvector 0.7 or later, and nothing above that. Every statement is `IF NOT EXISTS`. The revision copies no vectors. `python -m lkap_api.kb.jobs reindex --all` does that afterwards.
- **Postgres, downgrade:** `DROP TABLE IF EXISTS kb_vectors`, which drops its indexes too. The extension stays.

### Rehearsal on a copy of the dev database (SQLite, 2026-09-27)

`sqlite3 api/data/lkap.db ".backup <scratchpad>/devcopy.db"` at `v5_010_tool_provider_kind` (18 knowledge bases, 154 chunks): `upgrade head`, `downgrade v5_010_tool_provider_kind`, `upgrade head`. The row counts were unchanged at every step, and no `kb_vectors` table appeared. A fresh scratch database did the same, and `alembic check` found no drift.

### Rehearsal on Postgres (2026-09-27)

Run on a scratch PostgreSQL 16.2 server with pgvector 0.6.2 (the `pgserver` wheel's binaries, in a scratch directory, Docker is not running on this machine). Database `lkap_mig`:

- `upgrade head` from empty. `kb_vectors`, `pk_kb_vectors` and `ix_kb_vectors_kb_id` were created.
- Two knowledge bases were inserted (384 and 3,072 dimensions), then `downgrade v5_010_tool_provider_kind` and `upgrade head`. The 384-dimension knowledge base got its `kbv_hnsw_…_384` index. The 3,072-dimension one got none, which is correct for pgvector below 0.7.
- `stamp v5_010_tool_provider_kind`, then `upgrade head` over the existing table, index and extension: no error (idempotent).
- The CI sequence: `downgrade 4135323c6ecc`, `upgrade head`, `downgrade base`, `upgrade head`. All clean.
- `alembic check` on Postgres reports only `kb_vectors` and its index as "remove". That is expected. `alembic/env.py` compares against `Base.metadata` only. The fix is filed as an ask on `env.py`. CI does not run `check` on Postgres.

The CI job (`pgvector/pgvector:pg16`, pgvector 0.8.x) repeats the CI sequence in "Migrate up, down and up again on Postgres". It also runs `tests/test_kb_store_pgvector.py` and the Postgres test of `tests/test_kb_reindex.py`, where the `halfvec` and iterative-scan branches that 0.6.2 skips are exercised.

## `v5_009_consent` (V5-15)

### What the revision does

Adds the nullable JSON column `sessions.consent_state` (`lkap_contracts.compliance.ConsentState`: the latest consent answer per kind, folded in by `POST /internal/v1/sessions/{id}/events` from the `consent` events). `upgrade()` is a plain `ADD COLUMN` on both dialects (no table rebuild). Existing rows read `NULL`. `downgrade()` uses SQLite's native `ALTER TABLE sessions DROP COLUMN consent_state` (3.35+, the api venv has 3.47) instead of a batch rebuild, which would reflect the table and lose its CHECK constraints, Postgres uses `op.drop_column`. Chained after `v5_010_tool_provider_kind` (the head when V5-15 started, the ledger reserved the id `v5_009` before V5-47 took `v5_010`).

### Rehearsal on a scratch database (V5-15, 2026-09-27)

The worktree has no dev database and the package rules keep the live one out of reach, so the rehearsal ran on a scratch copy of `api/tests/fixtures/v1_seed.sqlite` in the session scratchpad: `upgrade head` ran every revision from the v1 head through `v5_010` to `v5_009_consent`, `downgrade v5_010_tool_provider_kind` ran `v5_009` down, `upgrade head` again, `alembic current` = `v5_009_consent (head)`.

### Tests

- `api/tests/test_migration_v5_009.py` (SQLite): upgrade adds the column and every existing row reads `NULL`. Downgrade drops it and the `sessions` DDL still carries `status_valid`, `channel_valid` and `recording_status_valid`. The next upgrade reaches head again.
- `api/tests/test_migrations.py` and `test_health.py` (existing): fresh `upgrade head` matches the models (`Session.consent_state`), no drift. The head id keeps the `v5_` prefix.

**Postgres: not executed** (no Postgres here). The revision uses only `op.add_column` / `op.drop_column` with `sa.JSON()` on Postgres. The CI `test-postgres` job's up/down/up step runs it.

### To apply (coordinator)

```
sqlite3 api/data/lkap.db ".backup <scratchpad>/lkap-before-v5_003.db"
cd api && uv run alembic upgrade head      # v5_010_tool_provider_kind -> v5_003_pgvector (a no-op on SQLite)
```

On a Postgres deployment, back up first (`scripts/backup.sh`), switch the server image to `pgvector/pgvector:pg16`, upgrade, then run `python -m lkap_api.kb.jobs reindex --all` (docs/RUNBOOK.md §9.2).

sqlite3 api/data/lkap.db ".backup <scratchpad>/lkap-before-v5_009.db"
cd api && uv run alembic upgrade head      # v5_010_tool_provider_kind -> v5_009_consent
```

Instant on the dev database. **Order matters:** backup → `upgrade head` → restart the api → restart the worker. `Session.consent_state` is a mapped column, so an api on the V5-15 code against an unmigrated database fails on every `sessions` read, not only on consent writes. In the window between the api and the worker restarts, an old worker never holds a consent-gated recording back, so the new api answers its start with 409 (recorded as a failed recording) for any agent that already has `recording.require_consent: true` (none do before V5-17 ships the switch).

## `v5_004_mcp_oauth` (V5-14)

`down_revision = "v5_003_pgvector"`. Additive: two new tables, nothing existing changes.

### What the revision does

- **Upgrade (both dialects):** `mcp_oauth_clients` (`id` PK, `workspace_id` FK → `workspaces` `ON DELETE CASCADE`, `issuer`, `client_id`, `registration` checked `IN ('preregistered','dcr')`, `redirect_uri`, `token_endpoint_auth_method`, `ciphertext` (a vault bag, nullable), `registration_client_uri`, `client_secret_expires_at`, timestamps; index `ix_mcp_oauth_clients_workspace_issuer`) and `mcp_oauth_flows` (`state_hash` PK, `workspace_id` FK, `tool_id` FK → `tools` `ON DELETE CASCADE`, `actor_type`, `actor_id`, `verifier_ciphertext`, `issuer`, `iss_parameter_supported`, `resource`, `token_endpoint`, `revocation_endpoint`, `registration` checked `IN ('preregistered','cimd','dcr')`, `client_id`, `client_row_id` FK → `mcp_oauth_clients` `ON DELETE CASCADE`, `token_endpoint_auth_method`, `scopes`, `redirect_uri`, `created_at`, `expires_at`, `consumed_at`; indexes `ix_mcp_oauth_flows_tool`, `ix_mcp_oauth_flows_expires`).
- **Downgrade:** drops both (flows first). `mcp-oauth` credentials a finished sign-in wrote stay in `credentials`.

### Rehearsal (SQLite, 2026-09-27)

Fresh scratch database in the session scratchpad (never `api/data/lkap.db`): `upgrade head` (… → `v5_003_pgvector` → `v5_004_mcp_oauth`), `downgrade v5_003_pgvector` (both tables gone), `upgrade head` (both tables and the three indexes back), `alembic current` = `v5_004_mcp_oauth (head)`, `alembic check`: "No new upgrade operations detected." `tests/test_mcp_oauth.py::test_the_migration_goes_up_down_and_up_on_a_scratch_copy` repeats up/down/up on a copy of the v1 seed, and `tests/test_migrations.py` confirms the models match head exactly.

Postgres was not rehearsed locally (no server on this machine); the DDL uses only portable types (`TEXT`, `BYTEA` via `LargeBinary`, `BOOLEAN`, `TIMESTAMP`), and the CI Postgres job's up/down/up sequence covers it.

## `v5_002_session_uploads` (V5-19)

### What the revision does

Creates `session_assets` (`id`, `session_id` → `sessions` ON DELETE CASCADE, `workspace_id` →
`workspaces` ON DELETE CASCADE, `kind` with the CHECK `kind IN ('upload','frame','signature','document')`,
`name`, `mime`, `size`, `storage_key` UNIQUE, `sha256`, `meta` JSON nullable, `created_at`) and two
indexes (`ix_session_assets_session (session_id, created_at)`, `ix_session_assets_workspace`). A new
table only: no existing table is touched on either dialect, so there is nothing to rebuild and no data
to backfill. `downgrade()` drops the indexes and the table; the stored bytes stay in the storage
backend under `sessions/` (they are not the database's). Chained after `v5_009_consent`, the head when
V5-19 started (ledger numbers are not chain order). Two ledger deviations: `meta` and the `document`
kind (ask #116).

### Rehearsal on a scratch database (V5-19, 2026-09-27)

As for V5-15, the worktree has no dev database and the package rules keep the live one out of reach,
so the rehearsal ran on a scratch copy of `api/tests/fixtures/v1_seed.sqlite` in the session
scratchpad with the alembic CLI: `upgrade head` ran every revision from the v1 head through
`v5_009_consent` to `v5_002_session_uploads`; `alembic current` = `v5_002_session_uploads (head)`;
`downgrade v5_009_consent` dropped the table; `upgrade head` again; `alembic check` = "No new upgrade
operations detected".

### Tests

- `api/tests/test_session_assets.py::test_v5_002_upgrades_downgrades_and_upgrades_again` (SQLite): up,
  down, up on a copy of the v1 seed; the table exists only when it should.
- `api/tests/test_migrations.py` (existing): a fresh `upgrade head` matches the models
  (`SessionAsset`), no drift; `test_health.py`: the head id keeps the `v5_` prefix.

**Postgres: not executed** (no Postgres here). The revision uses only `op.create_table` /
`op.create_index` / `op.drop_*` with portable types (`sa.JSON`, `sa.DateTime`); the CI `test-postgres`
job's up/down/up step runs it.

### To apply (coordinator)

```
sqlite3 api/data/lkap.db ".backup <scratchpad>/lkap-before-v5_004.db"
cd api && uv run alembic upgrade head      # v5_003_pgvector -> v5_004_mcp_oauth
```

sqlite3 api/data/lkap.db ".backup <scratchpad>/lkap-before-v5_002.db"
cd api && uv run alembic upgrade head      # v5_009_consent -> v5_002_session_uploads
```

Instant on the dev database. **Order:** backup → `upgrade head` → restart the api → restart the
worker. The api mounts the new routes and starts the retention loop at boot. An api on this code
against an unmigrated database fails only on the new routes and in that loop (logged, retried), not
on existing ones. An old worker never posts files, so the order between the two restarts does not
matter otherwise.

## `v5_006_agent_tests` (V5-29)

### What the revision does

Creates `agent_test_runs` (`id`, `workspace_id` → `workspaces` ON DELETE CASCADE, `agent_id` →
`agents` ON DELETE CASCADE, `config_version`, `status` with the CHECK
`status IN ('queued','running','passed','failed','inconclusive','error')`, `created_by`,
`created_at`, `started_at`, `finished_at`, `summary` JSON, `error`) and `agent_test_results`
(`id`, `run_id` → `agent_test_runs` ON DELETE CASCADE, `workspace_id`, `case_id`, `ordinal`,
`session_id` → `sessions` ON DELETE SET NULL, `status` with its CHECK, `mocks` JSON, `verdict` JSON,
`created_at`, `finished_at`), with indexes `ix_agent_test_runs_agent (agent_id, created_at)`,
`ix_agent_test_runs_workspace`, `ix_agent_test_results_run (run_id, ordinal)`,
`ix_agent_test_results_session`, `ix_agent_test_results_workspace`. New tables only. Nothing
existing is touched. `down_revision = v5_002_session_uploads` (the head when V5-29 started).
Ledger deviation: the ledger names `agent_tests` and `agent_test_runs`. The card rules the test
cases live in the agent config (no cases table), so the second table holds per-case **results**
(`agent_test_results`), named so it cannot be mistaken for a cases table. Both are tenant tables
(`db/guard.py::TENANT_TABLES`).

### Rehearsal on a scratch database (V5-29, 2026-09-27)

A fresh SQLite file in the session scratchpad (the live database was not read or copied), alembic
CLI with `-x url=`: `heads` = `v5_006_agent_tests (head)`. `upgrade head` ran every revision to
`v5_006_agent_tests`, both tables present, `downgrade v5_002_session_uploads` dropped both, `upgrade
head` again. `alembic check` = "No new upgrade operations detected", `alembic_version` =
`v5_006_agent_tests`.

### Tests

- `api/tests/test_agent_tests.py::test_v5_006_upgrades_downgrades_and_upgrades` (SQLite): up, down,
  up. The tables exist only when they should.
- `api/tests/test_migrations.py` (existing): a fresh `upgrade head` matches the models
  (`AgentTestRun`, `AgentTestResult`), no drift.

**Postgres: not executed** (no Postgres here). Only `op.create_table` / `op.create_index` /
`op.drop_*` with portable types. The CI `test-postgres` job's up/down/up step runs it.

## `v5_005_knowledge_connections` (V5-20)

Rehearsed 2026-09-27 by V5-20 on **scratch SQLite databases only**. **Not applied** to the dev database, and the dev database was not read or copied (the package brief forbids touching `api/data/lkap.db`). The coordinator rehearses on its own `.backup` copy before applying.

**Chain.** Re-chained at merge to `down_revision = "v5_006_agent_tests"` (V5-29 landed first, applied to the dev database on 2026-09-27 after a backup). Originally `down_revision = "v5_002_session_uploads"` (the head when V5-20 started, ledger numbers are not chain order).

### What the revision does

| Object | Change |
|---|---|
| `knowledge_connections` (new) | `id PK, workspace_id → workspaces ON DELETE CASCADE, name, kind VARCHAR(32) (no CHECK: validated by the contracts literal, so V5-45's `ragie` needs no rebuild), settings JSON, credential_id → credentials ON DELETE SET NULL, status CHECK (unverified|ok|error), last_checked_at, last_error, capabilities JSON, created_at, updated_at` + `ix_knowledge_connections_workspace`. |
| `knowledge_bases` | `+ connection_id VARCHAR(32) NULL → knowledge_connections (no ondelete: a connection with knowledge bases is refused with 409 first)`, `+ kind VARCHAR(16) NOT NULL DEFAULT 'managed'`, `+ external_ref VARCHAR(512) NULL`. On SQLite the foreign key rebuilds the table through `batch_alter_table` (the `v2_004` precedent, the migration connection does not enable `PRAGMA foreign_keys`, so dropping the old copy cascades nothing). Existing rows read as `connection_id NULL, kind 'managed'` = the platform's store, unchanged. |

**Downgrade** drops the foreign key and the three columns (a batch rebuild on SQLite) and then the table. A knowledge base that was stored through a connection falls back to the platform's store (re-index it afterwards).

### Rehearsal (scratch SQLite)

Fresh database: `upgrade head` → `downgrade v5_002_session_uploads` → `upgrade head` → `alembic check` = "No new upgrade operations detected.". `PRAGMA integrity_check` = ok, `foreign_key_check` = no rows.

Seeded database (`upgrade v5_002_session_uploads`, then one workspace, two knowledge bases, one with a recorded 384-wide embedder, one legacy with NULLs, two documents, three chunks, FTS rows by the `v5_001` triggers):

```
before:         2 kbs, 2 docs, 3 chunks, 3 fts
after up:       2 kbs, 2 docs, 3 chunks, 3 fts
  kb1|NULL|managed|NULL|384      kb2|NULL|managed|NULL|NULL
  fts MATCH 'text:flood' -> 1
check:          No new upgrade operations detected.
after down:     2 kbs, 2 docs, 3 chunks, 3 fts   (columns back to the v5_002 set)
after up again: 2 kbs, 2 docs, 3 chunks, 3 fts
integrity: ok   fk_violations: 0
```

**Postgres: rendered, not executed** (no Postgres here). `alembic upgrade v5_002_session_uploads:v5_005_knowledge_connections --sql` against a Postgres url emits `CREATE TABLE knowledge_connections (…)`, the index, three `ALTER TABLE knowledge_bases ADD COLUMN` and one `ADD CONSTRAINT … FOREIGN KEY`, no rebuild. The CI `test-postgres` job's up/down/up step runs it.

### To apply (coordinator)

```
sqlite3 api/data/lkap.db ".backup <scratchpad>/lkap-before-v5_006.db"
cd api && uv run alembic upgrade head      # v5_002_session_uploads -> v5_006_agent_tests
```

Instant on the dev database. **Order:** backup → `upgrade head` → restart the api (mounts
`/v1/agents/{id}/tests/*`, registers the `agent_tests_run` job). The worker needs a restart only for
the `tool_mocks` branch, and only once asks #190 (`internal.py`) and #191 (`main.py`) are applied. An api on this code
against an unmigrated database fails on the new routes and on **publishing an agent whose
`publish_gate.require_tests` is on** (no agent has it on before this package), nowhere else.

sqlite3 api/data/lkap.db ".backup <scratchpad>/lkap-before-v5_005.db"
cd api && uv run alembic upgrade head      # v5_002_session_uploads -> v5_005_knowledge_connections
```

Instant on the dev database (a small table rebuild of `knowledge_bases`). **Order:** backup → `upgrade head` → restart the api (it mounts `/v1/knowledge-connections` and reads the new columns; an api on this code against an unmigrated database fails on every knowledge-base query). The worker needs no restart for V5-20.


## v5_007_telephony_amd (V5-32)

Rehearsed on a scratch copy of `api/tests/fixtures/v1_seed.sqlite` in the session scratchpad only
(`api/data/lkap.db` was not read or touched), chained after `v5_005_knowledge_connections` (the head
when V5-32 started; `alembic heads` → `v5_005_knowledge_connections`).

```
upgrade head:                    ... -> v5_005_knowledge_connections -> v5_007_telephony_amd
downgrade v5_005_knowledge_connections: v5_007_telephony_amd -> v5_005_knowledge_connections
upgrade head:                    v5_005_knowledge_connections -> v5_007_telephony_amd
check:                           No new upgrade operations detected.
```

Three nullable `ADD COLUMN`s on `calls` (`amd_result VARCHAR(32)`, `transfer_mode VARCHAR(8)`,
`transfer_summary TEXT`); no rebuild, `status_valid` / `direction_valid` untouched (the downgrade uses
SQLite's native `DROP COLUMN`, and `tests/test_telephony_amd.py` pins that the CHECKs survive
up/down/up). **Postgres: rendered, not executed**: `upgrade v5_005_knowledge_connections:v5_007_telephony_amd
--sql` emits exactly the three `ALTER TABLE calls ADD COLUMN`, the downgrade the three `DROP COLUMN`.

### To apply (coordinator)

```
sqlite3 api/data/lkap.db ".backup <scratchpad>/lkap-before-v5_007.db"
cd api && uv run alembic upgrade head      # v5_005_knowledge_connections -> v5_007_telephony_amd
```

Instant. **Order:** backup → `upgrade head` → restart the api (it reads and writes the new `calls`
columns on every call list, report and `/resolved` of a phone session; an api on this code against an
unmigrated database fails on those) → restart the worker (the AMD start path, `transfer_call`'s `summary`
and the warm path). Nothing else changes for existing agents: `amd` is off and every target is `cold`.

## v5_008_memory (V5-40)

Rehearsed on a scratch copy of `api/tests/fixtures/v1_seed.sqlite` in the session scratchpad only
(`api/data/lkap.db` was not read or touched), chained after `v5_007_telephony_amd` (`alembic heads` →
`v5_007_telephony_amd` when V5-40 started; V5-37 may add one in parallel, the coordinator re-chains).

```
upgrade head:                    ... -> v5_007_telephony_amd -> v5_008_memory
downgrade v5_007_telephony_amd:  v5_008_memory -> v5_007_telephony_amd
upgrade head:                    v5_007_telephony_amd -> v5_008_memory
check:                           No new upgrade operations detected.
```

Two new tables (`memory_subjects` with a unique `(workspace_id, subject_id, scope_key)` and an index on
`retention_until`; `memory_events` with a `kind` CHECK and two indexes); nothing existing is altered.
`tests/test_memory.py::test_v5_008_upgrade_downgrade_upgrade` pins up/down/up. **Postgres: rendered,
not executed** (`upgrade v5_007_telephony_amd:v5_008_memory --sql`: two `CREATE TABLE`, three
`CREATE INDEX`).

### To apply (coordinator)

```
sqlite3 api/data/lkap.db ".backup <scratchpad>/lkap-before-v5_008.db"
cd api && uv run alembic upgrade head      # v5_007_telephony_amd -> v5_008_memory
```

Instant. **Order:** backup → `upgrade head` → restart the api (the summary route reads the new
tables for memory-enabled agents, and `test_db_guard` lists them as tenant tables) → restart the worker
(the recall client). Nothing changes for existing agents: memory is off.
