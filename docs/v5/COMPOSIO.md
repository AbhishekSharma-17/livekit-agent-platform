# LKAP v5 — Composio: connected apps from the console, tools for agents, MCP and Tool Router

Status: **design, binding** (Fable 5.1, 2026-09-25). Written for the user's requirement, paraphrased: *"Set up Composio so we can do auth on our platform from the UI, and those tools become available to agents. In Tools, a dedicated Composio section: list all the apps, do the auth there, and the authenticated ones can be seen in the agents' and tools' configuration. From there we can use them as MCP and so on, plus Composio's search MCP, which helps the agent explore and use tools."* The user has a Composio API key and adds it through the console. Packages: **V5-18** (connection, catalogue, auth, MCP exposure), **V5-22** (the console Apps section), **V5-47** (tools on agents: materialised, MCP server, Tool Router), **V5-48** (the agent editor's connected-apps card); cards in `PLAN-V5.md` §3. Vendor facts (§1) were read on 2026-09-25 from the pages in §11; anything the pages did not state is marked **verify at implementation** and the implementer records what the API actually returned in the brief, without printing key values.

Naming in the UI: Composio is shown as **"Apps"** ("Connected apps", "Connect an app", "Actions"). Never "auth config", "connected account id", "tool router" or "MCP" in a label that a builder reads without a helper line; "MCP" may appear in the mode picker with a plain explanation.

## 0. Summary

- One **workspace-level Composio API key** in the vault, a new registry entry `composio` of kind `tool_provider` with a credential test. The key is entered **either** through the **Enable Composio** flow inside Tools → Apps (paste, **Test key** against Composio before saving, see the account/project name and status, save) **or** through Console → Keys; both write the same `Credential` row, and both screens show the same status (valid / invalid / last tested), Rotate and Disable (D-V5-C13).
- **Tools → Apps**: browse and search Composio's toolkits with logos and categories; **Connect** runs Composio's managed auth (`connected_accounts/link` → the vendor's consent page → back to an LKAP callback), or takes an API key / custom OAuth client that LKAP forwards to Composio and never stores; connection status, Reconnect, Disconnect. Composio holds every third-party token; LKAP stores only the connected-account reference.
- Connected apps are **per workspace** (`user_id = "ws:<workspace_id>"`, D-V5-5 confirmed here as D-V5-C2); an app can also be connected for one agent (`agent:<id>`).
- Agents use apps in **three modes, chosen per agent**: (a) **actions as tools** — picked Composio actions become LKAP tool definitions of kind `provider`, executed through `POST /api/v3.1/tools/execute/{slug}` (recommended; one hop, pinned schemas, editable descriptions, the V4-12 execution policy per tool); (b) **an app server** — a Composio MCP server scoped to the workspace's connected apps, attached as an MCP tool server through the existing guarded MCP client; (c) **Tool Router** — Composio's session MCP whose meta tools let the agent search, inspect and run tools it discovers at run time (the latency caveat is shown and the user may pick it anyway).
- Guard rails in every mode: `net_guard` on every URL, MCP hosts pinned to `backend.composio.dev`, the V4-12 execution policy (`auto` for discovery and reads, `blocking` and never cancellable for `COMPOSIO_MULTI_EXECUTE_TOOL` and any write action), the never-list, and no connect link ever read aloud on a call.
- `lkap_mcp` exposes it all to Claude Code: list apps, connect (returns the link for the human), status, actions, attach.

## 1. Verified facts (accessed 2026-09-25)

Base URL `https://backend.composio.dev/api/v3.1`; header `x-api-key` (or `x-user-api-key`) on every call [C-EXEC][C-TOOLKITS].

| Area | Fact | Source |
|---|---|---|
| Toolkits | `GET /api/v3.1/toolkits` with `category`, `managed_by` (`composio` \| `all` \| `project`), `search`, `limit` (≤ 1000), `cursor`, `sort_by` (`usage` \| `alphabetically`), `include_deprecated`; items carry `slug`, `name`, `type` (`native` \| `custom`), `logo`, `auth_schemes`, `composio_managed_auth_schemes`, `no_auth`, `auth_guide_url`, `meta.description`, `meta.categories`, `meta.tools_count`, `meta.triggers_count`, `meta.version` | [C-TOOLKITS] |
| Tools | `GET /api/v3.1/tools` with `toolkit_slug`, `search`/`query`, `tool_slugs`, `limit`, `cursor`, `important` (featured only), `tags`; items carry `slug`, `name`, `description`, `input_parameters`, `output_parameters`, `scopes`, `no_auth`, `toolkit {slug, name, logo}`, `tags`, `version` | [C-TOOLS] |
| Auth configs | `POST /api/v3.1/auth_configs {toolkit: {slug}, auth_config: {type, credentials?}}`; `type: "use_composio_managed_auth"` for Composio's shared app; response `auth_config.id`, `auth_scheme`, `is_composio_managed`. The custom-credentials `type` value is not shown on the page: **verify at implementation** (the docs' "Custom Auth Configs" page) | [C-AUTHCFG][C-AUTH] |
| Connect (managed OAuth) | `POST /api/v3.1/connected_accounts/link {auth_config_id, user_id, callback_url?, alias?, connection_data?}` → 201 `{link_token, redirect_url, expires_at, connected_account_id}`; the user opens `redirect_url`; after auth Composio redirects to `callback_url` with `status=success` and `connected_account_id={id}` appended | [C-LINK][C-AUTH] |
| Connect (API key and other non-OAuth) | `initiate()` / `POST /api/v3.1/connected_accounts` with `config {auth_scheme: "API_KEY", val: {api_key: …}}`; `initiate()` for Composio-managed OAuth configs is blocked for organisations created on or after **2026-05-08** and for everyone from **2026-07-03**, so LKAP uses `link` for managed OAuth and the create route for keys | [C-AUTH][C-MIGRATE] |
| Connection status | `ACTIVE`, `INITIATED` (expires in 10 minutes), `EXPIRED`, `FAILED`, `INACTIVE`; `GET /api/v3.1/connected_accounts/{id}`; list with `user_ids`, `auth_config_ids`, `statuses`, `toolkit_slugs`; `DELETE …/{id}`; `POST …/{id}/revoke` (revoke at the provider); `PATCH …/{id}/status` (enable/disable) | [C-AUTH][C-CA] |
| Execute | `POST /api/v3.1/tools/execute/{tool_slug}` body `{user_id?, connected_account_id?, arguments?, text?, version? (default latest)}` → `{data, error, successful, session_info, log_id}`; failures carry `{message, code, slug, status, suggested_fix}` | [C-EXEC] |
| MCP server (deprecated by Composio; not used — R-V5-6) | `POST /api/v3.1/mcp/servers {name, auth_config_ids (required), allowed_tools?, no_auth_apps?, managed_auth_via_composio?}` → `{id, mcp_url (deprecated), toolkits, toolkit_icons, …}`; the URL to use is `https://backend.composio.dev/v3/mcp/{id}?user_id={user_id}` (or `connected_account_id`); header `x-api-key` "required when `require_mcp_api_key` is enabled (default for newly created organizations)" | [C-MCP][C-MCPAPI] |
| Tool Router | `POST /api/v3.1/tool_router/session {user_id (required), toolkits?, auth_configs?, connected_accounts?, manage_connections?, tools?, preload?, search?, execute?, …}` → 201 `{session_id, mcp: {type: "http", url}, tool_router_tools[], config, config_version, warnings[]}`; sessions are long-lived, reusable by id, deletable; meta tools `COMPOSIO_SEARCH_TOOLS`, `COMPOSIO_MULTI_EXECUTE_TOOL`, `COMPOSIO_MANAGE_CONNECTIONS` (docs), plus `COMPOSIO_GET_TOOL_SCHEMAS`, `COMPOSIO_WAIT_FOR_CONNECTIONS`, `COMPOSIO_REMOTE_BASH_TOOL`, `COMPOSIO_REMOTE_WORKBENCH`, `COMPOSIO_SEARCH_SKILLS`, `COMPOSIO_USE_SKILL`, `COMPOSIO_MANAGE_SKILL`, `COMPOSIO_SUBMIT_FEEDBACK` (observed on a live Tool Router MCP connection during planning; **verify the exact `tool_router_tools` list at implementation**); `POST …/session/{id}/execute_meta {slug, arguments}` runs a meta tool server-side; "toolkit filters do not preload every matching tool", `preload.tools` loads known tools upfront | [C-TR][C-TRAPI][C-TRKB][C-TRMETA] |
| Pricing | Free: 100,000 tool calls/month, unlimited connected accounts, 3 members, 50,000 triggers; Composio-managed (shared) OAuth apps: 20,000 of those calls free, then $0.0005/call; Scale $29/month, $0.0003/call overage. Tool Router pricing not stated | [C-PRICING] |
| SDK | `composio` Python SDK 0.23.0 (2026-09-23), MIT; `composio-livekit` is stale (0.7.20, 2025-07) — not used | research T §2.1 |

Not on any page read: the query parameters appended on a **failed** callback (assume `status=error` plus a message; handle any non-`success` value as failure), whether the MCP server URL accepts a `user_id` that has no connected account yet (expect a tool-side auth error), and the shape of `tool_router/session.tools` / `execute` filters (allow/deny lists). Each is a one-line check in V5-18's brief.

## 2. Decisions (D-V5-C1 … D-V5-C12)

- **D-V5-C1 — Composio is a `tool_provider` registry entry and a vault credential, like any provider.** `providers.json` gains `composio` (`kind: "tool_provider"`, `secret_fields: [api_key]`, `test_call: GET /api/v3.1/toolkits?limit=1`, `docs_url`, `notes` naming the managed-app surcharge). The key is a `Credential` row; `POST /v1/credentials/{id}/test` works unchanged; workspace enablement through `workspace_providers`. One key per workspace (a second key is allowed but the console says which one is default).
- **D-V5-C2 — Subject per workspace by default: `user_id = "ws:<workspace_id>"`; per agent (`agent:<agent_id>`) is a checkbox in the Connect dialog.** Rationale: on a call the systems belong to the business (research T §2.2). Per-end-user is out of v5 (D-V5-5). The subject is stored on the connection row and every execute, MCP URL and Tool Router session uses it verbatim.
- **D-V5-C3 — LKAP owns no third-party token.** What LKAP stores per connected app: `{provider: "composio", toolkit_slug, auth_config_id, connected_account_id, subject, auth_scheme, is_composio_managed, status, last_checked_at}` in a `Credential` row of kind `tool-provider-account` (the vault encrypts the bag anyway; the bag is not secret material, but the existing table gives fingerprints, tests and audit for free). An API key or a custom OAuth client the admin types is forwarded to Composio in the create call and dropped from memory; it never lands in LKAP's DB or logs.
- **D-V5-C4 — Managed auth first, custom second, keys third — per toolkit, in the Connect dialog.** If `composio_managed_auth_schemes` is non-empty: one click, `link`, Composio's consent page (the surcharge is shown once per workspace). Else if the toolkit has an OAuth scheme: the dialog asks for the vendor client id/secret and creates a custom auth config (the vendor's redirect URI to register is Composio's, shown from `auth_guide_url`). Else (API key, bearer, basic): the fields the toolkit's scheme declares. Auth configs are created once per (workspace, toolkit, scheme) and reused; their ids are kept on the connection row.
- **D-V5-C5 — The callback is an LKAP route bound by a nonce and verified against Composio.** `callback_url = {LKAP_PUBLIC_BASE_URL}/v1/tool-providers/composio/callback?flow=<nonce>`. Because `link` returns `connected_account_id` before the redirect, **no flow table is needed**: the connection `Credential` row is created at link time in status `initiated` with `{nonce, expires_at (10 min), connected_account_id, auth_config_id, toolkit, subject}` in its bag. On return the route: loads the `initiated` row by `connected_account_id` **within the workspace**, checks the nonce (constant time, unexpired, status still `initiated` — single use by flipping the status), requires `status=success`, then calls `GET /connected_accounts/{id}` and requires `user_id == subject` and `status == ACTIVE` before writing the connection row; anything else → the console page with `?connect=error` and an audit row. Nothing from the query string is trusted on its own. Localhost is fine (it is a browser redirect).
- **D-V5-C6 — Three modes per agent, `ToolsConfig.apps: AppsMode`**: `{mode: "actions" | "server" | "router" | "off" = "off", allowed_toolkits: list[str] = [], router: {search: true, execute: true, manage_connections: false}}`. **`actions`** attaches the materialised `provider` tools the builder picked (through `tool_ids`, as today); **`server`** ("app server") provisions, through the api, a **Tool Router session** with the picked actions preloaded (`preload.tools`) and the search meta tools off, attached as an `McpServerDefinition` (`auth = header {x-api-key}`, `origin = {provider: composio, kind: server, remote_id: session_id}`); **`router`** ("tool finder") creates a Tool Router session with the search and execute meta tools and attaches its `mcp.url` the same way (`kind: router`). Both are sessions: Composio's reference marks `/mcp/servers` deprecated (R-V5-6). Modes combine: `actions` + `router` is legitimate ("my pinned booking tools, and let it explore"). The console explains each in one sentence and shows the latency note on `server` and `router` ("the agent looks tools up during the call; expect slower replies").
- **D-V5-C7 — Execution policy (V4-12) is baked in, not optional.** Materialised tools: `execution.mode` defaults to `auto` for actions whose slug or tags say read/list/get/search/fetch, `blocking` otherwise, `cancellable=False` for writes, `max_duration_s=20`, `announce` prefilled ("Let me look that up"). MCP server tools: `tool_options` prefilled with the same rule from the tool list snapshot. Tool Router: `COMPOSIO_SEARCH_TOOLS` and `COMPOSIO_GET_TOOL_SCHEMAS` → `auto` (announce "One moment"), `COMPOSIO_MULTI_EXECUTE_TOOL` → `blocking`, never cancellable, and on `NEVER_BACKGROUND_TOOLS` by name; `COMPOSIO_MANAGE_CONNECTIONS` and `COMPOSIO_WAIT_FOR_CONNECTIONS` are **excluded from `allowed_tools` by default** (a call cannot open a consent page; the router flag `manage_connections` is for text and web agents), and the remote bash/workbench/skill meta tools are excluded always. Destructive actions: the toolkit's tools whose slug matches `DELETE|REMOVE|DESTROY|PURGE|SEND_MONEY|REFUND` are never materialised without an explicit "I understand" in the picker and are always `blocking`; in `server`/`router` modes they are listed under "Actions the agent may take"; an unreviewed destructive action is denied by the api (`effective_denied_actions`, R-V5-9) until the builder reviews it in the card, and `AppsMode.denied_actions` / `reviewed_actions` persist the decision.
- **D-V5-C8 — Voice hygiene at materialisation (research T §2.3.5).** The LKAP tool's `description` is the Composio description's first sentence (editable), parameters are pinned from `input_parameters` with a "Refresh schema" action that diffs, `max_result_chars=1500`, `result_path="data"`, `silent_reply=False`. The tool name is `<toolkit>_<action>` lower-snake (e.g. `googlecalendar_find_free_slots`), unique per workspace.
- **D-V5-C9 — Errors are spoken-safe.** A `successful=false` execute → `ToolError(error.message)` trimmed to one sentence; an auth-shaped failure (`code` or `message` naming an expired/missing connection) → `ToolError("This app needs to be reconnected by an admin")`, a `tool_needs_reauth` session event and the connection row marked `EXPIRED` on the next status check; a URL in any error text is stripped before it reaches the model.
- **D-V5-C10 — Hosts and guards.** Every api call goes through `net_guard.guarded_http_client` to `backend.composio.dev`; every MCP URL LKAP attaches must have host `backend.composio.dev` and scheme `https` (checked at save and at connect in the worker, on top of `LKAP_MCP_ALLOWED_HOSTS` when V5-09 lands); the api key reaches the worker only as the `{{ secret }}` substitution already used for HTTP tools, over `/internal/v1`.
- **D-V5-C11 — Sessions are provisioned by the api, never by the worker (both dynamic modes are Tool Router sessions, R-V5-6).** The worker receives resolved MCP definitions with the URL and headers; it never calls `/mcp/servers` or `/tool_router/session`. Provisioning happens at agent save (`actions`: nothing; `server`: create/update the MCP config when the allow-lists change; `router`: create the session once, reuse). Deleting the agent or turning the mode off deletes the Composio-side MCP config / session (best effort, audited).
- **D-V5-C12 — Cost lines.** Each execute and each router meta call is counted as a `requests` usage line under `composio` (priced from the free tier's zero, with the managed-app surcharge as `tier_note`) once V4-15's `PriceQuote` exists; before that, the count is recorded and shown as "no price".

- **D-V5-C13 — One key row, two doors.** The Composio key is a single `Credential` row of provider `composio`. Tools → Apps shows an **Enable Composio** empty state when the workspace has no such row (or the provider is disabled): a dialog with the key field (write-only; never shown again), **Test key** (a live check of the *pasted* value through `POST /v1/tool-providers/composio/key/test`, which never persists it and returns the account or project name where Composio exposes one — **verify at implementation** which endpoint carries a project/org name; the fallback result is "Key works — N apps available" from the toolkit count), then **Save** (creates the row through the existing provider-keys route and enables the provider for the workspace) — the catalogue and Connect unlock in place. Once enabled, the section header shows the key status (Valid / Invalid / Not tested, with the last-tested time from `Credential.last_test_*`) and three actions: **Validate** (`POST /v1/credentials/{id}/test`, the stored key), **Rotate** (a dialog with the new key, Test key, then replace the secret on the *same* row, keeping every connection and tool), **Disable** (turns the provider off for the workspace — `workspace_providers.enabled=false` — keeps the row and the connections, flags the tools as paused; a separate "Remove key" in Console → Keys deletes the row after a confirm that names the connections and tools affected). Console → Keys lists the same row under Composio with the same status and actions, so there is one source of truth and no second copy.

## 3. Contracts

`contracts/src/lkap_contracts/tool_providers.py` (new module; V5-18):

```python
ToolProviderId = Literal["composio"]            # "arcade" joins by ruling
ConnectionStatus = Literal["active", "initiated", "expired", "failed", "inactive", "unknown"]

class ToolkitOut(BaseModel):        # a Composio toolkit, trimmed for the console
    slug: str; name: str; logo: str | None; categories: list[str]; description: str
    auth: list[Literal["oauth_managed", "oauth_custom", "api_key", "bearer", "basic", "none"]]
    tools_count: int; connected: bool; connection_id: str | None

class ToolkitPage(BaseModel): items: list[ToolkitOut]; next_cursor: str | None

class ActionOut(BaseModel):         # a Composio tool, trimmed
    slug: str; name: str; description: str; parameters: dict[str, Any]
    important: bool; tags: list[str]; risk: Literal["read", "write", "destructive"]

class ConnectIn(BaseModel):
    toolkit: str; subject: Literal["workspace", "agent"] = "workspace"; agent_id: str | None = None
    method: Literal["managed", "custom_oauth", "api_key"]
    fields: dict[str, str] = {}        # forwarded to Composio, never stored (client id/secret or the key)

class ConnectOut(BaseModel):
    connection_id: str; status: ConnectionStatus; redirect_url: str | None; expires_at: datetime | None

class ConnectionOut(BaseModel):
    id: str; toolkit: str; subject: str; status: ConnectionStatus; method: str
    connected_at: datetime | None; last_checked_at: datetime | None; agents_using: int

class KeyTestIn(BaseModel): api_key: str
class KeyTestOut(BaseModel): ok: bool; account_name: str | None; project_name: str | None; toolkits_count: int | None; message: str
class ProviderStatusOut(BaseModel): enabled: bool; credential_id: str | None; last_test_ok: bool | None; last_test_at: datetime | None; connections: int

class MaterialiseIn(BaseModel):
    connection_id: str; actions: list[str]; agent_id: str | None = None   # attach when given
```

`contracts/src/lkap_contracts/tools.py` (V5-47, after V4-12 merges):

```python
class ProviderToolDefinition(BaseModel):
    kind: Literal["provider"] = "provider"
    provider: ToolProviderId
    name: str = Field(pattern=TOOL_NAME_PATTERN)
    description: str
    parameters: dict[str, Any]               # pinned at import
    tool_slug: str
    connection_id: str                        # the tool-provider-account credential
    credential_id: str                        # the provider key credential
    subject: str                              # "ws:<id>" | "agent:<id>", copied from the connection
    timeout_s: float = 10
    max_result_chars: int = 1500
    result_path: str | None = "data"
    silent_reply: bool = False
    execution: ToolExecution = ToolExecution()   # V4-12
    schema_version: str | None = None            # Composio tool version at import

ToolDefinition = Annotated[HttpToolDefinition | McpServerDefinition | ProviderToolDefinition, Field(discriminator="kind")]

class McpServerOrigin(BaseModel):
    provider: ToolProviderId; kind: Literal["server", "router"]; remote_id: str   # mcp config id or session id
# McpServerDefinition gains: origin: McpServerOrigin | None = None
```

`contracts/src/lkap_contracts/agent_config.py` (V5-47): `ToolsConfig.apps: AppsMode` as in D-V5-C6. `ResolvedAgentConfig` needs nothing new: `server`/`router` resolve to `McpServerDefinition`s with the header substituted, `actions` to `provider` definitions with the key substituted (`config_service.resolve_tool_definition`).

## 4. The api (V5-18, then V5-47's resolve step)

`api/src/lkap_api/tool_providers/`:

- `adapter.py`: `ToolProviderAdapter` Protocol — `list_toolkits(query, category, cursor)`, `list_tools(toolkit, query, important, cursor)`, `get_or_create_auth_config(toolkit, method, fields)`, `start_link(auth_config_id, subject, callback_url)`, `create_with_key(auth_config_id, subject, fields)`, `get_connection(id)`, `delete_connection(id)`, `execute(tool_slug, subject, connection_id, args)`, `create_mcp_server(name, auth_config_ids, allowed_tools)`, `delete_mcp_server(id)`, `create_router_session(subject, options)`, `delete_router_session(id)`; `composio.py` implements it over `guarded_http_client` with the paths of §1; `fake.py` is the test double (also used by the live-less gates) and answers from fixtures under `api/tests/fixtures/composio/`.
- `service.py`: catalogue caching like vendor catalogs (D-V2-9; 10-minute TTL per workspace key; logos are vendor URLs shown by the console, never proxied), the connect flow of D-V5-C5, status refresh (`GET` once per console open and lazily before a session that uses the app), materialisation (D-V5-C8) creating `Tool` rows and optionally attaching, deletion cascade (a deleted connection disables its tools with a `needs_reconnect` flag rather than deleting them).
- `router.py` (all admin + `providers:write` except the callback and the reads):
  `GET /v1/tool-providers/composio/toolkits?query&category&cursor` · `GET …/toolkits/{slug}/actions?query&important&cursor` · `POST …/connections` (`ConnectIn` → `ConnectOut`) · `GET …/connections` · `GET …/connections/{id}` (refreshes status) · `POST …/connections/{id}/reconnect` (a new link) · `DELETE …/connections/{id}` (Composio delete + revoke best effort, tools flagged) · `GET /v1/tool-providers/composio/callback?flow&status&connected_account_id` (unauthenticated, D-V5-C5, 302 to `/console/tools?tab=apps&connect=ok|error`) · `POST …/materialise` (`MaterialiseIn` → the created `ToolOut`s) · `POST /v1/tool-providers/composio/tools/{id}/refresh-schema` (R-V5-6) · **`POST /v1/tool-providers/composio/key/test`** (`{api_key}` → `{ok, account_name|None, project_name|None, toolkits_count, message}`; the value is used for one request and discarded; rate-limited like the credential test; never logged) · **`GET /v1/tool-providers/composio/status`** (`{enabled, credential_id|None, last_test_ok|None, last_test_at|None, connections: int}` — the section's header) · **`POST …/enable`** and **`POST …/disable`** (workspace provider enablement; disable pauses the tools) · Rotate reuses the existing provider-keys update route on the same row.
- No new table and no migration: the connection is a `Credential` row (D-V5-C3) that carries the connect nonce while `initiated` (D-V5-C5); the sessions sweep expires stale `initiated` rows.
- `routers/tools.py::_check_payload`: a `provider` definition may bind `credential_id` → a `composio` credential and `connection_id` → a `tool-provider-account`; an `mcp` definition may bind the `composio` credential for its `x-api-key` header only when `origin.provider == "composio"` **and** the URL is `https` on a Composio host (R-V5-6) (this is the only `_check_payload` change V5-18/47 make; V5-09 generalises header auth later and must keep this case).
- `config_service.resolve_tool_definition` (V5-47): substitutes the key for `provider` definitions and for Composio-origin MCP headers; validators: an agent with `apps.mode != "off"` and no `composio` credential → error; `router` with `manage_connections=True` on a `sip_*`-only agent → warning ("a call cannot open a connect link"); a `provider` tool whose connection is `expired|failed|inactive` → error naming the app.
- Audit: `apps.connect.start`, `apps.connect.ok`, `apps.connect.failed(reason)`, `apps.disconnect`, `apps.materialise`, `apps.server.create|delete`, `apps.router.create|delete`.

## 5. The worker (V5-47)

- `tools/provider.py`: builds a `function_tool` per `ProviderToolDefinition` (raw schema = the pinned parameters), runs `POST /api/v3.1/tools/execute/{slug}` with `{user_id: subject, connected_account_id, arguments, version: schema_version or "latest"}` through `guarded_transport()`, `follow_redirects=False`, the timeout, then `result_path` and `max_result_chars` exactly like HTTP tools; errors per D-V5-C9; wrapped by V4-12's `run_with_policy` with the definition's `execution`.
- `tools/declarative.py`: the `provider` branch; MCP definitions with `origin` go through the existing `build_mcp_toolsets` (`GuardedMCPServerHTTP`, `tool_options` prefilled by the api at resolve time per D-V5-C7), host pinned to `backend.composio.dev`.
- Never-list: `COMPOSIO_MULTI_EXECUTE_TOOL`, `COMPOSIO_MANAGE_CONNECTIONS`, `COMPOSIO_WAIT_FOR_CONNECTIONS` join `NEVER_BACKGROUND_TOOLS` (V4-12's list, `tools.py`).
- Activity feed: a materialised tool's headline is its `name` humanised; router meta calls show "Searching for a tool…" / "Running <tool>".

## 6. The console

**V5-22 — Tools → Apps** (`web/src/components/console/tools/apps/**`, a tab on the Tools page; dialogs only):
- **Enable Composio** (D-V5-C13): with no key row (or the provider disabled) the section is an empty state with one button, **Enable Composio**; the dialog has the key field (masked, write-only), **Test key** (live; shows "Connected to <account/project> — N apps available" or the error), **Save** enabled only after a passing test (a warning lets the user save an untested key deliberately), and one line "Stored securely; you won't see it again". After Save the gallery loads in place, no navigation.
- The section header once enabled: the key status chip (Valid / Invalid / Not tested + "last tested <relative time>"), **Validate**, **Rotate** (dialog: new key, Test key, Replace), **Disable** (confirm naming the paused tools), and a link "Also in Keys".
- The gallery: cards with logo, name, category chips, "Connected" state; search and a category filter; paging by cursor; a "Connected only" toggle.
- **Connect dialog**: per toolkit the method radio (Managed — one click; Your own OAuth app — client id/secret; API key — the fields), the "For this workspace / for one agent" choice, the managed-app note ("Composio's shared apps include 20,000 calls a month, then a small per-call fee"), then either a button that opens the vendor page in a new tab and polls status (Connected / Waiting / Failed with Retry) or an immediate result for keys.
- Connection row actions: Reconnect, Disconnect (confirm dialog naming the tools that will stop working), "Actions" (opens the picker below).
- **Actions dialog** (per connected app): the tool list with search and the Featured filter, risk badges (Read / Writes / Destructive), a checkbox per action, the "Add as tools" button that materialises (optionally "and attach to agent …"); the created tools appear in the tools list with an "App" chip and the app logo.
- Reads through `useToolProviderToolkits`, `useToolProviderConnections`, etc. in `api-hooks.ts` (additive).

Console → Keys (`web/src/components/console/providers/**` or the keys page's row for `composio`): the same status chip and the same three actions plus **Remove key**; the two screens share one hook (`useComposioStatus`) so the state cannot diverge.

**V5-48 — the agent editor** (`agents/tabs/tools-tab.tsx`, after V4-13): a **"Connected apps"** card: the mode picker (Off / Use picked actions (recommended) / Let the agent use an app server / Let the agent find tools itself) with one-line helpers and the latency note on the last two; the app list with per-app action picker (reusing V5-22's dialog) that attaches the materialised tools; for `server`/`router` the allowed apps multi-select and the "Actions the agent may take" list with destructive ones unchecked by default; a status line per app (Connected / Needs reconnect → link to Apps). The tools list (`tools-list.tsx`) shows the app chip on `provider` rows and "App server" / "Tool finder" rows for the Composio-origin MCP entries, read-only (they are managed from the card).

## 7. `lkap_mcp` (V5-18 and V5-47)

Tools: `apps_list(query, category, connected_only)`, `apps_actions(toolkit, query, important)`, `apps_connect(toolkit, method, subject, agent_id?, fields?)` (plan-capable; returns the link for the human to open; `fields` follow the secret-reference rules — `env:`/`file:` or inline, never echoed), `apps_connections()`, `apps_connection_status(id)`, `apps_disconnect(id, confirm)`, `apps_add_tools(connection_id, actions, agent_id?)`, `agent_apps_mode(id_or_slug, mode, allowed_toolkits?, router?)`. Docs: `concepts/apps.md`, recipe `connect-an-app.md`; `lkap_guide` gains one line. Results are `Untrusted{…}` where they carry vendor descriptions.

## 8. Tests (all offline; the fake adapter answers from fixtures)

- Contracts: round-trips, the discriminator, `AppsMode` defaults (`off` — compatibility), `ProviderToolDefinition` name pattern, the never-list membership.
- api: the connect nonce row expires and is single-use; `key/test` with a fake 401 → `ok=false` and no row; with a 200 → the name (or the count fallback) and still no row; the value appears in no capture; `status` reflects `last_test_*`; enable/disable flip `workspace_providers` and disable pauses the tools; rotate replaces the secret on the same row (same `credential_id`, new fingerprint) and every connection and tool keeps working; toolkit paging and search hit the cache; connect with `managed` returns a redirect and creates the flow row; the callback: wrong nonce, expired, consumed, `status != success`, mismatched `connected_account_id`, Composio reporting a different `user_id` or `INITIATED` → all `?connect=error` with an audit row and no connection; the happy path writes the connection and consumes the flow; `api_key` method forwards the value and the DB, audit log and structlog capture contain no trace of it; materialise creates tools with pinned schemas, the risk rule and the execution defaults; `refresh-schema` reports a diff; disconnect flags tools; `_check_payload` accepts exactly the two new bindings; validators; provisioning of the MCP config / router session happens on save and is idempotent; resolve substitutes the key.
- agent: the provider tool posts the exact body and headers, applies `result_path`/`max_result_chars`, maps `successful=false` and auth-shaped errors, strips URLs, refuses a non-Composio host in an origin-tagged MCP definition; `run_with_policy` receives the definition's execution; the tripwire that `COMPOSIO_MULTI_EXECUTE_TOOL` is never backgrounded.
- web: the Enable Composio empty state and dialog (Test key states: idle, testing, passed with the name, failed; Save gating), the status header with Validate / Rotate / Disable, the Keys page showing the same row; the gallery, the Connect dialog per method, the polling states, the Actions picker, the card and mode picker; no jargon, no `sheet`, axe, 375 px.
- mcp: snapshot rows, `apps_connect(plan=true)`, doc lint.

## 9. Live check (the user's key is present; run once V5-47 and V5-48 have merged, R-V4-17 rules)

1. Enable: open Tools → Apps, **Enable Composio**, paste the key, **Test key** shows the account/project name (or the apps count) — recorded as "name shown: yes/no", never the name itself if it identifies the user — Save; the gallery loads in place. Console → Keys shows the same row with the same status; **Validate** there updates the last-tested time in both places.
2. Apps: the gallery lists toolkits with logos; search "calendar" finds Google Calendar; the Connect dialog offers Managed.
3. Connect a **low-risk read-only app** (first choice: a keyless `no_auth` toolkit such as a public data or weather-style toolkit if the catalogue has one; else the user's own Google Calendar with the managed app, read-only actions only). The redirect returns to `/console/tools?tab=apps&connect=ok`; the row shows Connected.
4. Actions: pick one read action, "Add as tools and attach to `Demo — Blank agent`"; the tool appears with the App chip; the agent's Tools tab shows the mode as "Use picked actions".
5. Text chat (`chat_start` / `chat_send`): a turn that needs the action; the activity feed shows the tool running and the reply uses its result; the `provider_requests`/usage line counts one call.
6. Router: on a scratch `Demo — Apps scratch` agent set the mode to "Let the agent find tools itself" with the same app allowed; a text chat turn "what tools can you use for my calendar?" → `COMPOSIO_SEARCH_TOOLS` runs (activity row), then a follow-up executes through `COMPOSIO_MULTI_EXECUTE_TOOL`; the EOU→first-audio delta versus step 5 is recorded in a browser session.
7. Server mode: the same on "app server"; the tool list of the attached MCP server matches the picked actions.
8. Disconnect: the tools show "Needs reconnect"; a chat turn gets the admin message; Reconnect restores it.
9. Record in `docs/v5/_briefs/v5-47-live.md`: the free-tier figure the dashboard shows (D-V5-1), every field-shape verification from §1, session ids and cost lines; no key, no connected-account id, no vendor URL beyond `backend.composio.dev`.

## 10. Packages (cards in `PLAN-V5.md` §3)

| ID | What | Owner | Wave | Depends on |
|---|---|---|---|---|
| V5-18 | Composio connection: registry entry, contracts module, adapter + fake, catalogue, connect/callback/status/disconnect, materialisation waits for V5-47's `provider` kind (V5-18 stores picked actions on the connection row until then), MCP exposure; no migration | Opus (worktree) | 1 | V4-12's contracts commit (`export.py`) |
| V5-22 | Console Tools → Apps | Sonnet | 2 | V5-18's contracts commit |
| V5-47 | Composio on agents: `ProviderToolDefinition`, `AppsMode`, the worker handler, MCP server and Tool Router provisioning, execution policy and never-list, resolve, validators, `lkap_mcp` attach tools | Opus (worktree) | 2 (first in the wave; V5-09 rebases on it) | V4-14 merged; V5-18 merged |
| V5-48 | Console: the Connected apps card in the agent editor, app chips in the tools list | Sonnet | 3 | V5-47's contracts commit; V4-13 merged |

## 11. Sources (accessed 2026-09-25)

- [C-AUTH] https://docs.composio.dev/docs/authenticating-tools — auth configs, connected accounts, `link()` vs `initiate()`, the callback parameters `status=success` and `connected_account_id`, the five statuses, `waitForConnection`
- [C-MIGRATE] https://docs.composio.dev/docs/auth-configuration/migrating-initiate-to-link — the 2026-05-08 and 2026-07-03 cut-offs for `initiate()` on Composio-managed OAuth
- [C-LINK] https://docs.composio.dev/reference/api-reference/connected-accounts/postConnectedAccountsLink — `POST /api/v3.1/connected_accounts/link` request and response fields
- [C-CA] https://docs.composio.dev/reference/api-reference/connected-accounts — list, get, delete, revoke, status, `complete_auth`
- [C-AUTHCFG] https://docs.composio.dev/reference/api-reference/auth-configs/postAuthConfigs — `POST /api/v3.1/auth_configs`, `use_composio_managed_auth`
- [C-TOOLKITS] https://docs.composio.dev/reference/api-reference/toolkits/getToolkits — `GET /api/v3.1/toolkits` and item fields
- [C-TOOLS] https://docs.composio.dev/reference/api-reference/tools/getTools — `GET /api/v3.1/tools` and item fields
- [C-EXEC] https://docs.composio.dev/reference/api-reference/tools/postToolsExecuteByToolSlug — `POST /api/v3.1/tools/execute/{tool_slug}` body and response
- [C-MCP] https://docs.composio.dev/docs/mcp-overview — `composio.mcp.create`, the per-user URL `https://backend.composio.dev/v3/mcp/{id}?user_id=…`, `x-api-key` and `require_mcp_api_key`
- [C-MCPAPI] https://docs.composio.dev/reference/api-reference/mcp/postMcpServers — `POST /api/v3.1/mcp/servers`, `mcp_url` deprecated in favour of the `user_id` URL
- [C-TR] https://docs.composio.dev/tool-router/overview — sessions, `session.mcp.url`/`headers`, meta tools, runtime connection links, `composio.use(session_id)`
- [C-TRAPI] https://docs.composio.dev/reference/api-reference/tool-router/postToolRouterSession — `POST /api/v3.1/tool_router/session` fields and the `{session_id, mcp: {type, url}, tool_router_tools}` response
- [C-TRMETA] https://docs.composio.dev/reference/api-reference/tool-router/postToolRouterSessionBySessionIdExecuteMeta — `execute_meta`, `COMPOSIO_SEARCH_TOOLS`, `COMPOSIO_MULTI_EXECUTE_TOOL`, `COMPOSIO_MANAGE_CONNECTIONS`
- [C-TRKB] https://docs.composio.dev/kb/guide/mcp-tool-router-sessions — session lifetime, reuse and deletion, `COMPOSIO_MANAGE_CONNECTIONS`, preload for latency-sensitive use
- [C-PRICING] https://composio.dev/pricing — Free 100,000 calls, managed apps 20,000 then $0.0005/call, Scale $29
- Research: `docs/research-v4/tools-and-integrations.md` §2 (the platform comparison, the tool-provider design sketch this document makes concrete)
