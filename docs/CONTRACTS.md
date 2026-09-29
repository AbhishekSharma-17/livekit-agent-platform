# LKAP Contracts

> **Amended (2026-09-18):** `docs/DECISIONS-W2.md` wins where it differs: §2 (`openai>=2,<3`, `mcp` extra, `py.typed`), §3 (`LIVEKIT_AGENT_NAME` optional — the worker registers as `lkap-agent` in code, D-W2-11; `LKAP_SESSION_*` sweep settings), §4 export (`tsType: "unknown"` for `Any` fields), §7 (`k` clamped 1..20), §10 (the platform sends `UiSnapshot` seq 1). Everything else in this file is unchanged and binding.

Every cross-team interface. Implementers copy shapes from here verbatim; changes to this file go through the architect. Versioned fields carry `v: 1`.

Owner of this file's Python realisation: package `contracts/` (`lkap_contracts`). Wave 0 lands it before anything else (see IMPLEMENTATION_PLAN).

---

## 1. Repository layout

```text
livekit_agent_platform/
├── README.md
├── docs/
│   ├── ARCHITECTURE.md  CONTRACTS.md  INSURANCE_PACK_MAPPING.md  IMPLEMENTATION_PLAN.md
│   └── research/livekit-core.md  providers-and-avatars.md
├── contracts/                          # Python package lkap_contracts (pure Pydantic, no I/O)
│   ├── pyproject.toml
│   ├── src/lkap_contracts/
│   │   ├── __init__.py
│   │   ├── providers.py                # ProviderSpec models + REGISTRY list
│   │   ├── agent_config.py             # AgentConfig v1, PipelineConfig, ResolvedAgentConfig
│   │   ├── dispatch.py                 # DispatchMetadata
│   │   ├── tools.py                    # HttpToolDefinition, McpServerDefinition, ToolDefinition
│   │   ├── packs.py                    # PackManifest
│   │   ├── ui_protocol.py              # UiState envelope, UiSnapshot/UiPatch, ActivityEvent, RPC payloads
│   │   ├── api_models.py               # request/response models shared by api + web codegen
│   │   └── export.py                   # `python -m lkap_contracts.export` → generated/*
│   ├── generated/                      # committed outputs (diff-tested)
│   │   ├── providers.json
│   │   ├── schemas/*.schema.json       # JSON Schema per exported model
│   │   └── ts/lkap-contracts.d.ts      # json-schema-to-typescript output, copied to web/src/contracts/
│   └── tests/
├── agent/                              # LiveKit worker
│   ├── pyproject.toml  livekit.toml  Dockerfile  env.example  README.md
│   ├── src/lkap_agent/
│   │   ├── main.py                     # AgentServer, rtc_session entrypoint, setup_fnc
│   │   ├── settings.py
│   │   ├── config_client.py            # fetch resolved config, post events/summary, kb search
│   │   ├── providers/factory.py        # ProviderFactory: registry id → plugin object
│   │   ├── providers/image_gen.py      # ImageGen protocol + google/openai impls
│   │   ├── platform_agent.py           # PlatformAgent(Agent): hooks, RAG injection, vision injection
│   │   ├── session_builder.py          # build AgentSession + RoomOptions + avatar
│   │   ├── vision.py                   # FrameBuffer, encode_jpeg
│   │   ├── ui/channel.py               # UiChannel: state seq, patches, assets, activity, RPC
│   │   ├── tools/builtin/*.py          # end_call, search_knowledge, http_request, describe_current_frame, pin_frame, push_note, set_status, escalate_to_human, current_time
│   │   ├── tools/declarative.py        # HTTP tools from JSON schema, MCP servers
│   │   ├── tools/background.py         # BackgroundToolRunner
│   │   ├── packs/loader.py             # import packs from LKAP_PACKS; builds the concrete PackSessionContext
│   │   ├── observability.py            # structlog, metrics → events
│   │   └── workflow_llm.py             # StructuredLLM: prompt-for-JSON + Pydantic parse + 1 repair
│   └── tests/ (fakes/, unit/, live/)
├── api/
│   ├── pyproject.toml  Dockerfile  env.example  alembic.ini  alembic/  README.md
│   ├── src/lkap_api/
│   │   ├── main.py  settings.py  deps.py  auth.py  errors.py  keys.py
│   │   ├── db/models.py  db/session.py
│   │   ├── vault.py                    # Fernet encrypt/decrypt
│   │   ├── livekit_tokens.py           # mint token + RoomAgentDispatch
│   │   ├── kb/ingest.py  kb/store.py (VectorStore Protocol + LanceDB)  kb/embed.py (Embedder + fastembed/openai)
│   │   ├── packs.py                    # manifest discovery (imports packs for manifest only)
│   │   └── routers/{providers,credentials,agents,tools,knowledge,sessions,connect,internal,health}.py
│   └── tests/
├── packs/
│   ├── pyproject.toml                  # package "lkap-packs", depends ONLY on lkap_contracts + livekit-agents (never on lkap_agent)
│   ├── src/packs/__init__.py
│   ├── src/packs/base.py               # Pack interface + runtime Protocols (§8). agent depends on packs, not the reverse
│   ├── src/packs/generic/pack.py
│   ├── src/packs/insurance_claim/
│   │   ├── manifest.py (pure Pydantic, importable by api without livekit)  pack.py  instructions.py  schemas.py  rules.py  workflow.py  policy_directory.py  tools.py  ui_state.py  seeds/ (kb docs)
│   └── tests/insurance_claim/ (+ fixtures copied from ../insurance_claim_live_agent_team/tests/fixtures)
├── web/
│   ├── package.json  next.config.ts  Dockerfile  env.example  components.json
│   ├── src/app/(session)/s/[slug]/page.tsx
│   ├── src/app/console/...             # agents list, editor tabs, credentials, kbs, sessions
│   ├── src/components/agents-ui/       # vendored via shadcn @agents-ui/all
│   ├── src/components/session/         # SessionView, ControlBar wrapper, Transcript, VideoStage
│   ├── src/components/console/         # RegistryForm, CredentialPicker, ToolEditor, KbEditor
│   ├── src/lib/api.ts  src/lib/livekit.ts  src/lib/ui-state.ts (reducer)  src/hooks/useUiState.ts  useByteStream.ts  useAgentRpc.ts
│   ├── src/panels/registry.ts  src/panels/generic/  src/panels/insurance_notebook/
│   ├── src/contracts/                  # copied generated TS types
│   └── tests/ (vitest) e2e/ (playwright)
├── deploy/
│   ├── docker-compose.yml              # api + web (agent runs on LiveKit Cloud or locally)
│   └── README.md
└── scripts/
    ├── dev.sh                          # runs api + agent + web concurrently (for humans)
    └── export_contracts.sh
```

---

## 2. Pinned dependency versions

Python (all packages: `requires-python = ">=3.12,<3.13"`, build backend `hatchling`, `uv` lockfile per package, ruff line-length 110, `mypy --strict` with `pydantic.mypy` plugin, pytest `asyncio_mode = "auto"`).

`contracts/pyproject.toml` deps: `pydantic>=2.11,<3`.

`agent/pyproject.toml` deps:
```
livekit-agents[google,openai,deepgram,cartesia,elevenlabs,silero,bey,tavus]==1.8.2   # exact pin; see note below
livekit-plugins-google==1.8.2
livekit-plugins-openai==1.8.2
livekit-plugins-deepgram==1.8.2
livekit-plugins-cartesia==1.8.2
livekit-plugins-elevenlabs==1.8.2
livekit-plugins-silero==1.8.2
livekit-plugins-bey==1.8.2
livekit-plugins-tavus==1.8.2
livekit-api==1.2.1
livekit==1.1.18
google-genai>=1.30,<3          # image generation (gemini image models)
openai>=2,<3                   # image generation (gpt-image); livekit-agents 1.8.2 requires openai>=2,<3
httpx>=0.28,<1
pydantic>=2.11,<3
pydantic-settings>=2.10,<3
structlog>=25.4,<26
pillow>=11,<12
lkap-contracts (path: ../contracts), lkap-packs (path: ../packs)
```

**Pin policy (DECISIONS-W2 §D-W2-9p, §D-W2-11).** The exact `==1.8.2` pins stay. Any bump must re-run the offline tripwires (`test_sdk_default_text_input_cb_still_matches_our_assumptions`, `test_sdk_rtc_session_precedence_still_matches_our_assumptions`) and re-read `voice/room_io/types.py`, `voice/agent_session.py` (`_claim_user_turn`, `generate_reply(user_input=, chat_ctx=)`) and `worker.py` (`rtc_session` name precedence) first.

Dev: `pytest>=8.4`, `pytest-asyncio>=1.1`, `pytest-timeout>=2.4`, `ruff>=0.13`, `mypy>=1.18`, `respx>=0.22`. Do **not** add `livekit-plugins-turn-detector` (deprecated) or `livekit-plugins-noise-cancellation`; use `inference.TurnDetector()` and no server-side noise cancellation for MVP.

`api/pyproject.toml` deps:
```
fastapi>=0.118,<1
uvicorn[standard]>=0.36,<1
sqlalchemy[asyncio]>=2.0.43,<3
aiosqlite>=0.21,<1
alembic>=1.16,<2
cryptography>=45,<46
livekit-api==1.2.1
lancedb>=0.24,<1
fastembed>=0.7,<1
pypdf>=6,<7
python-multipart>=0.0.20
httpx>=0.28,<1
pydantic>=2.11,<3
pydantic-settings>=2.10,<3
structlog>=25.4,<26
lkap-contracts (path), lkap-packs (path, manifests only)
```
Dev: same as agent + `pytest-httpx` not needed (use `httpx.AsyncClient(app=...)`).

`packs/pyproject.toml` deps: `pydantic`, `livekit-agents==1.8.2`, `lkap-contracts`. No `google-adk`. **Dependency direction: `agent` → `packs` → `contracts`; packs never import `lkap_agent`.** Each pack exposes `manifest.py` (pure Pydantic `MANIFEST: PackManifest`) so `api` imports manifests without importing livekit; `pack.py` (`PACK`) is imported only by the worker. Register pytest markers `live` and `slow` in every Python pyproject.

`web/package.json` (pnpm 9.15.9 via `packageManager`, Node ≥24; Node 25 works):
```json
{
  "dependencies": {
    "@livekit/components-react": "2.9.24",
    "livekit-client": "2.22.3",
    "next": "15.5.18",
    "react": "19.1.1",
    "react-dom": "19.1.1",
    "tailwindcss": "^4.1.0",
    "@phosphor-icons/react": "^2.1.8",
    "@tanstack/react-query": "^5.85.0",
    "react-hook-form": "^7.62.0",
    "zod": "^4.1.0",
    "class-variance-authority": "^0.7.1",
    "clsx": "^2.1.1",
    "tailwind-merge": "^3.3.0"
  },
  "devDependencies": {
    "typescript": "^5.9.0",
    "@types/react": "^19.1.0",
    "@types/node": "^24",
    "eslint": "^9",
    "prettier": "^3.6.0",
    "vitest": "^3.2.0",
    "@testing-library/react": "^16.3.0",
    "jsdom": "^26",
    "@playwright/test": "^1.55.0",
    "json-schema-to-typescript": "^15.0.0"
  }
}
```
`livekit-server-sdk` is **not** a web dependency (tokens come from api). Implementers may bump patch versions when `pnpm install` requires it; majors are fixed.

---

## 3. Environment variables per service

All services: pydantic-settings, `env_prefix="LKAP_"` except LiveKit canonical names. `.env` is optional and human-created; each service ships `env.example`. Values in `.claude/launch.json` `runtimeArgs` (`bash -c "export ... && exec ..."`) for Claude-driven dev.

| Var | api | agent | web | Notes |
|---|---|---|---|---|
| `LIVEKIT_URL` | req | req | — | `wss://your-project.livekit.cloud` |
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | req | req | — | api mints tokens; agent registers + Inference billing |
| `LIVEKIT_AGENT_NAME` | — | opt | — | the worker registers as `lkap-agent` by code (DECISIONS-W2 §D-W2-11); if set it must equal `lkap-agent` (`LIVEKIT_AGENT_NAME_OVERRIDE` likewise); `livekit.toml [agent] name` must match |
| `LKAP_MASTER_KEY` | req | — | — | Fernet key (urlsafe b64, 32 bytes) |
| `LKAP_AGENT_NAME` | req | — | — | `lkap-agent`; the dispatch target the api puts in `RoomAgentDispatch`; must equal the worker's `REQUIRED_AGENT_NAME` |
| `LKAP_ADMIN_TOKEN` | req | — | server-only | Static admin bearer |
| `LKAP_SERVICE_TOKEN` | req | req | — | Worker→api auth |
| `LKAP_API_BASE_URL` | — | req | — | e.g. `http://127.0.0.1:8080` |
| `LKAP_DATA_DIR` | req | — | — | default `./data` (db, lancedb, kb files, models) |
| `LKAP_DATABASE_URL` | opt | — | — | default `sqlite+aiosqlite:///{DATA_DIR}/lkap.db` |
| `LKAP_CORS_ORIGINS` | opt | — | — | default `http://localhost:3000` |
| `LKAP_PUBLIC_BASE_URL` | opt | — | — | used in connect response for asset links; also the origin of the Apps sign-in return address (`/v1/tool-providers/composio/callback`) and of the MCP sign-in return address (`/v1/oauth/mcp/callback`, V5-14), falling back to the api's own url (V5-18) |
| `LKAP_PACKS` | opt | opt | — | default `packs.generic` (V6-22; add `packs.insurance_claim,` to keep the legacy pack) |
| `LKAP_HTTP_TOOL_ALLOWED_HOSTS` | opt | opt | — | comma list; empty = only per-tool allowlist (the api reads it only for `LKAP_MCP_ALLOWED_HOSTS=@http`) |
| `LKAP_MCP_ALLOWED_HOSTS` | opt | opt | — | comma list of MCP server hosts (V5-09, D-V5-4); empty = any public `https` host that passes the network guard, non-empty = a ceiling, `@http` = reuse `LKAP_HTTP_TOOL_ALLOWED_HOSTS` (then empty allows nothing). Set the same value on both |
| `LKAP_LOG_LEVEL` / `LKAP_LOG_JSON` | opt | opt | — | `INFO` / `false` |
| `LKAP_EMBEDDER` | opt | — | — | `fastembed` (default) or `openai:<credential_id>` |
| `LKAP_EMBED_MODEL` / `LKAP_RERANK_MODEL` | opt | — | — | the local fastembed embedder and cross-encoder (V5-01, V5-04); defaults `BAAI/bge-small-en-v1.5` / `Xenova/ms-marco-MiniLM-L-6-v2` |
| `LKAP_VECTOR_STORE` | opt | — | — | unset = follow the database (pgvector on Postgres, LanceDB on SQLite); `lancedb` forces LanceDB (V5-13) |
| `LKAP_MCP_OAUTH_ALLOW_UNBOUND` | opt | — | — | default `false`; `true` accepts an MCP sign-in callback without the start's binder cookie (R-V5-14) |
| `LKAP_SELF_HOSTED_ALLOWED_NETWORKS` | opt | — | — | CIDRs a self-hosted LiveKit connection may reach (R-V5-17); unset = loopback, RFC 1918, ULA, CGNAT |
| `LKAP_CALL_START_WORKER_CHECK` | opt | — | — | `block` (default) \| `warn` \| `off` (V6-27): a call or test-chat start on an external or supervised connection with no live worker answers 409 `no_worker_running` (`block`) or only logs `call_start_no_worker` (`warn`); the default was confirmed by the user (V6 U-V6-4) |
| `LKAP_VISION_MAX_FRAME_AGE_S` | — | opt | — | default `8` |
| `LKAP_IDLE_HANGUP_S` | — | opt | — | default `120`; worker hangs up a session that stays `away` this long while the agent is listening/idle; `None`/`0` disables (REVIEW-FINAL.md F-02) |
| `LKAP_SESSION_SWEEP_INTERVAL_S` | opt | — | — | default `60`; how often the stale-session sweep runs (D-W2-2b) |
| `LKAP_SESSION_STALE_CREATED_S` | opt | — | — | default `600`; a `created` row older than this → `failed` "never started" |
| `LKAP_SESSION_STALE_ACTIVE_S` | opt | — | — | default `21600`; an `active` row older than this → `failed` "summary never received" |
| `NEXT_PUBLIC_API_BASE_URL` | — | — | req | e.g. `http://localhost:8080` |
| `LKAP_ADMIN_TOKEN` (web server) | — | — | req | used by Next server actions/route handlers proxying console calls; never exposed to the client bundle |
| `PORT` | 8080 | — | 3000 | |

Every setting v5 added, with defaults, is also in `docs/RUNBOOK.md` §9.7; every setting v6 added or re-defaulted in §9.9.

Vendor keys (`GOOGLE_API_KEY`, `OPENAI_API_KEY`, ...) are **not** read from env by the platform; they live in the credential vault. Exception for convenience: the api accepts `LKAP_BOOTSTRAP_CREDENTIALS_JSON` (JSON `{provider_id: {field: value}}`) at startup to seed credentials in dev.

### Apps (Composio, V5-18)

Connected third-party apps (`docs/v5/COMPOSIO.md`). No new environment variable and no new table:

- **Key.** Registry entry `composio` (`kind: "tool_provider"`, one secret field `api_key`, no `test`/`catalog`); a normal provider-key row. `POST /v1/credentials/{id}/test` checks it through `lkap_api.credential_tests` (Composio's session-info call, then the app count). `POST /v1/tool-providers/composio/key/test` checks a pasted key without storing it (10 per workspace per minute). Rotate is `PUT /v1/credentials/{id}` on the same row. Enablement is the `workspace_providers` row of `composio` (`POST …/enable`, `POST …/disable`; disable switches off the tools bound to the key or to a connection).
- **Connections.** One `credentials` row per connected app with `provider_id = "tool-provider-account"` (not a registry id, so `POST /v1/credentials` cannot create one). The encrypted bag holds references only: `toolkit`, `method`, `subject` (`ws:<workspace_id>` or `agent:<agent_id>`), `auth_config_id`, `connected_account_id`, status, picked actions, and while a sign-in is pending the SHA-256 of its single-use nonce and its expiry. `last_test_message` mirrors the status (`initiated|active|expired|failed|inactive|unknown`, transiently `verifying`), `last_test_ok` = active, `last_test_at` = last checked. The sessions sweep marks sign-ins unfinished after 10 minutes `expired`.
- **Routes** (`lkap_api/tool_providers/router.py`, reads `viewer` + `providers:read`, writes `admin` + `providers:write`): `GET …/status`, `POST …/key/test`, `POST …/enable`, `POST …/disable`, `GET …/toolkits`, `GET …/toolkits/{slug}`, `GET …/toolkits/{slug}/actions`, `GET …/categories` (every toolkit category; cached 10 minutes, `refresh=true` re-reads for writers, rate limited), `POST …/connections`, `GET …/connections`, `GET …/connections/{id}` (refreshes from Composio), `PATCH …/connections/{id}` (`ConnectionRenameIn {label?, is_default?}`: rename an account or make it the app's default; R-V5-13), `POST …/connections/{id}/reconnect`, `DELETE …/connections/{id}[?purge=true]`, `POST …/materialise` (stores the picks and creates one `provider` tool per action, attached to `agent_id` when given — V5-47), `POST …/tools/{id}/refresh-schema[?apply=true]` (diffs a `provider` tool's pinned inputs with Composio's current ones; V5-47), and the unauthenticated `GET …/callback?flow=<row id>.<nonce>&status&connected_account_id` (302 to `/console/tools?tab=apps&connect=ok|error`). Prefix `…` = `/v1/tool-providers/composio`.
- **Models** (`lkap_contracts.tool_providers`, exported with an `App`/`Toolkit` prefix because `ConnectionOut` is taken): `ToolkitOut`, `ToolkitPage`, `AppAuthField`, `AppActionOut`, `AppActionPage`, `AppConnectIn`, `AppConnectOut`, `AppConnectionOut`, `AppConnectionPage`, `AppReconnectIn`, `AppKeyTestIn`, `AppKeyTestOut`, `AppsStatusOut`, `AppActionsPickIn`, `AppActionsPickOut`, `ConnectionRenameIn`.
- **Tool bindings.** `_check_payload` lets a `composio` key bind only to an `mcp` definition tagged `origin.provider == "composio"` whose url is `https://backend.composio.dev/…`, or to a `provider` definition (V5-47), which also binds a connection row as `connection_id` with the connection's own `subject` (an app connected for one agent serves only that agent's tools); an `http` tool can never bind the key, and a connection row is never a tool credential.

### Apps on agents (V5-47)

`docs/v5/COMPOSIO.md` §3–§5. No environment variable; one migration, `v5_010_tool_provider_kind`
(`tools.kind` CHECK gains `provider`).

- **`provider` tools.** `ProviderToolDefinition` (`kind: "provider"`, `provider`, `name`, `description`,
  pinned `parameters`, `tool_slug`, `toolkit`, `connection_id`, `credential_id`, `subject`,
  `connected_account_id` (the account the tool is pinned to; set at materialisation and when the
  connection's account changes, R-V5-13), `headers`
  (default `{"x-api-key": "{{ secret.api_key }}"}`), `timeout_s`, `max_result_chars` (1500), `result_path`
  (`"data"`), `silent_reply`, `execution`, `schema_version`, `risk`) joins the `ToolDefinition` union and
  `ToolCreate.kind`. Materialisation names it `<toolkit>_<action>` (≤ 64 characters), reads run `auto`
  with an announcement, writes and destructive actions block and are not cancellable, 20 s. The api
  substitutes the key into `headers` at resolve time; the worker (`lkap_agent.tools.provider`) posts
  `{user_id: subject, arguments, version, connected_account_id?}` to `https://backend.composio.dev/api/v3.1/tools/execute/{slug}`.
- **`tools.apps`.** `ToolsConfig.apps: AppsMode` (`mode: actions|server|router|off = off`,
  `allowed_toolkits`, `denied_actions`, `reviewed_actions` (≤ 500: destructive actions the builder has
  decided about; an unreviewed destructive action in scope is blocked, R-V5-9, via
  `effective_denied_actions`), `router: {search, execute, manage_connections=false}`, `accounts:
  {toolkit: [connection id]}` (≤ 20 apps × 5; an app not named uses its default account, R-V5-13)).
  `server`/`router` make the api provision a Composio Tool Router session on agent save
  (`tool_providers/provisioning.py`; the MCP server API is deprecated at Composio) and attach it as an
  agent-owned `mcp` row with `origin: McpServerOrigin{provider, kind: server|router, remote_id: <session
  id>, config_hash}`, `allowed_tools` (the picked actions, or the permitted meta tools) and
  `tool_options`; its id is kept in `tools.tool_ids`. Unchanged settings reuse the session; `off`, another
  mode or deleting the agent deletes it. The worker connects to an origin-tagged server only over `https`
  on `backend.composio.dev`.
- **Never-list.** `COMPOSIO_MULTI_EXECUTE_TOOL`, `COMPOSIO_MANAGE_CONNECTIONS`,
  `COMPOSIO_WAIT_FOR_CONNECTIONS` join `NEVER_BACKGROUND_TOOLS`.
- **Validation.** `tools.apps.mode` other than `off` without a Composio key (or with Apps disabled) is an
  error; `router.manage_connections` is an error (V5-27, S5-41: refused until the Composio live check); an agent
  save that turns `mode` to `server`/`router`, adds to `reviewed_actions`, turns `manage_connections` on or names a
  new account needs `admin` + `providers:write` (403 otherwise, S5-40); an attached `provider` tool whose connection is gone,
  `expired`, `failed` or `inactive` is an error naming the app.
- **Events.** `tool_needs_reauth {call_id, tool}` when an action fails because its app needs a person to
  reconnect it (the model is told "This app needs to be reconnected by an admin"; no link is ever spoken).

### MCP servers: auth, host policy, connection test (V5-09)

`docs/research-v4/tools-and-integrations.md` §4.3.1, §4.3.6, §4.3.7, §4.3.10; D-V5-4, D-V5-11. No migration.

- **Auth union.** `McpServerDefinition.auth: McpAuth = McpNoAuth()`, where `McpAuth` is
  `McpNoAuth{kind: "none"} | McpHeaderAuth{kind: "header", headers, credential_id} |
  McpOAuthAuth{kind: "oauth", credential_id, registration: auto|preregistered, client_id,
  client_secret_ref, scopes, subject: workspace|agent}` discriminated on `kind`. Header values may use
  `{{ secret.NAME }}` from an `http-tool-secret` bag. The pre-V5-09 top-level `headers` and
  `credential_id` stay as **deprecated mirrors**: set without `auth` (or with `auth.kind == "none"`) they
  fold into header auth; with `auth` set they are filled from it (`credential_id` also mirrors an OAuth
  credential); a value that disagrees with `auth` is a validation error. Stored rows need no data
  migration: they load as header auth and re-save with `auth` (plus the mirrors, for readers that have
  not moved to `auth`). `kind: "oauth"` servers are signed in through the api (V5-14) and the worker
  connects with an api-issued bearer (V5-16), skipping a server the api issued no access for (both below).
- **Host policy.** At save (`_check_payload`), before a test connection, and on the worker at connect
  time, an MCP url must pass the network guard (`net_guard.check_url`; the worker's `check_url_public`),
  be `https` (the api allows plain `http` only to a loopback host in `LKAP_ENV=dev`; the worker, which
  refuses loopback anyway, requires `https`), and sit on `LKAP_MCP_ALLOWED_HOSTS` when that is set.
  The ceiling applies to provider-provisioned servers too (list the provider's host when Apps are used).
  The api answers `422 blocked_destination`; the worker skips the server with a warning and a session
  event, and the session starts with its other tools.
- **Connection test.** `POST /v1/tools/{id}/test` (`builder` + `agents:write`, the `/v1/tools` rule) → `McpTestResult{ok, tool_names, tool_count,
  cached_at, duration_ms, reason: blocked_destination|needs_auth|unreachable|protocol_error|http_error,
  error}`. The api connects with the stored auth (secrets substituted as for a session) through the
  guarded client (no redirects), runs `initialize`, `notifications/initialized` and `tools/list` (up to
  five `nextCursor` pages; JSON or event-stream answers), and stores the snapshot as
  `cached_tools: list[McpToolSnapshot{name, description, input_schema}]` and `cached_at` on the
  definition (at most 200 tools, descriptions cut to 1,000 characters, an input schema over 16 KB
  dropped). A save that sends no snapshot keeps the stored one while the url is unchanged. The worker
  still lists tools itself at session start; the api strips `cached_tools` from the resolved config.
  For an `oauth` server the test sends `Authorization: Bearer <the access token>`, refreshed first
  when it is about to expire (V5-16); with no sign-in, or one that needs an admin, it answers
  `needs_auth` without a request (`unreachable` when the provider cannot refresh just now). A
  provisioned (`origin`) server whose url is not Composio's `https` host is `422 blocked_destination`
  (D-V5-C10).
- **Upgrade tripwire.** `agent/tests/unit/test_sdk_tripwires.py` fails when livekit-agents'
  `MCPServerHTTP.__init__` gains `auth`, when `_create_http_client` or its two call sites change, when
  livekit-agents stops pinning `mcp<2`, or when the pinned version moves off 1.8.3; the file says what to
  do (shrink `GuardedMCPServerHTTP` per research §4.3.10).

### MCP servers: signing in with OAuth (V5-14)

`docs/research-v4/tools-and-integrations.md` §4.3.2–§4.3.5, §4.3.8, §4.3.9, §4.3.11; D-V5-2, D-V5-3.
Migration `v5_004_mcp_oauth` (`mcp_oauth_flows`, `mcp_oauth_clients`). The api is the OAuth client;
refresh, revoke, the internal token route and the worker bearer are V5-16 (next section).

| Route | Auth | Purpose |
|---|---|---|
| `POST /v1/tools/{id}/oauth/start` | admin + `providers:write` | `McpOauthStartIn{client_secret?, authorization_server?}` → `McpOauthStartOut{status: redirect\|needs_client_registration, authorization_url, expires_at, redirect_uri, issuer, registration: preregistered\|cimd\|dcr}`. Runs discovery and registration and writes a flow row. Refusals are `422` with `details.reason` (`pkce_unsupported`, `blocked_destination`, `issuer_mismatch`, `resource_mismatch`, `no_resource_metadata`, `no_authorization_server_metadata`, `oauth_not_required`, `redirect`, `registration_failed`, `client_id_required`, `not_oauth`, `unreachable`, …). |
| `GET /v1/tools/{id}/oauth/status` | admin + `providers:read` | `McpOauthStatusOut{status: not_connected\|connected\|needs_reauth\|revoked, issuer, scopes, expires_at, connected_at, last_refresh_at, registration, worker_supported}` (`worker_supported: true` from V5-16). |
| `GET /v1/oauth/mcp/callback?state&code&iss?&error?` | **none** (browser redirect; bound by `state`) | `302` to `LKAP_WEB_BASE_URL` (else the first web origin) `/console/tools?oauth=ok\|error` (no id, no token material; `Cache-Control: no-store`, `Referrer-Policy: no-referrer`); `400` when `state` names no live sign-in (unknown, used, expired, malformed); at most 30 requests per client address per minute (`429`). |
| `GET /v1/oauth/mcp/client-metadata.json` | public | The deployment's Client ID Metadata Document, only when `LKAP_PUBLIC_BASE_URL` is a public `https` origin; `404` otherwise. |

The start, status and revoke requirements are checked in the handler, on top of the `/v1/tools` route rule.

- **Discovery.** Unauthenticated `initialize` → `401` → `WWW-Authenticate` (`resource_metadata`,
  `scope`) → protected resource metadata (the header's url, then `/.well-known/oauth-protected-resource/<path>`,
  then the root) → its `resource` must contain the server's canonical url and is sent as `resource`
  on both requests → authorization server metadata (RFC 8414 then OpenID, path-inserted then
  appended) → `issuer` must match; `code_challenge_methods_supported` must list `S256` (absent →
  refused). Every server-supplied url passes the network guard and must be `https` (plain `http` only
  to loopback in dev) **before** it is fetched; redirects are never followed; bodies are capped
  (64 KB); each request times out after 10 s, the whole start after 30 s.
- **Registration**, in the spec's order: pre-registered (`auth.registration = "preregistered"` +
  `auth.client_id`; an optional client secret arrives write-only in the start body and is kept
  encrypted on the `mcp_oauth_clients` row), or a client this workspace already registered at the
  issuer → the metadata document when the provider supports it and `LKAP_PUBLIC_BASE_URL` is public
  `https` → dynamic registration (a public client, `application_type` `native` for a loopback return
  address, `web` otherwise; the registration is stored and reused per workspace and issuer) →
  `needs_client_registration`.
- **Flow row.** `state` (32 random bytes) is stored only as its SHA-256; the PKCE verifier only as a
  vault ciphertext; ten minutes; single use; a new start replaces the tool's pending flow. The row
  records the workspace, the tool, the initiating actor and the issuer exactly as the metadata spelled it.
- **Callback checks**, in order: the hashed `state` (constant-time compare) → the row is claimed and
  committed before anything else → expiry → the browser binding (R-V5-14: a sign-in a person
  started needs the `lkap_mcp_oauth` cookie `oauth/start` set, compared in constant time, else
  `browser_mismatch`; a flow an API key started needs none; `LKAP_MCP_OAUTH_ALLOW_UNBOUND=true` turns the
  check off for split-origin deployments) → RFC 9207 (`iss` present: byte-equal to the recorded
  issuer; absent: refused when the provider advertised `authorization_response_iss_parameter_supported`;
  on a mismatch `error` is not acted on) → `error` → `code` → the tool still exists, still uses OAuth,
  and its url is still covered by the flow's `resource` → code exchange (`authorization_code`, the
  verifier, `redirect_uri`, `resource`; `client_secret_basic` or `_post` when the client has a secret).
- **The `mcp-oauth` credential** (registry entry `mcp-oauth`, kind `secret_bag`, no secret fields):
  bag keys `access_token`, `refresh_token`, `expires_at` (absolute ISO UTC), `scope`, `issuer`,
  `token_endpoint`, `revocation_endpoint`, `resource`, `client_id`, `client_secret`,
  `token_endpoint_auth_method`, `registration`, `registration_client_uri`, `registration_access_token`,
  `client_metadata_url`, `status` (`active | needs_reauth | revoked`), `tool_id`, `connected_at`,
  `last_refresh_at`. Fingerprint `issuer host · first scope`. The callback sets the tool's
  `auth.credential_id`. At save, an `oauth` definition binds an `mcp-oauth` credential only on update
  and only when the bag's `tool_id` is that tool and its `resource` covers the url
  (`422 oauth_credential_mismatch` / `oauth_credential_misuse` otherwise); its url may hold no
  `{{ secret.* }}` (`422 oauth_url_placeholder`); a save that omits `auth.credential_id` keeps the
  stored sign-in while the url is unchanged.
- **Audit**: `mcp_oauth.start`, `mcp_oauth.callback_ok`, `mcp_oauth.callback_rejected` (`reason`),
  identifiers only. **Sweep**: the sessions sweep deletes consumed or expired flows and
  dynamically registered clients whose secret expired.

### MCP servers: tokens, the worker bearer, disconnect (V5-16)

`docs/research-v4/tools-and-integrations.md` §4.3.6. No migration (`status` lives in the bag).

| Route | Auth | Purpose |
|---|---|---|
| `POST /internal/v1/tools/{id}/oauth/token` | service token | `McpOAuthTokenIn{session_id, rejected_token_sha256?}` → `McpOAuthTokenOut{access_token, expires_at}` — never the refresh token. Bound to a live session (`created`/`active`) whose agent lists the tool in `tools.tool_ids`; the tool must be an enabled `oauth` MCP server of the session's workspace whose sign-in belongs to it. `404` unknown session or a tool the agent does not use; `409` `session_ended` or `needs_reauth`; `503 token_unavailable` on a transient refresh failure. |
| `POST /v1/tools/{id}/oauth/revoke` | admin + `providers:write` | Disconnect → `McpOauthStatusOut{status: "not_connected"}`. |

- **Refresh** (`mcp_oauth/tokens.py::get_access_token`): the stored token is handed out while it
  has more than 60 s left; otherwise it is refreshed (`refresh_token` grant with `resource` and the
  client's authentication) **single-flight per credential**: a process lock, a Redis `SET NX PX`
  lock when `LKAP_REDIS_URL` is set, and `SELECT … FOR UPDATE` on the credential row (Postgres).
  Under the lock the bag is re-read, so a token another request already refreshed is used as is and
  a rotated refresh token is never replayed; a new refresh token from the provider replaces the old
  one. `rejected_token_sha256` equal to the stored token's hash forces a refresh; a stale hash (the
  token was already rotated) returns the new one. `invalid_grant`, or an expired token without a
  refresh token, sets the bag's `status` to `needs_reauth`, writes the audit row
  `mcp_oauth.needs_reauth` and emits the `tool.needs_reauth` webhook (`data: {tool_id, reason}`); a
  transient failure keeps a token that has not expired yet.
- **Session start**: `ResolvedAgentConfig.mcp_oauth: list[McpOAuthAccess{tool_id, name, url,
  access_token?, expires_at?}]`, one per `oauth` server with a usable sign-in, refreshed first when it
  is about to expire (`access_token` is `null` when the api could not refresh it then — e.g.
  `sessions/start` on SQLite, whose write lock the request already holds — and the worker fetches one
  before its first request). The `mcp-oauth` bag is never decrypted into `tools`; the definition there
  carries no credential id. A sign-in that needs an admin is left out and the worker skips the server.
- **Worker** (`agent/src/lkap_agent/tools/mcp_auth.py`): `ApiIssuedBearer` wraps the guarded
  transport of each such server — every request carries `Authorization: Bearer <token>` (replaced
  when less than 30 s is left); on `401` one call to the token route (with the refused token's
  SHA-256) and one retry, never a third request; on `403` with `error="insufficient_scope"` the
  challenged scope names are logged and nothing is fetched. When no token can be had the transport
  answers the MCP client itself so the connection survives: a `tools/call` gets an `isError` result
  "This integration needs to be re-authorised by an admin" (the model's `ToolError`, recorded as a
  `tool_needs_reauth` session event), any other request a JSON-RPC error, a notification `202`; after
  `needs_reauth` it waits 30 s before asking the api again.
- **Disconnect** (`mcp_oauth/revoke.py`), also run before a tool is deleted: RFC 7009 at the
  `revocation_endpoint` (the refresh token, then the access token), then RFC 7592 deletion of a
  dynamically registered client — only when its `registration_client_uri` passes the URL check, a
  `registration_access_token` is held, and no other sign-in or live flow of the workspace uses the same
  client (the `mcp_oauth_clients` row goes with it); every provider call through the guard, no
  redirects, 20 s in total, failures recorded but never blocking. Then the credential is deleted and the
  tool's `auth.credential_id` cleared. Audit `mcp_oauth.revoked {trigger: revoke|tool_delete,
  credential_id, issuer_host, revocation: ok|failed|unsupported|no_token, client_deleted}`.
- **Logs**: no token, code, `state` or secret in any log line, audit row or error message; the
  worker's structlog chain masks `*_token`, `*_secret`, `code`, `state` and `authorization` fields.

### Curated built-in tools (V5-25)

No environment variable, no table, no migration. Vendor keys live in the vault like every other key.

- **Names.** `BUILTIN_TOOL_NAMES` gains `calculate`, `spell_back` (local, registered by default,
  `NEVER_BACKGROUND_TOOLS`) and `web_search`, `fetch_url`, `send_sms`, `notify_team`
  (`CONFIGURED_BUILTINS`: registered only when configured, and `builtin_disabled` still removes them).
  The four join `BACKGROUNDABLE_BUILTINS`; `WRITE_BUILTINS = {send_sms, notify_team}` run as writes
  (a repeat while one runs asks first, not cancellable). `BUILTIN_DEFAULT_MODES` is each tool's own mode
  when `builtin_execution` sets none, and wins over `execution_default`: `web_search` `auto`, the other
  three `background`. `generated/builtin_tools.json` carries `configured_builtins`, `write_builtins`,
  `builtin_default_modes`.
- **Config.** `ToolsConfig.web_search: ProviderRef | None` (kind `web_search`: `tavily-search` — the
  suggested one — or `brave-search`, D-V5-7), `ToolsConfig.sms: ProviderRef | None` (kind `sms`:
  `twilio-sms` with secrets `account_sid`/`auth_token`, or `telnyx-sms` with `api_key`; both take the
  non-secret field `from_number`, E.164), `ToolsConfig.fetch_url_allowed_hosts: list[str]`,
  `ToolsConfig.notify_team: NotifyTeamConfig | None` (`credential_id` of an `http-tool-secret` key,
  `secret_name` = `TEAM_WEBHOOK_URL`, `style: slack|generic`, `on_escalation = true`,
  `include_transcript = false`), `TelephonyConfig.sms_targets: list[SmsTarget{label, to (E.164)}]`.
  The four registry entries have no package, class, `test` or `catalog` (a key test is open: ask #151).
- **Resolution.** `ResolvedAgentConfig.builtin_providers: dict["web_search"|"sms"|"notify_team",
  ResolvedProvider]` (**contains secrets**), kept out of `resolved` (whose every slot the provider factory
  builds). `notify_team` resolves to `kwargs={"webhook_url": …}` from the named secret.
- **Tool templates** (D-V5-36). `GET /v1/tool-templates` (`viewer` / `agents:read`) →
  `ToolTemplatesResponse`; `POST /v1/tool-templates/{id}/instantiate` (`builder` / `agents:write`, plus
  `providers:write` to bind the key, R-V2-33)
  with `ToolTemplateInstantiate{credential_id, defaults, agent_id, enabled, names}` →
  `ToolTemplateInstantiated{tool_ids, names, template_ids}`. `{id}` is one template
  (`cal_com.booking_create`) or a group (`cal_com`, all six). The rows are ordinary `http` tools checked
  like `POST /v1/tools`; `defaults` become the JSON Schema `default` of those arguments.

---

## 4. Provider registry (`lkap_contracts.providers`)

```python
from typing import Literal
from pydantic import BaseModel, Field

ProviderKind = Literal["realtime", "stt", "llm", "tts", "avatar", "image_gen", "embedding", "secret_bag"]
FieldType = Literal["string", "secret", "number", "boolean", "enum", "json", "model"]


class FieldSpec(BaseModel):
    name: str  # constructor kwarg name (dots allowed for nested configs, e.g. "simli_config.face_id")
    label: str
    type: FieldType
    required: bool = False
    default: str | int | float | bool | None = None
    options: list[str] | None = None  # for enum
    placeholder: str | None = None
    help: str | None = None
    condition: str | None = None  # "vertexai=true" → show only when sibling equals value
    env_fallback: str | None = None  # informational only; platform never reads it


class ModelSpec(BaseModel):
    id: str  # e.g. "deepgram/nova-3" or "gemini-3.8-live"
    label: str
    supports_video: bool = False
    note: str | None = None


class ProviderCapabilities(BaseModel):
    video_input: bool = False  # realtime models that accept frames
    tool_calling: bool = True
    silent_tool_reply: bool = False  # honours reply_required (realtime only)
    voices: list[str] = []  # suggested voice ids/names


class ProviderSpec(BaseModel):
    v: Literal[1] = 1
    id: str  # "google-realtime"
    kind: ProviderKind
    label: str
    vendor: str
    status: Literal["mvp", "deferred"] = "mvp"
    package: str  # pip package
    python_class: str  # dotted path used by the factory
    requires_credential: bool = True
    secret_fields: list[FieldSpec] = []  # stored encrypted, form-masked
    fields: list[FieldSpec] = []  # stored in AgentConfig, not encrypted
    models: list[ModelSpec] = []
    default_model: str | None = None
    capabilities: ProviderCapabilities = ProviderCapabilities()
    docs_url: str | None = None
    get_key_url: str | None = None


REGISTRY: list[ProviderSpec]  # module-level list; `get(id)`, `by_kind(kind)` helpers
```

Initial `status="mvp"` entries (implementers fill labels/help from the research doc; ids, classes, fields and defaults are fixed here):

| id | kind | python_class | secret_fields | fields (defaults) | default_model / models |
|---|---|---|---|---|---|
| `livekit-inference-stt` | stt | `livekit.agents.inference.STT` | — (`requires_credential=false`) | `language` ("en") | `deepgram/nova-3`; `deepgram/flux-general-en`, `deepgram/flux-general-multi`, `deepgram/nova-3-medical`, `assemblyai/universal-streaming`, `assemblyai/universal-3-6-pro`, `cartesia/ink-2`, `cartesia/ink-whisper`, `google/gemini-3.5-transcribe-live`, `speechmatics/linden-1`, `xai/stt-2` (V6-02) |
| `livekit-inference-llm` | llm | `livekit.agents.inference.LLM` | — | `temperature` (0.7) | `google/gemma-4-31b-it`; `google/gemini-3.5-flash`, `google/gemini-3.8-flash`, `google/gemini-3.1-flash-lite`, `openai/gpt-4.1`, `openai/gpt-4o-mini`, `openai/gpt-5.5`, `openai/gpt-5.4-mini`, `openai/gpt-5.6-luna`, `openai/gpt-oss-120b`, `xai/grok-4.7`, `deepseek-ai/deepseek-v4.1-flash` (V6-02) |
| `livekit-inference-tts` | tts | `livekit.agents.inference.TTS` | — | `voice` ("Ashley"), `language` ("en") | `inworld/inworld-tts-2`; `cartesia/sonic-3.6`, `cartesia/sonic-3`, `deepgram/aura-2`, `deepgram/flux-tts`, `fishaudio/s2.1-pro`, `gradium/default`, `rime/coda`, `rime/mistv3`, `xai/tts-1` (V6-02) |
| `google-realtime` | realtime | `livekit.plugins.google.realtime.RealtimeModel` | `api_key` | `voice` (enum, "Kore"), `temperature` (0.8), `tool_behavior` (enum `NON_BLOCKING`), `tool_response_scheduling` (enum `WHEN_IDLE`), `enable_affective_dialog` (false) | `gemini-3.8-live`; `gemini-3.1-flash-live-preview`, `gemini-2.5-flash-native-audio-preview-12-2025` — all `supports_video=true`; capabilities `video_input=true, silent_tool_reply=true` |
| `openai-realtime` | realtime | `livekit.plugins.openai.realtime.RealtimeModel` | `api_key` | `voice` ("marin"), `base_url` | `gpt-realtime`; `video_input=false`, `silent_tool_reply=true` |
| `deepgram-stt` | stt | `livekit.plugins.deepgram.STT` | `api_key` | `language` ("en-US") | `nova-3`; `nova-2` (Flux moved to `deepgram-flux-stt`, V6-02) |
| `openai-llm` | llm | `livekit.plugins.openai.LLM` | `api_key` | `base_url`, `temperature` | `gpt-4.1`; `gpt-4o`, `gpt-4.1-mini` |
| `google-llm` | llm | `livekit.plugins.google.LLM` | `api_key` | `temperature` | `gemini-2.5-flash`; `gemini-3.5-flash` |
| `cartesia-tts` | tts | `livekit.plugins.cartesia.TTS` | `api_key` | `voice`, `language` ("en") | `sonic-3.6`; `sonic-3` (V6-02) |
| `elevenlabs-tts` | tts | `livekit.plugins.elevenlabs.TTS` | `api_key` | `voice_id`, `encoding` (unset; V6-02) | `eleven_flash_v2_5`; `eleven_turbo_v2_5` (V6-02) |
| `openai-tts` | tts | `livekit.plugins.openai.TTS` | `api_key` | `voice` ("ash") | `gpt-4o-mini-tts` |
| `bey-avatar` | avatar | `livekit.plugins.bey.AvatarSession` | `api_key` | `avatar_id` (default `b9be11b8-89fb-4227-8f86-4a881393cbdb`) | — |
| `tavus-avatar` | avatar | `livekit.plugins.tavus.AvatarSession` | `api_key` | `face_id`, `pal_id` (both optional) | — |
| `google-image-gen` | image_gen | `lkap_agent.providers.image_gen.GoogleImageGen` | `api_key` | — | `gemini-3.1-flash-image` |
| `openai-image-gen` | image_gen | `lkap_agent.providers.image_gen.OpenAIImageGen` | `api_key` | `size` ("1024x1024") | `gpt-image-1` |
| `fastembed-embedding` | embedding | `lkap_api.kb.embed.FastEmbedEmbedder` | — | — | `BAAI/bge-small-en-v1.5` |
| `openai-embedding` | embedding | `lkap_api.kb.embed.OpenAIEmbedder` | `api_key` | — | `text-embedding-3-small` |

**Language capabilities (V5-31).** `ProviderCapabilities.languages` holds base codes (`hi`, not `hi-IN`) from the vendor's documentation; empty means *not recorded* (validators stay silent), never "none". Three STT-only fields: `language_detection` (the value of the entry's `language` field that asks for detection: `multi` for `livekit-inference-stt` and `deepgram-stt`, `multi` for `openai-stt`/`openrouter-stt` which the worker's factory maps to `detect_language=True`, `unknown` for `sarvam-stt`), `detect_languages` (what detection covers when narrower than `languages`: Deepgram Nova-3 `multi` = en, es, fr, de, hi, ru, pt, ja, it, nl) and `language_switch` (`update_options(language=...)` on the `STT` object reaches the running transcriber in livekit-agents 1.8.3: Inference, Deepgram, OpenAI/OpenRouter, Azure, Cartesia, Clova, fal Wizper, Fireworks, Mistral, SLNG, Smallest, xAI, Baseten; Google and Gladia take `languages=`, AssemblyAI `language_codes=`, Sarvam only per stream with a `model`, Palabra on new streams only). Recorded rows: Nova-3 on Inference and Deepgram, the OpenAI transcription list (57), Sarvam STT (24 codes, 1.8.3 plugin enum) and Sarvam TTS (11). Helpers: `LANGUAGE_CODE_PATTERN` (`^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$`), `LANGUAGE_NAMES`, `base_language`, `language_name`, `declares_language` (`None` for an empty list).

**Telephony variant and price note (V5-07).** `ProviderSpec.telephony_variant` (noise-cancellation entries only) is the dotted class the worker builds instead of `python_class` on a phone call when the agent uses the `telephony` conversation preset: `legacy-noise-cancellation` → `livekit.plugins.noise_cancellation.BVCTelephony`, `krisp-noise-cancellation` → `livekit.plugins.krisp.voice_isolation_telephony`. Neither plugin is installed on the worker yet (R-V5-3, ask #208), so the preset warns instead. `ProviderSpec.price_note` is a plain-language price line the console shows for a provider metered outside the price table (LiveKit Cloud noise cancellation; also used by the V5-20/V5-25/V5-45 entries).

**Speech streaming (V6-02, D-V6-2 … D-V6-8).** Four `ProviderCapabilities` fields for `stt`/`tts` entries: `streaming: bool | None` (whether the entry streams with its registry defaults, read from each plugin's `STTCapabilities`/`TTSCapabilities(streaming=...)` at livekit-agents 1.8.3; `null` = not recorded), `streaming_field` (the boolean field that turns streaming on when it is off by default: `openai-stt.use_realtime`, `rime-tts.use_websocket`), `streaming_note` (a plain-words condition, e.g. `google-tts` streams with Chirp 3 HD voices) and `end_of_turn` (STT only: the transcriber decides when the turn ends; `deepgram-flux-stt`, which the worker runs with `turn_detection="stt"`). `speech_streams(spec, fields)` answers for a stored reference. Every available speech entry records `streaming` or a note (a parity test). Not streaming at 1.8.3: `openrouter-stt`, `openrouter-tts`, `openai-tts`, `azure-tts`, `speechmatics-tts`, `hume-tts`, `groq-stt`, `groq-tts`, `fal-wizper-stt`, `aws-polly-tts`, `lmnt-tts`, `cambai-tts`, `clova-stt`, `simplismart-stt`, `simplismart-tts`, `mistral-stt` (default model), `elevenlabs-stt` (default model), and `openai-stt`/`rime-tts` unless their field is on. `FieldSpec.recommended` is the value the console pre-selects for a new agent while `default` stays unset (a stored agent resolves to the same kwargs): `openai-stt.use_realtime`, `rime-tts.use_websocket` (true), `elevenlabs-tts.encoding` (`pcm_24000`), `minimax-tts.audio_format` (`pcm`). `ModelSpec.deprecated` flags a vendor-deprecated id kept so stored references resolve (`note="deprecated by the vendor"`): `inworld-tts-1.5-max` (default now `inworld-tts-2`), `speech-02-turbo` (default now `speech-2.8-turbo`). `minimax-tts` ships from `livekit-plugins-minimax-ai`; `fireworksai-stt` is `removed` (Fireworks stopped its speech service on 2026-06-10); since V6-21 (S6-23) the worker fails a session that names a `removed` entry with the entry's own reason, before any import.

**Avatar aspect (V6-26).** `ProviderCapabilities.avatar_aspect: portrait|landscape|square | None` (avatar entries only) is the vendor-documented shape of the avatar's video, used by the session stage before the first frame arrives; `avatar_aspect_note` cites where it came from. Only `lemonslice-avatar` (portrait) and `anam-avatar` (landscape; per persona model at the vendor, R-V6-3 #160) set it; every other avatar entry stays unset rather than guessed (pinned by a contracts test).

**Reasoning models (V6-31).** Additive, `null` = unknown. `ModelCapabilities` gains `reasoning: bool | None`, `reasoning_efforts: list[ReasoningEffort] | None` (lowest first; `[]` = the model takes no effort setting) and `request_parameters: list[str] | None` (the chat parameters the model accepts, OpenRouter's `supported_parameters`). `ReasoningEffort` is the `openai` SDK's `none|minimal|low|medium|high|xhigh|max` (`REASONING_EFFORTS`, in order). `ModelSpec.reasoning`/`reasoning_efforts` record what the registry knows for a listed model (the OpenAI GPT-5 ids on `livekit-inference-llm`; `reasoning=false` on the plain GPT-4 ids). A `reasoning_effort` enum field with no default sits on `openrouter-llm`, `openai-llm` and `livekit-inference-llm` (`REASONING_EFFORT_FIELD`; the worker passes it to `with_openrouter`/`openai.LLM` as `reasoning_effort` and to `inference.LLM` in `extra_kwargs`). `reasoning_effort_to_send(capabilities, requested)` is the one rule: nothing known → the stored value; a model that does not reason or does not accept `reasoning_effort` → none; a stored value the model does not list → the next listed one above it; nothing stored on a reasoning model that lists efforts → the lowest. `accepts_parameter` answers for one parameter; the worker leaves out any of `OPTIONAL_REQUEST_PARAMETERS` (never `tools`) the model does not accept. `SLOW_VOICE_EFFORTS` (`medium` and up) is what the api's voice warning names.

Deferred entries (data only, `status="deferred"`): azure-openai-realtime, xai-realtime, assemblyai-stt, google-stt, openai-stt, speechmatics-stt, elevenlabs-stt, cartesia-stt, groq-stt, azure-stt, anthropic-llm, groq-llm, cerebras-llm, openai-compatible-llm, aws-bedrock-llm, google-tts, deepgram-tts, rime-tts, inworld-tts, hume-tts, azure-tts, simli-avatar, anam-avatar, bithuman-avatar, liveavatar-avatar.

Export: `uv run python -m lkap_contracts.export` writes `contracts/generated/providers.json` (`{"v":1,"providers":[...]}`), JSON Schemas for `AgentConfig`, `ResolvedAgentConfig`, `DispatchMetadata`, `UiSnapshot`, `UiPatch`, `ActivityEvent`, `PackManifest`, `ToolDefinition`, all `api_models`, and runs `json-schema-to-typescript` (via `pnpm dlx` in `web/`) to produce `generated/ts/lkap-contracts.d.ts`. A contracts test fails if generated files are stale. TS types are **generated, never hand-written**. Before the TS step, `prepare_for_typescript` (DECISIONS-W2 D-W2-3) injects `tsType: "unknown"` into every property schema that is empty (a Pydantic `Any` field such as `UiPatchOp.value`), so `json-schema-to-typescript` emits `value?: unknown` instead of an indexed object type.

---

## 5. Database schema (SQLAlchemy 2, Alembic; SQLite default, Postgres-compatible)

```sql
CREATE TABLE agents (
  id TEXT PRIMARY KEY,                 -- uuid4 hex
  slug TEXT NOT NULL UNIQUE,           -- url-safe, from name
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  pack_id TEXT NOT NULL DEFAULT 'generic',
  ui_panel_id TEXT NOT NULL DEFAULT 'generic',
  published INTEGER NOT NULL DEFAULT 0,
  config JSON NOT NULL,                -- AgentConfig v1
  config_version INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
);
CREATE TABLE credentials (
  id TEXT PRIMARY KEY,
  provider_id TEXT NOT NULL,           -- registry id
  label TEXT NOT NULL,
  ciphertext BLOB NOT NULL,            -- Fernet(json.dumps({field: value}))
  fingerprint TEXT NOT NULL,           -- "…" + last 4 chars of the first secret field
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
);
CREATE TABLE tools (
  id TEXT PRIMARY KEY,
  agent_id TEXT NULL REFERENCES agents(id) ON DELETE CASCADE,   -- NULL = shared
  kind TEXT NOT NULL CHECK (kind IN ('http','mcp')),
  name TEXT NOT NULL,                  -- model-facing name for http; display name for mcp
  definition JSON NOT NULL,            -- HttpToolDefinition | McpServerDefinition
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
);
CREATE TABLE knowledge_bases (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  embedder_id TEXT NOT NULL DEFAULT 'fastembed-embedding',
  chunk_count INTEGER NOT NULL DEFAULT 0,
  connection_id TEXT NULL REFERENCES knowledge_connections(id),  -- V5-20: NULL = the platform's store
  kind TEXT NOT NULL DEFAULT 'managed',                          -- V5-20: managed | external (V5-45)
  external_ref TEXT NULL,                                        -- V5-20: collection, index/namespace or tenant
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
);
-- V5-20 (v5_005): bring-your-own vector stores and hosted re-rankers. `settings` holds only the
-- non-secret fields of the kind's registry entry (provider kind `knowledge`: `qdrant`, `pinecone`,
-- `weaviate`, `cohere-rerank`, `voyage-rerank`, and V5-45's `ragie`); the key is a vault credential by id, returned only as
-- its fingerprint. A connection with knowledge bases cannot be deleted (409); its url/collection/index
-- cannot change while they exist. The vendor stores carry `id, kb_id, document_id` (+ the text only
-- when the store's own keyword search is on); chunk text stays in `kb_chunks` (D-V5-37).
CREATE TABLE knowledge_connections (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name TEXT NOT NULL, kind TEXT NOT NULL,           -- qdrant|pinecone|weaviate|cohere_rerank|voyage_rerank|ragie (no CHECK; the api validates)
  settings JSON NOT NULL, credential_id TEXT NULL REFERENCES credentials(id) ON DELETE SET NULL,
  status TEXT NOT NULL CHECK (status IN ('unverified','ok','error')),
  last_checked_at TIMESTAMP NULL, last_error TEXT NULL, capabilities JSON NOT NULL,
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
);
CREATE TABLE kb_documents (
  id TEXT PRIMARY KEY, kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  filename TEXT NOT NULL, mime TEXT NOT NULL, bytes INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','ready','failed')), error TEXT NULL,
  chunk_count INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMP NOT NULL
);
CREATE TABLE kb_chunks (
  id TEXT PRIMARY KEY, kb_id TEXT NOT NULL, document_id TEXT NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL, text TEXT NOT NULL, meta JSON NOT NULL   -- vector lives in LanceDB table kb_{kb_id}, row id = chunk id
);
CREATE TABLE agent_knowledge_bases (agent_id TEXT REFERENCES agents(id) ON DELETE CASCADE, kb_id TEXT REFERENCES knowledge_bases(id) ON DELETE CASCADE, PRIMARY KEY (agent_id, kb_id));
CREATE TABLE sessions (
  id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id),
  config_version INTEGER NOT NULL, room_name TEXT NOT NULL UNIQUE,
  participant_identity TEXT NOT NULL, participant_name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('created','active','ended','failed')),
  pipeline_mode TEXT NOT NULL, created_at TIMESTAMP NOT NULL, started_at TIMESTAMP NULL, ended_at TIMESTAMP NULL,
  usage JSON NULL, transcript JSON NULL, final_ui_state JSON NULL, error TEXT NULL
);
CREATE TABLE session_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  ts TIMESTAMP NOT NULL, type TEXT NOT NULL, payload JSON NOT NULL
);
CREATE INDEX ix_session_events_session ON session_events(session_id, id);
CREATE INDEX ix_sessions_agent ON sessions(agent_id, created_at);
```

**Pricing and cost (v4, `docs/v4/COSTS.md`, migration `v4_003_session_estimates`).** A session's
priced usage lands in `session_costs` (`provider_id, model, unit, quantity, unit_price_usd, cost_usd,
price_version, price_source, vendor_usd, vendor_ref`; only priced lines are stored — an unknown price
is `note="no price"` on read, never a zero) and sums into `sessions.cost_usd`. Prices resolve through
`lkap_contracts.pricing.quote()` from three sources in order: a **workspace** price
(`workspaces.settings["cost"]["prices"]`, USD, admin-entered), the **live** OpenRouter sheet (the
cached `/api/v1/models` catalog, USD per unit), then the **table** `pricing.PRICES` (each row with
`source_url`, `as_of`, and a `tier_note` for tiered pages; `PRICE_VERSION` bumps with every edit;
LiveKit Cloud minutes under the pseudo ids `livekit-agent`, `livekit-participant`, `livekit-sip`,
`livekit-egress`). Units (`pricing.Unit`, ≤ 16 chars): `tokens_in`, `tokens_out`, `cached_tokens_in`,
`text_tokens_in/out` and `audio_tokens_in/out` (realtime splits, never the folded total),
`audio_s_in`, `audio_s_out`, `chars`, `minutes`, `images`, `requests`. Every session also carries
the `CostEstimate` snapshotted after creation at its pinned `config_version`
(`sessions.estimate`), `sessions.estimated_usd` (per-minute mid × actual minutes + per-session
lines, set at summary time) and `sessions.reconciled_usd` (a vendor's own charge, V4-17);
`usage_daily.estimated_usd` rolls the estimates up per day.

**Caller memory (V5-40, migration `v5_008_memory`).** `memory_subjects` (`id, workspace_id, subject_id
VARCHAR(64), scope_key VARCHAR(64), agent_id, created_at, last_seen_at, retention_until`; unique
`(workspace_id, subject_id, scope_key)`, index on `retention_until`) is one caller the memory knows per
scope: `subject_id` is the hex HMAC-SHA256 of the caller's E.164 number or trusted participant identity
under the workspace's memory key (a vault `credentials` row with `provider_id = "memory-key"`, 32 random
bytes, created on first use; deleting it orphans every memory of the workspace), `scope_key` is the agent
id or `workspace`, `retention_until` = last remembered session + `memory.retention_days`.
`memory_events` (`id, workspace_id, subject_id, session_id → sessions SET NULL, agent_id, kind
recalled|stored|forgotten|purged|expired, count, created_at`) is the content-free audit trail and links a
session to its subject. The memories themselves live in the memory backend (Mem0: the `lkap_memory`
pgvector table on Postgres, a local Qdrant collection under `LKAP_DATA_DIR/memory/qdrant` on SQLite),
never in these tables; neither table stores a phone number or an identity.

**Datasets (V6-16, D-V6-27, migration `v6_002_datasets`).** `datasets` (`id, workspace_id, name,
slug` (unique per workspace), `format csv|json, columns JSON [{name, label, type, key}], key_columns
JSON [{name, type}], row_count, storage_key, sha256, status pending|ready|failed, created_at,
updated_at`) is a workspace's read-only lookup table; the uploaded bytes stay in the storage backend
under `datasets/<workspace>/<id>/source.<csv|tsv|json>` (V6-21, S6-18; rows stored before keep their key). `dataset_rows` (`id, dataset_id → datasets CASCADE, ordinal,
keys JSON, row JSON`; index `(dataset_id, ordinal)`) holds the cells by column name and the row's
normalised keys; `dataset_keys` (`row_id → dataset_rows CASCADE, column_name, dataset_id → datasets
CASCADE, value VARCHAR(256)`; primary key `(row_id, column_name)`, index `(dataset_id, column_name,
value)`) is the per-key lookup index (the ledger's `column`, renamed: a reserved word). The import's
progress and error live on its `jobs` row (`kind = dataset_import`, `payload.done/total/error`). The
same migration widens `tools.kind` to `('http','mcp','provider','dataset')`. Quotas
(`lkap_api.limits`): 100 datasets and 1,000,000 rows per workspace, checked before an upload is read; 10
uploads and 60 console test lookups a minute per workspace (V6-21, S6-14). Audit rows `dataset.create`,
`dataset.delete`, `dataset.import_failed` carry ids and counts, never a cell (S6-19).

---

## 6. Dispatch metadata and agent config (`lkap_contracts.dispatch`, `.agent_config`)

```python
class DispatchMetadata(BaseModel):
    """Serialised as JSON into RoomAgentDispatch.metadata. IDs ONLY — the browser can read it."""

    v: Literal[1] = 1
    session_id: str
    agent_id: str
    config_version: int
    participant_identity: str
```

```python
PipelineMode = Literal["realtime", "cascaded", "half_cascade"]   # half_cascade: v2


class ProviderRef(BaseModel):
    provider_id: str  # registry id
    credential_id: str | None = None  # required iff spec.requires_credential
    model: str | None = None  # None → spec.default_model
    fields: dict[str, str | int | float | bool] = {}  # non-secret fields, validated against spec.fields


class PipelineConfig(BaseModel):
    mode: PipelineMode = "cascaded"
    realtime: ProviderRef | None = None  # required when mode == realtime
    stt: ProviderRef | None = None  # required when cascaded
    llm: ProviderRef | None = None
    tts: ProviderRef | None = None
    avatar: ProviderRef | None = None
    # AvatarOptions (pipeline.avatar_options) gains, V6-26: framing: auto|portrait|landscape|square | None,
    # fit: contain|cover | None — unset on stored agents, rendered like auto + contain; display only
    image_gen: ProviderRef | None = None
    workflow_llm: ProviderRef | None = (
        None  # None → llm (cascaded) or livekit-inference-llm default (realtime)
    )
    # V5-07 (`lkap_contracts.turn_handling`): a typed mirror of livekit-agents 1.8.3 TurnHandlingOptions
    # (endpointing, interruption, preemptive_generation, user_turn_limit; unknown keys pass through),
    # stored as a plain dict of the keys that were set; a dict whose typed keys fail is kept as it is
    turn_handling: TurnHandlingOptions | dict = {}
    # a named preset replaces the turn-taking keys at session build (resolve_turn_handling,
    # CONVERSATION_PRESETS); never stored expanded; `custom` uses turn_handling as saved (R-V5-4)
    conversation_preset: Literal["patient", "balanced", "snappy", "telephony", "custom"] = "custom"
    turn_detector: TurnDetectorSettings | None = None   # {mode: hosted|local, unlikely_threshold: 0..1}
    # (v2 also added vad, turn_detection and noise_cancellation slots: CONTRACTS-V2)


class VoiceConfig(BaseModel):
    greeting: str = "Hello! How can I help you today?"
    greeting_mode: Literal["say", "generate"] = "say"
    language: str = "en"
    allow_interruptions: bool = True
    user_away_timeout_s: float | None = 15.0
    # V5-31: the agent's languages (first = default; empty = [language]; <= 10, unique tags),
    # automatic detection, and a text-to-speech voice per language (keys like `languages`).
    languages: list[str] = []
    auto_detect: bool = False
    voices_by_language: dict[str, ProviderRef] = {}
    # V5-07: "none", a built-in clip name lower-cased (office_ambience, city_ambience, crowded_room, ...)
    # or "asset:<id>" (accepted; the worker does not play uploaded clips yet); never on the text channel
    ambient_sound: str = "none"


class CapabilitiesConfig(BaseModel):
    camera: bool = False
    screen_share: bool = False
    chat_input: bool = True
    vision_inject_per_turn: bool = True


class ToolsConfig(BaseModel):
    builtin_disabled: list[str] = []
    http_request_enabled: bool = False
    tool_ids: list[str] = []  # rows in `tools`
    max_tool_steps: int = 3


class KnowledgeConfig(BaseModel):
    kb_ids: list[str] = []
    auto_inject: bool = True
    top_k: int = 4
    # V5-06; the defaults retrieve at least what an agent saved before them did
    min_score: float | None = None          # 0..1; in hybrid mode without rerank the score is rank-derived
    prefetch: bool = True                   # start the auto-inject search on the interim transcript
    rerank: Literal["none", "local"] | str = "none"   # or "connection:<id>" (V5-20): a hosted re-ranker,
                                            # search tool only; refused for auto-inject (D-V5-19)
    mode: Literal["vector", "hybrid"] = "hybrid"
    max_inject_tokens: int = 1200           # >= 1; upper bound on the knowledge note per turn
    skip_short_turns: bool = True           # skip backchannels, digits-only and very short turns
    query_mode: Literal["last_turn", "conversation"] = "conversation"


class AgentConfig(BaseModel):
    v: Literal[1, 2] = 2   # 2 since v2
    instructions: str
    pipeline: PipelineConfig
    voice: VoiceConfig = VoiceConfig()
    capabilities: CapabilitiesConfig = CapabilitiesConfig()
    tools: ToolsConfig = ToolsConfig()
    knowledge: KnowledgeConfig = KnowledgeConfig()
    pack_settings: dict = {}  # validated by PackManifest.settings_schema
    timezone: str = "UTC"


class ResolvedProvider(BaseModel):
    provider_id: str
    python_class: str
    model: str | None
    kwargs: dict[str, object]  # non-secret + secret fields merged, ready for the constructor


class ResolvedAgentConfig(BaseModel):
    """What the worker receives from /internal/v1/sessions/{id}/resolved. Contains secrets. Never logged."""

    v: Literal[2] = 2   # 2 since v2
    session_id: str
    agent_id: str
    agent_slug: str
    config_version: int
    pack_id: str
    ui_panel_id: str
    config: AgentConfig
    resolved: dict[
        Literal["realtime", "stt", "llm", "tts", "avatar", "image_gen", "workflow_llm"], ResolvedProvider
    ]
    tools: list["ToolDefinition"]
    kb_ids: list[str]
    participant_identity: str
```

The listing above is the v1 shape plus the v5 fields named in it. `AgentConfig` also has the v2 sections (CONTRACTS-V2) and the v5 sections described below and in §3 (`tools.apps`, the curated built-ins), and `ResolvedAgentConfig` carries further v5 fields (`mcp_oauth`, `builtin_providers`, `tool_mocks`, `compliance`, `voices_by_language`, `business_timezone`, `locale`, …); the generated JSON Schemas in `contracts/generated/` are complete.

**Languages (V5-31).** `effective_languages(voice)` is `voice.languages` or `[voice.language]`, so an agent saved before V5-31 behaves as before and registers no `switch_language`. `ResolvedAgentConfig.voices_by_language: dict[str, ResolvedProvider]` carries each voice resolved with its key (**secrets**; empty outside cascaded / half-cascade). The worker appends one fixed languages rule to `instructions` (`session_builder.with_language_rule`, so flow nodes get it too), builds the STT slot with the registry's `language_detection` value when `auto_detect` is on, and on a switch (the `switch_language` built-in, or `DETECTION_TURNS = 2` consecutive caller turns detected in another allowed language) calls `stt.update_options(language=...)` when the entry has `language_switch` and is not detecting, swaps the voice with `Agent.update_options(tts=...)` (no handoff: `on_enter` would re-run; a later flow node re-applies it) and appends a reply-language note at the tail of the chat context (on a detected switch; the tool's answer says it on a tool switch). Validators (`config_service.language_issues`): a language the STT entry does not list → error at `voice.languages`; `auto_detect` on an STT without detection, or a language detection does not cover → warning at `voice.auto_detect`; several languages on an STT that cannot switch → warning; a language with no voice that the agent's voice does not list → warning at `voice.voices_by_language`; each voice is checked like a `tts` slot at `voice.voices_by_language.<code>`.

**Consent and disclosure (V5-15, D-V5-22; `lkap_contracts.compliance`).** Additive fields; an agent saved before them behaves as before except that the AI disclosure is now spoken first (intended):

```python
class DisclosureConfig(BaseModel):          # AgentConfig.disclosure
    enabled: bool = True
    text: str | None = None                 # None = the workspace's (Settings → Compliance)
    position: Literal["greeting", "banner", "both"] = "both"

class RecordingConfig(BaseModel):           # additions
    require_consent: bool = False           # Egress starts only after an accepted `recording` consent
    consent_text: str | None = None         # the question when no consent block supplies one; None = the workspace's

class ResolvedAgentConfig(BaseModel):       # addition
    compliance: ResolvedCompliance | None = None   # {jurisdiction, disclosure_text, recording_text}; None = older api

class ComplianceSettings(BaseModel):        # workspaces.settings.compliance (extra keys forbidden)
    jurisdiction: Literal["eu", "in", "us"] = "in"
    disclosure_text: str | None = None      # None/blank = the preset's
    recording_text: str | None = None
    counsel_note_ack: bool = False
```

`COMPLIANCE_PRESETS` holds the three presets (plain wording, a counsel note each; `us` says "confirm with counsel for two-party-consent states"; no state list). The worker (`session_builder.apply_compliance`, after the flow preparation) fills `disclosure.text`, `recording.consent_text` and the empty `text` of `recording`/`ai_disclosure` consent blocks from `compliance`, then puts the disclosure in front of the greeting once (or at a `{disclosure}` placeholder) when `position` is `greeting`/`both`, or `banner` on a phone call; without a spoken greeting it becomes an instruction for the first reply. `consent_text_hash(text)` is the SHA-256 of the exact UTF-8 wording, carried by every `consent` event.

**Privacy and post-call fields (V5-30; `docs/research-v4/panels-and-capabilities.md` §4.2, C10 and C23).** Additive; the defaults keep every agent saved before them unchanged (nothing masked, everything kept, telemetry as before, no fields):

```python
class PrivacyConfig(BaseModel):             # AgentConfig.privacy
    stt_redact: list[Literal["pci", "pii", "phi", "numbers"]] = []  # the STT's `redact` kwarg, where supported
    storage_tier: Literal["full", "redacted", "basic"] = "full"      # what is kept after the call
    telemetry_pii: bool = True              # LIVEKIT_TELEMETRY_ALLOW_PII when an OTEL_EXPORTER_OTLP_* endpoint is set
    scrub_model: ProviderRef | None = None  # the optional LLM pass of the scrub (OpenAI / OpenRouter with a key)

class QaField(BaseModel):                   # QaConfig.fields (max 20, unique names)
    name: str                               # ^[a-z][a-z0-9_]{0,47}$ — the JSON key and the CSV column
    type: Literal["text", "number", "boolean", "select"] = "text"
    options: list[str] = []                 # select only (1-50 unique choices)
    description: str = ""

class ProviderCapabilities(BaseModel):      # addition
    redaction: list[str] = []               # deepgram-stt: ["pci", "pii", "phi", "numbers"]; Inference: [] (unverified)
```

The worker maps `stt_redact` onto the STT's `redact` kwarg for the classes its registry entry lists (else a validator warning, and the setting is ignored). The QA judge fills `qa.fields` in a second structured extraction (`lkap_contracts.qa.qa_fields_model`: one nullable, typed property per field; one repair retry; 30 s) into `session_qa.raw["fields"]` (`raw["fields_error"]` when it fails; the verdict is unaffected); both judge prompts get the transcript inside the `<untrusted>` fence. `storage_tier != "full"` schedules the `session_scrub` job once the session ends: emails, Luhn-valid card numbers and runs of 6+ digits are masked (`[email]`, `[card number]`, `[number]`) in `sessions.transcript`, every `session_events` payload (keys `id`, `ts`, `type`, `url`, `href` and `*_id(s)` kept) and `final_ui_state`, then the `scrub_model` pass (names, addresses, other details) when set; `basic` also drops `args_redacted` / `result_preview` / `message_preview` from the tool events. It runs once per session and records a `privacy_scrubbed {tier, replaced, model_pass, tool_payloads_dropped}` event whose `ts` is `SessionDetailOut.scrubbed_at`. The telemetry flag does not change what LiveKit Cloud Insights receives (the project's dashboard setting), and the SDK applies it once per tracer provider per worker process. STT redaction's live check is deferred (needs a Deepgram key).

**Test cases and the publish gate (V5-29, D-V5-29; `lkap_contracts.agent_tests`).** Additive; both default off, so an agent saved before them validates, resolves and publishes exactly as before:

```python
class AgentTest(BaseModel):                 # AgentConfig.tests: list[AgentTest] (max 50, unique ids)
    id: str                                 # ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$
    name: str
    persona_instructions: str               # who the simulated caller is (played by an LLM)
    scenario: str = ""                      # what the caller wants
    expectations: list[str] = []            # what must hold for the agent's side (≤ 20, ≤ 500 chars each)
    mocks: dict[str, Any] = {}              # tool name → fixture (HTTP tools and app actions only)
    max_turns: int = 12                     # 1..40 caller turns

class PublishGate(BaseModel):               # AgentConfig.publish_gate
    require_tests: bool = False             # opt-in
    min_pass_ratio: float = 1.0             # 0..1 share of cases that must pass

class ResolvedAgentConfig(BaseModel):       # addition
    tool_mocks: dict[str, Any] = {}         # a test case's scratch session only; {} for every real session
```

A run (`AgentTestRun`, `POST /v1/agents/{id}/tests/run`, job `agent_tests_run`) is pinned to the `config_version` it was queued on; the api plays each case's persona with the agent's `workflow_llm` (else `llm`) over a scratch `channel="text"` session and judges the transcript with `qa.model` (else `workflow_llm`, else `llm`) through its OpenAI-compatible client — five judges (`task_completion`, `tool_use`, `safety`, `relevancy`, `accuracy`), each `{verdict: pass|fail|inconclusive, score 0..1, reason}`, one repair retry for a non-JSON answer. Transcripts and tool output reach the persona and the judges inside the `<untrusted>` fence (R-V5-15). Run `status`: `queued`, `running`, `passed`, `failed`, `inconclusive`, `error` (did not run — never a test failure). The worker honours `tool_mocks` in `tools/declarative.py`: a mocked HTTP tool or app action returns its fixture (a string as-is, else JSON), fenced like a real result, with no request. Publishing (`PUT /v1/agents/{id}` with `published: true` on an unpublished agent) with `require_tests` on answers `422 tests_failing` with `details: PublishGateRefusal {reason: missing|running|failing|error, config_version, min_pass_ratio, run_id?, run_status?, pass_ratio?, error?}` unless the latest run on the version being published passed at least `min_pass_ratio` of its cases. Validation: a mock naming none of the agent's HTTP tools or app actions is an error (a warning when an MCP server is attached); `require_tests` with no cases is a warning. Tables: `agent_test_runs`, `agent_test_results` (`v5_006_agent_tests`).

### Live extraction and declarative rules (V6-13, D-V6-24/25; `lkap_contracts.{extraction,rules,rules_expr}`)

```python
class AgentConfig(BaseModel):               # additions (defaults: off / empty — no stored agent changes)
    extraction: ExtractionConfig = ExtractionConfig()
    rules: list[Rule] = []                  # ≤ 50, unique ids

class ExtractionConfig(BaseModel):
    enabled: bool = False
    fields: list[ExtractionField] = []      # ≤ 30, unique names
    triggers: list[ExtractionTrigger] = [every_n_turns(n=1)]   # one per kind
    min_turn_chars: int = 12                # 0..200; shorter caller turns do not count
    still_needed: Literal["checklist"] | None = None           # need_<field> items, merged into the checklist

class ExtractionField(VariableSpec):        # name, type, description, options, required
    label: str = ""; hint: str = ""; sensitive: bool = False
    show_in: str | None = None              # details:<block>[.<key>] | notebook:<block>.<section> (details or text section)

ExtractionTrigger = every_n_turns{n 1..20} | tool{tools[1..20]} | node_exit{nodes[]} | manual  # manual → `extract_now`

class Rule(BaseModel):
    id: str; label: str = ""; when: str     # ≤ 200 chars, the rules_expr grammar
    then: list[RuleAction]                  # 1..10: checklist.set_item | checklist.check | status.set | details.set
                                            # | note.push | var.set | escalate | instruct | disposition.set
    once: bool = True; enabled: bool = True
```

The condition grammar (`rules_expr.parse_condition` → a frozen-dataclass tree, `evaluate(tree, variables, tool_outcomes)`; never `eval`): `var.x is [not] set|empty`, `var.x ==|!= "text"|number|true|false`, `var.n >=|<=|>|< number`, `var.x matches /re/i`, `tool.<name>.ok|failed`, `not`, `and`, `or`, parentheses; ≤ 8 levels, ≤ 12 checks, literals ≤ 100 chars; a regex the guardrails scanner (`nested_repeat`) refuses — a repeated group holding an unbounded repeat or alternatives, or two overlapping unbounded repeats side by side (V6-21, S6-4) — is an `error` Issue from `rule_issues` at `rules[i].when`, so a save is a 422; since V6-28 the `Rule` model itself checks the grammar only (`parse_condition(…, safe_patterns=False)`), so a stored rule with such a pattern still loads, and the worker's strict parse skips it (`rules.condition_unreadable`). An unset variable makes every comparison false. The worker (`agent/extraction/**`, `agent/rules/**`) extracts in the background on `workflow_llm` (one call ≤ `EXTRACTION_BUDGET_S` = 2 s, cached by a transcript hash, never delaying the reply) into the shared variable store (`tools.context.session_variables`), marks the names in `userdata["lkap.extracted_variables"]` (fenced as `extraction` where a flow renders them into instructions; bound names as `tool_binding`, ask #31), then evaluates the rules; rules also run after each tool batch. Events: `extraction` (`ExtractionEvent`: trigger, status, `fields {name: set}`, `changed`, `still_needed`; `values` only on `storage_tier == "full"` and never for a `sensitive` field — the post-call scrub drops `values` on `redacted`/`basic`), `rule_fired` (`RuleFiredEvent`: rule id, label, action kinds, skipped kinds, trigger; never a value), and a rule's `escalate` records the ordinary `escalation` event. Validation (`config_service.extraction_rules_issues`): targets on the panel and of the right type (error; a notebook target must name a `details` or `text` section), unknown `var.`/`tool.` names, missing `node_exit` steps and flow-extracted overlaps (warnings).

Validation rules (api, at save): every `ProviderRef.provider_id` exists and matches the slot kind; `credential_id` present iff required and credential's `provider_id` matches; `model` in `spec.models` **or** free text (warn, not error — Inference lists churn); a realtime provider whose `spec.capabilities.video_input` is false combined with `capabilities.camera`/`screen_share` = warning (the model will not see frames; frames still reach the UI/pin path); avatar works with both modes.

### Flow `tool` node (V6-17, D-V6-28; `lkap_contracts.flow`)

```python
NodeKind = Literal["start", "agent", "end", "global", "transfer", "qa", "tool"]

class ToolNode(NodeBase):                   # kind="tool"
    tool: str                               # the `name` of an attached tool row (config.tools.tool_ids)
    mcp_tool: str | None = None             # an MCP server row: the one server tool to call
    arguments: dict[str, str | int | float | bool | None] = {}   # ≤ 20; text may use {{ var.* }} / {{ ctx.* }}
    bindings: list[ToolBinding] = []        # ≤ 20, V6-07's targets (var:, details:, table:, checklist:, status, note)
    timeout_s: float = 10                   # 0 < t ≤ 30
    on: ToolNodeOutcomes = {}               # {ok, error, empty} → edge ids leaving this node
```

Structure (`FlowSpec`, and `draft_flow_issues` with paths): `on.ok` is required; each named edge exists and leaves the node; every edge leaving a tool node is named by an outcome (its `condition` is not used); no loop consists of tool nodes only (`tool_only_cycles`). `argument_template_issues` refuses a bare `{{ name }}`, an unknown `ctx.` name or a bad variable name. `NODE_ID_PATTERN` and `VARIABLE_NAME_PATTERN` live in `common` (re-exported by `flow`) so `flow` can import `tool_context`.

Worker (`agent/flow/tool_node.py`, `FlowRuntime.transition`): entering a tool node speaks the incoming edge's `transition_speech`, settles pending extractions, renders the arguments in V6-07's single pass (a text that is exactly `{{ var.x }}` passes the stored value as is; a missing value is `error` and nothing is called; `confirmed` is never supplied), then runs the tool through livekit-agents' `execute_function_call` with a standalone `RunContext` on the session (the tool's own checks, execution policy, output guardrail and fence apply; the node waits up to `timeout_s`; an MCP row gets a toolset built for the one call). Outcome: `error` (raised, refused, timed out, not in the session), `empty` (a result that is nothing, `null`, blank, `[]` or `{}`, or bindings of which none found a value), else `ok`. The node's `bindings` apply to the unfenced, parsed result; the result text never reaches the model (bound variables reach later instructions fenced as `tool_binding`). The runtime takes `on.<outcome>` (`empty` falls back to `ok`); a tool node after a tool node runs in the same transition (at most 10). `error` without an `error` edge: a `flow_error` event `{node, tool, outcome, reason, message}`, the fixed apology `TOOL_STEP_FAILED_LINE`, and the job ends. A `start` node whose single edge leads to a tool node greets, then takes it itself (no edge tool, no routing instruction). Records: `handoff` into and out of the node (`reason` = the incoming reason, then the outcome), `tool_call_started {call_id, tool, args_redacted (the templates, never rendered values), flow_node}`, `tool_call_ended {…, status, outcome, reason, flow_node}`, an activity row, and the outcome for the rules engine's `tool.<name>.ok` (`LiveStructure.on_tool_outcomes`).

Api (`lkap_api.flows.validation`): `tool` must be an attached row (a built-in or pack name is an error); an MCP row needs `mcp_tool` from its `allowed_tools` (else `cached_tools`), any other row must not have one; a tool with `confirm_readback` cannot be a step (error); a `var:` binding must name a flow variable or an extraction field (error); binding blocks as V6-07; a `{{ var.* }}` nothing sets, an argument the tool lacks, a required argument not given and a missing `error` path are warnings. `GET /v1/flows/node-specs` lists the node as "Tool step".

---

## 7. API endpoints (`lkap_api`) — request/response models in `lkap_contracts.api_models`

Conventions: JSON; ids are uuid4 hex; timestamps ISO-8601 UTC; list endpoints return `{items: [...], total}`; errors `{"error": {"code": str, "message": str, "details": object|null}}` with 400/401/403/404/409/422/500. Auth headers: `X-Admin-Token`, `X-Service-Token`.

```python
# ---- shared
class Page(BaseModel, Generic[T]): items: list[T]; total: int
class ErrorBody(BaseModel): code: str; message: str; details: object | None = None
class ErrorResponse(BaseModel): error: ErrorBody

# ---- providers (admin)
GET  /v1/providers                       -> ProvidersResponse { v:1, providers: list[ProviderSpec] }
GET  /v1/providers/{provider_id}         -> ProviderSpec

# ---- credentials (admin)
class CredentialCreate(BaseModel): provider_id: str; label: str; secrets: dict[str, str]
class CredentialOut(BaseModel): id: str; provider_id: str; label: str; fingerprint: str; created_at: datetime; updated_at: datetime
class CredentialTestResult(BaseModel): ok: bool; message: str
POST /v1/credentials                     CredentialCreate -> CredentialOut (201)
GET  /v1/credentials?provider_id=        -> Page[CredentialOut]
PUT  /v1/credentials/{id}                CredentialCreate (secrets optional: omitted = keep) -> CredentialOut
DELETE /v1/credentials/{id}              -> 204 (409 if referenced by an agent)
POST /v1/credentials/{id}/test           -> CredentialTestResult   # cheap vendor call per provider (list models / 1-token completion); MVP may return ok=true,"not implemented" for avatar providers

# ---- agents (admin; GET by slug also public when published)
class AgentCreate(BaseModel): name: str; description: str = ""; pack_id: str = "generic"; ui_panel_id: str | None = None; config: AgentConfig | None = None   # None → pack defaults
class AgentUpdate(BaseModel): name: str | None; description: str | None; ui_panel_id: str | None; config: AgentConfig | None; published: bool | None
class AgentOut(BaseModel): id; slug; name; description; pack_id; ui_panel_id; published: bool; config: AgentConfig; config_version: int; created_at; updated_at
class AgentPublicOut(BaseModel): id; slug; name; description; ui_panel_id; capabilities: CapabilitiesConfig; pipeline_mode: PipelineMode
#   + avatar_framing: AgentAvatarFraming {framing, fit, declared_aspect} | None   # V6-26b: display hints only; None without an avatar
POST /v1/agents                          AgentCreate -> AgentOut (201)
#   Starter templates (lkap_contracts.templates.StarterTemplate, docs/v4/TEMPLATES.md) gained, V6-22 (ask #226):
#   extraction, rules, tests, dataset_seeds: [DatasetSeed {name, file, key_columns}], kits: [TemplateKit {kit_id,
#   variant, block_prefix, settings, dataset, key_columns, add_test_case}], all empty by default; creation applies
#   HTTP tool seeds, then lookup tables (reused by name), then kits, and validates once (ask #227).
GET  /v1/agents                          -> Page[AgentOut]
GET  /v1/agents/{id_or_slug}             -> AgentOut (admin) | AgentPublicOut (no token, published only)
PUT  /v1/agents/{id}                     AgentUpdate -> AgentOut (bumps config_version when config changes)
DELETE /v1/agents/{id}                   -> 204
POST /v1/agents/{id}/validate            -> ValidationResult { ok: bool; errors: list[str]; warnings: list[str] }

# ---- connect (public when published; admin token always allowed)
class ConnectRequest(BaseModel): participant_name: str = "Guest"; participant_identity: str | None = None; participant_metadata: dict[str,str] = {}
class ConnectResponse(BaseModel):
    serverUrl: str; participantToken: str; roomName: str; participantName: str   # TokenSourceResponse-compatible (camelCase, protobuf-JSON)
    sessionId: str; agent: AgentPublicOut; uiPanelId: str; protocolVersion: Literal[1] = 1
POST /v1/agents/{id_or_slug}/connect     ConnectRequest -> ConnectResponse
class TextSessionCreate(ConnectRequest): timezone: str | None = None   # R-V5-10
POST /v1/agents/{id_or_slug}/text-sessions TextSessionCreate -> ConnectResponse   # channel "text"
# R-V5-10: participant_metadata["timezone"] (and TextSessionCreate.timezone, which wins) is the browser's
# Intl.DateTimeFormat().resolvedOptions().timeZone. The api keeps it only when it is an IANA name
# (lkap_contracts.common.is_iana_timezone; an unknown name is dropped, never a 422) and stamps it as the
# participant attribute `lkap.tz`; every client key starting with `lkap.` is dropped first.
# Token: identity = participant_identity or f"user-{uuid[:8]}", ttl 2h, grants room_join/can_publish/can_subscribe/can_publish_data,
# room_config = RoomConfiguration(agents=[RoomAgentDispatch(agent_name=settings.agent_name, metadata=DispatchMetadata(...).model_dump_json())]).
# Any roomConfig/agentName sent by the client is ignored.
# V6-27: connect and text-sessions answer 409 no_worker_running on an external or supervised connection
# with no worker heard from in 90 s (LKAP_CALL_START_WORKER_CHECK=block; never in the api's first 120 s,
# never while another connection's live worker shares the server + agent name). Builders get a message
# naming the connection; a public caller gets PUBLIC_NO_WORKER_MESSAGE and no detail (R-V6-3 #181).
# V6-27, connections: create, an update that changes the url or agent_name, and POST /v1/connections/test
# answer 409 agent_name_in_use when another connection (any workspace) on the same normalised server has
# that agent_name; ConnectionTestResult.warnings names live workers of other connections sharing it;
# FleetStatus gains ready_workers and shared_agent_name_workers (counts only).

# ---- tools (admin)
class ToolCreate(BaseModel): agent_id: str | None; kind: Literal["http","mcp","provider"]; name: str; definition: ToolDefinition; enabled: bool = True   # provider: V5-47
class ToolOut(ToolCreate): id: str; created_at; updated_at
POST/GET/PUT/DELETE /v1/tools[/{id}]     (GET list filters: agent_id, kind)
POST /v1/tools/{id}/dry-run              { arguments: dict } -> { ok: bool; result: str; status_code: int | None; duration_ms: int }   # http only
POST /v1/tools/{id}/test                 -> McpTestResult                       # V5-09, mcp only; see §3
POST /v1/tools/{id}/oauth/start | GET …/oauth/status | POST …/oauth/revoke   # V5-14/V5-16; see §3
GET  /v1/oauth/mcp/callback | GET /v1/oauth/mcp/client-metadata.json          # V5-14; see §3
GET  /v1/tool-templates                  -> the Cal.com template set; POST /v1/tool-templates/{id}/instantiate   # V5-25; see §3
# ---- tool kits (V6-18, D-V6-26; lkap_contracts.kits; reads viewer + agents:read, instantiate builder + agents:write)
GET  /v1/tool-kits                       -> ToolKitsResponse {items: list[ToolKit]}   # rendered with each kit's default_prefix
GET  /v1/tool-kits/{id}                  -> ToolKit (404 unknown)
POST /v1/tool-kits/{id}/instantiate      ToolKitInstantiate -> ToolKitInstantiated   # 200; dry_run changes nothing
GET  /v1/panels/presets                  -> PanelPresetsResponse (viewer / agents:read)   # V6-08; see §10

# ---- apps (Composio; V5-18, V5-47, V5-53): /v1/tool-providers/composio/*, see §3 "Apps"

# ---- knowledge bases (admin)
class KbCreate(BaseModel): name: str; description: str = ""; embedder_id: str = "fastembed-embedding"
    connection_id: str | None = None    # V5-20: store the vectors in a knowledge connection (fixed after creation)
    kind: Literal["managed","external"] = "managed"; external_ref: str | None = None   # V5-45: external = a managed search service (needs both)
class KbOut(BaseModel): id; name; description; embedder_id; chunk_count: int; document_count: int; created_at; updated_at
class KbDocumentOut(BaseModel): id; kb_id; filename; mime; bytes; status; error: str | None; chunk_count; created_at
class KbSearchOptions(BaseModel): mode: Literal["vector","hybrid"] = "vector"; rerank: str = "none"   # none|local|connection:<id>
    min_score: float | None = None      # 0..1  (V5-04; every default is the pre-V5-04 behaviour)
class KbSearchRequest(KbSearchOptions): query: str (1..2000); k: int = 4 (1..20)
class KbHit(BaseModel): chunk_id: str; document_id: str; filename: str; score: float; text: str
    kb_id: str | None; meta: dict   # V5-01 locators: filename, heading_path, page, char_start, char_end
    vector_score: float | None; lexical_rank: int | None; fused_score: float | None; rerank_score: float | None
    score_source: Literal["vector","fused","rerank","external"] = "vector"
class KbSearchWarning(BaseModel): code: Literal["kb_not_found","kb_embedder_mismatch","kb_timeout","kb_error","lexical_unavailable","rerank_failed","rerank_refused"]; message: str; kb_id: str | None
class KbSearchResponse(BaseModel): hits: list[KbHit]; mode; rerank; min_score; dropped: int = 0
    warnings: list[KbSearchWarning]; rerank_usage: KbRerankUsage | None; timings_ms: dict[str, float]
POST/GET/PUT/DELETE /v1/knowledge-bases[/{id}]
POST /v1/knowledge-bases/{id}/documents  multipart file -> KbDocumentOut (202; ingestion in FastAPI BackgroundTasks; poll GET)
GET  /v1/knowledge-bases/{id}/documents  -> Page[KbDocumentOut]
DELETE /v1/knowledge-bases/{id}/documents/{doc_id} -> 204
POST /v1/knowledge-bases/{id}/search     KbSearchRequest -> KbSearchResponse
GET  /v1/knowledge-bases/{id}/source     -> KbSourceOut                         # where the vectors live (V5-20/V5-45)
GET|PUT /v1/knowledge-bases/{id}/evals   KbEvalSetIn -> KbEvalSetOut            # V5-01/V5-05 golden questions (≤ 500)
POST /v1/knowledge-bases/{id}/evaluate   -> KbEvalRunOut (202)                  # V5-05; job kb_evaluate
GET  /v1/knowledge-bases/{id}/evaluate/latest | /evaluate/{job_id} -> KbEvalRunOut
POST /v1/knowledge-bases/{id}/reindex    KbReindexIn -> KbReindexOut (202)      # V5-01: re-extract and re-chunk stored documents

# ---- datasets (V6-16, D-V6-27; lkap_contracts.datasets; reads viewer + agents:read, writes builder + agents:write)
POST /v1/datasets                        multipart file (.csv|.tsv|.json, <= 5 MiB) + name + key_columns (JSON {column: string|phone|email|number})
                                         -> DatasetOut (201, status pending; 413 > 5 MiB, 415 another type, 422 with a plain reason:
                                            > 50,000 rows, > 64 columns, unknown key column, key cell > 256 chars, unreadable; 409 quota)
GET  /v1/datasets                        -> DatasetPage
GET  /v1/datasets/{id}                   -> DatasetOut (progress, error from the dataset_import job)
GET  /v1/datasets/{id}/rows?offset&limit -> DatasetPreviewOut (limit <= 200)
GET  /v1/datasets/{id}/export            -> text/csv (formula-looking cells prefixed with ')
DELETE /v1/datasets/{id}                 -> 204 (409 naming the tools that still use it)
POST /v1/datasets/{id}/lookup            DatasetLookupIn {keys, match exact|prefix, return_columns, max_rows <= 20} -> DatasetLookupOut
                                         (409 while importing or after a failed import; 422 a non-key column, a short prefix)

# ---- knowledge connections (V5-20, V5-45; reads builder + providers:read, writes admin + providers:write)
GET|POST /v1/knowledge-connections       -> KnowledgeConnectionPage | KnowledgeConnectionCreate -> KnowledgeConnectionOut (201)
GET|PUT|DELETE /v1/knowledge-connections/{id}   KnowledgeConnectionUpdate -> KnowledgeConnectionOut (DELETE 409 while it stores knowledge bases)
POST /v1/knowledge-connections/{id}/test -> KnowledgeConnectionTestOut          # 10 per minute

# ---- packs (admin)
class PackOut(BaseModel): manifest: PackManifest
GET  /v1/packs                           -> { items: list[PackOut] }

# ---- sessions (admin)
class SessionOut(BaseModel): id; agent_id; agent_name; config_version; room_name; status; pipeline_mode; created_at; started_at; ended_at; usage: dict | None; error: str | None
    caller_timezone: str | None   # R-V5-10: from the `locale` event, stored as usage["caller_timezone"] by the summary
class SessionDetailOut(SessionOut): transcript: list[TranscriptTurn] | None; final_ui_state: UiState | None
class TranscriptTurn(BaseModel): role: Literal["user","assistant"]; text: str; ts: float; interrupted: bool = False; language: str | None = None   # V5-31
class SessionEventOut(BaseModel): id: int; ts: datetime; type: str; payload: dict
GET  /v1/sessions?agent_id=&status=      -> Page[SessionOut]
GET  /v1/sessions/{id}                   -> SessionDetailOut
GET  /v1/sessions/{id}/events?after_id=  -> Page[SessionEventOut]
POST /v1/sessions/{id}/listen-token     -> SessionListenTokenOut   # V5-37: builder+, API keys sessions:listen (sessions:write implies it)
POST /v1/sessions/{id}/whisper          SessionWhisperIn {text ≤ 1000, reply_now=false} -> SessionWhisperOut {id, delivered_to} (202)
class SessionAssetOut(BaseModel): id; session_id; kind: Literal["upload","frame","signature","document"]; name; mime; size; sha256; meta: dict[str,str]; created_at; url: str | None; expires_at: datetime | None
GET  /v1/sessions/{id}/assets           -> SessionAssetPage { items }   # V5-19; viewer + sessions:read; oldest first; each item has a signed link valid 15 minutes
GET  /v1/sessions/{id}/assets/{asset_id}/content?exp=&sig=  -> bytes   # no token: the signature (workspace, session, file, expiry) is the authorisation; 401 otherwise
GET  /v1/sessions/export.csv            -> text/csv                     # V5-30, see below
POST /v1/sessions/{id}/scrub            -> SessionScrubOut (202)        # V5-30, see below
GET  /v1/sessions/{id}/memory           -> SessionMemoryOut             # V5-40, see below
DELETE /v1/memory/subjects/{subject_id} | POST /v1/memory/purge          # V5-40, see below
POST /v1/agents/{id}/tests/run (202); GET /v1/agents/{id}/tests/runs; GET …/tests/runs/{run_id}   # V5-29, §6
POST /v1/hooks/link/{session_id}        LinkHookIn -> LinkHookOut (202) # V5-43, signed; §10

# ---- internal (service token)
GET  /internal/v1/sessions/{id}/resolved -> ResolvedAgentConfig   (404 if unknown; 409 if status=ended; marks status=active, started_at)
POST /internal/v1/tools/{id}/oauth/token  McpOAuthTokenIn -> McpOAuthTokenOut   (V5-16; 404/409/503, see §3)
class SessionEventIn(BaseModel): ts: float; type: str; payload: dict
POST /internal/v1/sessions/{id}/events   { events: list[SessionEventIn] } -> 202
class SessionSummaryIn(BaseModel): status: Literal["ended","failed"]; usage: dict; transcript: list[TranscriptTurn]; final_ui_state: UiState | None; error: str | None = None
PUT  /internal/v1/sessions/{id}/summary  SessionSummaryIn -> 204
class InternalKbSearchRequest(KbSearchOptions): kb_ids: list[str]; query: str (1..2000); k: int = 4 (1..20)
    session_id: str | None = None        # V5-27: only that session's workspace is searched
    purpose: Literal["tool","auto_inject"] | None = None   # auto_inject never uses a hosted re-ranker (D-V5-19)
                                                           # and skips managed search knowledge bases; the worker does not send it yet (ask #326)
POST /internal/v1/kb/search              InternalKbSearchRequest -> KbSearchResponse
POST /internal/v1/datasets/{id}/lookup   InternalDatasetLookupIn (DatasetLookupIn + session_id) -> DatasetLookupOut   # V6-16: the session's
                                         # workspace only; another workspace's dataset or an unknown session is a 404
POST /internal/v1/sessions/{id}/assets   multipart file (≤ 25 MB), kind upload|frame|signature, name?, meta? (JSON) -> SessionAssetOut (201)   # V5-19: type sniffed from the bytes, checked against the block; ≤ 50 files per session
POST /internal/v1/sessions/{id}/assets/from-document  SessionAssetFromDocumentIn {document_id} -> SessionAssetOut (201 new, 200 already held; 404 outside the agent's knowledge bases; 415 not showable)   # R-V5-5
GET  /internal/v1/sessions/{id}/assets/{asset_id}/content  -> bytes
POST /internal/v1/sessions/{id}/recording/stop  -> {stopped, egress_id}   # V5-27, see below
POST /internal/v1/memory/recall          MemoryRecallIn -> MemoryRecallOut   # V5-40, see below

# ---- health (public)
GET  /v1/health                          -> { ok: bool; version: str; livekit_url: str; packs: list[str]; db: "ok"|"error" }
```

Event `type` values posted by the worker: `session_started`, `agent_state` (`{state}`), `user_turn` (`{text}`; V5-31 adds `language` when the transcriber reported one), `agent_turn` (`{text, interrupted}`; V5-31: may carry the reply `language` of a multilingual agent, but only when the message was stamped before the event was recorded — the stored `TranscriptTurn.language` is the reliable record), `language_switched` (`LanguageSwitchedEvent {from_language, to_language, source: tool|detected, stt_switched, voice_switched}`, V5-31), `tool_call_started` (`{call_id, tool, args_redacted}`), `tool_call_ended` (`{call_id, tool, status, duration_ms, result_preview}`), `tool_call_updated` (`{call_id, tool, message_preview}`: a background tool reported progress; its first update is the announcement, docs/v4/BACKGROUND-TOOLS.md D-V4-38), `tool_reply` (`{call_ids, status, speech_id}`: the deferred reply that voices background results; `status` is `scheduled`, `completed`, `interrupted` or `skipped`, the last meaning the model had already said it), `workflow_run` (`{name, duration_ms, status}`), `ui_state` (`{seq}` only), `asset` (`{asset_id, kind, bytes}`), `escalation` (`EscalationEvent {reason, urgency, mode}`; `mode` since V5-37, left out for `transfer` so the old payload is unchanged), `metrics` (`{kind, data}`), `error` (`{message}`), `info` (`{message}`), `session_ended` (`{reason}`), `locale` (`LocaleEvent {caller_timezone, source, business_timezone}`, once at session start, R-V5-10: `source` is `browser` (the `lkap.tz` attribute), `number` (the caller's E.164 number maps to exactly one zone), `business` (`AgentConfig.timezone`, also when `locale.caller_timezone == "business"`), or `default` (UTC); `workspace` is in the schema but not emitted, because the api resolves the workspace default (`workspaces.settings.locale.timezone`) into `config.timezone`, which then reports as `business`; the summary copies `caller_timezone` into `usage`), `consent` (`ConsentEvent {kind, accepted, method, text_hash, block_id}`, one per answer, V5-15: `kind` is `recording`, `ai_disclosure`, `terms` or `custom`, `method` is `tap` or `voice`, `text_hash` the SHA-256 of the exact wording; the api folds the latest answer per kind into `sessions.consent_state` (`ConsentState {latest: {kind: ConsentRecord}}`, migration `v5_009_consent`)), `tool_needs_reauth` (V5-47 / V5-16: `{call_id, tool}` when a tool call failed because its app or MCP sign-in needs an admin — the model heard "This app needs to be reconnected by an admin" or "This integration needs to be re-authorised by an admin"; `{mcp_server, reason}` with `reason` `needs_reauth`, `unauthorized` or `insufficient_scope` when an MCP request other than a tool call hit it, once per server and reason), `privacy_scrubbed` (V5-30, api-written, not by the worker: `{tier, replaced: {email, card, number}, model_pass: none|done|failed|unsupported, tool_payloads_dropped}`, once per session; its `ts` is `scrubbed_at`).

Consent routes (V5-15): `POST /internal/v1/sessions/{id}/recording/start` answers 409 for an agent with `recording.require_consent` until the latest `recording` answer is an acceptance (the worker holds the start back and flushes its events first). At the summary, a consent-gated voice session that was never recorded keeps `recording_status = "none"` with `recording_error` "Not recorded: consent declined" (or "Not recorded: the caller did not agree to be recorded"), returned as `SessionDetailOut.recording.error`. `PUT /v1/workspaces/{id}` accepts `settings.compliance` (`ComplianceSettings`, merged one level down, 422 when invalid); `GET /v1/workspaces/{id}/compliance` → `ComplianceOut {settings, effective: ResolvedCompliance, presets: list[CompliancePreset]}`.

V5-30 (privacy and post-call fields): `QaOut.fields: dict[str, Any]` (`session_qa.raw["fields"]`, `{}` when none); `SessionDetailOut.scrubbed_at`; `GET /v1/sessions/export.csv` (the list's filters, newest first, `limit` ≤ 5000) → `text/csv` with the fixed columns `session_id, agent_id, agent_name, channel, status, created_at, ended_at, duration_s, cost_usd, disposition, qa_status, qa_score, qa_sentiment` then one column per post-call field (a field named like a fixed column is `field_<name>`; a text cell starting `= + - @` gets a leading `'`); `POST /v1/sessions/{id}/scrub` → 202 `SessionScrubOut {status: queued|already_scrubbed, job_id, scrubbed_at}`, 409 `storage_tier_full` / `not_ended`; `DELETE /v1/sessions/{id}` also removes the session's stored files and recording at once (S5-36). Knowledge (S5-28/S5-30): an upload's extension decides its type (`.md .markdown .txt .csv .json .pdf .docx .pptx .xlsx .html .htm`, else 415) and a url import's chosen filename cannot pick another extractor; 409 `quota_exceeded` past 1,000 documents per knowledge base or 2 GiB per workspace; `POST …/reindex` checks existence without reading bytes and, like `POST …/evaluate`, answers 409 while one is queued or running for that knowledge base, with a shared 20/min per-workspace limit (429). The `session.qa_completed` webhook carries them as `data.fields` (the worker scores after `session.ended` is sent, so `session.ended` has none).

V5-27 (the V5-26 security fixes; `docs/v5/SECURITY-REVIEW-V5.md`): `ConsentEvent.turn_id` (a voice answer's user turn; null for a tap) and `ConsentState.withdrawn_at {kind: epoch seconds}` (an acceptance replaced by a decline) are additive; `record_consent` no longer takes `method` (always `voice`; a tap comes only from the block). `POST /internal/v1/sessions/{id}/recording/stop` (service token) → `{stopped, egress_id}` stops the session's Egress once when the caller withdraws recording consent; `recording_error` then says "Stopped early: the caller withdrew consent". `InternalKbSearchRequest` gains `session_id` (only that session's workspace is searched; unknown session → 404) and both search requests cap `query` at 1–2000 characters. `user_turn` events carry `turn_id`; the worker records `recording_stopped {reason: consent_withdrawn}`. Third-party text reaches the model as `<untrusted source="…">…</untrusted>` (R-V5-15). `PUT /v1/workspaces/{id}` accepts only `locale`, `compliance`, `cost.reconcile` and `telephony` in `settings`. Any request body over 26 MB is `413 payload_too_large`.

V5-40 (caller memory, D-V5-17; opt-in): `AgentConfig.memory: MemoryConfig {enabled=false, scope: agent|workspace = agent, retention_days=90 (1..3650), consent_line ≤ 500 | null, max_recall_tokens=400 (50..2000), verbatim=false}`. `POST /internal/v1/memory/recall` (service token; `MemoryRecallIn {session_id, caller_e164: E.164 | null}` → `MemoryRecallOut {status: recalled|empty|disabled|no_identity|unavailable|failed, memories ≤ 20 × ≤ 500 chars, newest first, remember}`): the api resolves the caller from the session (phone: `caller.from` inbound / `caller.to` outbound, else the hint; other channels: `participant_identity` unless platform-generated `user-xxxxxxxx` or the `<channel>-caller` placeholder), reads the backend within 3 s, masks the texts when `privacy.storage_tier != "full"` and records `memory_recalled`; `remember` is true when the session will be written (identity known, backend installed, `verbatim` or an OpenAI/OpenRouter model with a key). The worker appends the memories to the instructions inside `<untrusted source="memory">` (bounded by `max_recall_tokens` at ~4 chars/token, whole memories only) and the consent line when `remember`. After the summary commits, `memory.enabled` enqueues the job **`memory_remember`**: the transcript (latest 200 turns; masked like the scrub's deterministic pass when the tier is not `full`; the caller's lines only when `verbatim`) goes to the backend, which extracts facts with the agent's `pipeline.llm` / `workflow_llm` (OpenAI or OpenRouter at the registry's base URL, through the platform's guarded client); once per session; records `memory_stored`. `DELETE /v1/memory/subjects/{subject_id}` → `MemoryForgetOut {subject_id, forgotten, sessions_updated}` (every scope; 404 unknown, 503 `memory_unavailable` when the backend is missing, nothing deleted); `POST /v1/memory/purge` (`MemoryPurgeIn {confirm: true}`, 422 otherwise) → `MemoryPurgeOut {status: queued|nothing_to_purge, subjects, job_id}` deletes the memory key and the subject rows at once and the backend entries in the job **`memory_purge`**; `GET /v1/sessions/{id}/memory` → `SessionMemoryOut {enabled, subject_id, recall_status, recalled, store_status, store_reason, stored, forgotten_at}`. Forget, purge and the retention sweep (the sessions sweep, `retention_until` past) blank the `memories` of the affected sessions' `memory_recalled`/`memory_stored` events (`forgotten: true`) and record `memory_forgotten {reason: caller|workspace|retention}` on each. api-written events: `memory_recalled` (`MemoryRecalledEvent {status, count, memories, forgotten}`), `memory_stored` (`MemoryStoredEvent {status: stored|nothing_new|skipped|failed, count, memories, reason, forgotten}`), `memory_forgotten`. `/v1/memory/*`: reads `viewer` + `sessions:read`, writes (forget, purge) `admin` + `sessions:write` (`ROUTE_POLICY`); `GET /v1/sessions/{id}/memory` follows the sessions rule (`viewer` + `sessions:read`). Validators (warnings): memory on without the backend installed (`memory.enabled`), or not verbatim without an OpenAI/OpenRouter model with a key (`memory.verbatim`).

V5-32 (answering-machine detection, warm transfer, the handoff block; migration `v5_007_telephony_amd` adds the nullable `calls.amd_result`, `.transfer_mode`, `.transfer_summary`): `AgentConfig.telephony.amd: AmdConfig {enabled=false, on_machine: hangup|leave_message = hangup, message ≤ 1000, ivr_detection=false}` and `TransferTarget.mode: cold|warm = cold`. `CallOut` gains `amd_result` (`human`, `machine-ivr`, `machine-vm`, `machine-unavailable`, `uncertain` — livekit-agents 1.8.3 `AMDCategory`; `null` = no detection ran), `transfer_mode` and `transfer_summary`. `POST /internal/v1/telephony/calls/report` (`CallReportIn`) gains `status: "transferred"` and `amd_result`, `transfer_mode`, `transfer_to`, `transfer_summary`: the verdict is stored once (a later report never overwrites it) and a `machine-*` verdict queues the new webhook **`call.voicemail`** (`data` = the `call.*` fields plus `amd_result`, same `call_event` outbox as `call.started/ended`); `transferred` moves the row forward (a warm transfer never passes through the SIP REFER route) and keeps the mode, target and summary (a cold fallback keeps its summary here instead of speaking it, D-V5-21). `ResolvedAgentConfig.warm_transfer: WarmTransferRoute {trunk_id, caller_id, targets} | null` — filled only for a phone session on a LiveKit Cloud connection with exactly one synced outbound trunk, naming the `warm` targets the dialing policy allows at resolve time (the worker dials those itself; `null` = every transfer is cold). Worker events: `voicemail` (`VoicemailEvent {result, action: hangup|leave_message|navigate, message_left}`, machine verdicts only) and `transfer` (`TransferEvent`: the V2-17 `{to, ok, status, reason}` plus `mode` (what ran), `requested_mode` (the target's), `target` (label), `outcome: connected|transferred|timeout|declined|refused|failed`, `summary`); a warm request that falls back also records `info` ("Warm transfer is not available (…); transferring directly."). `transfer_call` gains an optional `summary` argument. Validators (warnings): a `warm` target off LiveKit Cloud or without exactly one outbound line (`telephony.transfer_targets[i].mode`), `amd.enabled` without an outbound line or SIP, or with a realtime/half-cascade pipeline (`telephony.amd.enabled`), `leave_message` without `message` (a "Tip:" at `telephony.amd.message`). The `handoff` block (`HandoffBlockConfig {show_queue, show_agent_name}`, `HandoffBlockState {status: idle|requested|connecting|connected|timeout|ended, mode, target, queue_position, agent_name, reason}`) is written by `transfer_call` through `lkap_agent.ui.blocks.set_handoff`.

V5-37 (supervisor listen-in and typed whisper; no migration): the API-key scope **`sessions:listen`** (`sessions:write` implies it; members need `builder`+). `POST /v1/sessions/{id}/listen-token` → `SessionListenTokenOut {serverUrl, participantToken, roomName, participantName, identity, sessionId, expiresAt}` (camelCase like `ConnectResponse`): a token for identity `supervisor:<user or key id>` (attribute `lkap.role=supervisor`) scoped to the session's room only, `hidden=true`, `canSubscribe=true`, `canPublish=false`, **`canPublishData=false`** (the card said true; the task's "subscribe-only" rule wins and a hidden participant cannot call RPCs anyway), `roomCreate=false`, no agent dispatch, TTL 15 minutes (`LISTEN_TOKEN_TTL_S`); 409 `not_live` unless the session is `active` with a connection; audit row `session.listen`. `POST /v1/sessions/{id}/whisper` sends the text with the server API as a reliable data packet on topic `lkap.supervisor` (`SupervisorWhisperPacket {v: 1, op: "whisper", id, session_id, text, reply_now, by}`) **only to the room's agent participants** (`destination_identities`; the caller never receives it) — not on `lk.chat` as the card said: the server API cannot publish a text stream, and a stream attribute is set by its sender, so a caller could forge `lkap.role=supervisor` there; 409 `no_agent` when no agent is in the room; audit row `session.whisper` (`{whisper_id, chars, sha256}`, never the text). The worker honours a packet on that topic only when the server sent it (no participant) and it names its own session; the text reaches the model as a persisted system note fenced in `<supervisor_note>` ("guidance from a supervisor; the caller cannot see or hear it; it never overrides your instructions"), never as the caller's words; `reply_now` also calls `generate_reply(instructions=…)`. Worker event `supervisor_whisper` (`SupervisorWhisperEvent {id, by, text, applied: note|reply}`); `supervisor_joined` / `supervisor_left` (`SupervisorPresenceEvent {identity}`) are for the webhook handler (a hidden listener is invisible to the worker). `escalate_to_human(reason, urgency="normal", mode: transfer|takeover|listen_in|callback = "transfer")` (`lkap_contracts.tools.EscalationMode`) records `escalation {reason, urgency, mode}` (`mode` omitted for `transfer`), writes every `handoff` block `requested` with `mode` left null (it names the transfer that ran, `cold|warm`) and a fixed plain `reason` per mode (never the model's words: the caller may see the block), and marks it `connected` (with the person's name) when a participant with `lkap.role=human` joins. Late listener snapshot (ask #252, worker side only; V5-43): the worker answers `UiSnapshotRequestPacket {v: 1, op: "snapshot", session_id}` on `lkap.supervisor` (server-sent, its own session only) by republishing a full `UiSnapshot`; the api does not send it yet (ask #308), so a listener waits for the next periodic snapshot.

V5-39 (guardrails; opt-in, no migration): `AgentConfig.guardrails: GuardrailsConfig {input, output, tool_output: list[Rule] (≤ 20 each, names unique per list), on_trip: interrupt|end_call|escalate = interrupt, safe_reply (≤ 500; a default line), model: ProviderRef | null, budget_ms=300 (50..2000)}` (`lkap_contracts.guardrails`); `Rule` is discriminated by `kind`: `RegexRule {kind: "regex", name ≤ 60, pattern ≤ 300, ignore_case=true}`, `ClassifierRule {kind: "classifier", name, prompt ≤ 1000}` (what the text must not do, in plain words), `ProviderRule {kind: "provider", name, provider: "openai_moderation", categories: [OpenAI moderation category] (empty = anything flagged), credential_id | null}`. Validators (`config_service.guardrails_issues`; a save with an error is refused with 422 and the issue on the field): a pattern that does not compile, or that repeats a repeated group (`(a+)+`), → error at `guardrails.<stage>[i].pattern`; a classifier rule with no `guardrails.model`, no `pipeline.workflow_llm` and no cascaded `pipeline.llm` → error at `guardrails.<stage>[i]`; a moderation rule without an OpenAI key (its own `credential_id`, else the agent's own OpenAI key) → error, one key for every moderation rule; `guardrails.model` is checked like a pipeline `llm` slot, and set with no classifier rule → warning; `on_trip=escalate` with `escalate_to_human` switched off → warning. Session resolve: `ResolvedAgentConfig.builtin_providers` gains `guardrails_llm` (`guardrails.model` resolved with its key, only with a classifier rule) and `guardrails_moderation` (`{api_key}` only; OpenAI's own endpoint). Worker: input rules in `on_user_turn_completed` (regex first; model rules overlap the knowledge and vision injection; a trip speaks the safe reply and raises `StopResponse`, so the caller's turn is neither kept nor answered), and for a realtime model with server-side turns on the committed caller message in parallel (a trip interrupts); output rules in `Agent.transcription_node`, sentence by sentence while the text streams through untouched (a trip → `session.interrupt(force=True)` while that reply still plays, then the safe reply) — not on `conversation_item_added` as the card said, which fires after playout; tool-output rules on every result of `run_with_policy` (`tools.execution.guard_tool_output`; built-in, HTTP, connected-app and opted-in pack tools; MCP toolsets are not covered yet), a trip replacing the result with a fixed "withheld" line that carries the safe reply. `end_call` ends the job once the safe reply has played; `escalate` calls `escalate_to_human(reason="A guardrail tripped (<rule>).", urgency="high")` (never the caller's words). Fail directions: regex rules cannot time out; classifier and moderation rules **fail open** past `budget_ms`, on an error or without a key. Events: `guardrail` (`GuardrailEvent {stage: input|output|tool_output, rule, kind, action: interrupt|end_call|escalate|replaced, excerpt_hash, excerpt | null, categories, tool | null, latency_ms}`; `excerpt_hash` is a keyed hash with a per-session random key, `excerpt` ≤ 120 characters only when `privacy.storage_tier == "full"`) and `guardrail_timeout` (`GuardrailTimeoutEvent {stage, rule, kind, reason: timeout|error|unavailable, budget_ms}`). The activity feed gets a row with `ActivityEvent.kind = "guardrail"` (`source="guardrail"`, `detail {stage, rule, action}`); `kind` is `null` on every other row.

V6-18 (tool kits, D-V6-26; no migration; `lkap_contracts.kits`, catalogue `api/src/lkap_api/templates/kits/*.json`): `ToolKit {id, name, summary, default_prefix, default_variant, variants: [KitVariant], defaults: [KitSetting {name, label, help, kind: url|host|text|integer, required, example, default, variants}], blocks: [KitBlock (BlockSpec + shared)], instructions_snippet, variables: [ExtractionField], extraction: KitExtraction {enabled, still_needed} | null, rules: [Rule], flow_nodes: KitFlowFragment | null, test_case: KitTestCase | null, docs_url}`; `KitVariant {id, label, summary, source: http|mcp_preset|composio_action|dataset|none, tools: [KitTool {key, label, risk, definition: ToolDefinition | null, template: tool template id | null, only_with: "sms" | null, fake}], apps: [KitApp {toolkit, label, actions: [KitAppAction {slug, key, label, risk, fake}]}], requires: KitRequires {secret_names, apps, dataset, min_key_columns, sms}, blocks, variables, rules, instructions_snippet (replaces the kit's), flow_nodes (replaces the kit's), configures: ["notify_team"]}`; `KitFlowFragment {nodes: [FlowNode], edges: [FlowEdge] (source/target "@anchor" = the step named by the request), variables: [VariableSpec], anchor_extract}`. The catalogue writes `{{ kit.prefix }}`, `{{ kit.<setting> }}`, `{{ kit.dataset_id }}`, `{{ kit.key_column }}` and `{{ kit.tool.<key> }}`; the api renders them (a setting value may not hold `{{`/`}}`; a url setting is `https` with no user, query or fragment and passes the network guard) and `GET /v1/tool-kits` shows each kit rendered with its `default_prefix` and the settings' examples. Loader rules (import time, like the starter catalogue): every variant renders; every snippet passes `snippet_problems` (no link or site name, no key name, no comment marker, no placeholder; ≤ 2,000 characters); HTTP tools are `https` and name only the variant's `secret_names`; what a kit names starts with its prefix (shared blocks, template and app tools excepted). `ToolKitInstantiate {agent_id, variant?, block_prefix? (^[a-z][a-z0-9_]{0,23}$), settings, credential_id?, connection_id?, actions?, dataset_id?, key_columns?, flow_anchor?, add_test_case=true, dry_run=false}` → `ToolKitInstantiated {kit_id, variant, prefix, dry_run, changes: [KitChange {kind: tool|block|instructions|variable|extraction|rule|flow_node|flow_edge|flow_variable|test_case|notify_team, id, label, status: added|exists|skipped, note}], tools: [KitToolPlan {key, name, kind, status, tool_id, definition}], tool_ids, instructions_snippet, config_version, notes, validation: ValidationResult}`. Instantiation: HTTP tools get `allowed_hosts` from their url; without `credential_id` their headers that carry `{{ secret.* }}` are dropped (keys just unset; `notes` says so), with it the key is bound through `POST /v1/tools`'s rule (`admin` + `providers:write`); template tools (the `booking` kit wraps the Cal.com set) take the kit's settings as defaults; a `dataset` variant needs the workspace's `dataset_id` (not failed; `key_columns` default to its keys, at least `requires.min_key_columns`); a `composio_action` variant needs `admin` + `providers:write` before anything else and a connected app of one of its toolkits, whose actions are picked through the Apps pick (vendor schema, destructive refusal; app actions keep their `<toolkit>_<action>` names and the rules and snippet are rendered with them); an `only_with: sms` tool is added only when `tools.sms` is set; kit blocks need a composite panel (422 otherwise). Every item is matched first (an attached or agent-owned tool by name, a block by id — a `shared` block by type —, the snippet by `<!-- kit:<id>:<prefix> -->` … `<!-- /kit:<id>:<prefix> -->` markers, an extraction field by name, a rule, flow node, edge or variable by id, the test case `kit-<id>-<prefix>` by id), so a second call with the same prefix adds nothing and writes no version. Flow steps are added only with `flow_anchor` on an agent that has a flow (an agent or start step; the anchor step gains `anchor_extract` in its `extract`); otherwise they are `skipped`. The test case's `mocks` are the kit tools' `fake`s (HTTP, app action and lookup tools). `dry_run` validates the would-be config with the planned tools standing in for rows and writes nothing; a real run creates the tools, writes one config version (note `added the <kit> kit (<prefix>)`) and is refused (422, nothing kept) when it would add a validation error the agent did not have. Audit `tool_kit.instantiate` (`target_type="agent"`; payload: kit id, variant, prefix, created tool ids, counts — never a setting value).

`metrics` kinds: `session_usage` (`data` = the SDK's `AgentSessionUsage`, on every update) and, only when the workspace opted into cost reconciliation (`ResolvedAgentConfig.cost_reconcile` non-empty, docs/v4/COSTS.md D-V4-45), one `provider_requests` just before the summary: `data = {llm, stt, tts: [{request_id, provider, model}], dropped}` — per-request vendor ids only (≤ 2,000; `dropped` counts the rest), never a prompt, completion or secret; the api's `cost_reconcile` job looks them up.

**Emission rules (DECISIONS-W2 §D-W3-1).**
- Platform-owned types are emitted only by the worker: `session_started`, `agent_state`, `user_turn`, `agent_turn`, `tool_call_started/updated/ended`, `tool_reply`, `workflow_run`, `metrics`, `error`, `info`, `session_ended`. A pack must not emit them.
- Packs and tools may emit `escalation` (`{reason: str, urgency: "low"|"normal"|"high"}`) and `info` (`{message: str}`) via `PackSessionContext.record_event(event_type, payload)` (§8). Any other type is stored as-is by the api (`SessionEventIn.type` is a plain `str`) and rendered generically by the console; packs should prefix custom types with their pack id (`insurance_claim.route_changed`) so they never collide with platform types.
- `escalate_to_human` emits `escalation{reason, urgency, mode?}` (`mode` since V5-37, left out for `transfer`) right after `set_status("Escalated", "warning")`.
- The insurance pack emits `escalation` on the route transition into `emergency_escalation` (not on every subsequent run), payload `{"reason": "claim routed to emergency_escalation", "urgency": "high", "route": <route>}`.

---

## 8. Packs (`lkap_contracts.packs` + `lkap_agent.packs`)

```python
class KbSeed(BaseModel):
    kb_name: str
    files: list[str]  # paths relative to the pack's `seeds/` dir


class PackManifest(BaseModel):
    v: Literal[1] = 1
    id: str  # "insurance_claim"
    version: str  # semver
    name: str
    description: str
    ui_panel_id: str  # web registry key
    default_instructions: str
    default_greeting: str
    default_voice: dict[
        str, str
    ] = {}  # per provider id → voice name, e.g. {"google-realtime": "Kore", "livekit-inference-tts": "Ashley"}
    recommended_pipeline: (
        PipelineConfig  # credential_ids left None; api fills at seed time when a matching credential exists
    )
    capabilities: CapabilitiesConfig
    builtin_tools_disabled: list[str] = []
    tool_names: list[str]  # code tools the pack registers (for the console read-only list)
    state_schema: dict  # JSON Schema for UiState.custom
    settings_schema: dict = {"type": "object"}  # JSON Schema for AgentConfig.pack_settings
    kb_seeds: list[KbSeed] = []
    instructions_by_mode: dict[
        PipelineMode, str
    ] = {}  # appended per pipeline mode (e.g. cascaded tool-acknowledgement rule)
```

Python pack interface (`packs.base`; packs import only `lkap_contracts`, `livekit.agents` types and `packs.base`; the worker implements these Protocols):

```python
from typing import Protocol, Any, Awaitable, Callable
from livekit.agents import llm, AgentSession, ChatContext, ChatMessage
from livekit import rtc

class UiChannel(Protocol):
    seq: int
    state: "UiState"                                            # current envelope (mutable copy)
    async def patch(self, ops: list["UiPatchOp"]) -> None: ...  # sends UiPatch, bumps seq
    async def snapshot(self) -> None: ...
    async def set_status(self, label: str, tone: "Tone") -> None: ...
    async def add_note(self, text: str, kind: str = "note", key: str | None = None) -> None: ...
    async def set_checklist(self, items: list["ChecklistItem"]) -> None: ...
    async def push_asset(self, data: bytes, mime: str, kind: str, caption: str | None = None, meta: dict[str, str] | None = None) -> str: ...  # returns asset_id, streams bytes + adds AssetRef to state.assets
    async def activity(self, event: "ActivityEvent") -> None: ...
    async def request_ui(self, method: str, payload: dict[str, Any]) -> dict[str, Any]: ...   # RPC lkap.ui.request

class FrameBufferProto(Protocol):
    def latest(self, max_age_s: float | None = None) -> "FrameSnapshot | None": ...   # FrameSnapshot(frame: rtc.VideoFrame, source: "camera"|"screen", age_s: float)
    async def latest_jpeg(self, max_age_s: float | None = None, max_width: int = 1024) -> tuple[bytes, "FrameSnapshot"] | None: ...

class KbClient(Protocol):
    async def search(self, query: str, k: int = 4, kb_ids: list[str] | None = None) -> list["KbHit"]: ...

class StructuredLLM(Protocol):
    async def extract(self, *, instructions: str, input_text: str, schema: type[BaseModel], timeout_s: float = 45) -> BaseModel: ...

class BackgroundRunner(Protocol):
    def submit(self, *, name: str, coro: Awaitable[Any], on_result: Callable[[Any], Awaitable[None]] | None = None,
               urgent: Callable[[Any], bool] | None = None, urgent_instructions: Callable[[Any], str] | None = None,
               routine_note: Callable[[Any], str | None] | None = None, call_id: str | None = None) -> str: ...   # returns job id
    def cancel(self, job_id: str) -> None: ...

class ImageGen(Protocol):
    async def generate(self, prompt: str, *, timeout_s: float = 40) -> tuple[bytes, str]: ...   # (png/jpeg bytes, mime)

class PackSessionContext(Protocol):
    session_id: str; agent_id: str; pipeline_mode: PipelineMode
    config: AgentConfig; pack_settings: dict[str, Any]
    session: AgentSession; room: rtc.Room
    ui: UiChannel; frames: FrameBufferProto; kb: KbClient
    workflow_llm: StructuredLLM; image_gen: ImageGen | None; background: BackgroundRunner
    log: Any                                                    # structlog bound logger
    userdata: dict[str, Any]                                    # pack-private per-session store
    def record_event(self, event_type: str, payload: dict[str, Any]) -> None: ...  # best-effort session-timeline event (DECISIONS-W2 §D-W3-1)

class ToolMeta(BaseModel):
    name: str
    silent_reply: bool = False          # realtime: cancel_tool_reply() in function_tools_executed
    activity_label: str | None = None   # UI "team" label, e.g. "Policy desk"

class Pack(Protocol):
    manifest: PackManifest
    def tools(self, ctx: PackSessionContext) -> list[llm.FunctionTool]: ...
    def tool_meta(self) -> list[ToolMeta]: ...
    def initial_state(self, ctx: PackSessionContext) -> dict[str, Any]: ...     # UiState.custom initial value
    async def on_session_start(self, ctx: PackSessionContext) -> None: ...
    async def on_user_turn_completed(self, ctx: PackSessionContext, turn_ctx: ChatContext, new_message: ChatMessage) -> None: ...
    async def on_agent_turn_completed(self, ctx: PackSessionContext, text: str, interrupted: bool) -> None: ...
    async def on_ui_action(self, ctx: PackSessionContext, action: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    async def on_session_end(self, ctx: PackSessionContext, reason: str) -> None: ...

# packs/<id>/manifest.py must expose: MANIFEST: PackManifest   (api reads this; no livekit imports)
# packs/<id>/pack.py must expose:     PACK: Pack           (worker reads this; PACK.manifest is MANIFEST)
# Discovery: for path in LKAP_PACKS.split(","): importlib.import_module(path + ".manifest").MANIFEST / importlib.import_module(path + ".pack").PACK

Seeding rule (api `seed-from-pack`, W1-API-CORE): start from `manifest.recommended_pipeline`; for each slot whose provider `requires_credential` and no credential of that provider exists, substitute the LiveKit Inference default of the same kind (`stt`→`livekit-inference-stt`, `llm`→`livekit-inference-llm`, `tts`→`livekit-inference-tts`); a `realtime` slot with no credential switches `mode` to `cascaded` with the three Inference defaults; `avatar`/`image_gen` slots without a credential are dropped (null). The insurance pack's `recommended_pipeline` is therefore the **cascaded Inference stack** (works with LiveKit creds alone); `default_voice["google-realtime"]="Kore"` is applied when an admin later switches to Gemini Live. Manifest `builtin_tools_disabled` seeds `AgentConfig.tools.builtin_disabled`, which is authoritative at runtime.
```

Tools returned by `Pack.tools` are ordinary `@function_tool` functions/closures over `ctx`. `PlatformAgent` passes `tools=[*builtin, *declarative, *pack_tools]`.

---

## 9. Tool definitions (`lkap_contracts.tools`)

```python
class HttpToolDefinition(BaseModel):
    kind: Literal["http"] = "http"
    name: str  # ^[a-zA-Z_][a-zA-Z0-9_]{0,63}$
    description: str
    parameters: dict  # JSON Schema object (type=object). Becomes RawFunctionDescription.parameters
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    url: str  # template: "https://api.example.com/items/{{ item_id }}" (Jinja-lite: {{ arg }} only, url-encoded)
    headers: dict[
        str, str
    ] = {}  # values may reference {{ secret.NAME }} resolved from credential `secrets` of `credential_id`
    credential_id: str | None = None  # a credentials row of provider_id "http-tool-secret" (see note below)
    body_template: str | None = (
        None  # JSON string with {{ arg }} substitutions; default: JSON of all arguments (POST/PUT/PATCH)
    )
    allowed_hosts: list[str] = []  # must contain url host; empty → platform allowlist only
    timeout_s: float = 10
    max_result_chars: int = 4000
    result_path: str | None = None  # optional JSON pointer to extract, e.g. "/data/summary"
    silent_reply: bool = False


class McpNoAuth(BaseModel):
    kind: Literal["none"] = "none"


class McpHeaderAuth(BaseModel):
    kind: Literal["header"] = "header"
    headers: dict[str, str] = {}  # {{ secret.NAME }} allowed
    credential_id: str | None = None  # an http-tool-secret bag


class McpOAuthAuth(BaseModel):  # V5-14
    kind: Literal["oauth"] = "oauth"
    credential_id: str | None = None  # the mcp-oauth credential once signed in
    registration: Literal["auto", "preregistered"] = "auto"
    client_id: str | None = None  # pre-registered clients only
    client_secret_ref: str | None = None  # accepted and ignored (ask #112)
    scopes: list[str] | None = None
    subject: Literal["workspace", "agent"] = "workspace"


McpAuth = Annotated[McpNoAuth | McpHeaderAuth | McpOAuthAuth, Field(discriminator="kind")]  # V5-09


class McpServerDefinition(BaseModel):
    kind: Literal["mcp"] = "mcp"
    name: str
    url: str  # streamable HTTP endpoint; host policy in §3 (V5-09)
    auth: McpAuth = McpNoAuth()
    headers: dict[str, str] = {}  # deprecated mirror of auth.headers
    credential_id: str | None = None  # deprecated mirror of auth.credential_id
    allowed_tools: list[str] | None = None
    timeout_s: float = 5
    sse_read_timeout_s: float = 300
    tool_options: dict[str, ToolExecution] = {}  # per MCP tool (keys within allowed_tools when set); V4-12
    origin: McpServerOrigin | None = None  # {provider, kind: server|router, remote_id, config_hash}; V5-47
    cached_tools: list[McpToolSnapshot] | None = None  # <= 200; stored by POST /v1/tools/{id}/test (V5-09)
    cached_at: datetime | None = None


ToolDefinition = Annotated[
    HttpToolDefinition | McpServerDefinition | ProviderToolDefinition, Field(discriminator="kind")
]
```

`ProviderToolDefinition` (V5-47, a connected app's action) is described in §3 "Apps on agents".

Note on tool secrets: `ProviderKind` includes `"secret_bag"`; the registry entry `http-tool-secret` (`kind="secret_bag"`, `requires_credential=true`, free-form `NAME=value` secret pairs in the console) is what `credential_id` points to for HTTP/MCP tools. The api substitutes `{{ secret.NAME }}` in an HTTP tool's `headers`/`url`/`body_template`, in an MCP server's `auth.headers` (mirrored in `headers`) and in a provider tool's `headers` when it builds `ResolvedAgentConfig.tools`, so the worker never sees credential ids or the vault. An `oauth` MCP server's `mcp-oauth` credential is never put into `tools`: the worker gets a short-lived access token through `ResolvedAgentConfig.mcp_oauth` and the internal token route (§3, V5-16).

Agent-side construction (`lkap_agent.tools.declarative`): `function_tool(_http_handler_for(defn), raw_schema={"name": defn.name, "description": defn.description, "parameters": defn.parameters})` where `async def handler(raw_arguments: dict[str, object], context: RunContext) -> str`. MCP: one `MCPToolset(id=f"mcp_{defn.name}", mcp_server=GuardedMCPServerHTTP(url=..., transport_type="streamable_http", allowed_tools=..., headers=<resolved header auth>, timeout=..., sse_read_timeout=..., transport_factory=<the guarded transport, wrapped in ApiIssuedBearer for an oauth server>), tool_options={name: MCPToolOptions(...)})` per server (`lkap_agent.tools.mcp_client`, `.mcp_auth`), passed in `Agent(tools=[...])`; results are fenced as `mcp:<server>`. Provider tools: `lkap_agent.tools.provider` (§3).

### Tool context: placeholders, bindings, `requires_vars`, `confirm_readback` (V6-07)

`lkap_contracts.tool_context` (D-V6-22/23). Added fields, all defaulting to empty (a definition without them builds and sends exactly what it did before):

```python
class ToolBinding(BaseModel):
    path: str = ""  # RFC 6901 pointer into the tool's result as the model sees it (after result_path); "" = all
    to: str  # details:<block_id>.<key> | table:<block_id> | checklist:<item_id> | status | note | var:<name>


class ToolContextSpec(BaseModel):  # one MCP tool's settings
    requires_vars: list[str] = []  # <= 20 variable names
    confirm_readback: list[str] = []  # <= 10 argument names; adds a `confirmed` parameter
    bindings: list[ToolBinding] = []  # <= 20
    pinned_arguments: dict[str, str | int | float | bool | None] = {}  # <= 20; hidden from the model


# HttpToolDefinition: + requires_vars, confirm_readback, bindings
# ProviderToolDefinition: + requires_vars, confirm_readback, bindings, pinned_arguments
# McpServerDefinition: + tool_context: dict[str, ToolContextSpec] = {}  # keys within allowed_tools when set
```

- **Placeholders.** `{{ ctx.<name> }}` for `session_id`, `agent_id`, `caller_phone` (E.164, phone calls only), `caller_identity`, `language`, `timezone` (the caller's zone, R-V5-10), `channel` (`web` | `phone` | `text`: `sip_*` → phone, `text` → text, every other channel → web; `TOOL_CHANNEL_OF`), and `{{ var.<name> }}` (the session's variables: a flow's `FlowState.variables`, else the worker's `userdata["lkap.variables"]` store seeded from the session's variables, plus those a binding wrote). Allowed in an HTTP url's path and query (percent-encoded), its `body_template` (JSON-escaped) and in pinned arguments (as-is, sent as a JSON value). `placeholder_issues(definition)` refuses one in a url's scheme or authority, in any header, anywhere in an MCP server's url; an unknown `ctx` name; a malformed spelling. The definition models call it in a validator, so the api refuses such a definition at save (422); the worker checks again when it builds the tool (a refusing tool, or a skipped MCP server) and the agent validator reports a stored row. The worker renders arguments and context in **one pass** (a value is never re-scanned). A value the session lacks → the tool answers "I need <label> first …" and sends nothing.
- **`requires_vars`** refuses before any request, listing the missing variables. **`confirm_readback`** refuses until the call carries `confirmed=true` (the refusal says what to read back, spelled for a voice); `confirmed` is stripped before the request.
- **Bindings** apply after a successful call (HTTP 2xx, an app action that succeeded, an MCP result that is not an error) and before the result reaches the model. Only `details` and `table` blocks that are on the session's panel are written (so never a requestable, link, consent, upload, captions or handoff block); a missing checklist item or path is skipped. Caps: 20 bindings, 500 characters per value (control characters removed), 100 table rows. One `ActivityEvent` per call records `detail = {event: "tool_bindings_applied", applied: [targets], skipped: [{to, reason}]}` — never a value. A `var:` binding marks the name in `userdata["lkap.bound_variables"]` (third-party text). For MCP the bound value is the result's `structuredContent`, else a single text item parsed as JSON (or the text), else the list of items.
- **Api validator** (`config_service.tool_context_issues`): a binding into a block the panel lacks, or into a block of another type → error; a status, note or checklist binding without that block → warning; on a flow agent, a required or referenced variable that no flow variable or binding sets → warning.

### Dataset tools (V6-16, D-V6-27; `lkap_contracts.datasets`)

```python
class DatasetToolDefinition(BaseModel):  # kind "dataset"; always runs blocking
    kind: Literal["dataset"] = "dataset"
    name: str  # TOOL_NAME_PATTERN
    description: str
    dataset_id: str  # a dataset of the tool's workspace (checked at save and by agent validation)
    key_columns: list[str]  # 1..8 of the dataset's declared key columns: the model's arguments
    return_columns: list[str] = []  # the columns a found row carries (empty: all)
    match: Literal["exact", "prefix"] = "exact"
    max_rows: int = 5  # 1..20
    max_result_chars: int = 2000  # 100..8000
    requires_vars: list[str] = []  # V6-07
    bindings: list[ToolBinding] = []  # V6-07; the bound result is the list of rows (/0/<column>)
    pinned_arguments: dict[str, str | int | float | bool | None] = {}  # key column -> value, hidden; {{ ctx.* }}/{{ var.* }}
```

The worker (`lkap_agent.tools.dataset`) offers one string argument per unpinned key column, posts
`POST /internal/v1/datasets/{id}/lookup` with the service token and the session id, and returns the rows
inside `<untrusted source="dataset:<tool>">` (plus a plain note when more matched than `max_rows`); no rows
is a plain "No matching record" answer. Key values are normalised the same way at import and lookup
(`string`: NFKC, case-folded, spaces collapsed; `phone`: ASCII digits, leading zeros dropped, the last 10;
`email`: case-folded; `number`: a canonical decimal). Key values are never logged.

---

## 10. Agent ↔ UI protocol (`lkap_contracts.ui_protocol`; TS generated to `web/src/contracts/lkap-contracts.d.ts`)

Topics / methods (string constants in `lkap_contracts.ui_protocol.TOPICS`):

| Constant | Value | Primitive | Direction |
|---|---|---|---|
| `TOPIC_UI_STATE` | `lkap.ui.state` | text stream | agent → UI |
| `TOPIC_UI_ACTIVITY` | `lkap.ui.activity` | text stream | agent → UI |
| `TOPIC_UI_ASSET` | `lkap.ui.asset` | byte stream | agent → UI |
| `RPC_UI_REQUEST` | `lkap.ui.request` | RPC (agent calls UI) | agent → UI |
| `RPC_AGENT_ACTION` | `lkap.agent.action` | RPC (UI calls agent) | UI → agent |

Pydantic (all with `v: Literal[1] = 1`):

```python
Tone = Literal["neutral", "info", "success", "warning", "danger"]


class StatusStamp(BaseModel):
    label: str
    tone: Tone = "neutral"
    key: str | None = None


class Note(BaseModel):
    id: str
    text: str
    kind: str = "note"
    tone: Tone = "neutral"
    ts: float
    key: str | None = None  # key → upsert semantics


class ChecklistItem(BaseModel):
    id: str
    label: str
    done: bool = False
    blocking: bool = False
    hint: str | None = None


class AssetRef(BaseModel):
    asset_id: str
    kind: str
    mime: str
    caption: str | None = None
    meta: dict[str, str] = {}
    ts: float


class ActivityEvent(BaseModel):
    v: Literal[1] = 1
    id: str  # call_id or job id; same id → replaces earlier entry
    ts: float
    source: str  # tool name / workflow name
    label: str  # UI-facing "team member" label
    phase: Literal["running", "done", "error", "cancelled"]
    headline: str
    urgent: bool = False
    duration_ms: int | None = None
    detail: dict[str, Any] | None = None


class UiState(BaseModel):
    v: Literal[1] = 1
    status: StatusStamp | None = None
    progress: int | None = None  # 0-100
    notes: list[Note] = []
    checklist: list[ChecklistItem] = []
    assets: list[AssetRef] = []
    activity: list[ActivityEvent] = []  # last 30
    custom: dict[str, Any] = {}  # pack-defined, validated by PackManifest.state_schema


class UiSnapshot(BaseModel):
    v: Literal[1] = 1
    type: Literal["snapshot"] = "snapshot"
    seq: int
    session_id: str
    state: UiState


class UiPatchOp(BaseModel):
    op: Literal["set", "append", "remove", "upsert"]
    path: str
    value: Any = None
    key: str | None = None
    # path: "/status", "/notes", "/custom/fields/policy" (JSON-pointer style over UiState). "upsert" matches list items by `key` or `id`.


class UiPatch(BaseModel):
    v: Literal[1] = 1
    type: Literal["patch"] = "patch"
    seq: int
    session_id: str
    ops: list[UiPatchOp]


UiStateMessage = Annotated[UiSnapshot | UiPatch, Field(discriminator="type")]

# lkap.ui.asset byte stream: attributes = {"asset_id", "kind", "mime", "caption"?, "session_id"}; name = f"{asset_id}.{ext}"; the matching AssetRef is patched into /assets AFTER the stream completes.


class UiRequest(BaseModel):  # RPC lkap.ui.request payload (agent → UI)
    v: Literal[1] = 1
    method: Literal["open_dialog", "focus", "request_video_source", "toast"]
    payload: dict[str, Any] = {}


class UiRequestResult(BaseModel):
    ok: bool
    payload: dict[str, Any] = {}


class AgentAction(BaseModel):  # RPC lkap.agent.action payload (UI → agent)
    v: Literal[1] = 1
    action: Literal["get_snapshot", "set_video_source", "ui_action"]
    payload: dict[
        str, Any
    ] = {}  # set_video_source: {"source": "camera"|"screen"|"none"}; ui_action: {"name": str, "data": {...}} → Pack.on_ui_action


class AgentActionResult(BaseModel):
    ok: bool
    payload: dict[str, Any] = {}
    error: str | None = None
```

Ordering: `seq` starts at 1 with a snapshot on session start — the platform guarantees it, sent *after* the pack's `on_session_start` (DECISIONS-W2 §D-W2-9a); every patch increments it. The UI reducer applies `seq == last+1`; on a gap it calls `get_snapshot` and discards intermediate patches. The agent re-sends a snapshot on RPC request, and every 50 patches. `activity` is also kept in state (last 30) so late joiners/reconnects see it.

Transcript: `useSessionMessages()` (topic `lk.transcription` + `lk.chat`), chat input `send()`; the agent's default `TextInputOptions` handler stays enabled. Agent state: `useVoiceAssistant().state`. Avatar video: `useVoiceAssistant().videoTrack` (resolves to the `lk.publish_on_behalf` participant automatically).

TypeScript (generated; illustrative signature of the hook layer in `web/src/hooks`):

```ts
export type UiStateMessage = UiSnapshot | UiPatch;                       // generated
export function useUiState(sessionId: string): { state: UiState; seq: number; assets: Map<string, string /* objectURL */>; connected: boolean };
export function useByteStream(topic: string, onAsset: (info: { attributes: Record<string,string>; name: string; mimeType: string }, bytes: Uint8Array) => void): void;  // room.registerByteStreamHandler
export function useAgentRpc(): { perform: (action: AgentAction) => Promise<AgentActionResult> };       // wraps useRpc().perform({ destinationIdentity: agent.identity, method: "lkap.agent.action", payload })
export function useUiRequests(handler: (req: UiRequest) => Promise<UiRequestResult>): void;            // useRpc("lkap.ui.request", handler)
```

### Live captions and per-turn language (V5-31)

`TranscriptTurn.language: str | None` (the caller's detected language on a user turn, the reply language on an assistant turn; `null` when unknown) is stored with the summary and returned by `GET /v1/sessions/{id}`. `BlockType` gains `captions` (`CaptionsBlockConfig {show_user=true, show_agent=true, target_language: str|null (reserved for translation), position: block|bottom}`, `CaptionsBlockState {language, target_language}`). While the panel has a `captions` block the worker streams `CaptionSegment {v:1, id, speaker: user|agent, text, final, language, ts}` JSON on the text-stream topic `lkap.captions` (`TOPIC_UI_CAPTIONS`): an utterance keeps its `id` from the first interim to its final; the agent's words come from a text output after RoomIO's transcription (`TextOutputOptions(next_in_chain=...)`), so they are timed to the audio; interim agent captions at most every 0.25 s. Captions are never stored in `UiState`.

### Links, time slots, cards and `describe_panel` (V5-43)

`BlockType` gains three blocks (configs in `lkap_contracts.blocks`, states in `lkap_contracts.ui_protocol`):

| Block | Config | State | Tools |
|---|---|---|---|
| `link` | `allowed_hosts: [str]` (required; `example.com` or `*.example.com` for its sub-domains, never a scheme, path, port or IP), `open_in: new_tab\|dialog`, `show_qr` | `LinkBlockState {url (https only), label (≤ 80), kind: checkout\|esign\|portal\|other, status: idle\|pending\|opened\|completed\|failed\|expired, reference, channel: panel\|sms, sent_at, opened_at, expires_at, completed_at}` | `send_link` |
| `slots` | `timezone_mode: caller\|agent`, `days_visible` (1–31), `allow_custom` | `SlotsBlockState` (requestable) `{prompt, timezone, slots: [TimeSlot {id, start, end (ISO 8601 with UTC offset, end > start), label, capacity}] (≤ 50), selected, grouped_by_day}` | `request_slot`, `resolve_slot` |
| `cards` | `layout: carousel\|grid\|list`, `selectable`, `max_cards` (1–20), `image_hosts: [str]` | `CardsBlockState {cards: [Card {id, title, subtitle, image_asset_id, image_url (https), facts [{label, value}] ≤ 8, badges ≤ 5, actions [{name, label, tone}] ≤ 3}] ≤ 20, selected}` | `show_cards`, `update_block` |

**Links are checked at the contract layer.** `https_url_problem(url, allowed_hosts=…)` refuses every scheme but `https` (`javascript:`, `data:`, `http:`), user names or passwords in the link, spaces, quotes, angle brackets, backslashes and control characters, IP literals and links over 2048 characters; `host_allowed` matches exact names and `*.` sub-domains (the bare domain of a `*.` entry is not included). `LinkBlockState.url` and `Card.image_url` validate with it; `send_link` and `show_cards` also check the block's allowlist.

**Answers from the browser.** A `slots` tap is `block_submit {values: {selected: <slot id>}}`: the worker reads only `selected` (an unknown id is not stored) and takes `start`/`end` from its own slots, for the waiting tool and for a late answer alike. A card tap is `block_action {name: "select", data: {card_id}}` (with `selectable`), a card button `block_action {name: <action>, data: {card_id}}`; the worker refuses a card or a button that is not on the block, records `selected` for a tap, and gives the model a short user message with the card title fenced. A link tap is `block_action {name: "opened"}` (`pending → opened`); a browser can never complete a link.

**Link outcomes.** `POST /v1/hooks/link/{session_id}` with `LinkHookIn {block_id | reference, status: completed|failed|expired}` (`extra="forbid"`), signed `X-LKAP-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "<t>.<raw body>")>` with the signing secret of any enabled webhook endpoint of the session's workspace (±300 s). 202 `LinkHookOut {id, session_id, status, delivered_to}`; 401 for an unknown session or a bad signature (indistinguishable); 422 for a bad body; 409 `not_live` / `no_agent`. The api sends `LinkCompletedPacket {v, op: "link_completed", id, session_id, block_id, reference, status}` as a reliable data packet on `lkap.ui.link` (`TOPIC_UI_LINK`) to the room's agents only; the worker honours a packet only without a participant (server-sent) and for its own session, and applies it only while the link is `pending` or `opened` (idempotent). The model then gets `[The payment link <untrusted source="panel">…</untrusted> was completed.]`.

**`describe_panel`** (registered with any block): one entry per block — `id`, `type`, `title`, `status` and a type summary (counts and at most eight short labels; a link's site, never its URL; never bytes) — as JSON inside `<untrusted source="panel">`, at most 4000 characters (details dropped first, then trailing blocks, with a note).

### Generic panel primitives and caller edits (V6-06, D-V6-19)

Built-ins over the envelope and the blocks, registered from the panel: `set_checklist(items, keep_done=true)` and `check_item(item_id, done=true, hint?)` (the envelope checklist; with a checklist block), `generate_image(prompt, caption?, block_id?)` (only when `pipeline.image_gen` resolves **and** the panel has a `gallery` block, ask #22; the picture goes out on `lkap.ui.asset` and is stored as a session asset of kind `frame` with `meta.source="generated"`; a result over `MAX_UPLOAD_BYTES` is dropped, S6-25), and `push_note(text, kind?, block_id?)` with `NoteItem.block_id` (a note in one block's margin). On `realtime` / `half_cascade` the three writes answer nothing. **Caller edits:** `EDITABLE_BLOCK_TYPES = {details, checklist, notebook}` and `CALLER_EDIT_FLAGS` (`caller_can_edit` on `details`/`checklist`, `caller_can_write` on `notebook`); the page sends `block_action {block_id, name: "edit", data}` with a strict `DetailsEdit {key, value ≤ 500}`, `ChecklistEdit {item_id, done}` or `NotebookEdit` (ids and keys match `NOTEBOOK_ID_PATTERN` since S6-7); the answer is `{ok: true}`, `{ok: true, payload: {changed: false}}` or `{ok: false, error}`. The worker takes it from the caller participant only, applies it in one patch through the state model, records `caller_edit` without the value, tells the model in one line fenced as `caller_edit` and then calls `Pack.on_block_action`. One per-session bucket shared with card actions allows `MAX_CALLER_ACTIONS_PER_MIN` (10); past it the answer is `{ok: false, error: "Please wait a moment before changing that again."}` and nothing is applied or told (S6-5).

### Notebook and layout blocks, ready-made panels (V6-08)

`BlockType` gains two blocks (D-V6-15, D-V6-18; configs in `lkap_contracts.blocks`, the state in `lkap_contracts.ui_protocol`):

| Block | Config | State | Tools |
|---|---|---|---|
| `notebook` | `paper: plain\|ruled\|grid\|legal` (`ruled`), `font: print\|handwritten` (`print`; a handwriting look for typed notes, not ink), `sections: [{id (letters, digits, _.:-), title (≤ 80), kind: text\|checklist\|details\|ink}]` (1–12, unique ids; default one `notes` text section), `caller_can_write` (off), `caller_can_draw` (off; V6-12: the caller may draw on the boards of its ink sections); V6-12: an `ink` section may name its board, `canvas_block_id` | `NotebookBlockState {sections: {<section id>: {kind: "text", entries: [NotebookEntry {id, text ≤ 2000, author: agent\|caller, key, tone, ts, edited_by}] ≤ 100} \| {kind: "checklist", items: [ChecklistItem] ≤ 30} \| {kind: "details", items: [DetailsItem] ≤ 30} \| {kind: "ink", canvas_block_id (the board its config names, V6-12)}} ≤ 12, updated_at}` | `notebook_write`, `notebook_check` |
| `layout` | `kind: tabs\|columns`, `children: [{block_id, label ≤ 40}]` (≤ 12), `columns: 2\|3` | none (`{}`) | none |

**The notebook.** The config lists the sections and their order; the state keys each section's content by id (seeded empty by the worker), so a patch addresses one section (`/blocks/<id>/sections/<section id>/entries`). `notebook_write(section_id, text?, items?, fields?, mode: append|replace, key?, tone?, block_id?)` appends a note (or updates the note with the same `key`, or replaces the section), upserts checklist items by id (a tick is kept) or details rows by key; `notebook_check(section_id, item_id, done, hint?, block_id?)` ticks. Both are write built-ins, never in the background, and answer nothing on `realtime` / `half_cascade` pipelines. The platform's entry ids start with `n:` and a note's key never contains `:`, so an id never matches another note's key in a keyed `upsert`. An `ink` section is never written by these tools. `update_block` refuses a notebook (and a layout), and neither type is in `UPDATABLE_BLOCK_TYPES`, so a page's `state_delta` never writes one.

**Caller edits.** `EDITABLE_BLOCK_TYPES` gains `notebook`; `CALLER_EDIT_FLAGS` maps each editable type to its flag (`caller_can_edit` on `details` and `checklist`, `caller_can_write` on `notebook`). `block_action {name: "edit", data: NotebookEdit}` where `NotebookEdit` (strict) is `{section_id}` plus exactly one of `{text}` (add the caller's note), `{entry_id, text}` (change a note; empty text removes it), `{item_id, done}` (tick) or `{key, value}` (change a details row; empty clears it), text and values ≤ 500 characters. The worker checks it against the section's kind (an `ink` section refuses), applies it in one patch validated against `NotebookBlockState`, marks the note `author: "caller"` or the changed note, item or row `edited_by: "caller"`, and tells the model in one line fenced as `<untrusted source="caller_edit">`; the pack's `on_block_action` then sees `{section_id, section, key | item_id, label, text | done | value, change}`. The answer is `{ok: true}`, `{ok: true, payload: {changed: false}}` for a no-op, or `{ok: false, error}`.

**`describe_panel`.** A notebook entry lists its sections in config order with totals and at most five notes, items or rows, each cut to 80 characters (the caller's marked "by the caller"); a layout entry is `{shows_as, holds: [block ids]}`. The claimed blocks keep their own entries, so a layout never hides a block from the model; the full Notebook preset stays within the 4,000-character bound without dropping details.

**Layout validation.** `layout_issues(blocks)` (the api's panel validator calls it): a child that is not a block of the panel, the layout itself, another layout, or a block already claimed by a layout (or listed twice) is an error at `panel.blocks[i].config.children[j].block_id`; an empty layout is a warning. Children stay top-level `PanelLayout.blocks` entries; the console renders a claimed child inside its layout instead of in the panel's flow (V6-10).

**Ready-made panels.** `PanelPreset {id, name, description, panel: PanelLayout}`; `GET /v1/panels/presets` (viewer or `agents:read`) → `PanelPresetsResponse {items}`. `NOTEBOOK_PRESET` (`id="notebook"`): `layout="wide"`, blocks `status`, `notebook` (Notes / Still needed / Summary / Sketch, `font="handwritten"`, `caller_can_write=true`) and `gallery`. `panel_preset(id)` returns a copy; the MCP's `agent_update(panel_preset="notebook")` replaces `config.panel` with it.

### The drawing board: the `canvas` block and `lkap.ui.ink` (V6-12)

`BlockType` gains `canvas` (D-V6-16; config in `lkap_contracts.blocks`, state and wire in `lkap_contracts.ui_protocol`):

| Block | Config | State | Tools |
|---|---|---|---|
| `canvas` | `caller_can_draw` (off), `tools: [pen\|highlighter\|eraser\|box\|arrow\|text]` (pen, highlighter, eraser; `text` is reserved: a stroke never carries text), `background: none\|asset\|live_camera` (what the board starts on; `asset` waits for a picture), `max_strokes` (500; ≤ 2,000), `signature_mode` (off; superseded by the `signature` block, kept so older boards validate) | `CanvasBlockState {width, height (1600×1200: the aspect only), background: none\|live_camera\|asset:<id>, strokes: [InkStroke {id, author: caller, tool: pen\|highlighter\|box\|arrow, points: [[x, y(, pressure)]] ≤ 1,000, color #rrggbb, width, ts}] ≤ 2,000, shapes: [CanvasShape {id, author: agent, kind: box\|circle\|arrow\|text\|path, x, y, w, h \| points ≤ 200, text ≤ 200, label ≤ 80, color, width, ts}] ≤ 200, snapshot_asset_id, limit_reached, updated_at}` | `draw_on_canvas`, `clear_canvas`, `read_canvas` |

Every coordinate is normalised to the board (0..1). `canvas` is not in `UPDATABLE_BLOCK_TYPES` (so neither `update_block`, which refuses it by name, nor a page's `state_delta` writes it) nor in `EDITABLE_BLOCK_TYPES`.

**Who may draw.** `canvas_caller_can_draw(canvas_id, blocks)` is the one rule the worker's ink handler, the worker's snapshot acceptance and the api's snapshot check all call: the board's own `caller_can_draw`, or, for a board a notebook `ink` section shows, that notebook's `caller_can_draw`.

**`lkap.ui.ink` (`TOPIC_UI_INK`)** is the first browser → agent **text** stream (`localParticipant.sendText(json, {topic: "lkap.ui.ink"})` in livekit-client 2.22; the worker registers `Room.register_text_stream_handler` in livekit 1.1.18; never RPC: payloads cap near 15 KiB). One strict `InkMessage {v, block_id, stroke_id, op: add|erase|clear, tool, points ≤ 128, color, width}` per message: `add` with a new `stroke_id` starts a stroke (its tool, colour and width come from this first message; the tool must be one of the board's `tools`), `add` with the id of one of the caller's strokes appends points (a long stroke is sent in pieces; the page batches points, about every 50 ms), `erase` removes one of the caller's strokes, `clear` removes all of them. The worker drops and counts (never logging the content) a message from anyone but the caller (before reading it), one over 20 a second (a token bucket, before reading), one over 2 KiB (declared or read), one that does not validate (text, a point outside 0..1, an unknown key), one for a block that is not a canvas the caller may draw on, and one over the limits: 1,000 points a stroke, `max_strokes` strokes and 20,000 points a board, 50,000 points a session. A full board sets `limit_reached` (the board says so; clearing lifts it) and posts one `canvas_stroke_limit` line in the team feed. The first drop of each kind is a `block_update` session event `{op: "ink_dropped", reason}`; the session's close records `{op: "ink_summary", accepted, dropped}`. Accepted strokes are applied to the state at once and sent in one patch per 100 ms at most (any other patch sends them first; a snapshot carries them).

**Agent shapes.** `draw_on_canvas(shapes: [{kind, id?, x, y, w, h, points: [{x, y}], text, label, color}], block_id?, background?, replace?)` upserts the agent's shapes by id (≤ 50 a call, ≤ 200 on a board), `replace` swaps them all; `background` is a picture of this session (its asset id: a pinned frame, a gallery picture), `live_camera` or `none`. `clear_canvas(block_id?, which: all|shapes|strokes)`. Both are write built-ins, never in the background, silent on `realtime` / `half_cascade`; a signature board takes no shapes. On a panel with a canvas, `pin_frame` gains `canvas_block_id` and puts the pinned frame behind that board (annotation = `pin_frame` + `draw_on_canvas`); without a canvas its schema is unchanged.

**Reading the board.** `read_canvas(block_id?, question?)` asks the page for a PNG: `lkap.ui.request {method: "snapshot", payload: {block_id}}`; the page acks and streams the PNG on `lkap.ui.upload` with the canvas's `block_id` (a page without a board answers `ok: false` and the worker stops waiting). The worker takes the file only while that snapshot is awaited (20 s), only for a board the caller may draw on, only as a PNG ≤ 5 MiB, stores it as a session asset of kind `frame` with `meta: {block_id, source: "ink"}` (`CANVAS_SNAPSHOT_SOURCE`; set by the worker, never by the page; no new asset kind, no migration), shows it in no gallery (`UiState.assets` kind `drawing`) and names it in the board's `snapshot_asset_id`. The api stores a `frame` with `meta.source == "ink"` only when `meta.block_id` names a canvas of the session's panel the caller may draw on, only as a PNG ≤ 5 MiB (else 422 / 415 / 413). The agent's own vision model then reads it (`vision.describe_image(task="read_drawing")` → `{text, description}`); the reading reaches the model fenced as `<untrusted source="canvas:<block id>">` (≤ 2,000 characters; `FENCED_SITES`). With no vision model (a text-only LLM such as the Cloud default `google/gemma-4-31b-it`, or a realtime pipeline), on a phone call, on a board the caller cannot draw on or an empty board, the tool answers one plain sentence. `describe_asset` is also registered for a panel with a canvas, so a snapshot can be read again with a question. The "locate" half of the card (`describe_current_frame` returning boxes for `draw_on_canvas`) is not built: no vision model's boxes were verified (ask #95).

**Notebook ink sections (ask #57).** `NotebookSectionConfig.canvas_block_id` (ink sections only; `""` means none; omitted from the dump while unset) names the board the section shows; the worker seeds `NotebookInkSection.canvas_block_id` from it. `canvas_claim_issues(blocks)` (the api's panel validator calls it): the board must be a canvas of the panel, shown by one ink section only and not also inside a `layout`, else an error at `panel.blocks[i].config.sections[j].canvas_block_id`; a section without a board is valid ("Drawing board coming soon"). The console renders the board inside the notebook and leaves it out of the panel's flow (V6-10 / V6-14).

**`describe_panel`.** A canvas entry is `{caller_can_draw, caller_strokes (a count, never points), your_marks: ["<kind> <label>"], behind, full}`; a notebook ink section says `drawing: "on the board <id>"` or `"no drawing board yet"`.

**Validation.** The `canvas` config is strict like every block config. Tips and warnings: a board the caller may draw on, on an agent set up for phone calls, gets the phone tip at its `caller_can_draw`; and (`config_service.canvas_vision_issues`) a warning at `panel.blocks[i]` when the pipeline is realtime or half-cascade, or when the cascaded LLM is known not to see images (naming vision models of the same provider), a tip when nobody knows; nothing when `read_canvas` is switched off.

### The next blocks: `signature`, `chart`, `timer`, `code`, `cart` (V6-23)

`BlockType` gains five blocks (D-V6-20; configs in `lkap_contracts.blocks`, states, the event and the wire in `lkap_contracts.ui_protocol`). Every config is strict and every string and list capped; the config keys `disclosure_text`, `kind`, `mode` and `currency` seed the state field of the same name.

| Block | Config | State | Tools |
|---|---|---|---|
| `signature` | `disclosure_text` (≤ 2000; set, the agent cannot change it), `allow_decline` (on) | `SignatureBlockState` (requestable) `{status, submitted_at, disclosure_text ≤ 2000, signed, asset_id, text_hash, at}` | `request_signature` |
| `chart` | `kind: number\|bar\|line\|pie\|gauge` (`bar`), `show_table` (off) | `ChartBlockState {kind, title ≤ 120, unit ≤ 16, points: [ChartPoint {label 1..40, value (finite, \|v\| ≤ 1e12), series ≤ 40}] ≤ 200, gauge_min < gauge_max (0..100), caption ≤ 200, updated_at}`; `number`/`gauge` hold one value and no series, `pie` values ≥ 0 and no series, ≤ 8 series | `show_chart`, `update_block` |
| `timer` | `mode: countdown\|elapsed` (`countdown`), `max_seconds` (3600; ≤ 14,400) | `TimerBlockState {mode, label ≤ 80, status: idle\|running\|ended\|stopped, duration_s, started_at, ends_at, ended_at}` (a running timer has its times) | `start_timer` |
| `code` | `max_chars` (8000; 200..20,000), `wrap` (off) | `CodeBlockState {code ≤ 20,000, language (a label: ^[a-z0-9][a-z0-9+#._-]{0,23}$), title ≤ 120, updated_at}` | `show_code`, `update_block` |
| `cart` | `currency` (ISO 4217, `USD`), `max_lines` (20; ≤ 50) | `CartBlockState {currency, lines: [CartLine {id, name ≤ 120, quantity 1..9999, unit_price 0..1e9, line_total, note ≤ 120}] ≤ 50 (unique ids), adjustments: [CartAdjustment {label ≤ 40, amount}] ≤ 5, subtotal, total, updated_at}` | `cart_set`, `update_block` |

**Allow-lists.** `UPDATABLE_BLOCK_TYPES` gains `chart`, `code` and `cart` (S6-1: deliberately; the state models bound what `update_block` writes, so a 201-point chart is refused there too, and a cart patch may set only `currency`, `lines` and `adjustments`: the worker recomputes every total). `signature` (a request) and `timer` (run by the worker) stay refused. `STATE_DELTA_BLOCK_TYPES` (what a page's AG-UI `state_delta` may write) is `UPDATABLE_BLOCK_TYPES` less `kb_citations`, `custom`, `chart`, `code` and `cart`: what the agent shows the caller is never rewritten by the page. None of the five is in `EDITABLE_BLOCK_TYPES`. All five tools are on `NEVER_BACKGROUND_TOOLS`; the four writes are `WRITE_BUILTINS`.

**Signatures.** `request_signature(disclosure_text?, block_id?)` writes the wording (the block's `disclosure_text` when set, else the model's, control characters removed, ≤ 2000) and requests the block (`UiChannel.request_block`, `method="request"`, 180 s). The caller's strokes stay on the page. Sign answers `block_submit {values: {signed: true}}`, "Not now" `{signed: false}`; `signature` is not in `ANSWER_KEYS`, so a browser answer writes nothing but the status. On `signed: true` the worker asks for the picture through the canvas snapshot path: `lkap.ui.request {method: "snapshot", payload: {block_id: <the signature block>}}`, and the page streams a PNG (≤ `MAX_SIGNATURE_BYTES`, 1 MiB) on `lkap.ui.upload` with that `block_id`, taken only while awaited and stored as a session asset of kind `signature` with `meta: {block_id, source: "signature"}` (no migration; shown in no gallery, `UiState.assets` kind `signature`). Only then are `signed`, `asset_id`, `at` and `text_hash` (`consent_text_hash` of the worker's own wording) written and one `signature` session event recorded (`SignatureEvent {block_id, signed, text_hash, asset_id, method: "drawn"}`); a decline is recorded the same way without a picture; a Sign whose picture never arrives records nothing and sets the block `cancelled`. A barge-in, a cancel or a timeout releases the request (`cancelled`, R-V5-1); a `block_submit` on a signature block with no request waiting is dropped and recorded (`block_update {op: "late_answer_dropped"}`), never handed to the unsolicited-answer path. On `realtime` / `half_cascade` the tool returns `None` and the answer arrives as an urgent background result (`_REALTIME_SILENT_BUILTINS`); on phone channels and the text chat it answers at once (`{channel, signed: false}`) and is not silenced.

**Timers.** `start_timer(seconds, label?, mode?, block_id?)` writes a running `TimerBlockState` (`ends_at = started_at + seconds`, ≤ the block's `max_seconds`) and schedules the end on the session's `BackgroundRunner` (job `timer`, cancelled at session close). A new call replaces the running timer (the old end is cancelled); `seconds=0` stops it (`stopped`). At the end the worker writes `ended`, records `timer_ended {block_id, mode, duration_s}` (never the label) and adds one routine note for the model. The page counts on its own clock from `duration_s` once it sees `running`.

**Display tools.** `show_chart(points, kind?, title?, unit?, caption?, gauge_min?, gauge_max?, block_id?)`, `show_code(code, language?, title?, block_id?)` (lines and tabs kept, other control characters removed, ≤ the block's `max_chars`; never run, rendered as the text of a code block) and `cart_set(lines, adjustments?, currency?, block_id?)` (≤ the block's `max_lines`; totals by `cart_totals`, to the cent) replace the block's state. On phone channels they write nothing and answer `{visible: false}` (the cart with its total); on `realtime` / `half_cascade` they answer nothing.

**`describe_panel`.** `signature {status, wording_starts, signed, picture_saved}`, `chart {kind, heading, unit, points, series, values: ["label: value"] ≤ 8}`, `timer {status, mode, label, seconds_left | seconds_gone}`, `code {language, heading, lines}` (never the code), `cart {currency, lines: ["quantity x name"] ≤ 8, more_lines, total}`; fenced with the rest of the panel.

**Validation.** The five configs are strict; a `signature` block on an agent set up for phone calls gets a tip at `panel.blocks[i]` (`SIGNATURE_ON_PHONE_MESSAGE`).

### The AG-UI state adapter (`lkap_contracts.ui_agui`)

Our wire protocol is unchanged; the adapter maps it to AG-UI's state events (`STATE_SNAPSHOT {snapshot}`, `STATE_DELTA {delta: RFC 6902 ops}`; docs.ag-ui.com concepts/state, concepts/events and the Python SDK's `EventType`, checked 2026-09-27). Each LKAP op is mapped against the state before it:

| LKAP op | RFC 6902 |
|---|---|
| `set` on an object member | `add` (missing parents added as `{}` first) |
| `set` on an array index | `replace` |
| `append` | `add <path>/-` (`add <path> [value]` when the list is missing) |
| `remove` (no `key`) | `remove` (nothing if absent) |
| `remove` with `key` | `remove <path>/<i>` per matching item (`key` or `id`), highest index first |
| `upsert` | `replace <path>/<i>` on a match, else `add <path>/-` |

`/activity` overflow past 30 rows becomes `remove /activity/0`. Inbound, `agui_delta_to_patch(delta, state)` accepts only `/blocks/<id>/…` paths (and `from` paths), checks every op against the state the previous ops left (a failed `test` or a missing target refuses the whole delta), and maps `add`/`replace` on a member → `set`, `add …/-` → `append`, `remove` → `remove`, `replace` at an index → `set`, and an insert at an index, `move` and `copy` → `set` of the changed container. `AgentAction {action: "state_delta", payload: StateDeltaPayload {type?: "STATE_DELTA", delta (1–100 ops)}}` is **opt-in**: the worker refuses it unless the agent's `PanelLayout.accept_state_delta` is on (default off; ruling on ask #309; the refusal is logged once per session). When on, it applies such a delta from the caller's page to blocks `update_block` may write **except `kb_citations` and `custom`** (the worker's `STATE_DELTA_BLOCK_TYPES`): every touched block is validated before one `UiPatch` is sent; a requestable block, a link, consent, upload, captions or handoff block is refused. Round trips (`LKAP → AG-UI → LKAP` and `AG-UI → LKAP → AG-UI`) are pinned on the web fixture layout (`contracts/tests/test_ui_agui.py`).

---

## 11. Frontend panel registry (`web/src/panels/registry.ts`)

```ts
import type { UiState, AgentPublicOut } from "@/contracts/lkap-contracts";

export interface PanelProps {
  state: UiState;                                  // envelope; state.custom typed per panel via a zod schema derived from PackManifest.state_schema
  assets: Map<string, string>;                     // asset_id → object URL
  agent: AgentPublicOut;
  sessionId: string;
  perform: (action: { action: "ui_action"; payload: { name: string; data?: unknown } }) => Promise<unknown>;
  transcript: ReceivedMessage[];                   // from useSessionMessages, for panels that render turns
  connectionState: "connecting" | "connected" | "reconnecting" | "disconnected";
}

export interface PanelDefinition {
  id: string;                                      // matches PackManifest.ui_panel_id
  title: string;
  Component: React.ComponentType<PanelProps>;
  layout?: "side" | "wide";                         // "wide" gives the panel the main column (insurance notebook)
}

export const PANELS: Record<string, PanelDefinition>;   // { composite, generic, insurance_notebook } — insurance_notebook is the
                                                        // legacy pack's own panel, shipped with the pack this release (R-V6-3 #229)
export function resolvePanel(id: string): PanelDefinition;   // falls back to generic
```

Rules: panels are pure renderers of `state` (+ assets) and emit intents only through `perform`. The generic panel renders `status`, `progress`, `notes`, `checklist`, `assets` grid, `activity` feed, and a collapsible JSON view of `custom`.

---

## 12. Local dev commands, ports, deployment

Prereqs: `uv`, `pnpm`, `lk` logged in (`lk cloud auth`), LiveKit env vars in the shell or launch config.

```bash
# one-time
cd contracts && uv sync && uv run python -m lkap_contracts.export && cd ..
cd api && uv sync && uv run python -m lkap_api.keys generate   # prints LKAP_MASTER_KEY; put it in launch config / .env
cd api && uv run alembic upgrade head
cd web && pnpm install   # components already vendored under web/src/components/agents-ui/ — re-vendor only deliberately, see note below

# run (three terminals or scripts/dev.sh)
cd api   && LKAP_ADMIN_TOKEN=dev-admin LKAP_SERVICE_TOKEN=dev-service LKAP_MASTER_KEY=... uv run uvicorn lkap_api.main:app --reload --port 8080
cd agent && LKAP_API_BASE_URL=http://127.0.0.1:8080 LKAP_SERVICE_TOKEN=dev-service uv run python -m lkap_agent.main dev   # + LIVEKIT_URL/API_KEY/API_SECRET; no hot reload (D-W2-13)
cd web   && NEXT_PUBLIC_API_BASE_URL=http://localhost:8080 LKAP_ADMIN_TOKEN=dev-admin pnpm dev     # http://localhost:3000

# quality gates (each Python package)
uv run ruff check . --fix && uv run ruff format . && uv run mypy src/ --strict && uv run pytest -x -v
# live tests (need LiveKit creds only)
uv run pytest -x -v -m live
# web
pnpm lint && pnpm typecheck && pnpm test
```

Note: re-vendor `@agents-ui` only deliberately (`pnpm dlx shadcn@latest add --yes @agents-ui/all`), to pull in upstream component changes — the components are already vendored under `web/src/components/agents-ui/` and may carry local edits, so running the add on every setup is unnecessary and, with `--overwrite` or a newer CLI default, destructive. `pnpm e2e` (`playwright test`) is not a gate yet: `web/e2e/` has no specs, so it exits non-zero with "no tests found" (`web/e2e/README.md`).

`.claude/launch.json` entries (names): `lkap-api` (port 8080), `lkap-web` (port 3000), `lkap-agent` (no port; `bash -c "export ... && exec uv run python -m lkap_agent.main dev"`). Exports go inline in `runtimeArgs`; secrets never in files Claude writes except `env.example` placeholders.

Deployment:
- Agent → LiveKit Cloud: `agent/livekit.toml` `[agent] name = "lkap-agent"`; `cd agent && lk agent create --secrets-file secrets.env` (first time; `secrets.env` human-created with `LKAP_API_BASE_URL`, `LKAP_SERVICE_TOKEN`, `LKAP_PACKS`), then `lk agent deploy`; `lk agent logs` to tail; `lk agent update-secrets --secrets KEY=VAL` to rotate. Dockerfile = starter's `uv` multi-stage with `python3.12-slim-bookworm` and `CMD ["uv","run","python","-m","lkap_agent.main","start"]`; run `uv run --module livekit.agents download-files` in build (Silero weights).
- api → any container host with a volume at `/data` (`LKAP_DATA_DIR=/data`), `uvicorn --workers 1` (LanceDB single writer); web → `next build` standalone image; `deploy/docker-compose.yml` runs both for a VM. Public HTTPS required for the browser to reach api (CORS `LKAP_CORS_ORIGINS`).
