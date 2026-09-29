# v6 migration rehearsals

PLAN-V6 §0.1 / §0.3: every v6 migration is rehearsed `upgrade head → downgrade <previous> → upgrade
head` before the coordinator applies it to the dev database (after a backup). Packages append one
section each.

## `v6_002_datasets` (V6-16)

**Chain.** `… → v5_007_telephony_amd → v5_008_memory → v6_002_datasets` (`down_revision =
"v5_008_memory"`; `uv run alembic heads` in `api/` showed `v5_008_memory` as the only head before this
package, and `v6_002_datasets` after it). `v6_001_inference_credentials` (V6-05) is not on `main` yet: if
it lands first and also chains after `v5_008_memory`, there are two heads and one of the two must be
re-pointed at merge (see `_asks.md` #102).

**What it does.** Creates `datasets`, `dataset_rows`, `dataset_keys` (with `ix_datasets_workspace`,
`uq_datasets_workspace_slug`, `ix_dataset_rows_dataset_ordinal`, `ix_dataset_keys_lookup`) and widens
the `tools.kind` CHECK to `('http','mcp','provider','dataset')` with a `batch_alter_table` rebuild of
`tools` on SQLite (`copy_from` spells out the table as `v5_010` left it; nothing since changed it).
The downgrade deletes `kind='dataset'` tool rows, drops the three tables and restores the narrower
CHECK.

**Rehearsed (2026-09-28), SQLite, scratch copy of `api/tests/fixtures/v1_seed.sqlite`** (never
`api/data/lkap.db`):

| Step | Result |
|---|---|
| `alembic upgrade head` (from the v1 seed, every revision) | ok; `current` = `v6_002_datasets (head)` |
| `alembic downgrade v5_008_memory` | ok; `current` = `v5_008_memory` |
| `alembic upgrade head` | ok; `current` = `v6_002_datasets (head)` |
| `alembic check` | "No new upgrade operations detected." (models and migration agree) |

`api/tests/test_migration_v6_002.py` repeats the rehearsal in the suite: the tables and indexes exist,
`tools.kind` accepts `dataset` and still refuses anything else, the constraint keeps its name
`ck_tools_kind_valid`, rows and keys cascade from their dataset, the slug is unique per workspace,
the downgrade removes the tables and the dataset tool rows and restores the narrow CHECK, and a second
upgrade reaches head again with `ix_tools_workspace` intact. `test_migrations.py`
(`upgrade head` matches the models exactly) passes with it.

**Not rehearsed here.** Postgres (no server in this worktree; the CI `test-postgres` job runs the
suite, including `test_migrations.py`, against it) and a copy of the dev database (the package never
touches `api/data/lkap.db`).

**Applying it (coordinator).** Stop nothing else first; with the api stopped or idle:

1. `sqlite3 api/data/lkap.db ".backup <scratchpad>/lkap-before-v6_002.db"`
2. Rehearse on a copy of that backup: `LKAP_DATABASE_URL=sqlite+aiosqlite:///<copy> uv run alembic upgrade head`,
   then `downgrade v5_008_memory` (or the head it chains after), then `upgrade head`, then `alembic check`.
3. `cd api && uv run alembic upgrade head` on the dev database; `uv run alembic current` shows
   `v6_002_datasets (head)`.
4. Restart the api (new routes and the `dataset_import` job) and the worker (the `dataset` tool kind).

## `v6_003_credential_last_used` (V6-32)

**Chain.** `… → v5_008_memory → v6_002_datasets → v6_003_credential_last_used` (`down_revision =
"v6_002_datasets"`; `uv run alembic heads` in `api/` shows `v6_003_credential_last_used` as the only head).
If `v6_001_inference_credentials` is ever built (ask #102) it re-points after this revision.

**What it does.** Adds a nullable `credentials.last_used_at` (`DateTime`, `ADD COLUMN` on both dialects; no
rebuild). The downgrade drops it (SQLite: native `ALTER TABLE … DROP COLUMN`, as `v5_009`), losing only the
"last used" times.

**Rehearsed (2026-09-30), SQLite, scratch copy of `api/tests/fixtures/v1_seed.sqlite`** (never
`api/data/lkap.db`):

| Step | Result |
|---|---|
| `alembic upgrade head` (from the v1 seed, every revision) | ok; `current` = `v6_003_credential_last_used (head)` |
| `alembic downgrade v6_002_datasets` | ok; `current` = `v6_002_datasets` |
| `alembic upgrade head` | ok; `current` = `v6_003_credential_last_used (head)` |
| `alembic check` | "No new upgrade operations detected." (models and migration agree) |

`api/tests/test_migration_v6_003.py` repeats it in the suite (rows and `ix_credentials_workspace` kept, the
column added empty, dropped on downgrade, back on a second upgrade); `test_migrations.py` passes with it.

**Not rehearsed here.** Postgres (the CI `test-postgres` job) and a copy of the dev database (the package never
touches it). **Apply before the api restart**: the model maps the column, so the new api fails every key query
on an un-migrated database.

