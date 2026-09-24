# v3 cross-package asks

One line per change a package needs in a file it does not own (`PLAN-V3.md` "Exclusive ownership"). Format: **owner package** — file — the exact edit — who asked — status. The coordinator's decisions in the last section are binding. Rulings that come out of an ask are recorded in `PLAN-V3.md` §8 and referenced here by id.

Created by the architect (2026-09-24); V3-01 owns this file's structure from wave 1 on.

## Grants recorded by the architect (pre-approved edits outside a package's tree)

| # | Owner | File | Edit | Why |
|---|---|---|---|---|
| G1 | V3-00 | `web/src/components/console/settings/api-types.ts` | add `"audit:read"` to `SCOPES` (one line) | the scope must exist in the console before V3-04 builds the presets |
| G2 | V3-01 | `scripts/export_contracts.sh` | one block copying `contracts/generated/{providers.json,builtin_tools.json,schemas/}` into `mcp/src/lkap_mcp/generated/` | contracts gate keeps the MCP's generated resources in sync (D-V3-10) |
| G3 | V3-05 | `.github/workflows/python.yml`, `.github/workflows/contracts.yml` | `mcp` in the matrix; the generated dir in the diff paths | `.github/**` was V2-09's; granted for these two files |
| G4 | V3-08 | `scripts/export_contracts.sh` | one block copying `mcp/src/lkap_mcp/docs/recipes/*.md` into `mcp/claude-plugin/skills/lkap/recipes/` | V3-01 owns the script; one source for recipes (R-V3-16) |
| G6 | V3-08 → V3-04 | `web/src/components/console/settings/connect-agent-dialog.tsx` | **superseded by G8 (R-V3-26)** — the strings live in `snippets.ts` and V3-04 is not in wave 2. Original: V3-08 files the exact step-3 lines (skill install, Codex `AGENTS.md` note) as an ask; V3-04 applies them | V3-04 owns the dialog |
| G5 | V3-02 | `mcp/src/lkap_mcp/server.py` | **none** — chat tools register through `TOOL_MODULES`; if the hook is missing when V3-02 starts, file an ask, do not edit | keeps `server.py` exclusive to V3-01 |
| G7 | V3-06 | `web/src/components/console/settings/snippets.ts` | replace the "best-effort placeholder" comment above `codexSnippet`'s remote form with "verified against codex-cli 0.153.4 (R-V3-25)" — comment only, no string change | R-V3-25; the snippet test stays pinned |
| G8 | V3-08 | `web/src/components/console/settings/snippets.ts` (`skillInstallLine`, `CODEX_AGENTS_NOTE`, `ASK_AGENT_LINE`), `web/tests/console-settings-snippets.test.ts` (their pins) | set the three step-3 strings to the verified install command, the `AGENTS.md` note and the "ask your agent" line; run the web gate; nothing else in `web/` | R-V3-26 replaces G6 (V3-04 is not in wave 2) |

## Open — left by V3-00 (api + contracts additions)

| # | Owner | File | Edit | Why | Status |
|---|---|---|---|---|---|
| V3-00-1 | V3-00 (announced) | `web/src/components/console/settings/api-types.ts` | G1 needed **two** lines: `"audit:read"` in the `Scope` union as well as in `SCOPES` (`SCOPES: Scope[]` does not typecheck otherwise) | grant G1 | done; `pnpm typecheck` + `pnpm test` green |
| V3-00-2 | V3-00 (announced) | `contracts/src/lkap_contracts/export.py` | one line: `"KbImportIn": api_models.KbImportIn` in `EXPORTED_MODELS` | the card's "`KbImportIn` … exported" needs the registration; regen adds `schemas/KbImportIn.schema.json` and the `.d.ts` interface only | done |
| V3-00-3 | V3-00 (announced) | `api/tests/test_health.py` | `expected.startswith("v2_")` → `startswith(("v2_", "v3_"))` in `test_migration_head_matches_the_script_directory` | the new head `v3_001_agent_keys` failed the hard-coded prefix | done |
| V3-00-4 | coordinator | `api/src/lkap_api/main.py` | AGENT-ACCESS §7 last row: mention API keys and the `X-LKAP-Client` header in the OpenAPI auth `description` (docstring only) | §7 lists it, the V3-00 card's exclusive files do not; the route descriptions of `/v1/audit`, `/v1/api-keys` and `/self` already document it | open |
| V3-00-5 | coordinator | `api/data/lkap.db` | apply `v3_001_agent_keys` after a `.backup` (`docs/v3/_briefs/migration-rehearsal-v3.md`) | the `--reload` api on :8080 already runs the new `ApiKey` model: every `Bearer lkap_…` request and the API keys list 500 (`no such column: api_keys.kind`) until it is applied | done — `alembic_version` in `api/data/lkap.db` reads `v3_001_agent_keys` (architect check, 2026-09-24) |
| V3-00-6 | CI | `.github/workflows/python.yml` (existing `test-postgres` job) | none: the Postgres rehearsal is that job's migration chain + full api suite against `postgres:16` | no Docker locally; the card asks for a Postgres rehearsal | open until the repo has a remote |

## Open — left by V3-01 (MCP server core)

| # | Owner | File | Edit | Why | Status |
|---|---|---|---|---|---|
| V3-01-1 | V3-02 (announced) | `mcp/src/lkap_mcp/chat/tools.py` | **The hook contract.** `server.TOOL_MODULES` is a list of dotted module paths and already contains `"lkap_mcp.chat.tools"` (imported only if it exists; an import error *inside* it still fails). The module exposes `def register(registry: lkap_mcp.registry.Registry) -> None` and declares each tool with `registry.register(fn, scopes={"agents:write"}, annotations=WRITE, data="Chat")` or the `@registry.tool(...)` decorator (`READ`/`WRITE`/`IDEMPOTENT_WRITE`/`DESTRUCTIVE` from `registry`). `fn` is an `async def` with typed keyword parameters returning `lkap_mcp.results.ToolResult` (`ToolResult.success(data)`, `ToolResult.fail(code, message, next_steps=…)`; wrap replies in `results.untrusted(text, source)`). `registry.ctx` is the `ServerContext` (`.settings` incl. `max_chats`, `.client` = `LkapClient` with `get/post/put/delete/items`, `.identity`, `.allows(scope)`); `registry.on_shutdown(async_fn)` closes open chats. Every call is attributed, sanitized and exception-safe in the wrapper, so tools may raise `ApiFailure`. Tests build the server with `lkap_mcp.server.build_server(settings, client=LkapClient(settings, api_key=raw, transport=httpx.ASGITransport(app)))`; see `mcp/tests/conftest.py`. The scope gate is lazy (first `tools/list`/`tools/call`), so a tool is visible only to keys whose scopes cover it. | V3-02 registers chat tools without editing `server.py` (G5) | closed (V3-02 uses it, V3-02-2) |
| V3-01-2 | V3-03 (announced) | `mcp/src/lkap_mcp/docs/**` | Integration with V3-03's own loader (`lkap_mcp.docs`): `prompts.py` renders every prompt through `lkap_mcp.docs.prompt(name).render(**args)` (the frontmatter concepts/recipes are inlined there) and falls back to V3-01's recipe+concept composition only when that raises; `lkap_mcp.content` (V3-01) reads the same `docs/` and `generated/` files but tolerates missing ones (placeholder guide, contracts fallbacks for `generated/`), and the discovery tools/resources use it. Nothing for V3-03 to change. `lkap_mcp.catalog.all_tool_names()` / `declared_specs()` give every declared tool (V3-01's 56 + V3-02's 4) for doc-lint without an api. | one source for prompts; resources survive partial content | open (info) |
| V3-01-3 | V3-03 | `mcp/src/lkap_mcp/docs/__init__.py`, `mcp/tests/test_docs_lint.py` | The shared `mcp/` gate fails on V3-03's files only: `mypy --strict` `no-any-return` at `docs/__init__.py` lines 244, 249, 266 (`json.loads(...)` returned as `dict[str, Any]`; bind it to an annotated local first), and `ruff check .` on `test_docs_lint.py` (F821 undefined `Any` ~l.808, E501 ~l.790/850, I001 ~l.86). V3-01's files pass `ruff check`, `ruff format --check` and `mypy --strict` on their own. | package gate | closed (V3-03 fixed both; package gate green 2026-09-24) |
| V3-01-4 | V3-05 | `.github/workflows/python.yml` (G3) | Add `mcp` to the matrix. Notes for the job: the dev group pulls `lkap-api`, `lkap-packs` and `lkap-testing` as path deps (tests boot the real api in-process), and `tests/conftest.py` appends `api/tests` to `sys.path` for `auth_helpers` and `connection_fakes`, so the checkout must include `api/`. `tests/test_stdio.py` spawns `python -m lkap_mcp` (no network: the api url is a closed loopback port). | V3-01 does not own `.github/**` | open |
| V3-01-5 | V3-06 (announced) | `mcp/src/lkap_mcp/http.py`, `__main__.py` | What V3-01 left for HTTP mode: `McpSettings` already has `transport` (`LKAP_MCP_TRANSPORT=http` switches `file:` refs to `ref_unavailable_in_http_mode`, `kb_add_document(file_path=)` and `webhook_create` to refusals), `http_host`/`http_port`/`public_url`/`max_sessions_per_key`/`calls_per_min`. A per-session server is `build_server(settings, client=LkapClient(settings, api_key=<bearer>))`; registration is lazy per server instance (first `tools/list`/`tools/call`/`resources/read`), so one server per MCP session gives per-session scope-gated tool lists without the union fallback. `server.aclose()` runs the shutdown hooks (chats) and closes the client. `__main__.py` has an `--http` flag stub that exits 2 with "not available in this build yet". | V3-06 builds on it | open (info) |
| V3-01-6 | architect / V3-07 | — (design note) | `lkap_delete(kind="agent", confirm=true)` relays the api's 409 only when the agent still has sessions (or `purge=true` on an unarchived agent); a session-less, unarchived agent is deleted (204), which is the api's documented rule (`DELETE /v1/agents/{id}`). V3-07 step 5 expects "confirm → 409 (not archived)"; after a test chat the agent has a session, so the 409 holds there. The MCP adds no extra "archive first" gate. Tests cover both 409 paths. | acceptance wording vs api semantics | ruled — R-V3-21 (V3-07 step 5 now archives then `purge=true`) |
| V3-01-7 | api (Phase 2) | `api/src/lkap_api/routers/workspaces.py` | `GET /v1/audit` has only `limit`/`offset`; the `activity` tool filters `mine`/`since`/`action_prefix` client-side over at most 2,000 rows. Server-side `actor_id`/`since`/`action` filters would make it exact. | efficiency | ruled — R-V3-22 (Phase 2 backlog; the tool names the window when exhausted) |
| V3-01-8 | V3-01 (announced) | — | Deviations from the card/§4 wording, all deliberate: (a) `TOOL_MODULES` is `list[str]` of dotted paths (lazy, optional import) rather than `list[ModuleType]`, so V3-02's module could be listed before it existed and there is no import cycle; (b) `ToolResult.data` is `Any` (FastMCP validates structured output against the return model, and redaction/`Untrusted` wrapping rewrite fields), and each tool names its data model in `_meta["lkap/data"]`; (c) `session_list` uses `since`/`until` (`from` is a Python keyword; sent as the api's `from`/`to`); (d) the blocked-destination hint keys off the api's `details.reason="blocked_destination"` (the api's code is `unprocessable_entity`); (e) values shorter than 8 characters are not scrubbed from free text (key-based redaction still covers them); (f) mixed read/write tools (`connection_fleet`, `agent_versions`, `agent_limits`, `api_request`) present as read-only (`readOnlyHint=true`) to keys without their write scope and refuse the write path with `forbidden`. | record | ruled — R-V3-23 ratifies (a)–(f); V3-01 card amended |

## Open — left by V3-02 (test chat over the room)

| # | Owner | File | Edit | Why | Status |
|---|---|---|---|---|---|
| V3-02-1 | V3-06 | `mcp/src/lkap_mcp/http.py` | none in V3-02's tree. Contract for HTTP mode: chats are owned by `lkap_mcp.chat.tools.OWNER_KEY(ctx)` (a replaceable module hook). Default: `"stdio"` in stdio mode; in HTTP mode `f"session:{mcp-session-id header}"` from `request_ctx.get().request`, falling back to `id(session)` only when no header is reachable (unsafe across GC: an id can be reused by a later session of another key). V3-06 **must** set `chat_tools.OWNER_KEY` from its own session registry (the 32-byte id bound to the key hash) and, when an MCP session ends, call `await chat_tools.manager_for(registry).close_owner(owner)`. Limits already enforced by `ChatManager`: `LKAP_MCP_MAX_CHATS` (3) per owner, `max_total=20` per process, 5 min idle | §9.1 "chats are keyed by session and die with it", §9.3 "3 per session, 20 per process" | ruled — R-V3-24 (V3-06: registry id owner, fail-closed, `close_owner` on every session end) |
| V3-02-2 | V3-01 | none | nothing needed: `server.TOOL_MODULES` already lists `lkap_mcp.chat.tools`, `register(registry)` + `registry.on_shutdown(...)` (a wrapper that runs `manager.close_all()` and drops the manager) are used as documented; `livekit==1.1.18` and `lkap-testing` are already in `mcp/pyproject.toml` | G5 | closed |

## Open — left by V3-03 (docs, recipes, prompts, doc-lint)

| # | Owner | File | Edit | Why | Status |
|---|---|---|---|---|---|
| V3-03-1 | V3-03 (announced) | `mcp/src/lkap_mcp/docs/blocks.md` (new) | Added the "blocks catalog lines" the card lists as a deliverable, in the exact `- \`type\` — description` format `lkap_mcp.content._block_lines()` (V3-01) already parses. Confirmed: `content.block_catalog()` now returns V3-03's descriptions instead of the empty-string fallback (spot-checked via `uv run python -c "from lkap_mcp import content; print(content.block_catalog()[0])"` in `mcp/`). | one source for block descriptions; no code change needed on either side | done |
| V3-03-2 | ack of V3-01-2/V3-01-3 | — | Confirming both from V3-03's side: (a) `lkap_mcp.docs` (this package's loader) and `lkap_mcp.content`/`prompts.py` (V3-01) turned out to overlap in purpose but not in code — V3-01 built its own tolerant reader independently and wired `prompts.py` directly to `lkap_mcp.docs.prompt(name).render(...)` as designed; both are now exercised by the shared test suite (423 tests green) and left as-is, no consolidation needed. (b) The three field-name deviations V3-01-8 records (`session_list.since/until`, `call_list.direction/session_id`, `chat_rewind.timeout_s`) are now reflected in `test_docs_lint.py`'s `MCP_TOOLS` and in the two docs that named the old `session_list`/`call_list` fields (`concepts/sessions-and-test-chat.md`, `concepts/telephony.md`, `recipes/diagnose-a-session.md`); `test_hardcoded_tool_fields_match_registry_signatures_once_v301_lands` now passes field-for-field against `lkap_mcp.catalog.declared_specs()` for all 60 tools. | keeping this file the single place both directions of an ask are visible | done |

## Open — left by V3-04 (console: AI agents tab)

| # | Owner | File | Edit | Why | Status |
|---|---|---|---|---|---|
| V3-04-1 | V3-06 or V3-08 | `web/src/components/console/settings/snippets.ts` (`codexSnippet`'s remote form) | Verify the Codex CLI remote-MCP header field name against whichever Codex version is installed at build time (§9.4: "`http_headers` map, or `bearer_token_env_var` with the key exported in the shell") and correct `codexSnippet` if it has moved. V3-04 shipped `[mcp_servers.lkap.http_headers]` / `Authorization = "Bearer <key>"` as the more directly testable literal form (no shell env var needed) — this is a best-effort placeholder, not a verified-against-Codex value, since remote mode (V3-06) hasn't landed yet and there is nothing running to check it against. | §5/§9.4 ask V3-04 to verify this "at build time"; that verification needs the remote service and an installed Codex CLI, neither of which exist in this wave. `console-settings-snippets.test.ts` pins the current text so a future change is a deliberate diff. | ruled — R-V3-25: verified against codex-cli 0.153.4, the shipped `http_headers` form stays; V3-06 documents the `bearer_token_env_var` alternative (G7 for the comment) |
| V3-04-2 | V3-08 (G6, reversed direction) | `web/src/components/console/settings/snippets.ts` (`skillInstallLine`, `CODEX_AGENTS_NOTE`, `ASK_AGENT_LINE`) | V3-08 hasn't landed yet, so step 3's copy (the skill install line, the Codex `AGENTS.md` note, "ask your agent" prompt) was written directly from `AGENT-ACCESS.md` §5/§10's own wording rather than waiting for V3-08's G6 ask. If V3-08 wants different phrasing (e.g. the plugin form's install command instead of `scripts/install_claude_skill.sh`), file the exact lines here and V3-04's `snippets.ts` will take them — the strings are isolated in one module precisely so this swap is a one-file change. | G6 says V3-08 → V3-04; V3-04 ran first (wave 1) so it used the doc's own text as a placeholder instead of blocking on a wave-2 package. | ruled — R-V3-26: V3-08 edits the three constants itself (G8) |
| V3-04-3 | coordinator / architect | — (no file; a design note) | `AGENT-ACCESS.md` §5 says the Connect dialog is "≤ 640 px"; the shipped dialog uses `DialogContent size="lg"` (768 px, `sm:max-w-3xl`) instead, because the snippet code blocks (multi-line `claude mcp add …`, TOML, JSON) and the client/preset radio-card grids read poorly at 640 px on desktop. Every step still fits and scrolls correctly at `md` (512 px) if a narrower width is preferred — it's a one-line prop change (`size="md"`) in `connect-agent-dialog.tsx` if the architect wants the letter of §5 followed instead. | flagging the deviation rather than silently keeping it | ruled — R-V3-27: `lg` stands, §5 amended |

## Open — left by V3-05 (tests, packaging, CI)

| # | Owner | File | Edit | Why | Status |
|---|---|---|---|---|---|
| | | | | | |

## Open — left by V3-06 (remote HTTP mode, stretch)

| # | Owner | File | Edit | Why | Status |
|---|---|---|---|---|---|
| | | | | | |

## Open — left by V3-08 (Claude Code skill, plugin, Codex guidance)

| # | Owner | File | Edit | Why | Status |
|---|---|---|---|---|---|
| | | | | | |

## Open — left by V3-07 (live acceptance)

| # | Owner | File | Edit | Why | Status |
|---|---|---|---|---|---|
| | | | | | |

## For the architect (design questions; answered as R-V3-n rulings)

| # | Asked by | Question | Recommendation | Ruling |
|---|---|---|---|---|
| A-V3-1 | V3-00 | KB url import: the request-time check (`net_guard.validate_url`) uses an **empty** policy, so `LKAP_ENV=dev`'s loopback default and `LKAP_NET_ALLOW_PRIVATE_HOSTS` never admit a private destination (the acceptance needs `http://127.0.0.1/…` → 422, and tests run in dev). The fetch still goes through `deps.get_http_client` as the card says, whose connect-time guard uses the *process* policy. So in dev, or with an operator allowlist, a public *name* that resolves to loopback or an allowlisted private range would pass. Metadata addresses are always refused. Keep this, or give the import route its own strict client dependency? | Accept for v3: prod's default policy is empty, and the offline check already refuses every literal and private name. If internal wiki imports are ever wanted, add an explicit `LKAP_KB_IMPORT_ALLOW_HOSTS` rather than reusing the LiveKit allowlist. | R-V3-18 — accepted as recommended |
| A-V3-2 | V3-00 | `X-LKAP-Client` is parsed for every principal (cookie, key, admin token), not only API keys, and `last_client` is written only inside the existing 60 s `last_used_at` window. A header-less request by the same key just before an MCP call therefore delays `last_client` until the next window. | Keep both. The header is attribution, not authority. The 60 s rule is the card's; V3-04 can show `last_client` as "last seen via". | R-V3-19 — keep both; V3-07 records `last_client` after step 4 |
| A-V3-3 | V3-02 | §4.7 gates the chat tools on `agents:write` and says `POST /v1/agents/{id}/text-sessions` treats an API key as privileged "through `member_role_in`". It does not: `routers/connect.py::is_privileged` also requires the **`sessions:write`** scope for API keys, so an `agents:write`-only key gets 403 on a draft agent (and, unprivileged, the origin check refuses a published one). The worker preflight also reads `GET /v1/connections/{id}/fleet` (`connections:read`). V3-02 therefore gates the four chat tools on `agents:write` + `sessions:write` + `connections:read` (tested: hidden for the Read-only preset and for `agents:write` without `sessions:write`). | Amend §4.7's scope column and note to "`agents:write`, `sessions:write`, `connections:read`". The Builder preset (D-V3-6) already holds all three, so no console change. | R-V3-20 — §4.7 amended (table + note); V3-05 snapshot pins the scopes; V3-08 docs say "Builder or higher" |

## Coordinator decisions

Binding. One line each, dated.

- 2026-09-24 — The user ruled "if MCP itself can do it, no need for a CLI": no CLI package, commands or JSON output mode (R-V3-1).
- 2026-09-24 — The user ruled no side drawers or sheets anywhere in the console; dialogs only (R-V3-2). Existing conversions belong to another agent; v3 packages do not touch those files.
- 2026-09-24 — The user ruled "Allow pasting in chat": inline secrets are first-class, never echoed, redacted, straight to the vault; one-time console warning (R-V3-3, amended).
- 2026-09-24 — The user ruled "Build remote now too": V3-06 is core, own service, never mounted in the api (R-V3-15).
- 2026-09-24 — The user ruled "Yes, include a skill": new V3-08, `mcp/claude-plugin/`, `AGENTS.md` for Codex (R-V3-16).
- 2026-09-24 — Approved: migration `v3_001` (coordinator applies with a backup); a second worker under a fresh agent name for V3-07; local `uv run --project` install line until the git remote exists (R-V3-17).
- 2026-09-24 — Architect rulings R-V3-18 … R-V3-27 close every open wave-1 ask above (A-V3-1/2/3, V3-01-6/7/8, V3-02-1, V3-04-1/2/3); grants G7 (V3-06) and G8 (V3-08) recorded. Still open for the coordinator: V3-00-4 (the `main.py` OpenAPI auth docstring) and V3-00-6 (CI Postgres, until the remote exists).

## Closed

| # | Owner | Closed by | Note |
|---|---|---|---|
| | | | |
