# V5-18 live check: Composio connection (pending the user's key)

Status: **not run.** V5-18 landed offline: every Composio call in the tests goes to `FakeComposio` (`api/tests/fakes/composio.py`) or to `httpx.MockTransport`. Nothing here has called Composio with a real key, and no key was ever available to the implementer. The user adds the key themselves (Console → Keys → Composio, or Tools → Apps → Enable Composio once V5-22 ships). The steps are `COMPOSIO.md` §9 steps 1–3, driven through `lkap_mcp` before the console exists.

Live rules (HANDOFF rule 3, PLAN-V5 §0.1): a scratch api on its own port and DB, a Builder-or-higher key minted for the run and revoked at the end (R-V4-17), `Demo — ` objects only, cleaned up. The Apps tools that write need `providers:write`, which is the **Operator** preset (Builder can read Apps but not connect). Record pass or fail, the evidence (status codes, counts, statuses) and the date. Never record the key, a connected-account id, an auth-config id, a project or person name that identifies the user, or any vendor URL other than `backend.composio.dev`.

## Before you start

- The scratch api must be reachable by the browser at the address the callback uses: `LKAP_PUBLIC_BASE_URL` if set, else `LKAP_API_BASE_URL`, else `http://127.0.0.1:<PORT>`. The browser comes back to `LKAP_WEB_BASE_URL` (else the first `LKAP_CORS_ORIGINS` entry) at `/console/tools?tab=apps&connect=ok|error`; before V5-22 that page is just the Tools page, which is fine.
- Localhost callbacks are fine: this is a browser redirect, Composio does not call the api.

## Steps

| # | Step | Expect | Result |
|---|---|---|---|
| 1a | `POST /v1/tool-providers/composio/key/test` with the pasted key (or, before the console, `api_request` from `lkap_mcp` with `confirm=true`) | `ok: true`; record "project name shown: yes/no" and the `toolkits_count` (a number). A deliberately wrong key: `ok: false`, "Composio rejected this key". No `credentials` row is created by either call. | pending |
| 1b | The user saves the key (Console → Keys → Composio, or `provider_key_create(provider_id="composio", …)`) | `provider_key_create` returns `test.ok: true`; `GET /v1/tool-providers/composio/status` shows `enabled: true`, the same `credential_id`, `last_test_ok: true` and a `last_test_at`. | pending |
| 1c | `POST /v1/credentials/{id}/test` again after 10 minutes | `last_test_at` moves; the status endpoint mirrors it. | pending |
| 2a | `apps_list(query="calendar")` | Google Calendar is listed with a logo (https), categories, `auth` containing `oauth_managed`. Record the `total` shown. | pending |
| 2b | `apps_actions(toolkit="googlecalendar", important=true)` | Actions with risk labels; at least one `read`. | pending |
| 2c | `GET /v1/tool-providers/composio/toolkits/googlecalendar` | `auth_fields.oauth_custom` lists the client id/secret fields; `oauth_redirect_uri` is Composio's callback. | pending |
| 3a | Pick a **low-risk, read-only** app. First choice: a keyless toolkit (`auth: ["none"]`) → `apps_connect(toolkit=<slug>, method="none")` | `status: active` immediately; no vendor account is created. | pending |
| 3b | Else the user's own Google Calendar with the managed app: `apps_connect(toolkit="googlecalendar")` | `status: initiated`, a `redirect_url` **given to the user** (not opened by the agent). The user signs in; the browser lands on `/console/tools?tab=apps&connect=ok`. | pending |
| 3c | `apps_connection_status(id=…)` | `status: active`, `needs_reconnect: false`. Audit (`activity`) shows `apps.connect.start` and `apps.connect.ok`, with no URL in either payload. | pending |
| 3d | `apps_add_tools(connection_id=…, actions=[<one read action>])` | `picked_actions` holds it; a destructive action without `allow_destructive=true` is refused. | pending |
| 3e | Cleanup: `apps_disconnect(id=…, purge=true, confirm=true)`; revoke the run's MCP key | The connection disappears from `apps_connections()` and from the Composio dashboard. | pending |

## Facts to verify and record (COMPOSIO.md §1 "verify at implementation")

Each was read from Composio's docs on 2026-09-25 and coded to; the live run confirms or corrects it. Record only "confirmed" / "differs: <field names>".

| Fact | What the code assumes | Result |
|---|---|---|
| Key test endpoint | `GET /api/v3.1/auth/session/info` with `x-api-key` answers 200 for a project key and carries `project.name`; 401/403 for a bad key. The code falls back to `GET /auth_configs?limit=1` when session-info is unavailable. | pending |
| Organisation name | Not read (the only name besides the project's is the org member's personal name, which is never shown). If `project.org.name` exists, it fills `account_name`. | pending |
| Toolkit list shape | `items[]` with `slug`, `name`, `auth_schemes`, `composio_managed_auth_schemes`, `no_auth`, and `meta.{logo, description, categories, tools_count}`; top level `next_cursor`, `total_items`. | pending |
| One toolkit | `GET /toolkits/{slug}` has no top-level `auth_schemes`/`no_auth`; the code reads `auth_config_details[].mode` and `.fields.{auth_config_creation, connected_account_initiation}.{required, optional}[]` (`name`, `displayName`, `is_secret`). | pending |
| Custom auth config body | `{"toolkit": {"slug"}, "auth_config": {"type": "use_custom_auth", "authScheme": "OAUTH2"|"API_KEY", "credentials": {…}, "name"}}` — `authScheme` is camel case on the wire (the Python SDK's alias). | pending |
| Managed auth config reuse | `GET /auth_configs?toolkit_slug=&is_composio_managed=true` items carry `is_composio_managed`, `status`, `auth_scheme`, `toolkit.slug`. | pending |
| Key-based connection | `POST /connected_accounts {"auth_config": {"id"}, "connection": {"user_id", "state": {"authScheme": "API_KEY", "val": {"api_key", "status": "ACTIVE"}}}}` → `{id, status}`. | pending |
| Callback parameters | Success appends `status=success&connected_account_id=…`; failure appends `status=failed` (any non-`success` is treated as failure). | pending |
| Connected account | `GET /connected_accounts/{id}` has `user_id` and `status` in `ACTIVE|INITIATED|EXPIRED|FAILED|INACTIVE`; `DELETE` answers 2xx. | pending |
| Free-tier figure | Record what the dashboard shows (D-V5-1): 100,000 calls/month, managed apps 20,000 of those. | pending |

## For V5-47 (recorded here, not checked live by V5-18)

- Composio's docs mark the `/api/v3.1/mcp/servers` API **deprecated** in favour of sessions (`tool_router/session` with MCP on). V5-47 should prefer the session route for both "app server" and "tool finder" modes, or confirm the old route still works before using it.
- Tool Router session routes seen in the reference: `POST /tool_router/session`, `GET|PATCH|DELETE /tool_router/session/{id}`, `POST …/{id}/execute`, `…/execute_meta`, `…/search`, `…/link`, `GET …/{id}/tools`, `…/toolkits`. The allow/deny field shapes for `tools`/`execute` filters are still unverified.

## Asks raised by V5-18 (for `docs/v5/_asks.md`, which V5-01 creates)

1. **Provider-keys owner** — `api/src/lkap_api/routers/provider_keys.py` — connected-app rows are `credentials` rows with provider `tool-provider-account`: hide them from `list_credentials` (unless `provider_id` asks for them), and make `update_credential`, `test_credential` and `delete_credential` refuse that provider with a pointer to `/v1/tool-providers/composio/connections` (today a Keys-page Delete removes the row without the Composio delete or pausing its tools). `api/tests/test_tool_providers.py::test_provider_keys_still_list_and_delete_connection_rows_pending_an_ask` pins the current behaviour; flip it with the fix. — asked by V5-18 — open.
2. **Provider-keys owner** — same file, `update_credential` — clear `last_test_at`/`last_test_ok`/`last_test_message` when `secrets` change, so a Validate right after Rotate does not answer from the old key's 10-minute cache (affects every provider, Composio included). — asked by V5-18 — open.
3. **V5-22** — until ask 1 lands, filter `provider_id == "tool-provider-account"` rows out of the Keys page client-side. — asked by V5-18 — open.
