# V5-09 live check: MCP auth union, host policy, connection test

Status: **not run.** V5-09 landed offline: every MCP server in the tests is `httpx.MockTransport` (`api/tests/test_tools_mcp.py`, `agent/tests/unit/test_mcp_guard.py`). The implementer could not start the dev stack (the user's api, web and worker are never started or stopped by a package agent).

Live rules (HANDOFF rule 3, PLAN-V5 §0.1): a scratch api on its own port and DB, a worker under a fresh agent name (never `lkap-agent`), an **Operator** key minted for the run (binding a key to a tool needs `providers:write`) and revoked at the end (R-V4-17), `Demo — ` objects only, cleaned up. Record pass or fail with the evidence (status codes, counts, event types) and the date. Never record a key, a header value or a full session id.

**Which server.** Any public, keyless MCP server reachable over `https` (streamable HTTP) will do; name it by vendor only, never by URL, in this file. The worker refuses loopback and private hosts, so the chat step needs a public server. If none is reliable on the day, steps 1–4 can run against a local fake MCP server on a scratch port in `LKAP_ENV=dev` (the api allows plain `http` to loopback in dev only), and step 5 is recorded as "deferred (needs a public server)".

## Steps

| # | Step | Expect | Result |
|---|---|---|---|
| 1 | `provider_key_create(provider_id="http-tool-secret", secrets={"TOKEN": <any value>})`, then `tool_create_mcp(name="Demo — MCP", url=<server>, auth={"kind":"header","headers":{"x-demo":"{{ secret.TOKEN }}"},"credential_id":<id>})` | `201`; `tool_get` shows `definition.auth.kind == "header"` and the deprecated `headers`/`credential_id` mirrors equal to it. | pending |
| 2 | `tool_test(tool_id)` | `ok: true`, `tool_count` ≥ 1, `tool_names` as untrusted text; `tool_get` shows `cached_tools` with the same names and a `cached_at`. Record the duration. | pending |
| 3 | `tool_update(tool_id, patch={"definition": {"timeout_s": 7}})` then `tool_get` | `cached_tools` kept (the url did not change). | pending |
| 4a | `tool_create_mcp(name="Demo — MCP oauth", url=<server>, auth={"kind":"oauth"})` | `422`, `details.reason == "oauth_not_available"`. | pending |
| 4b | `tool_create_mcp(..., url="https://10.0.0.5/mcp")` and `url="http://<the public server host>/mcp"` | Both `422 blocked_destination` (private; not https). | pending |
| 4c | Scratch api with `LKAP_MCP_ALLOWED_HOSTS=example.com`: `tool_test` on the step 1 tool | `422 blocked_destination`, reason names `LKAP_MCP_ALLOWED_HOSTS`. Restore the variable after. | pending |
| 5 | `agent_attach` the tool to `Demo — Blank agent`; `chat_start` / `chat_send` with a turn that needs one of the listed tools | The activity feed shows the MCP tool running; session events include `tool_call_started`/`tool_call_ended` for it. | pending |
| 6 | Worker with `LKAP_MCP_ALLOWED_HOSTS=example.com` (fresh agent name), one chat turn | The session starts; its events include an `error` event "MCP server '…' skipped: host '…' is not on LKAP_MCP_ALLOWED_HOSTS"; no MCP tool is offered. | pending |
| 7 | Cleanup: detach and delete the Demo tools and the secret bag, revoke the run's key | Nothing `Demo — ` left from this run. | pending |

Worker restart: the worker must be restarted to pick up the new `build_mcp_toolsets` code and `LKAP_MCP_ALLOWED_HOSTS` (the api too, for the new route and setting).
