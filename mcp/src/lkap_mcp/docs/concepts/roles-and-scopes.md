# Roles and scopes

Your API key acts as `admin` inside its one workspace, but only for the
routes its **scopes** cover. Scopes, not roles, are what shape your tool
list. `me()` returns exactly which scopes your key carries. A tool whose
scope you lack simply is not registered, so you cannot even attempt the
call (least privilege by omission, not by a runtime 403 you have to catch).

## The scopes

`agents:read`, `agents:write`, `sessions:read`, `sessions:write`,
`sessions:listen`, `calls:write`, `connections:read`, `connections:write`,
`providers:read`, `providers:write`, `webhooks:write`, `audit:read`, and `*`
(every scope at once. Never offered by the console's key presets, and you
should not ask a user to mint one for you). A `:write` scope implies its
matching `:read`, and `sessions:write` also implies `sessions:listen`.

## Console presets

Agent keys (`kind="agent"`) are minted from the console, never by a tool:

| Preset | Scopes |
|---|---|
| Read-only | `agents:read`, `sessions:read`, `connections:read`, `providers:read`, `audit:read` |
| Builder | Read-only + `agents:write`, `sessions:write` |
| Operator | Builder + `connections:write`, `providers:write`, `webhooks:write` |

`calls:write` is a separate, off-by-default checkbox on any preset. Having
it is necessary but not sufficient for `call_place`/`call_control` to
appear (`lkap_explain("telephony")` has the other two gates). Key
management itself (`POST /v1/api-keys`, revoking one) is never a tool;
`api_request` refuses every `/v1/api-keys*` path.

## What each scope actually gates

- `agents:read`/`agents:write`: agents, tools, knowledge bases, packs, and
  (write) test chat.
- `sessions:read`/`sessions:write`: session and call history. Re-scoring
  QA needs `sessions:write`.
- `sessions:listen`: listen in to a live session and send its agent written
  guidance the caller never hears: `session_whisper(session_id, text,
  confirm=true)`. `sessions:write` implies it.
- `connections:read`/`connections:write`: LiveKit connections and their
  fleet.
- `providers:read`/`providers:write`: the registry, provider settings and
  credentials. Also required to bind a secret to an HTTP tool.
- `webhooks:write`: the only scope for webhooks (there is no read-only
  webhook access).
- `audit:read`: `activity()`, your own key's history in the console's
  activity view.

## Read-only mode

`LKAP_MCP_READ_ONLY=1` on the MCP process restricts the tool list to reads
regardless of what the key's scopes allow. A way to run a coding agent
against a real key without any risk of it writing, even by mistake.

## Related tools

`me`, `activity`, every scope-gated tool above.

## Related schemas

`ApiKeySelfOut`, `ApiKeyOut`, `AuditOut`.
