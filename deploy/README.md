# deploy

Deployment tooling for the LiveKit Agent Platform (`lkap`): `api` + `web` as
containers on any host, `agent` to LiveKit Cloud. Owned by **W2-DEPLOY**
(docs/IMPLEMENTATION_PLAN.md). Contracts: docs/CONTRACTS.md §3 (env vars),
§12 (commands/ports/deployment); architecture: docs/ARCHITECTURE.md §14;
binding decisions/order-of-operations that supersede both where they
differ: docs/DECISIONS-W2.md (D-W2-6, D-W2-9f) and docs/LIVE_TEST_PLAN.md
(§A1 bring-up order, §A0 preconditions).

No secret lives in this repo. Every real value goes in a human-created file
this tooling only *reads*: `deploy/api.env`, `deploy/web.env` (compose),
`agent/secrets.env` (LiveKit Cloud). Copy the matching `*.env.example`
template and fill it in yourself — Claude/agents never create or read
`.env*`/`secrets.env` files.

---

## 1. api + web → any container host (docker compose)

One-time, from `livekit_agent_platform/`:

```bash
cp deploy/api.env.example deploy/api.env   # fill in real values (see file)
cp deploy/web.env.example deploy/web.env
```

`deploy/api.env` needs `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`,
`LKAP_MASTER_KEY` (generate with `cd api && uv run python -m lkap_api.keys
generate`), `LKAP_ADMIN_TOKEN`, `LKAP_SERVICE_TOKEN` — pick your own values
for the last two, they're bearer tokens this stack mints and checks, not
vendor keys. Full field list and meaning: docs/CONTRACTS.md §3.

Build, migrate, run:

```bash
docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml run --rm api alembic upgrade head   # first boot, and after any schema change
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml logs -f
```

The api does **not** auto-migrate (by design — W1-API-CORE); the
`alembic upgrade head` step above is not optional on first boot.

### NEXT_PUBLIC_API_BASE_URL — read this before you deploy for real

Next.js inlines `NEXT_PUBLIC_API_BASE_URL` into the JS bundle *and* into the
server-side console proxy (`web/src/app/api/console/[...path]/route.ts`) at
**build** time, not run time. One value has to work for both the visitor's
browser and the web container's own outgoing requests, so in a real
deployment it must be the api's **public HTTPS origin** (e.g.
`https://api.example.com`), reachable from anywhere — not a Docker-internal
hostname:

```bash
NEXT_PUBLIC_API_BASE_URL=https://api.example.com \
  docker compose -f deploy/docker-compose.yml build web
```

Put api and web behind a reverse proxy (Caddy/Nginx/Traefik/your host's LB)
on their own public hostnames with TLS; CORS on the api is controlled by
`LKAP_CORS_ORIGINS` in `deploy/api.env`.

**Local, single-machine smoke test only** (no public hostname yet):
`deploy/docker-compose.yml` ships with `NEXT_PUBLIC_API_BASE_URL` defaulted
to `http://localhost:8080` and `web` set to `network_mode: "service:api"` —
web shares api's network namespace so `localhost:8080` resolves correctly
from *both* the host browser and the web container's server-side proxy
(the alternative, `web` on its own network calling `http://localhost:8080`,
would hit the web container's own loopback, not api). This is why `web` has
no `ports:` of its own and 3000 is published from the `api` service instead
— a Compose requirement of sharing a network namespace, not a statement
about which service serves the app. Remove `network_mode` and give `web`
its own `ports: ["3000:3000"]` once you switch to a real public hostname.

### Volumes, workers, health

- `LKAP_DATA_DIR=/data` inside the container, backed by the named volume
  `api-data` (SQLite + LanceDB). Back this volume up; it's the only state.
- `uvicorn --workers 1` — LanceDB is single-writer; do not raise this.
- `api` has a container healthcheck against `GET /v1/health`; `web` waits
  for it (`depends_on: condition: service_healthy`) before starting.

### Updating

```bash
docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml run --rm api alembic upgrade head
docker compose -f deploy/docker-compose.yml up -d
```

---

## 2. agent → LiveKit Cloud

The agent is **not** in docker-compose — LiveKit Cloud builds and runs it
from `agent/` directly via the `lk` CLI (`lk cloud auth` once, per machine).

```bash
cd livekit_agent_platform
scripts/vendor_agent_deps.sh          # see "Build-context fix" below — required every time contracts/ or packs/ change
cd agent
cp secrets.env.example secrets.env    # fill in: LKAP_API_BASE_URL, LKAP_SERVICE_TOKEN (LKAP_PACKS has a default)
lk agent create --secrets-file secrets.env    # first time only
lk agent deploy                               # every subsequent update (re-run vendor_agent_deps.sh first if contracts/packs changed)
lk agent logs                                 # tail
lk agent update-secrets --secrets KEY=VALUE   # rotate a secret without a full deploy
lk agent list                                 # confirm lkap-agent AND the untouched other-project-agent both show
```

`agent/livekit.toml` already pins `[agent] name = "lkap-agent"`; `lk agent
create` fills in the project/agent ids into that same file. `LKAP_API_BASE_URL`
must be the api's public HTTPS origin (same one used for
`NEXT_PUBLIC_API_BASE_URL` above) — LiveKit Cloud's worker calls it directly,
same as any other client. **Never** pass `other-project-agent`'s id/secrets to
any `lk agent` command here; the two are independent deployments on the
same LiveKit Cloud project. Do **not** put `LIVEKIT_URL`/`LIVEKIT_API_KEY`/
`LIVEKIT_API_SECRET`/`LIVEKIT_AGENT_NAME` in `secrets.env` — LiveKit Cloud
injects the first three for you and the fourth comes from `livekit.toml`
(docs/DECISIONS-W2.md §D-W2-11, superseding D-W2-9f: the worker registers
as `lkap-agent` in code — `rtc_session(agent_name="lkap-agent",
on_request=only_lkap_jobs)` — so it can never take automatic dispatch in the
shared project; `LIVEKIT_AGENT_NAME`/`LIVEKIT_AGENT_NAME_OVERRIDE` are
optional and only refused when set to something else, and any job not
dispatched to `lkap-agent` is rejected).

### Build-context fix (`agent/pyproject.toml`'s path deps)

`lk agent create`/`lk agent deploy` build from the directory containing
`livekit.toml` (`agent/`) — verified: it takes a single `[working-dir]`
argument, no separate build-context flag. `agent/pyproject.toml` depends on
`lkap-contracts` and `lkap-packs` as **local path** deps (`../contracts`,
`../packs`), which that build can't see.

Fix: `scripts/vendor_agent_deps.sh` builds real wheels for both packages
into `agent/vendor/*.whl` (gitignored — regenerate, don't commit).
`agent/Dockerfile` installs everything else normally from `agent/uv.lock`,
explicitly skipping those two path entries (`uv sync --no-install-package
lkap-contracts --no-install-package lkap-packs` — confirmed this does not
touch `../contracts`/`../packs` at all), then installs the two vendored
wheels directly (`uv pip install --no-deps vendor/*.whl`). Run the script
before every build/deploy where `contracts/` or `packs/` changed since the
last one — the Dockerfile refuses to build with a clear error if
`agent/vendor/*.whl` is missing.

Local build (no `lk`, just Docker):

```bash
scripts/vendor_agent_deps.sh
docker build -f agent/Dockerfile -t lkap-agent agent
```

(`lk agent dockerfile`, LiveKit's own Dockerfile generator, was checked as
an alternative: it requires the target agent to already be registered with
the project — `project does not match agent subdomain` otherwise — so it
can't produce a template ahead of the first `lk agent create`; not usable
here.)

---

## 3. Local dev without Docker

`scripts/dev.sh` (repo root) starts api + agent + web together, matching
docs/CONTRACTS.md §12 and docs/LIVE_TEST_PLAN.md §A1's bring-up order: api
first (`alembic upgrade head` then `uvicorn`), agent worker second, web
third; all three are cleaned up together on exit or Ctrl+C. It reads
secrets from your shell environment — it never creates or reads `.env*`:

```bash
export LIVEKIT_URL=wss://your-project.livekit.cloud
export LIVEKIT_API_KEY=...
export LIVEKIT_API_SECRET=...
export LKAP_MASTER_KEY=...   # cd api && uv run python -m lkap_api.keys generate
scripts/dev.sh
```

`LIVEKIT_AGENT_NAME`/`LKAP_AGENT_NAME` default to `lkap-agent` inside the
script, but neither is required: the worker registers as `lkap-agent` by
construction (`rtc_session(agent_name="lkap-agent", on_request=only_lkap_jobs)`,
docs/DECISIONS-W2.md D-W2-11, superseding D-W2-9f), so it can never take
*automatic* dispatch for another room in the shared LiveKit Cloud project,
including `other-project-agent`'s, whatever env is or isn't set. The env vars are
now only a belt: if set, `LIVEKIT_AGENT_NAME`/`LIVEKIT_AGENT_NAME_OVERRIDE`
must equal `lkap-agent` or the worker refuses to start (a mismatch means it
was launched from another agent's env), and a job request whose
`agent_name` isn't `lkap-agent` is rejected at the SDK's `on_request` hook.
After starting, confirm the guard held: the agent's first log lines should
show `agent_name=lkap-agent` (LIVE_TEST_PLAN §A1 step 2) — if a registration
line appears with no agent name, stop it immediately.
Since livekit-agents 1.8.3 the worker also logs the warning "agent_name is set in code; move it to livekit.toml …" at startup. This is expected. The name stays in code on purpose (R-V4-56), so don't move it.

`.claude/launch.json` (outside this repo, at the `Insurance_live_Agent/`
root) has equivalent `lkap-api`, `lkap-web` and `lkap-agent` entries for
Claude-driven dev, each exporting its env inline (the same LiveKit values
already in `lkap-api`, plus `LIVEKIT_AGENT_NAME=lkap-agent`,
`LKAP_API_BASE_URL=http://127.0.0.1:8080`, `LKAP_SERVICE_TOKEN=dev-service`
— byte-identical to `lkap-api`'s) and none touching `other-project-agent`.
`lkap-agent`'s entry deliberately has **no `port`** — unlike `lkap-api`/
`lkap-web`, the worker doesn't serve HTTP, so opening it with this host's
preview tool would try (and fail) to load a browser tab against a port
nothing listens on. Run it as a plain background command instead (this is
exactly what `scripts/dev.sh` does; the launch entry exists so its command
+ env are recorded in one place, matching the other two).

---

## 4. v2: images, dev/prod compose, supervisor (V2-09)

Additive to sections 1–3 above, which still work unmodified for a v1-only
checkout. See `docs/v2/CONTRACTS-V2.md` §7 (images/manifest), §5
(supervisor), §6 (env vars) and `docs/v2/PLAN-V2.md` §8 R-V2-1 (why the four
slim-shipped VAD/turn-detector ids don't come from `worker_image` alone) for
the binding design; this section is operational, not a second spec.

### Worker images (`slim` / `full`)

```bash
scripts/vendor_agent_deps.sh   # as in §2 above — still required first, every time
contracts/.venv/bin/python scripts/gen_plugin_requirements.py   # regenerate agent/requirements/*.txt after any registry change; --check for CI

docker build --build-arg LKAP_IMAGE_FLAVOR=slim -f agent/Dockerfile -t lkap-agent:slim agent
docker build --build-arg LKAP_IMAGE_FLAVOR=full -f agent/Dockerfile -t lkap-agent:full agent   # several GB; several native wheels (bithuman, Krisp, Azure Speech, awscrt) may need fixing — the import check names exactly which
```

Either build writes `/app/installed_providers.json` (the import-check
manifest) into the image; inspect it with
`docker run --rm --entrypoint cat lkap-agent:slim cat installed_providers.json`
(actually `docker create` + `docker cp`, since there's no shell entrypoint —
see `.github/workflows/docker.yml`'s own extraction step for the exact
commands). A non-empty `"failed"` array fails the build — CONTRACTS-V2 §7's
"the build is the gate", not a post-hoc report.

### supervisor image

```bash
docker build -f supervisor/Dockerfile -t lkap-supervisor .   # from livekit_agent_platform/ — root context, like api/Dockerfile
docker build -f mcp/Dockerfile -t lkap-mcp .   # from livekit_agent_platform/ — root context, like supervisor
```

### dev / prod compose

```bash
# dev: sqlite + local storage by default; add postgres/redis/minio profiles as needed
cp deploy/api.env.example deploy/api.env
cp deploy/web.env.example deploy/web.env
cp deploy/supervisor.env.example deploy/supervisor.env
docker compose -f deploy/docker-compose.dev.yml up --build
docker compose -f deploy/docker-compose.dev.yml run --rm api alembic upgrade head

# prod: api x2 behind Caddy, web, supervisor, postgres, redis, minio
cp deploy/prod.env.example deploy/prod.env   # LKAP_PUBLIC_DOMAIN, LKAP_POSTGRES_PASSWORD, LKAP_STORAGE_*, LKAP_DOCKER_GID
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/prod.env up -d --build
```

Read the header comments in `deploy/docker-compose.dev.yml` and
`deploy/docker-compose.prod.yml` before running either — in particular the
prod file's LanceDB/SQLite single-writer caveat under `--scale api=2`, and
the `--forwarded-allow-ips` note on why `api`/`jobs` publish no host ports in
prod (Caddy is the only ingress; see `api/Dockerfile`'s own comment on the
same topic). `deploy/Caddyfile` is the reverse-proxy config; its own header
comment covers the untested multi-replica load-balancing caveat.

### Backups

```bash
LKAP_BACKUP_S3_BUCKET=lkap-backups scripts/backup.sh   # pg_dump (if Postgres is configured) + LKAP_DATA_DIR tar, both to S3
```
Needs the `aws` CLI on whatever host runs it (not installed by any image
here) and `pg_dump` for the Postgres leg (skipped with a warning, not a
failure, on a sqlite-only deployment). See the script's own header for every
env var it reads and the restore commands it prints.
