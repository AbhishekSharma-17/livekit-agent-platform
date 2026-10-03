# LKAP architecture, deployment and handoff (2026-10-03)

Read this first if you are a new engineer or a new Claude Code session taking over LKAP. It explains what the system is and how a call flows through it. It also covers how the system is deployed (the reference deployment runs on one NVIDIA DGX Spark), how to operate it, and what is still open.

Deeper references:
- `docs/ARCHITECTURE.md` and `docs/v6/ARCHITECTURE-V6.md` for the feature-level design.
- `docs/RUNBOOK.md` for every setting and procedure.
- `docs/CONTRACTS.md` for the data contracts.
- `docs/v6/PLAN-V6.md` for the plan, the status table and the rulings.
- `docs/v6/_asks.md` for open items.
- `docs/ui/DESIGN-SYSTEM.md` for how the web console looks and reads.

This file uses placeholders for hostnames and never contains secrets. The deployment's private notes live next to its compose file (see §5.6).

---

## 1. What LKAP is

LKAP is a generic platform for building, running and observing voice and video AI agents on LiveKit. Builders configure agents in a web console, or through Claude Code with the MCP integration. They set:
- instructions
- models (speech-to-text, language model, text-to-speech, realtime, avatars, image generation)
- tools
- knowledge bases and lookup tables
- the panels the caller sees (notebook, drawing board, charts, cart, signature, forms and more)
- flows, captured details and rules
- guardrails and tests

Callers talk to agents from a web page, an embed widget or the phone. Insurance claims intake was the first use case. It is now a starter template built only from generic parts.

## 2. Architecture

### 2.1 Components (repo folder to deployed service)

| Folder | What it is | Deployed as |
|---|---|---|
| `agent/` | **The agent logic.** A LiveKit Agents worker with the voice loop (VAD, STT, turn detection, LLM, TTS), tools, the panels protocol, flows, extraction and rules, and guardrails | **Worker** containers, one set per LiveKit connection, scaled by simultaneous calls |
| `api/` | **Control plane** (FastAPI). Workspaces and users, agents and versions, provider keys (encrypted), knowledge bases, lookup tables, tools, kits, sessions, tokens and dispatch, webhooks, tests, costs | **api** service (plus a **jobs** runner from the same image in larger deployments) |
| `web/` | Console for builders, public call page `/s/<slug>`, embed widget (Next.js) | **web** service |
| `contracts/` | Shared Pydantic models, provider registry, schemas (TS and JSON generated) | Library in the api and worker images |
| `packs/` | Use-case packs (`generic`, legacy `insurance_claim`) | Library in the api and worker images |
| `mcp/` | MCP server so Claude Code and similar tools can build and operate agents | Optional **mcp** service |
| `supervisor/` | Starts and stops worker containers for "Supervised" connections | Optional **supervisor** service |

### 2.2 System diagram

```
Builders ─► web (console) ─┐                         ┌─ Postgres/SQLite (all data)
Callers  ─► web (/s/slug) ─┼─► api (FastAPI) ────────┼─ Redis (queues and rate limits, optional on SQLite)
                           │      │   ▲              └─ object storage (recordings and uploads, local dir or S3/MinIO)
                           │      │   │ config, tools, knowledge, results (HTTP + service token)
                           │      │ dispatch + tokens
                           ▼      ▼   │
                     ┌────────────────────┐   audio/video   ┌──────────────────────────┐
  caller ◄──────────►│ LiveKit server     │◄───────────────►│ agent workers (agent/)    │──► AI providers
                     │ Cloud or self-host │                 │ one set per connection    │    (Deepgram, OpenRouter,
                     └────────────────────┘                 └──────────────────────────┘     LiveKit Inference…)
```

- **LiveKit server.** It moves packets only and runs no AI. LKAP can manage several servers ("connections"), each on Cloud or self-hosted.
- **api.** It is never in the audio path. It mints the caller's token, asks LiveKit to dispatch the agent, and serves config to workers. It also runs tools, knowledge and lookups on their behalf, and stores transcripts, costs and results.
- **Workers.** These are long-running processes that register with one LiveKit server under that connection's agent name. Each call is a job, which is a separate agent process that joins the room and runs the conversation. VAD (Silero) and the LiveKit turn detector run **inside** the worker (CPU). STT, LLM and TTS are mostly external APIs. One generic image serves every agent, and the api supplies each agent's behaviour at call start.

### 2.3 Call flow

1. The caller opens `/s/<slug>`. The web app asks the api (`POST /v1/agents/<slug>/connect`). The api checks access, creates the session, mints the caller token and dispatches agent name X to room R. It fails fast with `no_worker_running` if no worker of that connection is alive.
2. LiveKit gives the job to a worker registered as X. The worker resolves the session from the api (`/internal/v1/...`, service token), builds the pipeline from the agent's config and joins the room.
3. The turn loop runs in this order.
   - Silero VAD detects speech and silence.
   - STT transcribes. Deepgram Flux also decides when the turn ends.
   - If the STT cannot end turns, the LiveKit turn detector does it (hosted on Cloud, local in the worker when self-hosted).
   - The LLM answers, with tools, panels, knowledge and rules.
   - TTS turns the answer into audio and sends it back.
4. At the end, the worker posts the transcript, usage and costs, latency metrics and captured details. The api stores them and runs QA and webhooks.

### 2.4 Current model setup on the demo agents

- **Listens and ends turns.** Deepgram Flux (`deepgram-flux-stt`, `flux-general-en`) with the Deepgram key.
- **Thinks.** `openai/gpt-6-luna` through OpenRouter, with reasoning effort on automatic (which means the lowest, `none`). Three demo agents (the blank, phone and self-hosted check agents) use `gpt-oss-120b` instead, which starts replying faster.
- **Speaks.** Deepgram Aura-2 (`aura-2-thalia-en`) with the same Deepgram key. There is one key per vendor family (V6-32).
- **Turn taking.** Every demo agent uses the "fast" conversation preset with sticky routing (V6-34, see §3).
- **Images.** OpenRouter image generation. OpenRouter is used for LLM, images and embeddings only, because its STT and TTS do not stream.
- **Demo data.** All demo names start with "Demo ·" (for example "Demo · Policies"). There are 15 agents, 10 knowledge bases and 4 lookup tables. Seven agents run on the LiveKit Cloud connection (avatars, phone, flagship demos) and eight on the self-hosted connection.

Measured with the fast preset (details in `docs/v6/_briefs/v6-model-latency.md`, with the design in `docs/research-v6/low-latency-stack.md`):
- On Cloud with Deepgram, reply audio starts about 2.2 to 2.7 s after the caller stops when the model is GPT-6 Luna.
- With `gpt-oss-120b` on Groq or Cerebras, reply audio starts about 2.0 to 2.5 s after the caller stops (best single run 1.4 s).
- On the DGX, the worker's LLM first token is about 0.25 s and the TTS first byte about 0.3 s.

## 3. What has been built (by plan)

- **v1 and v2.** The generic platform from the insurance demo. Packs, a provider registry for every LiveKit plugin, cascaded, realtime and half-cascade pipelines, the console, multiple LiveKit connections, workers per connection, the supervisor and deploy bundles.
- **v3 and v4.** The MCP server with a Claude Code skill and plugin, starter templates, demo population through headless Claude Code, phone numbers and SIP, knowledge bases, tools (HTTP, MCP, Composio), webhooks and QA.
- **v5.** Generic panels and requests, memory, flows, guardrails, the tests runner, pgvector and a security review.
- **v6** (closed 2026-09-29, R-V6-5).
  - Speech registry truth and streaming stacks, and OpenRouter pricing.
  - Notebook, drawing board and ink, plus five new blocks (signature, chart, timer, code, cart).
  - Tool context and bindings, extraction and rules, lookup tables, tool kits, the flow tool step and the claims-intake starter.
  - Avatar framing, connection agent-name clashes and worker binding (V6-27).
  - Two security reviews with fixes (V6-21, V6-29).
  - V6-30 to V6-33: demo-quality fixes, reasoning-model awareness (`reasoning_effort`, unsupported parameters dropped), one key per vendor family with a truthful credentials page, and console "what each part does" labels with a pipeline summary.
- **After v6 closed.**
  - **V6-34, low-latency turn taking.** The new "fast" conversation preset. With a Flux transcriber it uses Flux end of turn (`eot_threshold` 0.75, `eager_eot_threshold` 0.4, `eot_timeout_ms` 3000), `min_delay` 0.1, preemptive generation and preemptive TTS. With a turn detector it uses `min_delay` 0.3 and preemptive generation. The OpenRouter LLM also gained `sticky_routing`, which keeps each agent's requests on one provider so the prompt cache stays warm. Measured gains are in §2.4.
  - **V6-35, connected account identity.** A Composio connected account now shows who it is signed in as (an address, a user name or a workspace name) wherever the account appears.
  - **V6-36, current Composio integration.** The API version is v3.1 everywhere, and an owner check confirms a connected account belongs to the subject that asked for it (it fails closed). Details are in `docs/v5/COMPOSIO.md` ("V6-36 changes").
  - **UI redesign, UI-R1 to UI-R5.** The web console, sign-in, public pages and caller page follow `docs/ui/DESIGN-SYSTEM.md`, with tokens in `docs/ui/TOKENS.md`.
    - Colours are oklch tokens in two themes, with an indigo brand accent (UI-R1).
    - Plain-copy passes cover the backend messages (UI-R2a) and the web (UI-R2b).
    - The sign-in page has an animated showcase and the sidebar collapses (UI-R3).
    - Official vendor logos come from one pipeline (`docs/ui/VENDOR-MARKS.md`) (UI-R4).
    - The LKAP logo, the app icons and the last four vendor logos are in (UI-R5).
    - Two gates guard the look. `pnpm check:contrast` checks every colour pair in both themes and `pnpm lint:design` blocks colour literals, raw shadows, native selects, browser dialogs and em dashes in user-facing copy.

## 4. How to build and test (development)

Run `uv sync` in each Python package (`contracts testing agent api mcp packs`) and `pnpm install --frozen-lockfile` in `web/`.

Gates per Python package (for api, add `-n auto` to pytest):

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ --strict && uv run pytest -q
```

Gates for the web package:

```bash
pnpm exec tsc --noEmit && pnpm lint && pnpm lint:design && pnpm check:contrast && pnpm exec vitest run
```

After a contract change, run `bash scripts/export_contracts.sh --generate`. After an MCP tool schema change, run `cd mcp && uv run pytest -q tests/test_catalog_snapshot.py --snapshot-update`. The MCP docs lint (`cd mcp && uv run pytest -q tests/test_docs_lint.py`) must pass whenever docs change. Process rules are in `docs/v6/PLAN-V6.md` §0.

## 5. Reference deployment on one DGX Spark (arm64, 20 cores, 128 GB, Ubuntu)

### 5.1 What runs where

| Service | How | Listens | Published |
|---|---|---|---|
| LiveKit server 1.13.7 (plus ingress, egress, SIP, redis) | pre-existing compose project `livekit-platform` | `127.0.0.1:7880` | Tailscale Serve `https://<dgx-host>:7443` (tailnet only) |
| LKAP api | `lkap-api:dgx`, compose project `lkap` | `127.0.0.1:8080` | Tailscale Serve `https://<dgx-host>:8446` (tailnet only) |
| LKAP web | `lkap-web:dgx` (built with `NEXT_PUBLIC_API_BASE_URL=https://<dgx-host>:8446`) | `127.0.0.1:3300` | Tailscale Serve `https://<dgx-host>:8447` (tailnet only) |
| worker-cloud | `lkap-agent:dgx`, agent name `lkap-agent`, connection "Default" (LiveKit Cloud) | outbound only | none |
| worker-dgx | `lkap-agent:dgx`, agent name `lkap-dgx`, connection "DGX-LivekitServer", `LIVEKIT_URL=ws://127.0.0.1:7880` | outbound only | none |
| Postgres 16 with pgvector | `pgvector/pgvector:pg16`, compose project `lkap`, own bridge network | `127.0.0.1:55432` | none |
| Valkey 8 (Redis-compatible) | `valkey/valkey:8-alpine` | `127.0.0.1:56379` | none |
| SeaweedFS (S3 gateway, bucket `lkap`) | `chrislusf/seaweedfs:3.97`, `server -s3` | `127.0.0.1:58333` | Tailscale Serve `https://<dgx-host>:8448` (tailnet only), used for presigned links |
| Local data dir | the api's `/data` bind mount keeps the embedder models and the pre-cutover SQLite copy | none | none |

All LKAP containers use host networking and bind to `127.0.0.1`. HTTPS comes from `tailscale serve`, which is required because browsers only allow the microphone on HTTPS. Never bind or serve on the host's Tailscale **Funnel** ports, which face the public internet.

### 5.2 Configuration (names only, values in `~/.config/lkap/*.env`, mode 0600)

- `api.env` holds:
  - `LIVEKIT_URL/API_KEY/API_SECRET` (the Cloud project, which seeds the default connection)
  - `LKAP_MASTER_KEY` (encrypts every stored provider key, and **must be the same key the data was written with**)
  - `LKAP_ADMIN_TOKEN`, `LKAP_SERVICE_TOKEN`, `LKAP_SESSION_SECRET`
  - `LKAP_AGENT_NAME=lkap-agent`
  - `LKAP_PACKS=packs.insurance_claim,packs.generic` (must match the workers)
  - `LKAP_DATA_DIR=/data`
  - `LKAP_API_BASE_URL=http://127.0.0.1:8080` (the worker callback)
  - `LKAP_WEB_BASE_URL` and `LKAP_CORS_ORIGINS`, both set to the web origin
  - `LKAP_LOG_JSON=true` and `PORT=8080`
  - `LKAP_ENV` is left at its default (`dev`) to match the original instance, because the console proxy uses the admin token in dev. Move to `prod` together with real logins when exposing beyond the tailnet.
- `web.env` holds `LKAP_ADMIN_TOKEN` (same value), `PORT=3300` and `HOSTNAME=127.0.0.1`. `build.env` holds `NEXT_PUBLIC_API_BASE_URL`, which is baked in at build time.
- `worker-cloud.env` holds `LIVEKIT_*` (Cloud), `LKAP_AGENT_NAME` and `LIVEKIT_AGENT_NAME=lkap-agent`, `LKAP_CONNECTION_ID=<default connection id>`, `LKAP_API_BASE_URL`, `LKAP_SERVICE_TOKEN`, `LKAP_PACKS`, `LKAP_LOG_LEVEL` and `LKAP_WORKER_HTTP_PORT=0`.
- `worker-dgx.env` is generated from the api. `GET /internal/v1/connections/<id>/worker-env` (header `X-Service-Token`) returns the decrypted env of a connection's worker. Then override `LIVEKIT_URL=ws://127.0.0.1:7880`, `LKAP_API_BASE_URL=http://127.0.0.1:8080` and `LKAP_WORKER_HTTP_PORT=0`.

### 5.3 Build (on the target, natively, because the images are multi-arch)

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

To verify:
- `curl https://<dgx-host>:8446/v1/health` returns 200.
- The web app loads on `:8447`.
- `docker compose logs worker-cloud|worker-dgx` shows `registered worker` with the right agent name.
- The console's Connections page shows a ready worker on each connection.
- One voice call works on each connection.

The api container runs as the data dir's owner (`user: "1000:1000"`, `HOME=/tmp`).

### 5.5 Operate

- **Logs.** `docker compose -f <deploy-dir>/compose.yml logs -f <service>`. Restart with `... restart <service>`. Workers drain on SIGINT, which is already configured.
- **Update.** Run `git pull`, rebuild the images (5.3), `up -d api`, `alembic upgrade head`, then `up -d web worker-cloud worker-dgx`. Rebuild web whenever `NEXT_PUBLIC_API_BASE_URL` changes.
- **Backup.** Back up the data dir (stop the api first, or use `sqlite3 lkap.db ".backup …"`). Also back up `~/.config/lkap/`, which holds secrets, and store it separately and securely.
- **One instance only.** Never run workers with the same agent names anywhere else, because calls would be split between instances.

### 5.6 Private notes

The deployment keeps a private notes file beside its compose file. It holds real hostnames, paths, connection ids and where the data came from. It is not in the repo.

### 5.7 Data stores (since 2026-10-03)

The DGX moved from SQLite, LanceDB and a local storage dir to Postgres with pgvector, Valkey and SeaweedFS on 2026-10-03, following RUNBOOK §9.10. The api env gained `LKAP_DATABASE_URL`, `LKAP_REDIS_URL` and the `LKAP_STORAGE_*` settings (including `LKAP_STORAGE_PUBLIC_ENDPOINT_URL`). The data-store passwords and the S3 identity live in two more private files beside the others. All 20 knowledge bases were re-indexed into pgvector. Rollback is the env file saved before the cutover, as RUNBOOK §9.10 describes.

## 6. Moving the reference deployment elsewhere (checklist)

1. A LiveKit server (a Cloud project, or self-hosted with TLS, UDP and TURN).
2. A host or cluster for api and web (2 to 4 CPU), with HTTPS in front (Tailscale Serve, Caddy or a load balancer).
3. Workers next to each LiveKit server (about 4 CPU and 8 GB per 10 to 25 simultaneous calls with external STT, LLM and TTS). For the Cloud connection you can use LiveKit Cloud Agents instead (`lk agent deploy`, see `deploy/README.md` §2).
4. Data. Use SQLite and a local dir on a single node. Otherwise use Postgres with pgvector, Valkey or Redis, and S3-compatible storage. `deploy/docker-compose.datastores.yml` is the single-node recipe (Postgres 16 with pgvector, Valkey, SeaweedFS) and RUNBOOK §9.10 has the cutover. `deploy/docker-compose.prod.yml` is the larger setup.
5. Secrets. The master key travels with the data. The admin, service and session tokens are new for each deployment. LiveKit keys are per connection. Provider keys are stored encrypted in LKAP and added in the console.
6. Build the three images, run migrations, start api, then web, then workers, and verify as in 5.4.
7. AI provider accounts. Deepgram (speech) and OpenRouter (LLM, images, embeddings). Optional extras are LiveKit Inference, avatar vendors and Composio.

## 7. Open work for the next session

1. **Own data stores on the DGX.** The tooling is built (V6-37). `python -m lkap_api.tools.sqlite_to_postgres` copies the database, `python -m lkap_api.tools.copy_storage` copies the files, and `LKAP_STORAGE_PUBLIC_ENDPOINT_URL` makes presigned links reachable from browsers. RUNBOOK §9.10 lists the cutover steps and the rollback.
2. **Vendor logos (V6-38, merged).** UI-R6 puts a logo wherever the console names a company or product.
3. **Tailscale direct connection.** The Mac and the DGX currently relay through DERP. Allow UDP 41641 on the DGX side.
4. **Open asks** in `docs/v6/_asks.md` (Open at close, plus #296 onward). Notable ones are #178 (workers report server and agent name), #180 (outbound calls fail fast), #296 (reasoning text spoken by some models) and #305 (catalog refresh on miss). Ask #370 covers the em dashes left in older docs.
5. **Noise cancellation** is not installed in the worker yet. LiveKit's version is Cloud-only and ai-coustics works self-hosted.
6. **Live checks never run.** See `docs/v6/PLAN-V6.md` §5.1.

## 8. Rules that carry over

- Never commit, print or paste secrets (keys, tokens, the master key). Env files stay 0600 outside the checkout. The repo is public, so no personal hostnames or paths in committed files.
- Back up the database before every migration.
- Run exactly one worker set per agent name.
- Console UI uses dialogs only (no side drawers), plain words and no internal jargon.
- Copy rule for every user-facing text and doc. No em dashes, and no two statements joined by a colon or a semicolon.
- Contracts-first commits. Run every package's gates before merging.
