# Migration rehearsal (V2-19)

V2-19B ran this on 2026-09-23 against HEAD `53f9295` plus the uncommitted V2-19 tree. The tools were alembic 1.20.0, SQLAlchemy 2.0.54 and sqlite 3.51.0.

**Result:** the chain is healthy. The live file is already at head. Head is still `v2_010_qa_status`, so V2-19 needed **no new migration**. In particular, `agent_config_versions.created_by` (V2-16-9) already exists.

## Method

Every run passed an explicit `-x url=` pointing into the session scratchpad (`…/scratchpad/v219b/mig/`). Without it, `alembic/env.py` falls back to `api/data/lkap.db`.

The live file was only ever read, through `sqlite3 api/data/lkap.db ".backup <scratchpad>/lkap-copy.db"`. Nothing ran against `api/data/lkap.db` itself.

```bash
cd api
COPY="sqlite+aiosqlite:///<scratchpad>/mig/lkap-copy.db"
FRESH="sqlite+aiosqlite:///<scratchpad>/mig/fresh.db"
uv run alembic -x url=$COPY  current                    # v2_010_qa_status (head)
uv run alembic -x url=$COPY  upgrade head               # no-op
uv run alembic -x url=$COPY  downgrade 4135323c6ecc     # 10 steps down to the v1 schema
uv run alembic -x url=$COPY  upgrade head               # 10 steps back up
uv run alembic -x url=$FRESH upgrade head               # base → head
uv run alembic -x url=$FRESH downgrade base             # head → base
uv run alembic -x url=$FRESH upgrade head               # base → head again
```

Every command exited 0. The full log is `…/scratchpad/v219b/mig/rehearsal.log`, not committed because it holds scratchpad paths.

## Copy of the live dev DB

| Step | Revision | Rows |
|---|---|---|
| Before | `v2_010_qa_status` (head) | 10 agents, 29 sessions, 10 config versions |
| `upgrade head` | unchanged (no-op) | unchanged |
| `downgrade 4135323c6ecc` | `4135323c6ecc` | 10 agents, 29 sessions |
| `upgrade head` | `v2_010_qa_status` | 10 agents, 29 sessions, 10 config versions |

A row-by-row comparison against a second `.backup` of the original shows three things.

- **`agents.config` is byte-identical for all 10 agents** after the v2 → v1 → v2 round trip. `v2_008_agentconfig_v2` is lossless for today's configs.
- **`agents.connection_id` is `NULL` for all 10 agents after the round trip.** This is expected and is not a data bug. `downgrade` below `v2_002` drops `livekit_connections`, so the bindings, and any non-default connections, cannot survive. On the next api start, bootstrap re-creates the default connection from `LIVEKIT_*` (`ensure_default_connection`) and binds every unbound agent and session to it (`bind_unbound_rows`). The connection gets a new id. `/v1/health` reports `agents_unbound` until then.
- **One `test` session comes back as `web`.** `v2_005` drops `sessions.channel` on downgrade, and the upgrade backfills `web`.

Both are the documented cost of a full downgrade: v2-only tables and columns are dropped, not archived. **Take a `.backup` before any downgrade** (RUNBOOK §"Backups").

## Fresh database

`base → head → base → head` runs every revision in both directions on an empty file and ends at `v2_010_qa_status`. `api/tests/test_migrations*.py` asserts the same chain against the models (`test_upgrade_head_matches_the_models_exactly`, `test_upgrade_head_on_a_v1_database_keeps_every_row`). Both files pass in the api suite.

## Postgres

This was not run locally: Docker is not running on the dev host, and Postgres has never been run here. CI covers it in `.github/workflows/python.yml`, job `test-postgres`, which uses a `postgres:16` service with `LKAP_DATABASE_URL=postgresql+asyncpg://…/lkap_test`. The job:

1. runs `alembic upgrade head`, `downgrade 4135323c6ecc`, `upgrade head`, then `downgrade base`;
2. runs the whole api suite against Postgres (`LKAP_TEST_DATABASE_URL`, read by `tests/conftest.py::postgres_url`).

The V2-01 stub `api-postgres.yml` is gone (asks #18), folded into that job.

The workflow runs once the repo has a GitHub remote (HANDOFF "Waiting on the user" #4). Until then, the Postgres leg is unverified.

## For the coordinator

- Nothing to apply: `api/data/lkap.db` is at head.
- Before any future `alembic` run on the live file, back it up first: `sqlite3 api/data/lkap.db ".backup <path outside api/data>"`. SQLite DDL is non-transactional.
