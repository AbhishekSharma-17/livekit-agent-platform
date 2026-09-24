# Tools and integrations for LKAP: tool platforms, a curated tool list, MCP OAuth, preset MCP servers

Status: research and recommendations, no implementation. Written 2026-09-24 by the architect (Fable) from a read-only pass over `docs/v2/ARCHITECTURE-V2.md` (tools and provider sections), `contracts/src/lkap_contracts/tools.py`, `agent/src/lkap_agent/tools/**`, `api/src/lkap_api/routers/tools.py`, `api/src/lkap_api/vault.py`, `api/src/lkap_api/net_guard.py`, `api/src/lkap_api/db/models.py`, `docs/v3/AGENT-ACCESS.md`, the installed `livekit-agents==1.8.2` and `mcp==1.30.0`, the `livekit-agents==1.8.3` wheel from PyPI and the `mcp` python-sdk `main` branch. Vendor facts are cited with URLs and access dates in §8; every figure that only comes from an aggregator or blog is marked *third-party*, and anything marked *unverified* was not confirmed against a primary source today.

Scope note: this document is about the tools an LKAP **voice agent uses** (the worker acting as an MCP client, HTTP tools, built-ins). It is not about `lkap_mcp`, LKAP's own MCP server for coding agents (`docs/v3/AGENT-ACCESS.md`), which stays API-key-only; OAuth for that server was a stated v3 non-goal and nothing here changes it.

---

## 0. Executive summary

LKAP today has a sound but narrow tool layer: nine built-ins plus panel-block and telephony tools, admin-authored **HTTP tools** with `{{ secret.NAME }}` substitution from an `http-tool-secret` credential, intersection host allowlists and a DNS-pinned SSRF-guarded transport, and **MCP servers** that are streamable-HTTP only, authenticated by static headers, and (unlike HTTP tools) neither allowlist-checked nor redirect-safe in the worker (`build_mcp_servers` passes `headers` straight to `MCPServerHTTP`, whose httpx client is built with `follow_redirects=True`). There is no OAuth anywhere in the tool path, no catalogue of ready-made tools, and every integration is a hand-written HTTP definition.

Six findings drive the recommendations:

1. **The MCP authorization spec moved twice since the repo's pins.** The current spec is **2026-07-28**: Protected Resource Metadata (RFC 9728) is a MUST for discovery, resource indicators (RFC 8707) are a MUST on every request, PKCE S256 is a MUST, **Client ID Metadata Documents (CIMD) are the SHOULD**, Dynamic Client Registration (RFC 7591) is **deprecated** and kept as a MAY for backwards compatibility, and clients **MUST** validate the `iss` parameter on authorization responses (RFC 9207) [MCP-AUTH-2607]. The pinned `mcp==1.30.0` implements the 2025-11-25 flow (PRM, AS metadata discovery, CIMD, DCR, RFC 8707, issuer-bound credentials), but has **no RFC 9207 `iss` check** on the callback; the `main` branch (2.x, latest 2.2.0 on 2026-09-07) does [MCP-SDK-SRC][MCP-PYPI].
2. **The worker cannot move to `mcp` 2.x yet.** `livekit-agents` 1.8.3 (released 2026-09-23) still declares `mcp<2,>=1.24.0` [LKA-PYPI]. The design below is therefore built on the stateless helpers that exist in 1.30 (`mcp.client.auth.utils`, `PKCEParameters`, the `mcp.shared.auth` models) with the RFC 9207 check implemented in LKAP, and has an explicit upgrade note.
3. **`MCPServerHTTP` has no `auth` hook in 1.8.3, but it will.** The 1.8.3 wheel's constructor takes only `url, transport_type, allowed_tools, headers, timeout, sse_read_timeout, client_session_timeout_seconds`; its private `_create_http_client(headers, timeout, auth)` accepts an `httpx.Auth` but `client_streams()` calls it with no arguments. `main` already exposes `auth: httpx.Auth | None` on the constructor [LK-MCP-SRC][LK-MCP-183]. A thin subclass that overrides `_create_http_client` (injecting our bearer `httpx.Auth`, `follow_redirects=False` and the guarded transport) is the implementable path today and shrinks to a transport override once the kwarg ships.
4. **The api is stateless ×N, so the SDK's interactive `OAuthClientProvider` (which awaits a `callback_handler` inside one process) is the wrong shape for the console flow.** Persist the in-flight flow (state, verifier, recorded issuer, resource, workspace, tool) in a table keyed by `state`; run the code exchange in whichever replica receives the callback; keep refresh tokens in the vault and refresh only in the api under a lock. The worker receives short-lived access tokens on the same secret-substituted path that already carries `{{ secret }}` values, and asks an `/internal/v1` route for a refreshed one on a 401.
5. **Pre-built tool platforms are converging on the same shape (per-user managed OAuth + execute API + an MCP front), and on a phone call the "user" is the business, not the caller.** That collapses "managed OAuth per end user" to `user_id = workspace_id` for LKAP, which makes the platforms' main selling point secondary and their execute APIs (fixed schemas, no discovery step) the real value for voice. Discovery-style routers (Composio Tool Router, Klavis Strata's four-call flow) add round trips that do not belong on a voice turn.
6. **The remote-MCP ecosystem has real first-party endpoints now, with three auth patterns**: OAuth with DCR (Linear, Cal.com, Sentry, Zendesk *third-party*), OAuth requiring a pre-registered client (Slack, Google Workspace, HubSpot, Shopify Customer Accounts), and an API-key/bearer alternative (Stripe agent keys, Linear API keys, Intercom, Square, Atlassian, GitHub PAT, Cloudflare API tokens). Presets need an auth matrix, not one field.

Top recommendations (details in §2, §3, §4, §5):

- **Integrate Composio first, Arcade second, as a "tool provider" connection.** Composio: 1,500+ toolkits, a REST execute endpoint, custom OAuth apps on every plan, MIT-licensed SDK, and a free tier of 100,000 tool calls/month per its pricing page [COMPOSIO-PRICING] (an August 2026 third-party write-up quotes 20,000 and $4/1k overage for new sign-ups; the primary page wins, but confirm on sign-up [SCALEKIT-COMPOSIO]). Arcade: 2,000 free tool calls + 2,000 auth events/month, $25 + usage on Team, VPC/air-gapped only on Enterprise, `arcade-mcp` actively released (1.16.0 on 2026-09-22), and it absorbed Smithery on 2026-08-05 [ARCADE-PRICING][ARCADE-PYPI][ARCADE-SMITHERY]. Model: one workspace-level provider credential (existing `Credential`/vault, `POST /v1/credentials/{id}/test`), toolkits and tools picked in the console from the provider's catalogue, tools **materialised as LKAP tool definitions** with pinned schemas (a new `kind="provider"` definition executed by a small worker handler through the same guarded transport), never proxied through the provider's dynamic MCP endpoint on the voice path. Connected accounts are authorised by an admin in the console with the provider's connect-link flow.
- **Ship a curated first-party tool set in three tiers** (§3): P0 = booking (Cal.com API-key tools, the same six Retell ships), SMS during a call (Twilio/Telnyx), web search (Brave or Tavily), order/ticket lookup templates, warm/cold transfer and DTMF (exist), time-zone-aware `current_time` (exists), a local `calculate`; P1 = Google Calendar, HubSpot/GoHighLevel/Pipedrive CRM, Zendesk/Freshdesk/Intercom tickets, Stripe read-only, Sheets/Airtable append, email (Resend/SendGrid/Gmail); P2 = Shopify customer accounts, Salesforce, Notion/Confluence knowledge, weather (Open-Meteo is non-commercial-only without a subscription).
- **Implement MCP OAuth as described in §4**: a discriminated `McpServerDefinition.auth` (`none | header | oauth`), a new `mcp-oauth` credential kind in the existing vault, an `mcp_oauth_flows` table, three console routes plus one internal token route, CIMD-first registration with DCR fallback and pre-registered client entry for servers without either, RFC 9207 done in LKAP, `net_guard` on every discovered URL, revocation via RFC 7009 where advertised, and a `tools/list` snapshot at save time so session start-up does not pay for discovery.
- **Presets** (§5): ship GitHub, Linear, Notion, Atlassian, Stripe, HubSpot, Slack, Google Workspace (Developer Preview), Zapier, Cloudflare, Cal.com, Sentry, Intercom, PayPal, Square, Asana, Shopify Customer Accounts as JSON preset records with an `auth_matrix` (which of `oauth_dcr | oauth_cimd | oauth_preregistered | api_key` each supports) and vendor-specific hints (Stripe stops accepting non-agent API keys on 2026-10-31; Slack and Google need your own OAuth app).

---

## 1. What exists today (baseline)

| Piece | Where | Notes |
| --- | --- | --- |
| Built-in tools | `agent/src/lkap_agent/tools/builtin/`, names in `lkap_contracts.tools.BUILTIN_TOOL_NAMES` | `end_call`, `search_knowledge`, `http_request`, `describe_current_frame`, `pin_frame`, `push_note`, `set_status`, `escalate_to_human`, `current_time`; block tools `update_block`, `show_document`, `table_append`, `request_form`; telephony `send_dtmf`, `transfer_call` (cold transfer only, label-allowlisted targets). |
| HTTP tools | `HttpToolDefinition`; worker `tools/declarative.py::build_http_tools`; api `routers/tools.py` (CRUD + dry run) | `{{ secret.NAME }}` resolved by the api from an `http-tool-secret` credential before the worker sees the definition; `{{ arg }}` rendered in the worker; `allowed_hosts` ∩ `LKAP_HTTP_TOOL_ALLOWED_HOSTS`; `follow_redirects=False` + `guarded_transport()` (DNS-pinned private-range refusal); `result_path`, `max_result_chars`, `silent_reply`. Binding a credential needs `admin` + `providers:write`. |
| MCP servers | `McpServerDefinition {url, headers, credential_id, allowed_tools, timeout_s, sse_read_timeout_s}`; worker `build_mcp_servers` → `livekit.agents.mcp.MCPServerHTTP(transport_type="streamable_http", headers=...)` | Static headers only. **No `check_url_allowed`, no guarded transport, and livekit's client uses `follow_redirects=True`**, so an MCP URL that passes the api's save-time `net_guard` can still be redirected inward at call time. Tools are listed once per `initialize()` and cached (`_cache_dirty`). |
| Secrets | `Credential(provider_id, ciphertext, fingerprint, last_test_*)`, Fernet vault (`LKAP_MASTER_KEY`), flat `dict[str,str]` bags | Decrypted values leave the api only through the service-token `/internal/v1` routes. `_check_payload` only lets a tool bind a `http-tool-secret` credential. |
| Outbound safety | `api/net_guard.py` (`NetPolicy`, `validate_url`, `guarded_http_client`, aiohttp variants), `agent/tools/_http_safety.py` | Both refuse loopback/private/link-local/CGNAT/metadata names and numeric-looking hosts, resolve-and-pin at connect time. |
| Background tools | `tools/background.py::BackgroundToolRunner` | The pattern for anything >500 ms: submit, return immediately, deliver later via UI activity and chat context. |
| Provider registry | `contracts/generated/providers.json`, `routers/provider_keys.py`, `workspace_providers` | Per-workspace enablement, `POST /v1/credentials/{id}/test`, catalogues. The natural home for "tool provider" keys. |

---

## 2. Pre-built tool platforms

### 2.1 Comparison

All figures accessed 2026-09-24. "Model" = how an agent consumes tools. "Auth" = who holds the third-party OAuth app and tokens. Latency judgements are architectural (number of hops and whether a discovery step precedes execution), not measurements; none of the vendors publish per-call latency.

| Platform | Model | Auth handling | Catalogue | Pricing / free tier | Voice latency fit | Python / LiveKit | Self-host, license |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Composio** | Python/TS SDK (`composio` 0.23.0, 2026-09-23), REST v3.1 (`POST /tools/execute/{tool_slug}` with `x-api-key`), per-user MCP URL `https://backend.composio.dev/v3/mcp/{SERVER_ID}?user_id=…`, "Tool Router" [COMPOSIO-MCP] | Connected accounts per `user_id`; **Composio-managed OAuth apps being phased out** (`initiate()` errors for managed configs from mid-2026; production wants your own OAuth app); API-key/bearer/basic auth configs supported [COMPOSIO-AUTH] | "1500+ toolkits" [COMPOSIO-PRICING] | Free: 100,000 tool calls, 50,000 triggers, 3 members, unlimited connected accounts; Scale $29/mo incl. $29 credit, $0.0003/call overage; managed-app connections 1,000 free then $0.10/connection; Enterprise: SSO/SCIM, CMK [COMPOSIO-PRICING]. *Third-party* (Scalekit, 2026-08) reports 20k free and $4/1k for sign-ups after 2026-08-15 [SCALEKIT-COMPOSIO]. | Good when tools are pinned (one hop to Composio, then the vendor). Tool Router / dynamic discovery adds a call per turn: avoid on voice. | SDK yes; `composio-livekit` exists but is stale (0.7.20, 2025-07-03, targets `composio_core<0.8`) [COMPOSIO-LK]. Use REST or the new SDK, not the plugin. | SDK MIT [COMPOSIO-GH]; platform is hosted (no self-host on the pricing page). |
| **Arcade.dev** | `arcadepy` client (`tools.authorize(tool, user_id)` → URL, `auth.wait_for_completion`, `tools.execute`) [ARCADE-QS]; MCP Gateway; hybrid MCP servers | Arcade Engine runs the OAuth flow per `user_id`, stores tokens, "User Sources" (Auth0, Clerk, Okta, Entra, Stytch) [ARCADE-HOSTING] | "100+ integrations" in Optimized/Starter tiers [ARCADE-HOSTING]; Smithery registry acquired 2026-08-05 [ARCADE-SMITHERY] | Free: 2,000 tool calls + 2,000 auth events/mo; Team $25/mo + $0.01/call + $0.10/auth event; Enterprise: VPC / air-gapped [ARCADE-PRICING] | Good: execute is one hop; auth-required responses are structured (`status != "completed"` + URL) so the tool can hand off cleanly. | `arcade-mcp` 1.16.0 (2026-09-22) is the live package; `arcade-ai` deprecated; `arcadepy` last 2025-11-06 [ARCADE-PYPI]. No LiveKit plugin (plain `function_tool` wrapper). | Cloud, Azure/AWS marketplace, Helm self-host, hybrid workers; TDK/worker MIT, **Engine license unverified** [ARCADE-HOSTING]. |
| **Pipedream Connect** | Remote MCP `https://remote.mcp.pipedream.net/v3` with headers `x-pd-project-id`, `x-pd-environment`, `x-pd-external-user-id`, `x-pd-app-slug`; OAuth client-credentials for your project; SSE + streamable HTTP; also an "app discovery" mode [PD-MCP] | Managed OAuth per external user in your project; connect via Pipedream's Connect Link | "3,000+ APIs", "10,000+ tools" [PD-MCP] | Free in `development` mode; production needs a paid plan; billing = credits (1 credit / 30 s compute) + unique external users [PD-PRICING]. *Third-party*: $99/mo incl. 100 external users, then $2/user [PD-3P]. | Medium: MCP-only surface, per-app server, tool list must be fetched; fine with `allowed_tools` and a cached list. | Any MCP client; no Python SDK needed. | Hosted only. |
| **Zapier MCP** | Remote MCP `https://mcp.zapier.com/api/v1/connect`, streamable HTTP only; OAuth for listed clients, connection token in `Authorization` for others; one server per client, actions auto-provisioned from the user's connected apps [ZAPIER-MCP] | Zapier holds the app connections (user-level) | 9,000+ apps / 30,000+ actions (*third-party*) [ZAPIER-3P] | No separate SKU: draws on plan tasks; *third-party*: 2 tasks per tool call, Free 100 tasks (≈50 calls), Pro from $19.99/mo annual [ZAPIER-3P] | Poor-to-medium: Zapier actions are workflow-grade, not sub-second; use in background only. | MCP only. | Hosted only. |
| **Klavis (Strata)** | Hosted MCP server instances per integration + Strata "one MCP server" with progressive discovery (`discover…` → `get_category_actions` → `get_action_details` → `execute_action`) [KLAVIS-STRATA]; Python SDK `klavis` 2.20.0 (2026-01-29) | Per-user OAuth URLs (`user_id`), white-label with your own OAuth app; `handle_auth_failure` in Strata [KLAVIS-DOCS] | "100+ prebuilt integrations" [KLAVIS-GH]; 600+ claimed by aggregators (*third-party*) | Hobby free + Pro/Team/Enterprise; **public prices not extractable** (pricing page renders client-side); *third-party* "from $79.2/mo" [KLAVIS-3P] | Poor on voice for Strata (four calls before execution); OK for a single-integration hosted server with pinned tools. | SDK yes; no LiveKit plugin. | **Apache-2.0**; per-server Docker images (`ghcr.io/klavis-ai/*-mcp-server`), `pipx install strata-mcp` [KLAVIS-GH]. |
| **Smithery** | MCP registry + hosting; now part of Arcade (2026-08-05); site still live for free sign-up [ARCADE-SMITHERY] | Per-server | Registry (thousands of community servers) | Not on the pricing page any more (redirects to the Arcade announcement) | n/a as a runtime for voice | n/a | Registry. |
| **Merge Agent Handler** | Tool-calling platform with MCP-ready connectors (Salesforce, Slack, Jira, GitHub, HubSpot, NetSuite, Workday…); auth per end user or shared per Group; tool scoping per agent; DLP on every call [MERGE-AH] | Merge-managed | "Unlimited tool packs" [MERGE-AH] | Free 2,000 credits/mo; Pro $1,000/mo for 25,000 credits; Enterprise (on-prem, mVPC) [MERGE-AH] | Medium (enterprise systems, not tuned for sub-second). | REST/MCP. | Enterprise-only deployments. |
| **Nango** | OAuth + proxy + pre-built tools/syncs; built-in MCP server on paid tiers; SDKs [NANGO-PRICING] | Nango holds tokens per "connection" (one authorised user account); BYO OAuth apps | "7k pre-built tools, triggers & syncs" [NANGO-PRICING] | Free: 10 connections, 10 h compute, 10 GB; Pay-as-you-go $50/mo base + $0.29/connection + $0.72/compute-hour; Enterprise: self-host, HIPAA [NANGO-PRICING] | Good as an **auth + proxy layer** (you call the vendor API through Nango's proxy); tools are thinner than Composio/Arcade. | Node/Python SDKs. | Open source under **Elastic License 2.0**; free self-host covers auth + proxy only, MCP/syncs need Enterprise or Cloud [NANGO-LICENSE][NANGO-3P]. |
| **Paragon ActionKit** | "One API & MCP server for hundreds of synchronous integration CRUD actions"; Connect Portal for end users, user-token JWT [PARAGON] | Paragon-managed per Connected User | "thousands" of actions [PARAGON-AK] | Not public (Pro/Enterprise quotes, priced by Connected Users); free trial; Enterprise self-host / forward-deploy [PARAGON] | Medium. | REST/MCP. | Enterprise self-host. |
| **Toolhouse** | Pivoted to no-code "AI workers" (work queues, inboxes); tool-store/SDK positioning not visible on the current pricing page [TOOLHOUSE] | n/a | "1,000+ integrations" | Business $500/mo, Business Max $1,200/mo, 14-day trial [TOOLHOUSE] | Not a fit. | Not a fit. | Hosted. |
| **StackOne** | Unified API + "Tool Kit" for agents, MCP-compatible, Python/TS SDKs for OpenAI/LangChain/CrewAI/Pydantic AI; "Defender Core" prompt-injection defence [STACKONE] | StackOne-managed per account | "520+ connectors, 32,000+ actions" [STACKONE] | Starter free (1,000 credits/seat/mo); Team $600/mo; OEM Core free; Enterprise; on-prem/VPC add-on [STACKONE] | Medium (HR/ITSM/collab focus). | SDK yes. | Enterprise add-on. |
| **Auth0 Token Vault** (auth-only alternative) | Not a tool platform: stores third-party OAuth tokens per user and hands an upstream access token to a service via token exchange (refresh-token, access-token or privileged-worker JWT exchange) [AUTH0-TV] | Auth0 holds tokens; you build the tools | Social + enterprise connections (Google, Microsoft, Slack, GitHub, custom) | Plan requirement not on the page (*unverified*) | Good (one exchange, then a direct vendor call). | Any. | Hosted IdP. |

### 2.2 What "per end user" means for a voice platform

On a phone call the caller is unauthenticated; the systems being read or written (calendar, CRM, ticketing, orders) belong to the **business** running the agent. The account that must be connected is therefore the workspace's (or one agent's) service account, authorised once by an admin in the console. Per-end-user connections only make sense for the `web`/`widget` channels when the site has an authenticated user, and even then the token binding is to the page identity, not the voice session. Every platform above bills or scopes by `user_id`; LKAP should use `user_id = "ws:{workspace_id}"` (or `"agent:{agent_id}"` for per-agent connections), which keeps most tenants at one connected account per app.

### 2.3 Recommendation: Composio first, Arcade second, as a "tool provider" connection

Why Composio first: the largest catalogue, a plain REST execute endpoint that fits the existing HTTP-tool execution model, API-key auth configs for the services voice agents most need (Cal.com, Twilio, Stripe restricted keys), a free tier large enough for a pilot, and an MIT SDK. Its managed OAuth apps are going away, so plan on tenants registering their own OAuth apps for OAuth toolkits (the same thing Google, Slack and HubSpot force anyway, see §5). Why Arcade second: cleaner "authorisation needed" semantics, a governance story (User Sources, VPC/air-gapped on Enterprise) that some LKAP customers will ask for, and an active package line; the free tier (2,000 calls) is only a demo allowance.

Why not the MCP fronts of either for the voice path: both offer per-user MCP URLs, and `MCPServerHTTP` could consume them today with a header, but the tool list is then dynamic and discovered per session, the schemas cannot be edited (descriptions matter a lot for voice models), `silent_reply`/background flags cannot be attached per tool, and `allowed_tools` is the only filter. Materialised tools give the admin the same editor as HTTP tools.

Design sketch ("tool provider" connection):

1. **Registry entries** `composio` and `arcade` in `contracts/generated/providers.json` with `kind: "tool_provider"`, a secret field (`api_key`) and a `test_call` (Composio: `GET /api/v3.1/toolkits?limit=1`; Arcade: a cheap list call) so `POST /v1/credentials/{id}/test` and workspace enablement work unchanged. The key is a normal `Credential` row; binding it to a tool keeps the `admin` + `providers:write` gate.
2. **Catalogue routes** on the api, cached like vendor catalogues (D-V2-9): `GET /v1/tool-providers/{provider}/toolkits?credential_id=` and `GET /v1/tool-providers/{provider}/toolkits/{slug}/tools` (name, description, JSON schema, auth requirement). Every outbound call goes through `guarded_http_client`.
3. **Connected accounts**: `POST /v1/tool-providers/{provider}/connections` `{toolkit, subject: "ws"|"agent:<id>"}` → the api calls Composio's connect-link (`connectedAccounts.link`) or Arcade's `tools.authorize`, returns the vendor's redirect URL for the admin to open, and a status route polls until `ACTIVE` (Composio `waitForConnection` semantics). Stored as a `Credential` of kind `tool-provider-account` holding only the provider's connection id (the provider holds the OAuth tokens). API-key toolkits (Cal.com, Twilio) take the key in the same dialog and forward it to the provider's auth config.
4. **Materialisation**: the console's "Add from Composio/Arcade" picker creates one `Tool` row per selected tool with a new definition kind:
   ```python
   class ProviderToolDefinition(BaseModel):
       kind: Literal["provider"] = "provider"
       provider: Literal["composio", "arcade"]
       name: str = Field(pattern=TOOL_NAME_PATTERN)   # model-facing, editable
       description: str                               # editable
       parameters: dict[str, Any]                     # pinned at import; "Refresh schema" action
       tool_slug: str                                 # provider's id
       credential_id: str                             # the provider API key
       subject: str = "ws"                            # which connected account
       timeout_s: float = 10
       max_result_chars: int = 4000
       result_path: str | None = None
       silent_reply: bool = False
       background: bool = False                       # run via BackgroundToolRunner
   ```
   The worker handler (`tools/provider.py`) is the HTTP-tool handler with a fixed request shape per provider (Composio: `POST https://backend.composio.dev/api/v3.1/tools/execute/{tool_slug}` with `x-api-key` and a body carrying `user_id` and `arguments`, exact field names to be confirmed against the v3.1 reference at implementation time; Arcade: `tools.execute(tool_name, input, user_id)`), the same `guarded_transport()`, and a provider-specific "authorisation required" mapping (Arcade's `status != "completed"`, Composio's connection errors) that returns a spoken-safe error and raises a console notification instead of reading a URL aloud.
   Shortcut for a spike: a Composio tool can be expressed today as a plain `HttpToolDefinition` (`url` = execute endpoint, header `x-api-key: {{ secret.COMPOSIO_API_KEY }}`, `body_template` with `{{ arg }}` placeholders, `result_path`), i.e. zero worker changes. The dedicated kind is for the UX (catalogue picker, schema refresh, connection status), not for execution.
5. **Voice hygiene** applied at import: description rewritten to one sentence, parameters trimmed to what the model should collect by voice, `max_result_chars` default 1,500, `background=True` suggested for anything the provider marks slow, and the `BackgroundToolRunner` used when set.

---

## 3. Curated built-in and predefined tools

Priority: P0 = ship with the first tools release, P1 = next, P2 = later or on demand. "Voice" = appropriate to call synchronously on a voice turn (≤ ~1 s typical) without the background runner; "bg" = should run through `BackgroundToolRunner`. Auth column names the auth LKAP stores; "provider" means via the §2 tool provider. Competitor reference points: Vapi's built-ins are `transferCall`, `endCall`, `sms` (Twilio), `dtmf`, `apiRequest` [VAPI-TOOLS]; Retell's Cal.com set is check availability, book, list/get/reschedule/cancel bookings, keyed by a Cal.com API key and an event type id [RETELL-CAL].

### 3.1 Calendar and booking

| Tool | Provider(s) | Auth | Voice | Priority | Notes |
| --- | --- | --- | --- | --- | --- |
| `booking_check_availability`, `booking_create`, `booking_list`, `booking_get`, `booking_reschedule`, `booking_cancel` | Cal.com API v2 (own key; or the hosted MCP `https://mcp.cal.com/mcp`, OAuth 2.1, 34 tools) [CALCOM-MCP] | API key + event type id (pack setting) | Voice (availability/booking are single API calls) | **P0** | Mirrors Retell's six; a first-class pack, not an HTTP-tool template. The MCP server is the P1 alternative for tenants who want OAuth. |
| `calendar_find_slots`, `calendar_create_event` | Google Calendar (REST, or the Workspace MCP `https://calendarmcp.googleapis.com/mcp/v1`, Developer Preview, own OAuth client) [GWS-MCP] | OAuth (pre-registered client) | Voice | P1 | Needs the §4 OAuth path or the §2 provider. |
| Same | Microsoft 365 / Outlook calendar | OAuth (Entra app) | Voice | P2 | Via provider first. |
| Same | Calendly, Acuity, GoHighLevel calendars | API key / OAuth | Voice | P2 | GoHighLevel is a Vapi staple for agencies [VAPI-GHL]; via provider. |

### 3.2 CRM

| Tool | Provider(s) | Auth | Voice | Priority |
| --- | --- | --- | --- | --- |
| `crm_find_contact`, `crm_create_contact`, `crm_update_contact`, `crm_log_call_note` | HubSpot (REST private app token, or remote MCP `https://mcp.hubspot.com`, OAuth 2.1 PKCE via an "MCP auth app") [HUBSPOT-MCP][HUBSPOT-GA] | Token / OAuth (pre-registered) | Voice (lookup), bg (writes can be) | P1 |
| Same | GoHighLevel, Pipedrive, Zoho CRM | API key / OAuth | Voice | P1 (via provider) |
| Same | Salesforce (hosted MCP servers GA April 2026 for Enterprise Edition+, *third-party*; client-credentials OAuth) [SF-3P] | OAuth | Voice | P2 |

### 3.3 Ticketing and support

| Tool | Provider(s) | Auth | Voice | Priority |
| --- | --- | --- | --- | --- |
| `ticket_lookup`, `ticket_create`, `ticket_add_note` | Zendesk (first-party MCP at `https://<subdomain>.zendesk.com/api/mcp`, OAuth + DCR, EAP since June 2026, *third-party*; API tokens being retired through 2027) [ZENDESK-3P] | OAuth | Voice (lookup), bg (create) | P1 |
| Same | Intercom (`https://mcp.intercom.com/mcp`, OAuth or bearer, 14 tools) [INTERCOM-MCP] | OAuth / bearer | Voice | P1 |
| Same | Freshdesk, Jira Service Management (Atlassian MCP `https://mcp.atlassian.com/v2/mcp`, OAuth 2.1 or API token) [ATLASSIAN-MCP] | API key / OAuth | Voice | P1 (Freshdesk via provider) |
| Same | Linear (`https://mcp.linear.app/mcp`, OAuth 2.1 DCR or API key) [LINEAR-MCP] | OAuth / API key | Voice | P2 (internal-ops agents) |

### 3.4 Email and SMS

| Tool | Provider(s) | Auth | Voice | Priority | Notes |
| --- | --- | --- | --- | --- | --- |
| `send_sms` (to the caller or a configured number) | Twilio, Telnyx, Vonage | API key (`http-tool-secret` style) | bg (fire-and-forget) | **P0** | Vapi's `sms` equivalent [VAPI-TOOLS]. On SIP sessions default the destination to `sip.phoneNumber`; allowlist other destinations like `transfer_targets`. |
| `send_email` | Resend, SendGrid, Postmark; Gmail via Workspace MCP (`https://gmailmcp.googleapis.com/mcp/v1`) [GWS-MCP] | API key / OAuth | bg | P1 | Templates with pack variables; never read addresses aloud from tool output. |
| `send_whatsapp` | Twilio, Meta Cloud API | API key | bg | P2 | |

### 3.5 Payments

| Tool | Provider(s) | Auth | Voice | Priority | Notes |
| --- | --- | --- | --- | --- | --- |
| `payment_lookup` (customer, invoice, subscription status), `send_payment_link` | Stripe (`https://mcp.stripe.com`, OAuth or **agent API key** bearer; full/restricted keys rejected from 2026-10-31; `stripe_api_read`/`stripe_api_write` meta-tools; human confirmation required for refunds) [STRIPE-MCP] | OAuth / agent key | Voice (read), bg (link) | P1 | Read-only by default; write tools off unless the admin enables them. |
| Same | PayPal (`https://mcp.paypal.com/http`, OAuth) [PAYPAL-MCP], Square (`https://mcp.squareup.com/mcp`, OAuth or token) [SQUARE-MCP] | OAuth | Voice | P2 |
| `collect_card_by_dtmf` | Telephony + Stripe/PCI vendor | n/a | Voice | P2 | Requires DTMF capture with audio muting on the recording path; not just a tool. Open question §6. |

### 3.6 Search and web

| Tool | Provider(s) | Auth | Voice | Priority | Notes |
| --- | --- | --- | --- | --- | --- |
| `web_search` | Brave Search API ($5/1k, $5 free credit/mo, "lowest latency" claim) [BRAVE]; Tavily (1,000 free credits/mo, $0.008/credit PAYG) [TAVILY]; Exa (search $7/1k, $10 free/mo; deep variants 4–40 s) [EXA] | API key (provider registry entry, workspace key) | Voice for standard search; Exa deep = bg | **P0** | Return 3 snippets, ≤ 1,500 chars. |
| `fetch_url` | Built-in over the guarded transport | none | bg | P1 | Readability extraction; allowlist. |

### 3.7 Knowledge

| Tool | Provider(s) | Auth | Voice | Priority |
| --- | --- | --- | --- | --- |
| `search_knowledge` (exists) | LKAP KB | n/a | Voice | P0 (exists) |
| `search_notion`, `search_confluence`, `search_drive` | Notion MCP (`https://mcp.notion.com/mcp`, OAuth only) [NOTION-MCP], Atlassian MCP, Google Drive MCP | OAuth | bg | P2 (prefer ingesting into the KB; see the knowledge research doc) |

### 3.8 Telephony

| Tool | Provider(s) | Auth | Voice | Priority | Notes |
| --- | --- | --- | --- | --- | --- |
| `transfer_call` (cold, exists), `send_dtmf` (exists) | LiveKit SIP | n/a | Voice | P0 (exist) | |
| `transfer_call` warm mode | LiveKit SIP (conference room + summary, D-V2-14 Phase 2) | n/a | Voice | P1 | Retell shipped warm transfer as a differentiator [RETELL-CHANGELOG]. |
| `schedule_callback` | Internal (jobs queue → outbound `call_place`) | n/a | Voice | P1 | |
| `leave_voicemail` / AMD-aware `end_call` | LiveKit SIP + classifier | n/a | Voice | P2 | |
| `send_sms` on SIP | see 3.4 | | | P0 | |

### 3.9 E-commerce and order lookup

| Tool | Provider(s) | Auth | Voice | Priority | Notes |
| --- | --- | --- | --- | --- | --- |
| `order_lookup`, `order_status`, `start_return` | Shopify Customer Accounts MCP (`https://{shop}/customer/api/mcp` discovered via `/.well-known/customer-account-api`; OAuth authorization code + PKCE, `client_id` = your App ID, scope `customer-account-mcp-api:full`) [SHOPIFY-CA]; Shopify Order MCP (`https://{shop}/api/ucp/mcp`, client-credentials JWT, 60-min TTL, Token-tier agents only) [SHOPIFY-ORDER] | OAuth (pre-registered) / client credentials | Voice | P2 | The customer-account flow needs the **caller** to authenticate in a browser, which does not fit a phone call; the practical voice pattern is a merchant-side Admin API lookup by order number + phone/email with verification questions (HTTP tool template). |
| Same | WooCommerce, BigCommerce | API key | Voice | P2 (via provider) |

### 3.10 Handoff and escalation

| Tool | Provider(s) | Auth | Voice | Priority | Notes |
| --- | --- | --- | --- | --- | --- |
| `escalate_to_human` (exists) | LKAP | n/a | Voice | P0 | Today a status + event; wire it to a notification (Slack `https://mcp.slack.com/mcp` needs a directory-published or internal app with `client_id`/`client_secret`, no DCR [SLACK-MCP]; or a webhook) as the P1 step. |
| `notify_team` | Slack (incoming webhook or MCP), Teams webhook, email | Webhook URL secret / OAuth | bg | P1 | |
| `create_ticket_on_escalation` | see 3.3 | | bg | P1 | Composite: escalation + ticket + SMS to caller. |

### 3.11 Data (Sheets, Airtable)

| Tool | Provider(s) | Auth | Voice | Priority |
| --- | --- | --- | --- | --- |
| `sheet_append_row`, `sheet_lookup` | Google Sheets (Workspace MCP `https://sheetsmcp.googleapis.com/mcp/v1` or REST) [GWS-MCP] | OAuth (pre-registered) | bg / voice | P1 |
| `airtable_create_record`, `airtable_find` | Airtable REST | PAT | Voice | P1 |
| `table_append` (exists, panel) | LKAP panel | n/a | Voice | P0 (exists) |

### 3.12 Utilities

| Tool | Provider(s) | Auth | Voice | Priority | Notes |
| --- | --- | --- | --- | --- | --- |
| `current_time` (exists) | local `zoneinfo` | none | Voice | P0 | Add a `timezone` argument and default it from the SIP number's region / the agent's configured zone; add `convert_time`. |
| `calculate` | local (safe expression evaluator, no `eval`) | none | Voice | **P0** | Quotes, instalments, percentages; models are unreliable at arithmetic aloud. |
| `get_weather` | Open-Meteo (no key; **free tier is non-commercial only**, 10,000 calls/day; commercial needs a subscription; CC-BY 4.0) [OPEN-METEO] | none / subscription key | Voice | P2 | Nice for demos; commercial tenants need a paid key or another vendor. |
| `lookup_postcode` / address validation | Google Address Validation, Loqate | API key | Voice | P2 | |
| `spell_back` / `confirm_value` | local | none | Voice | P1 | Helpers that format numbers/emails for read-back; no network. |

---

## 4. MCP OAuth: the worker as an MCP client against remote servers

### 4.1 What the spec requires of a client (2026-07-28)

From [MCP-AUTH-2607] (quoted MUST/SHOULD, access 2026-09-24):

- OAuth 2.1 (draft-ietf-oauth-v2-1-13). Clients **MUST** implement PKCE with `S256` and **MUST** verify PKCE support from AS metadata: "If `code_challenge_methods_supported` is absent, the authorization server does not support PKCE and MCP clients MUST refuse to proceed."
- Discovery: "MCP servers MUST implement OAuth 2.0 Protected Resource Metadata (RFC9728)… MCP clients MUST use OAuth 2.0 Protected Resource Metadata for authorization server discovery." Clients **MUST** support both the `WWW-Authenticate: Bearer resource_metadata="…"` header on a 401 and the well-known fallbacks (`/.well-known/oauth-protected-resource/<path>` then `/.well-known/oauth-protected-resource`), and **MUST** try both RFC 8414 (`/.well-known/oauth-authorization-server[/tenant]`) and OpenID Connect Discovery (`/.well-known/openid-configuration`, path-inserted then path-appended) for AS metadata.
- Registration: "Authorization servers and MCP clients SHOULD support OAuth Client ID Metadata Documents", "MAY support the OAuth 2.0 Dynamic Client Registration Protocol (RFC7591). Note that Dynamic Client Registration is deprecated and retained for backwards compatibility". Priority order: pre-registered → CIMD (if `client_id_metadata_document_supported: true`) → DCR (if `registration_endpoint`) → prompt the user. A CIMD `client_id` **MUST** be an HTTPS URL with a path, and the document **MUST** contain `client_id` (equal to the URL), `client_name`, `redirect_uris`.
- Resource indicators: "MCP clients MUST implement Resource Indicators… The `resource` parameter MUST be included in both authorization requests and token requests" with the canonical server URI.
- Issuer validation (new in 2026-07-28): before redirecting, the client **MUST** record the validated `issuer` alongside the PKCE verifier; on the callback it **MUST** apply the RFC 9207 table (reject when the AS advertises `authorization_response_iss_parameter_supported: true` and `iss` is absent; compare by simple string comparison when present, no normalisation; on mismatch **MUST NOT** act on `error`/`error_description`).
- Tokens: `Authorization: Bearer` on every request, **MUST NOT** be in the query string; clients **MUST NOT** send the MCP server any token not issued by its AS; refresh tokens **MUST** be kept confidential, clients **SHOULD** list `refresh_token` in `grant_types`, **MAY** add `offline_access` only when advertised, **MUST NOT** assume a refresh token is issued.
- Scopes: use the 401's `scope` if present, else PRM `scopes_supported`; on `403 insufficient_scope`, step up with the **union** of previous and challenged scopes, with retry limits.
- Redirect URIs **MUST** be `localhost` or HTTPS; AS endpoints **MUST** be HTTPS; clients **SHOULD** use and verify `state`.

Differences from 2025-11-25 that matter to LKAP: DCR is now deprecated rather than merely a MAY; RFC 9207 `iss` validation and the recorded-issuer requirement are new; refresh-token guidance is new; DCR clients are expected to declare an OpenID `application_type` (SEP-837) [MCP-BLOG-2607]. Enterprise-Managed Authorization is an optional extension, not core [MCP-AUTH-2607].

### 4.2 What the pinned SDKs provide

`mcp==1.30.0` (`mcp/client/auth/oauth2.py`, `utils.py`, `mcp/shared/auth.py`), verified in the installed package:

- `OAuthClientProvider(server_url, client_metadata, storage: TokenStorage, redirect_handler, callback_handler, timeout, client_metadata_url)` — an `httpx.Auth` that runs the whole flow inside one process: on 401 it discovers PRM (header then fallbacks) and validates `resource`, discovers AS metadata and validates the issuer (SEP-2468), honours SEP-2352 issuer-bound stored credentials, picks CIMD when `client_id_metadata_document_supported` and `client_metadata_url` are set, else DCR via `create_client_registration_request`, else stored `client_info`; generates PKCE + `state`; **awaits `callback_handler()` for `(code, state)`**; exchanges; refreshes when `can_refresh_token()`; handles `403 insufficient_scope` step-up. `OAuthToken` has `expires_in`, not an absolute expiry (the provider computes `token_expiry_time` in memory).
- **Missing versus 2026-07-28**: no RFC 9207 `iss` validation on the callback (the callback returns only code and state), no `application_type` on DCR. Both exist on `main` (2.x): `AuthorizationCodeResult(code, state, iss)` and the `# RFC 9207` block [MCP-SDK-SRC][MCP-SDK-OAUTH].
- Reusable stateless helpers: `PKCEParameters.generate()`, `extract_resource_metadata_from_www_auth`, `extract_scope_from_www_auth`, `build_protected_resource_metadata_discovery_urls`, `build_oauth_authorization_server_metadata_discovery_urls`, `validate_metadata_issuer`, `create_client_registration_request`, `should_use_client_metadata_url`, `create_client_info_from_metadata_url`, `handle_token_response_scopes`; models `ProtectedResourceMetadata`, `OAuthMetadata`, `OAuthClientMetadata`, `OAuthClientInformationFull`, `OAuthToken`.
- `mcp.client.auth.extensions.client_credentials` exists for machine-to-machine servers (Shopify Order MCP, Salesforce hosted servers).

`livekit-agents==1.8.2/1.8.3` (`livekit/agents/llm/mcp.py`): `MCPServerHTTP` builds its own `httpx.AsyncClient(follow_redirects=True, timeout=…, headers=…)` in `_create_http_client`, which accepts `auth: httpx.Auth | None` but is called without it; `Agent(mcp_servers=[...])`/`AgentSession(mcp_servers=…)`; tools listed once at `initialize()` and cached until `invalidate_cache()`; `MCPToolset(tool_result_resolver=…, tool_options=…)` for result shaping and per-tool options [LK-MCP-DOCS][LK-MCP-183]. `main` adds `auth` to the constructor [LK-MCP-SRC]. Dependency pin `mcp<2,>=1.24.0` in 1.8.3 [LKA-PYPI].

### 4.3 Design

Principle: **the console (api) is the OAuth client; the worker is only a bearer-token user.** The api holds client registrations and refresh tokens in the vault, runs discovery, consent and exchange, refreshes under a lock, and hands the worker a short-lived access token at session start. The worker never sees a refresh token, a client secret or a token endpoint.

#### 4.3.1 Contracts

```python
class McpNoAuth(BaseModel):
    kind: Literal["none"] = "none"

class McpHeaderAuth(BaseModel):          # today's behaviour, made explicit
    kind: Literal["header"] = "header"
    headers: dict[str, str] = {}         # may reference {{ secret.NAME }}
    credential_id: str | None = None     # http-tool-secret bag

class McpOAuthAuth(BaseModel):
    kind: Literal["oauth"] = "oauth"
    credential_id: str | None = None     # an `mcp-oauth` credential once connected
    registration: Literal["auto", "preregistered"] = "auto"
    client_id: str | None = None         # preregistered only
    client_secret_ref: str | None = None # name of a key in the credential bag, preregistered only
    scopes: list[str] | None = None      # admin override; None = spec strategy
    subject: Literal["workspace", "agent"] = "workspace"

McpAuth = Annotated[McpNoAuth | McpHeaderAuth | McpOAuthAuth, Field(discriminator="kind")]

class McpServerDefinition(BaseModel):
    kind: Literal["mcp"] = "mcp"
    name: str
    url: str
    auth: McpAuth = McpNoAuth()
    allowed_tools: list[str] | None = None
    timeout_s: float = 5
    sse_read_timeout_s: float = 300
    cached_tools: list[dict[str, Any]] | None = None   # tools/list snapshot, see 4.3.7
    # `headers` / `credential_id` kept as deprecated aliases → McpHeaderAuth in a migration
```

The worker-facing `ResolvedAgentConfig` carries, per OAuth server, `{url, access_token, expires_at, tool_id}` — the token is substituted by the api exactly where `{{ secret }}` values are substituted today (`config_service.resolve_tool_definition`), so nothing new leaves the api except a bearer with minutes of life.

#### 4.3.2 Storage

- **Credential kind `mcp-oauth`** (a `Credential` row with `provider_id="mcp-oauth"`, reusing the Fernet vault, `fingerprint`, `last_test_*`). Bag keys: `access_token`, `refresh_token`, `expires_at` (ISO), `scope`, `issuer`, `token_endpoint`, `revocation_endpoint?`, `resource`, `client_id`, `client_secret?`, `token_endpoint_auth_method`, `registration_client_uri?`, `registration_access_token?`, `client_metadata_url?`, `status` (`active | needs_reauth | revoked`). Fingerprint = `issuer host · first scope`. `_check_payload` is relaxed to allow an MCP definition to bind `mcp-oauth` (and `http-tool-secret` for header auth); the `admin` + `providers:write` gate stays.
- **Table `mcp_oauth_flows`**: `state` (PK, 32+ random bytes), `workspace_id`, `tool_id`, `actor_id`, `code_verifier`, `issuer` (recorded from validated AS metadata, per 2026-07-28), `authorization_response_iss_parameter_supported`, `resource`, `authorization_endpoint`, `token_endpoint`, `client_id`, `client_secret_ref`, `scopes`, `redirect_uri`, `created_at`, `expires_at` (10 min), `consumed_at`. Rows are single-use and swept with the sessions sweep.
- Optional **table `mcp_oauth_clients`** keyed by `(workspace_id, issuer)` to reuse a DCR registration across tools on the same AS (registrations are per issuer, not per tool).

#### 4.3.3 Routes

| Route | Auth | Purpose |
| --- | --- | --- |
| `POST /v1/tools/{id}/oauth/start` | admin, `providers:write` | Runs discovery (4.3.4), registration (4.3.5), writes an `mcp_oauth_flows` row, returns `{authorization_url, expires_at}`. Never returns tokens. |
| `GET /v1/oauth/mcp/callback?code&state&iss?&error?` | **unauthenticated** (browser redirect), bound by `state` | Looks up the flow row, applies RFC 9207, exchanges the code (PKCE verifier + `resource`), stores the `mcp-oauth` credential, marks the flow consumed, audits, and 302s to the console tool page with `?oauth=ok|error` (no token material in the URL). The redirect URI is `{LKAP_PUBLIC_BASE_URL}/v1/oauth/mcp/callback` and must be HTTPS (localhost allowed in dev). |
| `GET /v1/tools/{id}/oauth/status` | admin | `{status, issuer, scopes, expires_at, last_refresh_at}`. |
| `POST /v1/tools/{id}/oauth/revoke` | admin, `providers:write` | RFC 7009 revoke at `revocation_endpoint` if advertised (refresh token first, then access token), best-effort RFC 7592 client deletion if `registration_client_uri`, then delete the credential; audit. |
| `GET /v1/oauth/mcp/client-metadata.json` | public | The CIMD document (4.3.5). |
| `POST /internal/v1/tools/{id}/oauth/token` | worker service token | Returns `{access_token, expires_at}`; refreshes when < 60 s left (4.3.6). |
| `POST /v1/tools/{id}/test` | admin | For any MCP definition: connect with the stored auth, `initialize`, `tools/list`, store `cached_tools`, return the tool names. Existing `/dry-run` stays HTTP-only. |

#### 4.3.4 Discovery (api side, all via `net_guard.guarded_http_client`)

1. `POST {url}` an `initialize` request without a token (or `GET`), expect 401. Parse `WWW-Authenticate` with `extract_resource_metadata_from_www_auth` / `extract_scope_from_www_auth`.
2. PRM URLs = `build_protected_resource_metadata_discovery_urls(header_url, server_url)`; fetch in order; `ProtectedResourceMetadata.resource` must match the canonical server URI (the SDK's `_validate_resource_match` logic); pick an AS from `authorization_servers` (first, or the admin's choice when several).
3. AS metadata URLs = `build_oauth_authorization_server_metadata_discovery_urls(as_url, server_url)`; `validate_metadata_issuer`; require `code_challenge_methods_supported ∋ "S256"` (refuse otherwise, per spec); record `issuer`, `authorization_endpoint`, `token_endpoint`, `registration_endpoint?`, `revocation_endpoint?`, `client_id_metadata_document_supported?`, `authorization_response_iss_parameter_supported?`.
4. **SSRF**: every URL that came from a server-supplied document (`resource_metadata`, `authorization_servers[]`, each metadata endpoint, `registration_endpoint`, `token_endpoint`, `revocation_endpoint`, `registration_client_uri`) passes `net_guard.validate_url(url, policy)` before it is fetched, and AS endpoints must be `https` (spec) except loopback in dev. Redirects are not followed; a 3xx is an error.
5. Scopes: admin override → 401 `scope` → PRM `scopes_supported` → omit.

#### 4.3.5 Registration

Priority as the spec orders it:

1. **Pre-registered** (`registration="preregistered"`, or a stored `mcp_oauth_clients` row for this issuer): use `client_id` (+ secret from the bag with `client_secret_post`/`basic`). This is the only option for Slack, Google Workspace, HubSpot MCP auth apps and Shopify; the console form shows the redirect URI to paste into the vendor's app settings.
2. **CIMD** when `client_id_metadata_document_supported: true` **and** `LKAP_PUBLIC_BASE_URL` is a public HTTPS origin: `client_id = {LKAP_PUBLIC_BASE_URL}/v1/oauth/mcp/client-metadata.json`, `token_endpoint_auth_method="none"` (`create_client_info_from_metadata_url`). The document: `{client_id: <same URL>, client_name: "LKAP", client_uri, logo_uri, redirect_uris: [callback], grant_types: ["authorization_code","refresh_token"], response_types: ["code"], token_endpoint_auth_method: "none"}`. Keep **one document per deployment**, not per workspace: the AS fetches and caches it by URL, so a per-workspace `client_id` URL or a workspace name inside the document (rejected option) would multiply registrations and cache entries for no gain; the workspace is carried only in the `state` that maps back to the flow row. Self-hosted LKAP behind a private network cannot use CIMD; the console says so and falls through.
3. **DCR** when `registration_endpoint` exists: `create_client_registration_request` with `OAuthClientMetadata(redirect_uris=[callback], grant_types=["authorization_code","refresh_token"], token_endpoint_auth_method="none", client_name, application_type="web")`; store `client_id`, `registration_client_uri`, `registration_access_token` in the credential bag (and in `mcp_oauth_clients` for reuse). Note DCR is deprecated; expect it to disappear from some servers.
4. Otherwise return `needs_client_registration` with the redirect URI and the vendor's AS issuer, so the admin can register an app and switch to `preregistered`.

#### 4.3.6 Tokens: storage, refresh, delivery, revocation

- Access and refresh tokens live only in the `mcp-oauth` bag. `expires_at` is stored absolute (the SDK's `OAuthToken` only has `expires_in`).
- **Refresh happens only in the api**, in `POST /internal/v1/tools/{id}/oauth/token` and in a pre-session step of `resolve_agent_config`: if `expires_at - now < 60 s`, refresh with `grant_type=refresh_token` + `resource` + client auth. Because public-client refresh tokens are **rotated** (spec MUST), two api replicas refreshing the same credential concurrently would invalidate each other: take a lock (`SET NX` in Redis when present, else `SELECT … FOR UPDATE` on the credential row, SQLite falls back to the single-writer), re-read after acquiring, and only refresh if still stale. A refresh failure (`invalid_grant`) sets `status=needs_reauth`, emits a webhook/console notification, and the worker gets `409 needs_reauth`.
- **Delivery to the worker**: the api substitutes `{url, access_token, expires_at}` into the resolved config at session start (the same trust boundary as `{{ secret }}` today, over the service-token `/internal/v1` channel). The worker keeps it in memory for the session only; the `structlog` processors already drop `*token*` fields.
- **Worker `httpx.Auth`** (`tools/mcp_auth.py::ApiIssuedBearer`): adds `Authorization: Bearer`; on a 401 it calls the internal token route once (which refreshes if needed), replaces the token and retries once; on `403 insufficient_scope` it does **not** step up (no browser on a call): it raises a `ToolError("this integration needs to be re-authorised by an admin")`, logs the challenged scopes, and posts an event so the console can show "needs re-authorisation: scope X". Step-up is done by the admin from the console (`/oauth/start` with the union of scopes).
- **Worker MCP client** (`tools/declarative.py`): a subclass `GuardedMCPServerHTTP(MCPServerHTTP)` overriding `_create_http_client` to build `httpx.AsyncClient(follow_redirects=False, timeout=…, headers=…, auth=<ApiIssuedBearer|None>, transport=guarded_transport())`. This also fixes the existing gap for header-auth and no-auth servers (redirect following, no private-range check at connect time). Host policy for MCP URLs: **recommended default** is a separate `LKAP_MCP_ALLOWED_HOSTS` where an empty list means "any public HTTPS host that passes `net_guard`" (presets are named vendor hosts and the private-range/DNS-pin checks still apply), and a non-empty list is a ceiling exactly like the HTTP-tool list. Reusing `LKAP_HTTP_TOOL_ALLOWED_HOSTS` with today's `check_url_allowed` semantics would fail closed for every MCP server whenever that variable is unset (two empty lists allow nothing), which is the wrong default for a preset catalogue; §6 #4 asks the user to confirm or override. When livekit-agents exposes `auth=` on the constructor, the subclass keeps only the transport override.
- **Revocation**: `POST /v1/tools/{id}/oauth/revoke` (4.3.3); deleting the tool or the credential triggers the same best-effort revoke. Vendors also revoke from their side (Stripe "OAuth sessions" [STRIPE-MCP], Slack admin approvals [SLACK-MCP]); a 401 that survives one refresh marks `needs_reauth`.
- **Per-workspace vs per-end-user**: `subject` defaults to `workspace`; `agent` binds the credential to one agent. Per-end-user (`user:<id>`) is a P2 hook: the flow row and credential gain a `subject` column, the web token carries the user id in `session.userdata`, and the worker asks the token route with `subject`; the console flow is replaced by an in-widget consent link. Not needed for telephony.

#### 4.3.7 Tool listing and latency

`MCPServerHTTP.initialize()` performs the MCP handshake and `tools/list` on every session; with several OAuth servers this is hundreds of milliseconds of start-up. `POST /v1/tools/{id}/test` stores `cached_tools` (name, description, input schema) so the console can show and filter tools and so a future worker fast-path can register the tools from the snapshot and connect lazily on first call. The 2026-07-28 spec adds `ttlMs`/`cacheScope` to `tools/list` results [MCP-BLOG-2607], which the snapshot should honour once the SDKs are upgraded.

#### 4.3.8 Servers that don't support DCR (or CIMD)

- Pre-registered client entry in the console (4.3.5 step 1) with the callback URI displayed; vendors: Slack (fixed app id, `client_secret`, admin approval), Google Workspace (own OAuth client, Developer Preview), HubSpot (MCP auth app), Shopify Customer Accounts (App ID), Zapier (connection token, not OAuth, for unlisted clients).
- **API-key alternative** (`McpHeaderAuth`) where the vendor offers one: Stripe agent keys, Linear API keys, Intercom bearer, Square access token, Atlassian API token, GitHub PAT, Cloudflare API token, Cal.com self-hosted `CAL_API_KEY`. Presets carry both options.
- `client_credentials` (extension in the SDK) for machine-to-machine servers (Shopify Order MCP, Salesforce hosted servers): a fourth `McpAuth` kind, P2.

#### 4.3.9 RFC 9207 in LKAP (because 1.30 lacks it)

On the callback: load the flow row; if `authorization_response_iss_parameter_supported` was `true` and `iss` is absent → reject; if `iss` is present → compare byte-for-byte with the recorded `issuer` (no case folding, no trailing-slash normalisation); on mismatch → reject and **ignore** any `error` parameters; else proceed. Then verify `state` with constant-time comparison and that the row is unconsumed and unexpired.

#### 4.3.10 Upgrade path

When `livekit-agents` relaxes `mcp<2` (watch the release notes; `main` already has the `auth` kwarg), move the api's flow helpers to `mcp` 2.x (`AuthorizationCodeResult`, built-in RFC 9207, `application_type`), keep the LKAP tables and routes unchanged, and drop the `_create_http_client` override. The worker and api can pin different `mcp` versions in the meantime because they never exchange SDK objects, only JSON.

#### 4.3.11 Audit and tests

Audit events: `mcp_oauth.start`, `.callback_ok`, `.callback_rejected(reason)`, `.refreshed`, `.needs_reauth`, `.revoked`. Tests: an in-process fake AS + fake MCP server (`respx`) covering PRM header vs well-known fallbacks, path-inserted vs appended OIDC discovery, CIMD vs DCR vs preregistered selection, PKCE refusal when `code_challenge_methods_supported` is missing, RFC 9207 four rows, refresh rotation under concurrency, `insufficient_scope` handling, SSRF rejection of a private `authorization_servers` entry, and the worker subclass refusing redirects.

---

## 5. Preset remote MCP servers

Endpoint and auth facts accessed 2026-09-24 from the vendor pages cited; "DCR" = dynamic client registration observed or documented; "pre-reg" = you must create an OAuth app with the vendor; "key" = an API-key/bearer alternative exists. Presets should be JSON records `{id, name, url, transport, auth_matrix, scopes_hint, allowed_tools_default, notes, docs_url}` shipped in `contracts/generated/mcp_presets.json` and shown in the console's "Add MCP server" dialog.

| Preset | Endpoint | Transport | Auth | Notes |
| --- | --- | --- | --- | --- |
| GitHub | `https://api.githubcopilot.com/mcp/` (`/insiders` variant, `X-MCP-Insiders` header) | streamable HTTP | OAuth (for hosts with OAuth support), **key**: PAT bearer [GITHUB-MCP] | Read-only mode documented for the local server only. Dev/ops agents, not callers. |
| Linear | `https://mcp.linear.app/mcp`, `/mcp/readonly`; legacy `/sse` | streamable HTTP | OAuth 2.1 **DCR**; **key**: API key as bearer; `read` scope for read-only [LINEAR-MCP] | Good OAuth reference implementation for testing 4.3. |
| Notion | `https://mcp.notion.com/mcp` (SSE fallback `/sse`) | streamable HTTP | **OAuth only**, no token option [NOTION-MCP] | 20 MiB upload tool. |
| Atlassian (Jira/Confluence/Bitbucket/Loom) | `https://mcp.atlassian.com/v2/mcp` (`?tools=all` for gateways) | HTTP | OAuth 2.1; **key**: API token optional [ATLASSIAN-MCP] | Consumes Rovo credits per call. |
| Stripe | `https://mcp.stripe.com` | streamable HTTP | OAuth (consent per live/sandbox account), **key**: agent API key bearer; full/restricted keys rejected from **2026-10-31**; `Stripe-Account` header for Connect (keys only) [STRIPE-MCP] | Meta-tools `stripe_api_read/write`; human confirmation link for refunds/outbound payments (not voice-friendly: treat writes as bg + escalation). |
| HubSpot | `https://mcp.hubspot.com` | HTTP | OAuth 2.1 PKCE with **pre-reg** "MCP auth app" (self-service since 2026-01-13); GA 2026-04-13 [HUBSPOT-MCP][HUBSPOT-GA] | Read/write core CRM objects; excludes sensitive data properties. |
| Google Workspace | `https://gmailmcp.googleapis.com/mcp/v1`, `calendarmcp…`, `drivemcp…`, `docsmcp…`, `sheetsmcp…`, `slidesmcp…`, `chatmcp…`, `people.googleapis.com/mcp/v1` | streamable HTTP | OAuth 2.0 with your **own client ID/secret** (pre-reg); Developer Preview Program membership required [GWS-MCP] | Availability gate is an open question (§6). |
| Slack | `https://mcp.slack.com/mcp` | streamable HTTP (no SSE) | Confidential OAuth 2.0, **pre-reg** app (`client_id`/`client_secret`, fixed app id), **no DCR**, directory-published or internal apps only, admin approval; GA 2026-02-17 (*third-party* date) [SLACK-MCP][SLACK-3P] | Search, messaging, canvases, users, files, lists. |
| Zapier | `https://mcp.zapier.com/api/v1/connect` | streamable HTTP only | OAuth for listed clients; **key**: connection token in `Authorization` (or query, avoid) for custom clients; one server per client [ZAPIER-MCP] | Uses plan tasks (*third-party*: 2 per call) [ZAPIER-3P]. |
| Cloudflare | `https://mcp.cloudflare.com/mcp` (API), `docs.mcp.cloudflare.com/mcp`, `bindings…`, `builds…`, `observability…`, `radar…`, `containers…`, `browser…`, `logs…`, `ai-gateway…`, `autorag…`, `auditlogs…`, `dns-analytics…`, `dex…`, `casb…`, `graphql…`, `agents.cloudflare.com/mcp` | streamable HTTP | OAuth (Cloudflare account); **key**: API token on the API server [CF-MCP] | Ops agents. |
| Cal.com | `https://mcp.cal.com/mcp` (self-host: stdio + `CAL_API_KEY`) | streamable HTTP | OAuth 2.1 (DCR *unverified*, "your client handles the authorization flow automatically") [CALCOM-MCP] | 34 tools; the P0 booking pack can use REST + API key instead. |
| Sentry | `https://mcp.sentry.dev/mcp` (org/project scoping in the path) | HTTP | OAuth only [SENTRY-MCP] | |
| Intercom | `https://mcp.intercom.com/mcp`, EU `https://mcp.eu.intercom.com/mcp` | streamable HTTP | OAuth; **key**: bearer [INTERCOM-MCP] | 14 tools (conversations, contacts, companies, help center, notes). |
| PayPal | `https://mcp.paypal.com/http` (sandbox `https://mcp.sandbox.paypal.com/http`; `/sse` variants) | streamable HTTP / SSE | OAuth consent via PayPal login [PAYPAL-MCP] | |
| Square | `https://mcp.squareup.com/mcp` | streamable HTTP (docs show `mcp-remote`) | OAuth; **key**: access token (local) [SQUARE-MCP] | Production only on the remote server. |
| Asana | `https://mcp.asana.com/v2/mcp` (old `/sse` shut down 2026-05-11) | streamable HTTP | OAuth [ASANA-MCP] | |
| Shopify Customer Accounts | `https://{shop}/customer/api/mcp` via `/.well-known/customer-account-api` | HTTP | OAuth code + PKCE, `client_id` = App ID (**pre-reg**), scope `customer-account-mcp-api:full` [SHOPIFY-CA] | The **shopper** authenticates; not for phone callers. |
| Shopify Order MCP | `https://{shop}/api/ucp/mcp` | JSON-RPC POST | Client-credentials JWT (`read_global_api_orders`, 60-min TTL) [SHOPIFY-ORDER] | `get_order` only; Token-tier agents. P2 via a `client_credentials` auth kind. |
| Zendesk (*third-party*) | `https://<subdomain>.zendesk.com/api/mcp` | HTTP | OAuth with DCR, read/write scopes; EAP since 2026-06; API tokens retired through 2027-04-30 [ZENDESK-3P] | Zendesk's own docs only describe Zendesk **as an MCP client** (OAuth-only outbound) [ZENDESK-CLIENT]; endpoint **unverified**. |
| Salesforce hosted MCP (*third-party*) | per-org endpoint | HTTP | OAuth 2.0 client-credentials; GA 2026-04 for Enterprise Edition+ [SF-3P] | **Unverified** against Salesforce docs; the salesforce.com MCP page only describes Agentforce as a client [SF-MCP]. |

Preset hygiene: presets are data, never executed at save time; the console still runs `net_guard.validate_url` on the URL and `POST /v1/tools/{id}/test` before an agent can use the server; `allowed_tools_default` keeps voice agents to a handful of read tools per vendor.

---

## 6. Open questions for the user

1. **Composio pricing reality**: the pricing page says 100,000 free tool calls/month; a third-party post says 20,000 and $4/1k overage for sign-ups after 2026-08-15. Which do we see on sign-up, and is $0.10/managed connection acceptable, given managed OAuth apps are being retired anyway?
2. **Who registers vendor OAuth apps?** Slack, Google Workspace, HubSpot and Shopify require the tenant to own an OAuth app. Do we (a) require tenants to bring their own per workspace (the "preregistered" path), (b) run one LKAP-owned app per vendor for hosted LKAP (LKAP becomes the data processor, needs vendor app review), or (c) both with (b) only on the hosted plan?
3. **Public origin for CIMD and callbacks**: `LKAP_PUBLIC_BASE_URL` must be a public HTTPS origin for CIMD and for every vendor redirect. Is the console always public, or must the OAuth callback be routable separately (e.g. a small public `/v1/oauth/mcp/*` surface behind Caddy while the rest is private)?
4. **Allowlist for MCP hosts**: §4.3.6 recommends a separate `LKAP_MCP_ALLOWED_HOSTS` whose empty value means "any public HTTPS host under `net_guard`". Confirm, or override with reuse of `LKAP_HTTP_TOOL_ALLOWED_HOSTS` (operators would then have to list `mcp.stripe.com` etc. before any preset works).
5. **Per-end-user connections**: is there a near-term web/widget use case with an authenticated end user, or can `subject ∈ {workspace, agent}` be the whole v4 scope?
6. **Payments by phone**: do we want card capture at all (PCI-DSS scope, DTMF masking, recording pause), or only "send a payment link by SMS"?
7. **Search vendor default**: Brave (needs a card even for the free credit), Tavily (1,000 free credits, no card), or Exa? One registry entry per vendor or a single `web_search` provider slot with a picker?
8. **Weather**: Open-Meteo's free tier is non-commercial. Ship it behind a "demo only" flag, buy a subscription for hosted tenants, or skip?
9. **Google Workspace MCP access**: it is a Developer Preview Program feature; do we have (or want) membership, or do we use the Calendar/Gmail REST APIs directly for P1?
10. **Zendesk and Salesforce**: both endpoints are third-party reports only. Do we have accounts to verify (Zendesk EAP, Salesforce Enterprise Edition) before promising presets?
11. **Upgrade cadence**: do we track `livekit-agents` releases for the `mcp<2` relaxation and the constructor `auth` kwarg, or fork the two-method subclass and stop caring?

---

## 7. Sources

All accessed 2026-09-24.

Spec and SDKs
- [MCP-AUTH-2607] https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
- [MCP-AUTH-2511] https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- [MCP-BLOG-2607] https://blog.modelcontextprotocol.io/posts/2026-07-28/
- [MCP-PYPI] https://pypi.org/project/mcp/ (2.2.0 released 2026-09-07; v1.x branch in maintenance)
- [MCP-SDK-OAUTH] https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/client/oauth-clients.md
- [MCP-SDK-SRC] https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/src/mcp/client/auth/oauth2.py (RFC 9207 block; `client_metadata_url`); installed `mcp==1.30.0` `mcp/client/auth/oauth2.py`, `utils.py`, `mcp/shared/auth.py`
- [LKA-PYPI] https://pypi.org/project/livekit-agents/ and https://pypi.org/pypi/livekit-agents/1.8.3/json (released 2026-09-23; `mcp<2,>=1.24.0` under the `mcp` extra)
- [LK-MCP-183] `livekit_agents-1.8.3` wheel, `livekit/agents/llm/mcp.py` (`MCPServerHTTP.__init__` without `auth`; `_create_http_client(headers, timeout, auth)`; `follow_redirects: True`)
- [LK-MCP-SRC] https://raw.githubusercontent.com/livekit/agents/main/livekit-agents/livekit/agents/llm/mcp.py (`auth: httpx.Auth | None` on the constructor)
- [LK-MCP-DOCS] https://docs.livekit.io/agents/logic/tools/mcp/
- RFCs referenced: RFC 9728 https://datatracker.ietf.org/doc/html/rfc9728 · RFC 8414 https://datatracker.ietf.org/doc/html/rfc8414 · RFC 7591 https://datatracker.ietf.org/doc/html/rfc7591 · RFC 8707 https://www.rfc-editor.org/rfc/rfc8707.html · RFC 9207 https://datatracker.ietf.org/doc/html/rfc9207 · RFC 7009 https://datatracker.ietf.org/doc/html/rfc7009 · CIMD draft https://datatracker.ietf.org/doc/html/draft-ietf-oauth-client-id-metadata-document-00

Tool platforms
- [COMPOSIO-PRICING] https://composio.dev/pricing
- [COMPOSIO-MCP] https://docs.composio.dev/docs/mcp-overview
- [COMPOSIO-AUTH] https://docs.composio.dev/docs/authenticating-tools
- [COMPOSIO-GH] https://github.com/ComposioHQ/composio (MIT); https://pypi.org/pypi/composio/json (0.23.0, 2026-09-23)
- [COMPOSIO-LK] https://docs.composio.dev/frameworks/livekit ; https://pypi.org/pypi/composio-livekit/json (0.7.20, 2025-07-03)
- [SCALEKIT-COMPOSIO] *third-party* https://www.scalekit.com/blog/composio-pricing-change
- [ARCADE-PRICING] https://www.arcade.dev/pricing/
- [ARCADE-HOSTING] https://docs.arcade.dev/en/home/hosting-overview
- [ARCADE-QS] https://docs.arcade.dev/en/home/quickstart
- [ARCADE-SMITHERY] https://www.arcade.dev/blog/smithery-joins-arcade (2026-08-05); https://smithery.ai/pricing (banner "now a part of Arcade.dev")
- [ARCADE-PYPI] https://pypi.org/pypi/arcade-mcp/json (1.16.0, 2026-09-22); https://pypi.org/pypi/arcade-ai/json (deprecated); https://pypi.org/pypi/arcadepy/json (1.10.0, 2025-11-06)
- [PD-MCP] https://pipedream.com/docs/connect/mcp/developers
- [PD-PRICING] https://pipedream.com/docs/pricing
- [PD-3P] *third-party* https://www.usagepricing.com/blueprint/pipedream ; https://zapier.com/blog/pipedream-pricing/
- [ZAPIER-MCP] https://docs.zapier.com/mcp/overview
- [ZAPIER-3P] *third-party* https://latenode.com/blog/zapier-mcp-pricing ; https://www.usecarly.com/blog/zapier-mcp/
- [KLAVIS-GH] https://github.com/Klavis-AI/klavis (Apache-2.0); https://pypi.org/pypi/klavis/json (2.20.0); https://pypi.org/pypi/strata-mcp/json (1.0.2, Apache-2.0)
- [KLAVIS-DOCS] https://www.klavis.ai/docs/llms.txt ; https://www.klavis.ai/docs/auth/oauth.md
- [KLAVIS-STRATA] https://www.klavis.ai/docs/concepts/strata.md
- [KLAVIS-3P] *third-party* https://www.saasworthy.com/product/klavis-ai/pricing ; https://www.usefuturestack.com/tools/klavis-ai
- [MERGE-AH] https://www.merge.dev/pricing/agent-handler
- [NANGO-PRICING] https://nango.dev/pricing
- [NANGO-LICENSE] https://raw.githubusercontent.com/NangoHQ/nango/master/LICENSE (Elastic License 2.0)
- [NANGO-3P] *third-party* https://www.g2.com/products/nango/pricing ; https://nango.dev/docs/guides/platform/self-hosting
- [PARAGON] https://www.useparagon.com/pricing
- [PARAGON-AK] https://docs.useparagon.com/actionkit/overview
- [TOOLHOUSE] https://toolhouse.ai/pricing
- [STACKONE] https://www.stackone.com/pricing
- [AUTH0-TV] https://auth0.com/docs/secure/tokens/token-vault
- Gateway landscape (*third-party*): https://mastra.ai/articles/best-mcp-gateways ; https://www.stackone.com/blog/best-mcp-gateways/

Preset MCP servers
- [GITHUB-MCP] https://raw.githubusercontent.com/github/github-mcp-server/main/README.md
- [LINEAR-MCP] https://linear.app/docs/mcp
- [NOTION-MCP] https://developers.notion.com/docs/get-started-with-mcp ; https://developers.notion.com/docs/mcp
- [ATLASSIAN-MCP] https://support.atlassian.com/rovo/docs/getting-started-with-the-atlassian-remote-mcp-server/
- [STRIPE-MCP] https://docs.stripe.com/mcp
- [HUBSPOT-MCP] https://developers.hubspot.com/ai-tools/mcp
- [HUBSPOT-GA] https://developers.hubspot.com/changelog/remote-hubspot-mcp-server-is-now-generally-available ; https://developers.hubspot.com/changelog/public-beta-self-service-mcp-auth-apps-for-the-hubspot-remote-mcp-server
- [GWS-MCP] https://developers.google.com/workspace/guides/configure-mcp-servers ; https://developers.google.com/workspace/calendar/api/guides/configure-mcp-server
- [SLACK-MCP] https://docs.slack.dev/ai/mcp-server/
- [SLACK-3P] *third-party* https://growthmethod.com/slack-mcp-server/ ; https://mcpservers.org/remote-mcp-servers/slack
- [CF-MCP] https://developers.cloudflare.com/agents/model-context-protocol/mcp-servers-for-cloudflare/
- [CALCOM-MCP] https://cal.com/docs/mcp-server
- [SENTRY-MCP] https://mcp.sentry.dev/
- [INTERCOM-MCP] https://developers.intercom.com/docs/guides/mcp
- [PAYPAL-MCP] https://developer.paypal.com/tools/mcp-server/
- [SQUARE-MCP] https://developer.squareup.com/docs/mcp
- [ASANA-MCP] https://developers.asana.com/docs/using-asanas-model-control-protocol-mcp-server
- [SHOPIFY-CA] https://shopify.dev/docs/apps/build/storefront-mcp/servers/customer-account
- [SHOPIFY-ORDER] https://shopify.dev/docs/agents/orders/order-mcp
- [ZENDESK-3P] *third-party* https://www.geckoboard.com/blog/zendesk-mcp-the-different-options-compared-2026/ ; https://www.strac.io/blog/zendesk-mcp-server
- [ZENDESK-CLIENT] https://support.zendesk.com/hc/en-us/articles/10497779528730
- [SF-3P] *third-party* https://growthmethod.com/salesforce-mcp-server/ ; https://www.jitendrazaa.com/blog/salesforce/salesforce-mcp-server-for-claude-code-mcp-clients-setup/
- [SF-MCP] https://www.salesforce.com/agentforce/mcp-support/

Voice-agent references and utilities
- [VAPI-TOOLS] https://docs.vapi.ai/tools/default-tools ; [VAPI-GHL] https://docs.vapi.ai/tools/go-high-level
- [RETELL-CAL] https://docs.retellai.com/integrations/cal-com-functions ; [RETELL-CHANGELOG] https://www.retellai.com/changelog/new-dashboard-warm-transfer-cal-com-fields-and-partner-programs
- [BRAVE] https://brave.com/search/api/
- [TAVILY] https://tavily.com/pricing
- [EXA] https://exa.ai/pricing
- [OPEN-METEO] https://open-meteo.com/en/terms
