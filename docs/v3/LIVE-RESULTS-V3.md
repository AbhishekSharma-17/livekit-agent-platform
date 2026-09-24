# LKAP v3: live acceptance results (V3-07)

**Status: in progress — paused at step 0a.** Headless Claude Code cannot authenticate with the user's existing login (see §0). Every step that doesn't need Claude Code has run. The Claude Code steps (2a, 2b-sim, 4, 5 via Claude, 6a–6c, 6d via Claude, 6h) are waiting on the user.

**Run:** 2026-09-24, from 09:00 UTC, by V3-07. Repo HEAD `b8b10a6` (V3-06F), with no working-tree changes from this run. LiveKit Cloud project `wss://your-project.livekit.cloud`.

## Protocol as amended by the user's login decision

The user chose **"Use my existing login"**. That overrides R-V3-38's scratch `CLAUDE_CONFIG_DIR` and `CODEX_HOME`:

- **Claude Code**
  - `claude -p` runs with the user's own login and default config dir.
  - MCP comes only from `--mcp-config <scratch json> --strict-mcp-config`.
  - The skill is installed with `scripts/install_claude_skill.sh --project` into `<scratchpad>/v307/project`, which is the cwd of every run.
  - Never run: `claude mcp add`, `claude plugin install`, `claude plugin marketplace add`.
  - So the card's step 0b (a scratch config showing zero servers) doesn't apply. The isolation evidence is the `system/init` message of each stream: `mcp_servers` lists only `lkap`. The dialog's `claude mcp add` lines are verified by inspection only.
- **Codex**
  - Uses the user's own login and home.
  - The MCP server is passed only through `-c` overrides.
  - `codex mcp add` is never run, and `~/.codex/config.toml` is never printed or edited.
- **Real LiveKit values**
  - Read in memory from the `lkap-api` entry of `.claude/launch.json` (outside the repo). They go only to:
    - the worker's env;
    - a 0600 scratch reference file, `<scratchpad>/v307/lk-ref.conf` (not yet written, because step 2a hasn't run), for `connection_create`'s `file:…#KEY` refs.
  - The card's `~/.config/lkap/dev.env` is not used.

## 0. Preflight and setup

| Item | Result |
|---|---|
| 0a Claude Code auth | **Blocked.** See below |
| 0b config isolation | Replaced by the `system/init` evidence. Preflight init: `mcp_servers=[{name: lkap, status: connected}]`, 58 `mcp__lkap__*` tools, built-ins limited to `Skill` and `ToolSearch` (`--tools`), skill list contains `lkap` (project skill) |
| 0c `lk agent list` before (read-only) | One agent: `other-project-agent` (`CA_v5XKrAreaGbw`, ap-south). The user's `lkap-agent` worker log (`<scratchpad>/worker.log`, 16 lines, last write 14:08 local) was recorded before the run |
| 0d V3-06F in tree + `mcp/` gate | `grep -c load_tool_modules mcp/src/lkap_mcp/http.py` → `0`. Gate: ruff, format, `mypy --strict` clean; **614 passed** (52.4 s) |
| 0e config snapshot | `snapshot.py before`: mtime + sha256 prefix of `~/.claude/settings.json`, `settings.local.json`, `plugins/{installed_plugins,known_marketplaces,config}.json`, `~/.codex/config.toml`, `api/data/lkap.db`; and hashes of `~/.claude.json`'s MCP tables only (user `mcpServers` = `[codex]`, one project entry). No contents are read or printed |

**Why 0a is blocked**
- The first run was:
  ```
  claude -p "Reply with the single word ok. Do not call any tool." --output-format stream-json --verbose --model sonnet --max-budget-usd 0.5 --max-turns 2 --tools Skill,ToolSearch --allowedTools mcp__lkap mcp__lkap-remote Skill ToolSearch --strict-mcp-config --mcp-config <v307>/stdio.mcp.json
  ```
  (cwd `<v307>/project`). It answered `Failed to authenticate: OAuth session expired and could not be refreshed`. Session `75118561-e79f-4648-a992-2d74cbbd7980`, cost $0.
- `claude auth status` → `{"loggedIn": false, "authMethod": "none"}`. The result is the same with a clean env (`env -i HOME PATH USER …`).
- So the standalone `claude` 2.1.226 CLI has no login of its own on this machine. The Claude desktop app authenticates its own sessions through its host, and a spawned CLI can't refresh that token.
- V3-07 did not improvise a fallback (R-V3-38).

## 1. Scratch stack (up during the run, now stopped)

| Piece | Value |
|---|---|
| launcher | `<scratchpad>/v307/v307.py`. It reads launch.json in memory and passes secrets through env only (never argv, never printed) |
| scratch api | `:8121`, `PORT=8121`, `LKAP_API_BASE_URL=http://127.0.0.1:8121`, `LKAP_ENV=dev`, SQLite `<v307>/data/lkap.db` migrated to `v3_001_agent_keys` (fresh DB, `alembic upgrade head`), `api/data/models` copied beside it |
| scratch secrets | Fresh `LKAP_MASTER_KEY`, admin and service tokens in `scratch-tokens.json` (0600) |
| placeholder LiveKit | `LIVEKIT_URL=wss://v307-placeholder.livekit.cloud`, `LIVEKIT_API_KEY=APIv307placeholder`, a random secret, `LKAP_AGENT_NAME=lkap-v307-dud` |
| bootstrap | The seeded `default` connection has url `wss://v307-placeholder…`, fingerprint `…lder` and `agent_name=lkap-v307-dud`. It can reach nothing |
| remote MCP | `mcp/.venv/bin/lkap-mcp --http` on `127.0.0.1:8090`, `LKAP_API_URL=http://127.0.0.1:8121`, `LKAP_MCP_PUBLIC_URL=http://127.0.0.1:8090/mcp`. Env has no `LKAP_API_KEY`, service, admin or master token (asserted by the launcher). `/healthz` → `{"status":"ok"}` |
| worker | Not started yet (step 3 needs step 2a's connection id) |
| scratch web / dialog | Not started. Every key was minted with `POST /v1/api-keys` and the dialog's exact body (`kind=agent`, `client=claude-code`, preset scopes, `expires_at` +7 d). **The dialog leg was not exercised live** |

**Real key fingerprints** (sha256 prefix only):

| Key | Fingerprint |
|---|---|
| `LIVEKIT_API_KEY` | `sha256:b231e1ac3925`; the console shows `…X9ea` |
| `LIVEKIT_API_SECRET` | `sha256:baa329978a6c` |

These match V2-20's.

**Keys minted (scratch api only)**

| Label | Prefix | Preset | State |
|---|---|---|---|
| `claude-code local` (stdio leg) | `lkap_Cxx` | Operator | active; `last_client=lkap-mcp` after the preflight's `initialize` |
| `remote` | `lkap_WJb` | Operator | active |
| `rate` | `lkap_HNu` | Builder | active |
| `rev-http` | `lkap_xIU` | Builder | revoked (6e) |
| `rev-stdio` | `lkap_Xa2` | Builder | revoked (step 5 revoke) |

## 2. Steps

| Step | Status | Evidence |
|---|---|---|
| 0 preflight | **partial**: blocked at 0a | §0 |
| 1 scratch api | passed | §1 |
| 2 key mint | passed (API form) | Keys above. The dialog leg was not exercised |
| 2a connection by reference | not run | needs Claude Code |
| 2b-sim inline | not run | needs Claude Code |
| 3 worker | not run | needs 2a's connection |
| 4 build / test / publish | not run | needs Claude Code |
| 5 safety: revoke mid-session (stdio) | **passed** (python `mcp` client) | See 5-revoke below |
| 5 safety: delete / dial / secret via Claude | not run | needs Claude Code |
| 6 remote prerequisites | passed, one part not observed | Started with none of the four forbidden variables in its env. `me` over HTTP → `{"ok": true, "transport": "http", "workspace": "default"}`; 58 tools, `call_place` absent. **Not observed:** that the `initialize`-time `/v1/api-keys/self` call carries `X-LKAP-Client: lkap-mcp/<v>` without `client=`. The api ran at INFO, which doesn't log headers. `last_client=lkap-mcp` was observed. The resume runs the api at DEBUG for 6b |
| 6a–6c | not run | needs Claude Code |
| 6d rate limit, primary proof | **passed** | See 6d below |
| 6d via Claude Code | not run | needs Claude Code |
| 6e revoke the remote key | **passed** | See 6e below |
| 6f | **passed** | See 6f below |
| 6g Codex | **passed** | See 6g below |
| 6h plugin form | not run | needs Claude Code |
| 7 teardown | done for this pause | api and remote MCP stopped (SIGINT, both exited within 15 s); no worker was started |

**5-revoke (stdio).** A python `mcp` client (`mcp_check.py revoke rev-stdio stdio`) spawns `uv run --project mcp lkap-mcp` and calls `me` (`ok`, `transport: stdio`). The key is then revoked (`DELETE /v1/api-keys/{id}` → 204). The next `me` on the same process → `ok=false, code="unauthorized", status=401, "invalid, revoked or expired API key"`.

**6d, primary proof.** The service was restarted with `LKAP_MCP_CALLS_PER_MIN=5`. `mcp_check.py rate` on one streamable-HTTP session:
- Calls 1–5 → ok.
- Call 6 → in band with HTTP 200:
  ```
  {"ok": false, "error": {"code": "rate_limited", "status": 429, "message": "more than 5 tool calls in a minute on this MCP session", "details": {"retry_after_s": 59.9, "limit": 5, "scope": "calls_per_min"}}, "next_steps": ["wait retry_after_s seconds, then retry this call"]}
  ```
- After `sleep 61`, call 7 on the **same** session (`5a3adbf6…`) → `ok=True`.
- The service log has one `mcp_call_rate_limited`. The service was then restarted with the defaults.

**6e.** `mcp_check.py revoke rev-http http`:
- `me` → ok.
- Revoke → 204.
- `me` → `ok=false, code="unauthorized", status=401`.
- The next call on that session raises `McpError: Session terminated`.
- `curl` with the old `Mcp-Session-Id` → **404**.

**6f.** `curl` `initialize` with no bearer → **401**. With a valid bearer and `Origin: http://evil.example` → **403**.

**6g.** codex-cli **0.153.4**, logged in with ChatGPT (the user's own home). Two checks:
- **Write-free:**
  ```
  codex mcp get lkap -c 'mcp_servers.lkap.url="http://127.0.0.1:8090/mcp"' -c 'mcp_servers.lkap.bearer_token_env_var="LKAP_V307_KEY"'
  ```
  → `transport: streamable_http`, `bearer_token_env_var: LKAP_V307_KEY`. `codex mcp list` with the same overrides shows `lkap … enabled  Bearer token`.
- **Live smoke:**
  ```
  codex exec --ignore-user-config --ephemeral --skip-git-repo-check --sandbox read-only --json -c approval_policy="never" -c mcp_servers.lkap.url=… -c mcp_servers.lkap.bearer_token_env_var=LKAP_V307_KEY "Call the lkap MCP server's me tool once …"
  ```
  - `--ignore-user-config` means `config.toml` isn't loaded; auth still comes from the Codex home.
  - The key is only in that process's env.
  - Result: `mcp_tool_call lkap/lkap_guide` ok, then `mcp_tool_call lkap/me` ok → "Workspace slug: `default`, Transport: `http`, Key scopes: 10". Thread `01a0d2a8-af2e-7d91-8086-ddd0c873c7c9`.
- The `bearer_token_env_var` form was used rather than `http_headers`, so the key never appears in argv.

## 3. Isolation incidents (recorded in full)

1. **`ps` printed the user's api argv into this agent's context.**
   - An early `ps -axo …,command` listed the user's `:8080` api launcher. Its command line carries the LiveKit key and secret, the master key and the dev tokens, because launch.json still puts them in argv (REVIEW-V2 §7, already an open user item).
   - The values were not written to any file or repeated anywhere. From then on only `ps -o pid=,comm=` was used.
2. **A `codex mcp list` override captured the user's real server table.**
   - `codex mcp list -c 'mcp_servers={lkap={…}}'` merges rather than replaces the table, so its output (13 lines, the user's real Codex servers) went to a scratch file.
   - Only the header, the first row (`chrome-devtools  npx -y chrome-devtools-mcp@latest`, env `-`) and the `lkap` row were displayed. The file was deleted unread.
   - From then on, `codex mcp get lkap` and `codex exec --ignore-user-config` were used.
3. **A `uv run` parent didn't forward SIGINT.**
   - Stopping the first remote service (`uv run … lkap-mcp --http`) SIGKILLed the `uv` parent after 45 s. Its python child (pid 80362) kept `:8090`.
   - The child was then stopped with SIGINT and exited within 15 s.
   - The first 6d attempt, which ran against that default-limit orphan, is discarded.
   - The launcher now runs `mcp/.venv/bin/lkap-mcp` directly.

## 4. Isolation state at the pause

| Check | Result |
|---|---|
| `~/.claude/settings.json`, `settings.local.json`, `plugins/*.json`, `~/.codex/config.toml` | mtime and sha256 unchanged |
| `~/.claude.json` | MCP tables' hashes unchanged (user `mcpServers` still `[codex]`). Only the mtime moved, which is expected from normal session state (R-V3-38) |
| `~/.claude/skills` | no `lkap` entry (the skill is only in `<v307>/project/.claude/skills/lkap`) |
| transcripts | `~/.claude/projects/-private-tmp-…-scratchpad-v307-project` (accepted by the user) |
| `api/data/lkap.db` | mtime and hash moved: the user's own `:8080` api and `lkap-agent` heartbeats; nothing in this run points at `:8080`. Read-only check (`sqlite3 -readonly`): 0 `api_keys` named `v307%`; 0 `livekit_connections` with a v3/v307 slug or agent name; default connection `agent_name=lkap-agent`; 0 agents, 0 sessions and 0 audit rows since 09:00Z |
| `lk agent list` | still only `other-project-agent` |
| user's `lkap-agent` worker (35308) | never signalled |
| ports `:8121` and `:8090` | free; no `lkap-mcp` or `lkap-v307*` process remains |

## 5. No-secret scan so far

`check_no_secret.py` never prints a value. It reads the real LiveKit key and secret in memory and uses their last 8 characters. It also uses every minted `lkap_` key in full and the scratch master, admin and service tokens, and prints `<file>: OK|HIT`.
- Scanned: all 16 files under `logs/` and `transcripts/`. That includes:
  - the DEBUG-level `mcp-http.log` (450 lines, from a service whose every inbound request carried a bearer key);
  - `api.log`;
  - both stdio stderr files;
  - the Claude and Codex transcripts.
- Result: **every file `OK`**.

## 6. Bugs

None fixed. Two items logged in `_asks.md` under "Open — left by V3-07":
- **V3-07-1:** `uv run` doesn't forward SIGINT (incident 3).
- **V3-07-2:** the Claude Code CLI login prerequisite, a user task.

## Verdict

pending architect
