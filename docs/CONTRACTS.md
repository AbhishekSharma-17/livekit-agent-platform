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
| `LKAP_PUBLIC_BASE_URL` | opt | — | — | used in connect response for asset links (unused MVP) |
| `LKAP_PACKS` | opt | opt | — | default `packs.insurance_claim,packs.generic` |
| `LKAP_HTTP_TOOL_ALLOWED_HOSTS` | — | opt | — | comma list; empty = only per-tool allowlist |
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
class SessionDetailOut(SessionOut): transcript: list[TranscriptTurn] | None; final_ui_state: UiState | None
class TranscriptTurn(BaseModel): role: Literal["user","assistant"]; text: str; ts: float; interrupted: bool = False
class SessionEventOut(BaseModel): id: int; ts: datetime; type: str; payload: dict
GET  /v1/sessions?agent_id=&status=      -> Page[SessionOut]
GET  /v1/sessions/{id}                   -> SessionDetailOut
GET  /v1/sessions/{id}/events?after_id=  -> Page[SessionEventOut]

# ---- internal (service token)
GET  /internal/v1/sessions/{id}/resolved -> ResolvedAgentConfig   (404 if unknown; 409 if status=ended; marks status=active, started_at)
class SessionEventIn(BaseModel): ts: float; type: str; payload: dict
POST /internal/v1/sessions/{id}/events   { events: list[SessionEventIn] } -> 202
class SessionSummaryIn(BaseModel): status: Literal["ended","failed"]; usage: dict; transcript: list[TranscriptTurn]; final_ui_state: UiState | None; error: str | None = None
PUT  /internal/v1/sessions/{id}/summary  SessionSummaryIn -> 204
class InternalKbSearchRequest(BaseModel): kb_ids: list[str]; query: str; k: int = 4
POST /internal/v1/kb/search              InternalKbSearchRequest -> KbSearchResponse

# ---- health (public)
GET  /v1/health                          -> { ok: bool; version: str; livekit_url: str; packs: list[str]; db: "ok"|"error" }
```

Event `type` values posted by the worker: `session_started`, `agent_state` (`{state}`), `user_turn` (`{text}`), `agent_turn` (`{text, interrupted}`), `tool_call_started` (`{call_id, tool, args_redacted}`), `tool_call_ended` (`{call_id, tool, status, duration_ms, result_preview}`), `workflow_run` (`{name, duration_ms, status}`), `ui_state` (`{seq}` only), `asset` (`{asset_id, kind, bytes}`), `escalation` (`{reason, urgency}`), `metrics` (`{kind, data}`), `error` (`{message}`), `info` (`{message}`), `session_ended` (`{reason}`).

**Emission rules (DECISIONS-W2 §D-W3-1).**
- Platform-owned types are emitted only by the worker: `session_started`, `agent_state`, `user_turn`, `agent_turn`, `tool_call_started/ended`, `workflow_run`, `metrics`, `error`, `info`, `session_ended`. A pack must not emit them.
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
