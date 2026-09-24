# lkap-mcp

The MCP server a coding agent (Claude Code, Codex CLI, Cursor or any MCP
client) uses to understand and configure an LKAP workspace. It connects
LiveKit projects, stores provider keys, builds agents from packs, adds
knowledge bases and HTTP/MCP tools, validates, test-chats and publishes an
agent. It is the only agent-facing surface: there is no CLI (R-V3-1).

- **Transport:** stdio by default. The coding agent spawns `lkap-mcp` as a
  child process. The remote streamable-HTTP mode is covered in
  [Remote (HTTP) mode](#remote-http-mode).
- **Auth:** one LKAP API key per client, sent as `Authorization: Bearer lkap_…`
  to the api's `/v1` routes only (R-V3-5). The key's scopes decide which tools
  exist for the client (R-V3-13).
- **Attribution:** every request carries
  `X-LKAP-Client: lkap-mcp/<version>; client=<clientInfo.name>; tool=<tool>; call=<id>`,
  so each change appears in the console's Settings → AI agents → Activity,
  and through the `activity` tool, with the client and tool that made it.

The design is in `docs/v3/AGENT-ACCESS.md`, and the rulings are in
`docs/v3/PLAN-V3.md` §8.

## Requirements

- A checkout of this repository. No package has been published yet, so every
  install line runs the server from the checkout with
  `uv run --project <checkout>/mcp lkap-mcp` (R-V3-17). Replace `<checkout>`
  with the absolute path of your clone.
- [uv](https://docs.astral.sh/uv/) and Python 3.12. `uv run` creates
  `mcp/.venv` on first use.
- A running LKAP api (for example `http://127.0.0.1:8080` in dev) and an
  agent key for it.

## 1. Mint an agent key

In the console, open **Settings → AI agents → Connect an AI agent**. Pick the
client, a preset and an expiry, acknowledge the transcript warning, and copy
the key. The key is shown once. The dialog also prints the snippets below with
your api origin and key already filled in.

| Preset | Scopes | What the agent can do |
|---|---|---|
| Read-only | `agents:read`, `sessions:read`, `connections:read`, `providers:read`, `audit:read` | look around: agents, sessions, connections, providers, activity |
| Builder | Read-only + `agents:write`, `sessions:write` | build agents, knowledge bases and tools, **test chat**, publish |
| Operator | Builder + `connections:write`, `providers:write`, `webhooks:write` | also LiveKit connections, provider keys and webhooks |

- `calls:write` is a separate checkbox, off by default. See
  [Outbound calls](#outbound-calls-dial-opt-in).
- The test chat tools (`chat_start`, `chat_send`, `chat_rewind`, `chat_end`)
  need `agents:write`, `sessions:write` and `connections:read`, so they need
  the **Builder preset or higher** (R-V3-20).
- Keys expire after 30 days by default (365 at most). Revoke a key in the
  console at any time; the next tool call then fails with `unauthorized`.
- Key management is never an MCP tool (R-V3-9).

Keep the key in your **user-level** agent config, never in a project file that
is committed.

## 2. Install (local, stdio)

In each snippet, `<api-origin>` is your api (for example
`http://127.0.0.1:8080`), `<key>` is the agent key, and `<checkout>` is the
path of your clone. The strings match the console dialog
(`web/src/components/console/settings/snippets.ts`).

### Claude Code

```bash
claude mcp add -s user --transport stdio lkap --env LKAP_API_URL=<api-origin> --env LKAP_API_KEY=<key> -- uv run --project <checkout>/mcp lkap-mcp
```

`-s user` stores the server in your user-level Claude Code config, not in the
project. `claude mcp list` should then show `lkap`. For the packaged skill and
plugin, see [Claude Code skill and plugin](#claude-code-skill-and-plugin).

### Codex CLI

Add this to `~/.codex/config.toml`:

```toml
[mcp_servers.lkap]
command = "uv"
args = ["run", "--project", "<checkout>/mcp", "lkap-mcp"]

[mcp_servers.lkap.env]
LKAP_API_URL = "<api-origin>"
LKAP_API_KEY = "<key>"
```

Codex reads `AGENTS.md` for project guidance. Copy the repository's
`AGENTS.md` into your project, or point Codex at the checkout.

### Cursor and other MCP clients

Most clients, including Cursor's `mcp.json`, take the generic `mcpServers`
JSON form:

```json
{
  "mcpServers": {
    "lkap": {
      "command": "uv",
      "args": ["run", "--project", "<checkout>/mcp", "lkap-mcp"],
      "env": {
        "LKAP_API_URL": "<api-origin>",
        "LKAP_API_KEY": "<key>"
      }
    }
  }
}
```

Any client that can spawn a stdio MCP server works the same way. The command
is `uv`, and the arguments are `run --project <checkout>/mcp lkap-mcp`, with
the two env vars set.

### Check the install

```bash
uv run --project <checkout>/mcp lkap-mcp --version
```

This prints `lkap-mcp <version>`. Then ask your agent:
*"Call lkap_guide and tell me what this workspace has."* The agent should call
`lkap_guide` and `me`, and report the workspace, the key's scopes and the
agents that exist.

If `lkap-mcp` exits at once with `LKAP_API_KEY is not set`, the client did not
pass the env block. Logs go to stderr (stdout is the MCP wire); most clients
show them in their MCP log view.

### After the package is published

When the package is published, `uvx lkap-mcp` and a git-source install
(`uvx --from git+<remote>#subdirectory=mcp lkap-mcp`) will replace the
`--project` form. Neither works today, and the console does not offer them as
snippets (R-V3-17).

## 3. Configuration

Every setting comes from the environment the client spawns the server with.
No `.env` file is read.

| Variable | Default | Meaning |
|---|---|---|
| `LKAP_API_URL` | `http://127.0.0.1:8080` | The api origin. |
| `LKAP_API_KEY` | — (required) | The agent key (`lkap_…`). |
| `LKAP_WORKSPACE` | — | Sent as `X-Workspace`. It is only needed when the key's user belongs to several workspaces. |
| `LKAP_MCP_READ_ONLY` | `0` | `1` exposes only read tools, whatever the key's scopes are. |
| `LKAP_MCP_INLINE_SECRETS` | `on` | `off` refuses pasted secret values and requires `env:`/`file:` references. |
| `LKAP_MCP_ALLOW_DIAL` | `0` | `1` is the process half of the dial gate. |
| `LKAP_MCP_CLIENT` | the client's `clientInfo.name` | Overrides the client name used for attribution. |
| `LKAP_MCP_MAX_CHATS` | `3` | The number of test chats open at once (1–20). |
| `LKAP_MCP_LOG_LEVEL` | `INFO` | The stderr log level. |
| `LKAP_MCP_WEBHOOK_SECRET_DIR` | `~/.config/lkap/webhooks` | Where `webhook_create` writes the one-time signing secret (a 0600 file). |
| `LKAP_MCP_MAX_IN_FLIGHT` | `4` | The number of concurrent api requests. |
| `LKAP_MCP_REQUEST_TIMEOUT_S` | `30` | The per-request api timeout. |
| `LKAP_MCP_MAX_RETRY_AFTER_S` | `10` | The longest `Retry-After` the client waits for its single 429 retry. |

`LKAP_MCP_TRANSPORT`, `LKAP_MCP_HTTP_*`, `LKAP_MCP_PUBLIC_URL` and the session
limits belong to the remote mode.

### Read-only mode

A key with only read scopes already sees read tools only: write tools are not
registered, so the client cannot call them. `LKAP_MCP_READ_ONLY=1` applies the
same restriction to a key that holds write scopes, which is useful for letting
an agent look around with a key that could do more. A few read tools have an
optional write path: `connection_fleet` actions, `agent_versions` restore,
`agent_limits` updates and non-GET `api_request`. Without the write scope, or
in read-only mode, these tools present as read-only and refuse the write path
with `forbidden` (R-V3-23 f).

### Secrets

When a tool takes a secret (a LiveKit key and secret, or a vendor API key), it
accepts either:

- the value itself (inline). The value goes straight to the api's vault and
  never appears in a result, plan, error or log line. A `plan` shows
  `<inline secret>` in its place; or
- a reference: `env:NAME`, `file:/abs/path`, `file:~/.config/lkap/dev.env#NAME`,
  or `raw:<value>` to pass a value that starts with `env:` or `file:`.

No tool ever returns a stored secret. Inline values do stay in your client's
own local transcript, which is why the console shows the one-time warning.
Prefer references when the values already live in an env file (R-V3-3).
`LKAP_MCP_INLINE_SECRETS=off` enforces references.

### Outbound calls (dial opt-in)

`call_place` and `call_control` exist only when all three gates are open
(R-V3-7):

1. the key has `calls:write` (the console checkbox);
2. the process runs with `LKAP_MCP_ALLOW_DIAL=1`;
3. each call passes `confirm=true`.

The workspace dialing policy stays console-only.

### Destructive steps

The following tools return `needs_confirmation` until they are called again
with `confirm=true` (R-V3-6):

- `lkap_delete`
- `connection_rotate`
- `connection_fleet` stop and restart
- `agent_archive`
- `agent_versions` restore
- `call_place` and `call_control`
- any non-GET `api_request`

Every write tool also accepts `plan=true`, which returns the requests the tool
would send without sending them.

### Test chat

The `chat_*` tools join the agent's LiveKit room from this process, using the
`livekit` rtc wheel that the project already depends on. They need a ready
worker on the agent's connection; `chat_start` answers `no_worker` with next
steps when there is none. If the wheel cannot load on your machine, the tools
answer `chat_unavailable`, and the console's Test chat is the fallback.

## Remote (HTTP) mode

`lkap-mcp --http` runs the same server as its own service, for agents that do
not run on your machine: Claude Code on the web, Codex cloud, a shared team
endpoint, CI. It is never part of the api process (R-V3-4, R-V3-15). In
production it sits behind Caddy at `https://<api origin>/mcp`. The operator
guide (enable, TLS, limits, revoke, dial gate) is the "Remote MCP" section of
`docs/RUNBOOK.md`.

- **Auth.** The service has no key of its own. Every request carries
  `Authorization: Bearer <key>`, and the key on the opening `initialize`
  request is the key that MCP session uses for all of its api calls. Its scopes
  shape that session's tool list, as in stdio mode. The session id is bound to
  the key, so a request with any other key is refused with `403`.
- **URL.** Use `LKAP_MCP_PUBLIC_URL`, for example `https://lkap.example.com/mcp`.
  The console's Connect dialog offers **Remote (HTTP)** only when that value is
  configured.

### Connect a client

Keep the key in user-level config, never in a committed file.

**Claude Code:**

```bash
claude mcp add -s user --transport http lkap <LKAP_MCP_PUBLIC_URL> --header "Authorization: Bearer <key>"
```

**Codex CLI.** These forms were verified against codex-cli 0.153.4 (R-V3-25).
This is the console's form for `~/.codex/config.toml`:

```toml
[mcp_servers.lkap]
url = "<LKAP_MCP_PUBLIC_URL>"

[mcp_servers.lkap.http_headers]
Authorization = "Bearer <key>"
```

On a shared machine, keep the key in the shell environment instead of in
`config.toml`. Codex reads the env var named by `bearer_token_env_var` and
sends it as the bearer:

```toml
[mcp_servers.lkap]
url = "<LKAP_MCP_PUBLIC_URL>"
bearer_token_env_var = "LKAP_API_KEY"
```

The same, from the command line (then `export LKAP_API_KEY=<key>` in the
shell that runs Codex):

```bash
codex mcp add lkap --url <LKAP_MCP_PUBLIC_URL> --bearer-token-env-var LKAP_API_KEY
```

`codex mcp list` shows `Auth = Bearer token` for either form. Codex rejects a
literal `bearer_token = "…"` for streamable-HTTP servers; use one of the two
forms above. `env_http_headers` (header name → env var name) also works.

**Cursor and other clients** (generic `mcpServers` JSON):

```json
{ "mcpServers": { "lkap": { "url": "<LKAP_MCP_PUBLIC_URL>", "headers": { "Authorization": "Bearer <key>" } } } }
```

### What differs from stdio

| | stdio | remote |
|---|---|---|
| Key | `LKAP_API_KEY` in the spawn env | the request's bearer; the service refuses to start if `LKAP_API_KEY` is set |
| Inline secrets | allowed unless `LKAP_MCP_INLINE_SECRETS=off` | the same (they travel inside the TLS request to the api vault) |
| `file:` references | allowed | refused: `ref_unavailable_in_http_mode` |
| `env:` references | your environment | the **service's** environment (operator-provisioned) |
| `kb_add_document(file_path=)` | allowed (25 MB) | refused; use `text` or `url` |
| `webhook_create` | writes the secret to a 0600 file | unavailable; create webhooks in the console |
| Test chats | 3 per process | 3 per session, 20 per service |
| Dialing | `LKAP_MCP_ALLOW_DIAL=1` on your process | `LKAP_MCP_ALLOW_DIAL=1` on the service (an operator decision) |

### Limits and answers

| Answer | When |
|---|---|
| `401` | No bearer, a bearer that is not an `lkap_` key, or a key the api does not accept at `initialize`. |
| `403` | A `Host` that is not the public host, an `Origin` that is not the public origin, or a key other than the one that opened the session. |
| `404` | An unknown or ended session. Clients start a new session on this answer. |
| `413` | A request body over 1 MB. |
| `429` + `retry_after_s`, `Retry-After` | A 6th session for one key (at `initialize`, before any session exists). |
| tool result `rate_limited` | A 121st `tools/call` in a minute on one session, or an 11th tool call in flight on one session. Sent with HTTP 200 and the session stays open: `status` is `429`, `details.retry_after_s` says how long to wait, and `details.scope` is `calls_per_min` or `in_flight`. Wait, then retry the call. |
| tool result `unauthorized` | The key was revoked or expired mid-session. The session is closed after that call; reconnect with a new key. |
| tool result `call_timeout` | A call ran over 60 s, or over its own `timeout_s` plus 15 s. |
| tool result `no_session` | A chat call that cannot be tied to a live session. Reconnect. |
| tool result `unknown_chat` | A `chat_id` that is ended, idle-closed, never started, or owned by another session (of this key or another). Start a new chat. |

A session with no request for 30 minutes is closed, together with its chats.
An open `GET` stream does not count as a request. The settings are
`LKAP_MCP_MAX_SESSIONS_PER_KEY` (5), `LKAP_MCP_CALLS_PER_MIN` (120),
`LKAP_MCP_MAX_IN_FLIGHT_PER_SESSION` (10), `LKAP_MCP_CALL_TIMEOUT_S` (60),
`LKAP_MCP_SESSION_IDLE_S` (1800), `LKAP_MCP_MAX_BODY_BYTES` (1048576) and
`LKAP_MCP_MAX_CHATS_TOTAL` (20).

### Run it locally

```bash
LKAP_API_URL=http://127.0.0.1:8080 uv run --project <checkout>/mcp lkap-mcp --http
```

This serves `http://127.0.0.1:8090/mcp`, the defaults of
`LKAP_MCP_HTTP_HOST` and `LKAP_MCP_HTTP_PORT`. In dev, loopback hosts are
accepted without `LKAP_MCP_PUBLIC_URL`. With `LKAP_ENV=prod`, the service
refuses to start unless `LKAP_MCP_PUBLIC_URL` is an `https://` url. The
compose files run it as the `mcp` service; see `docs/RUNBOOK.md`.

## Claude Code skill and plugin

`mcp/claude-plugin/` packages the platform guide as a Claude Code skill
(`skills/lkap/SKILL.md`, plus copies of the recipes above it, kept in sync by
`scripts/export_contracts.sh`) and, together with `.mcp.json`, as a full
plugin that also declares the `lkap` MCP server itself — so a fresh
checkout gives Claude Code both the server and the guide in one step,
without hand-editing any config file. `.mcp.json` names the server as
`uv run --project ${LKAP_CHECKOUT}/mcp lkap-mcp` with `LKAP_API_URL` and
`LKAP_API_KEY` expanded from your shell environment (`${VAR}` expansion,
per Claude Code's plugin config); export `LKAP_CHECKOUT`, `LKAP_API_URL` and
`LKAP_API_KEY` before loading the plugin, the same three values the stdio
snippet above uses. No key is ever written into a repo file.

If you already added `lkap` with `claude mcp add` (the console dialog,
above), install just the skill:

```bash
<checkout>/scripts/install_claude_skill.sh
```

This copies `skills/lkap` to `~/.claude/skills/lkap` (`--project` installs to
`./.claude/skills/lkap` for one project instead). Re-run it after an update
to refresh the recipes.

To load the whole plugin — the skill and the MCP server together — for one
session:

```bash
claude --plugin-dir <checkout>/mcp/claude-plugin
```

or persist it across sessions by registering the checkout as a local
marketplace once, then installing from it like any other plugin:

```bash
claude plugin marketplace add <checkout>/mcp/claude-plugin
claude plugin install lkap@lkap
```

Verified against Claude Code 2.1.226, 2026-09-24: `claude plugin validate
mcp/claude-plugin` (and its `marketplace.json`) both pass with no errors,
and `claude --plugin-dir <checkout>/mcp/claude-plugin plugin details lkap`
reports the plugin with one skill (`lkap`) and one MCP server (`lkap`)
loaded, at no cost beyond ~100 always-on tokens:

```
lkap 0.1.0
  Build and operate LKAP (LiveKit Agent Platform) voice/video agents from Claude Code: ...

Component inventory
  Skills (1)  lkap
  Agents (0)
  Hooks (0)
  MCP servers (1)  lkap  (tool schemas resolved at runtime; not counted)
  LSP servers (0)
```

The `claude plugin marketplace add` / `claude plugin install lkap@lkap` pair
above was run end to end (against a temporary `HOME`, never the real one)
and produces `lkap@lkap` as an enabled, `user`-scope plugin in
`claude plugin list`.

Codex CLI and other non-Claude-Code agents do not read a Claude Code plugin;
they read `AGENTS.md` at the repository root instead — see
[Codex CLI](#codex-cli) above and `AGENTS.md` itself.

## Development

```bash
cd mcp
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ --strict && uv run pytest -q -m "not live"
```

- **Scratch api.** The tests run the real api in-process
  (`lkap_api.main.create_app` over `httpx.ASGITransport`, a temp SQLite file,
  `FakeEmbedder`) and drive the tools through the real `mcp.ClientSession`
  over memory streams. Only external boundaries are faked:
  - LiveKit, with the api tests' in-process Twirp server;
  - vendor and tool hosts, with `respx`;
  - the room, with `FakeRoomTransport`.

  `tests/conftest.py` imports helpers from `api/tests`, so the tests need the
  whole repository checked out.
- **Scenario.** `tests/test_scenario_build_agent.py` runs the "agent builds an
  agent" recipe end to end, offline: connection → provider key → agent from
  `insurance_claim` → knowledge base → HTTP tool with a dry run → attach →
  update → validate → test chat → publish → activity.
- **Catalog snapshot.** `tests/tools.snap.json` pins every tool as a
  full-scope client sees it: name, description, annotations, input and output
  schemas, and `_meta`. It also records each tool's declared scopes, kind and
  gate, and the tool list of each console preset. A change to the catalog is a
  deliberate diff. After an intended change, regenerate the snapshot and
  review it:

  ```bash
  uv run pytest tests/test_catalog_snapshot.py --snapshot-update
  git diff tests/tools.snap.json
  ```
- **CI.** The `mcp` leg of `.github/workflows/python.yml` runs the gate above.
  The contracts workflow checks that `src/lkap_mcp/generated/` matches
  `contracts/generated` (`scripts/export_contracts.sh`).
- **Smoke.** `scripts/smoke_v2.sh --with-mcp` adds an MCP step to the
  compose-dev smoke. The step drives this server over stdio with a
  temporary agent key.
