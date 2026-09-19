# v2 decision sketch (architect working notes, pre-review)

## Topology
- Fleet = **one worker pool per LiveKitConnection**, not per (connection, agent). v1 already has one agent name serving every agent (config fetched per job via DispatchMetadata.session_id). So N connections = N pools; an agent is *bound* to a connection (`agents.connection_id`), never "deployed" separately. Agent name per connection (`connections.agent_name`, default `lkap-agent`) to avoid clashes inside shared projects (other-project-agent lives in the current one).
- New service **`supervisor/` (`lkap_supervisor`)**: reconciliation loop; desired state from api (`GET /internal/v1/fleet/desired`), backends `subprocess` (dev, single host) and `docker` (prod containers). K8s deferred. Creds fetched just-in-time from api over service token, injected as child env, never written to disk. Credential rotation = `connections.credentials_version` bump → drain (SIGINT, grace) + restart.
- Connection `deployment_mode`: `external` (someone else runs the worker; we only show env), `supervised` (our supervisor), `cloud_hosted` (LiveKit Cloud via `lk agent deploy`; Phase 1 = bundle generator + documented CLI steps, Phase 2 = automated).
- api `connect`: agent → connection → mint token with that connection's key/secret + RoomAgentDispatch(connection.agent_name) → `serverUrl=connection.url`. No frontend change.
- v1 migration: on startup, if `livekit_connections` empty and `LIVEKIT_URL/KEY/SECRET` set → create `default` connection (mode `external`, encrypted with the vault); `agents.connection_id` backfilled. Running launch.json worker keeps serving. Flip to `supervised` later.

## Providers
- `ProviderKind` += vad, turn_detection, noise_cancellation. `FieldType` += file, catalog.
- `ProviderSpec.status` → `availability: available|deferred|incompatible` + `verification: verified|unverified` + `worker_image: default|slim|minimax-isolated`. Hedra excluded; MiniMax `incompatible`; PlayAI deferred.
- Images: `slim` (v1 set, dev) and `full` (all 1.8.2-lockstep plugins that resolve). Worker reports installed provider ids on heartbeat → api marks `installed` per connection pool → UI greys out.
- Workspace enable/disable: `workspace_providers(workspace_id, provider_id, enabled, default_credential_id)`.
- Catalog fetchers: `GET /v1/providers/{id}/catalog?credential_id=&kind=models|voices|avatars`, vendor adapters in api, Redis/DB cache 1h.
- Pipeline modes: cascaded | realtime | half_cascade (realtime text modality + TTS).
- Verified in MVP = what a live test passed on: v1 set + whatever keys the user supplies.

## Avatars
- All 15 live plugins registered. Pickers: vendors with confirmed list APIs (bey, tavus, simli, anam, d-id, liveavatar) get `catalog` fields; others paste-id. Verified MVP: bey, tavus (+ simli/anam if keys).

## Panels
- Schema-driven **blocks**: PanelLayout{blocks:[{id,type,title,config}]}; UiState gains `blocks: dict[id, BlockState]`. Block types MVP: status, notes, checklist, form, document, gallery, table, transcript, video, kb_citations, activity, custom. Whiteboard Phase 2.
- UiChannel += block ops + form request/response RPC; built-in tools `update_block`, `show_document`, `table_append`, `request_form`. Custom React panels stay (registry unchanged) and can embed blocks.

## Flow builder
- Adopt. Node = `FlowNodeAgent(PlatformAgent)`; edges = generated transition tools (LLM-evaluated conditions); handoff = tool returns new Agent; global node = shared prefix; variables in `session.userdata.flow` with per-node extraction schema via structured LLM; end node disposition. `AgentTask` sub-flows Phase 2. Single `FlowSpec` JSON schema drives UI/API/MCP. `agents.mode: prompt|flow`; prompt stays default.

## Telephony
- Phase 1: per-connection SIP trunks (in/out), dispatch rules, number→agent, outbound single call, DTMF send/receive, cold transfer (REFER). Phase 2: campaigns, AMD, warm transfer, voicemail.

## Evals
- Phase 1: text test mode (audio-less room session) with rewind via replay history; post-call QA scoring job. Phase 2: simulated caller + judge, regression suites.

## Observability
- Sessions += connection_id, channel, cost, latency; recordings via Egress (audio-only room composite) to per-connection S3 config; price table in repo; webhooks durable via jobs; OTel env-configured (LiveKit 1.8 native), Langfuse UI Phase 2.

## Distribution
- Phase 1: widget (floating/inline iframe + postMessage), public REST API with API keys. Phase 2: headless widget, MCP server.

## Production
- Workspaces/users/roles (owner, admin, builder, viewer), API keys (hash+prefix), email/password + cookie session (Phase 1), OIDC Phase 2. Rate limiting (Redis). Postgres prod / SQLite dev. Jobs: **arq + Redis**. Storage: S3-compatible (MinIO dev). CI: GitHub Actions. Secrets: env from secret manager; `.claude/launch.json` remains dev-only.
