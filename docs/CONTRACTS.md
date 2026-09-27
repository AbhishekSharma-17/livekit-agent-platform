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
| `LKAP_PACKS` | opt | opt | — | default `packs.insurance_claim,packs.generic` |
| `LKAP_HTTP_TOOL_ALLOWED_HOSTS` | opt | opt | — | comma list; empty = only per-tool allowlist (the api reads it only for `LKAP_MCP_ALLOWED_HOSTS=@http`) |
| `LKAP_MCP_ALLOWED_HOSTS` | opt | opt | — | comma list of MCP server hosts (V5-09, D-V5-4); empty = any public `https` host that passes the network guard, non-empty = a ceiling, `@http` = reuse `LKAP_HTTP_TOOL_ALLOWED_HOSTS` (then empty allows nothing). Set the same value on both |
| `LKAP_LOG_LEVEL` / `LKAP_LOG_JSON` | opt | opt | — | `INFO` / `false` |
| `LKAP_EMBEDDER` | opt | — | — | `fastembed` (default) or `openai:<credential_id>` |
| `LKAP_VISION_MAX_FRAME_AGE_S` | — | opt | — | default `8` |
| `LKAP_IDLE_HANGUP_S` | — | opt | — | default `120`; worker hangs up a session that stays `away` this long while the agent is listening/idle; `None`/`0` disables (REVIEW-FINAL.md F-02) |
| `LKAP_SESSION_SWEEP_INTERVAL_S` | opt | — | — | default `60`; how often the stale-session sweep runs (D-W2-2b) |
| `LKAP_SESSION_STALE_CREATED_S` | opt | — | — | default `600`; a `created` row older than this → `failed` "never started" |
| `LKAP_SESSION_STALE_ACTIVE_S` | opt | — | — | default `21600`; an `active` row older than this → `failed` "summary never received" |
| `NEXT_PUBLIC_API_BASE_URL` | — | — | req | e.g. `http://localhost:8080` |
| `LKAP_ADMIN_TOKEN` (web server) | — | — | req | used by Next server actions/route handlers proxying console calls; never exposed to the client bundle |
| `PORT` | 8080 | — | 3000 | |

Vendor keys (`GOOGLE_API_KEY`, `OPENAI_API_KEY`, ...) are **not** read from env by the platform; they live in the credential vault. Exception for convenience: the api accepts `LKAP_BOOTSTRAP_CREDENTIALS_JSON` (JSON `{provider_id: {field: value}}`) at startup to seed credentials in dev.

### Apps (Composio, V5-18)

Connected third-party apps (`docs/v5/COMPOSIO.md`). No new environment variable and no new table:

- **Key.** Registry entry `composio` (`kind: "tool_provider"`, one secret field `api_key`, no `test`/`catalog`); a normal provider-key row. `POST /v1/credentials/{id}/test` checks it through `lkap_api.credential_tests` (Composio's session-info call, then the app count). `POST /v1/tool-providers/composio/key/test` checks a pasted key without storing it (10 per workspace per minute). Rotate is `PUT /v1/credentials/{id}` on the same row. Enablement is the `workspace_providers` row of `composio` (`POST …/enable`, `POST …/disable`; disable switches off the tools bound to the key or to a connection).
- **Connections.** One `credentials` row per connected app with `provider_id = "tool-provider-account"` (not a registry id, so `POST /v1/credentials` cannot create one). The encrypted bag holds references only: `toolkit`, `method`, `subject` (`ws:<workspace_id>` or `agent:<agent_id>`), `auth_config_id`, `connected_account_id`, status, picked actions, and while a sign-in is pending the SHA-256 of its single-use nonce and its expiry. `last_test_message` mirrors the status (`initiated|active|expired|failed|inactive|unknown`, transiently `verifying`), `last_test_ok` = active, `last_test_at` = last checked. The sessions sweep marks sign-ins unfinished after 10 minutes `expired`.
- **Routes** (`lkap_api/tool_providers/router.py`, reads `viewer` + `providers:read`, writes `admin` + `providers:write`): `GET …/status`, `POST …/key/test`, `POST …/enable`, `POST …/disable`, `GET …/toolkits`, `GET …/toolkits/{slug}`, `GET …/toolkits/{slug}/actions`, `POST …/connections`, `GET …/connections`, `GET …/connections/{id}` (refreshes from Composio), `POST …/connections/{id}/reconnect`, `DELETE …/connections/{id}[?purge=true]`, `POST …/materialise` (stores the picks and creates one `provider` tool per action, attached to `agent_id` when given — V5-47), `POST …/tools/{id}/refresh-schema[?apply=true]` (diffs a `provider` tool's pinned inputs with Composio's current ones; V5-47), and the unauthenticated `GET …/callback?flow=<row id>.<nonce>&status&connected_account_id` (302 to `/console/tools?tab=apps&connect=ok|error`). Prefix `…` = `/v1/tool-providers/composio`.
- **Models** (`lkap_contracts.tool_providers`, exported with an `App`/`Toolkit` prefix because `ConnectionOut` is taken): `ToolkitOut`, `ToolkitPage`, `AppAuthField`, `AppActionOut`, `AppActionPage`, `AppConnectIn`, `AppConnectOut`, `AppConnectionOut`, `AppConnectionPage`, `AppReconnectIn`, `AppKeyTestIn`, `AppKeyTestOut`, `AppsStatusOut`, `AppActionsPickIn`, `AppActionsPickOut`.
- **Tool bindings.** `_check_payload` lets a `composio` key bind only to an `mcp` definition tagged `origin.provider == "composio"` whose url is `https://backend.composio.dev/…`, or to a `provider` definition (V5-47), which also binds a connection row as `connection_id` with the connection's own `subject` (an app connected for one agent serves only that agent's tools); an `http` tool can never bind the key, and a connection row is never a tool credential.

### Apps on agents (V5-47)

`docs/v5/COMPOSIO.md` §3–§5. No environment variable; one migration, `v5_010_tool_provider_kind`
(`tools.kind` CHECK gains `provider`).

- **`provider` tools.** `ProviderToolDefinition` (`kind: "provider"`, `provider`, `name`, `description`,
  pinned `parameters`, `tool_slug`, `toolkit`, `connection_id`, `credential_id`, `subject`,
  `connected_account_id` (normally `None`: Composio picks the subject's account for the app), `headers`
  (default `{"x-api-key": "{{ secret.api_key }}"}`), `timeout_s`, `max_result_chars` (1500), `result_path`
  (`"data"`), `silent_reply`, `execution`, `schema_version`, `risk`) joins the `ToolDefinition` union and
  `ToolCreate.kind`. Materialisation names it `<toolkit>_<action>` (≤ 64 characters), reads run `auto`
  with an announcement, writes and destructive actions block and are not cancellable, 20 s. The api
  substitutes the key into `headers` at resolve time; the worker (`lkap_agent.tools.provider`) posts
  `{user_id: subject, arguments, version}` to `https://backend.composio.dev/api/v3.1/tools/execute/{slug}`.
- **`tools.apps`.** `ToolsConfig.apps: AppsMode` (`mode: actions|server|router|off = off`,
  `allowed_toolkits`, `denied_actions`, `router: {search, execute, manage_connections=false}`).
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
  not moved to `auth`). `kind: "oauth"` is saved from V5-14 on (below); from V5-16 the worker
  connects with an api-issued bearer (below) and skips a server the api issued no access for.
- **Host policy.** At save (`_check_payload`), before a test connection, and on the worker at connect
  time, an MCP url must pass the network guard (`net_guard.check_url`; the worker's `check_url_public`),
  be `https` (the api allows plain `http` only to a loopback host in `LKAP_ENV=dev`; the worker, which
  refuses loopback anyway, requires `https`), and sit on `LKAP_MCP_ALLOWED_HOSTS` when that is set.
  The ceiling applies to provider-provisioned servers too (list the provider's host when Apps are used).
  The api answers `422 blocked_destination`; the worker skips the server with a warning and a session
  event, and the session starts with its other tools.
- **Connection test.** `POST /v1/tools/{id}/test` (admin) → `McpTestResult{ok, tool_names, tool_count,
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
  `needs_auth` without a request (`unreachable` when the provider cannot refresh just now).
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
| `GET /v1/tools/{id}/oauth/status` | admin | `McpOauthStatusOut{status: not_connected\|connected\|needs_reauth\|revoked, issuer, scopes, expires_at, connected_at, last_refresh_at, registration, worker_supported}` (`worker_supported: true` from V5-16). |
| `GET /v1/oauth/mcp/callback?state&code&iss?&error?` | **none** (browser redirect; bound by `state`) | `302` to `{LKAP_WEB_BASE_URL}/console/tools?oauth=ok\|error` (no id, no token material; `Cache-Control: no-store`, `Referrer-Policy: no-referrer`); `400` when `state` names no live sign-in (unknown, used, expired, malformed). |
| `GET /v1/oauth/mcp/client-metadata.json` | public | The deployment's Client ID Metadata Document, only when `LKAP_PUBLIC_BASE_URL` is a public `https` origin; `404` otherwise. |

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
  committed before anything else → expiry → RFC 9207 (`iss` present: byte-equal to the recorded
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
  The four registry entries have no package, class, `test` or `catalog` (a key test is an open ask).
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
| `livekit-inference-stt` | stt | `livekit.agents.inference.STT` | — (`requires_credential=false`) | `language` ("en") | `deepgram/nova-3`; `deepgram/flux-general-en`, `assemblyai/universal-streaming`, `cartesia/ink-whisper`, `google/gemini-3.5-transcribe-live` |
| `livekit-inference-llm` | llm | `livekit.agents.inference.LLM` | — | `temperature` (0.7) | `google/gemma-4-31b-it`; `google/gemini-3.5-flash`, `openai/gpt-4.1`, `openai/gpt-4o-mini`, `openai/gpt-oss-120b` |
| `livekit-inference-tts` | tts | `livekit.agents.inference.TTS` | — | `voice` ("Ashley"), `language` ("en") | `inworld/inworld-tts-2`; `cartesia/sonic-3`, `deepgram/aura-2`, `rime/mistv3` |
| `google-realtime` | realtime | `livekit.plugins.google.realtime.RealtimeModel` | `api_key` | `voice` (enum, "Kore"), `temperature` (0.8), `tool_behavior` (enum `NON_BLOCKING`), `tool_response_scheduling` (enum `WHEN_IDLE`), `enable_affective_dialog` (false) | `gemini-3.8-live`; `gemini-3.1-flash-live-preview`, `gemini-2.5-flash-native-audio-preview-12-2025` — all `supports_video=true`; capabilities `video_input=true, silent_tool_reply=true` |
| `openai-realtime` | realtime | `livekit.plugins.openai.realtime.RealtimeModel` | `api_key` | `voice` ("marin"), `base_url` | `gpt-realtime`; `video_input=false`, `silent_tool_reply=true` |
| `deepgram-stt` | stt | `livekit.plugins.deepgram.STT` | `api_key` | `language` ("en-US") | `nova-3`; `nova-2`, `flux-general-en` |
| `openai-llm` | llm | `livekit.plugins.openai.LLM` | `api_key` | `base_url`, `temperature` | `gpt-4.1`; `gpt-4o`, `gpt-4.1-mini` |
| `google-llm` | llm | `livekit.plugins.google.LLM` | `api_key` | `temperature` | `gemini-2.5-flash`; `gemini-3.5-flash` |
| `cartesia-tts` | tts | `livekit.plugins.cartesia.TTS` | `api_key` | `voice`, `language` ("en") | `sonic-3` |
| `elevenlabs-tts` | tts | `livekit.plugins.elevenlabs.TTS` | `api_key` | `voice_id` | `eleven_turbo_v2_5`; `eleven_flash_v2_5` |
| `openai-tts` | tts | `livekit.plugins.openai.TTS` | `api_key` | `voice` ("ash") | `gpt-4o-mini-tts` |
| `bey-avatar` | avatar | `livekit.plugins.bey.AvatarSession` | `api_key` | `avatar_id` (default `b9be11b8-89fb-4227-8f86-4a881393cbdb`) | — |
| `tavus-avatar` | avatar | `livekit.plugins.tavus.AvatarSession` | `api_key` | `face_id`, `pal_id` (both optional) | — |
| `google-image-gen` | image_gen | `lkap_agent.providers.image_gen.GoogleImageGen` | `api_key` | — | `gemini-3.1-flash-image` |
| `openai-image-gen` | image_gen | `lkap_agent.providers.image_gen.OpenAIImageGen` | `api_key` | `size` ("1024x1024") | `gpt-image-1` |
| `fastembed-embedding` | embedding | `lkap_api.kb.embed.FastEmbedEmbedder` | — | — | `BAAI/bge-small-en-v1.5` |
| `openai-embedding` | embedding | `lkap_api.kb.embed.OpenAIEmbedder` | `api_key` | — | `text-embedding-3-small` |

**Language capabilities (V5-31).** `ProviderCapabilities.languages` holds base codes (`hi`, not `hi-IN`) from the vendor's documentation; empty means *not recorded* (validators stay silent), never "none". Three STT-only fields: `language_detection` (the value of the entry's `language` field that asks for detection: `multi` for `livekit-inference-stt` and `deepgram-stt`, `multi` for `openai-stt`/`openrouter-stt` which the worker's factory maps to `detect_language=True`, `unknown` for `sarvam-stt`), `detect_languages` (what detection covers when narrower than `languages`: Deepgram Nova-3 `multi` = en, es, fr, de, hi, ru, pt, ja, it, nl) and `language_switch` (`update_options(language=...)` on the `STT` object reaches the running transcriber in livekit-agents 1.8.3: Inference, Deepgram, OpenAI/OpenRouter, Azure, Cartesia, Clova, fal Wizper, Fireworks, Mistral, SLNG, Smallest, xAI, Baseten; Google and Gladia take `languages=`, AssemblyAI `language_codes=`, Sarvam only per stream with a `model`, Palabra on new streams only). Recorded rows: Nova-3 on Inference and Deepgram, the OpenAI transcription list (57), Sarvam STT (24 codes, 1.8.3 plugin enum) and Sarvam TTS (11). Helpers: `LANGUAGE_CODE_PATTERN` (`^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$`), `LANGUAGE_NAMES`, `base_language`, `language_name`, `declares_language` (`None` for an empty list).

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
-- `weaviate`, `cohere-rerank`, `voyage-rerank`); the key is a vault credential by id, returned only as
-- its fingerprint. A connection with knowledge bases cannot be deleted (409); its url/collection/index
-- cannot change while they exist. The vendor stores carry `id, kb_id, document_id` (+ the text only
-- when the store's own keyword search is on); chunk text stays in `kb_chunks` (D-V5-37).
CREATE TABLE knowledge_connections (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name TEXT NOT NULL, kind TEXT NOT NULL,           -- qdrant|pinecone|weaviate|cohere_rerank|voyage_rerank
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
PipelineMode = Literal["realtime", "cascaded"]


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
    image_gen: ProviderRef | None = None
    workflow_llm: ProviderRef | None = (
        None  # None → llm (cascaded) or livekit-inference-llm default (realtime)
    )
    turn_handling: dict = {}  # passed to TurnHandlingOptions (validated keys only)


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


class AgentConfig(BaseModel):
    v: Literal[1] = 1
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

    v: Literal[1] = 1
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

Languages (V5-31): `effective_languages(voice)` is `voice.languages` or `[voice.language]`, so an agent saved before V5-31 behaves as before and registers no `switch_language`. `ResolvedAgentConfig.voices_by_language: dict[str, ResolvedProvider]` carries each voice resolved with its key (**secrets**; empty outside cascaded / half-cascade). The worker appends one fixed languages rule to `instructions` (`session_builder.with_language_rule`, so flow nodes get it too), builds the STT slot with the registry's `language_detection` value when `auto_detect` is on, and on a switch (the `switch_language` built-in, or `DETECTION_TURNS = 2` consecutive caller turns detected in another allowed language) calls `stt.update_options(language=...)` when the entry has `language_switch` and is not detecting, swaps the voice with `Agent.update_options(tts=...)` (no handoff: `on_enter` would re-run; a later flow node re-applies it) and appends a reply-language note at the tail of the chat context (on a detected switch; the tool's answer says it on a tool switch). Validators (`config_service.language_issues`): a language the STT entry does not list → error at `voice.languages`; `auto_detect` on an STT without detection, or a language detection does not cover → warning at `voice.auto_detect`; several languages on an STT that cannot switch → warning; a language with no voice that the agent's voice does not list → warning at `voice.voices_by_language`; each voice is checked like a `tts` slot at `voice.voices_by_language.<code>`.

`COMPLIANCE_PRESETS` holds the three presets (plain wording, a counsel note each; `us` says "confirm with counsel for two-party-consent states"; no state list). The worker (`session_builder.apply_compliance`, after the flow preparation) fills `disclosure.text`, `recording.consent_text` and the empty `text` of `recording`/`ai_disclosure` consent blocks from `compliance`, then puts the disclosure in front of the greeting once (or at a `{disclosure}` placeholder) when `position` is `greeting`/`both`, or `banner` on a phone call; without a spoken greeting it becomes an instruction for the first reply. `consent_text_hash(text)` is the SHA-256 of the exact UTF-8 wording, carried by every `consent` event.

**Privacy and post-call fields (V5-30, P §4.2 C10/C23).** Additive; the defaults keep every agent saved before them unchanged (nothing masked, everything kept, telemetry as before, no fields):

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

Validation rules (api, at save): every `ProviderRef.provider_id` exists and matches the slot kind; `credential_id` present iff required and credential's `provider_id` matches; `model` in `spec.models` **or** free text (warn, not error — Inference lists churn); a realtime provider whose `spec.capabilities.video_input` is false combined with `capabilities.camera`/`screen_share` = warning (the model will not see frames; frames still reach the UI/pin path); avatar works with both modes.

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
POST /v1/agents                          AgentCreate -> AgentOut (201)
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

# ---- tools (admin)
class ToolCreate(BaseModel): agent_id: str | None; kind: Literal["http","mcp"]; name: str; definition: HttpToolDefinition | McpServerDefinition; enabled: bool = True
class ToolOut(ToolCreate): id: str; created_at; updated_at
POST/GET/PUT/DELETE /v1/tools[/{id}]     (GET list filters: agent_id, kind)
POST /v1/tools/{id}/dry-run              { arguments: dict } -> { ok: bool; result: str; status_code: int | None; duration_ms: int }   # http only

# ---- knowledge bases (admin)
class KbCreate(BaseModel): name: str; description: str = ""; embedder_id: str = "fastembed-embedding"
class KbOut(BaseModel): id; name; description; embedder_id; chunk_count: int; document_count: int; created_at; updated_at
class KbDocumentOut(BaseModel): id; kb_id; filename; mime; bytes; status; error: str | None; chunk_count; created_at
class KbSearchRequest(BaseModel): query: str; k: int = 4
class KbHit(BaseModel): chunk_id: str; document_id: str; filename: str; score: float; text: str
class KbSearchResponse(BaseModel): hits: list[KbHit]
POST/GET/PUT/DELETE /v1/knowledge-bases[/{id}]
POST /v1/knowledge-bases/{id}/documents  multipart file -> KbDocumentOut (202; ingestion in FastAPI BackgroundTasks; poll GET)
GET  /v1/knowledge-bases/{id}/documents  -> Page[KbDocumentOut]
DELETE /v1/knowledge-bases/{id}/documents/{doc_id} -> 204
POST /v1/knowledge-bases/{id}/search     KbSearchRequest -> KbSearchResponse

# ---- packs (admin)
class PackOut(BaseModel): manifest: PackManifest
GET  /v1/packs                           -> { items: list[PackOut] }

# ---- sessions (admin)
class SessionOut(BaseModel): id; agent_id; agent_name; config_version; room_name; status; pipeline_mode; created_at; started_at; ended_at; usage: dict | None; error: str | None
    caller_timezone: str | None   # R-V5-10: from the `locale` event, stored as usage["caller_timezone"] by the summary
class SessionDetailOut(SessionOut): transcript: list[TranscriptTurn] | None; final_ui_state: UiState | None
class TranscriptTurn(BaseModel): role: Literal["user","assistant"]; text: str; ts: float; interrupted: bool = False
class SessionEventOut(BaseModel): id: int; ts: datetime; type: str; payload: dict
GET  /v1/sessions?agent_id=&status=      -> Page[SessionOut]
GET  /v1/sessions/{id}                   -> SessionDetailOut
GET  /v1/sessions/{id}/events?after_id=  -> Page[SessionEventOut]
POST /v1/sessions/{id}/listen-token     -> SessionListenTokenOut   # V5-37: builder+, API keys sessions:listen (sessions:write implies it)
POST /v1/sessions/{id}/whisper          SessionWhisperIn {text ≤ 1000, reply_now=false} -> SessionWhisperOut {id, delivered_to} (202)

# ---- internal (service token)
GET  /internal/v1/sessions/{id}/resolved -> ResolvedAgentConfig   (404 if unknown; 409 if status=ended; marks status=active, started_at)
POST /internal/v1/tools/{id}/oauth/token  McpOAuthTokenIn -> McpOAuthTokenOut   (V5-16; 404/409/503, see §3)
class SessionEventIn(BaseModel): ts: float; type: str; payload: dict
POST /internal/v1/sessions/{id}/events   { events: list[SessionEventIn] } -> 202
class SessionSummaryIn(BaseModel): status: Literal["ended","failed"]; usage: dict; transcript: list[TranscriptTurn]; final_ui_state: UiState | None; error: str | None = None
PUT  /internal/v1/sessions/{id}/summary  SessionSummaryIn -> 204
class InternalKbSearchRequest(BaseModel): kb_ids: list[str]; query: str; k: int = 4
POST /internal/v1/kb/search              InternalKbSearchRequest -> KbSearchResponse

# ---- health (public)
GET  /v1/health                          -> { ok: bool; version: str; livekit_url: str; packs: list[str]; db: "ok"|"error" }
```

Event `type` values posted by the worker: `session_started`, `agent_state` (`{state}`), `user_turn` (`{text}`; V5-31 adds `language` when the transcriber reported one), `agent_turn` (`{text, interrupted}`; V5-31: may carry the reply `language` of a multilingual agent, but only when the message was stamped before the event was recorded — the stored `TranscriptTurn.language` is the reliable record), `language_switched` (`LanguageSwitchedEvent {from_language, to_language, source: tool|detected, stt_switched, voice_switched}`, V5-31), `tool_call_started` (`{call_id, tool, args_redacted}`), `tool_call_ended` (`{call_id, tool, status, duration_ms, result_preview}`), `tool_call_updated` (`{call_id, tool, message_preview}`: a background tool reported progress; its first update is the announcement, docs/v4/BACKGROUND-TOOLS.md D-V4-38), `tool_reply` (`{call_ids, status, speech_id}`: the deferred reply that voices background results; `status` is `scheduled`, `completed`, `interrupted` or `skipped`, the last meaning the model had already said it), `workflow_run` (`{name, duration_ms, status}`), `ui_state` (`{seq}` only), `asset` (`{asset_id, kind, bytes}`), `escalation` (`EscalationEvent {reason, urgency, mode}`; `mode` since V5-37, left out for `transfer` so the old payload is unchanged), `metrics` (`{kind, data}`), `error` (`{message}`), `info` (`{message}`), `session_ended` (`{reason}`), `locale` (`LocaleEvent {caller_timezone, source, business_timezone}`, once at session start, R-V5-10: `source` is `browser` (the `lkap.tz` attribute), `number` (the caller's E.164 number maps to exactly one zone), `business` (`AgentConfig.timezone`, also when `locale.caller_timezone == "business"`), `workspace` (`workspaces.settings.locale.timezone`) or `default` (UTC); the summary copies `caller_timezone` into `usage`), `consent` (`ConsentEvent {kind, accepted, method, text_hash, block_id}`, one per answer, V5-15: `kind` is `recording`, `ai_disclosure`, `terms` or `custom`, `method` is `tap` or `voice`, `text_hash` the SHA-256 of the exact wording; the api folds the latest answer per kind into `sessions.consent_state` (`ConsentState {latest: {kind: ConsentRecord}}`, migration `v5_009_consent`)), `tool_needs_reauth` (V5-47 / V5-16: `{call_id, tool}` when a tool call failed because its app or MCP sign-in needs an admin — the model heard "This app needs to be reconnected by an admin" or "This integration needs to be re-authorised by an admin"; `{mcp_server, reason}` with `reason` `needs_reauth`, `unauthorized` or `insufficient_scope` when an MCP request other than a tool call hit it, once per server and reason), `privacy_scrubbed` (V5-30, api-written, not by the worker: `{tier, replaced: {email, card, number}, model_pass: none|done|failed|unsupported, tool_payloads_dropped}`, once per session; its `ts` is `scrubbed_at`).

Consent routes (V5-15): `POST /internal/v1/sessions/{id}/recording/start` answers 409 for an agent with `recording.require_consent` until the latest `recording` answer is an acceptance (the worker holds the start back and flushes its events first). At the summary, a consent-gated voice session that was never recorded keeps `recording_status = "none"` with `recording_error` "Not recorded: consent declined" (or "Not recorded: the caller did not agree to be recorded"), returned as `SessionDetailOut.recording.error`. `PUT /v1/workspaces/{id}` accepts `settings.compliance` (`ComplianceSettings`, merged one level down, 422 when invalid); `GET /v1/workspaces/{id}/compliance` → `ComplianceOut {settings, effective: ResolvedCompliance, presets: list[CompliancePreset]}`.

V5-30 (privacy and post-call fields): `QaOut.fields: dict[str, Any]` (`session_qa.raw["fields"]`, `{}` when none); `SessionDetailOut.scrubbed_at`; `GET /v1/sessions/export.csv` (the list's filters, newest first, `limit` ≤ 5000) → `text/csv` with the fixed columns `session_id, agent_id, agent_name, channel, status, created_at, ended_at, duration_s, cost_usd, disposition, qa_status, qa_score, qa_sentiment` then one column per post-call field (a field named like a fixed column is `field_<name>`; a text cell starting `= + - @` gets a leading `'`); `POST /v1/sessions/{id}/scrub` → 202 `SessionScrubOut {status: queued|already_scrubbed, job_id, scrubbed_at}`, 409 `storage_tier_full` / `not_ended`; `DELETE /v1/sessions/{id}` also removes the session's stored files and recording at once (S5-36). Knowledge (S5-28/S5-30): an upload's extension decides its type (`.md .markdown .txt .csv .json .pdf .docx .pptx .xlsx .html .htm`, else 415) and a url import's chosen filename cannot pick another extractor; 409 `quota_exceeded` past 1,000 documents per knowledge base or 2 GiB per workspace; `POST …/reindex` checks existence without reading bytes and, like `POST …/evaluate`, answers 409 while one is queued or running for that knowledge base, with a shared 20/min per-workspace limit (429). The `session.qa_completed` webhook is where the fields belong (the worker scores after `session.ended` is sent); carrying them there is ask #174.

V5-27 (the V5-26 security fixes; `docs/v5/SECURITY-REVIEW-V5.md`): `ConsentEvent.turn_id` (a voice answer's user turn; null for a tap) and `ConsentState.withdrawn_at {kind: epoch seconds}` (an acceptance replaced by a decline) are additive; `record_consent` no longer takes `method` (always `voice`; a tap comes only from the block). `POST /internal/v1/sessions/{id}/recording/stop` (service token) → `{stopped, egress_id}` stops the session's Egress once when the caller withdraws recording consent; `recording_error` then says "Stopped early: the caller withdrew consent". `InternalKbSearchRequest` gains `session_id` (only that session's workspace is searched; unknown session → 404) and both search requests cap `query` at 1–2000 characters. `user_turn` events carry `turn_id`; the worker records `recording_stopped {reason: consent_withdrawn}`. Third-party text reaches the model as `<untrusted source="…">…</untrusted>` (R-V5-15). `PUT /v1/workspaces/{id}` accepts only `locale`, `compliance`, `cost.reconcile` and `telephony` in `settings`. Any request body over 26 MB is `413 payload_too_large`.

V5-40 (caller memory, D-V5-17; opt-in): `AgentConfig.memory: MemoryConfig {enabled=false, scope: agent|workspace = agent, retention_days=90 (1..3650), consent_line ≤ 500 | null, max_recall_tokens=400 (50..2000), verbatim=false}`. `POST /internal/v1/memory/recall` (service token; `MemoryRecallIn {session_id, caller_e164: E.164 | null}` → `MemoryRecallOut {status: recalled|empty|disabled|no_identity|unavailable|failed, memories ≤ 20 × ≤ 500 chars, newest first, remember}`): the api resolves the caller from the session (phone: `caller.from` inbound / `caller.to` outbound, else the hint; other channels: `participant_identity` unless platform-generated `user-xxxxxxxx` or the `<channel>-caller` placeholder), reads the backend within 3 s, masks the texts when `privacy.storage_tier != "full"` and records `memory_recalled`; `remember` is true when the session will be written (identity known, backend installed, `verbatim` or an OpenAI/OpenRouter model with a key). The worker appends the memories to the instructions inside `<untrusted source="memory">` (bounded by `max_recall_tokens` at ~4 chars/token, whole memories only) and the consent line when `remember`. After the summary commits, `memory.enabled` enqueues the job **`memory_remember`**: the transcript (latest 200 turns; masked like the scrub's deterministic pass when the tier is not `full`; the caller's lines only when `verbatim`) goes to the backend, which extracts facts with the agent's `pipeline.llm` / `workflow_llm` (OpenAI or OpenRouter at the registry's base URL, through the platform's guarded client); once per session; records `memory_stored`. `DELETE /v1/memory/subjects/{subject_id}` → `MemoryForgetOut {subject_id, forgotten, sessions_updated}` (every scope; 404 unknown, 503 `memory_unavailable` when the backend is missing, nothing deleted); `POST /v1/memory/purge` (`MemoryPurgeIn {confirm: true}`, 422 otherwise) → `MemoryPurgeOut {status: queued|nothing_to_purge, subjects, job_id}` deletes the memory key and the subject rows at once and the backend entries in the job **`memory_purge`**; `GET /v1/sessions/{id}/memory` → `SessionMemoryOut {enabled, subject_id, recall_status, recalled, store_status, store_reason, stored, forgotten_at}`. Forget, purge and the retention sweep (the sessions sweep, `retention_until` past) blank the `memories` of the affected sessions' `memory_recalled`/`memory_stored` events (`forgotten: true`) and record `memory_forgotten {reason: caller|workspace|retention}` on each. api-written events: `memory_recalled` (`MemoryRecalledEvent {status, count, memories, forgotten}`), `memory_stored` (`MemoryStoredEvent {status: stored|nothing_new|skipped|failed, count, memories, reason, forgotten}`), `memory_forgotten`. `/v1/memory/*` has no `ROUTE_POLICY` rule yet (admin, scope `*`). Validators (warnings): memory on without the backend installed (`memory.enabled`), or not verbatim without an OpenAI/OpenRouter model with a key (`memory.verbatim`).

V5-32 (answering-machine detection, warm transfer, the handoff block; migration `v5_007_telephony_amd` adds the nullable `calls.amd_result`, `.transfer_mode`, `.transfer_summary`): `AgentConfig.telephony.amd: AmdConfig {enabled=false, on_machine: hangup|leave_message = hangup, message ≤ 1000, ivr_detection=false}` and `TransferTarget.mode: cold|warm = cold`. `CallOut` gains `amd_result` (`human`, `machine-ivr`, `machine-vm`, `machine-unavailable`, `uncertain` — livekit-agents 1.8.3 `AMDCategory`; `null` = no detection ran), `transfer_mode` and `transfer_summary`. `POST /internal/v1/telephony/calls/report` (`CallReportIn`) gains `status: "transferred"` and `amd_result`, `transfer_mode`, `transfer_to`, `transfer_summary`: the verdict is stored once (a later report never overwrites it) and a `machine-*` verdict queues the new webhook **`call.voicemail`** (`data` = the `call.*` fields plus `amd_result`, same `call_event` outbox as `call.started/ended`); `transferred` moves the row forward (a warm transfer never passes through the SIP REFER route) and keeps the mode, target and summary (a cold fallback keeps its summary here instead of speaking it, D-V5-21). `ResolvedAgentConfig.warm_transfer: WarmTransferRoute {trunk_id, caller_id, targets} | null` — filled only for a phone session on a LiveKit Cloud connection with exactly one synced outbound trunk, naming the `warm` targets the dialing policy allows at resolve time (the worker dials those itself; `null` = every transfer is cold). Worker events: `voicemail` (`VoicemailEvent {result, action: hangup|leave_message|navigate, message_left}`, machine verdicts only) and `transfer` (`TransferEvent`: the V2-17 `{to, ok, status, reason}` plus `mode` (what ran), `requested_mode` (the target's), `target` (label), `outcome: connected|transferred|timeout|declined|refused|failed`, `summary`); a warm request that falls back also records `info` ("Warm transfer is not available (…); transferring directly."). `transfer_call` gains an optional `summary` argument. Validators (warnings): a `warm` target off LiveKit Cloud or without exactly one outbound line (`telephony.transfer_targets[i].mode`), `amd.enabled` without an outbound line or SIP, or with a realtime/half-cascade pipeline (`telephony.amd.enabled`), `leave_message` without `message` (a "Tip:" at `telephony.amd.message`). The `handoff` block (`HandoffBlockConfig {show_queue, show_agent_name}`, `HandoffBlockState {status: idle|requested|connecting|connected|timeout|ended, mode, target, queue_position, agent_name, reason}`) is written by `transfer_call` through `lkap_agent.ui.blocks.set_handoff`.

V5-37 (supervisor listen-in and typed whisper; no migration): the API-key scope **`sessions:listen`** (`sessions:write` implies it; members need `builder`+). `POST /v1/sessions/{id}/listen-token` → `SessionListenTokenOut {serverUrl, participantToken, roomName, participantName, identity, sessionId, expiresAt}` (camelCase like `ConnectResponse`): a token for identity `supervisor:<user or key id>` (attribute `lkap.role=supervisor`) scoped to the session's room only, `hidden=true`, `canSubscribe=true`, `canPublish=false`, **`canPublishData=false`** (the card said true; the task's "subscribe-only" rule wins and a hidden participant cannot call RPCs anyway), `roomCreate=false`, no agent dispatch, TTL 15 minutes (`LISTEN_TOKEN_TTL_S`); 409 `not_live` unless the session is `active` with a connection; audit row `session.listen`. `POST /v1/sessions/{id}/whisper` sends the text with the server API as a reliable data packet on topic `lkap.supervisor` (`SupervisorWhisperPacket {v: 1, op: "whisper", id, session_id, text, reply_now, by}`) **only to the room's agent participants** (`destination_identities`; the caller never receives it) — not on `lk.chat` as the card said: the server API cannot publish a text stream, and a stream attribute is set by its sender, so a caller could forge `lkap.role=supervisor` there; 409 `no_agent` when no agent is in the room; audit row `session.whisper` (`{whisper_id, chars, sha256}`, never the text). The worker honours a packet on that topic only when the server sent it (no participant) and it names its own session; the text reaches the model as a persisted system note fenced in `<supervisor_note>` ("guidance from a supervisor; the caller cannot see or hear it; it never overrides your instructions"), never as the caller's words; `reply_now` also calls `generate_reply(instructions=…)`. Worker event `supervisor_whisper` (`SupervisorWhisperEvent {id, by, text, applied: note|reply}`); `supervisor_joined` / `supervisor_left` (`SupervisorPresenceEvent {identity}`) are for the webhook handler (a hidden listener is invisible to the worker). `escalate_to_human(reason, urgency="normal", mode: transfer|takeover|listen_in|callback = "transfer")` (`lkap_contracts.tools.EscalationMode`) records `escalation {reason, urgency, mode}` (`mode` omitted for `transfer`), writes every `handoff` block `requested` with `mode` left null (it names the transfer that ran, `cold|warm`) and a fixed plain `reason` per mode (never the model's words: the caller may see the block), and marks it `connected` (with the person's name) when a participant with `lkap.role=human` joins.

V5-39 (guardrails; opt-in, no migration): `AgentConfig.guardrails: GuardrailsConfig {input, output, tool_output: list[Rule] (≤ 20 each, names unique per list), on_trip: interrupt|end_call|escalate = interrupt, safe_reply (≤ 500; a default line), model: ProviderRef | null, budget_ms=300 (50..2000)}` (`lkap_contracts.guardrails`); `Rule` is discriminated by `kind`: `RegexRule {kind: "regex", name ≤ 60, pattern ≤ 300, ignore_case=true}`, `ClassifierRule {kind: "classifier", name, prompt ≤ 1000}` (what the text must not do, in plain words), `ProviderRule {kind: "provider", name, provider: "openai_moderation", categories: [OpenAI moderation category] (empty = anything flagged), credential_id | null}`. Validators (`config_service.guardrails_issues`; a save with an error is refused with 422 and the issue on the field): a pattern that does not compile, or that repeats a repeated group (`(a+)+`), → error at `guardrails.<stage>[i].pattern`; a classifier rule with no `guardrails.model`, no `pipeline.workflow_llm` and no cascaded `pipeline.llm` → error at `guardrails.<stage>[i]`; a moderation rule without an OpenAI key (its own `credential_id`, else the agent's own OpenAI key) → error, one key for every moderation rule; `guardrails.model` is checked like a pipeline `llm` slot, and set with no classifier rule → warning; `on_trip=escalate` with `escalate_to_human` switched off → warning. Session resolve: `ResolvedAgentConfig.builtin_providers` gains `guardrails_llm` (`guardrails.model` resolved with its key, only with a classifier rule) and `guardrails_moderation` (`{api_key}` only; OpenAI's own endpoint). Worker: input rules in `on_user_turn_completed` (regex first; model rules overlap the knowledge and vision injection; a trip speaks the safe reply and raises `StopResponse`, so the caller's turn is neither kept nor answered), and for a realtime model with server-side turns on the committed caller message in parallel (a trip interrupts); output rules in `Agent.transcription_node`, sentence by sentence while the text streams through untouched (a trip → `session.interrupt(force=True)` while that reply still plays, then the safe reply) — not on `conversation_item_added` as the card said, which fires after playout; tool-output rules on every result of `run_with_policy` (`tools.execution.guard_tool_output`; built-in, HTTP, connected-app and opted-in pack tools; MCP toolsets are not covered yet), a trip replacing the result with a fixed "withheld" line that carries the safe reply. `end_call` ends the job once the safe reply has played; `escalate` calls `escalate_to_human(reason="A guardrail tripped (<rule>).", urgency="high")` (never the caller's words). Fail directions: regex rules cannot time out; classifier and moderation rules **fail open** past `budget_ms`, on an error or without a key. Events: `guardrail` (`GuardrailEvent {stage: input|output|tool_output, rule, kind, action: interrupt|end_call|escalate|replaced, excerpt_hash, excerpt | null, categories, tool | null, latency_ms}`; `excerpt_hash` is a keyed hash with a per-session random key, `excerpt` ≤ 120 characters only when `privacy.storage_tier == "full"`) and `guardrail_timeout` (`GuardrailTimeoutEvent {stage, rule, kind, reason: timeout|error|unavailable, budget_ms}`). The activity feed gets a row with `ActivityEvent.kind = "guardrail"` (`source="guardrail"`, `detail {stage, rule, action}`); `kind` is `null` on every other row.

`metrics` kinds: `session_usage` (`data` = the SDK's `AgentSessionUsage`, on every update) and, only when the workspace opted into cost reconciliation (`ResolvedAgentConfig.cost_reconcile` non-empty, docs/v4/COSTS.md D-V4-45), one `provider_requests` just before the summary: `data = {llm, stt, tts: [{request_id, provider, model}], dropped}` — per-request vendor ids only (≤ 2,000; `dropped` counts the rest), never a prompt, completion or secret; the api's `cost_reconcile` job looks them up.

**Emission rules (DECISIONS-W2 §D-W3-1).**
- Platform-owned types are emitted only by the worker: `session_started`, `agent_state`, `user_turn`, `agent_turn`, `tool_call_started/updated/ended`, `tool_reply`, `workflow_run`, `metrics`, `error`, `info`, `session_ended`. A pack must not emit them.
- Packs and tools may emit `escalation` (`{reason: str, urgency: "low"|"normal"|"high"}`) and `info` (`{message: str}`) via `PackSessionContext.record_event(event_type, payload)` (§8). Any other type is stored as-is by the api (`SessionEventIn.type` is a plain `str`) and rendered generically by the console; packs should prefix custom types with their pack id (`insurance_claim.route_changed`) so they never collide with platform types.
- `escalate_to_human` emits `escalation{reason, urgency}` right after `set_status("Escalated", "warning")`.
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


class McpServerDefinition(BaseModel):
    kind: Literal["mcp"] = "mcp"
    name: str
    url: str  # streamable HTTP endpoint
    headers: dict[str, str] = {}  # {{ secret.NAME }} allowed
    credential_id: str | None = None
    allowed_tools: list[str] | None = None
    timeout_s: float = 5
    sse_read_timeout_s: float = 300


ToolDefinition = Annotated[HttpToolDefinition | McpServerDefinition, Field(discriminator="kind")]
```

Note on tool secrets: `ProviderKind` includes `"secret_bag"`; the registry entry `http-tool-secret` (`kind="secret_bag"`, `requires_credential=true`, free-form `NAME=value` secret pairs in the console) is what `credential_id` points to for HTTP/MCP tools. The api substitutes `{{ secret.NAME }}` in `headers`/`url`/`body_template` when building `ResolvedAgentConfig.tools`, so the worker never sees credential ids or the vault.

Agent-side construction (`lkap_agent.tools.declarative`): `function_tool(_http_handler_for(defn), raw_schema={"name": defn.name, "description": defn.description, "parameters": defn.parameters})` where `async def handler(raw_arguments: dict[str, object], context: RunContext) -> str`. MCP: `mcp.MCPServerHTTP(url=..., headers=..., transport_type="streamable_http", allowed_tools=..., timeout=..., sse_read_timeout=...)`.

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

---

### Live captions and per-turn language (V5-31)

`TranscriptTurn.language: str | None` (the caller's detected language on a user turn, the reply language on an assistant turn; `null` when unknown) is stored with the summary and returned by `GET /v1/sessions/{id}`. `BlockType` gains `captions` (`CaptionsBlockConfig {show_user=true, show_agent=true, target_language: str|null (reserved for translation), position: block|bottom}`, `CaptionsBlockState {language, target_language}`). While the panel has a `captions` block the worker streams `CaptionSegment {v:1, id, speaker: user|agent, text, final, language, ts}` JSON on the text-stream topic `lkap.captions` (`TOPIC_UI_CAPTIONS`): an utterance keeps its `id` from the first interim to its final; the agent's words come from a text output after RoomIO's transcription (`TextOutputOptions(next_in_chain=...)`), so they are timed to the audio; interim agent captions at most every 0.25 s. Captions are never stored in `UiState`.

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

export const PANELS: Record<string, PanelDefinition>;   // { generic, insurance_notebook }
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
