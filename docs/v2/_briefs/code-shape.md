I've gone through all six packages. Two things need your attention first:

- **The launch config holds real secrets in plain text.** There is no `.claude/` folder inside `livekit_agent_platform/`. The only `launch.json` is two levels up, at `~/work/.claude/launch.json`. Its `lkap-api` and `lkap-agent` entries contain inline `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` and `LKAP_MASTER_KEY` values. I've left them out of this report.
- **There is no CI config.** I found no `.github/` or other CI files anywhere in the tree.

## 1. Top-level layout
Root: `~/work/insurance_claim_live_agent_team/livekit_agent_platform/`

- **Folders:** `agent/`, `api/`, `contracts/`, `packs/`, `web/` (the packages), plus `deploy/`, `scripts/` (`dev.sh`, `export_contracts.sh`, `vendor_agent_deps.sh`), `docs/`, `README.md`, `.dockerignore`, `.gitignore`. There is no `packages/` folder.
- **Python setup:** this is not a uv workspace. There is no root `pyproject.toml`. Each Python package has its own `pyproject.toml`, `uv.lock` and `.venv`, built with hatchling, Python `>=3.12,<3.13`.
  - They link to each other through `[tool.uv.sources]` editable path deps (`../contracts`, `../packs`).
  - Distribution names: `lkap-contracts`, `lkap-api`, `lkap-agent`, `lkap-packs`.
  - Import names: `lkap_contracts`, `lkap_api`, `lkap_agent`, `packs`.
- **pnpm:** only `web/` uses it (`packageManager: pnpm@9.15.9`, `web/pnpm-lock.yaml`). There is no pnpm workspace file.
- **launch.json** (path above) has 5 configurations:
  - `stable-v1` (port 4177) and `live-agent-studio` (port 4180) are older, non-lkap apps.
  - `lkap-api` (port 8080): `uv run uvicorn lkap_api.main:app --reload`. Env: `LIVEKIT_URL/KEY/SECRET`, `LKAP_MASTER_KEY`, `LKAP_ADMIN_TOKEN=dev-admin`, `LKAP_SERVICE_TOKEN=dev-service`, `LKAP_AGENT_NAME=lkap-agent`.
  - `lkap-web` (port 3000): `pnpm dev`, with `NEXT_PUBLIC_API_BASE_URL=http://localhost:8080` and `LKAP_ADMIN_TOKEN`.
  - `lkap-agent` (no port): `uv run python -m lkap_agent.main dev`, with `LKAP_API_BASE_URL=http://127.0.0.1:8080` and `LKAP_PACKS=packs.insurance_claim,packs.generic`.
- **Dockerfiles:**
  - `agent/Dockerfile`: python:3.12-bookworm-slim, multi-stage, `STOPSIGNAL SIGINT`, CMD `python -m lkap_agent.main start`.
  - `api/Dockerfile`: uvicorn with `--workers 1`.
  - `web/Dockerfile`: node:24-alpine, standalone `server.js`.
- **Compose:** `deploy/docker-compose.yml` defines `api` and `web`.
  - `api` publishes ports 8080 and 3000, has a health check on `/v1/health`, and stores data in the `api-data` volume at `/data`.
  - `web` uses `network_mode: service:api` for local smoke tests.
  - The `agent` service is commented out because it deploys to LiveKit Cloud.
  - `deploy/` also has `api.env.example` and `web.env.example`.
- **`agent/livekit.toml`:** `[agent] name = "lkap-agent"`. `agent/` also contains a `vendor/` folder.

## 2. contracts (`contracts/src/lkap_contracts/`)
- **`agent_config.py`:**
  - Types: `PipelineMode` (realtime|cascaded) and `ProviderSlot` (realtime, stt, llm, tts, avatar, image_gen, workflow_llm).
  - Models: `ProviderRef`, `PipelineConfig`, `VoiceConfig`, `CapabilitiesConfig`, `ToolsConfig`, `KnowledgeConfig`, `AgentConfig`, `ResolvedProvider`, `ResolvedAgentConfig`.
- **`providers.py`** (the provider registry):
  - Models and types: `FieldSpec`, `ModelSpec`, `ProviderCapabilities`, `ProviderSpec`, `ProviderKind`.
  - Registry: `REGISTRY` (= `_MVP` + `_DEFERRED`), with helpers `get()`, `by_kind()`, `mvp_providers()`, `vision_support()`, and the list `GEMINI_LIVE_VOICES`.
  - MVP ids:
    - livekit-inference-stt, -llm and -tts
    - google-realtime, openai-realtime
    - deepgram-stt, openai-llm, google-llm
    - cartesia-tts, elevenlabs-tts, openai-tts
    - bey-avatar, tavus-avatar
    - google-image-gen, openai-image-gen
    - fastembed-embedding, openai-embedding
    - http-tool-secret
  - 25 providers are marked deferred (for example azure-openai-realtime, xai-realtime, assemblyai-stt, anthropic-llm, groq-llm, simli-avatar, anam-avatar).
- **`tools.py`:** `HttpToolDefinition`, `McpServerDefinition`, and `ToolDefinition` (a union of the two, keyed on `kind`), plus `TOOL_NAME_PATTERN`.
- **`packs.py`:** `KbSeed`, `ToolMeta`, `PackManifest`.
- **`ui_protocol.py`** (the UI channel protocol):
  - Constants: topics `lkap.ui.state`, `lkap.ui.activity`, `lkap.ui.asset`; RPC names `lkap.ui.request`, `lkap.agent.action`.
  - Models: `StatusStamp`, `Note`, `ChecklistItem`, `AssetRef`, `ActivityEvent`, `UiState`, `UiSnapshot`, `UiPatchOp`, `UiPatch`, `UiRequest`, `UiRequestResult`, `AgentAction`, `AgentActionResult`.
- **`dispatch.py`:** `DispatchMetadata` (ids only: session_id, agent_id, config_version, participant_identity).
- **`api_models.py`:**
  - Generic and error: `Page[T]`, `ErrorBody`, `ErrorResponse`, `HealthResponse`.
  - Providers and credentials: `ProvidersResponse`, `CredentialCreate/Update/Out/TestResult`.
  - Agents and connect: `AgentCreate/Update/Out/PublicOut`, `ValidationResult`, `ConnectRequest/Response`.
  - Tools: `ToolCreate/Out/DryRunRequest/DryRunResult`.
  - Knowledge: `KbCreate/Out/DocumentOut/SearchRequest/Hit/SearchResponse`, `InternalKbSearchRequest`.
  - Packs: `PackOut`, `PacksResponse`.
  - Sessions: `TranscriptTurn`, `SessionOut`, `SessionDetailOut`, `SessionEventOut/In`, `SessionEventsIn`, `SessionSummaryIn`.
- **`export.py`:** writes `contracts/generated/` (`providers.json`, `schemas/*.schema.json`, `ts/lkap-contracts.d.ts`). The TypeScript file is copied to `web/src/contracts/lkap-contracts.d.ts`.

## 3. api (`api/src/lkap_api/`)
- **Routers:** all in `routers/`, wired up in `main.py` `_include_routers`.

| File | Prefix | Endpoints |
|---|---|---|
| `agents.py` | `/v1/agents` | CRUD, `/{id}/validate` |
| `connect.py` | `/v1/agents` | `/{id_or_slug}/connect` |
| `credentials.py` | `/v1/credentials` | CRUD, `/{id}/test` |
| `tools.py` | `/v1/tools` | CRUD, `/{id}/dry-run` |
| `knowledge.py` | `/v1/knowledge-bases` (admin) | CRUD, documents, search |
| `knowledge.py` | `/internal/v1/kb` (internal) | `/search` |
| `providers.py` | `/v1/providers` | list, `/{provider_id}` |
| `sessions.py` | `/v1/sessions` | list, detail, `/events` |
| `internal.py` | `/internal/v1` | `sessions/{id}/resolved` (GET), `/events` (POST), `/summary` (PUT) |
| `health.py` | `/v1` | `/health` |
| `packs.py` (package root, not in `routers/`) | `/v1` | `GET /v1/packs` |

- **DB:** `db/models.py` (SQLAlchemy `DeclarativeBase`) and `db/session.py`. Tables: `agents`, `credentials`, `tools`, `knowledge_bases`, `kb_documents`, `kb_chunks`, `agent_knowledge_bases`, `sessions`, `session_events`.
- **Alembic:** 1 version, `alembic/versions/4135323c6ecc_initial_schema.py`. Latest revision is `4135323c6ecc` (no down revision).
- **Vault:** `vault.py` has class `Vault` (Fernet encryption keyed by `LKAP_MASTER_KEY`), plus `VaultError` and `fingerprint()`.
- **Settings:** `settings.py` (pydantic-settings, prefix `LKAP_`).
  - Unprefixed: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `PORT`.
  - Core: `LKAP_MASTER_KEY`, `LKAP_AGENT_NAME`, `LKAP_ADMIN_TOKEN`, `LKAP_SERVICE_TOKEN`, `LKAP_PACKS`.
  - Storage: `LKAP_DATA_DIR`, `LKAP_DATABASE_URL` (defaults to `sqlite+aiosqlite:///{data_dir}/lkap.db`).
  - Web and logging: `LKAP_CORS_ORIGINS`, `LKAP_PUBLIC_BASE_URL`, `LKAP_LOG_LEVEL`, `LKAP_LOG_JSON`.
  - Other: `LKAP_EMBEDDER`, `LKAP_BOOTSTRAP_CREDENTIALS_JSON`.
  - Session sweep: `LKAP_SESSION_SWEEP_INTERVAL_S`, `LKAP_SESSION_STALE_CREATED_S`, `LKAP_SESSION_STALE_ACTIVE_S`.
- **Other modules:** `auth.py`, `config_service.py`, `deps.py`, `errors.py`, `keys.py`, `livekit_tokens.py`, `sessions_sweep.py`, and `kb/` (`embed`, `ingest`, `search`, `seed`, `store`; uses lancedb and fastembed).

## 4. agent (`agent/src/lkap_agent/`)
- **Entrypoint:** `main.py`.
  - `server = AgentServer(setup_fnc=prewarm)`.
  - `@server.rtc_session(agent_name=REQUIRED_AGENT_NAME, on_request=only_lkap_jobs)` wraps `async def entrypoint(ctx)`, which calls `run_session(ctx, Deps.from_env(...))`.
  - `prewarm` loads `silero.VAD.load()` into `proc.userdata`.
  - `__main__` runs `require_agent_name(...)`, then `cli.run_app(server)`.
  - `only_lkap_jobs` rejects jobs whose `agent_name` is not `lkap-agent`.
  - `main.py` also defines `Deps`, `NoopUiChannel`, `NoopFrameBuffer`, `NoopBackgroundRunner` and `_IdleHangup`.
- **Provider factory:** `providers/factory.py` has `ProviderFactory`, `BuiltProviders`, `ProviderBuildError`, `CONSTRUCTIBLE_KINDS` (realtime, stt, llm, tts, avatar, image_gen) and `SLOT_KINDS`.
  - It is registry-driven: plugin classes are imported lazily from each registry entry's `python_class`, so there is no hardcoded plugin list.
  - Special cases: `google-realtime` (builds `google.genai` types), `livekit-inference-llm` (temperature handling), and every `livekit-inference-*` provider (no api_key/api_secret passed).
  - Plugins it can build today (the MVP registry classes):
    - `livekit.agents.inference` STT, LLM and TTS
    - `livekit.plugins.google`: `realtime.RealtimeModel` and `LLM`
    - `livekit.plugins.openai`: `realtime.RealtimeModel`, `LLM` and `TTS`
    - `livekit.plugins.deepgram.STT`, `cartesia.TTS`, `elevenlabs.TTS`
    - `livekit.plugins.bey.AvatarSession`, `tavus.AvatarSession`
    - Image generation is local: `providers/image_gen.py` (`GoogleImageGen`, `OpenAIImageGen`).
- **Key classes:**
  - `PlatformAgent` (and `SessionContext`): `platform_agent.py`
  - `UiChannel`: `ui/channel.py`
  - `FrameBuffer`: `vision.py`
  - `BackgroundToolRunner`: `tools/background.py`
  - Also: `session_builder.py` (`SessionBuilder`, `SessionPlan`), `config_client.py` (`ConfigClient`, `ApiKbClient`), `observability.py` (`SessionObserver`), `workflow_llm.py` (`PromptJsonStructuredLLM`), `packs/loader.py` (`PackLoader`, `NullPack`), `tools/declarative.py`, `tools/_http_safety.py`.
  - Built-in tools in `tools/builtin/`: current_time, describe_current_frame, end_call, escalate_to_human, http_request, pin_frame, push_note, search_knowledge, set_status.
- **Settings:** `settings.py`.
  - Unprefixed: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEKIT_AGENT_NAME`.
  - Prefixed: `LKAP_SERVICE_TOKEN`, `LKAP_API_BASE_URL`, `LKAP_PACKS`, `LKAP_HTTP_TOOL_ALLOWED_HOSTS`, `LKAP_LOG_LEVEL`, `LKAP_LOG_JSON`, `LKAP_VISION_MAX_FRAME_AGE_S`, `LKAP_IDLE_HANGUP_S` (default 120).
- **Dependencies:**
  - `livekit-agents[google,openai,deepgram,cartesia,elevenlabs,silero,bey,tavus,mcp]==1.8.2`
  - `livekit-plugins-google`, `-openai`, `-deepgram`, `-cartesia`, `-elevenlabs`, `-silero`, `-bey`, `-tavus`, all `==1.8.2`
  - `livekit==1.1.18`, `livekit-api==1.2.1`, `google-genai`, `openai>=2,<3` (a comment in the file notes this departs from CONTRACTS.md), `pillow`.
  - Dev dependencies include `respx`.

## 5. web (`web/`)
Next.js 15.5.18, React 19.1.1, Tailwind 4, `@livekit/components-react` 2.9.24, `livekit-client` 2.22.3, react-query, zod 4; tests use vitest and Playwright.

- **Routes (`src/app/`):**
  - `/` (`page.tsx`)
  - `(session)/s/[slug]`
  - `console/`: `agents/[id]`, `knowledge`, `knowledge/[id]`, `sessions`, `sessions/[id]`
  - `api/console/[...path]/route.ts` (a server-side proxy)
- **`src/components/shared/`:** `agent-state.ts`, `capability-badge.tsx`, `copy-button.tsx`, `description-list.tsx`, `empty-state.tsx`, `field.tsx`, `icon.tsx`, `index.ts` (barrel), `kbd.tsx`, `page-header.tsx`, `relative-time.tsx`, `responsive-table.tsx`, `section.tsx` (also exports `SectionRow`), `state-meter.tsx`, `status-chip.tsx`, `vendor-mark.tsx`.
  - The README documents StateMeter, StatusChip, Icon, EmptyState, PageHeader, Section/SectionRow, Field, DescriptionList, CopyButton, RelativeTime, VendorMark, CapabilityBadge, Kbd and ResponsiveTable.
  - Other component folders: `agents-ui`, `console`, `session`, `ui`.
- **Panel registry:** `web/src/panels/registry.ts`, exporting `PANELS`, `resolvePanel` (falls back to generic), `PanelDefinition` and `PanelProps`.
  - Registered ids: `generic` (`panels/generic/index.tsx`) and `insurance_notebook` (`panels/insurance_notebook/`, which contains `index`, `paper`, `studio`, `sketch-card`, `packet-dialog`, `state`, `notebook-styles`).
- **Lib and hooks:**
  - `src/lib/`: `api.ts` (`apiRequest<T>()` and an `api` object with get/post/put/patch/delete), `livekit.ts`, `livekit-server.ts`, `ui-state.ts`, `format.ts`, `theme.ts`, `utils.ts`.
  - `src/hooks/`: `useUiState`, `useUiRequests`, `useAgentRpc`, `useByteStream`.

## 6. packs (`packs/src/packs/`)
- **Pack interface:** `base.py` defines the `Pack` protocol:
  - Property: `manifest`.
  - Methods: `tools(ctx)`, `tool_meta()`, `initial_state()`, `on_session_start`, `on_user_turn_completed`, `on_agent_turn_completed`, `on_ui_action`, `on_session_end`.
  - It also defines the protocols packs depend on: `UiChannel`, `FrameBufferProto`, `KbClient`, `StructuredLLM`, `BackgroundRunner`, `ImageGen`, `PackSessionContext`.
- **Pack layout:** each pack folder has `__init__.py`, `manifest.py` (`MANIFEST: PackManifest`, pure Pydantic with no livekit import) and `pack.py` (`PACK: Pack`, imports livekit).
- **What a manifest declares:** `v`, `id`, `version`, `name`, `description`, `ui_panel_id`, `default_instructions`, `default_greeting`, `default_voice`, `recommended_pipeline`, `capabilities`, `builtin_tools_disabled`, `tool_names`, `state_schema`, `settings_schema`, `kb_seeds`, `instructions_by_mode`.
- **`generic`:** id `generic`, panel `generic`, uses the livekit-inference STT/LLM/TTS pipeline, `tool_names=[]`.
- **`insurance_claim`:**
  - id `insurance_claim`, panel `insurance_notebook`.
  - Pipeline: livekit-inference STT/LLM (vision model)/TTS plus `google-image-gen`.
  - Tools: `lookup_policy`, `sync_claim_packet`, `pin_evidence_photo`, `draw_incident_sketch`.
  - KB seeds: `seeds/policy_lines.md` and `seeds/intake_playbook.md`.
  - Other files: `instructions.py`, `prompts.py`, `rules.py`, `schemas.py`, `policy_directory.py`, `tools.py`, `ui_state.py`, `workflow.py`.

## 7. Tests
Counts come from `test_*.py` files, `def test_` functions, and the node ids in each package's `.pytest_cache`. The cached number includes parametrized cases and may be out of date.

| Package | Test files | `def test_` | Cached node ids |
|---|---|---|---|
| contracts | 4 | 67 | 610 |
| api | 20 | 207 | 233 |
| agent | 18 | 280 | 348 |
| packs | 10 | 148 | 194 |
| web | 19 vitest files (about 145 `it`/`test` cases) | — | — |

`web/e2e/` holds one Playwright entry.