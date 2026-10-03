# LKAP — architecture, deployment and handoff (2026-10-03)

Read this first if you are a new engineer or a new Claude Code session taking over LKAP. It explains what the system is, how a call flows through it, how it is deployed (the reference deployment runs on one NVIDIA DGX Spark), how to operate it, and what is still open. Deeper references: `docs/ARCHITECTURE.md` → `docs/v6/ARCHITECTURE-V6.md` (feature-level design), `docs/RUNBOOK.md` (every setting and procedure), `docs/CONTRACTS.md` (data contracts), `docs/v6/PLAN-V6.md` (plan, status table, rulings), `docs/v6/_asks.md` (open items). This file uses placeholders for hostnames and never contains secrets; the deployment's private notes live next to its compose file (see §5.6).

---

## 1. What LKAP is

A generic platform for building, running and observing voice/video AI agents on LiveKit. Builders configure agents in a web console (or through Claude Code via the MCP integration): instructions, models (speech-to-text, language model, text-to-speech, realtime, avatars, image generation), tools, knowledge bases, lookup tables, panels the caller sees (notebook, drawing board, charts, cart, signature, forms…), flows, captured details and rules, guardrails, tests. Callers talk to agents from a web page, an embed widget, or the phone. Insurance claims intake was the first use case; it is now a starter template built only from generic parts.

## 2. Architecture

### 2.1 Components (repo folder → deployed service)

| Folder | What it is | Deployed as |
|---|---|---|
| `agent/` | **The agent logic**: LiveKit Agents worker: voice loop (VAD → STT → turn detection → LLM → TTS), tools, panels protocol, flows, extraction/rules, guardrails | **Worker** containers, one set per LiveKit connection, scaled by simultaneous calls |
| `api/` | **Control plane** (FastAPI): workspaces/users, agents and versions, provider keys (encrypted), knowledge bases, lookup tables, tools, kits, sessions, tokens and dispatch, webhooks, tests, costs | **api** service (+ a **jobs** runner from the same image in larger deployments) |
| `web/` | Console for builders, public call page `/s/<slug>`, embed widget (Next.js) | **web** service |
| `contracts/` | Shared Pydantic models, provider registry, schemas (TS + JSON generated) | Library in api and worker images |
| `packs/` | Use-case packs (`generic`, legacy `insurance_claim`) | Library in api and worker images |
| `mcp/` | MCP server so Claude Code etc. can build/operate agents | Optional **mcp** service |
| `supervisor/` | Starts/stops worker containers for "Supervised" connections | Optional **supervisor** service |

### 2.2 System diagram

```
Builders ─► web (console) ─┐                         ┌─ Postgres/SQLite (all data)
Callers  ─► web (/s/slug) ─┼─► api (FastAPI) ────────┼─ Redis (queues, rate limits; optional on SQLite)
                           │      │   ▲              └─ object storage (recordings, uploads; local dir or S3/MinIO)
                           │      │   │ config, tools, knowledge, results (HTTP + service token)
                           │      │ dispatch + tokens
                           ▼      ▼   │
                     ┌────────────────────┐   audio/video   ┌──────────────────────────┐
  caller ◄──────────►│ LiveKit server     │◄───────────────►│ agent workers (agent/)    │──► AI providers
                     │ Cloud or self-host │                 │ one set per connection    │    (Deepgram, OpenRouter,
                     └────────────────────┘                 └──────────────────────────┘     LiveKit Inference…)
```

- **LiveKit server**: moves packets only; runs no AI. LKAP can manage several ("connections"), each Cloud or self-hosted.
- **api**: never in the audio path. Mints the caller's token, asks LiveKit to dispatch the agent, serves config to workers, runs tools/knowledge/lookups on their behalf, stores transcripts/costs/results.
- **Workers**: long-running processes that register with one LiveKit server under that connection's agent name. Each call is a job → a separate agent process that joins the room and runs the conversation. VAD (Silero) and the LiveKit turn detector run **inside** the worker (CPU); STT/LLM/TTS are mostly external APIs. One generic image serves every agent; per-agent behaviour comes from the api at call start.

### 2.3 Call flow

1. Caller opens `/s/<slug>` → web asks api `POST /v1/agents/<slug>/connect` → api checks access, creates the session, mints the caller token, dispatches agent name X to room R (and fails fast with `no_worker_running` if no worker of that connection is alive).
2. LiveKit gives the job to a worker registered as X → worker resolves the session from the api (`/internal/v1/...`, service token) → builds the pipeline from the agent's config → joins the room.
3. Turn loop: Silero VAD (speech/silence) → STT (e.g. Deepgram Flux, which also decides end of turn) → if the STT can't end turns: LiveKit turn detector (hosted on Cloud; local in the worker on self-hosted) → LLM (with tools, panels, knowledge, rules) → TTS → audio back.
4. End: worker posts transcript, usage/costs, latency metrics, captured details; api stores them, runs QA/webhooks.

### 2.4 Current model setup on the demo agents

- **Listens + ends turns**: Deepgram Flux (`deepgram-flux-stt`, `flux-general-en`), Deepgram key.
- **Thinks**: `openai/gpt-6-luna` via OpenRouter, reasoning effort automatic (= lowest, `none`).
- **Speaks**: Deepgram Aura-2 (`aura-2-thalia-en`), same Deepgram key (one key per vendor family, V6-32).
- **Images**: OpenRouter image generation. OpenRouter is used for LLM, images and embeddings only (its STT/TTS are not streaming).
- 7 demo agents on the LiveKit Cloud connection (avatars, phone, flagship demos), 8 on the self-hosted connection.

Measured (V6): reply audio starts ≈2.7 s after the caller stops on Cloud with Deepgram; worker-side LLM first token ≈0.25 s and TTS first byte ≈0.3 s on the DGX. Details: `docs/v6/_briefs/v6-model-latency.md`.

## 3. What has been built (by plan)

- **v1–v2**: generic platform from the insurance demo: packs, provider registry for every LiveKit plugin, cascaded/realtime/half-cascade pipelines, console, multiple LiveKit connections, workers per connection, supervisor, deploy bundles.
- **v3–v4**: MCP server + Claude Code skill/plugin, starter templates, demo population via headless Claude Code, phone numbers/SIP, knowledge bases, tools (HTTP/MCP/Composio), webhooks, QA.
- **v5**: generic panels/requests, memory, flows, guardrails, tests runner, pgvector, security review.
- **v6** (closed 2026-09-29, R-V6-5): speech registry truth and streaming stacks, OpenRouter pricing, notebook/drawing board/ink, five new blocks (signature, chart, timer, code, cart), tool context/bindings, extraction + rules, lookup tables, tool kits, flow tool step, claims-intake starter, avatar framing, connection agent-name clashes + worker binding (V6-27), two security reviews + fixes (V6-21, V6-29); then V6-30…33: demo-quality fixes, reasoning-model awareness (`reasoning_effort`, unsupported parameters dropped), one key per vendor family + truthful credentials page, console "what each part does" labels and pipeline summary.

## 4. How to build and test (development)

`uv sync` in each Python package (`contracts testing agent api mcp packs`), `pnpm install --frozen-lockfile` in `web/`. Gates per package: `uv run ruff check . && uv run ruff format --check . && uv run mypy src/ --strict && uv run pytest -q` (api: `-n auto`); web: `pnpm exec tsc --noEmit && pnpm lint && pnpm exec vitest run`. After a contract change: `bash scripts/export_contracts.sh --generate`; after an MCP tool schema change: `cd mcp && uv run pytest -q tests/test_catalog_snapshot.py --snapshot-update`. Process rules: `docs/v6/PLAN-V6.md` §0.

## 5. Reference deployment: one DGX Spark (arm64, 20 cores, 128 GB, Ubuntu)

### 5.1 What runs where

| Service | How | Listens | Published |
|---|---|---|---|
| LiveKit server 1.13.7 (+ ingress, egress, SIP, redis) | pre-existing compose project `livekit-platform` | `127.0.0.1:7880` | Tailscale Serve `https://<dgx-host>:7443` (tailnet only) |
| LKAP api | `lkap-api:dgx`, compose project `lkap` | `127.0.0.1:8080` | Tailscale Serve `https://<dgx-host>:8446` (tailnet only) |
| LKAP web | `lkap-web:dgx` (built with `NEXT_PUBLIC_API_BASE_URL=https://<dgx-host>:8446`) | `127.0.0.1:3300` | Tailscale Serve `https://<dgx-host>:8447` (tailnet only) |
| worker-cloud | `lkap-agent:dgx`, agent name `lkap-agent`, connection "Default" (LiveKit Cloud) | outbound only | — |
| worker-dgx | `lkap-agent:dgx`, agent name `lkap-dgx`, connection "DGX-LivekitServer", `LIVEKIT_URL=ws://127.0.0.1:7880` | outbound only | — |
| Data | SQLite + LanceDB + files in a bind-mounted dir (`/data` in the api container) | — | — |

All LKAP containers use host networking and bind to `127.0.0.1`; HTTPS comes from `tailscale serve` (required: browsers only allow the microphone on HTTPS). Never bind or serve on the host's Tailscale **Funnel** ports (public internet).

### 5.2 Configuration (names only; values in `~/.config/lkap/*.env`, mode 0600)

- `api.env`: `LIVEKIT_URL/API_KEY/API_SECRET` (the Cloud project, seeds the default connection), `LKAP_MASTER_KEY` (encrypts every stored provider key; **must be the same key the data was written with**), `LKAP_ADMIN_TOKEN`, `LKAP_SERVICE_TOKEN`, `LKAP_SESSION_SECRET`, `LKAP_AGENT_NAME=lkap-agent`, `LKAP_PACKS=packs.insurance_claim,packs.generic` (must match the workers), `LKAP_DATA_DIR=/data`, `LKAP_API_BASE_URL=http://127.0.0.1:8080` (worker callback), `LKAP_WEB_BASE_URL` and `LKAP_CORS_ORIGINS` = the web origin, `LKAP_LOG_JSON=true`, `PORT=8080`. `LKAP_ENV` is left at its default (`dev`) to match the original instance (the console proxy uses the admin token in dev); move to `prod` together with real logins when exposing beyond the tailnet.
- `web.env`: `LKAP_ADMIN_TOKEN` (same value), `PORT=3300`, `HOSTNAME=127.0.0.1`. `build.env`: `NEXT_PUBLIC_API_BASE_URL` (baked in at build time).
- `worker-cloud.env`: `LIVEKIT_*` (Cloud), `LKAP_AGENT_NAME`/`LIVEKIT_AGENT_NAME=lkap-agent`, `LKAP_CONNECTION_ID=<default connection id>`, `LKAP_API_BASE_URL`, `LKAP_SERVICE_TOKEN`, `LKAP_PACKS`, `LKAP_LOG_LEVEL`, `LKAP_WORKER_HTTP_PORT=0`.
- `worker-dgx.env`: generated from the api: `GET /internal/v1/connections/<id>/worker-env` (header `X-Service-Token`) returns the decrypted env of a connection's worker; then override `LIVEKIT_URL=ws://127.0.0.1:7880`, `LKAP_API_BASE_URL=http://127.0.0.1:8080`, `LKAP_WORKER_HTTP_PORT=0`.

### 5.3 Build (on the target, natively: the images are multi-arch)

```bash
cd <checkout>
docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -v $PWD:/w -w /w \
  ghcr.io/astral-sh/uv:python3.12-bookworm-slim bash scripts/vendor_agent_deps.sh   # contracts+packs wheels for the agent image
docker build -f api/Dockerfile -t lkap-api:dgx .
docker build -f agent/Dockerfile --build-arg LKAP_IMAGE_FLAVOR=slim -t lkap-agent:dgx agent   # bakes Silero + turn detector
docker build -f web/Dockerfile --build-arg NEXT_PUBLIC_API_BASE_URL=https://<dgx-host>:8446 -t lkap-web:dgx web
```

### 5.4 Run, migrate, verify

```bash
docker compose -f <deploy-dir>/compose.yml up -d api
docker compose -f <deploy-dir>/compose.yml exec -T -w /app/api api alembic upgrade head   # first boot and after schema changes
docker compose -f <deploy-dir>/compose.yml up -d web worker-cloud worker-dgx
tailscale serve --bg --https=8446 http://127.0.0.1:8080
tailscale serve --bg --https=8447 http://127.0.0.1:3300
```
Verify: `curl https://<dgx-host>:8446/v1/health` → 200; web loads on `:8447`; `docker compose logs worker-cloud|worker-dgx` show `registered worker` with the right agent name; the console's Connections page shows a ready worker on each; one voice call per connection. The api container runs as the data dir's owner (`user: "1000:1000"`, `HOME=/tmp`).

### 5.5 Operate

- Logs: `docker compose -f <deploy-dir>/compose.yml logs -f <service>`. Restart: `... restart <service>`. Workers drain on SIGINT (configured).
- Update: `git pull` → rebuild images (5.3) → `up -d api` → `alembic upgrade head` → `up -d web worker-cloud worker-dgx`. Rebuild web whenever `NEXT_PUBLIC_API_BASE_URL` changes.
- Backup: the data dir (stop the api or use `sqlite3 lkap.db ".backup …"`), plus `~/.config/lkap/` (secrets; store separately and securely).
- **One instance only**: never run workers with the same agent names anywhere else (calls would be split between instances).

### 5.6 Private notes

The deployment keeps a private notes file beside its compose file (real hostnames, paths, connection ids, where the data came from). It is not in the repo.

## 6. Moving the reference deployment elsewhere (checklist)

1. A LiveKit server (Cloud project, or self-hosted with TLS/UDP/TURN).
2. A host or cluster for api + web (2–4 CPU), HTTPS in front (Tailscale Serve, Caddy, or a load balancer).
3. Workers next to each LiveKit server (≈4 CPU / 8 GB per ~10–25 simultaneous calls with external STT/LLM/TTS), or LiveKit Cloud Agents for the Cloud connection (`lk agent deploy`, see `deploy/README.md` §2).
4. Data: SQLite + local dir (single node) **or** Postgres with pgvector + Redis + S3-compatible storage (`deploy/docker-compose.prod.yml`), see §7.
5. Secrets: master key (carry it with the data), admin/service/session tokens (new per deployment), LiveKit keys per connection, provider keys (stored encrypted in LKAP, added in the console).
6. Build the three images, run migrations, start api → web → workers, verify as in 5.4.
7. AI provider accounts: Deepgram (speech), OpenRouter (LLM, images, embeddings); optional LiveKit Inference, avatar vendors, Composio.

## 7. Open work for the next session

1. **Phase 2 data stores on the DGX**: run LKAP's own Postgres 16 + pgvector, Redis and MinIO as extra services in the `lkap` compose project (isolated network/ports; don't reuse other projects' databases), then copy the SQLite data into Postgres (all tables in foreign-key order via the api's SQLAlchemy models; JSON columns; re-embed or copy LanceDB vectors into `kb_vectors`), switch `LKAP_DATABASE_URL`/`LKAP_REDIS_URL`/storage settings, verify, keep the SQLite copy as a backup. RUNBOOK §9 covers pgvector and storage settings. No migration tool exists yet; write it as a package with tests.
2. **Tailscale direct connection**: Mac ↔ DGX currently relays via DERP; allow UDP 41641 on the DGX side.
3. Open asks in `docs/v6/_asks.md` (Open at close + #296…#325), notably #178 (workers report server + agent name), #180 (outbound calls fail fast), #296 (reasoning text spoken by some models), #305 (catalog refresh on miss).
4. Noise cancellation is not installed in the worker yet (LiveKit's is Cloud-only; ai-coustics works self-hosted).
5. Live checks never run: `docs/v6/PLAN-V6.md` §5.1.

## 8. Rules that carry over

- Never commit, print or paste secrets (keys, tokens, master key); env files stay 0600 outside the checkout. The repo is public: no personal hostnames or paths in committed files.
- Back up the database before every migration.
- Exactly one worker set per agent name.
- Console UI: dialogs only (no side drawers), plain words, no internal jargon.
- Contracts-first commits; run every package's gates before merging.
