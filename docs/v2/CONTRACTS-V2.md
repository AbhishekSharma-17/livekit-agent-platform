# LKAP v2 — Contracts

Status: **decided** (Fable 5.1, 2026-09-19). This file wins over `ARCHITECTURE-V2.md` on names and shapes and extends `../CONTRACTS.md`; anything not mentioned here is unchanged from v1. All models are Pydantic v2 in `contracts/src/lkap_contracts/` unless stated; DB tables are SQLAlchemy 2 async in `api/src/lkap_api/db/models.py`; IDs are uuid4 hex; timestamps are UTC `datetime`.

Conventions carried from v1: list endpoints return `Page[T]{items,total}` and now accept `?limit=&offset=` (default 25/0, max 200); errors are `ErrorResponse{error:{code,message,details}}`; `ValidationResult` gains `issues: Issue[]{path, message, severity: error|warning}` (UI_UX_SPEC §7.14 ask). Every admin route is workspace-scoped through the `WorkspaceContext` dependency (§3.1).

## 1. Entities and DB schema

### 1.1 Tenancy

| Table | Columns | Notes |
|---|---|---|
| `workspaces` | `id`, `slug` UNIQUE, `name`, `settings` JSON (timezone, price_overrides_enabled), `created_at`, `updated_at` | Bootstrap creates `default`. The default connection is `livekit_connections.is_default`, not a settings key. |
| `users` | `id`, `email` UNIQUE (lowercased), `name`, `password_hash` (argon2id) NULLABLE (OIDC later), `is_platform_admin` BOOL default 0, `disabled_at`, `created_at`, `updated_at` | |
| `workspace_members` | (`workspace_id`, `user_id`) PK, `role` CHECK IN (`owner`,`admin`,`builder`,`viewer`), `created_at` | Role matrix in §3.2. |
| `user_sessions` | `id`, `user_id` FK CASCADE, `token_hash`, `expires_at`, `created_at`, `last_seen_at`, `user_agent`, `ip` | Cookie session; refresh rotates the row. |
| `api_keys` | `id`, `workspace_id` FK, `name`, `prefix` (first 8 chars, shown), `key_hash` (sha256), `scopes` JSON (`["agents:read","sessions:read","calls:write",...]` or `["*"]`), `created_by`, `last_used_at`, `revoked_at`, `expires_at` | Raw key `lkap_` + 32 urlsafe bytes, shown once. |
| `audit_log` | `id` autoinc, `workspace_id`, `actor_type` (`user`,`api_key`,`system`), `actor_id`, `action`, `target_type`, `target_id`, `payload` JSON, `ts` | Written for every mutating admin call. |

Every v1 tenant table (`agents`, `credentials`, `tools`, `knowledge_bases`, `sessions`) gains `workspace_id` FK NOT NULL + index `ix_<table>_workspace`. Child tables (`kb_documents`, `kb_chunks`, `agent_knowledge_bases`, `session_events`) inherit scope through their parent and gain no column.

### 1.2 Connections and fleet

| Table | Columns | Notes |
|---|---|---|
| `livekit_connections` | `id`, `workspace_id` FK, `slug` UNIQUE per workspace, `name`, `deployment_type` CHECK IN (`cloud`,`self_hosted`), `url`, `api_key_ct` BLOB, `api_secret_ct` BLOB, `credentials_version` INT default 1, `agent_name` default `lkap-agent`, `deployment_mode` CHECK IN (`external`,`supervised`,`cloud_hosted`), `replicas` INT default 1, `worker_image` CHECK IN (`slim`,`full`), `region`, `use_inference` BOOL default 1, `storage_config_id` FK NULL, `is_default` BOOL, `status` CHECK IN (`unverified`,`ok`,`error`), `capabilities` JSON (`ConnectionCapabilities`), `last_checked_at`, `last_error`, `created_at`, `updated_at` | Secrets Fernet-encrypted with `LKAP_MASTER_KEY`; `fingerprint` derived (`…` + last 4 of api_key). One `is_default=1` per workspace (partial unique index). |
| `worker_instances` | `id`, `connection_id` FK CASCADE, `instance_key` (hostname:pid or container id) UNIQUE, `image` (`slim`,`full`), `sdk_version`, `installed_provider_ids` JSON, `pack_ids` JSON, `registered_at`, `last_heartbeat_at`, `status` (`starting`,`ready`,`draining`,`gone`), `managed_by` (`external`,`supervisor`,`cloud`) | Rows older than 3 heartbeats are marked `gone` by the sweep. |
| `fleet_desired` | `connection_id` PK FK CASCADE, `desired_replicas`, `restart_generation` INT NOT NULL default 0, `restart_requested_at` NULL, `desired_hash` (sha256 of url+credentials_version+agent_name+image+packs+restart_generation), `updated_at` | Written by the api on any connection change; read by the supervisor. `POST …/fleet {action: restart}` bumps `restart_generation` (R-V2-4), which changes the hash and triggers the supervisor's existing rolling replacement. |
| `storage_configs` | `id`, `workspace_id`, `name`, `kind` (`s3`,`local`), `bucket`, `region`, `endpoint_url`, `prefix`, `access_key_ct`, `secret_key_ct`, `public_base_url`, `is_default`, timestamps | Used by Egress and KB uploads. Local kind is dev-only. |

### 1.3 Agents, versions, provider settings

| Table | Change |
|---|---|
| `agents` | += `workspace_id`, `connection_id` FK NULLABLE (RESTRICT delete; NULL only between migration and bootstrap — the api refuses to `connect`/resolve an agent with a NULL connection and `/v1/health` reports `agents_unbound: n`), `mode` CHECK IN (`prompt`,`flow`) default `prompt`, `archived_at` NULL, `limits` JSON (`AgentLimits`), `allowed_origins` JSON. `config` JSON is now `AgentConfig` v2 (`v=2`). `ui_panel_id` is kept for one release as the read-only mirror of `config.panel.panel_id`. |
| `agent_config_versions` | NEW: `id`, `agent_id` FK CASCADE, `config_version` INT, `config` JSON, `created_by`, `created_at`, `note`; UNIQUE(`agent_id`,`config_version`). Written on every config change (D-V2-13). |
| `workspace_providers` | NEW: (`workspace_id`,`provider_id`) PK, `enabled` BOOL default 1, `default_credential_id` FK NULL, `updated_at`. Absence of a row = enabled. |
| `credentials` | += `workspace_id`, `last_test_at`, `last_test_ok`, `last_test_message`. |
| `provider_catalog_cache` | NEW: `id`, `provider_id`, `credential_id` NULL, `kind` (`models`,`voices`,`avatars`,`personas`), `items` JSON (`CatalogItem[]`), `fetched_at`, `ttl_s`. Used when Redis is absent. |
| `tools` | += `workspace_id`. |
| `knowledge_bases` | += `workspace_id`, `storage_config_id` NULL. |

### 1.4 Sessions, records, QA, cost

| Table | Change |
|---|---|
| `sessions` | += `workspace_id`, `connection_id`, `channel` CHECK IN (`web`,`test`,`text`,`sip_in`,`sip_out`,`widget`,`api`), `caller` JSON (SIP: from/to/trunk_id/call_id; widget: origin), `recording_status` (`none`,`requested`,`active`,`ready`,`failed`), `recording_egress_id`, `recording_object_key`, `recording_duration_s`, `cost_usd` NUMERIC(12,6) NULL, `latency` JSON (`SessionLatency`), `disposition` (flow end-node value), `variables` JSON (flow extraction), `deleted_at` NULL. |
| `session_qa` | NEW: `session_id` PK FK CASCADE, `status` (`pending`,`done`,`failed`,`skipped`), `scored_by` (`worker`,`api`), `score` INT NULL (1–10), `sentiment` (`positive`,`neutral`,`negative`), `tags` JSON, `summary`, `raw` JSON, `model`, `scored_at`, `error`. |
| `session_costs` | NEW: `id`, `session_id` FK CASCADE, `provider_id`, `model`, `unit` (`tokens_in`,`tokens_out`,`audio_s_in`,`audio_s_out`,`chars`,`minutes`,`images`), `quantity` NUMERIC, `unit_price_usd` NUMERIC, `cost_usd` NUMERIC, `price_version`. |
| `usage_daily` | NEW: (`workspace_id`,`day`,`agent_id`) PK, `sessions`, `minutes`, `cost_usd`, `failed`. Rolled up by a nightly job for the analytics page. |

### 1.5 Webhooks and jobs

| Table | Columns |
|---|---|
| `webhook_endpoints` | `id`, `workspace_id`, `url`, `secret_ct`, `events` JSON, `enabled`, `description`, `created_at`, `updated_at` |
| `webhook_deliveries` | `id`, `endpoint_id` FK CASCADE, `event_type`, `event_id`, `payload` JSON, `attempt` INT, `status` (`pending`,`delivered`,`failed`,`dead`), `next_attempt_at`, `last_status_code`, `last_error`, `created_at`, `delivered_at`; index (`endpoint_id`,`status`,`next_attempt_at`) |
| `jobs` (inline backend only) | `id`, `kind`, `payload` JSON, `status`, `attempts`, `run_at`, `last_error`, timestamps. With `arq` the queue lives in Redis and this table records outcomes only. |

### 1.6 Telephony (stretch; migration ships in V2-01 so later waves add no schema)

| Table | Columns |
|---|---|
| `sip_trunks` | `id`, `workspace_id`, `connection_id` FK, `direction` (`inbound`,`outbound`), `name`, `lk_trunk_id`, `numbers` JSON, `provider_hint` (`twilio`,`telnyx`,`other`), `address`, `auth_username`, `auth_password_ct`, `created_at`, `updated_at` |
| `sip_dispatch_rules` | `id`, `workspace_id`, `connection_id`, `lk_rule_id`, `trunk_id` FK, `numbers` JSON, `agent_id` FK, `room_prefix`, `pin`, `created_at` |
| `phone_numbers` | `id`, `workspace_id`, `e164` UNIQUE, `trunk_id` FK, `inbound_agent_id` FK NULL, `label` |
| `calls` | `id`, `session_id` FK NULL, `workspace_id`, `connection_id`, `direction`, `from_e164`, `to_e164`, `status` (`dialing`,`ringing`,`answered`,`no_answer`,`busy`,`failed`,`completed`,`transferred`), `sip_call_id`, `lk_participant_identity`, `started_at`, `answered_at`, `ended_at`, `hangup_reason`, `transfer_to` |
| `campaigns`, `campaign_contacts` | Phase 2; not created in Phase 1. |

## 2. Migrations (Alembic, `api/alembic/versions/`, chained after `4135323c6ecc`)

All migrations must run on SQLite (dev) and Postgres (CI matrix). SQLite `ALTER TABLE … ADD COLUMN` with NOT NULL requires a server default or batch mode: use `op.batch_alter_table` and backfill.

| Rev (label) | Content |
|---|---|
| `v2_001_tenancy` | Create `workspaces`, `users`, `workspace_members`, `user_sessions`, `api_keys`, `audit_log`. Insert workspace `default` (id from a fixed constant `DEFAULT_WORKSPACE_ID = "00000000000000000000000000000001"`). |
| `v2_002_connections` | Create `livekit_connections`, `worker_instances`, `fleet_desired`, `storage_configs`. **Data migration**: if env `LIVEKIT_URL/API_KEY/API_SECRET` present at migration time, insert the `default` connection encrypted with `LKAP_MASTER_KEY` (else leave empty; the api bootstrap does it at startup). |
| `v2_003_scope_tables` | Add `workspace_id` (batch, default `DEFAULT_WORKSPACE_ID`, then NOT NULL) to `agents`, `credentials`, `tools`, `knowledge_bases`, `sessions`; indexes. Add `agents.connection_id` (backfilled to the default connection id if one exists; otherwise NULL until bootstrap creates it and backfills), `mode`, `archived_at`, `limits`, `allowed_origins`. |
| `v2_004_versions_providers` | Create `agent_config_versions` (backfill one row per agent from current `config`/`config_version`), `workspace_providers`, `provider_catalog_cache`; `credentials` test columns; `knowledge_bases.storage_config_id`. |
| `v2_005_sessions_ext` | `sessions` new columns; create `session_qa`, `session_costs`, `usage_daily`. Backfill `channel='web'`, `connection_id` = agent's connection. |
| `v2_006_webhooks_jobs` | `webhook_endpoints`, `webhook_deliveries`, `jobs`. |
| `v2_007_telephony` | `sip_trunks`, `sip_dispatch_rules`, `phone_numbers`, `calls`. |
| `v2_009_fleet_restart` (R-V2-4, owner V2-04 follow-up) | `fleet_desired` += `restart_generation` INT NOT NULL DEFAULT 0, `restart_requested_at` NULL. |
| `v2_010_qa_status` (R-V2-5, owner V2-08 follow-up) | `session_qa.status` CHECK += `skipped`; `session_qa` += `scored_by`. |
| `v2_008_agentconfig_v2` | Data migration: rewrite every `agents.config` and `agent_config_versions.config` from `AgentConfig` v1 to v2 using `lkap_contracts.migrate.agent_config_v1_to_v2` (pure function, unit-tested); sets `ui_panel_id` mirror. Reversible (`v2_to_v1` drops v2-only fields). |

Rollback: each revision has a `downgrade()`; V2-19 rehearses `upgrade head` + `downgrade 4135323c6ecc` on a copy of the live SQLite file and on Postgres.

---
## 3. Auth, tenancy and the API surface

### 3.1 Principals and scoping

- **User session**: cookie `lkap_session` (HttpOnly, Secure outside dev, SameSite=Lax), value = opaque token whose sha256 is in `user_sessions`. `POST /v1/auth/login {email,password}` → 204 + cookie; `POST /v1/auth/logout`; `GET /v1/auth/me` → `Me{user, workspaces:[{id,slug,name,role}]}`; `POST /v1/auth/password {current,new}`. Signup is invite-only: `POST /v1/workspaces/{id}/invites` (owner/admin) → link with a one-time token; `POST /v1/auth/accept-invite`.
- **API key**: header `Authorization: Bearer lkap_…`; scoped to one workspace; `scopes` checked per route (`agents:read|write`, `sessions:read|write`, `calls:write`, `connections:read|write`, `providers:read|write`, `webhooks:write`, `*`).
- **Service token** (`X-Service-Token`, worker/supervisor → `/internal/v1/*`): unchanged mechanism, ≥32 bytes enforced at startup (dev-* refused unless `LKAP_ENV=dev`).
- **Break-glass admin token**: `X-Admin-Token` only when `LKAP_ALLOW_ADMIN_TOKEN=true`; acts as owner of every workspace; audit-logged.
- **Workspace resolution**: header `X-Workspace: <slug|id>` or `?workspace=`; defaults to the user's only workspace, else 400. `WorkspaceContext{workspace_id, actor, role}` is the FastAPI dependency every admin router requires. Tests include a guard that fails any query on a tenant table without a `workspace_id` predicate.
- **Web**: Next.js middleware redirects unauthenticated `/console/**` to `/login`; `/api/console/[...path]` forwards the cookie (and `X-Workspace`) instead of `LKAP_ADMIN_TOKEN`; `/s/[slug]?mode=test` calls `/api/console/agents/{slug}/connect` which requires role ≥ builder.

### 3.2 Role matrix

| Action | viewer | builder | admin | owner |
|---|---|---|---|---|
| Read agents, sessions, analytics, recordings | ✓ | ✓ | ✓ | ✓ |
| Create/edit/publish agents, tools, KBs, panels, flows; test calls | | ✓ | ✓ | ✓ |
| Credentials, provider enablement, webhooks, storage configs | | | ✓ | ✓ |
| Connections (create, rotate, fleet start/stop), API keys, members, telephony trunks | | | ✓ | ✓ |
| Delete workspace, transfer ownership, break-glass | | | | ✓ |

### 3.3 Limits and public-connect protection

`AgentLimits{max_concurrent_sessions: int=5, max_session_duration_s: int=1800, rate_per_ip_per_min: int=6, rate_per_agent_per_min: int=60}` on `agents.limits`; `agents.allowed_origins: list[str]` (empty = console/test only; `["*"]` = any). `connect` enforces: origin (from `Origin`/`Referer`) ∈ allowed_origins for unauthenticated calls; per-IP and per-agent token buckets (Redis if `LKAP_REDIS_URL`, else in-memory); active sessions < max; token TTL = `max_session_duration_s`; client-supplied `participant_identity` honoured only for builder+ (F-13); `participant_metadata` ≤ 2 KB.

### 3.4 Endpoints (new or changed; unchanged v1 routes omitted)

Auth: `POST /v1/auth/login`, `POST /v1/auth/logout`, `GET /v1/auth/me`, `POST /v1/auth/password`, `POST /v1/auth/accept-invite`.

Workspaces & team: `GET /v1/workspaces`, `PUT /v1/workspaces/{id}`, `GET/POST /v1/workspaces/{id}/members`, `PUT/DELETE /v1/workspaces/{id}/members/{user_id}`, `POST /v1/workspaces/{id}/invites`, `GET/POST /v1/api-keys`, `DELETE /v1/api-keys/{id}` (revoke), `GET /v1/audit?limit=&offset=`.

Connections: `GET/POST /v1/connections`, `GET/PUT/DELETE /v1/connections/{id}` (DELETE 409 if agents bound), `POST /v1/connections/{id}/test` → `ConnectionTestResult{ok, message, capabilities, latency_ms}`, `POST /v1/connections/{id}/rotate {api_key, api_secret}` (bumps `credentials_version`), `POST /v1/connections/{id}/default`, `GET /v1/connections/{id}/fleet` → `FleetStatus{desired_replicas, instances: WorkerInstanceOut[], installed_provider_ids, image}`, `POST /v1/connections/{id}/fleet {action: start|stop|restart, replicas?}` (supervised only), `GET /v1/connections/{id}/worker-env?format=env|compose|lk` → redacted template for `external` mode, `POST /v1/connections/{id}/deploy-bundle` → zip (cloud_hosted), `GET/PUT /v1/connections/{id}/storage`.

Providers: `GET /v1/providers?kind=&availability=&enabled=` → `ProvidersResponse{v:2, providers: ProviderOut[]}` where `ProviderOut = ProviderSpec + {enabled, installed_on: [connection_id], default_credential_id}`; `PUT /v1/providers/{id}/settings {enabled, default_credential_id}`; `GET /v1/providers/{id}/catalog?kind=&credential_id=&refresh=` → `CatalogResponse{kind, items: CatalogItem[]{id,label,meta}, fetched_at, source: vendor|static}`.

Credentials: v1 routes + `POST /v1/credentials/{id}/test` now returns `CredentialTestResult{ok, message, checked_at, catalog_preview?}`.

Agents: v1 routes + `GET /v1/agents?connection_id=&mode=&archived=`; `POST /v1/agents/{id}/archive`, `POST /v1/agents/{id}/unarchive`, `DELETE` allowed when archived and `?purge=true` (cascades sessions); `GET /v1/agents/{id}/versions` → `Page[ConfigVersionOut]`, `GET /v1/agents/{id}/versions/{n}`, `POST /v1/agents/{id}/versions/{n}/restore`; `POST /v1/agents/{id}/validate` returns `issues[]`; `GET /v1/agents/{id}/limits`, `PUT /v1/agents/{id}/limits`.

Flows (stretch): `GET /v1/flows/node-specs` → `NodeSpecsResponse{v, nodes: NodeSpecSchema[]}` (JSON schema per node kind, exported from contracts); `POST /v1/agents/{id}/flow/validate {flow: FlowSpec}` → `ValidationResult`; the flow itself is saved via `PUT /v1/agents/{id}` (`config.flow`).

Sessions: `GET /v1/sessions?agent_id=&status=&channel=&connection_id=&from=&to=&limit=&offset=`; `GET /v1/sessions/{id}` → `SessionDetailOut` += `channel, connection_id, caller, recording{status,url,duration_s}, cost{total_usd, lines: CostLine[]}, latency, qa: QaOut|null, disposition, variables, config_version`; `GET /v1/sessions/{id}/recording` → 302 signed URL; `POST /v1/sessions/{id}/qa` (re-score); `DELETE /v1/sessions/{id}` (soft); `GET /v1/analytics/summary?range=` → `AnalyticsSummary{sessions, minutes, cost_usd, failed, by_day[], by_agent[]}`.

Test & text mode (stretch): `POST /v1/agents/{id}/text-sessions` → `ConnectResponse` with `channel=text`; `AgentAction.action` += `rewind` (`payload={turn_index}`) and `inject_user_text`.

Telephony (stretch): `GET/POST /v1/telephony/trunks`, `PUT/DELETE /v1/telephony/trunks/{id}`, `POST /v1/telephony/trunks/{id}/sync` (re-create on LiveKit), `GET/POST/DELETE /v1/telephony/dispatch-rules`, `GET/POST/PUT/DELETE /v1/telephony/numbers`, `POST /v1/calls {agent_id, to_e164, trunk_id?, variables?}` → `CallOut`, `GET /v1/calls`, `GET /v1/calls/{id}`, `POST /v1/calls/{id}/hangup`, `POST /v1/calls/{id}/transfer {to}`, `POST /v1/calls/{id}/dtmf {digits}`.

Webhooks: `GET/POST /v1/webhooks`, `PUT/DELETE /v1/webhooks/{id}`, `POST /v1/webhooks/{id}/test`, `GET /v1/webhooks/{id}/deliveries`, `POST /v1/webhooks/deliveries/{id}/redeliver`. Inbound LiveKit webhooks: `POST /hooks/livekit/{connection_id}` verified with that connection's key/secret (`WebhookReceiver`), handles `egress_ended`, `room_finished`, `participant_joined/left`, SIP events.

Internal (service token): `GET /internal/v1/sessions/{id}/resolved` (unchanged), **`POST /internal/v1/sessions/start`** `{agent_id, room_name, channel, participant_identity, caller?, dispatch_metadata}` → `ResolvedAgentConfig` (creates the row, 409 if room already has a live session), `POST /internal/v1/workers/register` `{connection_id?, instance_key, image, sdk_version, installed_provider_ids, pack_ids, managed_by}` → `{connection_id, agent_name}`, `POST /internal/v1/workers/{instance_key}/heartbeat {status, active_jobs}`, `GET /internal/v1/fleet/desired` → `FleetDesired[]`, `GET /internal/v1/connections/{id}/worker-env` → `WorkerEnv{env: dict[str,str], image, agent_name, packs}` (decrypted; supervisor only), **`PUT /internal/v1/sessions/{id}/qa`** `{status: done|failed|skipped, score?, sentiment?, tags?, summary?, raw?, model?, error?}` (worker-side judge result, R-V2-5; posted after the summary), `POST /internal/v1/sessions/{id}/metrics {latency, usage_lines}`, **`POST /internal/v1/sessions/{id}/recording/start`** (worker → api after `ctx.connect()`; api starts `RoomCompositeEgress` with the connection's client and returns `{egress_id}`), `POST /internal/v1/sessions/{id}/recording {egress_id, status, duration_s?}` (worker-side finalisation when no public webhook URL). Telephony, worker → api (R-V2-25): **`POST /internal/v1/telephony/calls/report`** `{session_id, status: answered|completed|failed, direction?, from_e164?, to_e164?, sip_call_id?, participant_identity?, reason?}` → `CallOut` (the worker's view of a SIP leg; creates or links the inbound `calls` row, fills `sessions.caller`, status moves forward only) and **`POST /internal/v1/telephony/sessions/{id}/transfer`** `{to, participant_identity?}` → `{ok, status, call_id?, reason?}` (the `transfer_call` tool's path to `SipService.transfer_sip_participant`; `to` must pass the workspace dialing policy, R-V2-23). Router ownership: `sessions/start`, `sessions/*`, `worker-env` live in `routers/internal.py` (V2-03); `workers/register`, `workers/{key}/heartbeat`, `fleet/desired` live in `routers/fleet_internal.py` (V2-04); the two telephony routes live in `routers/calls.py` (V2-17). Events and summary routes unchanged; new event types: `block_update`, `form_submitted`, `handoff` (flow node change), `sip_answered {participant_identity, from, to, call_id, trunk_id}`, `dtmf {direction: received|sent, digits, source?: console|tool}`, `transfer {to, ok, status, reason}`, `recording`. `POST /v1/calls/{id}/dtmf` reaches the worker as a reliable server-sent data packet on topic `lkap.telephony.dtmf` (`{"v":1,"op":"dtmf","digits","call_id"}`), honoured only when the packet has no sending participant; the route answers `{queued: true}` with no delivery acknowledgement.

Public API = the same `/v1` routes with an API key; `/v1/openapi-public.json` filters to routes tagged `public` (Dograh's `x-sdk-method` idea).

## 4. Contract models

### 4.1 Registry schema (`providers.py`)

```python
ProviderKind = Literal["realtime","stt","llm","tts","avatar","vad","turn_detection",
                       "noise_cancellation","image_gen","embedding","secret_bag"]
FieldType = Literal["string","secret","number","boolean","enum","json","model","file","catalog"]
Availability = Literal["available","deferred","incompatible","removed"]
Verification = Literal["verified","unverified"]
WorkerImage = Literal["slim","full","isolated"]

class FieldSpec(BaseModel):            # v1 fields +
    catalog_kind: Literal["models","voices","avatars","personas"] | None = None  # for type="catalog"
    accept: str | None = None          # for type="file", e.g. "image/*"
    positional: bool = False           # Synthesia avatar_config
    nested_model: str | None = None    # dotted class to wrap dict into (Simli SimliConfig, Anam PersonaConfig)

class CatalogSpec(BaseModel):
    adapter: str                       # "openai_models", "elevenlabs_voices", "tavus_faces", ...
    kinds: list[Literal["models","voices","avatars","personas"]]
    ttl_s: int = 3600

class ProviderCapabilities(BaseModel): # v1 fields +
    text_modality: bool = False        # realtime can emit text only (half-cascade)
    audio_input: bool = True
    languages: list[str] = []          # BCP-47, empty = unknown/many
    vision: bool | None = None         # tri-state, alias of video_input for LLMs
    voices_dynamic: bool = False       # voices come from catalog, not `voices`
    cloud_only: bool = False           # needs a Cloud connection (inference-*, krisp)
    platforms: list[str] = []          # e.g. ["linux-x86_64","linux-aarch64","macos-arm64"]

class ProviderSpec(BaseModel):
    v: Literal[2] = 2
    ... v1 fields ...
    availability: Availability = "available"
    verification: Verification = "unverified"
    verified_at: str | None = None
    verified_note: str | None = None
    worker_image: WorkerImage = "full"
    catalog: CatalogSpec | None = None
    test: str | None = None            # name of the credential test adapter
    price_ref: str | None = None       # key into pricing.PRICES
    notes: str | None = None
    @computed_field
    def status(self) -> Literal["mvp","deferred"]:  # alias for one release (R-V2-1)
        # "mvp" == constructible on the slim worker image. verification NEVER affects status:
        # it is an informational chip, not a gate.
        return "mvp" if self.availability == "available" and self.worker_image == "slim" else "deferred"
```

`REGISTRY` helpers keep their names; add `available_providers()`, `by_image(image)`, `constructible(installed_ids)`. `GEMINI_LIVE_VOICES` becomes the full 30-name list. Every v1 MVP entry (all 18, including `image_gen`/`embedding`/`secret_bag`) carries `worker_image="slim"`; every entry V2-05 adds carries `worker_image="full"`, so the alias preserves the exact v1 `mvp` set until consumers migrate (ruling R-V2-1 in PLAN §8). Consumers of `status` (unchanged in waves 0–1; migrated by V2-03 for the api and V2-13 for the web): `providers.mvp_providers()`, `providers._deferred()`, `api/config_service.validate_agent_config`, `api/packs.py` seeding, `web/src/components/console/registry/*` filter, `web/src/lib/providers.ts` (if present), `PANEL_META/CAPABILITY_META` in `web/src/components/shared`.

### 4.2 Pricing (`pricing.py`, new)

`Price{provider_id, model: str|None, unit: Unit, usd_per_unit: Decimal, source_url, as_of}`; `PRICES: list[Price]`, `PRICE_VERSION: str`; `lookup(provider_id, model, unit) -> Price | None`. Unknown prices produce a `cost_usd=None` line with `note="no price"`; never zero.

### 4.3 AgentConfig v2 (`agent_config.py`)

```python
PipelineMode = Literal["cascaded","realtime","half_cascade"]
ProviderSlot = Literal["realtime","stt","llm","tts","avatar","image_gen","workflow_llm","qa_llm",
                       "vad","turn_detection","noise_cancellation"]
# qa_llm (R-V2-6): resolved by the api from qa.model → workflow_llm → llm → Inference LLM default, present in
# ResolvedAgentConfig.resolved only when qa.enabled. The worker never resolves qa.model itself.

class AvatarOptions(BaseModel):
    participant_name: str = "Avatar"
    video_quality: Literal["low","medium","high","very_high"] | None = None
    idle_timeout_s: int | None = None
    max_duration_s: int | None = None

class PipelineConfig(BaseModel):
    mode: PipelineMode = "cascaded"
    realtime: ProviderRef | None = None
    stt: ProviderRef | None = None
    llm: ProviderRef | None = None
    tts: ProviderRef | None = None
    avatar: ProviderRef | None = None
    avatar_options: AvatarOptions = AvatarOptions()
    image_gen: ProviderRef | None = None
    workflow_llm: ProviderRef | None = None
    vad: ProviderRef | None = None                 # None → silero default
    turn_detection: ProviderRef | None = None      # None → inference TurnDetector (cascaded/half) / none (realtime)
    noise_cancellation: ProviderRef | None = None
    turn_handling: dict[str, Any] = {}
    # validation: realtime → realtime required; cascaded → stt+llm+tts; half_cascade → realtime(text_modality)+tts

class RecordingConfig(BaseModel):
    enabled: bool = False
    audio_only: bool = True
    storage_config_id: str | None = None           # None → connection/workspace default
    retention_days: int | None = None

class QaConfig(BaseModel):
    enabled: bool = False
    rubric_prompt: str | None = None               # None → platform default rubric
    model: ProviderRef | None = None               # None → workflow_llm

class PanelLayout(BaseModel):
    panel_id: str = "composite"                    # "composite" | custom registry id
    layout: Literal["side","wide"] = "side"
    blocks: list[BlockSpec] = []                   # ignored by custom panels unless they opt in

class AgentConfig(BaseModel):
    v: Literal[2] = 2
    instructions: str
    pipeline: PipelineConfig
    voice: VoiceConfig = VoiceConfig()             # += first_speaker: Literal["agent","user"]="agent"
    capabilities: CapabilitiesConfig = CapabilitiesConfig()   # += dtmf: bool=False
    tools: ToolsConfig = ToolsConfig()
    knowledge: KnowledgeConfig = KnowledgeConfig()
    panel: PanelLayout = PanelLayout()
    recording: RecordingConfig = RecordingConfig()
    qa: QaConfig = QaConfig()
    flow: FlowSpec | None = None                   # only when agents.mode == "flow"
    telephony: TelephonyConfig = TelephonyConfig() # R-V2-21: transfer_targets: [{label, to}]; contracts/telephony.py
    pack_settings: dict[str, Any] = {}
    timezone: str = "UTC"

class ConnectionInfo(BaseModel):                   # attached to ResolvedAgentConfig
    connection_id: str
    deployment_type: Literal["cloud","self_hosted"]
    capabilities: ConnectionCapabilities

class ResolvedAgentConfig(BaseModel):              # v1 fields +
    v: Literal[2] = 2
    workspace_id: str
    channel: SessionChannel
    connection: ConnectionInfo
    recording: RecordingConfig
    panel: PanelLayout
    installed_provider_ids: list[str] | None = None
    variables: dict[str, Any] = {}                 # R-V2-22: sessions.variables (an outbound call's CallCreate.variables)
```

`DispatchMetadata` v2: `{v:2, session_id: str|None, agent_id: str, config_version: int|None, participant_identity: str|None, channel: SessionChannel, connection_id: str}`. The worker: `session_id` present → `resolved`; absent → `sessions/start`.

`ConnectionCapabilities{inference_available, sip_enabled, egress_enabled, ingress_enabled, cloud_hosting, noise_cancellation_tier: Literal["none","ai_coustics","krisp"], observability_dashboard, turn_detector_mode: Literal["hosted","local"]}`.

`lkap_contracts.migrate.agent_config_v1_to_v2(cfg: dict, ui_panel_id: str) -> dict`: sets `v=2`; `ui_panel_id == "generic"` → `panel = {panel_id: "composite", blocks: [status, notes, checklist, activity]}`, any other id → `panel = {panel_id: <id>, blocks: []}`; maps `pipeline` unchanged; adds defaults. Pure, tested with the two pack manifests and every fixture in `contracts/tests`.

### 4.4 UI protocol v2 (`ui_protocol.py`)

- `UiState.v: Literal[2]`; `UiState.blocks: dict[str, Any] = {}` keyed by `BlockSpec.id`; everything else unchanged (`custom` stays for packs).
- `BlockSpec{id, type: BlockType, title: str|None, config: dict, order: int}`; `BlockType = Literal["status","notes","checklist","activity","form","document","gallery","table","transcript","video","kb_citations","custom"]`.
- Block state models (exported to TS, validated by the reducer): `FormBlockState{schema: dict (JSON schema), values: dict, status: idle|requested|submitted, submitted_at}`, `DocumentBlockState{asset_id|url, page:int, highlights:[{page, bbox, note}]}`, `GalleryBlockState{asset_ids: list[str], selected: str|None}`, `TableBlockState{columns:[{key,label,type}], rows: list[dict], selected_row: str|None}`, `TranscriptBlockState{show_tools: bool}`, `VideoBlockState{source: agent_avatar|user_camera|user_screen|track:<sid>, muted}`, `KbCitationsBlockState{items:[{chunk_id, filename, score, text}]}`; `status/notes/checklist/activity` blocks render the envelope fields and hold `{}`.
- `UiRequest.method` += `"form"` (`payload={block_id, schema, prefill}`; result `{values}` or `{cancelled: true}`), `"show_block"` (`{block_id}`), `"navigate"` (`{url}`, opens in a new tab; UI confirms). Payload keys are now fixed for the v1 methods too (R-V2-3b): `open_dialog` → `{dialog: str, params?: dict}`; `focus` → `{target: str}`; `request_video_source` → `{source: "camera"|"screen"}`; `toast` → `{message: str, tone?: Tone}`. `{"id": ...}` for `open_dialog` is wrong; the agent side changes.
- `AgentAction.action` += `"form_submit"` (`{block_id, values}`), `"block_action"` (`{block_id, name, data}` → `Pack.on_block_action`), `"rewind"`, `"inject_user_text"`.
- `UiChannel` protocol (packs.base) += `set_block(block_id, state: dict)`, `patch_block(block_id, ops)`, `request_form(block_id, schema, prefill=None, timeout_s=120) -> dict | None`, `cite(block_id, hits)`.
- Built-in tools (worker): `update_block(block_id, patch: dict)`, `show_document(asset_id|url, page?, note?)`, `table_append(block_id, row)`, `request_form(block_id, fields: list[{name,label,type,required}]) -> values` (blocking with timeout; realtime mode returns `None` and the result arrives as an urgent background result), `cite_sources` (implicit).
- Pack manifest += `default_panel: PanelLayout | None`, `blocks: list[BlockSpec]` (blocks a custom panel expects).
- **Layout delivery (R-V2-7)**: the block list is configuration, not state. `AgentPublicOut.panel: PanelLayout` and therefore `ConnectResponse.agent.panel` carry the effective layout (agent `config.panel` → pack `default_panel` → `{panel_id: "composite", blocks: []}`), resolved by the api with the same function the resolve endpoint uses (`lkap_api.panels.effective_layout(agent, pack)`) at the session's pinned `config_version`. `uiPanelId` stays as the mirror of `panel.panel_id`. `UiState` carries block **state** only; snapshots never carry the layout. `BlockSpec.config` is public by definition: it is validated against the block type's config schema (`contracts/blocks.py`), which has no secret-typed fields, and `custom` block config is pack-declared and public.
- Web `PanelDefinition` += `handleRequest?(req: UiRequest) => Promise<UiRequestResult>` and `blocksAware?: boolean`. New registry entry `composite`.

### 4.5 FlowSpec (`flow.py`, new)

```python
NodeKind = Literal["start","agent","end","global","transfer","qa"]

class VariableSpec(BaseModel):
    name: str            # ^[a-z][a-z0-9_]{0,63}$
    type: Literal["string","number","boolean","enum","date","phone","email"]
    description: str = ""
    options: list[str] | None = None
    required: bool = False

class NodeBase(BaseModel):
    id: str; kind: NodeKind; label: str; position: tuple[float, float] = (0, 0)

class StartNode(NodeBase):    kind: Literal["start"]; greeting: str | None = None; greeting_mode: Literal["say","generate"] = "say"
class GlobalNode(NodeBase):   kind: Literal["global"]; instructions: str; tools: list[str] = []; kb_ids: list[str] = []
class AgentNode(NodeBase):
    kind: Literal["agent"]
    instructions: str
    tools: list[str] = []                 # builtin names, tool ids, pack tool names
    kb_ids: list[str] = []
    extract: list[str] = []               # variable names to extract on exit
    allow_interruptions: bool | None = None
    providers: dict[Literal["llm","tts"], ProviderRef] = {}   # cascaded mode only
    max_turns: int | None = None
class EndNode(NodeBase):      kind: Literal["end"]; farewell: str | None = None; disposition: str | None = None; webhook_event: bool = True
class TransferNode(NodeBase): kind: Literal["transfer"]; to: str; mode: Literal["cold","warm"] = "cold"; announce: str | None = None
class QaNode(NodeBase):       kind: Literal["qa"]; rubric_prompt: str | None = None

FlowNode = Annotated[StartNode|AgentNode|EndNode|GlobalNode|TransferNode|QaNode, Field(discriminator="kind")]

class FlowEdge(BaseModel):
    id: str; source: str; target: str
    condition: str                        # natural language; becomes the tool description
    label: str | None = None
    transition_speech: str | None = None
    priority: int = 0

class FlowSpec(BaseModel):
    v: Literal[1] = 1
    nodes: list[FlowNode]
    edges: list[FlowEdge]
    variables: list[VariableSpec] = []
    # validation: exactly one start; ≤1 global; every agent node reachable from start; every edge source/target exists;
    # no edges from/to global or qa; end nodes have no outgoing edges; tool/kb refs resolvable at api validation time.
```

Runtime contract: edge tool name `go_to_{target_id}` (ids are `^[a-z][a-z0-9_]{0,31}$`), `FlowState{current_node, path: list[str], variables: dict, disposition: str|None}` stored in `session.userdata.flow`, `handoff` session event `{from, to, edge_id}`; mirrored into **every** `custom` block whose `config.kind == "flow_progress"`, under that block's own id (`UiState.blocks[<block id>]`), as `{current_node, label, path, variables, disposition}` (R-V2-14). A dedicated renderer is Phase 2; the custom block shows the JSON today.

### 4.6 Sessions, calls, webhooks models (api_models.py)

`SessionChannel = Literal["web","test","text","sip_in","sip_out","widget","api"]`; `SessionLatency{eou_to_first_audio_ms_p50, _p95, llm_ttft_ms_p50, _p95, tts_ttfb_ms_p50, _p95, turns}`; `CostLine{provider_id, model, unit, quantity, unit_price_usd, cost_usd, note}`; `QaOut{status, score, sentiment, tags, summary, scored_at, model}`; `RecordingOut{status, url, duration_s, expires_at}`; `CallCreate{agent_id, to_e164, trunk_id?, variables?, timeout_s=30}` (`variables` reach the worker as `ResolvedAgentConfig.variables`, R-V2-22; `to_e164` must pass the workspace dialing policy in `workspaces.settings["telephony"]{allowed_prefixes, allowed_sip_hosts, max_calls_per_min, max_concurrent_outbound}`, R-V2-23), `CallOut{...calls columns..., session_id}`; `TrunkCreate/TrunkUpdate/TrunkOut`, `DispatchRuleCreate/DispatchRuleOut`, `PhoneNumberCreate/PhoneNumberUpdate/PhoneNumberOut`, `CallTransferIn{to}`, `CallDtmfIn{digits}/CallDtmfOut`, `CallReportIn`, `InternalTransferIn/Out` as V2-17 defined them in `api/telephony/models.py` (promoted here by R-V2-25); `lkap_contracts/telephony.py`: `E164_PATTERN`, `TRANSFER_TARGET_PATTERN`, `DTMF_PATTERN`, `TransferTarget{label, to}`, `TelephonyConfig{transfer_targets}` (R-V2-21); `WebhookEndpointCreate{url (https only outside dev), events, description}`, `WebhookEndpointOut{... , secret_prefix}`, `WebhookDeliveryOut`; outbound payload envelope `{id, type, created_at, workspace_id, data}` signed with `X-LKAP-Signature: t=<unix>,v1=<hmac_sha256(secret, f"{t}.{body}")>` and `X-LKAP-Event-Id`.

---
## 5. Worker supervisor interface

Package `supervisor/` → `lkap_supervisor` (own `pyproject.toml`, editable dep on `lkap-contracts`; no dependency on `lkap_agent` or `lkap_api`). Entry: `python -m lkap_supervisor` (`--once` for tests).

```python
class FleetDesired(BaseModel):            # contracts/fleet.py
    connection_id: str
    agent_name: str
    desired_replicas: int                 # 0 = stopped
    restart_generation: int = 0           # bumped by fleet {action: restart}; part of desired_hash (R-V2-4)
    desired_hash: str
    image: Literal["slim","full"]
    packs: list[str]
    deployment_mode: Literal["supervised"] # only supervised rows are returned

class WorkerEnv(BaseModel):
    env: dict[str, str]                   # LIVEKIT_URL/API_KEY/API_SECRET, LKAP_AGENT_NAME, LKAP_CONNECTION_ID,
                                          # LKAP_API_BASE_URL, LKAP_SERVICE_TOKEN, LKAP_PACKS, LKAP_* tuning, OTEL_*
    image: str
    agent_name: str

class ReplicaHandle(BaseModel):
    connection_id: str; replica_index: int; instance_key: str; desired_hash: str
    state: Literal["starting","running","draining","stopped","failed"]; started_at: float; pid_or_container: str

class Backend(Protocol):                  # lkap_supervisor/backends/base.py
    async def list(self) -> list[ReplicaHandle]: ...
    async def start(self, desired: FleetDesired, index: int, env: WorkerEnv) -> ReplicaHandle: ...
    async def drain(self, handle: ReplicaHandle, grace_s: float) -> None: ...   # SIGINT then wait then SIGKILL
    async def health(self, handle: ReplicaHandle) -> bool: ...
```

Reconcile loop (every `LKAP_SUPERVISOR_INTERVAL_S`, default 10 s): fetch `FleetDesired[]`; for each connection compare running handles by `desired_hash`; start missing replicas (fetch `WorkerEnv` just-in-time, never cache secrets longer than the call), drain extras and stale-hash replicas one at a time (rolling), restart failed ones with exponential backoff (max 5 min), mark handles in `worker_instances` via the api. Single-instance guard: a Redis lease `lkap:supervisor:lease` when `LKAP_REDIS_URL` is set; otherwise a file lock (dev). Backends: `SubprocessBackend` (`python -m lkap_agent.main start`, cwd `agent/`, uses the current venv), `DockerBackend` (image `lkap-agent:<flavor>-<tag>`, labels `lkap.connection_id`, `lkap.hash`, `stop_grace_period` = drain, env via container config only). Metrics on `:9105/metrics`: `lkap_supervisor_replicas{connection,state}`, `lkap_supervisor_reconcile_seconds`, `lkap_supervisor_restarts_total`.

Worker side (V2-07): on `prewarm` read `LKAP_CONNECTION_ID` (optional) and `LKAP_AGENT_NAME` (replaces the hardcoded `REQUIRED_AGENT_NAME`; default `lkap-agent`; the D-W2-11 job filter now compares against this value); after `AgentServer` registers, `POST /internal/v1/workers/register`; heartbeat task every 30 s; on SIGINT set status `draining`.

## 6. Environment variables (new or changed)

| Variable | Used by | Notes |
|---|---|---|
| `LKAP_ENV` | all | `dev` \| `prod`. In `prod`: `dev-*` tokens refused, cookies Secure, `LKAP_ALLOW_ADMIN_TOKEN` defaults false, local storage refused. |
| `LIVEKIT_URL/API_KEY/API_SECRET` | api (bootstrap only), worker (external mode) | api uses them only to create the default connection; after that per-connection secrets come from the DB. |
| `LKAP_ALLOW_ADMIN_TOKEN` | api | Break-glass `X-Admin-Token`; default true in dev, false in prod. |
| `LKAP_SESSION_SECRET` | api | ≥32 bytes; signs cookie sessions and invite tokens. |
| `LKAP_BOOTSTRAP_OWNER_EMAIL` / `LKAP_BOOTSTRAP_OWNER_PASSWORD` | api | First owner; if the password is unset a random one is printed once to the log. |
| `LKAP_REDIS_URL` | api, supervisor | Optional. Enables arq, shared rate limits, catalog cache, supervisor lease. |
| `LKAP_JOBS_BACKEND` | api | `inline` (default in dev) \| `arq` (requires Redis). |
| `LKAP_STORAGE_KIND`, `LKAP_STORAGE_BUCKET`, `LKAP_STORAGE_ENDPOINT_URL`, `LKAP_STORAGE_REGION`, `LKAP_STORAGE_ACCESS_KEY`, `LKAP_STORAGE_SECRET_KEY`, `LKAP_STORAGE_PUBLIC_BASE_URL` | api | Platform default storage (`local` in dev → `LKAP_DATA_DIR/storage`). Workspace `storage_configs` override. |
| `LKAP_CONNECTION_ID` | worker | Set by the supervisor; unset ⇒ attributed to the default connection. |
| `LKAP_AGENT_NAME` | worker, api | Worker: its own agent name (default `lkap-agent`); api: no longer used for dispatch (connection.agent_name is). |
| `LKAP_SUPERVISOR_BACKEND` | supervisor | `subprocess` \| `docker`. |
| `LKAP_SUPERVISOR_INTERVAL_S`, `LKAP_SUPERVISOR_DRAIN_S` | supervisor | 10, 3600 (start mode drain per D-W2-13). |
| `LKAP_AGENT_IMAGE_SLIM`, `LKAP_AGENT_IMAGE_FULL` | supervisor (docker) | Image refs. |
| `LKAP_PUBLIC_BASE_URL` | api | Now required in prod: webhook receiver URLs, deploy bundles (`LKAP_API_BASE_URL` for cloud-hosted workers), signed recording URLs. |
| `LKAP_WEB_BASE_URL` | api | For widget snippet and invite links. |
| `LKAP_RATE_LIMIT_ENABLED` | api | Default true. |
| `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_SERVICE_NAME` | api, worker, supervisor | Standard OTel env; LiveKit 1.8 GenAI spans on the worker. |
| `LKAP_PRICE_VERSION_OVERRIDE` | api | Testing only. |
| `LKAP_WIDGET_ENABLED` | api, web | Stretch. |

Removed/demoted: `LKAP_ADMIN_TOKEN` (break-glass only), `LKAP_BOOTSTRAP_CREDENTIALS_JSON` (kept, now scoped to the default workspace).

Dev secrets stay in the workspace-level `.claude/launch.json` (outside the repo; Claude agents cannot read `.env*`). Production: env from the host secret manager via compose `env_file` or the orchestrator; no secret is ever written by the platform to disk except Fernet ciphertext in the DB.

## 7. Worker images and installed-provider manifest

`agent/Dockerfile` gains `ARG LKAP_IMAGE_FLAVOR=slim`. `agent/requirements/{slim,full}.txt` are generated by `scripts/gen_plugin_requirements.py` from the registry (`worker_image` field), pinned to `livekit-agents==1.8.2` lockstep, minus `availability in {incompatible, removed, deferred}`. Build step runs `python scripts/check_installed_providers.py --flavor $LKAP_IMAGE_FLAVOR --out /app/installed_providers.json`, which imports every `python_class` and fails the build on any ImportError (native wheel problems surface here, not in production). The worker reads that file at start and sends it in `workers/register`. `slim` = v1 set + silero + turn-detector + inference; `full` = everything else that resolves (catalog §4.1), Python 3.12, `python:3.12-bookworm-slim` (glibc 2.36 ≥ manylinux_2_28 for bithuman/Krisp), linux/amd64 + linux/arm64.

---
