# LKAP v2 — Phased Delivery Plan

Status: **decided** (Fable 5.1, 2026-09-19). Owners: **Opus 5** for hard/critical, **Sonnet 5** for the rest. Fable reviews at the end of each wave and owns the final review package.

Naming: v2 packages are **`V2-nn`**. Do not confuse with REVIEW-FINAL's fix packages (WP-A..D, done) or UI_UX_SPEC's UI packages (WP-0..WP-12; WP-0 landed, WP-1..12 resume under §4 below).

Definitions used by every package:
- **Python gate** (run inside the package dir): `uv run ruff check . && uv run ruff format --check . && uv run mypy src/ --strict && uv run pytest -q -m "not live"`.
- **Web gate** (in `web/`): `pnpm lint && pnpm typecheck && pnpm test && pnpm build`.
- **Contracts gate**: `cd contracts && uv run python -m lkap_contracts.export && cd .. && scripts/export_contracts.sh && git diff --exit-code contracts/generated web/src/contracts` (the export diff test must pass).
- **Live gate**: the named `uv run pytest -m live …` commands from `../RUNBOOK.md` §5 plus the v2 stages in §6 of this document, run against `wss://your-project.livekit.cloud` with exactly one worker per connection and never touching `other-project-agent`.
- **Exclusive ownership**: a package may create/edit only the paths it lists; shared files (`providers.py`, `api_models.py`, `registry.ts`, `main.py` router wiring, alembic versions) are owned by exactly one package per wave. Anyone else needing a change files it as a one-line ask in `docs/v2/_asks.md` for the owner.

## 1. Phases

| Phase | Goal | Contents |
|---|---|---|
| **Phase 1 — MVP, production-grade** | The user's asks 1–4 delivered in full, on a production-ready base. Split into **core** (the definition of done) and **stretch** (ships if capacity allows; otherwise becomes Phase 2 wave 1 without failing Phase 1). | Core: contracts v2, tenancy + auth + API keys, connections + per-connection minting, supervisor (subprocess + docker), provider registry at full breadth + full worker image, credential tests + catalogs, cascaded/realtime/half-cascade + VAD/turn/NC slots, all avatars + pickers, panels v2 (blocks), recordings/cost/latency, durable webhooks, jobs + storage + Postgres + CI, connections/providers/avatars/panels/team/API-keys UI, live verification. Stretch: flow builder, telephony core, text test mode with rewind, widget. |
| **Phase 2** | Dograh parity and beyond | Campaigns (CSV, pacing, retries, schedules, circuit breaker), AMD/voicemail, warm transfer, simulated-caller evals + LLM judge + regression suites, MCP authoring server, headless widget, Langfuse/Datadog presets, automated `cloud_hosted` deploy, OIDC/SSO, whiteboard/map/chart blocks, `AgentTask` sub-flows, credential rotation UI, K8s backend for the supervisor, master-key rotation UI. |
| **Phase 3** | Scale and ecosystem | Multi-region connection routing, per-workspace price overrides + billing export, pack marketplace + scaffold CLI, Ingress (RTMP/WHIP) sources as panel video, translation (Palabra) pipelines, browser-tool plugin, on-prem avatar runtimes (bitHuman local), SOC2 controls. |

## 2. Phase 1 waves and packages (one line each)

| ID | Package | Owner | Wave | Depends on |
|---|---|---|---|---|
| V2-00 | Contracts v2: registry schema, AgentConfig v2 (modes, slots, panel, flow, qa, recording, limits), DispatchMetadata v2, UI protocol v2 (blocks, form RPC), FlowSpec, api models, pricing table, export | Opus | 0 | — |
| V2-01 | DB schema v2 + migrations from v1 (tenancy on every table, connections, workers, versions, sessions ext, webhooks, storage, api keys) + bootstrap of default workspace/owner/connection + Postgres CI matrix | Opus | 0 | V2-00 |
| V2-02 | Auth, tenancy scoping, roles, API keys, rate limits, connect limits (F-06/F-07/F-08/F-13), console proxy cookie forwarding | Opus | 1 | V2-01 |
| V2-03 | Connections service: CRUD, test + capability probes, per-connection token minting + LiveKitAPI factory, worker-env endpoint, deploy bundle generator, validation against flags | Opus | 1 | V2-01 |
| V2-04 | Supervisor service (`lkap_supervisor`): reconcile loop, subprocess + docker backends, drain/restart on rotation, Prometheus metrics, worker register/heartbeat api side | Opus | 1 | V2-03 |
| V2-05 | Provider registry at full breadth (~90 entries from the catalog), factory special cases (Simli nested config, Synthesia positional, Google creds, AWS flat keys, Azure overloads, half-cascade modalities), pricing entries | Sonnet | 1 | V2-00 |
| V2-06 | Credential test matrix + vendor catalog adapters (models/voices/avatars) + cache + workspace provider enablement | Sonnet | 1 | V2-01, V2-05 |
| V2-07 | Worker v2: pipeline modes (half-cascade), VAD/turn/NC slots, connection flags, avatar options, registration/heartbeat, `sessions/start` path, metrics_collected latency, recording hooks | Opus | 1 | V2-00, V2-05 |
| V2-08 | Jobs (`inline`/`arq`), storage (local/S3), webhook endpoints + durable delivery, connection probe + catalog refresh jobs, QA scoring job | Sonnet | 1 | V2-01 |
| V2-09 | Worker images `slim`/`full` + `check_installed_providers.py` + `installed_providers.json` + Docker builds for api/web/agent/supervisor + compose dev/prod + CI workflows | Sonnet | 1 | V2-05 |
| V2-10 | Panels v2 runtime (worker): `blocks` state, UiChannel block helpers, form RPC, built-in panel tools, pack `default_panel` | Opus | 2 | V2-00, V2-07 |
| V2-11 | Panels v2 web: composite panel, 12 block components, `<Block>` for custom panels, panel composer in the agent editor | Opus | 2 | V2-00, WP-0 |
| V2-12 | Recordings via Egress + storage config + playback, cost computation, session records v2 api (channel, cost, latency, qa, recording) | Sonnet | 2 | V2-03, V2-08 |
| V2-13 | Web: connections screens (list, create/edit, test, capabilities, fleet status, deploy bundle, worker env), providers catalog screen (enable/disable, keys, test, catalogs), avatar pickers, agent editor connection + mode (half-cascade) + VAD/turn/NC slots | Sonnet | 2 | V2-03, V2-06, WP-4 |
| V2-14 | Web: login, team/members/roles, API keys, webhooks, storage configs, settings; sessions detail v2 (recording player, cost, latency, QA) | Sonnet | 2 | V2-02, V2-12, WP-1, WP-7 |
| V2-15 | Flow runtime in the worker (`FlowNodeAgent`, edge tools, handoffs, variables, global node, disposition) — **stretch** | Opus | 3 | V2-07, V2-10 |
| V2-16 | Flow API: validation, `agent_config_versions` diff, node specs endpoint; flow builder UI (React Flow canvas, node forms from schema, version history) — **stretch** | Opus | 3 | V2-00, V2-15 |
| V2-17 | Telephony core: SIP trunks, dispatch rules, number→agent, outbound single call, DTMF, cold transfer; telephony UI — **stretch** | Opus | 3 | V2-03, V2-07 |
| V2-18 | Text test mode with rewind (worker + console) and widget (`widget.js`, `embed=1`, origins allowlist) — **stretch** | Sonnet | 3 | V2-02, V2-07, WP-8 |
| V2-19 | Integration: merge order, migration rehearsal on a copy of the live SQLite DB and on Postgres, `LKAP_PACKS` parity check, docs (RUNBOOK v2, CONTRACTS regen) | Opus | 4 | all core |
| V2-20 | Live verification on LiveKit Cloud: v1 stages 0–9 regression + v2 stages L1–L12 (§6), with the user's keys where supplied | Opus | 4 | V2-19 |
| V2-21 | Security + production review: threat model pass on tenancy/auth/SSRF/webhooks, secrets hygiene, load test of connect, final punch list | Opus (+Fable sign-off) | 4 | V2-20 |

UI packages WP-1..WP-12 from `../UI_UX_SPEC.md` resume in waves 1–2 per `UI_UX_SPEC-V2-AMENDMENTS.md` §3 (WP-0 landed; WP-1, WP-3, WP-4, WP-5, WP-7 change scope; the rest proceed unchanged).

Wave rule: a wave starts when every package it depends on is merged and green. Waves 1 and 2 are the wide parallel waves (8 and 5 packages); wave 3 is stretch and may run concurrently with wave 4's V2-19 if core is done.

## 3. Before-production map (REVIEW-FINAL §5 → package)

| REVIEW-FINAL item | Package |
|---|---|
| 1 Authenticate the web app (F-06) | V2-02, V2-14 |
| 2 Rotate static secrets, strength (F-08), master key rotation procedure | V2-02 (strength), V2-01 (`keys rotate`) |
| 3 Bound public connect (F-07) | V2-02 |
| 4 TLS/CORS/allowlist intersection + private-range deny (F-14), KB upload cap (F-29) | V2-02 (limits), V2-07 (`_http_safety` deny-list), V2-08 (upload cap) |
| 5 Verify what has never run (F-21/F-34) | V2-20 |
| 6 Postgres + backups | V2-01, V2-09 |
| 7 Credentials page (F-16), admin-only identities (F-13), unknown `{{ secret }}` rejected (F-15) | V2-13 (page), V2-02 (F-13), V2-06 (F-15) |
| 8 Reconnect grace (F-33), session delete / agent archive (F-22) | V2-07 (grace), V2-12 (archive/delete) |
| 9 Shared fakes (F-18), Playwright smoke (F-35) | V2-19 (fakes package), V2-14 (Playwright login→console→test call) |
| 10 Log shipping + alerts, OTel | V2-08 (structured logs), V2-07/V2-09 (OTel env + Prometheus) |

Sections 4–7 (package cards with ownership, acceptance criteria and verification commands; UI package deltas; live stages; risks) follow below.

---
## 4. Phase 1 package cards

Card format: **Owner · Wave · Depends** / Scope / Exclusive files / Acceptance (with exact commands) / Live verification. Paths are relative to `livekit_agent_platform/`. Every package also passes its package gate(s) and adds tests in the same package; test naming `test_<function>_<scenario>_<expected>`.

### V2-00 Contracts v2 — Opus · Wave 0 · depends: none
- **Scope.** Implement `CONTRACTS-V2.md` §4 in full: registry schema (`providers.py` types + `status` computed alias + helpers), `pricing.py` (empty `PRICES` list + lookup; entries come from V2-05), `agent_config.py` v2, `dispatch.py` v2, `ui_protocol.py` v2 (blocks, form RPC, new actions), `flow.py`, `fleet.py`, `connections.py` (`ConnectionCapabilities`, `ConnectionInfo`), `api_models.py` additions, `migrate.py` (`agent_config_v1_to_v2`, `v2_to_v1`), export of the new schemas (`FlowSpec`, node kinds, block states) and TS types. Update the two pack manifests to `v2` defaults (`default_panel`). Update every `status` consumer listed in CONTRACTS §4.1 so nothing breaks while the alias exists.
- **Exclusive files.** `contracts/**`, `packs/src/packs/*/manifest.py`, `scripts/export_contracts.sh`, `web/src/contracts/**` (generated), `docs/v2/_asks.md` (create).
- **Acceptance.** Contracts gate; `cd contracts && uv run pytest -q` ≥ 700 tests green including: v1→v2 migration round-trips both pack manifests and all v1 fixtures; `FlowSpec` validation rejects the six invalid shapes in §4.5; `UiState` v2 patch on `/blocks/x` applies in the reducer fixture; `ProviderSpec.status` alias equals the old value for every existing entry. `cd packs && uv run pytest -q` still green (189 + xfails). `cd web && pnpm typecheck` green with regenerated types.
- **Live.** None.

### V2-01 DB schema v2 + migrations + bootstrap — Opus · Wave 0 · depends: V2-00
- **Scope.** `db/models.py` for every table in CONTRACTS §1; Alembic revisions `v2_001`…`v2_008` per §2 with `downgrade()`; `lkap_api.bootstrap` (default workspace, owner, default connection from env, idempotent) run at startup and as `python -m lkap_api.bootstrap`; `python -m lkap_api.keys rotate --old --new` re-encrypting `credentials`, `livekit_connections`, `storage_configs`, `webhook_endpoints`, `sip_trunks`; Postgres support verified (`asyncpg`), `LKAP_ENV`; a `conftest` fixture that runs the api test suite against Postgres when `LKAP_TEST_DATABASE_URL` is set; the "no unscoped tenant query" test guard (`before_compile` listener active in tests). **Router stubs**: create empty `api/src/lkap_api/routers/{auth,workspaces,api_keys,connections,hooks,fleet,fleet_internal,webhooks,analytics,flows,telephony,calls,text_sessions}.py` (each exporting an empty `APIRouter` with its prefix) and wire them all in `main.py` now, so wave-1/2 packages only fill their own file and nobody edits `main.py` again. Same for `agent/src/lkap_agent/{registration,text_mode,telephony}.py` stubs.
- **Exclusive files.** `api/src/lkap_api/db/**`, `api/alembic/**`, `api/src/lkap_api/bootstrap.py`, `api/src/lkap_api/keys.py`, `api/src/lkap_api/settings.py` (new fields only), `api/tests/conftest.py`, `.github/workflows/api-postgres.yml` (stub; V2-09 owns the rest of CI).
- **Acceptance.** Python gate in `api/`; `uv run alembic upgrade head` then `downgrade 4135323c6ecc` then `upgrade head` on a copy of a v1 SQLite DB seeded with 2 agents/3 sessions (fixture `api/tests/fixtures/v1_seed.sqlite` created by this package from the v1 models) keeps row counts and re-parses every `agents.config` as `AgentConfig` v2; same on Postgres via `docker run postgres:16`; bootstrap twice = no duplicate rows; `keys rotate` round-trips a credential.
- **Live.** Run the migration against a copy of the developer's live `data/lkap.db` (never the original) and confirm `/v1/health` reports `db: ok` and the default connection fingerprint matches the env key's last 4.

### V2-02 Auth, tenancy, API keys, limits — Opus · Wave 1 · depends: V2-01
- **Scope.** CONTRACTS §3.1–3.3: login/logout/me/password/invites, argon2id, cookie sessions, `WorkspaceContext` dependency on every admin router, role checks, API keys (create/list/revoke, scopes), audit log middleware, rate limiting (Redis or in-memory), connect protection (origins, buckets, concurrency, TTL, F-13, metadata cap), token strength checks, break-glass admin token; web: `/login` page, Next middleware, console proxy forwarding cookie + `X-Workspace`, `?mode=test` gate.
- **Exclusive files.** `api/src/lkap_api/auth/**` (new package: `passwords.py`, `sessions.py`, `api_keys.py`, `deps.py`, `roles.py`, `ratelimit.py`, `audit.py`), `api/src/lkap_api/routers/auth.py`, `routers/workspaces.py`, `routers/api_keys.py`, `api/src/lkap_api/routers/connect.py` (limits), `web/src/app/login/**`, `web/src/middleware.ts`, `web/src/app/api/console/[...path]/route.ts`, `web/src/lib/auth.ts`.
- **Acceptance.** Python gate; tests: unauthenticated `/v1/agents` → 401; viewer cannot `PUT /v1/agents/{id}` (403); builder can; API key with `sessions:read` cannot create agents; cross-workspace read returns 404 not 403 (no existence leak); `connect` from a disallowed origin → 403; 7th connect per IP within a minute → 429; 6th concurrent session → 429 with `code=agent_busy`; token `exp` = `max_session_duration_s`; `dev-service` token refused when `LKAP_ENV=prod`. Web gate; Playwright: login → console loads → logout → `/console` redirects to `/login`.
- **Live.** Stage L1 (§6).

### V2-03 Connections service — Opus · Wave 1 · depends: V2-01
- **Scope.** CRUD + test + rotate + default + worker-env + deploy bundle endpoints; `ConnectionClientFactory` (`LiveKitAPI`/`AccessToken` per connection with `(id, credentials_version)` cache); `connect` and `sessions/start` minting through it; capability probes (list_rooms primary, SIP/Egress/Ingress secondary, 5 s timeouts); `fleet_desired` writer; per-connection LiveKit webhook receiver `/hooks/livekit/{connection_id}` (verify with that connection's secret; handle `egress_ended`, `room_finished`, `participant_left`); validation of agent configs against connection flags and installed providers (`config_service.py`, which exposes a `VALIDATORS: list[Callable[[ValidationContext], list[Issue]]]` hook list so other packages register checks from their own modules); `LKAP_AGENT_NAME` demoted from required to optional in `api/settings.py` and no longer used for dispatch.
- **Exclusive files.** `api/src/lkap_api/connections/**` (new: `service.py`, `clients.py`, `probe.py`, `bundle.py`, `webhooks.py`), `api/src/lkap_api/routers/connections.py`, `routers/hooks.py`, `api/src/lkap_api/livekit_tokens.py`, `api/src/lkap_api/config_service.py`, `api/src/lkap_api/routers/internal.py` (`sessions/start`, `recording/start`, `worker-env`; **not** the workers/fleet routes).
- **Acceptance.** Python gate; tests with a fake Twirp server (`respx`): bad secret → `status=error`, `message` mentions `unauthenticated`; unreachable URL → error within 5 s; capability JSON populated; `connect` for an agent bound to connection B mints with B's secret (decode JWT `iss`) and returns B's URL; rotate bumps `credentials_version` and `fleet_desired.desired_hash`; `sessions/start` creates a row with `channel=sip_in` and 409s on a duplicate room; webhook with wrong signature → 401; validation rejects `livekit-inference-llm` on a self-hosted connection with an `issues[]` entry at `pipeline.llm`.
- **Live.** Stage L2: create the existing Cloud project as a second, explicit connection (`cloud-a`) via the UI/API, test → `ok`, bind an agent, run a test call, and confirm `sessions.connection_id`. Never touch `other-project-agent`.

### V2-04 Supervisor — Opus · Wave 1 · depends: V2-03
- **Scope.** `supervisor/` package per CONTRACTS §5: reconcile loop, `SubprocessBackend`, `DockerBackend` (docker SDK), rolling drain/restart, backoff, lease, Prometheus metrics, `--once`; worker registration/heartbeat consumer on the api side (`worker_instances`, sweep to `gone`); `GET /v1/connections/{id}/fleet` and `POST …/fleet` actions; `scripts/dev.sh` starts the supervisor when `LKAP_SUPERVISOR=1`.
- **Exclusive files.** `supervisor/**` (new package), `api/src/lkap_api/fleet/**` (new: `registry.py`, `sweep.py`), `api/src/lkap_api/routers/fleet.py`, `api/src/lkap_api/routers/fleet_internal.py` (`workers/register`, `workers/{key}/heartbeat`, `fleet/desired`), `scripts/dev.sh`. The compose supervisor service is filed as an ask to V2-09 (which owns `deploy/**`).
- **Acceptance.** Python gate in `supervisor/` and `api/`; tests with a `FakeBackend`: desired 2 replicas from 0 → two `start` calls with env from a stubbed `worker-env`; hash change → rolling drain (never two down at once); a failed replica restarts with backoff; `--once` exits 0; with `SubprocessBackend` and a stub `python -m lkap_agent.main` script the child receives `LIVEKIT_URL` and `LKAP_CONNECTION_ID` and gets SIGINT then SIGKILL after grace; secrets never appear in supervisor logs (log capture assert).
- **Live.** Stage L3: switch connection `cloud-a` to `supervised`, replicas 1, `SubprocessBackend`; confirm the UI fleet card shows one `ready` instance with `installed_provider_ids`; stop the launch.json worker; run a test call served by the supervised worker; rotate credentials (re-enter the same key/secret) and watch a rolling restart with no failed call.

### V2-05 Provider registry at full breadth — Sonnet · Wave 1 · depends: V2-00
- **Scope.** Add every entry from `../research-v2/livekit-plugins-catalog.md` §5 (with `label`/`vendor` filled as its note instructs), plus VAD/turn/NC kinds, plus corrections (Hedra `removed`, MiniMax `incompatible`, PlayAI `deferred`, `GEMINI_LIVE_VOICES` = 30, Gemini Live model list += the two missing ids, xAI voices, Nova Sonic voices/turn detection, Ultravox, Phonic, PersonaPlex as `deferred`). Capabilities: `text_modality` for Gemini/OpenAI/Azure OpenAI/Ultravox; `cloud_only` for inference-* and krisp; `platforms` for bithuman/krisp. `catalog`/`test`/`price_ref` per entry. `pricing.py` entries for every provider the user could plausibly run (inference catalog prices, OpenAI, Google, Deepgram, Cartesia, ElevenLabs, bey, tavus — `source_url` + `as_of` mandatory). Factory special cases in the worker for: nested/positional avatar configs, Google credentials, AWS flat keys, Azure `with_azure`, half-cascade modalities per engine, `FieldType=file` → path.
- **Exclusive files.** `contracts/src/lkap_contracts/providers.py` (entries only; V2-00 froze the schema), `contracts/src/lkap_contracts/pricing.py` (entries), `contracts/generated/**` + `web/src/contracts/**` regen, `agent/src/lkap_agent/providers/factory.py`, `agent/src/lkap_agent/providers/special_cases.py` (new), `agent/tests/unit/test_factory_*.py`.
- **Acceptance.** Contracts gate; ≥ 85 `available` entries; a test asserts every `available` entry's `python_class` module path exists in the pinned 1.8.2 wheel list (`agent/requirements/full.txt`); every `secret_fields`/`fields` name appears in the constructor signature captured in `agent/tests/fixtures/plugin_signatures.json` (AST snapshot script `scripts/snapshot_plugin_signatures.py`, committed); factory unit tests construct (with the plugin module monkeypatched to a fake) Simli, Synthesia, D-ID, Anam, Google-with-credentials_file, AWS LLM, Azure OpenAI Realtime, Gemini half-cascade, and assert kwargs; `verification=verified` only for `livekit-inference-stt/llm/tts` and `fastembed-embedding` (the only providers that passed a live call in v1); every other entry is `unverified` until V2-20. One-time fixture step: `scripts/snapshot_plugin_signatures.py` needs the 1.8.2 source tree (`git clone --depth 1 --branch livekit-agents@1.8.2 https://github.com/livekit/agents`), because the installed venv contains only 8 plugins.
- **Live.** None here (V2-20 verifies with keys).

### V2-06 Credential tests, vendor catalogs, workspace enablement — Sonnet · Wave 1 · depends: V2-01, V2-05
- **Scope.** `api/src/lkap_api/catalogs/` adapters (D-V2-9 list) behind a `CatalogAdapter` protocol with a 1 h cache (Redis or `provider_catalog_cache`); `GET /v1/providers/{id}/catalog`; `_TEST_CALLS` → `tests/` adapters keyed by `ProviderSpec.test` with 10 s timeout and 10 min cache; `workspace_providers` settings endpoint and enforcement in validation (registered into V2-03's `VALIDATORS` hook list from `catalogs/validation.py`, never by editing `config_service.py`); reject unknown `{{ secret.NAME }}` at tool save (F-15).
- **Exclusive files.** `api/src/lkap_api/catalogs/**`, `api/src/lkap_api/credential_tests/**`, `api/src/lkap_api/routers/providers.py`, `routers/credentials.py`, `api/src/lkap_api/routers/tools.py` (F-15 only).
- **Acceptance.** Python gate; `respx`-mocked vendor responses for every adapter produce `CatalogItem[]`; cache hit within TTL makes zero HTTP calls; `refresh=true` bypasses; disabled provider → validation issue `providers.disabled`; a tool with `{{ secret.MISSING }}` → 422.
- **Live.** Stage L4 with the user's keys: ElevenLabs voices, Tavus faces/pals, Bey avatars, OpenAI models list in the UI.

### V2-07 Worker v2 — Opus · Wave 1 · depends: V2-00, V2-05
- **Scope.** `LKAP_AGENT_NAME` replaces the hardcoded `REQUIRED_AGENT_NAME` (`require_agent_name`, `effective_agent_name`, `only_lkap_jobs` compare against it; `LIVEKIT_AGENT_NAME_OVERRIDE` → explicit → `LIVEKIT_AGENT_NAME` precedence unchanged; re-run the two tripwire tests) and `LKAP_CONNECTION_ID`; register + heartbeat; `POST /internal/v1/sessions/{id}/recording/start` after `ctx.connect()` when `recording.enabled`, and the `list_egress` poll fallback at shutdown; `sessions/start` path when `DispatchMetadata.session_id` is None; pipeline modes incl. half-cascade (`SessionBuilder`), `vad`/`turn_detection`/`noise_cancellation` slots, connection flags (`turn_detector_mode`), `AvatarOptions`; `metrics_collected` → `SessionLatency` posted at summary; recording hooks (`recording` event when Egress confirmed); reconnect grace (`close_on_disconnect=False` + 60 s timer, F-33); `_http_safety` private-range deny-list + intersection semantics (F-14); `first_speaker`; `channel=text` session mode (audio off) — scaffolding only, rewind is V2-18.
- **Exclusive files.** `agent/src/lkap_agent/main.py`, `session_builder.py`, `settings.py`, `config_client.py`, `observability.py`, `registration.py`, `tools/_http_safety.py`, `platform_agent.py` (**wave 1 only**: `first_speaker`/greeting; ownership passes to V2-10 in wave 2), `agent/tests/**` except `test_factory_*`.
- **Acceptance.** Python gate (≥ 331 tests + new); tests: half-cascade builds realtime with text modality + TTS and refuses when `text_modality=false`; realtime mode gets no turn detector; `turn_detector_mode=local` → `TurnDetector(version="v1-mini")`; missing `session_id` → `sessions/start` called with `channel` from metadata; heartbeat posts every 30 s (fake clock); participant drop → job survives 60 s then shuts down; `http_request` to `10.0.0.1` refused even if allowlisted; tripwire tests still pass.
- **Live.** Stage L5 (half-cascade on Gemini Live text + inworld TTS on the free tier, short session), L6 (worker instance appears in fleet with heartbeats).

### V2-08 Jobs, storage, webhooks, QA job — Sonnet · Wave 1 · depends: V2-01
- **Scope.** `jobs/` abstraction (`enqueue(kind, payload)`) with `InlineBackend` (BackgroundTasks + `jobs` table) and `ArqBackend`; storage abstraction (`local`, `s3`) with signed URLs; KB ingest moved onto jobs with a 25 MB upload cap (F-29); webhook endpoints CRUD + durable delivery (HMAC, retries 1m/5m/30m/2h/6h/12h/24h/24h, dead-letter, redeliver, test); QA scoring job (rubric default from Dograh's tag set, JSON with one repair retry, `session_qa`); connection probe + catalog refresh + `usage_daily` rollup + worker sweep jobs; structured log fields (`workspace_id`, `session_id`, `job_id`).
- **Exclusive files.** `api/src/lkap_api/jobs/**`, `storage/**`, `webhooks/**`, `qa/**`, `routers/webhooks.py`, `routers/knowledge.py` (upload cap + job enqueue only), `api/src/lkap_api/kb/ingest.py`.
- **Acceptance.** Python gate; tests: inline backend runs a job to completion in-process; arq backend enqueues (fake Redis via `fakeredis`); webhook signature verifies with the documented formula; 3 failures then success → `delivered`, `attempt=4`; 8 failures → `dead`; QA job on a fixture transcript yields a 1–10 score and tags, and a malformed-JSON first response is repaired; upload > 25 MB → 413; S3 backend round-trips against `moto`.
- **Live.** Stage L7: end a real test call → `session.ended` webhook received by a local `webhook.site`-style listener (`scripts/webhook_sink.py`) with a valid signature; QA row populated within 60 s.

### V2-09 Images, compose, CI — Sonnet · Wave 1 · depends: V2-05
- **Scope.** `agent/Dockerfile` flavors + `requirements/{slim,full}.txt` generator + `check_installed_providers.py` + `installed_providers.json`; `supervisor/Dockerfile`; `api`/`web` Dockerfiles verified (F-21 pins); `deploy/docker-compose.dev.yml` (api, web, supervisor, minio, redis optional, postgres optional) and `deploy/docker-compose.prod.yml` (api ×2 behind Caddy, web, supervisor, postgres, redis, minio) with `env_file` and health checks; backups script (`scripts/backup.sh`: pg_dump + `LKAP_DATA_DIR` tar to S3); `.github/workflows/`: `contracts.yml`, `python.yml` (matrix api/agent/packs/supervisor × sqlite/postgres for api), `web.yml`, `docker.yml` (build api/web/agent slim/supervisor on PR; `full` nightly with the import check), `live.yml` (manual dispatch, secrets from repo settings).
- **Exclusive files.** `agent/Dockerfile`, `agent/requirements/**`, `scripts/gen_plugin_requirements.py`, `scripts/check_installed_providers.py`, `scripts/backup.sh`, `supervisor/Dockerfile`, `api/Dockerfile`, `web/Dockerfile`, `deploy/**` (whole directory, including the supervisor service V2-04 asks for), `.github/**`, `.dockerignore`.
- **Acceptance.** `docker build --build-arg LKAP_IMAGE_FLAVOR=slim -f agent/Dockerfile agent` succeeds and `installed_providers.json` lists exactly the slim set; `full` build succeeds on linux/amd64 and the import check passes for every `available` entry (any failure is listed in the build log and must either be fixed or the entry flipped to `deferred` with a note — the build is the gate); `docker compose -f deploy/docker-compose.dev.yml up` → `/v1/health` ok and the console login page loads; CI green on a PR that touches every package.
- **Live.** None (Stage L11 uses the images).

### V2-10 Panels v2 runtime — Opus · Wave 2 · depends: V2-00, V2-07
- **Scope.** `UiChannelImpl` block helpers (`set_block`, `patch_block`, `request_form` with timeout and cancellation, `cite`); `UiState.blocks` in snapshots/patches; `AgentAction` handlers `form_submit`, `block_action` → `Pack.on_block_action` (new optional protocol method with a default no-op); built-in tools `update_block`, `show_document`, `table_append`, `request_form` (blocking in cascaded, urgent-background in realtime per D-W2-9i semantics), implicit `cite_sources` from `search_knowledge` when a `kb_citations` block exists; `PlatformAgent` initialises `blocks` from `config.panel.blocks` (empty block states) and pack `default_panel`; `block_update`/`form_submitted` session events.
- **Exclusive files.** `agent/src/lkap_agent/ui/**`, `agent/src/lkap_agent/tools/builtin/{update_block,show_document,table_append,request_form}.py`, `tools/builtin/search_knowledge.py` (citations only), `platform_agent.py` (block init + handlers only), `packs/src/packs/base.py` (`on_block_action`, `UiChannel` protocol additions), `agent/tests/unit/test_ui_blocks*.py`, `packs/tests/test_base_protocol.py`.
- **Acceptance.** Python gates in `agent/` and `packs/`; tests: `set_block` emits `set /blocks/<id>`; `request_form` resolves with values on `form_submit`, returns `None` after timeout, and cancels on session close; realtime mode `request_form` returns `None` immediately and the values arrive as an urgent background result; insurance pack still passes with `blocks={}`; snapshot every 50 patches includes `blocks`.
- **Live.** Stage L8 (with V2-11).

### V2-11 Panels v2 web — Opus · Wave 2 · depends: V2-00, WP-0
- **Scope.** `web/src/panels/composite/` (layout from `PanelLayout`, side/wide), block components under `web/src/panels/blocks/{status,notes,checklist,activity,form,document,gallery,table,transcript,video,kb_citations,custom}.tsx` on WP-0 primitives; `<Block>` export for custom panels; `handleRequest` for `form`/`show_block`/`navigate`; reducer support for `/blocks`; `useUiState` unchanged API; document viewer = PDF (pdf.js) + image + markdown; video block = `useVoiceAssistant().videoTrack` / local tracks / any track by sid; agent editor **Panel section** becomes a composer (add/remove/reorder blocks, per-block config forms generated from the block config schema, live preview via `/console/preview/panels?scene=`); `PANEL_META` update; `KNOWN_PANEL_IDS` removed (F-30) in favour of the registry.
- **Exclusive files.** `web/src/panels/**` (except `insurance_notebook/**` beyond a `<Block>` import), `web/src/lib/ui-state.ts`, `web/src/hooks/useUiRequests.ts`, `web/src/components/console/agents/panel-section/**`, `web/src/app/console/preview/**`, `web/src/components/shared/panel-meta.ts`.
- **Acceptance.** Web gate; vitest: reducer applies block patches and snapshots; each block renders its fixture state (`web/src/panels/blocks/__fixtures__/*.json`, generated from the contracts schemas); form block submits values through `perform`; the composite panel with 12 blocks renders under 400 kB First Load JS on `/s/[slug]` (`pnpm build` output asserted by `scripts/check-bundle.mjs`); axe pass on the preview scenes; the insurance notebook still renders unchanged.
- **Live.** Stage L8: an agent on the generic pack with `form`, `table`, `gallery`, `document`, `kb_citations` blocks — the LLM fills a form via `request_form`, appends table rows, pins a frame into the gallery, and citations appear after a KB answer.

### V2-12 Recordings, cost, sessions v2 — Sonnet · Wave 2 · depends: V2-03, V2-08
- **Scope.** `POST /internal/v1/sessions/{id}/recording/start` handler (`RoomCompositeEgress` audio-only to the resolved storage config via the connection's client), `hooks/livekit` `egress_ended` finalisation plus the worker-posted fallback, signed playback URL endpoint, retention job; cost computation at summary (usage × `pricing.lookup`, plus egress/avatar minutes) into `session_costs`/`sessions.cost_usd`; latency persisted; sessions list filters/pagination; `SessionDetailOut` v2; soft delete + agent archive/unarchive/purge (F-22); analytics summary endpoint; `AgentOut.session_count/last_session_at`.
- **Exclusive files.** `api/src/lkap_api/recordings/**`, `costs/**`, `routers/sessions.py`, `routers/agents.py` (archive/versions endpoints), `routers/analytics.py`, `api/src/lkap_api/sessions_sweep.py`.
- **Acceptance.** Python gate; tests: egress request built with the connection's storage creds and audio-only preset; `egress_ended` webhook → `recording_status=ready`, `duration_s` set; cost lines for a fixture usage payload match hand-computed totals to 6 dp and unknown models produce `note="no price"`; archive → connect returns 404; purge cascades sessions; analytics sums equal the fixture.
- **Live.** Stage L9: a 60 s test call on `cloud-a` with recording on → playable audio in the session detail from MinIO, cost lines for inference STT/LLM/TTS, latency p50/p95 populated.

### V2-13 Web: connections, providers, avatars, editor slots — Sonnet · Wave 2 · depends: V2-03, V2-06, WP-4
- **Scope.** Per `UI_UX_SPEC-V2-AMENDMENTS.md` §4: `/console/connections` (list with status/capability chips/fleet state), `/console/connections/new`, `/console/connections/[id]` (details, test, capabilities, rotate, fleet card with start/stop/restart + instances table, worker-env / compose / lk snippets with `CopyButton`, deploy bundle download, storage); `/console/providers` (catalog grouped by kind with search, enable/disable, `installed on` chips, verification chip, key sheet, test, catalog previews); avatar pickers (`catalog` fields → combobox loading `/catalog`, paste-id fallback, preview image when the catalog returns one); agent editor Providers section: connection picker (with capability warnings), three mode cards (cascaded/realtime/half-cascade with `text_modality` gating), STT→LLM→TTS→VAD→Turn→NC slot cards (advanced slots collapsed), avatar card with options, `installed on connection` gating and `issues[]` rendering.
- **Exclusive files.** `web/src/app/console/connections/**`, `web/src/app/console/providers/**`, `web/src/components/console/connections/**`, `web/src/components/console/providers/**`, `web/src/components/console/registry/**` (post WP-4), `web/src/components/console/agents/providers-section/**`, `web/src/hooks/useConnections.ts`, `useProviders.ts`, `useCatalog.ts`.
- **Acceptance.** Web gate; vitest with `msw`: test button shows capability chips from the mocked response; half-cascade card is disabled with a reason when the realtime provider lacks `text_modality`; a provider not installed on the bound connection renders disabled with the "not installed on <connection>" hint; catalog combobox falls back to free text when the adapter 404s; Playwright: create connection → test → bind agent → open session in test mode.
- **Live.** Stages L2–L4 UI paths.

### V2-14 Web: auth, team, keys, webhooks, settings, sessions v2 — Sonnet · Wave 2 · depends: V2-02, V2-12, WP-1, WP-7
- **Scope.** `/login`, invite acceptance, `/console/settings` tabs (Workspace, Team, API keys, Webhooks, Storage, Danger zone), workspace switcher in the sidebar, sessions list filters (channel, connection, range) + detail tabs Timeline · Transcript · Recording · Cost · QA · Panel at end of call · Raw; `/console/analytics` (summary cards + by-day chart + by-agent table on the `dataviz` guidance); Playwright smoke login → console → test call → transcript (F-35).
- **Exclusive files.** `web/src/app/login/**` (from V2-02, extend), `web/src/app/console/settings/**`, `web/src/app/console/analytics/**`, `web/src/app/console/sessions/**` (post WP-7), `web/src/components/console/settings/**`, `web/src/components/console/sessions/**`, `web/e2e/**`.
- **Acceptance.** Web gate; vitest for each settings tab with `msw`; recording player renders with a signed URL and handles 404; cost tab totals equal lines; Playwright smoke green in CI (`web.yml`).
- **Live.** Stage L10: login as the bootstrap owner, invite a second user as viewer, confirm the viewer cannot open the editor or test mode.

### V2-15 Flow runtime — Opus · Wave 3 (stretch) · depends: V2-07, V2-10
- **Scope.** `agent/src/lkap_agent/flow/`: `FlowNodeAgent(PlatformAgent)`, `build_flow(spec, resolved) -> start agent`, edge tools `go_to_<id>` with condition descriptions and `transition_speech`, carried `chat_ctx`, `FlowState` in `session.userdata`, global node prefix, per-node tools/KB subsets, cascaded-only LLM/TTS overrides, `extract` via `StructuredLLM` on node exit, end node farewell + disposition + `handoff` events, `qa` node → QA config override; `max_turns` guard.
- **Exclusive files.** `agent/src/lkap_agent/flow/**`, `agent/tests/unit/test_flow_*.py`, `platform_agent.py` (hook points only, coordinated with V2-10's ask file).
- **Acceptance.** Python gate; tests with the fake LLM: a 3-node flow transitions on tool call, `handoff` events emitted, variables extracted into `FlowState`, `transition_speech` spoken before handoff, realtime mode ignores provider overrides with a warning, `max_turns` forces the fallback edge.
- **Live.** Stage L12a: a 3-node intake flow on the free-tier Gemini cascaded LLM completes with a disposition.

### V2-16 Flow API + builder UI — Opus · Wave 3 (stretch) · depends: V2-00, V2-15
- **Scope.** `GET /v1/flows/node-specs`, flow validation in `config_service`, versions endpoints + restore, diff (`jsondiffpatch` in web); `/console/agents/[id]?section=flow`: React Flow (`@xyflow/react` v12 + dagre) canvas, node forms generated from the node-spec JSON schema (`@rjsf`-free: a small schema→Field renderer on WP-0 primitives), edge editor (condition, transition speech), variables dialog with `@mention` insertion, validation dots, version history sheet with diff; mode switch prompt↔flow with a confirmation.
- **Exclusive files.** `api/src/lkap_api/routers/flows.py`, `api/src/lkap_api/flows/**`, `web/src/app/console/agents/[id]/flow/**`, `web/src/components/console/flow/**`, `web/src/lib/schema-form/**`.
- **Acceptance.** Python gate; web gate; vitest: node form renders every field of `AgentNode` from the schema; invalid graph shows issues on nodes; restore creates a new version; canvas bundle is code-split (not in the session bundle).
- **Live.** Stage L12a via the UI.

### V2-17 Telephony core — Opus · Wave 3 (stretch) · depends: V2-03, V2-07
- **Scope.** `api/src/lkap_api/telephony/` (trunk/rule/number CRUD mirrored to LiveKit via `SipService`, `calls` service with `CreateSIPParticipant` + `wait_until_answered`, hangup, transfer via `transfer_sip_participant`, DTMF send via a worker RPC), worker `on_dtmf` hook + `send_dtmf` built-in + `transfer_call` built-in, SIP participant attributes → `caller`; UI: `/console/telephony` (trunks, numbers with inbound agent picker, dispatch rules, calls log, "Call a number" dialog on the agent editor's Test split button).
- **Exclusive files.** `api/src/lkap_api/telephony/**`, `routers/telephony.py`, `routers/calls.py`, `agent/src/lkap_agent/telephony.py`, `tools/builtin/{send_dtmf,transfer_call}.py`, `web/src/app/console/telephony/**`, `web/src/components/console/telephony/**`.
- **Acceptance.** Python gate with a fake `SipService`; tests: trunk create mirrors to LiveKit and stores `lk_trunk_id`; inbound rule metadata carries `agent_id`/`channel=sip_in`; outbound call row transitions `dialing→answered→completed` from webhook events; DTMF event → tool hook; self-hosted connection with `sip_enabled=false` → 409.
- **Live.** Stage L12b, only if the user supplies a SIP trunk + number: inbound call reaches the agent and a session row with `channel=sip_in` appears; outbound call from the console rings the user's phone; DTMF digits logged; cold transfer to a second number.

### V2-18 Text test mode + widget — Sonnet · Wave 3 (stretch) · depends: V2-02, V2-07, WP-8
- **Scope.** `POST /v1/agents/{id}/text-sessions`; worker text-channel session (audio off, `text_input=True`), `rewind` (truncate `chat_ctx` to turn N, regenerate) and `inject_user_text` actions; console **Test chat** drawer on the editor (transcript with per-turn edit/replay); `web/public/widget.js` + `/s/[slug]?embed=1` layout (compact, `postMessage` bridge), snippet dialog on the agent page, `allowed_origins` editor.
- **Exclusive files.** `api/src/lkap_api/routers/text_sessions.py`, `agent/src/lkap_agent/text_mode.py`, `web/public/widget.js`, `web/src/components/console/agents/test-chat/**`, `web/src/components/session/embed/**`, `web/src/app/(session)/s/[slug]/embed-layout.tsx`.
- **Acceptance.** Python gate; tests: text session starts with no audio tracks, `rewind` to turn 2 drops later turns and regenerates once; web gate; widget script mounts an iframe with `allow="microphone; autoplay"` and the agent slug; `/s/[slug]?embed=1` responds with `Content-Security-Policy: frame-ancestors <allowed_origins>` (the real enforcement) and connect additionally 403s a disallowed `Origin`; Playwright: text chat round trip with the fake worker.
- **Live.** Stage L12c: text chat with rewind on `cloud-a`; widget on a local static page.

### V2-19 Integration — Opus · Wave 4 · depends: all core
- **Scope.** Merge order and conflict resolution (`_asks.md` closed); migration rehearsal on a copy of the live SQLite DB and on Postgres; `LKAP_PACKS` parity check (worker registration reports `pack_ids`, api warns on mismatch, F-17); shared fakes package `testing/lkap_testing` used by agent and packs tests (F-18); RUNBOOK v2 (connections, supervisor, images, compose, backups, rotation), CONTRACTS regen check, README; a `scripts/smoke_v2.sh` that boots compose dev, logs in, creates a connection from env, binds the generic agent and runs a text-mode round trip.
- **Exclusive files.** `docs/RUNBOOK.md`, `README.md`, `testing/**`, `scripts/smoke_v2.sh`, plus conflict fixes anywhere (announced in `_asks.md`).
- **Acceptance.** Every package gate green on `main`; `scripts/smoke_v2.sh` exits 0 on a clean checkout with Docker; the migration rehearsal log is attached to `docs/v2/_briefs/migration-rehearsal.md`.

### V2-20 Live verification — Opus · Wave 4 · depends: V2-19
- **Scope.** Run RUNBOOK v1 stages 0–9 as regression and v2 stages L1–L12 (§6) on `wss://your-project.livekit.cloud`; record results in `docs/v2/LIVE-RESULTS.md` with commands, outputs and the exact keys used (fingerprints only); flip registry `verification=verified` for every provider that passed and regenerate.
- **Acceptance.** Every core stage passes; each stretch stage is passed or explicitly `not run` with the blocking reason (missing key/trunk).

### V2-21 Security and production review — Opus, Fable sign-off · Wave 4 · depends: V2-20
- **Scope.** Threat-model pass (tenancy isolation tests across two workspaces, auth, SSRF in tools/webhooks/catalog adapters, webhook receiver replay, signed URL scope, secrets in logs, supply-chain pins), `k6` load test of `connect` (100 rps mixed valid/invalid), dependency audit (`uv run pip-audit`, `pnpm audit`), a punch list in `docs/v2/REVIEW-V2.md` with severities; nothing ships to production with an open HIGH.

## 5. UI packages WP-1..WP-12 (resumed)

See `UI_UX_SPEC-V2-AMENDMENTS.md` §3 for the per-package delta. Summary: WP-1 (nav groups + workspace switcher + login redirect), WP-3 (mode switch prompt/flow, connection chip in the header, versions in the summary rail), WP-4 (superseded in part by V2-13: WP-4 ships the v1 slot editor on the v2 registry types; V2-13 extends it), WP-5 (Panel tab replaced by the V2-11 composer; Tools/Instructions/Knowledge unchanged), WP-7 (detail tabs extended by V2-14). WP-2, WP-6, WP-8, WP-9, WP-10, WP-11, WP-12 proceed unchanged. Waves: WP-1..WP-11 in wave 1 (parallel with the backend wave), WP-12 at the end of wave 2.

## 6. Live verification stages (v2)

Preconditions: LiveKit creds for the Cloud project; one worker per connection; `other-project-agent` untouched; text before audio; short sessions; the Google key is free-tier (≤ 5 short sessions per stage on Gemini).

| Stage | Package | Proves | Needs from the user |
|---|---|---|---|
| L1 | V2-02 | Login, roles, API key call, connect rate limit (429 on the 7th call) | — |
| L2 | V2-03 | Second explicit connection `cloud-a` created from the UI, `test` ok with capabilities, agent bound, test call served with `sessions.connection_id` | — |
| L3 | V2-04 | Supervised pool on `cloud-a` (subprocess backend), fleet card, rolling restart on rotation, launch.json worker stopped | — |
| L4 | V2-06 | Catalogs: OpenAI models, ElevenLabs voices, Tavus faces/pals, Bey avatars | vendor keys (any subset) |
| L5 | V2-07 | Half-cascade (Gemini Live text + inworld TTS), VAD/turn slots explicit, realtime without turn detector | Google key (free tier) |
| L6 | V2-07 | Worker registration + heartbeats + `installed_provider_ids` visible | — |
| L7 | V2-08 | `session.ended` webhook signed + delivered; QA scored | — |
| L8 | V2-10/11 | Composite panel: form, table, gallery, document, citations | — |
| L9 | V2-12 | Recording playable (MinIO), cost lines, latency | — |
| L10 | V2-14 | Viewer role restrictions; Playwright smoke | — |
| L11 | V2-09 | `docker compose -f deploy/docker-compose.prod.yml` boots with Postgres/Redis/MinIO; docker-backend pool serves a call | Docker on the host |
| L12a/b/c | V2-15..18 | Flow with disposition / inbound+outbound SIP + DTMF + transfer / text rewind + widget | SIP trunk + number for L12b |
| L13 | V2-20 | Avatars: bey and tavus (+ simli, anam) start → join → speak; `verification=verified` flipped | Bey (free tier), Tavus, optional Simli/Anam keys |
| L14 | V2-20 | Self-hosted connection: a local `livekit-server` (docker, `--dev`) as connection `self-a` reached over **`ws://localhost:7880`** (plain ws; self-signed `wss://` does not work), supervised pool, vendor-key cascaded call, `inference_available=false` enforced | Docker |

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Architect cutoff before the plan exists | This file was written table-first; each card is independent. |
| Wave 1 blocked on V2-00/V2-01 | Both are small by design (schemas + migrations only); they land first and alone. |
| `full` image native wheels fail (bithuman, Krisp, Azure Speech, awscrt) | The build gate is the import check; failures flip entries to `deferred` with a note instead of blocking the wave. |
| Google free-tier quota during live stages | Text-first stages, ≤ 5 sessions per stage, gemma/inference for non-vision stages, Gemini only where required. |
| Migration on the live SQLite file | Rehearsed on a copy in V2-01 and V2-19; downgrade tested; backups before `upgrade head`. |
| Tenancy regression (unscoped query) | `before_compile` guard in tests + two-workspace isolation tests in V2-21. |
| Supervisor and launch.json worker both serving `lkap-agent` | L3 stops the external worker before switching mode; the UI warns when `worker_instances` shows `external` and `supervisor` rows for one connection. |
| Half-cascade/realtime behaviours differ per engine | Capabilities gate the UI; only engines with `text_modality` are offered; L5 verifies Gemini first. |
| Stretch scope creep | Stretch packages cannot start until every core package of waves 0–2 is merged. |
| Vendor list APIs UNVERIFIED for several avatars | Paste-id fallback is the default; catalogs are additive. |
