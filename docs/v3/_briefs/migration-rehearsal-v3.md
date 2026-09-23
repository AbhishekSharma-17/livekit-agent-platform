# Migration rehearsal: `v3_001_agent_keys` (V3-00)

Rehearsed 2026-09-24 by V3-00. **Not applied to `api/data/lkap.db`**: the coordinator applies it (HANDOFF rule 4, R-V3-10). The live DB was only read, by `.backup` and one `-readonly` query, and it is still at `v2_011_recording_error`.

## What the revision does

`api/alembic/versions/v3_001_agent_keys.py`, down_revision `v2_011_recording_error`:
- `api_keys += kind VARCHAR(16) NOT NULL DEFAULT 'standard'`, with `CONSTRAINT ck_api_keys_kind_valid CHECK (kind IN ('standard','agent'))`;
- `client VARCHAR(64) NULL`;
- `last_client VARCHAR(64) NULL`.

On SQLite, `op.batch_alter_table` runs with `copy_from` (the v2_001 shape, spelled out as in v2_010), so the rebuild keeps `pk_api_keys`, `fk_api_keys_workspace_id_workspaces`, `uq_api_keys_key_hash` and `ix_api_keys_workspace`. `downgrade()` drops the CHECK and the three columns. Existing keys become `kind='standard'`.

## Procedure (scratchpad `…/scratchpad/v3_00/`)

```
sqlite3 api/data/lkap.db ".backup <scratchpad>/v3_00/lkap.backup.db"
cp lkap.backup.db rehearsal-copy.db
rehearse.sh rehearsal-copy.db     # current → upgrade head → downgrade v2_011_recording_error → upgrade head → alembic check
# fresh: alembic upgrade v2_011_recording_error on an empty file, seed one workspace + one key, then rehearse.sh fresh.db
```

`rehearse.sh` refuses any path ending in `api/data/lkap.db`. It runs `uv run alembic -x url=sqlite+aiosqlite:///<file>` with `LIVEKIT_*`, `LKAP_MASTER_KEY`, `LKAP_DATABASE_URL` and `LKAP_DATA_DIR` unset, so alembic can never fall back to the default data dir. After each step it records:
- the version;
- `api_keys` columns, indexes and DDL;
- the row count of every table;
- a hash of every pre-existing `api_keys` column of every row;
- `PRAGMA integrity_check` and `PRAGMA foreign_key_check`.

Logs: `rehearsal-copy.log` and `rehearsal-fresh.log` next to the script.

## Results

### Copy of the live DB (`lkap.backup.db`, 1.5 MB, 1 API key, 493 audit rows, 31 sessions)

| Step | Version | `api_keys` columns | Rows / hash | Integrity |
|---|---|---|---|---|
| before | `v2_011_recording_error` | 11 (v2) | 1 key, `7b3ab1db8554`; every table count recorded | ok, 0 FK violations |
| upgrade head | `v3_001_agent_keys` | 14 (+ `kind, client, last_client`), CHECK present, `ix_api_keys_workspace` kept | identical counts and hash; `kind='standard'` × 1 | ok, 0 |
| downgrade `v2_011_recording_error` | `v2_011_recording_error` | 11, DDL identical to before (bar the quoted table name the rebuild writes) | identical | ok, 0 |
| upgrade head again | `v3_001_agent_keys` | 14 | identical | ok, 0 |
| `alembic check` | — | — | "No new upgrade operations detected" (no model drift) | — |

### Fresh database (every migration from empty, plus one seeded key)
The same table with hash `30d861d6d10a`. All four steps pass, and `alembic check` is clean. `tests/test_migrations.py` also builds head from an empty file, checks idempotence and runs `alembic check`. `tests/test_agent_access_v3.py::test_migration_v3_001_upgrade_downgrade_upgrade_keeps_keys` runs up → down → up with a seeded key on every test run: the default is `standard`, the CHECK rejects `robot`, and an agent row survives downgrade as a plain key.

### Postgres
Not rehearsed locally: Docker is not running and Postgres has never run locally (HANDOFF). The batch runs as plain `ALTER TABLE ADD COLUMN … DEFAULT 'standard' NOT NULL` plus `ADD CONSTRAINT`, and it is covered by the `test-postgres` job in `.github/workflows/python.yml` (the migration chain plus the full api suite against `postgres:16`) once the repo has a remote.

## For the coordinator

1. Take the backup: `sqlite3 api/data/lkap.db ".backup <scratchpad>/lkap.pre-v3_001.db"`.
2. Migrate: `cd api && uv run alembic upgrade head`, or `-x url=…` for the live file.
3. Expect one `Running upgrade v2_011_recording_error -> v3_001_agent_keys` line.
4. Afterwards, `GET /v1/health` reports the DB at head.

**Apply it right after the wave-0 commit.** The user's api on :8080 runs with `--reload`, so it has already loaded the new `ApiKey` columns. Until the migration runs, every query that selects from `api_keys` fails with `no such column: api_keys.kind`. That means:
- every `Authorization: Bearer lkap_…` request returns 500;
- the console's API keys list returns 500.

Cookie sessions and the admin token are unaffected: they never query `api_keys`. This is expected and is not fixed by V3-00.
