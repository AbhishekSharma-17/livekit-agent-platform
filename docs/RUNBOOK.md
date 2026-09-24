# LKAP — Runbook (v2)

This runbook covers running the LiveKit Agent Platform, operating it (sign-in, LiveKit connections, worker pools, images, backups, key rotation) and testing it by hand.

- Sections 1–11 are v2, written by V2-19 on 2026-09-23.
- Sections 12–19 are the v1 runbook, from live runs on 2026-09-18 and 2026-09-19. They still hold, except where a v2 section replaces them.
- Section 20 is v3 (Remote MCP, the `lkap-mcp` service, V3-06).

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

Secrets come from your shell, or from a dev env file outside the repo (below). Never put them in committed files, and never on a command line. Never read or write `.env*` files with tools.

**Dev secrets in an env file (R-V2-35).** A secret written into a launch command (`bash -c "export LIVEKIT_API_SECRET=… && exec …"`) is visible to every local user in `ps` and stays in the launcher's config. Keep them in one file instead:

1. Create `~/.config/lkap/dev.env` yourself, outside the repo, and `chmod 600` it. One `KEY=value` per line: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LKAP_MASTER_KEY`, `LKAP_ADMIN_TOKEN`, `LKAP_SERVICE_TOKEN` (and any provider keys a worker reads from its env).
2. Have each process's launcher source it and then `exec` the process, so the values reach the process environment and never its argv:
   ```bash
   bash -c 'set -a; . "$HOME/.config/lkap/dev.env"; set +a; cd api && exec uv run uvicorn lkap_api.main:app --host 127.0.0.1 --port 8080'
   ```
   The same wrapper works for the worker (`cd agent && exec uv run python -m lkap_agent.main dev`), the supervisor and `pnpm dev`. Non-secret settings (`LKAP_PACKS`, `LKAP_API_BASE_URL`, ports) can stay in the launcher.
3. Rotate anything that was ever on a command line after moving it (§7).

Nothing in the repo reads that file or knows where your launcher keeps its configuration; the rotation CLI (`keys rotate`), `scripts/smoke_v2.sh` and both supervisor backends already take secrets from the environment only.

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
| `LKAP_API_BASE_URL` | ✓ | ✓ | ✓ | | `http://127.0.0.1:8080`. On the api it is the url handed to workers (worker env, deploy bundle); unset falls back to `LKAP_PUBLIC_BASE_URL`, then to a guess from `PORT`, which the api logs as `worker_callback_url_derived_from_port`. |
| `LKAP_EMBEDDER` | ✓ | | | | the knowledge-base embedder, platform-wide: `fastembed` (default, local), `<provider_id>:<credential_id>` for an OpenAI-shaped embedding entry (`openai-embedding`, `openrouter-embedding`; the credential must be stored under that entry's credential home), or the legacy `openai:<credential_id>`. |
| `LKAP_NET_ALLOW_PRIVATE_HOSTS` | ✓ | | | | comma list of host names, IPs or CIDRs the outbound network guard may reach although they are private. Unset = `localhost,127.0.0.1,::1` in `dev`, nothing in `prod` (§5.1). |
| `LKAP_CONNECTION_ID` | | optional | | | the connection a worker serves. Unset means the default connection. |
| `LKAP_AGENT_NAME` | | optional | | | must equal the connection's `agent_name` (default `lkap-agent`) |
| `LKAP_INSTANCE_KEY`, `LKAP_MANAGED_BY` | | set by the supervisor | | | leave unset for a hand-started worker |
| `LKAP_WORKER_HTTP_PORT` | | optional | | | port of the SDK's worker HTTP server. Unset keeps the SDK default (ephemeral in `dev`, 8081 in `start`). The supervisor's `subprocess` backend sets `0`, so replicas on one host never collide on 8081 (V2-20). |
| `LKAP_HTTP_TOOL_ALLOWED_HOSTS` | | optional | | | comma list. A tool's hosts must be in it **and** in its own `allowed_hosts`, and private ranges are refused (F-14). |
| `LKAP_HTTP_TOOL_USER_AGENT` | optional | optional | | | the `User-Agent` HTTP tools send (the worker's tool calls, the api's dry runs) when a tool's own `headers` set none. Default `LKAP/0.1 (+https://github.com/AbhishekSharma-17/livekit-agent-platform)`. Some APIs want a contact: Wikimedia answers 403 to a User-Agent with no URL or email, so an operator running their own deployment should put their own contact URL here. Set the same value on both. |
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
- **Security.** Only admins can create or change connections. Every LiveKit call the api makes goes through the outbound network guard (V2-21, R-V2-26): a url on a private, loopback, link-local or cloud-metadata address is refused at save (422 `blocked_destination`) and again at connect time, after DNS; numeric host forms such as `2130706433` or `127.1` are refused outright; redirects are never followed (V2-22). A self-hosted LiveKit on a private network must be listed in `LKAP_NET_ALLOW_PRIVATE_HOSTS` (§5.1).

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
docker build -f mcp/Dockerfile -t lkap-mcp .   # from livekit_agent_platform/ — root context, like supervisor
```

- **The import check.** Each worker image runs an import check at build time and writes `/app/installed_providers.json`. A provider that fails to import fails the build, and the fix is to mark it `deferred` in the registry. For example, Krisp is `deferred` because `livekit-plugins-krisp` has no 1.8.2 release (asks #56).
- **What is not in the image.** The shared test fakes (`testing/`) are a dev dependency only and are never installed in the image.
- **Compose:**
  - `deploy/docker-compose.dev.yml`: api (+ web on :3000), supervisor, and optional `postgres`/`redis`/`minio` profiles.
  - `deploy/docker-compose.prod.yml`: two api replicas behind Caddy, web, supervisor, postgres, redis, minio.
- **Config.** Env files are human-created copies of `deploy/*.env.example`. Run `alembic upgrade head` in the api container after every image update.
- **Not built locally yet.** Docker has not been running on the dev host, so no image has been built. CI builds them in `.github/workflows/docker.yml`, and the nightly `full` build runs once the repo has a GitHub remote.

### 5.1 Production rules (REVIEW-V2 §8, V2-22)

Follow these for anything that is not a developer's machine:

- **`LKAP_ENV=prod`.** The dev network allowlist (loopback) and weak `dev-*` secrets are refused in `prod`, and the admin token is off.
- **SQLite runs exactly one api process (R-V2-34).** Concurrency caps (`max_concurrent_sessions`, the dialing policy's `max_concurrent_outbound`) are reserved under a per-process lock plus a `FOR UPDATE` row lock. SQLite has no row locks, so with SQLite the guarantee is the in-process lock alone: never run a second api process (or `uvicorn --workers 2`) against one SQLite file. Anything that scales the api (`api ×2` in the prod compose) needs Postgres.
- **A private self-hosted LiveKit must be allowlisted (R-V2-26).** The api refuses private and local destinations. A self-hosted LiveKit server on a private network (for example `ws://livekit.internal:7880`) only works once its host name or CIDR is in `LKAP_NET_ALLOW_PRIVATE_HOSTS`, e.g. `LKAP_NET_ALLOW_PRIVATE_HOSTS=livekit.internal,10.20.0.0/16`. Cloud metadata addresses stay refused whatever is listed. The same list covers webhook endpoints and HTTP-tool dry runs, so list only what the api must reach. A future storage-config route must check S3 `endpoint_url`s the same way (`storage/endpoint.py`); the operator's own `LKAP_STORAGE_ENDPOINT_URL` is not checked.
- **The internal worker API is gated by source address (R-V2-27).** `deploy/Caddyfile` answers 403 on `/internal/*` unless the caller's address is in `LKAP_INTERNAL_ALLOWED_CIDRS` (space-separated CIDRs or IPs; default `private_ranges`: the compose network and private LANs). Workers that call back over the internet (LiveKit Cloud-hosted agents, §17, or a worker on another network) need their egress addresses listed, e.g. `LKAP_INTERNAL_ALLOWED_CIDRS=private_ranges 203.0.113.7/32` in `deploy/prod.env`. Check with LiveKit which addresses Cloud-hosted agents call out from; if they are not fixed, a `cloud_hosted` pool cannot pass this gate without opening `/internal/*` widely, so until Phase 2's per-connection worker tokens prefer `supervised` or `external` pools on your own network. If a load balancer is ever put in front of Caddy, switch the matcher to `client_ip` with `trusted_proxies`, or every caller looks like the balancer.
- **The service token is operator-only (R-V2-27).** `LKAP_SERVICE_TOKEN` unlocks every workspace's decrypted provider keys and every connection's LiveKit secret through `/internal/v1/*`. Never give it to a workspace admin, never paste it into the console, and keep the deploy bundle's placeholder until you, the operator, fill it in on infrastructure you control. Per-connection worker tokens (designed in PLAN-V2 R-V2-27) become mandatory before a second organisation is a tenant with its own admins, a tenant hosts its own workers, the Phase 2 automated `cloud_hosted` deploy ships, or the api serves workspaces of more than one operator.

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

Outbound dialing and transfers are **denied by default**. Before any outbound call or transfer, an admin must set the workspace dialing policy (`workspaces.settings.telephony.allowed_prefixes`, on `/console/telephony`; R-V2-23). Premium-rate and satellite ranges are always refused, and so is the NANP pay-per-call exchange (`+1 NPA 976 xxxx`).

- **`+1` means the United States and Canada only (R-V2-29).** Caribbean countries and US territories share `+1` but need their own prefix: `+1876` (Jamaica), `+1787`/`+1939` (Puerto Rico), `+1809`/`+1829`/`+1849` (Dominican Republic) and so on (`telephony/policy.py::NANP_NON_US_CA_NPAS`). The console warns when `+1` is the only NANP entry.
- **A `sip:` address with a number needs a listed host (R-V2-28).** `sip:+15551230000@pbx.example.com` passes only when the number passes the prefix rules **and** `pbx.example.com` is in `allowed_sip_hosts`. To transfer to a phone number, use `+E.164` (or `tel:+E.164`); use `sip:` only for a listed SIP host.
- **Concurrent dials** are capped by `max_concurrent_outbound` under a lock (§5.1), so a burst never exceeds it.

Setup, carrier side, trunk/rule/number creation and the live test ladder are in **`docs/v2/TELEPHONY-LIVE-TEST.md`** (§2 covers `allowed_prefixes`). This runbook does not repeat them.

### 8.1 LiveKit-hosted numbers (V4-05)

A number bought from LiveKit itself needs no SIP trunk (US numbers, inbound only; design in `docs/v4/PHONE-NUMBERS.md`).

1. **Buy** it yourself: LiveKit dashboard → Telephony → Phone numbers, or `lk number purchase --country-code US`. LKAP never buys or gives back a number.
2. **Refresh**: `/console/telephony` → Phone numbers → **Refresh from LiveKit** (`POST /v1/telephony/numbers/refresh`). The number appears with the **LiveKit** source chip. A number already registered here as a trunk number is reported as a conflict and skipped.
3. **Assign**: pick the inbound agent (only agents on the number's connection are offered). LKAP creates a trunk-less dispatch rule limited to that number and attaches it to the number. The Routing chip reads **Routed**; **Detached** means the attachment changed in LiveKit, so press **Re-attach**.

Check from the CLI with `lk number list` (the number's dispatch rule id) and `lk sip dispatch list` (the `lkap:<rule id>` rule, with no trunk and the number in its called numbers). Deleting the number in LKAP detaches it and forgets it here; the number stays in your LiveKit project. The Build plan's 50 inbound minutes are LiveKit's quota; LKAP does not see them.

## 9. Database migrations

- The migration head is shown by `cd api && uv run alembic heads`. `/v1/health` reports `db: error` until the schema is at head.
- **Before every run against the dev file:**
  1. Back it up (§6).
  2. Only the coordinator migrates `api/data/lkap.db`. Packages rehearse on a copy, using `-x url=sqlite+aiosqlite:///<copy>`.
- The last rehearsal (copy and fresh DB, up/down/up) is `docs/v2/_briefs/migration-rehearsal.md`.
- Postgres is exercised in CI (`python.yml`, job `test-postgres`).

### 9.1 Custom model ids (V4-07)

Any model id a vendor accepts can be typed into a slot; the registry's list is a suggestion, never an allowlist (design in `docs/v4/CUSTOM-MODELS.md`).

- **The rule.** An id is 1–200 printable ASCII characters with no whitespace, no `? # & = < > " ' `` ` ``, no `://`, and it does not start with `http`. A value that looks like an API key (a known key prefix such as `sk-`, `AIza`, `xai-`, or 32+ characters of one class with no `/ . : -`) is refused first. Both are validation **errors**, and no message ever repeats the value. The same rule covers `type="model"`/`type="catalog"` fields and the id fields (`voice`, `voice_id`, `avatar_id`, `face_id`, `pal_id`, `persona_id`, `voice_name`, `emotion_id`). `GET /v1/providers` returns the rule as `model_id_rules`.
- **Unknown is a warning.** An id outside the registry list is a warning ("not in the suggestion list or the live catalog … run Test model") unless the workspace's cached live catalog lists it, or a "Test model" run passed with the slot's **current** key in the last 30 days. Rotating the key brings the warning back.
- **Where records live.** Table `provider_models` (migration `v4_002_provider_models`), one row per workspace, credential home, kind and model id: an admin's declared capabilities, the last test result (with the key's fingerprint) and catalog sightings (`catalog_seen_at`, `catalog_missing_since`). Read them with `GET /v1/providers/{id}/models` (`?custom=true` hides registry ids) and `GET /v1/providers/{id}/models/{model_id}`. An admin declares capabilities with `PUT /v1/providers/{id}/models/{model_id}` and a body `{"declared": {"vision": false, "tools": true}}`.
- **Clearing one.** There is no delete route. To forget a record, back up the database (§6), then run `DELETE FROM provider_models WHERE workspace_id = '<ws>' AND provider_home = '<home>' AND kind = '<kind>' AND model_id = '<id>';`. To clear a stale "no longer in the catalog" warning without deleting, fetch the catalog again with `?refresh=true`; the flag clears when the vendor lists the id again.
- **Live catalogs.** Deepgram's and Rime's lists are public and are fetched without a key (cached 24 h for everyone); OpenRouter's are cached 6 h, everything else 1 h. `GET /v1/providers/{id}/catalog` takes `q`, `limit` (≤ 1000, default 200), `offset` and `model` (a TTS model's voices) and searches the cached list; only OpenRouter entries forward `q` to the vendor, and only with `search_vendor=true`.

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

## 20. Remote MCP (v3, V3-06)

The remote MCP service is the platform's MCP server (`mcp/`, `lkap-mcp`) run as `lkap-mcp --http` in its own process and compose service. It serves agents that do not run on the user's machine: Claude Code on the web, Codex cloud, a shared team endpoint, CI. It is never mounted in the api (R-V3-4, R-V3-15). The design is in `docs/v3/AGENT-ACCESS.md` §9, and the client side is in `mcp/README.md` "Remote (HTTP) mode".

**How it authenticates.** The service holds no key of its own. Every request carries `Authorization: Bearer lkap_…`, and the key on a session's `initialize` request is the key that session uses for every api call.
- The key's scopes shape that session's tool list (R-V3-13).
- The session id (32 random bytes) is bound to the key's sha256. A request on the session with another key gets `403`.
- Test chats belong to the session (`session:<id>`, R-V3-24). They close when the session ends: `DELETE`, 30 min idle, revocation or shutdown.

### 20.1 Enable it

**Dev:**
- `docker compose -f deploy/docker-compose.dev.yml up mcp` serves `http://127.0.0.1:8090/mcp` on loopback only. Plain HTTP is for this machine only.
- Without compose: `LKAP_API_URL=http://127.0.0.1:8080 uv run --project mcp lkap-mcp --http`.
  - Under a process manager, or anything that stops the service with a signal to one pid, run `mcp/.venv/bin/lkap-mcp --http` directly, after `uv sync` in `mcp/`.
  - Don't use `uv run` there: when it runs in its own session it does not forward `SIGINT`, and the orphaned child keeps `:8090` (V3-07-1).
- In dev (`LKAP_ENV` unset or `dev`), loopback `Host`/`Origin` values are accepted.

**Prod** (`deploy/docker-compose.prod.yml`):
- The `mcp` service is network-internal (no published port). Caddy proxies `https://$LKAP_PUBLIC_DOMAIN/mcp` to `mcp:8090/mcp` (`deploy/Caddyfile`).
- The service runs with `LKAP_ENV=prod`, `LKAP_API_URL=http://api:8080` and `LKAP_MCP_PUBLIC_URL`. The public url defaults to `https://$LKAP_PUBLIC_DOMAIN/mcp`; override it in `deploy/prod.env` only for a different https url.
- The console's Connect dialog offers **Remote (HTTP)** only when the web image was built with `NEXT_PUBLIC_LKAP_MCP_PUBLIC_URL`. `web/Dockerfile` declares it as a build arg (R-V3-31) and both compose files pass it; rebuild the web image after changing it.
- **Check:**
  - `curl -si https://<domain>/mcp -X POST -H 'content-type: application/json' -d '{}'` answers `401` with `WWW-Authenticate: Bearer`: the route reaches the service, and the service wants a key.
  - `docker compose … exec mcp python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8090/healthz').read())"` prints `{"status":"ok"}`.

**The service refuses to start** (exit 2, reason on stderr) when:
- `LKAP_ENV=prod` and `LKAP_MCP_PUBLIC_URL` is missing or not `https://` (the bearer travels only over TLS). The bare-IP `LKAP_PUBLIC_DOMAIN=:80` smoke mode therefore leaves `mcp` down on purpose;
- `LKAP_MCP_PUBLIC_URL` is not an absolute http(s) url;
- any of `LKAP_API_KEY`, `LKAP_SERVICE_TOKEN`, `LKAP_ADMIN_TOKEN`, `LKAP_MASTER_KEY` is in its environment. `env:` references resolve in this process, so a value there would be usable by every key holder. That is also why the service gets no `api.env`.

**One replica, always.** Sessions and chats live in the process. A second replica needs sticky routing on `Mcp-Session-Id` (Phase 2). Never `--scale mcp=2`. A restart drops every session and chat; clients reconnect on the `404`.

### 20.2 TLS and the proxy

- **TLS.** Caddy terminates TLS for the api origin, and `/mcp` shares its certificate.
- **Host header.** Caddy passes the client's `Host` through, and the service compares it with the public url's host (`403 forbidden_host` otherwise; DNS rebinding, §9.5 item 3). A proxy that rewrites `Host` breaks this. Keep `reverse_proxy`'s default.
- **Origin header.** An `Origin` header, when present, must be the public origin (`403 forbidden_origin`). Browsers are not clients of this endpoint: there are no cookies and no CORS.
- **Caddy settings for `/mcp`.** `request_body max_size 1MB`, `flush_interval -1` (SSE events go out at once), `read_timeout 5m` (a dead upstream; the service pings open SSE streams every 15 s), and no `encode`.
- **Access log.** Caddy's access log redacts `Authorization` by default. Never enable the `log_credentials` server option.
- **Validate the Caddyfile.** CI runs `caddy validate` in the `caddy-validate` job of `.github/workflows/docker.yml`, in the same `caddy:2-alpine` image the prod compose file uses (R-V3-33). Before the first prod deploy with `/mcp`, run it once yourself from `livekit_agent_platform/` and paste the last line of its output into the V3-06-8 row of `docs/v3/_asks.md`:
  ```bash
  docker run --rm -e LKAP_PUBLIC_DOMAIN=lkap.example.com -e LKAP_INTERNAL_ALLOWED_CIDRS=private_ranges \
    -v "$PWD/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2-alpine \
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  ```
  Without Docker: `brew install caddy`, then `LKAP_PUBLIC_DOMAIN=lkap.example.com LKAP_INTERNAL_ALLOWED_CIDRS=private_ranges caddy validate --config deploy/Caddyfile --adapter caddyfile`.

### 20.3 Connect a client

Mint an agent key in the console (Settings → AI agents), then use the Remote snippet:
- **Claude Code:** `claude mcp add -s user --transport http lkap <LKAP_MCP_PUBLIC_URL> --header "Authorization: Bearer <key>"`.
- **Codex CLI** (field names verified against codex-cli 0.153.4, R-V3-25). The console's form for `~/.codex/config.toml`:
  ```toml
  [mcp_servers.lkap]
  url = "<LKAP_MCP_PUBLIC_URL>"

  [mcp_servers.lkap.http_headers]
  Authorization = "Bearer <key>"
  ```
  On a shared machine, keep the key in the shell environment instead:
  ```toml
  [mcp_servers.lkap]
  url = "<LKAP_MCP_PUBLIC_URL>"
  bearer_token_env_var = "LKAP_API_KEY"
  ```
  You can also add it from the command line: `codex mcp add lkap --url <LKAP_MCP_PUBLIC_URL> --bearer-token-env-var LKAP_API_KEY`, then `export LKAP_API_KEY=<key>`. Codex rejects a literal `bearer_token`. `codex mcp list` shows `Auth = Bearer token` for both forms.
- **Generic clients:** `{"mcpServers": {"lkap": {"url": "<LKAP_MCP_PUBLIC_URL>", "headers": {"Authorization": "Bearer <key>"}}}}`.

### 20.4 Limits

| Limit | Default | Env on the `mcp` service | Answer |
|---|---|---|---|
| Sessions per key | 5 | `LKAP_MCP_MAX_SESSIONS_PER_KEY` | the 6th `initialize` gets HTTP `429` + `retry_after_s`, `Retry-After` |
| Tool calls per session | 120 per rolling minute | `LKAP_MCP_CALLS_PER_MIN` | tool result `rate_limited` (HTTP 200) with `details.retry_after_s`; the session stays open |
| Tool calls in flight per session | 10 | `LKAP_MCP_MAX_IN_FLIGHT_PER_SESSION` | tool result `rate_limited` (HTTP 200) with `details.retry_after_s`; the session stays open |
| Per tool call | 60 s, or the call's own `timeout_s` + 15 s | `LKAP_MCP_CALL_TIMEOUT_S` | tool result `call_timeout` |
| Chats | 3 per session, 20 per service | `LKAP_MCP_MAX_CHATS`, `LKAP_MCP_MAX_CHATS_TOTAL` | tool result `too_many_chats` |
| Request body | 1 MB | `LKAP_MCP_MAX_BODY_BYTES` (Caddy enforces it too) | `413` |
| Idle session | 30 min without a request (an open `GET` stream does not count) | `LKAP_MCP_SESSION_IDLE_S` | the session and its chats are closed; the next request gets `404` |
| Api calls per key | 600/min | the api's `LKAP_API_KEY_RATE_PER_MIN` | the api's `429` |

Transport refusals (`401`, `403`, `404`, `413`, and `429` for a 6th session) are JSON-RPC error bodies whose `error.data.code` names the reason: `unauthorized`, `forbidden_host`, `forbidden_origin`, `session_key_mismatch`, `session_not_found`, `payload_too_large` or `rate_limited`.

The per-call limits (calls per minute, calls in flight) count `tools/call` only and are answered in band (R-V3-28): the call's result is `ok=false, code="rate_limited", status=429` with `details.retry_after_s`, `details.limit` and `details.scope` (`calls_per_min` or `in_flight`), sent with HTTP 200. That is the same shape as the api's own `429`. A refused call does not count toward the window, so a client that waits `retry_after_s` always gets through. `tools/list`, `resources/read` and `prompts/get` are not metered.

### 20.5 Revoke a key

Console → Settings → AI agents → revoke. A key is also invalid after its expiry.
- **Existing sessions.** The next tool call on any session of that key gets the api's `401`. The service relays it as `ok=false, code="unauthorized"`, then closes the session and its chats. Later requests on that session id get `404`.
- **New sessions.** A new `initialize` with a revoked key is refused with `401` before any session exists.
- **To end every session at once**, whatever the key, restart the `mcp` service.

### 20.6 Secrets on the remote service

- **Inline values** (pasted by the user) are allowed. They travel inside the TLS request and go straight to the api's vault; they never appear in a result, plan, log or error. To force references, set `LKAP_MCP_INLINE_SECRETS=off` in `deploy/prod.env`.
- **`env:NAME` references** resolve in the `mcp` service's environment only. Provision such values in the optional `deploy/mcp.env` (human-created, never committed). Every key holder can use them as secrets: never read them back, but able to send them in the requests of an HTTP tool they build. Put nothing there that is not meant for every key holder.
- **`file:` references** are refused (`ref_unavailable_in_http_mode`). So is `kb_add_document(file_path=)`.
- **`webhook_create`** is unavailable, because the one-time signing secret has nowhere safe to go. Create webhooks in the console.

### 20.7 The dial gate

`call_place`/`call_control` exist only when the key has `calls:write`, the service has `LKAP_MCP_ALLOW_DIAL=1`, and each call passes `confirm=true` (R-V3-7). The service default is `0`. Enabling it on the shared service lets every `calls:write` key place calls through the remote endpoint.
- Treat enabling it as a change: set `LKAP_MCP_ALLOW_DIAL=1` in `deploy/prod.env`, record who approved it and when in your change log, then run `docker compose … up -d mcp`.
- To turn it off, set it back to `0` and run the same command.
- The workspace dialing policy (`/console/telephony`) still applies to every call.

### 20.8 Logs

- **What the service logs.** The key's id (never the key), an 8-character session id prefix, the request method, and the refusal status and code.
- **What it never logs.** The `Authorization` header or any tool argument.
- **Library logs.** The MCP SDK's own loggers (`mcp.*`) are raised to WARNING in the service, because at INFO and DEBUG they print full session ids and raw messages. `uvicorn.access` is off.

### 20.9 Security review items (AGENT-ACCESS §9.5)

| # | Item | Covered by |
|---|---|---|
| 1 | Bearer only over TLS; `Authorization` never logged | `test_main_http_in_prod_with_a_plain_http_public_url_refuses_to_start`, `test_check_startup_public_url_rules`, `test_authorization_header_and_key_are_in_no_log_record` |
| 2 | Tenant isolation; unguessable, key-bound session ids | `test_two_workspaces_in_parallel_sessions_never_see_each_others_agents`, `test_session_ids_are_32_random_bytes_bound_to_the_key_hash`, `test_a_request_on_a_session_with_a_different_key_is_403`, the chat-ownership tests |
| 3 | DNS rebinding / Origin | `test_foreign_origin_or_wrong_host_is_403`, `test_origin_policy_host_and_origin` |
| 4 | Secrets | `test_file_ref_is_ref_unavailable_in_http_mode`, `test_inline_secret_creates_the_row_and_appears_in_no_log_or_result`, `test_inline_secrets_off_refuses_inline_values_on_the_service`, `test_webhook_create_is_unavailable_in_http_mode` |
| 5 | Resource limits | `test_sixth_session_…_is_429…`, `test_a_rate_limited_call_is_in_band_and_the_same_client_session_recovers`, `test_the_121st_tool_call_…_in_band…`, `test_an_eleventh_tool_call_in_flight_…`, `test_session_meter_refusals_do_not_extend_the_window…`, `test_a_2_mb_body_is_413`, `test_a_body_streamed_without_content_length_is_still_capped`, `test_a_call_over_the_cap_is_call_timeout`, `test_the_process_wide_chat_cap_spans_sessions`, `test_an_idle_session_is_closed_with_its_chats` |
| 6 | Outbound | Note: the service calls only `LKAP_API_URL` and the LiveKit urls the api returns in `text-sessions` responses (already `net_guard`-checked when the connection was saved). No code path fetches a user-supplied url; KB url import happens on the api (R-V3-14). |
| 7 | Resumability | Note: no event store is configured, so there is no `Last-Event-ID` replay. SSE state is in memory, per session, and gone when the session ends. |
| 8 | Image | Note: `mcp/Dockerfile` uses the pinned `python:3.12-slim-bookworm` and `uv` images, runs as non-root `lkap`, and has no `curl \| sh`. The builder stage fails unless the docs, generated resources and rtc wheel load. R2-21 digest pinning applies once the remote exists. The image is built by CI (`docker.yml`), not locally (no Docker daemon on the dev host). |
| 9 | Revocation | `test_key_revoked_mid_session_relays_unauthorized_then_the_session_is_dropped`, `test_revocation_closes_the_sessions_chats` |
| 10 | Dial gate | Note: `LKAP_MCP_ALLOW_DIAL` defaults to `0` in both compose files and `prod.env.example`; enabling it is the recorded operator action in §20.7. |

### 20.10 Troubleshooting

| Symptom | Cause |
|---|---|
| `403 forbidden_host` for every request | `LKAP_MCP_PUBLIC_URL`'s host differs from the host clients use, or a proxy in front rewrites `Host`. |
| `401` at connect with a key that works in stdio | The key was revoked or expired, or the client does not send the header. Check the Claude Code `--header` form or the Codex `http_headers`/`bearer_token_env_var`. |
| `502 api_unavailable` at connect | The service cannot reach `LKAP_API_URL`. |
| The client keeps reconnecting | The session went idle (30 min) or the service restarted. Clients start a new session on `404`. |
| `too_many_chats` with few chats open | Another session is using the 20-per-service cap, or this session already has 3. End chats with `chat_end`. |
| `call_timeout` on `kb_add_document` | The ingest took longer than `timeout_s` + 15 s. Use `wait=false`, then `kb_get`. |
