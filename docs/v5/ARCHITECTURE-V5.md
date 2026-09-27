# LKAP v5 — Architecture: what v5 changed

Status: **as built** (V5-46, 2026-09-28), describing `main` at `86d0f3b`, where every V5 package is merged. This document explains what v5 added and how the parts fit. It does not repeat field lists: exact shapes are in `docs/CONTRACTS.md` (and the generated `contracts/generated/**`), operating steps in `docs/RUNBOOK.md` (§9.2–§9.7), the plan and its rulings in [`PLAN-V5.md`](PLAN-V5.md), the Composio design in [`COMPOSIO.md`](COMPOSIO.md), and the security review in [`SECURITY-REVIEW-V5.md`](SECURITY-REVIEW-V5.md). Where the plan and the code differ, this document follows the code and says so.

The v1 architecture ([`../ARCHITECTURE.md`](../ARCHITECTURE.md)) and the v2 architecture ([`../v2/ARCHITECTURE-V2.md`](../v2/ARCHITECTURE-V2.md)) still hold for everything v5 did not change: the three services (`api/`, `agent/` — the LiveKit worker, now on `livekit-agents` 1.8.3 — and `web/`), `contracts/` as the single source of shapes, packs, connections and worker pools, the vault, and the agent ↔ UI protocol.

Scope of v5 in one line: the three v4 research documents (`docs/research-v4/`) turned into 53 packages — conversation quality, knowledge, tools and integrations (Composio first), panel blocks, capabilities, memory — plus a security review that gated the second half. Local use only; there is no production deployment.

## 1. What changed, at a glance

| Area | Packages | Main code | Section |
|---|---|---|---|
| Knowledge: ingest, search, evaluation, stores, connections, managed search | V5-01, V5-04, V5-05, V5-06, V5-10, V5-13, V5-20, V5-24, V5-45 | `api/src/lkap_api/kb/**`, `api/src/lkap_api/knowledge_connections/**`, `agent/src/lkap_agent/knowledge.py` | §2 |
| Connected apps (Composio) | V5-18, V5-22, V5-47 … V5-50, V5-53, V5-54 | `api/src/lkap_api/tool_providers/**`, `contracts/src/lkap_contracts/tool_providers.py`, `agent/src/lkap_agent/tools/provider.py` | §3 |
| MCP servers: auth, host policy, sign-in | V5-09, V5-14, V5-16, V5-21 | `api/src/lkap_api/mcp_oauth/**`, `api/src/lkap_api/routers/tools.py`, `agent/src/lkap_agent/tools/{mcp_client,mcp_auth}.py` | §4 |
| Curated built-in tools, the untrusted-content fence | V5-25, V5-28, V5-27 (R-V5-15) | `agent/src/lkap_agent/tools/builtin/**`, `agent/src/lkap_agent/tools/untrusted.py` | §5 |
| Request blocks, caller files | V5-02, V5-03, V5-08, V5-12, V5-19, V5-23 | `agent/src/lkap_agent/ui/**`, `api/src/lkap_api/session_assets/**`, `web/src/panels/**` | §6 |
| Consent and disclosure | V5-15, V5-17 | `contracts/src/lkap_contracts/compliance.py`, the recordings and compliance code | §7 |
| Privacy and post-call fields | V5-30, V5-34 | `api/src/lkap_api/privacy/**`, `agent/src/lkap_agent/qa.py`, `observability.py` | §8 |
| Conversation tuning, languages and captions | V5-07, V5-11, V5-31, V5-35 | `contracts/src/lkap_contracts/turn_handling.py`, `agent/src/lkap_agent/{session_builder,languages,platform_agent}.py` | §9 |
| Tests and the publish gate | V5-29, V5-33 | `api/src/lkap_api/agent_tests/**` | §10 |
| Telephony: answering machines, warm transfer | V5-32, V5-36 | `agent/src/lkap_agent/telephony.py`, `contracts/src/lkap_contracts/telephony.py` | §11 |
| Supervisor listen-in | V5-37, V5-38 | `api/src/lkap_api/routers/sessions.py`, the supervisor topic on the worker | §12 |
| Guardrails | V5-39, V5-41 | `agent/src/lkap_agent/guardrails.py`, `contracts/src/lkap_contracts/agent_config.py` (`GuardrailsConfig`) | §13 |
| Caller memory | V5-40, V5-42 | `api/src/lkap_api/memory/**` | §14 |
| Panel blocks `link`, `slots`, `cards`; `describe_panel`; the AG-UI adapter | V5-43, V5-44 | `contracts/src/lkap_contracts/{ui_protocol,ui_agui}.py`, `agent/src/lkap_agent/tools/builtin/describe_panel.py` | §15 |
| Current date and time in the caller's timezone | V5-51, V5-52 | `agent/src/lkap_agent/locale.py`, `contracts/src/lkap_contracts/common.py` | §16 |
| Security review and hardening | V5-26, V5-27 | `docs/v5/SECURITY-REVIEW-V5.md` | §17 |
| Data model, settings, dependencies | all | `api/alembic/versions/v5_*.py`, `api/src/lkap_api/settings.py` | §18 |
| Decisions and rulings | — | `PLAN-V5.md` §1 and §7, `COMPOSIO.md` §2 | §19 |
| Known gaps at close | — | `_asks.md` "Open at close", `SECURITY-REVIEW-V5.md` §7.2 | §20 |

Every dev-stack live check except three (the knowledge baseline, the re-rank latency measurement and a scratch-Postgres run) is still deferred; `PLAN-V5.md` §5.1 lists them with what unblocks each. Everything below is verified against the code and its offline tests, not against a live vendor.

## 2. Knowledge service

v1 had one path: 800-character windows, one embedder, LanceDB, cosine top-k. v5 keeps the shape — the api owns ingestion and search, the worker asks `POST /internal/v1/kb/search` — and rebuilds each stage.

**Ingest (V5-01).** `kb/ingest.py` extracts PDF with pypdf (page spans kept), DOCX, PPTX, XLSX and HTML through MarkItDown into Markdown, and everything else as UTF-8 text. The chunker treats headings and pages as hard boundaries and packs about 256 tokens with 32 overlap (configurable per knowledge base, 32–512). Each `KbChunk.meta` carries locators: `filename`, `heading_path`, `page`, `char_start`, `char_end`. The embedded text is the heading path joined with " › " followed by the chunk, a cheap form of contextual chunking. The embedder is still one per process (`LKAP_EMBEDDER`, fastembed `LKAP_EMBED_MODEL` by default), but each knowledge base now records the model and width that built it (`knowledge_bases.embedder_model`, `.dimension`), and a search from another model is refused with `422 kb_embedder_mismatch`. Ingestion runs as the `kb_ingest` job and reports `kb_documents.progress`.

**Search (V5-04).** `KnowledgeService.search` (`kb/service.py`) runs, per request: scope and embedder checks; one query embedding through an LRU cache keyed by model, width and the normalised query; a parallel fan-out of the vector search per knowledge base (2 s each) and the lexical search (SQLite FTS5 `kb_chunks_fts` or Postgres `kb_chunks.tsv`, from `v5_001`); reciprocal-rank fusion (k = 60) of the two candidate lists of 20; an optional local cross-encoder re-rank (`LKAP_RERANK_MODEL`, pool of 20); and a `min_score` floor. Hits carry their locators and `score_source` (`vector`, `fused`, `rerank`, `external`); problems come back as warnings (`kb_timeout`, `kb_embedder_mismatch`, `lexical_unavailable`, `rerank_failed`, `rerank_refused`, …) rather than errors. Deleting a knowledge base or a document deletes its SQL rows, commits, then enqueues `kb_delete`, so the job runner is the only writer of vector files (the single-writer rule of D-V5-12).

**Evaluation (V5-05).** Golden questions per knowledge base (`kb_evals`, `GET/PUT /v1/knowledge-bases/{id}/evals`) and the `kb_evaluate` job (`POST …/evaluate`, `GET …/evaluate/latest`, `GET …/evaluate/{job_id}`) report recall@k, recall@1 and MRR, overall and by tag; the result lives in the job row's payload. The seed baseline is [`KNOWLEDGE-BASELINE.md`](KNOWLEDGE-BASELINE.md).

**On the worker (V5-06).** `KnowledgeConfig` gained `mode` (`hybrid` by default), `rerank`, `min_score`, `prefetch`, `query_mode` (`conversation` by default: the caller's turn plus the agent's previous sentence and flow variables), `max_inject_tokens` and `skip_short_turns`. Automatic injection (`PlatformAgent.on_user_turn_completed`) skips empty, backchannel, digits-only and under-three-word turns, uses a pre-fetch started on interim transcripts (300 ms debounce; reused when the final query is close enough) or searches within 0.4 s, fences the passages (§5) and adds them as an assistant message for that turn only; a `knowledge` session event records hits, tokens and whether the pre-fetch was ready. Cross-turn dedupe is off (R-V5-11) and preemptive generation stays off for auto-inject agents (R-V5-12). The `search_knowledge` tool uses the same search without the gate or the budget.

**Stores (V5-13).** `VectorStore` v2 (`kb/store.py`) adds capabilities (`hybrid`, `filters`, `stores_text`, `namespaces`), namespaces, `optimize` and `health`. The default store follows the database: `PgVectorStore` (the `kb_vectors` table of `v5_003`, one HNSW index per knowledge base, written in the same transaction as the chunk rows) on Postgres, LanceDB on SQLite, overridable with `LKAP_VECTOR_STORE`. A store that fuses natively makes the service skip its own lexical stage. The `kb_reindex` job re-embeds stored chunk rows into the current store (RUNBOOK §9.2).

**Knowledge connections (V5-20, V5-24).** A workspace can register its own vector stores (Qdrant, Pinecone, Weaviate) and hosted re-rankers (Cohere, Voyage AI) under `/v1/knowledge-connections` (reads: `builder` + `providers:read`; writes and tests: `admin` + `providers:write`). A knowledge base chooses its store at creation (`KbCreate.connection_id`); `KbStoreRouter` sends each call to the right store by knowledge base and fails closed. Vendors are reached with plain HTTP through the outbound network guard (no vendor SDK, no extra to install). Whatever the store, the SQL chunk row stays the source of truth for chunk text (D-V5-37). A hosted re-ranker is named as `rerank = "connection:<id>"`; it is refused for automatic injection (D-V5-19) and each use returns a `rerank_usage` line. Moving a knowledge base between the platform's store and a connection is the `kb_reindex` job with a destination, reachable from the CLI only (RUNBOOK §9.4).

**Managed search (V5-45).** A knowledge base of kind `external` points at a managed search service through the `ExternalRetriever` Protocol (`kb/external/`); Ragie is the first (`POST /retrievals`, 3 s request timeout, 3.5 s budget). Its documents live at the vendor, so upload, import and re-index answer 409. Its hits are merged with the pipeline's by rank.

```
upload ─► POST /v1/knowledge-bases/{id}/documents ─► storage ─► job kb_ingest
          ─► extract (pypdf | MarkItDown | text) ─► chunk with locators ─► embed ─► KbStoreRouter ─► store.upsert

caller turn (worker) ─► gate ─► pre-fetch hit, or POST /internal/v1/kb/search (0.4 s)
          api: KnowledgeService.search
             ├─ embed once (cache)
             ├─ vector fan-out per KB (platform store or connection) ┐
             ├─ lexical (FTS5 | tsvector)                            ├─► RRF ─► re-rank ─► min_score
             └─ external KBs (ExternalRetriever) ─────────── merged by rank ┘
          ◄─ hits with locators ─► fence ─► assistant note for this turn
```

Gaps at close (asks #181, #326): the worker does not send `purpose` and maps any `connection:<id>` re-rank to `none`, so a hosted re-ranker is reachable only from the console's test search, evaluations and MCP, and the api's "automatic injection skips managed search" rule (ask #236) does not take effect for worker traffic.

## 3. Connected apps (Composio)

The design is [`COMPOSIO.md`](COMPOSIO.md) (D-V5-C1 … C13, amended by R-V5-6, R-V5-9, R-V5-13 and R-V5-16). In the console the feature is called "Apps".

**The key and the catalogue (V5-18, V5-22).** `composio` is a registry entry of kind `tool_provider`; its API key is one vault credential per workspace, entered through Tools → Apps → Enable Composio or through Keys (D-V5-C13). `POST /v1/tool-providers/composio/key/test` checks a pasted key without storing it. `ComposioAdapter` (`api/src/lkap_api/tool_providers/composio.py`) is the only code that talks to Composio's REST API (through the outbound network guard, 10 s timeout); tests use a fake. The toolkit and action catalogue is cached for ten minutes; each action is classified `read`, `write` or `destructive` by `action_risk` (vendor tags first, then a marker list in the slug; R-V5-16).

**Connecting an app.** `POST /v1/tool-providers/composio/connections` (admin + `providers:write`) uses Composio-managed sign-in, the builder's own OAuth client, an API key, or nothing, per the toolkit's options (D-V5-C4). For a sign-in the api creates the connection row first, status `initiated`, holding only the SHA-256 of a 32-byte nonce, and hands the browser Composio's link with the return address `{LKAP_PUBLIC_BASE_URL}/v1/tool-providers/composio/callback?flow=<row id>.<nonce>`. The unauthenticated callback checks, in order: the row, the nonce in constant time, a single-use claim, the expiry (at most ten minutes), `status=success`, the `connected_account_id` in constant time, and then asks Composio that the account belongs to the expected subject and is `ACTIVE`; only then does the row become `active`. It redirects to the console with `connect=ok|error` and nothing else. A connection is a `credentials` row with the reserved provider id `tool-provider-account` that holds references only (toolkit, subject `ws:<id>` or `agent:<id>`, auth config, connected account, status, picked actions, label, default flag). LKAP stores no third-party token (D-V5-C3); a key or client secret typed into the Connect dialog is forwarded once and dropped. A status check that finds a connection broken pauses its tools and resumes them when it recovers; disconnect deletes the account at Composio and pauses the tools.

**Several accounts of one app (V5-53, V5-54, R-V5-13).** A subject may hold several accounts of one app, each with a `label` (up to 40 characters), exactly one `is_default`, and a Composio `alias` derived from the label; `AppsMode.accounts` chooses which accounts an agent's session may use (up to five per app, 20 apps; empty = the default account), and a Tool Router session is created with `multi_account` only when an app has more than one chosen account. The default account's tools are named `<toolkit>_<action>`; another account's are `<toolkit>_<action>__<label>`, and every description starts with "(<label>) " when the app has more than one account. Each provider tool is pinned to its account's `connected_account_id`.

**Apps on agents (V5-47 … V5-50).** `ToolsConfig.apps: AppsMode` has four modes:

- `actions` — picked actions become tools of kind `provider` (`ProviderToolDefinition`; migration `v5_010`): pinned parameters, a one-sentence description, `max_result_chars` 1500, `result_path` `data`, and an execution policy (reads `auto` with a spoken filler; writes and destructive actions `blocking` and not cancellable; 20 s). The worker (`agent/src/lkap_agent/tools/provider.py`) posts to Composio's execute endpoint through the guarded transport, turns auth failures into "This app needs to be reconnected by an admin" and a `tool_needs_reauth` event, strips URLs from errors, and fences the result as `app:<toolkit>`.
- `server` ("app server") and `router` ("tool finder") — the api, never the worker, provisions a Composio Tool Router session at agent save (`AppsProvisioner.on_save`, `tool_providers/provisioning.py`) and keeps one agent-owned MCP tool row pointing at it (named `composio_app_server` or `composio_tool_finder`, with `origin {provider, kind: server|router, remote_id, config_hash}`). The app server preloads the picked, non-denied actions with search off; the tool finder exposes the search and execute meta tools with each app's denied actions disabled. The session is reused while its configuration hash and key are unchanged and replaced otherwise; deleting the agent or turning the mode off deletes it. Both always send `manage_connections` off (S5-41) and the workbench off.
- `off` — the default.

Destructive actions are denied unless reviewed: `effective_denied_actions = denied_actions ∪ (destructive actions in scope − reviewed_actions)` (R-V5-9), applied at provisioning and again when a session is resolved. For a tool finder the scope is every destructive action in the apps' catalogues, scanned up to 40 pages; a scan that is still truncated refuses the save (422) instead of provisioning a partial deny list (R-V5-16). Turning the mode to `server` or `router`, reviewing a destructive action, or naming accounts needs `admin` + `providers:write` even through an agent save (S5-40); `router.manage_connections` is refused for everyone until the Composio live check (S5-41, ask #172).

```
Console ─POST /connections─► api ─link─► Composio ─► vendor consent ─► GET /callback?flow=<row>.<nonce>
        api: nonce, claim, account check with Composio ─► connection row `active`
Console ─POST /materialise─► provider tool rows (pinned schema, account) ─► agent tools
Agent save ─► provisioning: destructive scan ─► Tool Router session ─► agent-owned MCP row
Session ─► /resolved: deny list applied, key substituted ─► worker
          provider tool ─► Composio execute ─► fence("app:<toolkit>")
          MCP row ─► guarded MCP client ─► Tool Router session
```

Gaps at close: the multi-account prompt line the api composes (`SessionPlan.account_line`) has no delivery path to the model (ask #84); Composio cost lines are not counted (ask #24); `tool_output` guardrails do not see app-server and tool-finder results, which arrive over MCP (ask #277). `COMPOSIO.md` differs from the code in a few places (ask #330).

## 4. MCP servers: auth, host policy and sign-in

**Definitions (V5-09).** `McpServerDefinition.auth` is a union on `kind`: `none`, `header` (headers that may use `{{ secret.NAME }}` from an `http-tool-secret` key) and `oauth`. The older top-level `headers` and `credential_id` fold into header auth. `POST /v1/tools/{id}/test` (builder + `agents:write`) connects once — after substituting secrets and re-checking the real url — lists the server's tools within 15 s and stores them as `cached_tools` / `cached_at` for the console. The worker wraps the SDK's MCP client in `GuardedMCPServerHTTP` (no redirects, the guarded transport) and fences results as `mcp:<server>`; `agent/tests/unit/test_sdk_tripwires.py` pins the SDK facts that wrapper relies on (D-V5-11).

**Host policy (D-V5-4).** `LKAP_MCP_ALLOWED_HOSTS` is read by the api (at save and test, `net_guard.McpPolicy`) and by the worker (at connect, `declarative.check_mcp_host`). Empty = any public `https` host that passes the network guard; a list = a ceiling; `@http` = reuse `LKAP_HTTP_TOOL_ALLOWED_HOSTS` (an empty list then allows nothing). Plain `http` is accepted only for a loopback host in `LKAP_ENV=dev`, on the api side. App servers must be `https` on the Composio host. A refused server is skipped for the session, not fatal.

**Sign-in (V5-14, V5-16, R-V5-14).** For `auth.kind = "oauth"` an admin calls `POST /v1/tools/{id}/oauth/start` (admin + `providers:write`). The api discovers the authorization server from the MCP server (protected-resource metadata, then authorization-server metadata; PKCE S256 required); chooses a client — a pre-registered one, a stored dynamically registered one, the client metadata document when `LKAP_PUBLIC_BASE_URL` is a public `https` origin, a new dynamic registration, or else it answers "needs client registration" with the return address; and stores a ten-minute flow row keyed by the hash of `state`, with the PKCE verifier and a browser-binder hash in the vault. The start sets the `lkap_mcp_oauth` cookie. `GET /v1/oauth/mcp/callback` (unauthenticated, rate limited) claims the flow once, checks the expiry, the binder cookie (unless an API key started the flow or `LKAP_MCP_OAUTH_ALLOW_UNBOUND` is on) and the RFC 9207 `iss`, then exchanges the code and writes an `mcp-oauth` credential. Migration `v5_004` adds `mcp_oauth_flows` and `mcp_oauth_clients`. `GET …/oauth/status` and `POST …/oauth/revoke` complete the set.

At session time the refresh token never leaves the api. `/resolved` carries `ResolvedAgentConfig.mcp_oauth` (per server, a short-lived access token when one could be had). The worker's `ApiIssuedBearer` transport calls `POST /internal/v1/tools/{tool_id}/oauth/token` (service token; only for a live session whose agent uses the tool) when the token runs out or the server answers 401. The api refreshes under a lock (in process, in Redis when configured, and on the row); `invalid_grant` marks the sign-in `needs_reauth`, writes an audit row and sends the `tool.needs_reauth` webhook. When no token can be had the transport answers the MCP client itself, so the call fails with a plain sentence and the connection survives. Deleting the tool or the credential revokes the grant at the provider and deletes a dynamically registered client that no other tool uses.

```
Console ─POST /v1/tools/{id}/oauth/start─► discovery ─► client choice ─► flow row + binder cookie
Browser ─► provider consent ─► GET /v1/oauth/mcp/callback (state, code, iss, cookie)
        ─► claim ─► checks ─► token exchange ─► mcp-oauth credential (vault)
Session ─► /resolved: mcp_oauth[{tool, url, access token}] ─► worker GuardedMCPServerHTTP + ApiIssuedBearer
        401 or expiry ─► POST /internal/v1/tools/{id}/oauth/token ─► refresh under lock ─► new token | needs_reauth
```

## 5. Built-in tools and the untrusted-content fence

**Curated built-ins (V5-25, V5-28).** New built-ins: `calculate` (an `ast` whitelist over decimals, no `eval`), `spell_back`, `current_time` and `convert_time` (§16), and four that register only when configured: `web_search` (Tavily or Brave through the `web_search` provider kind, D-V5-7), `send_sms` (Twilio or Telnyx; only to the caller's own number on a phone call or to a labelled `telephony.sms_targets` entry, once per message per session), `fetch_url` (the agent's host list intersected with `LKAP_HTTP_TOOL_ALLOWED_HOSTS`, public hosts only, 1 MB) and `notify_team` (a webhook resolved from an `http-tool-secret`, also used by `escalate_to_human` when configured; the transcript only when asked for). The api resolves their keys into `ResolvedAgentConfig.builtin_providers`. Cal.com booking is a set of six HTTP tool templates, not a pack (D-V5-36): `GET /v1/tool-templates` and `POST /v1/tool-templates/{template_id}/instantiate`.

**The fence (R-V5-15, V5-27).** Every place that hands third-party text to the model calls `fence(text, source=…)` (`agent/src/lkap_agent/tools/untrusted.py`), which strips control characters and any `untrusted` tag, bounds the length and wraps the text as `<untrusted source="…">…</untrusted>`; `compose_instructions` appends one fixed rule telling the model never to follow instructions inside those tags. `FENCED_SITES` lists the sites and a parity test pins them: knowledge, `search_knowledge`, `http_request`, `fetch_url`, `web_search`, provider tools, declarative HTTP tools, MCP results, recalled memories (`session_builder`), guardrail classifiers, `describe_panel` and the panel messages (`platform_agent`). The api's test runner uses a byte-identical copy (ask #193). `agent/src/lkap_agent/qa.py` fences transcripts but is not in the list yet (ask #177).

## 6. Request blocks, caller files and the asset path

**Requestable blocks (V5-02, V5-03, V5-08, V5-12).** A block whose state inherits `RequestableState` (`idle | requested | submitted | cancelled`) can be asked for: `UiChannel.request_block` patches it to `requested`, sends `lkap.ui.request` with `method = "request"`, and waits for `lkap.agent.action {action: "block_submit"}` (the values, or `cancelled`). One request per block; a timeout cancels. `form` stays as the v2 alias with its own statuses. When the caller starts speaking, pending `request`-method requests are cancelled (D-V5-34 as amended by R-V5-1); forms are not. Blocking request tools are on the never-background list, and on realtime pipelines they run silently in the background (`_REALTIME_SILENT_BUILTINS`). The block quartet adds `choices` (`request_choice`, `resolve_choice`; on the phone the options are spoken), `details`, `markdown` and `steps`; `flow_progress` custom blocks render through the `steps` renderer (D-V5-33). A tapped citation (`block_action open_citation`) opens the cited page in a `document` block from a session asset, copying the knowledge-base document into the session on first use (R-V5-5).

**Caller files (V5-19, V5-23, D-V5-35).** The browser streams a file on `lkap.ui.upload`. The worker accepts it only from the caller and only for a block that is `requested`, checks the count, the declared and actual size and the sniffed media type against the block's `accept`, and posts it to `POST /internal/v1/sessions/{id}/assets` (service token). The api checks it again and stores the bytes in the storage backend under `sessions/<session id>/` with a server-chosen name and a SHA-256, one `session_assets` row each (migration `v5_002`; kinds `upload`, `frame`, `signature`, `document`; at most 50 files and 250 MiB per session). The console lists them with `GET /v1/sessions/{id}/assets` and downloads them through fifteen-minute signed links (`GET /v1/sessions/{id}/assets/{asset_id}/content?exp=&sig=`, an HMAC over workspace, session, asset and expiry; `nosniff` and a sandbox policy on every response). `describe_asset` reads an image of the session with the agent's own vision model and a JSON-schema answer (describe, extract fields, read an ID). A sweep deletes the files with the session's recording retention.

```
browser ─lkap.ui.upload─► worker checks (requested? count, size, sniffed type)
        ─POST /internal/v1/sessions/{id}/assets─► api checks again ─► storage sessions/<id>/… + session_assets row
        ◄─ AssetRef ─ worker patches the block ─► browser block_submit ─► request_upload returns the files
console ─GET /v1/sessions/{id}/assets─► signed link ─► GET …/content?exp&sig
```

## 7. Consent and disclosure

`AgentConfig.disclosure {enabled: true, text, position: greeting|banner|both}` puts an AI-disclosure line in the greeting (always spoken on a phone call, which has no banner). The workspace chooses a jurisdiction preset (`eu`, `in` — the default — or `us`) and may rewrite the wording (`settings.compliance`; `GET /v1/workspaces/{id}/compliance`); the presets carry a "confirm with counsel" note and encode no state lists (D-V5-22).

`recording.require_consent` (default off) makes recording wait for a yes (V5-15, V5-17). The `consent` block (`request_consent`, tap to accept) or a spoken answer (`record_consent`, tied to the caller's turn) settles it; each answer is a `consent` session event with the SHA-256 of the exact wording the worker used. In the worker, `RecordingConsentGate` starts Egress only after an accepted recording consent and stops it (`POST /internal/v1/sessions/{id}/recording/stop`) when the caller withdraws; the api folds every consent event into `sessions.consent_state` (migration `v5_009`) and refuses to start a consent-gated recording without an accepted answer. A declined consent is never recorded.

## 8. Privacy and post-call fields

`AgentConfig.privacy` (V5-30, V5-34): `stt_redact` (PCI, PII, PHI, numbers — passed to the speech-to-text provider only when its registry entry supports it), `storage_tier` (`full` by default; `redacted` masks the transcript, event payloads, final panel state and transfer summary after the call; `basic` also drops tool payloads), an optional `scrub_model` that also masks names and addresses, and `telemetry_pii`. The `session_scrub` job runs once, after the summary, when the tier is not `full`, and records a `privacy_scrubbed` event; `POST /v1/sessions/{id}/scrub` runs it on demand. `QaConfig.fields` (up to 20 typed fields) are filled by the worker's call review from the finished conversation into `session_qa.raw["fields"]`; `GET /v1/sessions/export.csv` flattens them into columns and neutralises spreadsheet formulas. The worker sets `LIVEKIT_TELEMETRY_ALLOW_PII` from `telemetry_pii` only when a third-party OpenTelemetry exporter is configured.

## 9. Conversation tuning, languages and captions

**Conversation (V5-07, V5-11).** `PipelineConfig.conversation_preset` (`patient`, `balanced`, `snappy`, `telephony`, `custom`) and a typed `turn_handling` (a mirror of the SDK's options that passes unknown keys through) are stored unexpanded; `lkap_contracts.turn_handling.resolve_turn_handling` expands the preset in `session_builder` and in the api's validators, so both agree by construction (R-V5-4). `turn_detector` sets the hosted or local end-of-turn model and its threshold. The `telephony` preset asks for the phone-tuned noise filter, which is not installed on the worker yet (R-V5-3, ask #208).

**Languages and captions (V5-31, V5-35).** `VoiceConfig.languages` (the first is the default), `auto_detect` and `voices_by_language`. With more than one language the agent gets `switch_language`; with `auto_detect` the speech-to-text provider detects the language and the agent follows after two consecutive turns in another listed language (`agent/src/lkap_agent/languages.py`). A switch changes the speech-to-text language when the provider can, swaps the voice when one is configured for that language, appends a note instead of rewriting the prompt, and records `language_switched`. Each stored transcript turn carries its `language`. The `captions` block shows live captions of both sides, streamed on `lkap.captions` and never stored in the block's state. Hindi/English code-switching is the target (D-V5-23).

## 10. Tests and the publish gate

Test cases live in the agent's configuration (`AgentConfig.tests`: persona instructions, scenario, expectations, tool mocks, turn limit), so they version with it (V5-29, V5-33). `POST /v1/agents/{id}/tests/run` creates an `agent_test_runs` row and one `agent_test_results` row per case (migration `v5_006`) and queues a job. For each case the api mints a scratch text session (whose `/resolved` delivers the case's tool mocks), joins the room as the caller over LiveKit's text streams, plays the persona with the workflow model, reads the tool calls back from the session events, and asks five judges (task completion, tool use, safety, relevancy, accuracy) through `qa/llm_client.py`; transcripts are fenced. A run that cannot run ends `error`, never `failed` (D-V5-29). The api joins rooms through `livekit.rtc`, which today reaches it only as a transitive dependency (ask #192). `AgentConfig.publish_gate {require_tests, min_pass_ratio}`: when tests are required, publishing is refused with `422 tests_failing` and a reason (`missing`, `running`, `error`, `failing`) unless the latest run on that version passes.

## 11. Telephony: answering machines and warm transfer

(V5-32, V5-36; nothing here has run on a real call — `PLAN-V5.md` §5.1.) `telephony.amd {enabled, on_machine: hangup|leave_message, message, ivr_detection}`: on an outbound call the worker's `AmdRunner` classifies what answered, reports it on the call (`calls.amd_result`, migration `v5_007`), records a `voicemail` event, and leaves the message or hangs up; the api queues the `call.voicemail` webhook for machine results. `transfer_targets[i].mode` is `cold` (a SIP transfer through the api) or `warm` (the SDK's `WarmTransferTask`: the agent briefs the person first). Warm transfer needs a LiveKit Cloud connection with exactly one synced outbound trunk (`warm_transfer_route` in `/resolved`); otherwise the worker falls back to cold (D-V5-21). The transfer mode and summary are stored on the call. The `handoff` block shows the transfer's progress (`requested`, `connecting`, `connected`, `timeout`, `ended`).

## 12. Supervisor listen-in

(V5-37, V5-38; D-V5-24.) A console user with `builder` and the scope `sessions:listen` (implied by `sessions:write`) can listen to a live session and guide the agent. `POST /v1/sessions/{id}/listen-token` mints a fifteen-minute token for a hidden participant that may subscribe but not publish audio or data (`lkap.role=supervisor`), and audits it. `POST /v1/sessions/{id}/whisper {text, reply_now}` sends a server data packet on `lkap.supervisor` to the agent participants only; the worker accepts only server-sent packets for its own session, adds the text as a `<supervisor_note>` system note (and replies at once when asked) and records `supervisor_whisper`; the audit row keeps a hash, not the text. The LiveKit webhook records `supervisor_joined` and `supervisor_left`. A hidden participant cannot call RPCs, so the Live tab builds its panel mirror from the broadcast state stream; the worker also answers a server-sent snapshot request, but the api does not send one yet (asks #252, #308). `escalate_to_human` gained `mode` (`transfer`, `takeover`, `listen_in`, `callback`); a takeover by a person has no token route yet (ask #253).

## 13. Guardrails

(V5-39, V5-41.) `AgentConfig.guardrails` holds up to 20 rules for each of `input` (what the caller says), `output` (what the agent says, checked sentence by sentence in `transcription_node`) and `tool_output` (checked in the tool execution policy). A rule is a regular expression (checked synchronously; a pattern that does not compile or repeats a repeating group is refused at save), a classifier prompt (judged by `guardrails.model`, else the workflow model) or OpenAI moderation. Model checks run within `budget_ms` (300 by default) and fail open with a `guardrail_timeout` event. On a trip (a `guardrail` event with a hash of the excerpt; the text only on the `full` storage tier) the agent is interrupted and says `safe_reply`; `on_trip` can also end the call or escalate. A tripped tool result is replaced by a line that withholds it. MCP results (including app servers and tool finders) do not pass through the tool execution policy and are not checked (ask #277).

## 14. Caller memory

(V5-40, V5-42; D-V5-17.) Off by default (`AgentConfig.memory.enabled`); scope per agent or per workspace; `retention_days` 90 by default. The backend is Mem0 OSS behind the `MemoryStore` Protocol (`api/src/lkap_api/memory/`), installed with the `lkap-api[memory]` extra: pgvector in the platform database on Postgres, a local Qdrant folder on SQLite. Mem0's telemetry is switched off, its history store is a stub, embeddings use the knowledge embedder, and fact extraction calls the agent's own OpenAI-compatible model through the network guard. A caller is known only by `HMAC-SHA256(workspace memory key, identity)` — the phone number, or a `participant_identity` supplied by a trusted caller; anonymous web visitors are never remembered. The key is a random per-workspace vault credential (`memory-key`).

Recall happens once, before the agent is assembled: the worker calls `POST /internal/v1/memory/recall` (3 s on the api, 6 s on the worker), and the recalled memories are fenced and appended to the instructions with the agent's consent line. Remembering happens once, after the summary, as the `memory_remember` job. Erasure: `DELETE /v1/memory/subjects/{subject_id}`, `POST /v1/memory/purge` (admin) and a retention sweep; each blanks the memories recorded on sessions. The tables `memory_subjects` and `memory_events` (migration `v5_008`) hold subjects and a content-free audit trail; the memories themselves live in the backend. Inbound phone calls recall only when the api already knows the number at session start (ask #264, for the user's ruling).

```
worker start ─POST /internal/v1/memory/recall─► identity ─► HMAC subject ─► MemoryStore.recall (3 s)
             ◄─ memories ─► fenced block appended to the instructions ─► agent
session end ─► summary ─► job memory_remember ─► MemoryStore.remember ─► memory_stored event
console ─► forget / purge / retention sweep
```

## 15. Panel blocks `link`, `slots`, `cards`; `describe_panel`; the AG-UI adapter

(V5-43, V5-44.) `link` shows a checkout, e-signature or identity-check link; payments and identity verification are link-only (D-V5-6, D-V5-26). `send_link` refuses a host outside the block's `allowed_hosts` and, on a phone call, texts the link through `tools.sms` when configured. The block's status moves `pending → opened → completed | failed | expired`; the business's system reports the outcome with a signed `POST /v1/hooks/link/{session_id}` (verified with the signing secret of any enabled webhook endpoint of the workspace), which the api forwards to the worker on `lkap.ui.link`. `slots` offers bookable times in a timezone (`request_slot`, `resolve_slot`; the worker takes the chosen slot's times from its own list, never from the page). `cards` shows cards with up to three actions each (`show_cards`); a tap reaches the model as a block action. `describe_panel` lets the model read what the panel shows, bounded and fenced.

`lkap_contracts.ui_agui` maps panel patches to and from AG-UI state events (`patch_to_agui_delta`, `snapshot_to_agui`, `agui_delta_to_patch`; the mapping is in `docs/CONTRACTS.md` §10). Only the inbound direction is wired: a page may send `lkap.agent.action {action: "state_delta"}` with up to 100 operations, which the worker accepts only when the panel sets `PanelLayout.accept_state_delta` (default off), only for the updatable block types (not `kb_citations` or `custom`), and validates as a whole. The outbound functions are a library for an AG-UI host (ask #312).

## 16. Current date and time

(V5-51, V5-52; R-V5-10.) Two zones: the business timezone (`AgentConfig.timezone`) and the caller's, chosen per session by `resolve_caller_zone` (`agent/src/lkap_agent/locale.py`): the browser's zone (sent on connect and text chat, validated by the api and stamped as the participant attribute `lkap.tz`), else the phone number when it maps to exactly one zone, else the business zone, else UTC; `locale.caller_timezone = "business"` pins the business zone. The workspace default (`settings.locale.timezone`) seeds new agents. The agent gets one line with the date and time at the start of the call and, when 15 minutes have passed or the day has changed, a short "Time now" note at the tail of the context; the system prompt is never rewritten per turn, which keeps provider prompt caching. `current_time(timezone)` and `convert_time(time, from_tz, to_tz)` answer in either zone. A `locale` session event records the zone and where it came from.

## 17. Security review and hardening

V5-26 reviewed the integrations wave at `d621fc7` against the checklist in `PLAN-V5.md` §4 and recorded 45 findings (0 Critical, 2 High, 14 Medium, 22 Low, 7 Info) with an authorization sweep, a dependency audit and residual risks ([`SECURITY-REVIEW-V5.md`](SECURITY-REVIEW-V5.md)). Fable's sign-off (R-V5-18) made V5-27 the gate for waves 7–11 (D-V5-38). V5-27 fixed the gate rows S5-1 … S5-13, S5-40 and S5-41 and most of the others, each with a named test; the rulings it implemented are R-V5-14 (browser-bound MCP sign-in), R-V5-15 (the fence), R-V5-16 (destructive classification and the Apps admin gate) and R-V5-17 (`LKAP_SELF_HOSTED_ALLOWED_NETWORKS`). Operator-visible effects are in RUNBOOK §9.6. The checklist rows for V5-16 and V5-25 (rows 9 and 16), which were not in HEAD at review time, are left for the final review (`PLAN-V5.md` §7); the residuals carried to a production plan are the review's §7.2.

## 18. Data model, settings and dependencies

Ten migrations, chained after `v4_003_session_estimates` in this order: `v5_001_knowledge_p0` → `v5_010_tool_provider_kind` → `v5_003_pgvector` → `v5_009_consent` → `v5_004_mcp_oauth` → `v5_002_session_uploads` → `v5_006_agent_tests` → `v5_005_knowledge_connections` → `v5_007_telephony_amd` → `v5_008_memory` (the head). None drops a column or table in its upgrade. The ids were reserved in the plan's ledger (`PLAN-V5.md` §0.3) and re-chained as packages merged, so they are not in numeric order; the ledger's table names for `v5_006` (the code has `agent_test_runs` and `agent_test_results`) and `v5_002` (the code adds the `document` kind and a `meta` column) are superseded by the migrations. Several features needed no migration: consent answers are session events (plus the one `consent_state` column), Composio connections and MCP sign-ins are vault credentials, the memory key is a credential, and the scrub time is an event.

Settings, the `memory` extra and the new dependencies (`markitdown`, `pgvector` and `mcp` on the api; `phonenumbers` on the worker) are in one table in RUNBOOK §9.7. The worker moved to `livekit-agents` 1.8.3 before any V5 agent package (V4-14, D-V5-32).

## 19. Decision and ruling index

The decisions and rulings live where they were made; this index only points. **Where** is the section that holds the text; **As built** notes where the code differs from the decision as written, or which ruling amended it (blank = as written).

**D-V5-1 … D-V5-38** — [`PLAN-V5.md`](PLAN-V5.md) §1 (T = tools and integrations, K = knowledge and memory, P = panels and capabilities, X = cross-cutting).

| Id | Subject | As built |
|---|---|---|
| D-V5-1 (T1) | Composio first; Arcade later | Only the `composio` adapter exists (§3) |
| D-V5-2 (T2) | Tenants bring their own vendor OAuth apps | Pre-registered MCP clients and custom Composio auth configs (§3, §4) |
| D-V5-3 (T3) | `LKAP_PUBLIC_BASE_URL` is the one public origin; loopback callbacks in dev; CIMD only with a public origin | RUNBOOK §9.3, §9.7 |
| D-V5-4 (T4) | `LKAP_MCP_ALLOWED_HOSTS`, separate from the HTTP-tool list | §4 |
| D-V5-5 (T5) | Subjects are workspace or agent; no per-end-user connections | §3 |
| D-V5-6 (T6) | Payments by phone are link-only | The `link` block (§15) |
| D-V5-7 (T7) | Web search through a `web_search` provider kind (Tavily, Brave) | §5 |
| D-V5-8 (T8) | No weather tool | |
| D-V5-9 (T9) | Google Workspace MCP only as a preset, never live-checked | |
| D-V5-10 (T10) | Zendesk and Salesforce not shipped as presets | |
| D-V5-11 (T11) | Track the MCP SDK, do not fork; a tripwire test | `test_sdk_tripwires.py` (§4) |
| D-V5-12 (K1) | Single host; pgvector the default on Postgres; one writer | §2 |
| D-V5-13 (K2) | The default knowledge path needs no vendor key | §2 |
| D-V5-14 (K3) | MarkItDown and pypdf; Docling and OCR deferred | §2 |
| D-V5-15 (K4) | English and Hindi | The default embedder stays English (§2) |
| D-V5-16 (K5) | BYO stores: Qdrant, Pinecone, Weaviate | Plain HTTP clients, no SDKs; Pinecone has no native hybrid (§2) |
| D-V5-17 (K6) | Memory: Mem0 OSS, opt-in, HMAC subjects, one read and one write per session | Scope may also be the workspace (§14) |
| D-V5-18 (K7) | Preemptive generation stays on by default | Off for auto-inject agents: R-V5-12 |
| D-V5-19 (K8) | Hosted re-rank on the tool path only | The worker does not send it yet (§2, asks #181, #326) |
| D-V5-20 (K9) | Graph RAG deferred; `ExternalRetriever` is the hook | Used by Ragie (§2) |
| D-V5-21 (P1) | Warm transfer on LiveKit Cloud only; cold fallback | §11 |
| D-V5-22 (P2) | Disclosure on; jurisdiction presets; "recording consent required by default when recording is enabled" | `recording.require_consent` defaults to off, so existing agents behave unchanged (§7) |
| D-V5-23 (P3) | Hindi/English code-switching | §9 |
| D-V5-24 (P4) | Supervisors are console users with `sessions:listen` | §12 |
| D-V5-25 (P5) | Voice cloning is catalog-only | |
| D-V5-26 (P6) | Payments and identity checks are link-only | §15 |
| D-V5-27 (P7) | No MCP Apps `embed` block | |
| D-V5-28 (P8) | No map block | |
| D-V5-29 (P9) | Simulations and judges in the api | The api reaches rooms through `livekit.rtc`, transitively (ask #192) (§10) |
| D-V5-30 (P10) | Phone noise cancellation opt-in with its price | Not installed on the worker (R-V5-3, ask #208) |
| D-V5-31 (P11) | Avatar recording stays audio-only | |
| D-V5-32 (P12) | `livekit-agents` 1.8.3 before any V5 agent package | |
| D-V5-33 | `flow_progress` renders as `steps` | §6 |
| D-V5-34 | Blocking request tools never run in the background and cancel on barge-in | Only `request`-method requests cancel (R-V5-1); no `request_signature` tool exists (§6) |
| D-V5-35 | Caller bytes are session assets | §6 |
| D-V5-36 | Cal.com as HTTP tool templates | `time_zone` is a required argument, not a default (ask #150) |
| D-V5-37 | The SQL row is the source of truth for chunk text | §2 |
| D-V5-38 | The security review gates waves 7–11 | §17 |

**D-V5-C1 … D-V5-C13** — [`COMPOSIO.md`](COMPOSIO.md) §2.

| Id | Subject | As built |
|---|---|---|
| C1 | Composio is a `tool_provider` registry entry and a vault key | |
| C2 | Subject per workspace by default, per agent optionally | Several accounts per subject: R-V5-13 |
| C3 | LKAP owns no third-party token | |
| C4 | Managed auth, then a custom OAuth client, then keys | |
| C5 | A nonce-bound callback verified with Composio | `flow=<row id>.<nonce>`; the row is loaded by id (ask #330) |
| C6 | Four modes per agent (`AppsMode`) | Both dynamic modes are Tool Router sessions (R-V5-6); `reviewed_actions` (R-V5-9) |
| C7 | The execution policy is built in | Vendor tags first, wider markers, fail-closed scan, the admin gate (R-V5-16) |
| C8 | Spoken-safe materialisation | Account-bound names (R-V5-13) |
| C9 | Errors are spoken-safe | |
| C10 | Composio host pinned; `net_guard` on every call | |
| C11 | Sessions are provisioned by the api, never the worker | A changed configuration replaces the session (ask #330) |
| C12 | Composio calls counted as cost lines | Not implemented (ask #24) |
| C13 | One key row, two doors (Apps and Keys) | |

**R-V5-1 … R-V5-18** — [`PLAN-V5.md`](PLAN-V5.md) §7. Fable's final review is recorded there as the last ruling.

| Id | Subject | Implemented by |
|---|---|---|
| R-V5-1 | Barge-in cancels generic requests only | V5-08 |
| R-V5-2 | The phone-agent heuristic for choices stays a tip | V5-08 |
| R-V5-3 | Phone noise cancellation deferred; the pin moves to telephony | Open: ask #208 |
| R-V5-4 | Preset expansion in the worker through a contracts function | V5-07 |
| R-V5-5 | Citations open from session assets, copied lazily | V5-19 |
| R-V5-6 | Refresh-schema path, key binding, Tool Router for both dynamic modes | V5-47 |
| R-V5-7 | A new block type's contracts package also fills every exhaustive web map | V5-15, V5-19, V5-31, V5-32, V5-43 |
| R-V5-8 | A dedicated provider-tool editor | V5-50 |
| R-V5-9 | `reviewed_actions`; the default deny is computed server-side | V5-49, V5-50 |
| R-V5-10 | Current date and time in the caller's timezone | V5-51, V5-52 |
| R-V5-11 | Cross-turn dedupe off | V5-06 |
| R-V5-12 | Preemptive generation stays off with auto-inject | V5-06; revisit with ask #58 |
| R-V5-13 | Several accounts of one app | V5-53, V5-54 |
| R-V5-14 | MCP sign-in bound to the starting browser | V5-27 |
| R-V5-15 | Untrusted content is fenced | V5-27 and every later content site |
| R-V5-16 | Destructive-action classification and the Apps admin gate | V5-27 |
| R-V5-17 | An operator ceiling on self-hosted LiveKit reach | V5-27 |
| R-V5-18 | V5-26 sign-off: conditionally approved | V5-27 (the gate) |

Coordinator rulings on individual asks ("Ruled (coordinator)", "Ratified") are in [`_asks.md`](_asks.md) beside each ask; the ones still open at close are summarised in its "Open at close" section.

## 20. Known gaps at close

The full list is the "Open at close" section of [`_asks.md`](_asks.md) (with a disposition per ask) and the residual risks in [`SECURITY-REVIEW-V5.md`](SECURITY-REVIEW-V5.md) §7.2. The ones that change what a builder or operator can rely on:

- **Live checks.** Almost none of the vendor paths has run against the real vendor: Composio, MCP sign-in with a real provider, BYO stores and hosted re-rankers, Ragie, web search, SMS, Cal.com, telephony (answering machines, warm transfer) and multilingual detection wait for keys, an account, the phone number or Docker (`PLAN-V5.md` §5.1).
- **Knowledge on the worker** (§2): the worker sends no `purpose`, so hosted re-rankers are not used on calls and knowledge bases on a managed search service are searched by automatic injection within its 0.4 s budget (asks #181, #326).
- **Apps** (§3): the multi-account prompt line does not reach the model (ask #84); no Composio cost lines (ask #24); callers cannot connect their own accounts (`router.manage_connections` refused, S5-41).
- **Guardrails** do not check MCP results, including app servers and tool finders (ask #277).
- **Listen-in**: a listener who joins mid-call waits for the next periodic panel snapshot (asks #252, #308); no audio takeover (ask #253).
- **Memory**: inbound phone calls recall only when the number is known at session start (ask #264, the user's ruling); the console test chat cannot act as a returning caller (ask #289).
- **Residuals from the reviews** (V5-25 re-review, ask #173): vendor bodies of `web_search`, SMS and Composio execute are read without a size cap (fixed vendor hosts); an MCP error result reaches the model unfenced and MCP results have no size cap; a realtime agent with input transcription off cannot record a spoken consent.
