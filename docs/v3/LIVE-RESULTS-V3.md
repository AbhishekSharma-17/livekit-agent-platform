# LKAP v3: live acceptance results (V3-07)

**Status: complete. Every step of the card was run; the verdict is pending architect.** The run was paused at step 0a on 2026-09-24, because the standalone `claude` CLI had no login. The user then ran `claude login`, and the Claude-driven steps ran on the resume.

**Run**
- **Part 1:** 2026-09-24 09:00–09:12 UTC. Repo HEAD was the V3-06F commit (then `b8b10a6`, now `38f396f` after a history rewrite).
- **Part 2:** 2026-09-24 18:45–19:05 UTC, at HEAD `b44786c`. This includes V4-01 starter templates, so `agent_create(template_id=...)` applies.
- **Environment:** LiveKit Cloud project `wss://your-project.livekit.cloud` (region India South).
- **Claude Code:** `claude` 2.1.226, model `claude-sonnet-5` in every run, the user's own claude.ai login.
- **Codex:** codex-cli 0.153.4, the user's own ChatGPT login.
- **Total Claude cost:** about $5.28 over 18 headless runs.

## Protocol as amended by the user's login decision

The user chose **"Use my existing login"**. That overrides R-V3-38's scratch `CLAUDE_CONFIG_DIR` and `CODEX_HOME`.

**Claude Code**
- Every `claude -p` ran with the user's login and default config dir, with cwd `<scratchpad>/v307/project`.
- MCP came **only** from `--mcp-config <scratch json> --strict-mcp-config`. There was one file per leg:
  - `stdio.mcp.json`: server `lkap`, `bash -c "exec uv run --project <checkout>/mcp lkap-mcp 2>>logs/mcp-stdio.stderr"`, env `LKAP_API_URL`, `LKAP_API_KEY`, 0600;
  - `remote.mcp.json`: server `lkap-remote`, `type: http`, `headers.Authorization: Bearer …`, 0600.

  These are the dialog's `claude mcp add -s user …` lines, expressed as config files. **The `claude mcp add` lines themselves were verified by inspection only.**
- The skill was installed with `scripts/install_claude_skill.sh --project` in the project dir.
- Built-in tools were limited to `--tools Skill,ToolSearch`.
- Permissions: `--allowedTools mcp__lkap mcp__lkap-remote Skill ToolSearch`, and no permission denial occurred.
  - The user's global settings put every run in **`permissionMode: auto`** (see each init message). So the classifier, not `--allowedTools`, may have approved those calls.
  - Three discriminating runs settled the `--allowedTools` spelling, each on a throwaway Builder key (`perm-check`, since revoked) with prompt "Call the lkap me tool once":

    | Run | Permission mode | `--allowedTools` | Outcome | Session |
    |---|---|---|---|---|
    | A | auto | `Skill ToolSearch` | `me` ran, no denial: auto mode approves it | `a8ce1cd3-…` |
    | B | `--permission-mode default` | `Skill ToolSearch` | **denied** (`permission_denials=[mcp__lkap__me]`) | `7166d7fd-…` |
    | C | `--permission-mode default` | `mcp__lkap Skill ToolSearch` | `me` ran, no denial | `ed05f6ed-cf52-4916-a120-204d64612267` |

  - So the server-level form `mcp__lkap` does pre-approve every tool of the server. For a run that doesn't depend on the user's auto mode, add `--permission-mode default`.
- Also on every run: `--output-format stream-json --verbose --max-budget-usd <n> --max-turns <n>`.
- Never run: `claude mcp add`, `claude plugin install`, `claude plugin marketplace add`, or `--dangerously-skip-permissions`.
- Isolation evidence is the `system/init` message of every stream: `mcp_servers` lists only `lkap` or only `lkap-remote`.
- The user's own plugins and hooks still load under their config dir (pinecone and ecc SessionStart hooks are visible in the stream).

**Codex**
- The user's own home. The server was passed only through `-c mcp_servers.lkap.*` overrides.
- `codex mcp add` was never run, and `~/.codex/config.toml` was never printed, read or edited.

**Real LiveKit values**
- Read in memory from the `lkap-api` entry of `.claude/launch.json`, outside the repo. They reached only:
  - the worker's env;
  - `<scratchpad>/v307/lk-ref.conf` (0600, `KEY=value` lines), for step 2a's `file:…#KEY` references. It was deleted at teardown.
- Any command line that could show them used name-only `ps`.

## 0. Preflight and setup

| Item | Result |
|---|---|
| 0a Claude Code auth | Part 1: **blocked**. `claude auth status` → `loggedIn: false`; the run answered "OAuth session expired and could not be refreshed" (session `75118561-…`, $0). Part 2, after the user's `claude login`: **passed**. `claude -p "Reply with the single word ok…"` → `ok` (session `64f7bb88-fe76-4218-b7dd-43c4575198a2`, $0.18). Init: `mcp_servers=[{lkap, connected}]`, 58 `mcp__lkap__*` tools, built-ins `Skill` and `ToolSearch`, `lkap` in `skills` and `slash_commands` |
| 0b config isolation | Replaced by the init-message evidence above, and by §6 |
| 0c `lk agent list` before (read-only) | One agent: `other-project-agent` (`CA_v5XKrAreaGbw`, ap-south). The user's `lkap-agent` worker log (`<scratchpad>/worker.log`) was recorded |
| 0d V3-06F in tree + `mcp/` gate | `grep -c load_tool_modules mcp/src/lkap_mcp/http.py` → `0`. Gate at part 1: 614 passed. Gate at `b44786c` (part 2): ruff, format, `mypy --strict` clean; **718 passed** |
| 0e config snapshot | `snapshot.py before`: mtime + sha256 prefix of `~/.claude/settings.json`, `settings.local.json`, `plugins/{installed_plugins,known_marketplaces,config}.json`, `~/.codex/config.toml` and `api/data/lkap.db`; hashes of `~/.claude.json`'s MCP tables only. No contents were printed |

## 1. Scratch stack

| Piece | Value |
|---|---|
| launcher | `<scratchpad>/v307/v307.py`. It reads launch.json in memory and passes secrets through env only |
| scratch api | `:8121`, `PORT=8121`, `LKAP_API_BASE_URL=http://127.0.0.1:8121`, `LKAP_ENV=dev`, fresh SQLite `<v307>/data/lkap.db` migrated to `v3_001_agent_keys`, `api/data/models` copied beside it. Part 2 ran it at `LKAP_LOG_LEVEL=DEBUG` |
| scratch tokens | Fresh `LKAP_MASTER_KEY`, admin and service tokens (`scratch-tokens.json`, 0600) |
| placeholder LiveKit | `LIVEKIT_URL=wss://v307-placeholder.livekit.cloud`, `LIVEKIT_API_KEY=APIv307placeholder`, a random secret, `LKAP_AGENT_NAME=lkap-v307-dud` |
| bootstrap | Seeded a `default` connection: url `wss://v307-placeholder…`, fingerprint `…lder`, `agent_name=lkap-v307-dud`. It can reach nothing |
| remote MCP | `mcp/.venv/bin/lkap-mcp --http` on `127.0.0.1:8090`, `LKAP_API_URL=http://127.0.0.1:8121`, `LKAP_MCP_PUBLIC_URL=http://127.0.0.1:8090/mcp`, `LKAP_MCP_LOG_LEVEL=DEBUG`. Env has no `LKAP_API_KEY`, service, admin or master token (asserted by the launcher). `/healthz` → `{"status":"ok"}` |
| worker | `lkap-v307-a1`, `LKAP_CONNECTION_ID=ec27d44c…` (cloud-v3), `LKAP_API_BASE_URL=http://127.0.0.1:8121`, `LKAP_WORKER_HTTP_PORT=0`, `dev` mode, packs `insurance_claim,generic`. It registered on LiveKit (`AW_kTYKwtpDUpuY`, India South) and with the scratch api (`installed_providers=28`) |
| scratch web / dialog | Not started. Every key was minted with `POST /v1/api-keys` and the dialog's exact body (`kind=agent`, `client=claude-code`, preset scopes, `expires_at` +7 d). **The dialog leg was not exercised live.** A browser MCP was available in the session: the Claude Browser pane, and ecc's Playwright. The leg was deferred because it was not on the coordinator's resume list. It needs a scratch web on `:3001` (a copy of `web/` with `node_modules` symlinked) |

**Real key fingerprints** (sha256 prefix only):

| Key | Fingerprint |
|---|---|
| `LIVEKIT_API_KEY` | `sha256:b231e1ac3925`; `connection_get` shows `…X9ea` |
| `LIVEKIT_API_SECRET` | `sha256:baa329978a6c` |

**Keys minted (scratch api only; all revoked at teardown)**

| Label | Prefix | Preset | Used for |
|---|---|---|---|
| `claude-code local` | `lkap_Cxx` | Operator | stdio leg 2a–5; revoked in step 5 |
| `remote` | `lkap_WJb` | Operator | remote leg 6a–6d; revoked in 6e |
| `rate` | `lkap_HNu` | Builder | 6d python proof |
| `rev-http`, `rev-stdio` | `lkap_xIU`, `lkap_Xa2` | Builder | mid-session revoke proofs |
| `init-only` | `lkap_tIa` | Builder | the `initialize`-only attribution check |
| `plugin-local` | `lkap_lPF` | Operator | 6h |
| `perm-check` | (Builder) | Builder | the `--allowedTools` discriminating runs |

## 2. Step table

| Step | Status | Evidence (sessions are Claude Code session ids) |
|---|---|---|
| 0 preflight | passed | §0 |
| 1 scratch api | passed | §1 |
| 2 key mint | passed (API form) | The dialog leg was not exercised |
| **2a** connection by reference | passed | §3.1. Session `e2f4c1ff-7e9f-40c9-a34e-1e24b5731eb4` |
| **2b-sim** inline | passed | §3.2. Session `981aba06-5ef4-401a-9b32-bb534324d028` |
| **3** worker | passed | §1 row "worker". Served every chat below |
| **4** build → test → publish (stdio) | passed | §3.3. Sessions `f061d2ba-…` (plain prompt) and `3f73c818-…` (`/lkap`) |
| **4** skill evidence | passed with `/lkap` | The plain prompt did **not** invoke the skill. The `/lkap` prompt did (§3.3) |
| **5** delete → 409 → archive → purge | passed | §3.4. Session `7c0e2e66-9196-4fa5-8c7a-9191326b5be6` |
| **5** dial and secret | passed | §3.4. Session `d993cee7-41be-426f-a412-d3f8f932fe11` |
| **5** revoke | passed | Mid-session (python client): `ok` → revoke 204 → `unauthorized`. Claude after the revoke (session `a9acfaf1-…`): `me` → `ok=false, code="unauthorized", status=401` |
| **6** remote prerequisites | passed, one part not observed | `me` → `transport: http`. An `initialize` + `tools/list`-only session with a fresh key set `last_client=lkap-mcp`, so the `initialize`-time `/v1/api-keys/self` call carries the `lkap-mcp` product. **Not observed live:** the exact header text (no `client=`). The api doesn't log request headers even at DEBUG, and no test in `test_http_mode.py` asserts that header either |
| **6a** register the remote key | passed (config-file form) | `remote.mcp.json` ≙ the dialog's `claude mcp add -s user --transport http lkap-remote … --header`. Init: `mcp_servers=[{lkap-remote, connected}]` |
| **6b** build over remote | passed | §3.5. Session `2861ca0b-357a-4407-be5d-85d85b1e41d3` |
| **6c** delete checks over remote | passed | needs_confirmation → 409 → archived → deleted. Session `0c7c5f71-9093-4518-b24e-6c806e4d391c` |
| **6d** rate limit, python (primary) | passed | §3.6 |
| **6d** rate limit, Claude | passed | §3.6. Session `6f28833c-61f5-45db-a565-4d1fa5f5a19c` |
| **6e** revoke the remote key | passed | §3.6 |
| **6f** `curl` checks | passed | no bearer → 401; foreign `Origin` → 403 |
| **6g** Codex | passed | §3.7 |
| **6h** plugin form | passed with a documented variant | §3.8. Sessions `614874f2-…` (strict: plugin and skill load, the server is dropped) and `7a9cfe58-139b-4f28-b285-d9c8ba7e3977` (strict + the plugin's `.mcp.json`: `me` answers) |
| **7** teardown | done | §6 |

## 3. Step details

### 3.1 Step 2a: connect by reference

**Prompt:** "Connect my LiveKit project … url `wss://your-project.livekit.cloud`; the API key and secret are the references `file:<v307>/lk-ref.conf#LIVEKIT_API_KEY` and `file:<v307>/lk-ref.conf#LIVEKIT_API_SECRET` … name the connection `cloud-v3` with agent name `lkap-v307-a1`, external mode, make it the default. Test it, then list the connections…"

**Calls**

| Call | Result |
|---|---|
| `lkap_guide` | ok |
| `me` | ok |
| `connection_create{name: cloud-v3, api_key: "file:…#LIVEKIT_API_KEY", api_secret: "file:…#LIVEKIT_API_SECRET", agent_name: lkap-v307-a1, deployment_mode: external, is_default: true, test_first: true}` | `ok`. Connection `ec27d44c4c1d43e8945549315b3d832d`, `deployment_type=cloud`, fingerprint **`…X9ea`** (matches the key's last 4) |
| `connection_test` | `connected`, 328.6 ms. Capabilities: `inference_available`, `sip_enabled`, `egress_enabled`, `ingress_enabled`, `cloud_hosting`, `noise_cancellation_tier=krisp`, `turn_detector_mode=hosted` |
| `connection_list` | `cloud-v3` is the default; `default` (placeholder) is non-default |

The references were passed exactly as written. **No-secret check:** the transcript, `mcp-stdio.stderr`, the DEBUG `api.log` and the audit dump are all `OK`.

### 3.2 Step 2b-sim: inline secret with synthetic values

The synthetic values were a key `APIv307sim<12 hex>` (22 chars) and a secret `LKSIM-v307-<40 chars>` (51 chars), kept in `sim-values.json` (0600).

**Prompt:** create `cloud-v3-inline-sim` (agent name `lkap-v307-sim`) with the url `connection_get(cloud-v3)` returns, the two values pasted inline, `test_first=false`, `is_default=false`, no worker; then delete it with `lkap_delete`, confirming.

**Calls**

| Call | Result |
|---|---|
| `connection_get(cloud-v3)` | returned the url |
| `connection_create{… inline values …}` | `ok`. Fingerprint `…b773`, which is the synthetic *key*'s last 4 (checked programmatically). No `api_key` or `api_secret` field in the result |
| `lkap_delete{kind: connection, id: 2d9c38e4…, confirm: true}` | `deleted: true` |
| `connection_list` | only `cloud-v3` and `default` remain |

Claude's own wording ("last 4 chars of the API secret") was wrong: the fingerprint is the key's last 4. That is an observation, not a platform issue.

**Where the synthetic values appear** (`sim_check.py`, counts only)

| Place | Count / result |
|---|---|
| stream transcript: `tool_use` input | 2 |
| stream transcript: `tool_result` | 0 |
| stream transcript: assistant text | 0 |
| stream transcript: other | 0 |
| stream transcript verdict | **`HIT (arguments only)`** |
| `mcp-stdio.stderr` | `OK` |
| DEBUG `api.log` | `OK` |
| audit dump (22 rows, including rows 11 `POST /v1/connections` and 12 `DELETE …` attributed `lkap-mcp / claude-code / connection_create, lkap_delete`) | `OK` |

**Client-side copies (recorded).** The persisted session file `~/.claude/projects/<scratch-project>/981aba06….jsonl` holds the values in:
- the user prompt (2);
- the `tool_use` (2);
- `queue-operation` records (2);
- **8 hook `stdout` echoes** from the user's own `PreToolUse`/`PostToolUse` hooks on `mcp__lkap__connection_create`.

A path-only search of `~/.claude` found the value in that one session file and nowhere else; `~/.claude.json` has 0. This is R-V3-3's acknowledged client-side exposure, widened by user hooks (ask V3-07-4). The part-1 note in the earlier version of this file ("audit OK") was based on a request that 422'd (`limit` > 200). It was redone with paging.

### 3.3 Step 4: build, test and publish over stdio

**Prompt** (`prompt-build.txt`): build "FNOL intake (stdio)" on `cloud-v3` from the `insurance_claim` starter; a KB "v307 policy notes" with three policy lines (the HO-3 burst-pipe limit, the flood rider `-FR`, 30-day reporting); an HTTP tool `lookup_weather` GET `https://httpbin.org/anything?city={{city}}` (allowed host `httpbin.org`), returning `args`; attach, validate, a chat about a flooded basement (ask about groundwater coverage and the Austin weather), end, publish.

**Call sequence** (plain prompt, session `f061d2ba-5cdf-4c31-b989-19137f0ddde3`, 23 turns, 107 s, $0.72)

| Call | Result |
|---|---|
| `lkap_guide`, `me` | ok |
| `lkap_describe(recipe, insurance-intake-agent)` | ok |
| `connection_list` | ok |
| `agent_create{template_id: insurance_claim, connection_id: ec27d44c…}` | slug `fnol-intake-stdio`; seeded 2 pack KBs; validation ok, 1 warning (auto-inject disables preemptive generation) |
| `kb_create` | ok |
| `tool_create_http` | dry run **200**, `{"city": "Austin"}`, 1288 ms |
| `kb_add_document(wait)` | **ready**, 1 chunk |
| `agent_attach` | 3 KBs, plus the tool |
| `agent_validate` | `ok: true`, no errors |
| `chat_start` | greeting "I can start the claim while we talk. First, are you and everyone else in a safe place?" |
| `chat_send` ×2 | see the session row below |
| `chat_end` | turns 2 |
| `agent_publish` | `published: true`, `/s/fnol-intake-stdio` |

**Session row** `2d94aab0efaa4629848c05a5bbe01963`: `channel=text`, `status=ended`, started 18:50:01, `ended_at` 18:50:35.31. That is **2.4 s after `chat_end`**: the tool_use is timestamped 18:50:32.86 in the persisted session file, inside the card's 10 s. The row has 5 transcript turns and `usage` (`model_usage`, `turns`). Its events include:
- `tool_call_*` for **`lookup_policy`**;
- **`search_knowledge`** (query "groundwater flood rider HO-3 policy" → `v307-policy-notes.md`);
- **`lookup_weather`** (`{"city":"Austin"}`, 1322 ms, done).

The reply cited the flood rider and `-FR`.

**Audit** (rows 13–22): every write row has `actor_type=api_key` (`718ee595…`) and `client={product: "lkap-mcp", name: "claude-code", tool: <tool>, call: <id>}`. The `clientInfo.name` Claude Code sends is **`claude-code`**.

**`last_client`** for `lkap_Cxx` is `lkap-mcp` (last used 18:50:15, within the 60 s window).

**Skill evidence**
- The plain prompt made **no** `Skill` tool use, and the skill body is absent from the persisted session file. The model worked from `lkap_guide` and `lkap_describe(recipe)` instead.
- Repeated with the prompt prefixed `/lkap` (agent "FNOL intake (stdio skill)", session `3f73c818-6ab0-40b3-a74d-56757fe308c0`, 17 turns, $0.65). The persisted session file has `<command-name>/lkap</command-name>` and the skill body ("lkap — build LKAP voice agents"), both before the first `mcp__lkap__*` call. This is the acceptance.
  - The `stream-json` output doesn't carry slash-command expansions, so the evidence comes from the persisted file (copied to `transcripts/persisted/`).
- That run: `agent_create(template_id)`, `kb_create`, `tool_create_http`, `kb_add_document`, `agent_attach`, `chat_start`, `chat_send` ×2, `chat_end`, `agent_publish`. It did not make a separate `agent_validate` call; it relied on `agent_attach`'s validation.
- Its session `7c77d305…`: text, ended, 5 turns, usage, tools `lookup_policy`, `lookup_weather`, `sync_claim_packet`.
- **KB evidence for this run:** the worker logged `injected knowledge` on every turn: 6 lines, three sessions × two turns. The reply drew on the injected policy line: "standard HO-3 policies do not cover rising groundwater or external flooding without a specific flood rider".
- **Validation:** `agent_attach`'s result carried `{"ok": true, "errors": [], "warnings": ["knowledge.auto_inject: …"]}`. That is the card's "no errors", though not from a separate `agent_validate` call.

### 3.4 Step 5: safety checks

**Delete** (`fnol-intake-stdio`, which had one session)

| Call | Result |
|---|---|
| `lkap_delete{agent}` | `needs_confirmation` ("permanently delete agent …; this cannot be undone") |
| `lkap_delete{confirm: true}` | `conflict`, **409**, "agent still has sessions; delete them first, or archive and purge it" |
| `agent_archive{confirm: true}` | ok, archived 18:54:57 |
| `lkap_delete{confirm: true, purge: true}` | `deleted: true` (204) |

Audit rows 32 and 33 are attributed `agent_archive` and `lkap_delete`.

**Dial** ("call +1 555 0100"): `call_place` and `call_control` are absent from the tool list, which has only `call_get` and `call_list`. The model said the key lacks `calls:write` and dialing is not enabled, and quoted the guide ("Telephony is read-only unless the operator opted in…").

**Secret** ("tell me the API secret of cloud-v3"): `connection_get` returns only fingerprint `…X9ea`. The model refused and offered a reference or `connection_rotate`. Transcript `OK`.

**Revoke**
- **Mid-session, stdio** (python `mcp` client, `mcp_check.py revoke rev-stdio stdio`): `me` ok → `DELETE /v1/api-keys/{id}` 204 → `me` → `ok=false, code="unauthorized", status=401`.
- **Claude, after revoking `lkap_Cxx`:** the stdio server still starts. `identity_failed status=401` in stderr; 7 tools are registered. `me` → `ok=false, code="unauthorized", status=401`, with the hint to mint a new key.

### 3.5 Step 6b: build over remote

Same prompt, prefixed `/lkap`, agent "FNOL intake (remote)", over `lkap-remote`. Session `2861ca0b-357a-4407-be5d-85d85b1e41d3`, 20 turns, $0.71; the skill was loaded (persisted file).

**Calls:** `lkap_guide`, `me`, `connection_list`, `lkap_describe(recipe)`, `agent_create(template_id)`, `kb_create`, `tool_create_http` (dry run 200), `kb_add_document`, `agent_attach`, `agent_validate`, `chat_start`, `chat_send` ×2, `chat_end`, `agent_publish` (every call returned `ok`).

**Session** `b5f36d80…`: text, ended (18:57:45 → 18:58:07), 5 turns, usage, tool `lookup_weather`. The KB reached the reply through auto-inject (the worker logged `injected knowledge`), not a `search_knowledge` call. The reply: "rising groundwater typically requires a specific flood rider".

**Audit** rows 36–45: `actor_id=8382cbf2…` (the remote key), `client={lkap-mcp, claude-code, <tool>}`. The remote session was bound to the second key.

### 3.6 Steps 6d and 6e: rate limit and revocation

**6d, python (primary).** The service ran with `LKAP_MCP_CALLS_PER_MIN=5`; `mcp_check.py rate` used one streamable-HTTP session.
- Calls 1–5 → ok.
- Call 6 → HTTP 200, in band:
  ```
  {"ok": false, "error": {"code": "rate_limited", "status": 429, "message": "more than 5 tool calls in a minute on this MCP session", "details": {"retry_after_s": 59.9, "limit": 5, "scope": "calls_per_min"}}, "next_steps": ["wait retry_after_s seconds, then retry this call"]}
  ```
- After `sleep 61`, call 7 on the **same** session `5a3adbf6…` → ok.

**6d, Claude.** "Call the lkap_guide tool six times…"
- Calls 1–5 → ok.
- Call 6 → `rate_limited`, `retry_after_s 49.1`, `scope calls_per_min`.
- The model reported it with the retry hint, and **the client didn't crash** (the run ended `success`). The service log has one `mcp_call_rate_limited`.
- The service was then restarted with the defaults.

**6e, mid-session** (python, `rev-http`):
- `me` → ok.
- Revoke → 204.
- `me` → `unauthorized` 401.
- The next call on that session raises `McpError: Session terminated`.
- `curl` with the old `Mcp-Session-Id` → **404**.

**6e, Claude.** After revoking `lkap_WJb`, a new `claude -p` got `lkap-remote` with status **`failed`**, because `initialize` → 401. The model said the server was unavailable (session `24ed4139-…`).

### 3.7 Step 6g: Codex

- **Write-free check:**
  ```
  codex mcp get lkap -c 'mcp_servers.lkap.url="http://127.0.0.1:8090/mcp"' -c 'mcp_servers.lkap.bearer_token_env_var="LKAP_V307_KEY"'
  ```
  → `transport: streamable_http`, `bearer_token_env_var: LKAP_V307_KEY`. `codex mcp list` with the same overrides shows `lkap … enabled  Bearer token`.
- **Live smoke:**
  ```
  codex exec --ignore-user-config --ephemeral --skip-git-repo-check --sandbox read-only --json -c approval_policy="never" -c mcp_servers.lkap.url=… -c mcp_servers.lkap.bearer_token_env_var=LKAP_V307_KEY "Call the lkap MCP server's me tool once …"
  ```
  - With `--ignore-user-config`, `config.toml` is not loaded; auth comes from the Codex home.
  - The key was only in that process's env.
  - Result: `lkap/lkap_guide` ok, then `lkap/me` ok → "Workspace slug: `default`, Transport: `http`, Key scopes: 10". Thread `01a0d2a8-af2e-7d91-8086-ddd0c873c7c9`.

### 3.8 Step 6h: plugin form

The run used a fresh Operator key `lkap_lPF`, with `LKAP_CHECKOUT`, `LKAP_API_URL` and `LKAP_API_KEY` in the `claude` process env. The cwd was `<v307>/project-plugin`, which has no project skill, so two `lkap` skills never coexist.

- **`--plugin-dir <checkout>/mcp/claude-plugin --strict-mcp-config`** (session `614874f2-…`):
  - The plugin loads (`lkap@inline 0.1.0`) and so does the skill `lkap:lkap`.
  - But `mcp_servers=[]`: `--strict-mcp-config` also drops a plugin's server. Running without strict would load the user's own MCP servers, which the user's rule forbids.
- **The same, plus `--mcp-config <checkout>/mcp/claude-plugin/.mcp.json`** (session `7a9cfe58-139b-4f28-b285-d9c8ba7e3977`):
  - `mcp_servers=[{lkap, connected}]`, 58 tools; the plugin's `${LKAP_CHECKOUT}`/`${LKAP_API_URL}`/`${LKAP_API_KEY}` expansion works.
  - `lkap_guide` then `me` → workspace **Default** (`default`), key "v307 plugin-local" (`lkap_lPF`).
  - `mcp/README.md` now documents this form (V3-07-3).

## 4. No-secret scan (final)

`check_no_secret.py` never prints a value. It reads the real LiveKit key and secret in memory (last 8 chars) and uses every minted `lkap_` key in full, the scratch master, admin and service tokens, and the synthetic 2b-sim values.

- **Files scanned:** 69, everything under `logs/` and `transcripts/`. That includes:
  - the DEBUG `mcp-http*.log`;
  - the DEBUG `api.log`;
  - the worker log;
  - both stdio stderr files;
  - three audit dumps;
  - every stream transcript;
  - copies of every persisted Claude session file;
  - the Codex transcript.
- **Result:** every file `OK` except the two 2b-sim records:
  - `transcripts/2b-sim.jsonl` → `HIT (arguments only)`, by `sim_check.py`'s classification;
  - its persisted copy → the prompt, the `tool_use`, queue records and user-hook echoes (§3.2).
- No real LiveKit value and no `lkap_` key appears in any file. That includes the transcripts: the 2a references are paths, and the MCP configs carrying keys are not transcripts.

## 5. Bugs and asks

**Fixed:** none in code.

**Documentation fixes**

| Item | Where | Change |
|---|---|---|
| V3-07-1 | `mcp/README.md` "Run it locally"; RUNBOOK §20.1 "Without compose" | Under a process manager, run `mcp/.venv/bin/lkap-mcp --http` directly, or signal the process group. `uv run` in its own session does not forward `SIGINT`, and the orphaned child keeps `:8090`. The V3-07 launcher uses the venv binary |
| V3-07-3 | `mcp/README.md` "Claude Code skill and plugin" | `--strict-mcp-config` drops the plugin's MCP server. Add `--mcp-config <checkout>/mcp/claude-plugin/.mcp.json` |

The `mcp/` gate was re-run after both doc edits: ruff, format, mypy clean, **718 passed**. That includes `test_docs_lint`.

**Logged in `_asks.md` "Open — left by V3-07":**
- V3-07-1: documented.
- V3-07-2: done (the user ran `claude login`).
- V3-07-3: documented.
- V3-07-4: user hooks echo MCP tool inputs, including inline secrets, into the session file. For the architect and user.
- V3-07-5: the skill is not auto-invoked by a plain build prompt. For V3-08 and the architect.
- V3-07-6: the `~/.codex/config.toml` mtime change (§6). For the user.

## 6. Isolation and teardown

**Isolation incidents in part 1** (recorded in full in the part-1 notes):
1. An early `ps -axo command` showed the user's `:8080` api argv, which carries secrets in launch.json. The values were not copied anywhere, and only name-only `ps` was used afterwards.
2. A `codex mcp list -c 'mcp_servers={…}'` override merged with the user's real server table instead of replacing it. Only one row with no env values was displayed, and the file was deleted unread.
3. A `uv run` orphan (V3-07-1).

There were no new incidents in part 2.

**Teardown**

| Check | Result |
|---|---|
| processes | Worker `lkap-v307-a1` (94838), remote MCP (1916) and scratch api (93688): each got SIGINT and exited within 15 s; no SIGKILL was needed. `pgrep` finds no `lkap-v307`, `lkap-mcp --http` or `:8121` process. `:8121` and `:8090` are free |
| rooms | The scratch sessions used rooms `lkap-2d94aab0`, `lkap-7c77d305` and `lkap-b5f36d80`. `lk room list` at teardown shows **0 rooms** in the project (all auto-closed), so nothing needed deleting |
| keys | All 8 scratch keys revoked (7 above plus `perm-check`); `GET /v1/api-keys` shows 0 active |
| reference file | `lk-ref.conf` deleted; `exists: False` |
| scratch DB ciphertexts | `v307/data/lkap.db` held `cloud-v3`'s real key and secret, encrypted under the scratch master key kept beside it in `scratch-tokens.json`. Together they would recover the secret. After teardown, `api_key_ct` and `api_secret_ct` of `cloud-v3` were overwritten with empty blobs: `length` 0/0. The row stays as evidence. The `credentials` table has 0 rows. The only remaining ciphertext is the placeholder `default` connection's |
| `lk agent list` after | unchanged: only `other-project-agent`; no Cloud-deployed agent was added |
| user's `lkap-agent` worker | Now PID 74171, restarted outside this run before part 2 (up 1 h 02 m at teardown); never signalled. Its log has 0 lines mentioning `lkap-v307` or `8121`. Last line: "worker registered with the api connection_id=ff57601e…" |
| user's api `:8080` / web `:3000` | `/v1/health` `ok: true` / 200. Untouched |
| user's `api/data/lkap.db` | mtime and hash moved (the user's own api heartbeats). Read-only check: 0 `v307%` keys; 0 v3/v307 connections; default connection `agent_name=lkap-agent`; 0 `FNOL intake (…)` agents; 0 `v307%` knowledge bases |
| `~/.claude/settings.json`, `settings.local.json`, `plugins/{installed_plugins,known_marketplaces,config}.json` | mtime **and** sha256 unchanged |
| `~/.claude.json` | MCP tables' hashes unchanged (user `mcpServers` still `[codex]`, one project entry). Only the mtime moved (session state) |
| `~/.claude/skills` | still no `lkap` |
| new files under `~/.claude` | only `~/.claude/projects/-private-tmp-…-scratchpad-v307-project{,-plugin}/` (session files, accepted by the user; copied to `transcripts/persisted/`) |
| `~/.codex/config.toml` | **Changed:** mtime 07:01:41 → **09:22:33 UTC**, size 11095 → 11202, hash `72c855e0…` → `999240d4…`. **Not attributable to V3-07:** its last Codex process ended at 09:04 UTC; the 09:10 UTC snapshot still showed the file unchanged; part 2 ran no Codex command. The contents were not read. The user may want to check what wrote it at 09:22 UTC (ask V3-07-6) |

**Kept in the scratchpad:** `<scratchpad>/v307/`, with the scratch DB, logs, transcripts, configs and scripts. Every key in them is revoked.

## Verdict

pending architect
