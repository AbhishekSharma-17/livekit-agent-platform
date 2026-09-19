# LKAP (LiveKit Agent Platform) v1: technical brief for v2 planning

**Sources read in full:** `ARCHITECTURE.md` (365 lines), `CONTRACTS.md` (942 lines) and `DECISIONS-W2.md` (333 lines), all under `~/work/insurance_claim_live_agent_team/livekit_agent_platform/docs/`.

**Which document wins:** `DECISIONS-W2.md` > `CONTRACTS.md` > `ARCHITECTURE.md` > `INSURANCE_PACK_MAPPING.md` > `IMPLEMENTATION_PLAN.md` > code comments.

**Pinned SDK baseline:** `livekit-agents==1.8.2` (exact pin), `livekit-plugins-*==1.8.2`, `livekit-api==1.2.1`, `livekit==1.1.18`, `@livekit/components-react@2.9.24`, `livekit-client@2.22.3`, `next@15.5.18`, `react@19.1.1`. Python is `>=3.12,<3.13` with hatchling, uv, ruff (line length 110), `mypy --strict` and pytest `asyncio_mode=auto`. A pin bump must re-run the tripwire tests `test_sdk_default_text_input_cb_still_matches_our_assumptions` and `test_sdk_rtc_session_precedence_still_matches_our_assumptions`.

---

## 1. Package layout

The root is `livekit_agent_platform/`. Dependencies point one way: **`agent → packs → contracts`**. Packs never import `lkap_agent`.

### `contracts/` (Python package `lkap_contracts`)
Pure Pydantic, no I/O, depends only on `pydantic>=2.11,<3`. It ships `py.typed` (D-W2-3).
- `providers.py`
  - Models: `ProviderSpec`, `FieldSpec`, `ModelSpec`, `ProviderCapabilities`.
  - Module-level list `REGISTRY` with helpers `get(id)` and `by_kind(kind)`.
  - `vision_support(provider_id, model) -> bool | None` (D-W2-10).
- `agent_config.py`: `AgentConfig`, `PipelineConfig`, `ProviderRef`, `VoiceConfig`, `CapabilitiesConfig`, `ToolsConfig`, `KnowledgeConfig`, `ResolvedProvider`, `ResolvedAgentConfig`.
- `dispatch.py`: `DispatchMetadata`.
- `tools.py`: `HttpToolDefinition`, `McpServerDefinition`, `ToolDefinition` (discriminated on `kind`).
- `packs.py`: `PackManifest`, `KbSeed`.
- `ui_protocol.py`
  - Constants in `TOPICS`.
  - Models: `UiState`, `UiSnapshot`, `UiPatch`, `UiPatchOp`, `UiStateMessage`, `ActivityEvent`, `StatusStamp`, `Note`, `ChecklistItem`, `AssetRef`, `UiRequest`, `UiRequestResult`, `AgentAction`, `AgentActionResult`.
- `api_models.py`: request/response models (see §3).
- `export.py`
  - `python -m lkap_contracts.export` writes:
    - `generated/providers.json` (`{"v":1,"providers":[...]}`)
    - `generated/schemas/*.schema.json`
    - `generated/ts/lkap-contracts.d.ts`, produced by `json-schema-to-typescript` and copied to `web/src/contracts/` by `scripts/export_contracts.sh`.
  - `prepare_for_typescript` injects `tsType: "unknown"` into empty (`Any`) property schemas (D-W2-3).
  - A diff test fails if the generated files are stale.

### `agent/` (Python package `lkap_agent`, the LiveKit worker)
- `main.py`: `AgentServer`, the `rtc_session` entrypoint, `setup_fnc`/`prewarm`. Named internals:
  - `Deps.from_env`, `_wire_optional_modules`, `_assemble`, `run_session`
  - `_workflow_model(providers)`
  - `require_agent_name`, `effective_agent_name`, `only_lkap_jobs`
  - Constants `REQUIRED_AGENT_NAME` and `WORKFLOW_FALLBACK_LLM_MODEL` (gemma)
  - `speak_fixed_line`, `_on_set_video_source`
- `settings.py`: pydantic-settings.
- `config_client.py`: fetches the resolved config, posts events and the summary; also holds `ApiKbClient` (KB search).
- `providers/factory.py`: `ProviderFactory` (registry id → plugin object) and `_INFERENCE_FORBIDDEN_KWARGS`.
- `providers/image_gen.py`: `ImageGen` protocol, `GoogleImageGen`, `OpenAIImageGen`.
- `platform_agent.py`
  - `PlatformAgent(Agent)`: hooks, RAG injection (`_inject_knowledge`), vision injection (`_inject_vision`), `on_function_tools_executed`, `on_enter`.
  - `SessionContext` (a `@dataclass(slots=True)`).
  - `platform_text_input_cb`, `resolve_greeting_mode`.
- `session_builder.py`: `SessionBuilder.build` produces the `AgentSession`, `RoomOptions` and avatar.
- `vision.py`: `FrameBuffer`, `encode_jpeg`, `encode_jpeg_data_url(frame, max_px=512)`, `set_preferred_source`.
- `ui/channel.py`: `UiChannel` implementation (`UiChannelImpl`) covering state seq, patches, assets, activity and RPC; `_remote_identity()`.
- `tools/builtin/*.py`: nine built-in tools (see §7).
- `tools/declarative.py`: HTTP tools and MCP servers.
- `tools/background.py`: `BackgroundToolRunner`.
- `packs/loader.py`: imports packs from `LKAP_PACKS` and builds the concrete `PackSessionContext`.
- `observability.py`: structlog, `SessionObserver` (with `.record`).
- `workflow_llm.py`: `StructuredLLM` (prompt for JSON, Pydantic parse, one repair retry).
- Also: `livekit.toml` (`[agent] name = "lkap-agent"`), `Dockerfile`, `env.example`, and `tests/` with `fakes/`, `unit/` and `live/`.

### `api/` (Python package `lkap_api`, FastAPI)
- `main.py`, `settings.py`, `deps.py`, `auth.py`, `errors.py`, `keys.py` (`python -m lkap_api.keys generate`).
- `db/models.py`, `db/session.py`.
- `vault.py`: Fernet encrypt/decrypt.
- `livekit_tokens.py`: `mint_participant_token` with `RoomAgentDispatch`.
- KB modules:
  - `kb/ingest.py`
  - `kb/store.py`: `VectorStore` Protocol plus the LanceDB implementation.
  - `kb/embed.py`: `Embedder` Protocol, `FastEmbedEmbedder`, `OpenAIEmbedder`.
- `packs.py`: manifest discovery.
- `config_service.py`: `validate_agent_config`.
- `sessions_sweep.py`: `sweep_loop` (D-W2-2).
- `routers/`: `providers`, `credentials`, `agents`, `tools`, `knowledge`, `sessions`, `connect`, `internal`, `health`.
- Also: `alembic.ini`, `alembic/`.

### `packs/` (distribution `lkap-packs`, import root `packs`)
Depends only on `pydantic`, `livekit-agents==1.8.2` and `lkap-contracts`.
- `src/packs/base.py`: the `Pack` interface and runtime Protocols: `UiChannel`, `FrameBufferProto`, `KbClient`, `StructuredLLM`, `BackgroundRunner`, `ImageGen`, `PackSessionContext`, `ToolMeta`, `Pack`.
- `src/packs/generic/pack.py`: the empty pack.
- `src/packs/insurance_claim/`: `manifest.py` (pure Pydantic `MANIFEST`), `pack.py` (`PACK`), `instructions.py`, `schemas.py`, `rules.py`, `workflow.py`, `policy_directory.py`, `tools.py` (`render_and_patch`, `submit_workflow_run`), `ui_state.py` (`build_ui_state`), `seeds/`.
- Contract: every pack exposes `manifest.py` → `MANIFEST: PackManifest` (read by the api, no livekit import) and `pack.py` → `PACK: Pack` (read by the worker).

### `web/` (Next.js 15 App Router, React 19, Tailwind 4, shadcn plus vendored `@agents-ui`)
- Routes:
  - `src/app/(session)/s/[slug]/page.tsx`
  - `src/app/console/...`
  - `src/app/api/console/[...path]/route.ts`: admin proxy that forwards to `/v1/<path>` with `X-Admin-Token`.
- Components:
  - `src/components/agents-ui/` (vendored)
  - `src/components/session/`: `SessionView`, `ControlBar`, `Transcript`, `VideoStage`, `SessionExperience`, `LiveSession`, `PreCallCard`, `session-room.tsx`.
  - `src/components/console/`: `RegistryForm`, `CredentialPicker`, `ToolEditor`, `KbEditor`, `agents/agents-table.tsx`, `agents/agent-editor.tsx`, `registry/model-combobox.tsx`, `providers-tab.tsx`, `provider-slot-editor.tsx`, `panel-tab.tsx`, `ValidationBanner`.
- Libraries:
  - `src/lib/api.ts`
  - `src/lib/livekit.ts`: `fetchConnect`, `createConnectTokenSource`, `toPublicAgent`, `SessionAccess`.
  - `src/lib/livekit-server.ts`: `server-only`; `fetchAdminAgentServerSide`.
  - `src/lib/ui-state.ts`: the reducer.
- Hooks: `src/hooks/useUiState.ts`, `useByteStream.ts`, `useAgentRpc.ts`.
- Panels: `src/panels/registry.ts`, `src/panels/generic/`, `src/panels/insurance_notebook/`.
- Generated types: `src/contracts/`.
- Other directories: `deploy/docker-compose.yml`, `scripts/dev.sh`, `scripts/export_contracts.sh`.

---

## 2. Database entities and Alembic

SQLAlchemy 2 async. SQLite via `aiosqlite` by default; Postgres works through `LKAP_DATABASE_URL` (JSON columns use `sa.JSON`, no SQLite-only types). IDs are uuid4 hex.

| Table | Key fields |
|---|---|
| `agents` | `id` PK; `slug` UNIQUE; `name`; `description`; `pack_id` (default `'generic'`); `ui_panel_id` (default `'generic'`); `published` INT (0/1); `config` JSON (`AgentConfig` v1); `config_version` INT (default 1, bumped when the config changes); `created_at`; `updated_at` |
| `credentials` (the vault) | `id`; `provider_id` (registry id); `label`; `ciphertext` BLOB (`Fernet(json.dumps({field: value}))`); `fingerprint` ("…" + last 4 chars of the first secret field); timestamps |
| `tools` | `id`; `agent_id` nullable FK → `agents` ON DELETE CASCADE (NULL = shared); `kind` CHECK IN (`'http'`, `'mcp'`); `name`; `definition` JSON (`HttpToolDefinition` or `McpServerDefinition`); `enabled`; timestamps |
| `knowledge_bases` | `id`; `name`; `description`; `embedder_id` (default `'fastembed-embedding'`); `chunk_count`; timestamps |
| `kb_documents` | `id`; `kb_id` FK CASCADE; `filename`; `mime`; `bytes`; `status` CHECK IN (`pending`, `ready`, `failed`); `error`; `chunk_count`; `created_at` |
| `kb_chunks` | `id`; `kb_id`; `document_id` FK CASCADE; `ordinal`; `text`; `meta` JSON. The vector lives in the LanceDB table `kb_{kb_id}`, row id = chunk id. |
| `agent_knowledge_bases` | (`agent_id`, `kb_id`) composite PK, both CASCADE |
| `sessions` | `id`; `agent_id` FK; `config_version`; `room_name` UNIQUE (`lkap-{session_id[:8]}`); `participant_identity`; `participant_name`; `status` CHECK IN (`created`, `active`, `ended`, `failed`); `pipeline_mode`; `created_at`; `started_at`; `ended_at`; `usage` JSON; `transcript` JSON; `final_ui_state` JSON; `error` |
| `session_events` | `id` INTEGER autoincrement; `session_id` FK CASCADE; `ts`; `type`; `payload` JSON |

- **Indexes:** `ix_session_events_session(session_id, id)` and `ix_sessions_agent(agent_id, created_at)`.
- **There is no `packs` table.** Packs are discovered from code via `LKAP_PACKS`.
- **Storage:** SQLite at `LKAP_DATA_DIR/lkap.db`.
  - KB files are stored under `LKAP_DATA_DIR/kb/{kb_id}/`.
  - LanceDB lives at `LKAP_DATA_DIR/lancedb/`.
  - fastembed models are cached in `LKAP_DATA_DIR/models/` (`BAAI/bge-small-en-v1.5`, about 130 MB).
- **Alembic:** migrations in `api/alembic/`, config `api/alembic.ini`, applied with `uv run alembic upgrade head`.
- **Encryption:** Fernet with `LKAP_MASTER_KEY` (urlsafe base64, 32 bytes). Decryption happens only inside the internal resolve endpoint. KMS and key rotation are deferred.
- **Stale-session sweep (D-W2-2):** runs every `LKAP_SESSION_SWEEP_INTERVAL_S`.
  - A `created` row older than `LKAP_SESSION_STALE_CREATED_S` becomes `failed` with error `'never started'`.
  - An `active` row older than `LKAP_SESSION_STALE_ACTIVE_S` becomes `failed` with error `'summary never received'`.
  - A later summary from the worker still overwrites the row.

---

## 3. API endpoints and auth model

**Conventions:** JSON; list endpoints return `{items, total}` (`Page[T]`); errors are `ErrorResponse{error: ErrorBody{code, message, details}}` with status 400, 401, 403, 404, 409, 422 or 500. OpenAPI is served at `/docs`. Route prefixes are `/v1` and `/internal/v1`.

### Auth model (D14)
There is no user auth, RBAC or rate limiting in v1.
- **Admin:** static header `X-Admin-Token` = `LKAP_ADMIN_TOKEN` (dev value `dev-admin`).
  - The web server holds the token and proxies console calls through `/api/console/[...path]`.
  - The token never reaches the client bundle.
- **Worker:** static header `X-Service-Token` = `LKAP_SERVICE_TOKEN` (dev value `dev-service`).
- **Public access:** the per-agent `published` flag. Connect is allowed if the agent is published or the admin token is present.

### providers (admin)
- `GET /v1/providers` → `ProvidersResponse{v, providers}`
- `GET /v1/providers/{provider_id}` → `ProviderSpec`

### credentials (admin)
- `POST /v1/credentials` (`CredentialCreate{provider_id, label, secrets}`) → `CredentialOut` (201)
- `GET /v1/credentials?provider_id=`
- `PUT /v1/credentials/{id}` (omitting secrets keeps the existing ones)
- `DELETE /v1/credentials/{id}` → 204, or 409 if an agent references it
- `POST /v1/credentials/{id}/test` → `CredentialTestResult{ok, message}`
- `CredentialOut` returns only the `fingerprint`, never the secrets.

### agents
- `POST /v1/agents` (`AgentCreate`; `config=None` means use the pack defaults) → `AgentOut`
- `GET /v1/agents`
- `GET /v1/agents/{id_or_slug}` → `AgentOut` for admin, or `AgentPublicOut` with no token (published agents only)
- `PUT /v1/agents/{id}` (`AgentUpdate`; bumps `config_version`)
- `DELETE /v1/agents/{id}`
- `POST /v1/agents/{id}/validate` → `ValidationResult{ok, errors, warnings}`
- Mentioned only in ARCHITECTURE §11, with no model in CONTRACTS: `seed-from-pack`, `publish`, `test-connect`. `test-connect` is superseded by D-W2-1. The seeding rule is described in CONTRACTS §8.

### connect (public if published, or admin)
- `POST /v1/agents/{id_or_slug}/connect`
  - Request `ConnectRequest{participant_name="Guest", participant_identity?, participant_metadata}`.
  - Response `ConnectResponse{serverUrl, participantToken, roomName, participantName, sessionId, agent: AgentPublicOut, uiPanelId, protocolVersion=1}`.

### tools (admin)
- `POST/GET/PUT/DELETE /v1/tools[/{id}]` (list filters: `agent_id`, `kind`)
- `POST /v1/tools/{id}/dry-run` → `{ok, result, status_code, duration_ms}` (HTTP tools only)

### knowledge-bases (admin)
- `POST/GET/PUT/DELETE /v1/knowledge-bases[/{id}]` (`KbCreate`, `KbOut`)
- `POST /v1/knowledge-bases/{id}/documents` (multipart) → `KbDocumentOut` (202; ingestion runs in FastAPI `BackgroundTasks`)
- `GET /v1/knowledge-bases/{id}/documents`
- `DELETE /v1/knowledge-bases/{id}/documents/{doc_id}`
- `POST /v1/knowledge-bases/{id}/search`: `KbSearchRequest{query, k}` → `KbSearchResponse{hits: KbHit[chunk_id, document_id, filename, score, text]}`

### packs (admin)
- `GET /v1/packs` → `{items: PackOut{manifest}}`

### sessions (admin)
- `GET /v1/sessions?agent_id=&status=` → `Page[SessionOut]`
- `GET /v1/sessions/{id}` → `SessionDetailOut` (adds `transcript: TranscriptTurn[]` and `final_ui_state`)
- `GET /v1/sessions/{id}/events?after_id=` → `Page[SessionEventOut]`

### internal (service token)
- `GET /internal/v1/sessions/{id}/resolved` → `ResolvedAgentConfig`
  - 404 if the session is unknown, 409 if it is ended or failed.
  - Marks the session `active` and sets `started_at`.
- `POST /internal/v1/sessions/{id}/events` (`{events: SessionEventIn[ts, type, payload]}`) → 202
- `PUT /internal/v1/sessions/{id}/summary` (`SessionSummaryIn{status: ended|failed, usage, transcript, final_ui_state, error}`) → 204
- `POST /internal/v1/kb/search` (`InternalKbSearchRequest{kb_ids, query, k}`) → `KbSearchResponse`

### health (public)
- `GET /v1/health` → `{ok, version, livekit_url, packs, db}`

### Event `type` values
- `session_started`
- `agent_state`
- `user_turn`
- `agent_turn`
- `tool_call_started`
- `tool_call_ended`
- `workflow_run`
- `ui_state` (payload is `{seq}` only)
- `asset`
- `escalation`
- `metrics`
- `error`
- `info`
- `session_ended`

Packs may emit only `escalation` and `info`, or custom types prefixed with the pack id, via `ctx.record_event` (D-W3-1).

### Save-time validation rules
- The provider id must exist and match the slot kind.
- `credential_id` must be present if and only if the provider requires a credential, and its provider must match.
- A model id not in the suggestion list produces a warning, not an error.
- A realtime model without `video_input` combined with camera or screen share produces a warning.
- A cascaded LLM with `vision_support` False combined with camera or screen share produces a warning (D-W2-10).

---

## 4. Provider registry and agent config

### Types
- `ProviderKind` = `realtime | stt | llm | tts | avatar | image_gen | embedding | secret_bag`
- `FieldType` = `string | secret | number | boolean | enum | json | model`

### Models
- **`FieldSpec`:** `name` (constructor kwarg; dots allowed for nested configs), `label`, `type`, `required`, `default`, `options`, `placeholder`, `help`, `condition` (e.g. `"vertexai=true"`), `env_fallback` (informational only).
- **`ModelSpec`:** `id`, `label`, `supports_video`, `note`. Since D-W2-10, `supports_video` means "accepts visual input" and is set only after the platform has verified it.
- **`ProviderCapabilities`:** `video_input`, `tool_calling=True`, `silent_tool_reply`, `voices`.
- **`ProviderSpec`:** `v=1`, `id`, `kind`, `label`, `vendor`, `status` (`mvp` or `deferred`), `package`, `python_class` (dotted path), `requires_credential=True`, `secret_fields` (encrypted), `fields` (stored in `AgentConfig`), `models` (a suggestion list, not an allowlist), `default_model`, `capabilities`, `docs_url`, `get_key_url`.

### MVP entries

| id | python_class | Notes |
|---|---|---|
| `livekit-inference-stt` | `inference.STT` | Default model `deepgram/nova-3` |
| `livekit-inference-llm` | `inference.LLM` | Default model `google/gemma-4-31b-it` (text-only); `google/gemini-3.5-flash` has `supports_video=True` |
| `livekit-inference-tts` | `inference.TTS` | Default model `inworld/inworld-tts-2`, voice `Ashley` |
| `google-realtime` | `livekit.plugins.google.realtime.RealtimeModel` | Fields `voice`=Kore, `temperature`, `tool_behavior`=NON_BLOCKING, `tool_response_scheduling`=WHEN_IDLE, `enable_affective_dialog`. Default model `gemini-3.8-live`. Capabilities `video_input` and `silent_tool_reply`. |
| `openai-realtime` | OpenAI realtime plugin | Model `gpt-realtime`, voice `marin` |
| `deepgram-stt` | Deepgram plugin | |
| `openai-llm` | OpenAI plugin | |
| `google-llm` | Google plugin | |
| `cartesia-tts` | Cartesia plugin | |
| `elevenlabs-tts` | ElevenLabs plugin | |
| `openai-tts` | OpenAI plugin | |
| `bey-avatar` | `bey.AvatarSession` | Field `avatar_id` |
| `tavus-avatar` | `tavus.AvatarSession` | Fields `face_id`, `pal_id` |
| `google-image-gen` | `lkap_agent.providers.image_gen.GoogleImageGen` | Model `gemini-3.1-flash-image` |
| `openai-image-gen` | `lkap_agent.providers.image_gen.OpenAIImageGen` | Model `gpt-image-1` |
| `fastembed-embedding` | `lkap_api.kb.embed.FastEmbedEmbedder` | |
| `openai-embedding` | `lkap_api.kb.embed.OpenAIEmbedder` | Model `text-embedding-3-small` |
| `http-tool-secret` | — | `secret_bag` kind for HTTP/MCP tool secrets |

- The three `livekit-inference-*` entries have `requires_credential=false`.
- About 25 entries are `status="deferred"` (e.g. azure/xai realtime, anthropic/groq LLMs, simli/anam/bithuman avatars). The console hides them with a `status === "mvp"` filter.

### Factory rules (`ProviderFactory`)
- Vendor credentials are passed only as explicit constructor kwargs from `ResolvedProvider.kwargs`. The factory never sets environment variables.
- Inference classes are constructed **without** `api_key`/`api_secret`; `_INFERENCE_FORBIDDEN_KWARGS` strips them, and the SDK reads `LIVEKIT_API_KEY`/`SECRET` from the environment (D-W2-6).
- Plugin imports are lazy.
- `inference.TurnDetector()` is built per session by `Deps.turn_detector_factory`, **in cascaded mode only**. Realtime sessions get no turn detector and no VAD (D-W2-9o). Silero is a dependency, but the docs name no VAD field.

### Agent config models
- **`ProviderRef`:** `provider_id`, `credential_id?`, `model?` (None → `default_model`), `fields` dict.
- **`PipelineConfig`:**
  - `mode` (`realtime` or `cascaded`, default `cascaded`)
  - `realtime` (required in realtime mode)
  - `stt`, `llm`, `tts` (required in cascaded mode)
  - `avatar`, `image_gen`
  - `workflow_llm` (None → `llm` in cascaded mode, or the Inference LLM default in realtime mode)
  - `turn_handling: dict` (passed to `TurnHandlingOptions`, validated keys only)
- **`VoiceConfig`:** `greeting`, `greeting_mode` (`say` or `generate`), `language`, `allow_interruptions`, `user_away_timeout_s=15.0`.
- **`CapabilitiesConfig`:** `camera`, `screen_share`, `chat_input=True`, `vision_inject_per_turn=True`.
- **`ToolsConfig`:** `builtin_disabled`, `http_request_enabled`, `tool_ids`, `max_tool_steps=3`.
- **`KnowledgeConfig`:** `kb_ids`, `auto_inject=True`, `top_k=4`.
- **`AgentConfig`:** `v`, `instructions`, `pipeline`, `voice`, `capabilities`, `tools`, `knowledge`, `pack_settings`, `timezone="UTC"`.
- **`ResolvedProvider`:** `provider_id`, `python_class`, `model`, `kwargs` (secret and non-secret fields merged).
- **`ResolvedAgentConfig`:** `session_id`, `agent_id`, `agent_slug`, `config_version`, `pack_id`, `ui_panel_id`, `config`, `resolved` (dict keyed by `realtime|stt|llm|tts|avatar|image_gen|workflow_llm`), `tools: ToolDefinition[]`, `kb_ids`, `participant_identity`. It contains secrets and is never logged.

### Pack seeding rule
- Start from `manifest.recommended_pipeline`.
- A slot with no available credential is replaced by the Inference default of the same kind.
- A realtime slot with no credential switches the mode to `cascaded`.
- Avatar and image-gen slots with no credential are set to null.

---

## 5. UiChannel protocol, envelopes and panels

### Topics and methods (`TOPICS`)

| Constant | Value | Primitive | Direction |
|---|---|---|---|
| `TOPIC_UI_STATE` | `lkap.ui.state` | text stream | agent → UI |
| `TOPIC_UI_ACTIVITY` | `lkap.ui.activity` | text stream | agent → UI |
| `TOPIC_UI_ASSET` | `lkap.ui.asset` | byte stream | agent → UI |
| `RPC_UI_REQUEST` | `lkap.ui.request` | RPC | agent → UI |
| `RPC_AGENT_ACTION` | `lkap.agent.action` | RPC | UI → agent |

Reserved LiveKit channels are left untouched: `lk.transcription`, `lk.chat`, and the `lk.agent.state` participant attribute.

### Envelope
- **`UiState`:** `v`, `status: StatusStamp{label, tone, key}`, `progress` (0–100), `notes: Note[]`, `checklist: ChecklistItem[]`, `assets: AssetRef[]`, `activity: ActivityEvent[]` (last 30), `custom` (pack-defined, validated by `PackManifest.state_schema`).
- **`Tone`:** `neutral | info | success | warning | danger`.
- **`UiSnapshot`:** `{type:"snapshot", seq, session_id, state}`.
- **`UiPatch`:** `{type:"patch", seq, session_id, ops: UiPatchOp[]}`.
  - Each op is `{op: set|append|remove|upsert, path, value, key}`. `path` is JSON-pointer style; `upsert` matches list items by `key` or `id`.
- **`UiStateMessage`:** discriminated on `type`.
- **`ActivityEvent`:** `id` (a call or job id; a repeated id replaces the earlier entry), `ts`, `source`, `label`, `phase` (`running | done | error | cancelled`), `headline`, `urgent`, `duration_ms`, `detail`.
- **Asset byte stream:** attributes `asset_id`, `kind`, `mime`, `caption?`, `session_id`; name `{asset_id}.{ext}`. The `AssetRef` is patched in only after the stream completes.
- **`UiRequest`:** `method` is one of `open_dialog | focus | request_video_source | toast`.
- **`AgentAction`:** `action` is one of `get_snapshot | set_video_source | ui_action`.
  - `set_video_source` payload: `{source: camera|screen|none}`.
  - `ui_action` payload: `{name, data}` → `Pack.on_ui_action`.
  - Results come back as `UiRequestResult` and `AgentActionResult{ok, payload, error}`.
- **Ordering:**
  - `seq` starts at 1 with a snapshot that the platform sends after `pack.on_session_start` (D-W2-9a). A snapshot reuses the current seq; only 0 → 1 bumps it.
  - The UI reducer applies a message only when `seq == last+1`. On a gap it calls `get_snapshot`.
  - The agent re-sends a snapshot on RPC request and every 50 patches.

### `UiChannel` Protocol (in `packs.base`)
- Attributes: `seq`, `state`.
- Methods:
  - `patch(ops)`
  - `snapshot()`
  - `set_status(label, tone)`
  - `add_note(text, kind, key)`
  - `set_checklist(items)`
  - `push_asset(data, mime, kind, caption, meta) -> asset_id`
  - `activity(event)`
  - `request_ui(method, payload)`
- The worker constructs `UiChannelImpl(ui_identity=resolved.participant_identity)`. The fallback identity resolution skips participants that carry `lk.publish_on_behalf` or are of kind AGENT (D-W2-7).

### Web hooks
- `useUiState(sessionId)` → `{state, seq, assets: Map<asset_id, objectURL>, connected}`
- `useByteStream(topic, onAsset)`: wraps `room.registerByteStreamHandler` (there is no React hook for byte streams).
- `useAgentRpc()` → `perform(AgentAction)`. It targets the remote participant of kind Agent that does not carry `lk.publish_on_behalf`.
- `useUiRequests(handler)`
- Transcript comes from `useSessionMessages()`; agent state from `useVoiceAssistant().state`; avatar video from `useVoiceAssistant().videoTrack`.

### Panel registry (`web/src/panels/registry.ts`)
- **`PanelProps`:** `state`, `assets`, `agent: AgentPublicOut`, `sessionId`, `perform`, `transcript: ReceivedMessage[]`, `connectionState`.
- **`PanelDefinition`:** `{id, title, Component, layout?: "side" | "wide"}`.
- `PANELS` contains `generic` and `insurance_notebook`. `resolvePanel(id)` falls back to `generic`.
- Panels are pure renderers and emit intents only through `perform`.
- **How a pack gets a panel:** the manifest's `ui_panel_id` is the registry key. The panel code is added by hand under `web/src/panels/<id>/` and registered statically in `registry.ts`. There is no dynamic registration.
- The generic panel renders `status`, `progress`, `notes`, `checklist`, an assets grid, the activity feed, and a collapsible JSON view of `custom`.

---

## 6. Worker start, registration and session flow

### Registration (D-W2-11)
- The worker registers with `@server.rtc_session(agent_name=REQUIRED_AGENT_NAME ("lkap-agent"), on_request=only_lkap_jobs)`.
- `only_lkap_jobs` rejects any `JobRequest` whose `agent_name != "lkap-agent"`, including automatic-dispatch jobs, which carry `""`.
- The SDK resolves the name in this order: `LIVEKIT_AGENT_NAME_OVERRIDE` → explicit argument → `LIVEKIT_AGENT_NAME` → `""`.
- `require_agent_name` refuses to start if any source that *is set* disagrees with `lkap-agent`.
- Dispatch is explicit only. The existing `other-project-agent` agent is unaffected.
- The API side sets the dispatch target from `LKAP_AGENT_NAME`, which must equal the worker's name.

### Run commands
- Dev: `uv run python -m lkap_agent.main dev`. There is no hot reload; config needs no restart because it is fetched per job.
- Deploy: `start` mode on LiveKit Cloud via `lk agent create --secrets-file secrets.env`, then `lk agent deploy`.
- Stop policy (D-W2-13): send SIGINT, wait at least 15 s in dev or `drain_timeout` in start mode, then SIGKILL. The Dockerfile sets `STOPSIGNAL SIGINT`.

### Session start flow
1. **Browser → api:** `POST /v1/agents/{slug}/connect`. In test mode (`/s/[slug]?mode=test`, D-W2-1) the request goes through `/api/console/agents/{slug}/connect`, where the proxy adds the admin token.
2. **api:**
   - Checks that the agent is published or the admin token is present.
   - Creates a `sessions` row (`status=created`, `room_name=lkap-{id[:8]}`).
   - Mints a 2 h token with the grants join, publish, subscribe and publish-data, plus `RoomConfiguration(agents=[RoomAgentDispatch(agent_name, metadata=DispatchMetadata.json())])`. Any client-supplied `roomConfig` is ignored.
   - `DispatchMetadata` carries IDs only: `v`, `session_id`, `agent_id`, `config_version`, `participant_identity`.
3. **Browser:** `useSession(TokenSource.custom(fetchConnect))` connects and publishes the mic. The token source freezes after the first successful connect (D-W2-2a, D-W3-2).
4. **LiveKit:** dispatches the job to the worker, which runs `only_lkap_jobs` and then `accept()`.
5. **Worker start order (D-W2-9b):**
   - Resolve with `GET /internal/v1/sessions/{id}/resolved` (`X-Service-Token`).
   - Build: `ProviderFactory`, `PackRuntime`/loader, tools, KB client, `SessionBuilder.build`.
   - `ctx.connect()`.
   - `UiChannel.start()` and `FrameBuffer.start()`.
   - `avatar.start()` + `wait_for_join()`.
   - `session.start(room_options=RoomOptions(participant_identity=…, video_input=realtime, close_on_disconnect=True, text_input=…))`.
   - `PlatformAgent.on_enter`: `custom = pack.initial_state`, `await pack.on_session_start`, `ui.snapshot()`.
   - Greeting via `resolve_greeting_mode`: `say()` only if a TTS exists, otherwise `generate_reply(instructions=…)`.
   - Post the `session_started` event.
   - If the resolve call fails, the worker connects, speaks a fixed line through Inference TTS (`speak_fixed_line`) and shuts down.
6. **End of call:**
   - `session.on("close")` → `ctx.shutdown()` (D-W2-9e). The shutdown callback PUTs `/summary` with usage (from `session_usage_updated`), transcript (from `session.history`, images stripped) and `final_ui_state`. The target is at most 10 s after hangup.
   - Idle hangup after `LKAP_IDLE_HANGUP_S`.

---

## 7. Tools model

Tools are passed as `PlatformAgent(tools=[*builtin, *declarative, *pack_tools], mcp_servers=[...])`.

### Built-in tools (`tools/builtin/`)
Individual tools can be disabled via `tools.builtin_disabled`.
- **`end_call`**
- **`search_knowledge(query)`:** searches the agent's attached KBs with `k=top_k`. The `kb` parameter was removed (D-W2-12).
- **`http_request(method, url, body?)`:** only when `http_request_enabled`; host allowlist enforced; 10 s timeout.
- **`describe_current_frame(question?)`:**
  - Cascaded mode: sends the latest frame to the LLM. It raises `ToolError` on a known text-only model.
  - Realtime mode: returns only the frame metadata.
- **`pin_frame(caption, confirmed?, kind?)`:** uses a frame at most 12 s old; returns `{pinned, asset_id}`.
- **`push_note(text, kind?)`**
- **`set_status(label, tone)`**
- **`escalate_to_human(reason, urgency)`:** a stub that emits `escalation`.
- **`current_time()`**

### HTTP tools (`kind=http`)
- **`HttpToolDefinition`:**
  - `name` (regex `^[a-zA-Z_][a-zA-Z0-9_]{0,63}$`), `description`, `parameters` (JSON Schema)
  - `method` (default POST)
  - `url` template (`{{ arg }}`)
  - `headers` (may use `{{ secret.NAME }}`)
  - `credential_id` (points to an `http-tool-secret` credential)
  - `body_template`, `allowed_hosts`, `timeout_s=10`, `max_result_chars=4000`, `result_path` (JSON pointer), `silent_reply`
- The api substitutes secrets when it resolves the config, so the worker never sees the vault.
- Built with `function_tool(handler, raw_schema={name, description, parameters})`, where the handler signature is `(raw_arguments, context: RunContext)` and it calls `httpx`.
- Host allowlist: the per-tool `allowed_hosts` plus `LKAP_HTTP_TOOL_ALLOWED_HOSTS`.

### MCP tools (`kind=mcp`)
- **`McpServerDefinition`:** `name`, `url`, `headers`, `credential_id`, `allowed_tools`, `timeout_s=5`, `sse_read_timeout_s=300`.
- Built as `mcp.MCPServerHTTP(..., transport_type="streamable_http")`. This requires the `livekit-agents[mcp]` extra (D-W2-9g).
- Stdio MCP servers are deferred.

### Pack code tools
- `Pack.tools(ctx)` returns `@function_tool` closures.
- `Pack.tool_meta()` returns `ToolMeta{name, silent_reply, activity_label}`.

### `BackgroundToolRunner` (`tools/background.py`)
- Implements the `BackgroundRunner` Protocol: `submit(name, coro, on_result, urgent, urgent_instructions, routine_note, call_id) -> job_id` and `cancel`.
- The tool returns quickly:
  - Realtime mode: returns `None`, so `reply_required=False` and the model stays silent.
  - Cascaded mode: returns a short acknowledgement string.
- When the job finishes:
  - The UI receives activity and a state patch.
  - Routine results go into the chat context with `update_chat_ctx`.
  - Urgent results call `generate_reply(instructions=…, allow_interruptions=False)`.
- Tools are flagged `ToolFlag.CANCELLABLE`: a user interruption cancels the tool's reply, not the background job.
- Silent replies: in realtime mode, the `on_function_tools_executed` handler calls `cancel_tool_reply()` for tools whose `ToolMeta.silent_reply` is true (D-W2-9i).
- Background jobs are logged as `workflow_run` events.

---

## 8. Environment variables

### api
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` (required)
- `LKAP_MASTER_KEY` (required)
- `LKAP_AGENT_NAME` (required; `lkap-agent`)
- `LKAP_ADMIN_TOKEN` (required)
- `LKAP_SERVICE_TOKEN` (required)
- `LKAP_DATA_DIR` (required; default `./data`)
- `LKAP_DATABASE_URL` (optional; default `sqlite+aiosqlite:///{DATA_DIR}/lkap.db`)
- `LKAP_CORS_ORIGINS` (optional; default `http://localhost:3000`)
- `LKAP_PUBLIC_BASE_URL` (optional; unused in the MVP)
- `LKAP_PACKS` (optional)
- `LKAP_LOG_LEVEL`, `LKAP_LOG_JSON` (optional)
- `LKAP_EMBEDDER` (`fastembed`, or `openai:<credential_id>`)
- `LKAP_SESSION_SWEEP_INTERVAL_S` (60), `LKAP_SESSION_STALE_CREATED_S` (600), `LKAP_SESSION_STALE_ACTIVE_S` (21600)
- `LKAP_BOOTSTRAP_CREDENTIALS_JSON` (dev credential seeding)
- `PORT` (8080)

### agent
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` (required)
- `LIVEKIT_AGENT_NAME` (optional; must equal `lkap-agent` if set)
- `LIVEKIT_AGENT_NAME_OVERRIDE` (same rule)
- `LKAP_SERVICE_TOKEN` (required)
- `LKAP_API_BASE_URL` (required)
- `LKAP_PACKS` (default `packs.insurance_claim,packs.generic`)
- `LKAP_HTTP_TOOL_ALLOWED_HOSTS`
- `LKAP_LOG_LEVEL`, `LKAP_LOG_JSON`
- `LKAP_VISION_MAX_FRAME_AGE_S` (8)
- `LKAP_IDLE_HANGUP_S` (120)

### web
- `NEXT_PUBLIC_API_BASE_URL` (required)
- `LKAP_ADMIN_TOKEN` (server-only)
- `PORT` (3000)

Vendor keys (`GOOGLE_API_KEY`, `OPENAI_API_KEY`, …) are deliberately **not** read from the environment; they live in the vault.

---

## 9. Where DECISIONS-W2 overrides ARCHITECTURE and CONTRACTS

- **D-W2-1:** test calls use `?mode=test` through the console proxy. This replaces the `test-connect` endpoint and the admin-cookie approach (ARCH §11, §12).
- **D-W2-2 (with D-W3-2):** the connect token source freezes after the first connect, plus the api-side stale-session sweep and its three `LKAP_SESSION_*` variables (CONTRACTS §3).
- **D-W2-3:** `tsType: "unknown"` for `Any` fields in generated TS; `py.typed` added; KB `k` validated as `Field(4, ge=1, le=20)` (CONTRACTS §4, §7).
- **D-W2-4 / 9j:** the frame source is the one the UI selected, otherwise the freshest frame. This replaces "screen share if published, else camera" (ARCH §8).
- **D-W2-5 / 9m:** an explicit `k` wins; platform callers pass `top_k`; `ApiKbClient` loses `default_k` (ARCH §7.4).
- **D-W2-6:** all Inference objects read LiveKit credentials from the environment.
- **D-W2-7:** UI RPC targets the identity the api minted; avatar (`lk.publish_on_behalf`) participants are excluded on both sides.
- **D-W2-8 / 9k:** cascaded vision uses a JPEG data URL of at most 512 px, `inference_detail="low"`, at most one image per LLM call, and auto-disables after an LLM error. This replaces raw `ImageContent(frame)` (ARCH §8).
- **D-W2-9a:** the platform sends the seq-1 snapshot after the pack's `on_session_start` (CONTRACTS §10).
- **D-W2-9b:** the start order is resolve → build → `connect` → UI/frames start → avatar → `session.start` (ARCH §4).
- **D-W2-9c:** usage comes from `session_usage_updated` and tool timing from `tool_execution_updated`. `metrics_collected` and `UsageCollector` are unused (ARCH §14).
- **D-W2-9d:** `say()` requires a TTS; realtime mode without a TTS greets via `generate_reply` (ARCH §15.9).
- **D-W2-9e:** a session `close` event triggers `ctx.shutdown`.
- **D-W2-9f → D-W2-11:** the agent name is fixed in code with a job-request filter; `LIVEKIT_AGENT_NAME` becomes optional (CONTRACTS §3).
- **D-W2-9g:** dependency changes: `openai>=2,<3` and the `mcp` extra (CONTRACTS §2).
- **D-W2-9h:** the asset attribute is `caption`, not `caption_ref` (ARCH §9).
- **D-W2-9i:** silent tool replies go through `cancel_tool_reply` in `function_tools_executed`, in realtime mode only (ARCH §7.2).
- **D-W2-9o:** the turn detector is used in cascaded mode only; no VAD in realtime mode.
- **D-W2-9p:** typed chat goes through `platform_text_input_cb` and then `on_user_turn_completed`; `chat_input=false` sets `text_input=False` (ARCH §9).
  - Known gap: in realtime mode, per-turn context edits never reach the model.
- **D-W2-10:** vision capability is registry data (`vision_support`, tri-state) and gates per-turn injection and `describe_current_frame`. gemma stays the default; the insurance pack seeds `google/gemini-3.5-flash`.
- **D-W2-12:** `search_knowledge(query)` with no `kb` parameter (ARCH §7).
- **D-W2-13:** no hot reload in dev; stop with SIGINT and a grace period, never SIGKILL first.
- **D-W3-1:** `PackSessionContext.record_event(event_type, payload)` added; event-emission rules added (CONTRACTS §7, §8).

---

## Gaps and conflicts in the docs
- ARCHITECTURE §11 lists `seed-from-pack` and `publish` endpoints, but CONTRACTS §7 defines no path or models for them. Publishing is also possible through `AgentUpdate.published`.
- ARCH §4 writes the connect path as `/connect` on `{agent_id}`; CONTRACTS uses `{id_or_slug}`.
- The docs name no avatar fields beyond the `avatar` `ProviderRef` slot and the per-provider fields listed in §4.
- The docs name no VAD configuration field.