# LKAP — Runbook (v2)

This runbook covers running the LiveKit Agent Platform, operating it (sign-in, LiveKit connections, worker pools, images, backups, key rotation) and testing it by hand.

- Sections 1–11 are v2, written by V2-19 on 2026-09-23.
- Sections 12–19 are the v1 runbook, from live runs on 2026-09-18 and 2026-09-19. They still hold, except where a v2 section replaces them.

**Binding references:**
- **v2:** `docs/v2/README.md`, which gives the precedence order: ARCHITECTURE-V2, CONTRACTS-V2, PLAN-V2 §8 rulings.
- **v1:** `DECISIONS-W2.md` (wins everywhere) → `CONTRACTS.md` → `ARCHITECTURE.md` → `LIVE_TEST_PLAN.md`.
- **Containers:** `deploy/README.md` §4 has the container details.

> **Shared LiveKit project.** The project also hosts an unrelated deployed agent, **`other-project-agent`**.
> - Never dispatch to it or redeploy it.
> - Never pass its ids or secrets to any `lk agent` command.
>
> The default connection's dispatch name is **`lkap-agent`**. Every connection has its own `agent_name`, and a worker serves exactly one of them. Never run two workers under one name, because dispatch would split between them.

---

## 1. Run locally

Four processes:

| Process | Port |
|---|---|
| api | 8080 |
| web | 3000 |
| worker (or the supervisor that starts workers) | none |

Secrets come from your shell, or from the launch config outside the repo. Never put them in committed files. Never read or write `.env*` files with tools.

| Variable | api | worker | supervisor | web | Dev value / meaning |
|---|---|---|---|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | ✓ | ✓ | | | project credentials. On first start the api creates the **default connection** from them (§3). |
| `LKAP_MASTER_KEY` | ✓ | | | | Fernet key encrypting every `*_ct` column: `cd api && uv run python -m lkap_api.keys generate`. Rotation: §7. |
| `LKAP_ENV` | ✓ | | | | `dev` (default) or `prod`. `prod` refuses weak or `dev-*` static secrets and turns the admin token off (§2). |
| `LKAP_ADMIN_TOKEN` | ✓ | | | ✓ | `dev-admin`, the break-glass token (§2). |
| `LKAP_SERVICE_TOKEN` | ✓ | ✓ | ✓ | | `dev-service`. Must be byte-identical everywhere. |
| `LKAP_PACKS` | ✓ | ✓ | | | `packs.insurance_claim,packs.generic`. **Must match on the api and every worker** (F-17, §4). |
| `LKAP_BOOTSTRAP_OWNER_EMAIL` / `_PASSWORD` | ✓ | | | | the first owner (default `owner@local`); see §2 |
| `LKAP_SESSION_SECRET` | ✓ | | | | signs invite links; unset = derived from the master key. Required in `prod`. |
| `LKAP_ALLOW_ADMIN_TOKEN` | ✓ | | | | unset = on in `dev`, off in `prod` |
| `LKAP_WEB_BASE_URL`, `LKAP_CORS_ORIGINS` | ✓ | | | | invite links, and the origins allowed to call `connect` anonymously |
| `LKAP_PUBLIC_BASE_URL` | ✓ | | | | public `https://` origin of the api, needed for Cloud-hosted pools (§3) |
| `LKAP_API_BASE_URL` | | ✓ | ✓ | | `http://127.0.0.1:8080` |
| `LKAP_CONNECTION_ID` | | optional | | | the connection a worker serves. Unset means the default connection. |
| `LKAP_AGENT_NAME` | | optional | | | must equal the connection's `agent_name` (default `lkap-agent`) |
| `LKAP_INSTANCE_KEY`, `LKAP_MANAGED_BY` | | set by the supervisor | | | leave unset for a hand-started worker |
| `LKAP_WORKER_HTTP_PORT` | | optional | | | port of the SDK's worker HTTP server. Unset keeps the SDK default (ephemeral in `dev`, 8081 in `start`). The supervisor's `subprocess` backend sets `0`, so replicas on one host never collide on 8081 (V2-20). |
| `LKAP_HTTP_TOOL_ALLOWED_HOSTS` | | optional | | | comma list. A tool's hosts must be in it **and** in its own `allowed_hosts`, and private ranges are refused (F-14). |
| `LKAP_VISION_MAX_FRAME_AGE_S`, `LKAP_IDLE_HANGUP_S` | | optional | | | default `8` / `120` |
| `NEXT_PUBLIC_API_BASE_URL` | | | | ✓ | `http://localhost:8080` |
| `LKAP_WEB_ADMIN_BYPASS` | | | | ✓ | unset = on outside `NODE_ENV=production` (§2) |

The full v2 variable list is in CONTRACTS-V2 §6.

### One-time setup

```bash
cd contracts && uv sync && cd .. && scripts/export_contracts.sh --generate
cd api && uv sync && uv run alembic upgrade head && cd ..   # back up api/data/lkap.db first (§9)
cd agent && uv sync && cd ..       # also installs testing/ (shared test fakes) as a dev dependency
cd packs && uv sync && cd ..
cd supervisor && uv sync && cd ..
cd web && pnpm install && cd ..
```

### Start and check

Start each process in order, and check it before starting the next:

```bash
# 1. api
cd api && uv run uvicorn lkap_api.main:app --host 127.0.0.1 --port 8080
curl -s localhost:8080/v1/health   # {"ok":true,"packs":[...],"db":"ok","agents_unbound":0,...}
                                   # db is "ok" only at the migration head

# 2a. one hand-started worker for the default connection
cd agent && uv run python -m lkap_agent.main dev
# 2b. or the supervisor, which starts pools for `supervised` connections (§4)
cd supervisor && uv run python -m lkap_supervisor

# 3. web
cd web && pnpm dev                 # http://localhost:3000/login, then /console
```

`scripts/dev.sh` starts api, worker and web from your shell's env. `LKAP_SUPERVISOR=1 scripts/dev.sh` swaps the worker for the supervisor.

**Before every live run:**
- `ps aux | grep lkap_agent.main | grep -v grep` must show exactly one worker per agent name.
- The connection's **Fleet** card (§4) must show one `ready` instance per replica you expect.
- A connection that shows both `external` and `supervisor` instances is running two pools. Stop one.

Restarting a worker: §12.1 (SIGINT, wait at least 15 s, then SIGKILL only if it is still alive).

---

## 2. Sign-in, the owner password and the escape hatch

- **Users.** The console is behind `/login`: an email and password, then an `lkap_session` cookie. Each user has a role (`viewer`, `builder`, `admin` or `owner`) in each workspace. Invites are `/login?invite=<token>`. API keys (`lkap_…`, scoped) are for machines. All of it is under Settings (`/console/settings`).
- **The owner.** Bootstrap creates `owner@local` (or `LKAP_BOOTSTRAP_OWNER_EMAIL`):
  - With `LKAP_BOOTSTRAP_OWNER_PASSWORD` set, bootstrap uses that password.
  - Without it, bootstrap logs a generated password **once** at WARNING.
- **An owner row with no password.** The dev DB's row predates this (#29). **Only the user sets their own password.** No agent or script runs this:
  ```bash
  cd api && LKAP_MASTER_KEY=… uv run python -m lkap_api.auth set-password --email owner@local   # --force replaces one
  ```
  It takes `LKAP_BOOTSTRAP_OWNER_PASSWORD` when set, otherwise it generates a password and logs it once.
- **The escape hatch (break-glass admin token).**
  - The api accepts `X-Admin-Token: $LKAP_ADMIN_TOKEN` while `LKAP_ALLOW_ADMIN_TOKEN` is on. Unset means on in `LKAP_ENV=dev` and off in `prod`.
  - The web proxy attaches the token while `LKAP_WEB_ADMIN_BYPASS` is on. Unset means on outside `NODE_ENV=production`.
  - The token acts as the `default` workspace's admin, and every write is audited as `actor_id="break-glass"` (`GET /v1/audit`).
  - Use it for scripts, the smoke (§10), and for recovering a locked-out owner.
  - In production, leave it off.
  - **To test real sign-in locally**, set `LKAP_WEB_ADMIN_BYPASS=false` on the web. The api then 401s and the console redirects to `/login`.

## 3. LiveKit connections

A **connection** is one LiveKit server: Cloud or self-hosted, with a URL, an API key and a secret (encrypted), an `agent_name` and a pool mode. Every agent is bound to one (`agents.connection_id`). An unbound agent uses the workspace default.

- **Console.** Go to Connections (`/console/connections`), then **New connection**. **Test** checks the credentials and reads capabilities (Inference, SIP, Egress, the noise-cancellation tier). Enter the secret once; it is never shown again.
  - To change a key or secret, use **Rotate** (`POST /v1/connections/{id}/rotate`). A plain update never takes secrets.
  - **Make default** moves the default. The api's own `LIVEKIT_*` only seed the first default connection.
- **Pool modes** (`deployment_mode`):

  | Mode | Who runs the workers | What you do |
  |---|---|---|
  | `external` | you, by hand or on your own infrastructure | Copy the **worker env** from the connection (`GET /v1/connections/{id}/worker-env?format=env\|compose\|lk`; secrets are placeholders) and start `python -m lkap_agent.main start` with `LKAP_CONNECTION_ID` set. |
  | `supervised` | the supervisor (§4) | Set replicas and the image (`slim`/`full`) on the Fleet card, then **Start**. |
  | `cloud_hosted` | LiveKit Cloud | **Download deploy bundle** (`POST …/deploy-bundle`: `livekit.toml`, `secrets.env` with placeholders, `lk agent` commands). This needs a Cloud connection and a public `https` `LKAP_PUBLIC_BASE_URL`. Then follow §17. |

- **Registration.** Every worker registers with the api (`POST /internal/v1/workers/register`: installed providers, pack ids, image) and heartbeats every 30 s.
  - The api validates provider choices against what that pool actually reports as installed (R-V2-2).
  - A worker that goes silent for 90 s is marked `gone`.
- **Security.** Connection test and update will dial any URL an admin enters (S1, open for V2-21). Only admins can create or change connections.

## 4. Supervisor and worker pools

`python -m lkap_supervisor` reconciles `GET /internal/v1/fleet/desired` every `LKAP_SUPERVISOR_INTERVAL_S` (10 s). It starts or stops replicas for each `supervised` connection, and rolls them on **Restart** (R-V2-4).

- **Backends** (`LKAP_SUPERVISOR_BACKEND`):
  - `subprocess` (dev default) runs `agent/` in-process-tree workers.
  - `docker` runs `lkap-agent:slim|full` containers. It needs `/var/run/docker.sock`, `LKAP_SUPERVISOR_WORKER_API_URL` (workers cannot reach the api's loopback) and `LKAP_SUPERVISOR_DOCKER_NETWORK`.
- **Stopping.** The supervisor stops a replica with SIGINT, waits `LKAP_SUPERVISOR_DRAIN_S` (3600 s; never less than 15 s, D-W2-13), then SIGKILL.
- **Replica count.** Run exactly **one** supervisor. In prod it takes a Redis lease (`LKAP_REDIS_URL`).
- **Metrics** are on `:9105`. Its state dir remembers which replicas it already signalled.
- **Env.** Each replica gets its env from `GET /internal/v1/connections/{id}/worker-env`. That includes `LKAP_PACKS`, so the api and supervised workers agree by construction.
- **Pack parity (F-17).**
  - Workers you start by hand must use the api's `LKAP_PACKS`.
  - A worker that registers with a different pack set makes the api log `worker_pack_mismatch` with `missing_on_worker` and `unknown_to_api`.
  - Without the fix, sessions on that worker fall back to the null pack: a generic panel and no pack tools.
  - Grep the api log after starting a worker, or compare each instance's `pack_ids` in `GET /v1/connections/{id}/fleet` with `GET /v1/health`'s `packs`.

## 5. Images and compose

Details are in `deploy/README.md` §4. In short:

```bash
scripts/vendor_agent_deps.sh                                       # lkap-contracts/lkap-packs wheels → agent/vendor/
contracts/.venv/bin/python scripts/gen_plugin_requirements.py      # after any registry change (--check in CI)
docker build --build-arg LKAP_IMAGE_FLAVOR=slim -f agent/Dockerfile -t lkap-agent:slim agent
docker build --build-arg LKAP_IMAGE_FLAVOR=full -f agent/Dockerfile -t lkap-agent:full agent
docker build -f supervisor/Dockerfile -t lkap-supervisor .
```

- **The import check.** Each worker image runs an import check at build time and writes `/app/installed_providers.json`. A provider that fails to import fails the build, and the fix is to mark it `deferred` in the registry. For example, Krisp is `deferred` because `livekit-plugins-krisp` has no 1.8.2 release (asks #56).
- **What is not in the image.** The shared test fakes (`testing/`) are a dev dependency only and are never installed in the image.
- **Compose:**
  - `deploy/docker-compose.dev.yml`: api (+ web on :3000), supervisor, and optional `postgres`/`redis`/`minio` profiles.
  - `deploy/docker-compose.prod.yml`: two api replicas behind Caddy, web, supervisor, postgres, redis, minio.
- **Config.** Env files are human-created copies of `deploy/*.env.example`. Run `alembic upgrade head` in the api container after every image update.
- **Not built locally yet.** Docker has not been running on the dev host, so no image has been built. CI builds them in `.github/workflows/docker.yml`, and the nightly `full` build runs once the repo has a GitHub remote.

## 6. Backups and restore

**Production (Postgres + `LKAP_DATA_DIR`).** Run this on a schedule from a host that has the `aws` CLI and `pg_dump`:

```bash
LKAP_BACKUP_S3_BUCKET=lkap-backups scripts/backup.sh
```

It dumps Postgres (when configured) and tars `LKAP_DATA_DIR` (local storage, LanceDB, any SQLite file), both to S3. It then prints the exact restore commands.

**Restore** is a decision, not a script:
1. Stop the api, the jobs worker and the supervisor.
2. `aws s3 cp` both artifacts down.
3. `pg_restore --clean` the dump, and untar the data dir in place.
4. Run `alembic current`. It must be the head of the running code; if it is not, run `upgrade head`.
5. Start the processes again, then check `/v1/health` (`db: ok`).

**Dev (SQLite):**
- **Before every `alembic` run and every downgrade:**
  ```bash
  sqlite3 api/data/lkap.db ".backup /somewhere/outside/api/data/lkap-$(date +%s).db"
  ```
  SQLite DDL is not transactional, so a failed chain leaves `alembic_version` out of step with the schema.
- **To restore:** stop the api, then copy the file back.
- **A full downgrade is lossy:** it drops v2 tables such as connections and channels. See `docs/v2/_briefs/migration-rehearsal.md`.

## 7. Key and secret rotation

| Secret | How to rotate | Effect |
|---|---|---|
| `LKAP_MASTER_KEY` | Back up the DB, then stop the api. Put both keys in the environment, not on the command line (argv is visible in `ps`): `LKAP_OLD_MASTER_KEY` and `LKAP_NEW_MASTER_KEY`, then run `cd api && uv run python -m lkap_api.keys rotate`, which re-encrypts every `*_ct` column in one transaction. Then set the new key in the launch config and start. | Nothing, if done in that order. Outstanding invites die when `LKAP_SESSION_SECRET` is unset. |
| A connection's LiveKit key/secret | Console: connection → **Rotate**, then **Test**. The status reads `unverified` until the test passes. | Supervised pools drain and restart automatically, because `credentials_version` is part of the desired hash. Restart external workers with the new env. |
| Provider credentials | Console → Keys (`/console/keys`) → edit | The next session uses them. There is no worker restart (config is fetched per job). |
| `LKAP_SERVICE_TOKEN` | Change it on the api, the supervisor and every worker at once, then restart them. For Cloud-hosted pools, update `secrets.env` and redeploy. | Workers that still hold the old token cannot register or fetch config. |
| `LKAP_ADMIN_TOKEN` | Change it on the api and the web, then restart both | Scripts using the old token get 401 |
| `LKAP_SESSION_SECRET` | Change it and restart the api | Outstanding invite links stop working |
| Webhook signing secrets | Settings → Webhooks → recreate the endpoint | The new secret is shown once |
| User passwords / API keys | Settings → Account (password), Settings → API keys (revoke, then create) | Immediate |

**Never pass secrets as command-line arguments in production.** `ps` shows them. The dev launch config still does this (S2, open for V2-21).

## 8. Telephony

Outbound dialing and transfers are **denied by default**. Before any outbound call or transfer, an admin must set the workspace dialing policy (`workspaces.settings.telephony.allowed_prefixes`, on `/console/telephony`; R-V2-23). Premium-rate and satellite ranges are always refused.

Setup, carrier side, trunk/rule/number creation and the live test ladder are in **`docs/v2/TELEPHONY-LIVE-TEST.md`** (§2 covers `allowed_prefixes`). This runbook does not repeat them.

## 9. Database migrations

- The migration head is shown by `cd api && uv run alembic heads`. `/v1/health` reports `db: error` until the schema is at head.
- **Before every run against the dev file:**
  1. Back it up (§6).
  2. Only the coordinator migrates `api/data/lkap.db`. Packages rehearse on a copy, using `-x url=sqlite+aiosqlite:///<copy>`.
- The last rehearsal (copy and fresh DB, up/down/up) is `docs/v2/_briefs/migration-rehearsal.md`.
- Postgres is exercised in CI (`python.yml`, job `test-postgres`).

## 10. Smoke test

`scripts/smoke_v2.sh` runs end to end against compose dev:
1. Boots compose dev.
2. Signs in (`LKAP_SMOKE_EMAIL`/`LKAP_SMOKE_PASSWORD`, or `LKAP_ADMIN_TOKEN`).
3. Creates a **supervised** connection from `LIVEKIT_*`, under a fresh `agent_name` `lkap-smoke-<ts>` so it can never split `lkap-agent` dispatch.
4. Binds and publishes a generic agent.
5. Waits for a ready replica.
6. Runs a text-mode round trip (`lk.chat` in, `lk.transcription` out).
7. Checks the session row. It then stops the pool and tears the stack down (`--keep` leaves it up).

Modes:
- `--no-boot` runs against an api that is already up.
- `--dry-run` makes **read-only** GETs against a running api (`LKAP_ADMIN_TOKEN=dev-admin scripts/smoke_v2.sh --dry-run`) and prints the writes it would make.

The dry run passes against the local dev api. The full run needs Docker and has not run on the dev host yet.

## 11. Quality gates

Python packages: `contracts`, `api`, `agent`, `packs`, `supervisor`, and `testing` (the shared fakes, F-18). In each:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ --strict && uv run pytest -q -m "not live"
```

In `web/`: `pnpm lint && pnpm typecheck && pnpm test`. Never run `pnpm build` in `web/` itself, because it clobbers the dev server's `.next`. Build in a scratch copy instead.

- **Test timing.** The api suite runs in about two minutes. Unit tests must never load the real fastembed model: `tests/conftest.py` refuses it (V2-19B found one test downloading ~90 MB for 20 minutes). Use `FakeEmbedder`.
- **Contracts.** After any contracts edit:
  ```bash
  scripts/export_contracts.sh --generate
  ```
  `web/src/contracts` must be byte-identical to `contracts/generated/ts`.

---

## 12. v1 reference

### 12.1 Restarting the worker (D-W2-13)

- Config edits (anything saved in the console) **never** need a restart, because config is fetched per job.
- Code edits **always** do. `python -m lkap_agent.main dev` has **no hot reload**; that exists only when the `lk` CLI drives the process, which the platform does not use.
- How to stop it: send **SIGINT**, wait **≥ 15 s** (dev) or `drain_timeout` (start mode, 3600 s by default), then **SIGKILL only if it is still alive**. Never SIGKILL first.
  ```bash
  kill -INT <pid>; for i in $(seq 1 15); do kill -0 <pid> 2>/dev/null || break; sleep 1; done; kill -0 <pid> 2>/dev/null && kill -KILL <pid>
  ```
- Observed on 2026-09-19 (livekit-agents 1.8.2), three restarts in `dev` mode with no active job: the process exited **1–2 s** after SIGINT, and the log showed `shutting down worker`. That matches D-W2-13 (dev → `aclose()` immediately). The start-mode drain was not exercised locally because Docker was not running.
- Plain SIGTERM in `start` mode only *drains*: the old worker stays registered while a new one starts. That is how two workers once served `lkap-agent` at the same time.
- `agent/Dockerfile` sets `STOPSIGNAL SIGINT`. If you ever run the worker under compose, the commented `agent` service in `deploy/docker-compose.yml` shows `stop_signal: SIGINT` and `stop_grace_period: 1h` (≥ `drain_timeout`).
- A killed worker leaves `active` session rows behind. The api's stale-session sweep (D-W2-2b) is the safety net, not a substitute for the grace period.

---

## 13. Create agents (v1; still valid)

Console path: `/console` → **New agent** → name + pack → **Create agent** → the editor opens → toggle **Draft → Published** (or use **Test call** for a draft; see D-W2-1). In v2 the agent is bound to the workspace's default connection unless you pick another (§3), and **Test chat** on the editor runs a text-mode session.

API path (admin token):

```bash
A='-H X-Admin-Token:dev-admin -H Content-Type:application/json'
curl -s $A -X POST localhost:8080/v1/agents -d '{"name":"Claims intake","pack_id":"insurance_claim"}'
curl -s $A -X PUT  localhost:8080/v1/agents/<id> -d '{"published":true}'
```

What seeding from a pack gives you (CONTRACTS §8, D-W2-10):

| Pack | Pipeline | LLM | Capabilities | Seeded KBs |
|---|---|---|---|---|
| `generic` | cascaded Inference: `deepgram/nova-3` → LLM → `inworld/inworld-tts-2` (voice Ashley) | `google/gemma-4-31b-it` (text-only, cheapest) | chat | none |
| `insurance_claim` | same cascaded Inference stack, plus `google-image-gen` only when exactly one Google credential exists | **`google/gemini-3.5-flash`**, the registry's first `supports_video` model, because the camera is on | camera, chat | "Insurance policy lines", "Intake playbook" (ingested with fastembed; `ready` within seconds) |

Everything runs on LiveKit credentials alone. Vendor keys (Google image/realtime, bey/tavus avatars) go through the console's credential modal, never into env or files.

**Vision and the model gate (D-W2-10).** Per-turn camera/screen injection in cascaded mode depends on the registry:
- A model flagged `supports_video` (shown as "· vision" in the model picker) gets frames.
- A known text-only model (gemma) gets none. The session records one `info` event, "vision injection skipped: … is text-only", and `describe_current_frame` refuses.
- A free-text model id is tried, and the worker auto-disables injection after an LLM error (D-W2-8 R5).

Only `google/gemini-3.5-flash` (Inference) and the three Gemini Live models are flagged today. To add another model, verify it first: publish an agent on that model with camera on, show a coloured card with text, ask "what colour and what does it say", then set `supports_video=True` in `contracts/src/lkap_contracts/providers.py` and regenerate.

---

## 14. Test each feature by hand (real browser, mic and camera)

Use Chrome on `http://localhost:3000`, allow the microphone and camera, and hang up as soon as each check passes (cost). Every call must leave exactly **one** session row that turns `ended` within 10 s of hangup (`/console/sessions`).

**HTTP tools need `allowed_hosts` filled in (F-05).** An HTTP tool with an empty `allowed_hosts` list now fails both the console's dry run and every live call (fail closed); fill in the host(s) the tool calls before testing it.

| # | Feature | How | Pass |
|---|---|---|---|
| 1 | Dispatch + resolve | `/s/smoke-generic` → Start call | Worker: `accepting job agent_name=lkap-agent` → `job accepted` → `session built` → `session started`; page shows the agent listening. |
| 2 | Voice both ways | Listen for the greeting; say "Tell me one fact about Denver." | Greeting audible (click **Enable sound** if autoplay blocked it); your words and the reply in the transcript. |
| 3 | Typed chat | Chat button → type "Answer in five words: what can you do?" | Reply in the transcript; a `user_turn` event. Typed turns run the same hook as spoken ones (D-W2-9p): KB auto-inject, per-turn frame, pack hook. |
| 4 | Tools → panel | Type "Add a note that says hello world, then set the status to Reviewing." | Generic panel shows the note and the stamp; `tool_call_started/ended` events carry the tool name. |
| 5 | Hangup → summary | **End call**; in a second call say "That's all, please end the call." | Row `ended` ≤ 10 s with transcript, usage and final UI state; the page shows "Call ended". |
| 6 | Test call on a draft | Unpublish `smoke-generic`; console **Test call** (`/s/smoke-generic?mode=test`) | Public route shows "not published"; test mode shows the "Test mode" badge and connects. |
| 7a | Camera/screen → pin | `/s/smoke-vision`, camera on, type "Pin what you see with the caption 'test pin'." Then screen share and repeat. | Image with caption in the panel; `meta.source` is `camera`, then `screen`. |
| 7b | Model sees the frame | Hold up an object: "What am I holding?" | Correct answer on `google/gemini-3.5-flash`. On gemma: no image is sent and one `info` event appears (by design). |
| 8 | Knowledge base | `/s/smoke-kb`, typed: "What does policy AUTO-11111's status say?" / "Search the knowledge base for flood coverage." | First answer says lapsed without a tool call; second calls `search_knowledge(query)` and names `policy_lines.md`. |
| 9 | Insurance end to end | Create from pack `insurance_claim`, publish, `/s/<slug>`. Call 1, type or say: "Policy H0-44721, my basement flooded yesterday in Denver, nobody was hurt." Camera on: "Please pin what you see as evidence." Call 2: "Policy AUTO-11111, I was rear-ended on I-25 and my passenger's neck hurts." | Call 1: policy note ≤ 5 s, Claim writer running→done, stamp, still-needed list and %, **Read the adjuster packet** dialog with markdown, a polaroid evidence photo. Call 2: lapsed-policy note, stamp **Escalate to human** (danger), and an urgent spoken reply containing "emergency". |
| 9b | Sketch (optional) | Needs a `google-image-gen` credential: "Draw a sketch of the incident." | Polaroid "Does this look right?" → confirm round-trip. |

---

## 15. Automated and live tests

Offline gates: see §11. The v1 form, in each of `contracts/ api/ packs/ agent/`:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ --strict && uv run pytest -q -m "not live"
```

In `web/`: `pnpm lint && pnpm typecheck && pnpm test`.

Live tests (billed to the LiveKit project, a few cents per run):

```bash
cd agent
export LIVEKIT_URL=… LIVEKIT_API_KEY=… LIVEKIT_API_SECRET=…
uv run pytest -m live -v tests/live/test_inference_smoke.py                    # Stage 0, no room
uv run pytest -m live -v tests/live/test_e2e_insurance.py -k text_mode         # insurance text mode, no room/api
# with the api + ONE local worker running:
export LKAP_LIVE_API_BASE_URL=http://127.0.0.1:8080 LKAP_LIVE_ADMIN_TOKEN=dev-admin
uv run pytest -m live -v tests/live/test_e2e_generic.py                        # Stages 1,3,4,5
uv run pytest -m live -v tests/live/test_e2e_insurance.py -k room              # Stage 9 room level + camera pin
```

The room-level tests create a fresh agent per run (named with a run id). Rows cannot be deleted while sessions exist, so the console accumulates `E2E …` agents. That is expected.

---

## 16. v1 live results (LiveKit Cloud, LiveKit credentials only)

Stages 0–8 were run by W2-AGENT-INTEGRATION on 2026-09-18 with a scripted room participant (typed `lk.chat`, synthetic camera/screen tracks, a WAV file for audio). Stage 9 was run by W3-E2E-INSURANCE on 2026-09-19.

| Stage | Result | Model | Evidence |
|---|---|---|---|
| 0 Inference smoke | pass | gemma-4-31b-it | `test_inference_smoke.py` 2 passed |
| 1 Dispatch + resolve | pass (re-run after D-W2-11 with **no** `LIVEKIT_AGENT_NAME` set) | gemma | worker `registered worker {"agent_name": "lkap-agent"}`; `accepting job agent_name=lkap-agent room=lkap-d3716f77`; session `d3716f77…` ended 3.6 s after hangup |
| 2 Audio round trip | pass (WAV via probe) | gemma | "Denver is known as the 'Mile High City'…"; ended 1.1 s after hangup |
| 3 Typed chat | pass | gemma | typed turn → reply; `user_turn` event |
| 4 Tools → panel | pass | gemma | `push_note`/`set_status`/`current_time`; "It is currently 7:12 AM." |
| 5 Hangup → summary | pass | gemma | End call ended 5.2 s after hangup; `end_call` tool ended 0.1 s after hangup; one row per call |
| 6 Test mode (draft) | pass (through the console proxy) | gemma | proxy connect → call |
| 7a Camera/screen → pin | pass | gemma | assets `caption="test pin"` (camera) and `"screen pin"` (screen) |
| 7b LLM sees the frame | **gemma: fails silently**; **gemini-3.5-flash: pass** | both | Red "CAM 42" card: gemma answered "The background color is white." / "It says 'Hello World'"; flash answered "The main background is red." / "It says 'CAM 42'". Now enforced by the D-W2-10 gate. |
| 8 Knowledge base | pass | gemma | auto-inject answer "lapsed personal auto policy…"; `search_knowledge` → `policy_lines.md` |
| 9 text mode | **pass** (2/2, 13 s) | gemini-3.5-flash | Flood: tools `lookup_policy, sync_claim_packet`, route `needs_docs`, stamp "Needs docs", the first reply asks for the policy number. Injury: route `emergency_escalation`, stamp "Escalate to human" (danger), and a separate urgent reply: "Please contact emergency services immediately if anyone is in danger…" |
| 9 room level (scripted) | **pass** (53 s) | gemini-3.5-flash | agent `e2e-insurance-bf8f95`, session `ea5563db…`. Events: `lookup_policy`, `sync_claim_packet`, `describe_current_frame` (the model correctly said the synthetic frame is "only a solid, uniform red colour"), `pin_evidence_photo` → asset `kind=evidence meta.source=camera confirmed=false`. Worker `injected frame … images_in_ctx=1`. Final state: route `needs_docs`, 9 notes, packet 2736 chars, 1 row, ended. |
| 9 browser, call 1 | **pass** | gemini-3.5-flash | `/s/stage9-insurance` (created and published in the console), typed flood turn. Notebook: "H0-44721 / Homeowners (HO-3), active", "home water damage · high", still-needed 53% (11 open), "Claim writer finished 4567 ms", "Policy desk · Active". Adjuster packet dialog rendered. Session `bd7869e5…` ended 0.1 s after End call. |
| 9 browser, call 2 | **pass** | gemini-3.5-flash | typed injury turn. Stamp "Escalate to human", lapsed note, urgent reply "Please contact emergency services right away…". Session `8345f11a…`, route `emergency_escalation`; 2 calls → 2 rows. |
| 9 timings (scripted) | pass | gemini-3.5-flash | policy note patch 1.6 s after the typed turn (≤ 5 s); Claim writer running→done 4.8 s; `workflow_run {name: sync_claim_packet, duration_ms: 4717, status: done}` recorded |
| 9b Sketch | skipped | — | no `google-image-gen` credential; the Google free-tier key is nearly out of quota |
| 10 Avatar | not run | — | no bey/tavus key. D-W2-7 step 3 (`useAgentRpc` avatar exclusion) is unverified. |
| 11 Gemini Live | not run | — | Google quota |
| 12 Cloud deploy | **build not verified** | — | Docker was not running on the dev machine, so the image could not be built. `lk agent list` (read-only) shows only `other-project-agent`; no `lkap-agent` is deployed. The deploy itself is a human step (§6). |

The browser runs used the Claude Browser pane, which blocks microphone and camera. The browser legs therefore prove the typed path, panel rendering and hangup. Voice (Stage 2) and a real camera pin in the browser still need a hand check (§3 rows 2, 7a, 9). The Playwright suite planned under `web/e2e/**` was not written. The browser leg was driven at DOM level in the pane, with the api's session rows and events as evidence, because the pane blocks media and every browser run costs Inference.

---

## 17. Deploy the worker to LiveKit Cloud (human steps)

These commands create real resources in the shared project. Run them yourself. Stop the local worker first (§12.1). In v2, the connection's **Download deploy bundle** (§3) generates `livekit.toml`/`secrets.env` for a `cloud_hosted` pool.

```bash
cd livekit_agent_platform
scripts/vendor_agent_deps.sh                         # wheels for lkap-contracts / lkap-packs into agent/vendor/
docker build -f agent/Dockerfile -t lkap-agent agent # optional local check (needs Docker running)

cd agent
cp secrets.env.example secrets.env                   # fill in: LKAP_API_BASE_URL (PUBLIC https origin of the api),
                                                     #          LKAP_SERVICE_TOKEN, LKAP_PACKS
                                                     # NOT LIVEKIT_* and NOT LIVEKIT_AGENT_NAME (D-W2-11)
lk agent create --secrets-file secrets.env           # first time only; writes the ids into livekit.toml
lk agent deploy                                      # every later update (re-run vendor_agent_deps.sh first)
lk agent list                                        # must show lkap-agent AND the untouched other-project-agent
lk agent logs                                        # first job must log: accepting job agent_name=lkap-agent
```

End to end from the cloud worker needs a publicly reachable api (`LKAP_API_BASE_URL`), either the compose deployment behind HTTPS or a tunnel. Without one, the MVP bar is "builds + registers". Never pass `other-project-agent`'s ids or secrets to any `lk agent` command.

---

## 18. Known gaps and open items

| Item | Status / reason |
|---|---|
| 9b sketch | Skipped: no image-gen credential / Google quota. The tool degrades gracefully ("Sketching isn't available in this session"). |
| 10 avatar | No key. `useAgentRpc` avatar exclusion (D-W2-7 step 3) is unverified. |
| 11 Gemini Live | Google quota. The workflow LLM falls back to Inference gemma in realtime mode. |
| 12 build + register | **DoD-blocking (LIVE_TEST_PLAN Part C), not met yet.** Docker was not running locally, so the image was not built; the deploy is a human step (§6). |
| 12 end to end from the cloud worker | Needs a public api URL. |
| Idle hangup | `LKAP_IDLE_HANGUP_S` (default 120 s) ends a session that stays `away` that long while the agent is listening/idle (REVIEW-FINAL F-02). |
| Realtime typed turns | In realtime mode the model sees only the raw typed text, not per-turn `turn_ctx` edits (SDK behaviour, D-W2-9p known gap). |
| Full reconnect after a network drop | Replays the same token/room; if the job already closed, start a new call (D-W2-2). |
| Shutdown log noise | The insurance pack's final "Session ended … Final route" note is applied to the stored state, but sending it to the already-departed browser logs a `StreamError: internal error` warning. Harmless. |
| Initial stamp | The insurance notebook starts at "Needs docs" (the blank-intake route), not a blank stamp. This is pack behaviour. |
| Other vision models | `openai/gpt-4.1`, `gpt-4o-mini` and the vendor LLMs are not flagged `supports_video` until someone verifies them (§2). |

## 19. Troubleshooting (LIVE_TEST_PLAN Part F, updated)

1. **The page shows "This session has ended" immediately, and no session row is created.** The token source was frozen before its first connect. This happened under `pnpm dev` (React StrictMode re-runs effects) before the 2026-09-19 fix in `web/src/lib/livekit.ts`: `freeze()` is now a no-op until a connect has been attempted.
2. **No `accepting job` line / job rejected.** The dispatch name drifted. The api's `LKAP_AGENT_NAME` must be `lkap-agent`; the worker refuses to start if `LIVEKIT_AGENT_NAME(_OVERRIDE)` is set to anything else.
3. **"configuration unavailable" spoken.** `LKAP_SERVICE_TOKEN` or `LKAP_API_BASE_URL` mismatch between the api and the worker.
4. **Two replies / nondeterministic dispatch.** Two workers are running (local + cloud, or an old drained one). See §1.1.
5. **The camera turn is answered wrongly.** Check the model: gemma ignores images silently. The console warns, and the worker records "vision injection skipped".
6. **No greeting audio.** Autoplay policy; click **Enable sound**.
7. **KB answers are empty.** The KB is not `ready` yet, or the fastembed model download (~130 MB, first ingest only) is blocked.
8. **Costs.** Keep calls short; never leave `/s/…` open (STT streams while it is open); check `usage` on each session in the console.
9. **Generic panel, no pack tools on a pack agent.** The worker cannot import that pack (it runs the null pack). Check the api log for `worker_pack_mismatch` and give the worker the api's `LKAP_PACKS` (§4).
10. **The console redirects to `/login`.** Either the web's admin bypass is off or the api refuses the admin token (`LKAP_ENV=prod`). Sign in, or see §2.
11. **A new agent's provider is rejected as "not installed".** The bound connection's workers do not report it. Use the `full` image, or pick another provider (§3).
12. **An api test run takes 20+ minutes.** Something is loading the fastembed model over the network. `tests/conftest.py` now fails such a test at once.
