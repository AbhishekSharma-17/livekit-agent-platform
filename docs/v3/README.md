# LKAP v3 — Agent Access Layer

Status: **decided** (2026-09-24). Author: Fable 5.1 (architect). Implementers: Opus / Sonnet agents per `PLAN-V3.md`. Baseline: HEAD `edac441`, Phase 1 complete (`../v2/HANDOFF.md`).

v3 lets external AI coding agents (Claude Code, OpenAI Codex CLI, Cursor, any MCP-capable client) connect to the platform, understand it and configure it on the user's behalf: create connections from a LiveKit URL, key and secret; enable providers and add keys; build agents from packs; add knowledge bases and HTTP or MCP tools; test the result in a text chat; publish. The single surface is an **MCP server** (`mcp/` → `lkap_mcp`) with two transports, both core: stdio for local agents and streamable HTTP (its own service) for remote ones, plus a packaged Claude Code skill and `AGENTS.md` guidance for Codex. There is no CLI (ruling R-V3-1).

## Files

| File | What it is | Read when |
|---|---|---|
| [`AGENT-ACCESS.md`](AGENT-ACCESS.md) | The architecture: decisions D-V3-1…11 with justification, topology and sequence diagrams, the discoverability layer (resources, prompts, doc tools), the full tool catalog with input/output models, the console design, the package layout and test strategy, the additive api changes, the safety summary, the remote HTTP mode (§9) and the Claude Code skill (§10) | Before implementing anything |
| [`PLAN-V3.md`](PLAN-V3.md) | Packages V3-00…V3-08 with owner, wave, dependencies, exclusive files, acceptance and live steps; the verification matrix; risks; **§8 rulings R-V3-1…17** | Orchestration and implementation |
| [`_asks.md`](_asks.md) | The cross-package change-request log; the coordinator's decisions there are binding | During implementation |
| `LIVE-RESULTS-V3.md` | Written by V3-07: the Claude Code live run on a scratch api | After the live run |
| `_briefs/migration-rehearsal-v3.md` | Written by V3-00: the `v3_001` rehearsal log | Before the coordinator migrates the live DB |

## Precedence

1. `PLAN-V3.md` acceptance criteria decide "done"; its §8 rulings win over the cards and over `AGENT-ACCESS.md`.
2. `AGENT-ACCESS.md` wins over the v2 docs where v3 adds something; it never contradicts a v2 decision (`../v2/README.md` gives the v2 order: PLAN-V2 §8 rulings > CONTRACTS-V2 > ARCHITECTURE-V2 > v1).
3. The code is the source of truth for what exists today (`../v2/HANDOFF.md`).

## Rules that apply to every v3 package

- **No CLI** (R-V3-1) and **no drawers or sheets** in the console (R-V3-2): dialogs only.
- **Never name a path segment starting with `credentials`** (HANDOFF rule 1); the provider-key router is `provider_keys.py` and the console page is `/console/keys`. v3 uses `secrets`, `keys` and `vault` in names.
- **Never read or write `.env*`** or the user's launch config (HANDOFF rule 2). The MCP's `file:` references point at files the *user* keeps (R-V2-35); no v3 test or doc names their contents.
- **Migration discipline** (R-V3-10): one migration, `v3_001_agent_keys`, rehearsed on a copy, applied by the coordinator only.
- **Live rules** (HANDOFF rule 3): a scratch api on its own port and DB, a worker under a fresh agent name, never `lkap-agent`, never `other-project-agent`.
- Package gates are the same as v2 (`PLAN-V3.md` definitions); `mcp/` joins the Python matrix.

## Decisions the user has already made (2026-09-24; rulings in `PLAN-V3.md` §8)

1. **No CLI** (R-V3-1) and **dialogs only, no drawers or sheets** (R-V3-2).
2. **Secrets may be pasted in chat** (R-V3-3): inline is a first-class path next to `env:`/`file:` references; never echoed, redacted everywhere, straight to the vault; the console warns once at minting that the client's transcript stores tool arguments.
3. **Remote streamable-HTTP mode is core** (R-V3-15): its own service behind Caddy, API key as bearer, limits and review list in `AGENT-ACCESS.md` §9.
4. **A packaged Claude Code skill ships** (R-V3-16): `mcp/claude-plugin/` plus `AGENTS.md` for Codex and generic agents (V3-08).
5. **Approvals** (R-V3-17): migration `v3_001` (coordinator applies it after a backup); a second worker under a fresh agent name for the live run; the git remote is still missing, so install lines use `uv run --project <checkout>/mcp lkap-mcp`.

## Still open (do not block wave 0)

1. A Google key for a Gemini variant in the live run (optional; the pack defaults run on LiveKit Inference alone).
2. The git remote, for `uvx lkap-mcp` and a git-based `lkap-contracts` source (Phase 2 packaging).
