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
| G6 | V3-08 → V3-04 | `web/src/components/console/settings/connect-agent-dialog.tsx` | V3-08 files the exact step-3 lines (skill install, Codex `AGENTS.md` note) as an ask; V3-04 applies them | V3-04 owns the dialog |
| G5 | V3-02 | `mcp/src/lkap_mcp/server.py` | **none** — chat tools register through `TOOL_MODULES`; if the hook is missing when V3-02 starts, file an ask, do not edit | keeps `server.py` exclusive to V3-01 |

## Open — left by V3-00 (api + contracts additions)

| # | Owner | File | Edit | Why | Status |
|---|---|---|---|---|---|
| V3-00-1 | V3-00 (announced) | `web/src/components/console/settings/api-types.ts` | G1 needed **two** lines: `"audit:read"` in the `Scope` union as well as in `SCOPES` (`SCOPES: Scope[]` does not typecheck otherwise) | grant G1 | done; `pnpm typecheck` + `pnpm test` green |
| V3-00-2 | V3-00 (announced) | `contracts/src/lkap_contracts/export.py` | one line: `"KbImportIn": api_models.KbImportIn` in `EXPORTED_MODELS` | the card's "`KbImportIn` … exported" needs the registration; regen adds `schemas/KbImportIn.schema.json` and the `.d.ts` interface only | done |
| V3-00-3 | V3-00 (announced) | `api/tests/test_health.py` | `expected.startswith("v2_")` → `startswith(("v2_", "v3_"))` in `test_migration_head_matches_the_script_directory` | the new head `v3_001_agent_keys` failed the hard-coded prefix | done |
| V3-00-4 | coordinator | `api/src/lkap_api/main.py` | AGENT-ACCESS §7 last row: mention API keys and the `X-LKAP-Client` header in the OpenAPI auth `description` (docstring only) | §7 lists it, the V3-00 card's exclusive files do not; the route descriptions of `/v1/audit`, `/v1/api-keys` and `/self` already document it | open |
| V3-00-5 | coordinator | `api/data/lkap.db` | apply `v3_001_agent_keys` after a `.backup` (`docs/v3/_briefs/migration-rehearsal-v3.md`) | the `--reload` api on :8080 already runs the new `ApiKey` model: every `Bearer lkap_…` request and the API keys list 500 (`no such column: api_keys.kind`) until it is applied | open — apply right after the wave-0 commit |
| V3-00-6 | CI | `.github/workflows/python.yml` (existing `test-postgres` job) | none: the Postgres rehearsal is that job's migration chain + full api suite against `postgres:16` | no Docker locally; the card asks for a Postgres rehearsal | open until the repo has a remote |

## Open — left by V3-01 (MCP server core)

| # | Owner | File | Edit | Why | Status |
|---|---|---|---|---|---|
| | | | | | |

## Open — left by V3-02 (test chat over the room)

| # | Owner | File | Edit | Why | Status |
|---|---|---|---|---|---|
| | | | | | |

## Open — left by V3-03 (docs, recipes, prompts, doc-lint)

| # | Owner | File | Edit | Why | Status |
|---|---|---|---|---|---|
| | | | | | |

## Open — left by V3-04 (console: AI agents tab)

| # | Owner | File | Edit | Why | Status |
|---|---|---|---|---|---|
| | | | | | |

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
| A-V3-1 | V3-00 | KB url import: the request-time check (`net_guard.validate_url`) uses an **empty** policy, so `LKAP_ENV=dev`'s loopback default and `LKAP_NET_ALLOW_PRIVATE_HOSTS` never admit a private destination (the acceptance needs `http://127.0.0.1/…` → 422, and tests run in dev). The fetch still goes through `deps.get_http_client` as the card says, whose connect-time guard uses the *process* policy. So in dev, or with an operator allowlist, a public *name* that resolves to loopback or an allowlisted private range would pass. Metadata addresses are always refused. Keep this, or give the import route its own strict client dependency? | Accept for v3: prod's default policy is empty, and the offline check already refuses every literal and private name. If internal wiki imports are ever wanted, add an explicit `LKAP_KB_IMPORT_ALLOW_HOSTS` rather than reusing the LiveKit allowlist. | |
| A-V3-2 | V3-00 | `X-LKAP-Client` is parsed for every principal (cookie, key, admin token), not only API keys, and `last_client` is written only inside the existing 60 s `last_used_at` window. A header-less request by the same key just before an MCP call therefore delays `last_client` until the next window. | Keep both. The header is attribution, not authority. The 60 s rule is the card's; V3-04 can show `last_client` as "last seen via". | |

## Coordinator decisions

Binding. One line each, dated.

- 2026-09-24 — The user ruled "if MCP itself can do it, no need for a CLI": no CLI package, commands or JSON output mode (R-V3-1).
- 2026-09-24 — The user ruled no side drawers or sheets anywhere in the console; dialogs only (R-V3-2). Existing conversions belong to another agent; v3 packages do not touch those files.
- 2026-09-24 — The user ruled "Allow pasting in chat": inline secrets are first-class, never echoed, redacted, straight to the vault; one-time console warning (R-V3-3, amended).
- 2026-09-24 — The user ruled "Build remote now too": V3-06 is core, own service, never mounted in the api (R-V3-15).
- 2026-09-24 — The user ruled "Yes, include a skill": new V3-08, `mcp/claude-plugin/`, `AGENTS.md` for Codex (R-V3-16).
- 2026-09-24 — Approved: migration `v3_001` (coordinator applies with a backup); a second worker under a fresh agent name for V3-07; local `uv run --project` install line until the git remote exists (R-V3-17).

## Closed

| # | Owner | Closed by | Note |
|---|---|---|---|
| | | | |
