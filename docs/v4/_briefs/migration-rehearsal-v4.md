# Migration rehearsal: `v4_001_livekit_numbers` (V4-05)

Rehearsed 2026-09-25 by V4-05. **Not applied to `api/data/lkap.db`**: the coordinator applies it (HANDOFF rule 4, R-V4-11). V4-05 only read the live DB, with one `.backup` and one `-readonly` query. It is still at `v3_001_agent_keys`.

## What the revision does

`api/alembic/versions/v4_001_livekit_numbers.py`, down_revision `v3_001_agent_keys` (PHONE-NUMBERS.md §4.1).

`phone_numbers` gains these columns:

| Column | Type and default |
|---|---|
| `source` | `VARCHAR(16) NOT NULL DEFAULT 'trunk'`, with `ck_phone_numbers_source_valid CHECK (source IN ('trunk','livekit'))` |
| `connection_id` | `VARCHAR(32) NULL`, FK to `livekit_connections` with `ON DELETE CASCADE` |
| `lk_number_id` | `VARCHAR(128) NULL`, with `uq_phone_numbers_workspace_lk_number UNIQUE (workspace_id, lk_number_id)` |
| `lk_status`, `lk_inbound_status` | `VARCHAR(16) NULL` |
| `lk_rule_ids` | `JSON NOT NULL DEFAULT '[]'` |
| `region` | `VARCHAR(200) NOT NULL DEFAULT ''` |
| `lk_synced_at` | `DATETIME NULL` |

`sip_dispatch_rules` changes:
- `trunk_id` becomes nullable.
- It gains `phone_number_id VARCHAR(32) NULL`, FK to `phone_numbers` with `ON DELETE SET NULL`.

The backfill runs in Python. A rule gets its number's id when:
- its `numbers` is exactly `[e164]` of a number;
- it has the same workspace and trunk as that number;
- its agent is that number's `inbound_agent_id`.

The downgrade deletes, and logs how many, every rule with `trunk_id IS NULL` and every `source='livekit'` number row. Then it drops the new columns and restores `trunk_id NOT NULL`.

On SQLite both tables are rebuilt through `batch_alter_table(copy_from=…)`, with the v2_007 shape spelled out, as in v3_001. Every constraint name and index is kept.

## Procedure (scratchpad `…/scratchpad/v405/`)

```
sqlite3 api/data/lkap.db ".backup <scratchpad>/v405/lkap.backup.db"
cp lkap.backup.db rehearsal-copy.db;   rehearse.sh rehearsal-copy.db   > rehearsal-copy.log
cp lkap.backup.db rehearsal-seeded.db; <seed a trunk, 2 trunk numbers, 1 managed + 1 catch-all rule at v3_001>
SEED_V4=<1 livekit number + its trunk-less rule, inserted after the upgrade> rehearse.sh rehearsal-seeded.db > rehearsal-seeded.log
```

`rehearse.sh` refuses any path that ends in `api/data/lkap.db`. It runs `uv run alembic -x url=sqlite+aiosqlite:///<file>` with `LIVEKIT_*`, `LKAP_MASTER_KEY`, `LKAP_DATABASE_URL` and `LKAP_DATA_DIR` unset. It runs the chain current → `upgrade head` → `downgrade v3_001_agent_keys` → `upgrade head` → `alembic check`.

After each step it records:
- the version;
- for both tables, columns with NOT NULL flags, indexes and DDL;
- the row count of every table;
- a hash of every pre-existing column of both tables;
- `PRAGMA integrity_check` and `foreign_key_check`.

## Results

### Copy of the live DB (0 trunks, 0 numbers, 0 rules; 31 sessions, 1010 audit rows)

| Step | Version | Shape | Rows / hash | Integrity |
|---|---|---|---|---|
| before | `v3_001_agent_keys` | v2_007 shape | every table count recorded | ok, 0 FK violations |
| upgrade head | `v4_001_livekit_numbers` | `phone_numbers` has 14 columns; `sip_dispatch_rules.trunk_id` nullable, `+phone_number_id`; CHECK, unique and FKs present; `ix_*_workspace` kept | identical counts; "linked 0 managed rules" | ok, 0 |
| downgrade `v3_001_agent_keys` | `v3_001_agent_keys` | back to 6 and 10 columns; `trunk_id NOT NULL` | identical; "deleted 0 … / 0 …" | ok, 0 |
| upgrade head (again) | `v4_001_livekit_numbers` | as after the first upgrade | identical | ok, 0 |
| `alembic check` | | "No new upgrade operations detected." (the models match) | | |

### Seeded copy: backfill and destructive downgrade

| Step | Result |
|---|---|
| upgrade head | "linked 1 existing managed dispatch rule(s)". The managed rule points at its number. The catch-all rule (`numbers=[]`) stays unlinked. Both numbers are `source='trunk'`. Pre-existing column hashes are unchanged (`cbcc5020b78c` / `d51989e9ce42`). |
| seed v4 rows | 1 livekit number and 1 trunk-less rule. |
| downgrade | "deleted 1 dispatch rule(s) without a trunk" and "deleted 1 LiveKit-hosted number row(s)". The trunk rows and their hashes are identical to before. Integrity ok, 0 FK violations. |
| upgrade head, then check | Linked again. Clean. |

Post-upgrade DDL (from `rehearsal-copy.log`):

```
CREATE TABLE "phone_numbers" ( id VARCHAR(32) NOT NULL, workspace_id VARCHAR(32) NOT NULL, e164 VARCHAR(32) NOT NULL, trunk_id VARCHAR(32), inbound_agent_id VARCHAR(32), label VARCHAR(200) DEFAULT '' NOT NULL, source VARCHAR(16) DEFAULT 'trunk' NOT NULL, connection_id VARCHAR(32), lk_number_id VARCHAR(128), lk_status VARCHAR(16), lk_inbound_status VARCHAR(16), lk_rule_ids JSON DEFAULT '[]' NOT NULL, region VARCHAR(200) DEFAULT '' NOT NULL, lk_synced_at DATETIME, CONSTRAINT pk_phone_numbers PRIMARY KEY (id), … CONSTRAINT uq_phone_numbers_e164 UNIQUE (e164), CONSTRAINT ck_phone_numbers_source_valid CHECK (source IN ('trunk','livekit')), CONSTRAINT fk_phone_numbers_connection_id_livekit_connections FOREIGN KEY(connection_id) REFERENCES livekit_connections (id) ON DELETE CASCADE, CONSTRAINT uq_phone_numbers_workspace_lk_number UNIQUE (workspace_id, lk_number_id) )
CREATE TABLE "sip_dispatch_rules" ( …, trunk_id VARCHAR(32), …, phone_number_id VARCHAR(32), …, CONSTRAINT fk_sip_dispatch_rules_phone_number_id_phone_numbers FOREIGN KEY(phone_number_id) REFERENCES phone_numbers (id) ON DELETE SET NULL )
```

The same path is covered in the suite by `api/tests/test_phone_numbers.py::test_migration_backfills_managed_rules_and_downgrade_drops_hosted_rows`, which runs on SQLite: seed at v3, upgrade, backfill, downgrade deletion, upgrade again. `tests/test_migrations.py::test_upgrade_head_matches_the_models_exactly` also passes.

**Postgres:** not rehearsed locally, because Docker is not running on this machine. The Postgres CI job runs the migration tests. The batches are plain `ALTER TABLE` statements there. The backfill uses only `SELECT` and `UPDATE` through SQLAlchemy Core, and the downgrade `DELETE`s are plain SQL.

## For the coordinator: when to apply

**Apply it now.** The model change is already live in the running dev api (`--reload`). Until the DB is at `v4_001_livekit_numbers`, these break:
- `GET/PUT/DELETE /v1/telephony/numbers` and `GET/DELETE /v1/telephony/dispatch-rules` answer 500, because they read the new columns. So do the console's Numbers and Rules sections and the MCP `telephony_overview` `numbers`/`dispatch_rules` keys; they degrade to warnings.
- A SIP `participant_joined` webhook for a leg that is not on one of our trunks would fail in `_is_our_leg`.
- `/v1/health` reports `db: "error"`, because the schema is not at head.

Trunks, calls, sessions and everything outside telephony are unaffected.

```
sqlite3 api/data/lkap.db ".backup <scratchpad>/lkap-before-v4_001-<ts>.db"
cd api && uv run alembic upgrade head        # → v4_001_livekit_numbers
sqlite3 -readonly data/lkap.db "SELECT version_num FROM alembic_version"
```

---

# Migration rehearsal: `v4_002_provider_models` (V4-07)

Rehearsed 2026-09-25 by V4-07. **Not applied to `api/data/lkap.db`**: the coordinator applies it (HANDOFF rule 4, R-V4-24). V4-07 only read the live DB with one `.backup`. The backup read `alembic_version = v4_001_livekit_numbers`, so the coordinator already applied `v4_001`.

## What the revision does

`api/alembic/versions/v4_002_provider_models.py`, down_revision `v4_001_livekit_numbers` (CUSTOM-MODELS.md D-V4-24). It adds **one new table** and touches nothing else:

| Column | Type |
|---|---|
| `id` | `VARCHAR(32)` PK (`pk_provider_models`) |
| `workspace_id` | `VARCHAR(32) NOT NULL`, FK `workspaces.id` `ON DELETE CASCADE` |
| `provider_id`, `provider_home` | `VARCHAR(64) NOT NULL` |
| `kind` | `VARCHAR(16) NOT NULL` |
| `model_id` | `VARCHAR(200) NOT NULL` |
| `declared`, `detected` | `JSON NULL` (`ModelCapabilities`) |
| `last_test_at` | `DATETIME NULL` |
| `last_test_ok` | `BOOLEAN NULL` |
| `last_test_message` | `VARCHAR(500) NULL` (scrubbed) |
| `last_test_latency_ms` | `INTEGER NULL` |
| `last_test_cost_usd` | `NUMERIC(12, 6) NULL` |
| `last_test_credential_id` | `VARCHAR(32) NULL` (no FK: deleting a key must not touch the record) |
| `last_test_fingerprint` | `VARCHAR(32) NULL` |
| `catalog_seen_at`, `catalog_missing_since` | `DATETIME NULL` |
| `created_at`, `updated_at` | `DATETIME NOT NULL` |

Also `uq_provider_models_workspace_home_kind_model UNIQUE (workspace_id, provider_home, kind, model_id)` and `ix_provider_models_workspace (workspace_id)`. It is a plain `CREATE TABLE`, with no `batch_alter_table`, so SQLite and Postgres run the same DDL. The downgrade drops the index and the table, and its rows go with it. Nothing references the table.

## Procedure (scratchpad `…/scratchpad/v407/`)

```
sqlite3 api/data/lkap.db ".backup <scratchpad>/v407/lkap.backup.db"
cp lkap.backup.db rehearsal-copy.db;  rehearse.sh rehearsal-copy.db <worktree>/api
seeded.sh <scratchpad>/v407 <worktree>/api      # its own copy: rehearsal-seeded.db
```

`rehearse.sh` refuses any path that ends in `api/data/lkap.db`. It unsets `LKAP_DATABASE_URL`, `LKAP_DATA_DIR`, `LKAP_MASTER_KEY` and `LIVEKIT_*`, and runs `uv run alembic -x url=sqlite+aiosqlite:///<copy>` from the worktree's `api/`. The chain is `upgrade head` → `downgrade v4_001_livekit_numbers` → `upgrade head` → `alembic check`. After each step it records the version, the `provider_models` DDL, the row count of every table, `PRAGMA integrity_check` and `foreign_key_check`.

## Results

### Copy of the live DB (35 sessions, 1042 audit rows, 12 agents, 2 credentials, 1 catalog cache row)

| Step | Version | Shape | Rows | Integrity |
|---|---|---|---|---|
| before | `v4_001_livekit_numbers` | no `provider_models` | every table count recorded | ok, 0 FK violations |
| upgrade head | `v4_002_provider_models` | `provider_models`: 19 columns, PK, FK (cascade), unique, `ix_provider_models_workspace` | every pre-existing count identical; `provider_models=0` | ok, 0 |
| downgrade `v4_001_livekit_numbers` | `v4_001_livekit_numbers` | table and index gone | identical to "before" | ok, 0 |
| upgrade head (again) | `v4_002_provider_models` | as after the first upgrade | identical | ok, 0 |
| `alembic check` | | "No new upgrade operations detected." (the models match) | | |

### Seeded copy

| Step | Result |
|---|---|
| upgrade head | Clean. |
| seed | Two rows with the same `(workspace, openrouter-llm, model_id)` and kinds `stt` and `llm`: both accepted. The same `(workspace, home, stt, model_id)` a second time is refused by `uq_provider_models_workspace_home_kind_model`. |
| downgrade | The table is gone, and its 2 rows with it. The other tables are unchanged. |
| upgrade head, then check | `provider_models` is back, empty. Integrity ok, 0 FK violations, and `alembic check` is clean. |

Post-upgrade DDL (from the rehearsal output):

```
CREATE TABLE provider_models ( id VARCHAR(32) NOT NULL, workspace_id VARCHAR(32) NOT NULL, provider_id VARCHAR(64) NOT NULL, provider_home VARCHAR(64) NOT NULL, kind VARCHAR(16) NOT NULL, model_id VARCHAR(200) NOT NULL, declared JSON, detected JSON, last_test_at DATETIME, last_test_ok BOOLEAN, last_test_message VARCHAR(500), last_test_latency_ms INTEGER, last_test_cost_usd NUMERIC(12, 6), last_test_credential_id VARCHAR(32), last_test_fingerprint VARCHAR(32), catalog_seen_at DATETIME, catalog_missing_since DATETIME, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, CONSTRAINT pk_provider_models PRIMARY KEY (id), CONSTRAINT fk_provider_models_workspace_id_workspaces FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, CONSTRAINT uq_provider_models_workspace_home_kind_model UNIQUE (workspace_id, provider_home, kind, model_id) )
CREATE INDEX ix_provider_models_workspace ON provider_models (workspace_id)
```

In the suite: `tests/test_migrations.py::test_upgrade_head_matches_the_models_exactly` and `tests/test_health.py` pass at the new head.

**Postgres**: not rehearsed locally, because Docker is not running on the dev host. The `test-postgres` job in `python.yml` runs the full api suite, and so every migration, on Postgres once the branch is on CI. The revision is one plain `create_table` plus `create_index`, with no SQLite-specific DDL.

## Coordinator step (after merge)

```
sqlite3 api/data/lkap.db ".backup <scratchpad>/lkap-before-v4_002-<ts>.db"
cd api && uv run alembic upgrade head        # → v4_002_provider_models
sqlite3 -readonly data/lkap.db "SELECT version_num FROM alembic_version"
```

The running api (`--reload`) loads the new code as soon as the merge lands, but the `provider_models` table does not exist until this step. Until then, `/v1/health` reports `db: error` (it compares against the migration head). Validation and the catalog route query `provider_models` too, so apply the migration right after merging.
