# LKAP v2: coordinator handoff

This is written for a fresh coordinator session so it can continue the v2 build without the prior chat. **Snapshot taken 2026-09-23.** Before acting on anything here, verify it against `git status`, the gates and `docs/v2/_asks.md`. The code is the source of truth.

## Status: Phase 1 complete (2026-09-24)
- **Commits (local only):** 5b715df (waves 1–2), 53f9295 (wave 3), 5367a50 (V2-19 integration), db8326e (V2-20 live verification), 1d660f6 (V2-21 security + V2-20F), and the V2-22 hardening commit on top.
- **Architect verdict** (REVIEW-V2 §8):
  - SHIP-WITH-CONDITIONS for local/MVP use.
  - NO-SHIP for multi-tenant production until the Phase 2 conditions are met: per-connection worker tokens, email verification, digest pins, and Postgres + Redis verified with two api processes.
- **DB:** at `v2_011_recording_error`; a backup from before the migration is in the scratchpad.
- **Open, owned by the user:**
  - Set the owner password.
  - Move secrets from launch.json into `~/.config/lkap/dev.env` (REVIEW-V2 §7).
  - Supply the Bey/Simli keys (L13), a SIP trunk (L12b) and Docker (L11/L14).
  - Add a GitHub remote and secrets.
  - Run `caddy validate` (V2-22-5).
  - Re-check the NANPA list (V2-22-6).
  - Add a Google key via Console → Keys to use Gemini Live directly.
- **Phase 2 backlog:** the open MEDIUM/LOW items in REVIEW-V2, the multi-tenant conditions, the text-chat fake worker for e2e (V2-19C-4), server-side sessions paging (V2-19C-8), and the storage-configs API.

## Status update (2026-09-23, later)
- **Commit `5b715df`:** waves 1–2 plus V2-15 and V2-17, including partial V2-16/V2-18 files.
- **Worker `lkap-agent`:** restarted on v2 code; log at scratchpad/worker.log.
- **Done since the snapshot below:** V2-11, V2-13, V2-14, V2-15 (live L12a passed), V2-17 (worker wiring deferred to V2-19 per R-V2-20), the V2-02 #25 pass.
- **Rulings:** R-V2-8..R-V2-25 are in PLAN-V2 §8.
- **Running:** V2-16 (told about the R-V2-22 merge amendment) and V2-18.
- **Next:**
  1. V2-19 integration, with a separate telephony block (R-V2-20..25) that starts only after V2-16 and V2-18 land.
  2. V2-20 live verification.
  3. V2-21 security review.
- **Telephony:** after V2-19, outbound calls are default-deny until `workspaces.settings.telephony.allowed_prefixes` is set (R-V2-23).
The sections below are the original snapshot, and their in-flight list is stale.

## What this is
LKAP (LiveKit Agent Platform) is a configurable voice and video agent platform.
- **Stack:** LiveKit Cloud (or a self-hosted server), a Python LiveKit Agents worker, FastAPI, and Next.js.
- **Reference pack:** the insurance-claim use case (`packs/insurance_claim`), rebuilt from the original Gemini Live demo in `../insurance_claim_live_agent_team/live_demo`.
- **v2 goal:** a complete platform:
  - every LiveKit provider and avatar is configurable from the UI;
  - LiveKit connections (Cloud or self-hosted) are managed from the UI, with a worker supervisor;
  - a no-code panel/blocks system;
  - a flow builder;
  - telephony over LiveKit SIP;
  - QA, recordings and cost tracking;
  - auth, tenancy and API keys;
  - Dograh feature parity or better.

## Process rules (the user set these; follow them)
- **Fable 5.1 decides.** All architecture, system design and important decisions go to a Fable agent (Agent tool, `model: "fable"`). Opus 5.5 and Sonnet 5 implement. Don't decide design questions yourself. Record rulings in `PLAN-V2.md` §8 (R-V2-1…7 so far).
- **Work packages have exclusive file ownership.** Cross-package needs go into `docs/v2/_asks.md`. The "Coordinator decisions" section there is binding.
- **Commits:** the user approved one local commit per completed wave, after all gates pass. There is no remote yet and nothing is pushed. End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Concurrency:** run about 6 agents at a time. More than that has repeatedly hit account usage limits. When an agent stops on a limit, resume it with SendMessage; it keeps its context.

## Hard safety rules
1. **Never name a source path segment starting with `credentials`.** The user's deny rule `Read(./**/credentials*)` blocks it. That's why the router is `api/src/lkap_api/routers/provider_keys.py` and the page is `/console/keys`. If a tool hits a deny rule, stop and report. **Never** read the file another way (`git show`, `cat`); a subagent did that once and the harness flagged it.
2. **Never read or write `.env` or `.env.*`.** They're permission-blocked. Runtime env lives in `~/work/.claude/launch.json`, outside the repo. It holds the LiveKit key and secret, `LKAP_MASTER_KEY`, and the `dev-admin` / `dev-service` tokens.
3. **LiveKit Cloud project** `wss://your-project.livekit.cloud`:
   - It also hosts an unrelated agent, **`other-project-agent`. Never touch it.**
   - The user's own worker is registered as **`lkap-agent`**. Never stop it without reason, and never start a second worker with that name (dispatch would split).
   - Live checks use a scratch api with its own DB under the session scratchpad, plus a worker with a fresh agent name.
   - Stop workers with SIGINT, wait at least 15 s, then SIGKILL (D-W2-13).
4. **Live dev DB is `api/data/lkap.db`.**
   - Before every `alembic` run: `sqlite3 api/data/lkap.db ".backup <scratchpad path>"`. SQLite DDL is non-transactional.
   - Only the coordinator migrates it. Packages rehearse migrations on copies and must stop and ask before adding one.
   - The current head is `v2_010_qa_status`, and the DB is at head.
5. **No broad `pkill`.** Other sessions run servers. Kill only PIDs you started.
6. **Don't run `pnpm build` in `web/`** (it clobbers the dev server's `.next`). Build in a scratch copy with `node_modules` symlinked and all tests included.
7. **Never run `shadcn init`.** The base library is radix.
8. **Only the user sets their owner password.** Never run `set-password` for them.

## Environment
- **Repo:** `livekit_agent_platform/`, its own git repo on branch `main`. It sits untracked inside the insurance repo, which another Claude session also works in.
- **Commits so far:**
  - `7dcc6a2` baseline
  - `d7cf92a` wave 0
  - `fdc27fa` and `058116b`: two small fixes from background tasks the user started
- **Uncommitted:** all of wave 1 and part of wave 2, about 330 changed paths. Commit once the in-flight wave-2 packages land and every gate is green.
- **Servers** (the Browser `preview_start` names come from launch.json; the desktop app sometimes stops them, so restart with `preview_start` as needed):
  - `lkap-api` on :8080, with `--reload`
  - `lkap-web` on :3000
- **Worker:** started by the coordinator in the background:
  `cd agent && export LIVEKIT_URL=… LIVEKIT_API_KEY=… LIVEKIT_API_SECRET=… LKAP_API_BASE_URL=http://127.0.0.1:8080 LKAP_SERVICE_TOKEN=dev-service LKAP_PACKS=packs.insurance_claim,packs.generic && exec uv run python -m lkap_agent.main dev`
  It is still running **v1-era code**, because there's no hot reload. Restart it on the v2 code (SIGINT, wait, restart) once the gates are green.
- **Gates:**
  - Python packages (`contracts`, `api`, `agent`, `packs`, `supervisor`): `uv run ruff check . && uv run ruff format --check . && uv run mypy src/ --strict && uv run pytest -q -m "not live"`
  - `web/`: `pnpm lint`, `pnpm typecheck`, `pnpm test`
- **UI capture and accessibility check:** from `web/`, run `node scripts/ui-capture.mjs <outDir>`.
- Docker is installed but not running. Postgres has never been run locally; CI covers it.

## Status of Phase 1 packages (see `PLAN-V2.md` for the cards)
**Done:**
- V2-00 contracts
- V2-01 DB and migrations
- V2-02 auth, tenancy and limits
- V2-03 connections
- V2-04 supervisor, plus the restart follow-up
- V2-05 registry (121 providers)
- V2-06 credential tests and catalogs
- V2-07 worker v2
- V2-08 jobs, storage, webhooks and QA, plus the QA follow-up (R-V2-5 and R-V2-6)
- V2-09 images and CI
- V2-10 panels runtime
- V2-12 recordings, cost and sessions v2, plus the R-V2-7 layout field
- UI WP-0 through WP-11 (except WP-12, the integration package)

**In flight when this was written.** Each agent is bound to the original session. If that session is gone, check the tree for partial work before relaunching.
- V2-11 panels web and composer
- V2-13 web connections, providers and avatars
- V2-14 web login, team, keys, webhooks and session tabs
- V2-02 follow-up: the #25 scoping pass, plus #47 and #58
- V2-15 flow runtime (stretch)
- V2-17 telephony core (stretch; mocked, no real SIP resources)

**Not started:**
- V2-16 flow API and builder UI (needs V2-15)
- V2-18 text test mode with rewind, plus the widget
- WP-12 UI integration
- V2-19 integration: also covers ask #54 (half-cascade vision), #56 (krisp pin), the A11Y list and B1
- V2-20 live verification on LiveKit Cloud (stages L1–L14 in PLAN §6; v1 stages 0–9 regression)
- V2-21 security review with Fable sign-off: S1 connection-test SSRF, S2 secrets passed as launch argv

## Open items to route (all in `docs/v2/_asks.md`)
- **B1 (for the architect):** the `/s/[slug]` bundle budget is ambiguous. The spec says First Load ≤400 kB, but that figure was actually "Size". The current First Load is about 580 kB.
- **A11Y (for WP-12/V2-19):** 13 serious and 124 moderate findings from the axe run. One is a spec-vs-AA conflict for the architect: the §5.4 `reconnecting` opacity-60 dim.
- **#54, #56 and the half-cascade vision rule:** deferred to V2-19.
- **Security:** S1 and S2 go to V2-21.

## Waiting on the user
1. Setting the owner password: `cd api && LKAP_MASTER_KEY=… uv run python -m lkap_api.auth set-password --email owner@local`. The login page (V2-14) depends on it, and V2-14 must keep a dev admin-token escape hatch.
2. Beyond Presence and Simli avatar keys, for live avatar verification.
3. A SIP trunk and phone number, when available (telephony is verified later).
4. A GitHub remote plus Actions secrets: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LKAP_MASTER_KEY`, `GOOGLE_API_KEY`.
5. Starting Docker, if they want real image builds or a local self-hosted LiveKit.
6. Deciding the three preserved insurance-rule parity bugs (the "hurts" bug is already fixed). The recommendation is to fix them.
7. The second LiveKit connection's live test is deferred by the user's choice.

## Key docs
- `docs/v2/README.md` (index and precedence)
- `ARCHITECTURE-V2.md`
- `CONTRACTS-V2.md`
- `PLAN-V2.md` (§8 has the rulings)
- `UI_UX_SPEC-V2-AMENDMENTS.md`
- `DOGRAH-PARITY.md`
- `_asks.md`
- `_briefs/` (distilled v1 context)
- Research: `docs/research-v2/`
- v1: `docs/{ARCHITECTURE,CONTRACTS,DECISIONS-W2,REVIEW-FINAL,RUNBOOK,UI_UX_SPEC}.md`
