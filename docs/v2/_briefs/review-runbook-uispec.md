# Brief for v2 planning: REVIEW-FINAL, RUNBOOK, LIVE_TEST_PLAN, UI_UX_SPEC

All four files under `~/work/insurance_claim_live_agent_team/livekit_agent_platform/docs/` were read in full.

**Name clash to watch for:** REVIEW-FINAL has its own fix packages called WP-0/A/B/C/D. UI_UX_SPEC has a separate set called WP-0..WP-12. They are unrelated.

---

## 1. REVIEW-FINAL.md

**Header**
- Review completed 2026-09-19 by the architect (Fable 5.1).
- Status line: "Fixes applied on 2026-09-19: WP-A, WP-B, WP-C, WP-D all report done."
- WP-D checked this against the code. Test results: agent 331 passed; packs 189 passed + 5 expected xfailed; api 230 passed (`-m "not live"`); web lint, typecheck and test green (181 tests, 13 files).
- Precedence for implementers: `DECISIONS-W2.md` (including D-W3) > REVIEW-FINAL §4 > `CONTRACTS.md` > everything else.

**Decisions**
- **D-W3-1:** `PackSessionContext` gains a Protocol *method* `record_event(event_type, payload)`.
  - The worker's `SessionContext` binds it to `SessionObserver.record`.
  - Platform-owned event types are worker-only: `session_started, agent_state, user_turn, agent_turn, tool_call_started/ended, workflow_run, metrics, error, info, session_ended`.
  - Packs and tools may emit `escalation{reason, urgency: low|normal|high}` and `info{message}`. Custom types should be prefixed with the pack id, e.g. `insurance_claim.route_changed`.
  - `escalate_to_human` emits `escalation`. The insurance pack emits it on the route transition into `emergency_escalation`, inside `render_and_patch`.
- **D-W3-2:** `freeze()` in `web/src/lib/livekit.ts` does nothing until a connect has been attempted (this fixes StrictMode). Known edge: if you freeze during an in-flight fetch and that fetch rejects, the source is dead. This is only safe because `SessionExperience` remounts a fresh `LiveSession` on each attempt.

**Verdict:** "MVP-grade for a supervised local test on LiveKit credentials; not for exposure to anyone but the operator."

**Five warnings to the user**
1. `/api/console/*` has no authentication. Anyone who can reach localhost:3000 is an admin, so do not tunnel it.
2. A failure in avatar setup or `session.start()` crashed the job silently. Fixed by WP-A.
3. Nothing ended an idle call. Fixed by WP-A with an idle hangup.
4. These live paths have never been verified: a real human mic+camera browser call, realtime models (Gemini Live / OpenAI Realtime), avatars (bey/tavus), image generation, vendor-key STT/LLM/TTS, MCP, and the Docker builds.
5. An HTTP tool with empty `allowed_hosts` passed the dry run but failed every live call. Fixed by WP-C.

### Findings F-01..F-35 (severity, then status)

**Fixed by WP-A..D (the "do now" set):**
- F-01 BLOCKER: `_start()` failure was unguarded.
- F-02 HIGH: no idle hangup. Now `LKAP_IDLE_HANGUP_S`, default 120.
- F-03 HIGH: `on_agent_turn_completed` was never called.
- F-04 HIGH: no `escalation` event was ever emitted.
- F-05 HIGH: dry run and runtime disagreed on the host allowlist.
- F-09 MEDIUM: a resolve failure after the row was marked `active` left it `active`.
- F-10 MEDIUM: knowledge-base auto-inject had no timeout. Now 3 s.
- F-11 MEDIUM: background jobs were not cancelled at shutdown. Now `cancel_all`.
- F-12 MEDIUM: hardcoded fallback model ids. Now derived from the registry.
- Doc and copy items handled by WP-D: F-19, F-20, F-22 (copy only), F-23, F-24, F-25, F-31, F-32.

**Deferred or open (the tech debt):**
- **F-06 HIGH:** the web console proxy has no auth; `?mode=test` rides on it.
- **F-07 HIGH:** public `POST /v1/agents/{slug}/connect` has no rate limit, no concurrency cap and no session budget. Each call mints a 2 h token.
- **F-08 HIGH:** static `dev-admin` / `dev-service` tokens; no minimum strength.
- **F-13 MEDIUM:** the public connect endpoint honours a client-chosen `participant_identity` and unbounded `participant_metadata`.
- **F-14 MEDIUM:** the HTTP-tool allowlist is a union of per-tool and platform lists. No private-range block, no DNS-rebinding defence.
- **F-15 MEDIUM:** an unknown `{{ secret.NAME }}` renders as an empty string.
- **F-16 MEDIUM:** no credential management page.
- **F-17 MEDIUM:** nothing checks that `LKAP_PACKS` matches between api and worker.
- **F-18 MEDIUM:** the fake pack context is duplicated in `agent/tests/fakes/fake_ctx.py` and `packs/tests/insurance_claim/fake_ctx.py`.
- **F-21 MEDIUM:** Docker builds unverified. Unverified pins: `ghcr.io/astral-sh/uv:0.12.15` and `pnpm@9.15.9`.
- **F-22 MEDIUM:** no session delete and no agent archive, so an agent that was ever called can never be deleted.
- **F-26 LOW:** two `error` events per vision degrade.
- **F-27 LOW:** the `get_snapshot` RPC returns no payload.
- **F-28 LOW:** `ProviderBuildError` could leak vendor exception text.
- **F-29 LOW:** knowledge-base upload has no size cap and parses untrusted PDFs with `pypdf`.
- **F-30 LOW:** insurance-specific values leak into core: `KNOWN_PANEL_IDS`, a help-text string, and the `LKAP_PACKS` default order.
- **F-31:** three intentional insurance-rule parity bugs, xfailed at `test_rules.py:254, 279, 345`.
- **F-33 INFO:** no reconnect grace. `close_on_disconnect=True` ends the call when the network drops.
- **F-34 INFO:** never proven by any test: real speech, realtime, avatars, image generation, vendor keys, MCP, HTTP tools against a real endpoint, browser screen share, reconnects, the `_start` failure path, idle behaviour.
- **F-35 INFO:** no web tests for `SessionRoom`, `LiveSession`, the `useUiState` hook, `AgentEditor`, `ToolsTab` or `HttpToolEditorDialog`. No Playwright smoke test.

### §5 Before-production checklist (in the document's priority order)

The architect says he would refuse to deploy without the first five.

1. **Authenticate the web app (F-06).** `/console` and `/api/console/*` need a real login (reverse-proxy SSO or a Next middleware session). Gate `?mode=test` behind it explicitly.
2. **Rotate every static secret and enforce strength (F-08).**
   - `LKAP_ADMIN_TOKEN` and `LKAP_SERVICE_TOKEN` at ≥32 random bytes.
   - A fresh `LKAP_MASTER_KEY` generated on the production host, with a written re-encryption procedure (rotation is not implemented).
   - Never `dev-*` values.
3. **Bound the public connect endpoint (F-07).** Per-IP and per-agent rate limits, max concurrent sessions per agent, max session duration. Publish only agents you want callable and watch `usage` on the sessions page.
4. **TLS and network hygiene.**
   - api on a public HTTPS origin (the service token travels as a header).
   - `LKAP_CORS_ORIGINS` set to the real web origin.
   - `LKAP_HTTP_TOOL_ALLOWED_HOSTS` set, and the union semantics tightened to an intersection or a private-range deny-list (F-14).
   - Cap knowledge-base upload size (F-29).
5. **Verify what has never run** before promising it: human mic+camera call, realtime, avatars, image generation, vendor STT/LLM/TTS keys, MCP, HTTP tools against a real endpoint, Docker builds and the cloud worker with a public api (F-21). Record each in RUNBOOK §5.
6. Postgres via `LKAP_DATABASE_URL`, plus backups of `LKAP_DATA_DIR`. SQLite and LanceDB are single-writer, so `--workers 1` stays.
7. Credential management page (F-16). Honour client-supplied identities only for admins (F-13). Reject unknown `{{ secret.NAME }}` at save time (F-15).
8. Reconnect grace instead of immediate job shutdown when the participant drops (F-33). Session delete or archive so agents can be removed (F-22).
9. Shared test-fakes package (F-18). Playwright smoke for console → test call → transcript (F-35). A component test for `SessionRoom`.
10. Observability beyond the console: ship structlog JSON to a log store, and alert on `session_finished status=failed` and on `sessions_swept`. OTel stays deferred.

### §6 Paths for a second use case, and what is still missing

- **Path A (no code):** build it in the console on the generic pack.
- **Path B (code pack):**
  - Add `packs/src/packs/<id>/` with `manifest.py` (`MANIFEST: PackManifest`, no livekit import) and `pack.py` (`PACK: Pack`).
  - Register it in `LKAP_PACKS` for both api and worker.
  - Optionally add a panel at `web/src/panels/<ui_panel_id>/index.tsx`, register it in `registry.ts`, and add it to `KNOWN_PANEL_IDS`.
- **Path C (new provider):**
  - Add a `ProviderSpec` in `contracts/.../providers.py`, then regenerate.
  - Add the plugin extra to the agent package.
  - Optionally add a row to `_TEST_CALLS` so keys can be tested.
- **Still missing:**
  - A pack scaffold script and a shared fakes package.
  - A UI for `pack_settings` / `settings_schema`.
  - A credentials page.
  - A warning when api and worker disagree on `LKAP_PACKS`.
  - Realtime typed turns: the model sees only the raw typed text, not per-turn context edits.

---

## 2. RUNBOOK.md

**LiveKit Cloud**
- Project: `wss://your-project.livekit.cloud`.
- Dispatch or agent name: **`lkap-agent`**. It is fixed in code (D-W2-11) and the worker rejects every job not dispatched to it.
- The same project hosts an unrelated agent, **`other-project-agent`**. Never dispatch to it, redeploy it, or pass its ids or secrets to `lk agent`.

**Processes and ports**
- api on **8080**, web on **3000**, the worker has no port.
- `.claude/launch.json` entries named in the docs: `lkap-api`, `lkap-web`, `lkap-agent`.
- The actual file is at `~/work/.claude/launch.json`. It has five `bash` entries:

| Entry | Port |
|---|---|
| `stable-v1` | 4177 |
| `live-agent-studio` | 4180 |
| `lkap-api` | 8080 |
| `lkap-web` | 3000 |
| `lkap-agent` | none listed |

- I did not read those entries' arguments because they may contain secrets.
- LIVE_TEST_PLAN says the `lkap-agent` entry adds `LIVEKIT_AGENT_NAME=lkap-agent`, which D-W2-11 has since made optional.

**Environment variables**

| Variable | Used by | Dev value / notes |
|---|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | api, worker | project credentials |
| `LKAP_MASTER_KEY` | api | `uv run python -m lkap_api.keys generate` |
| `LKAP_ADMIN_TOKEN` | api, web | `dev-admin` |
| `LKAP_SERVICE_TOKEN` | api, worker (byte-identical) | `dev-service` |
| `LKAP_AGENT_NAME` | api | `lkap-agent` |
| `LKAP_API_BASE_URL` | worker | `http://127.0.0.1:8080` |
| `LKAP_PACKS` | worker (must match api) | `packs.insurance_claim,packs.generic` |
| `LKAP_HTTP_TOOL_ALLOWED_HOSTS` | worker, optional | comma list |
| `LKAP_VISION_MAX_FRAME_AGE_S` | worker, optional | 8 |
| `LKAP_IDLE_HANGUP_S` | worker, optional | 120; 0 or unset disables |
| `NEXT_PUBLIC_API_BASE_URL` | web | `http://localhost:8080` |
| `LIVEKIT_AGENT_NAME` | worker, optional | leave unset; if set it must be `lkap-agent` |

**One-time setup**
- `cd contracts && uv sync && uv run python -m lkap_contracts.export && cd .. && scripts/export_contracts.sh`
- `cd api && uv sync && uv run alembic upgrade head`
- `uv sync` in `agent` and in `packs`.
- `pnpm install` in `web`.

**Start order**
1. api: `cd api && uv run uvicorn lkap_api.main:app --host 127.0.0.1 --port 8080`, then `curl -s localhost:8080/v1/health`.
2. worker: `cd agent && uv run python -m lkap_agent.main dev`. Expect `registered worker {"agent_name":"lkap-agent"}`; on the first job expect `accepting job agent_name=lkap-agent`.
3. web: `cd web && pnpm dev`, then open http://localhost:3000/console.
- `scripts/dev.sh` starts all three at once.
- Before every live run, check there is exactly one worker: `ps aux | grep lkap_agent.main`, and `lk agent list` must show no cloud `lkap-agent`.

**Restarting the worker (D-W2-13)**
- Config edits never need a restart; code edits always do. There is no hot reload in `dev`.
- Stop sequence: SIGINT, wait ≥15 s (dev) or `drain_timeout` (start mode, 3600 s), and SIGKILL only if it is still alive.
- In start mode, SIGTERM only drains, which is how two workers once served `lkap-agent` at the same time.
- `agent/Dockerfile` sets `STOPSIGNAL SIGINT`. The compose example uses `stop_grace_period: 1h`.

**Pack seeding**
- `generic`: cascaded Inference `deepgram/nova-3` → `google/gemma-4-31b-it` → `inworld/inworld-tts-2` (voice Ashley).
- `insurance_claim`: LLM `google/gemini-3.5-flash`; camera and chat on; seeds two knowledge bases.
- The only models flagged `supports_video`: `google/gemini-3.5-flash` and three Gemini Live models.

**Manual feature tests (§3, rows 1–9b):** dispatch, voice both ways, typed chat, tools → panel, hangup → summary, test call on a draft, camera/screen → pin, model sees the frame, knowledge base, insurance end to end, sketch.

**Automated gates**
- Python packages: `uv run ruff check . && uv run ruff format --check . && uv run mypy src/ --strict && uv run pytest -q -m "not live"`.
- Web: `pnpm lint && pnpm typecheck && pnpm test`.

**Live tests (run from `agent/` with the `LIVEKIT_*` variables set)**
- `uv run pytest -m live -v tests/live/test_inference_smoke.py` is Stage 0.
- `uv run pytest -m live -v tests/live/test_e2e_insurance.py -k text_mode`
- With the api and one worker running, plus `LKAP_LIVE_API_BASE_URL=http://127.0.0.1:8080` and `LKAP_LIVE_ADMIN_TOKEN=dev-admin`:
  - `uv run pytest -m live -v tests/live/test_e2e_generic.py` covers Stages 1, 3, 4, 5.
  - `uv run pytest -m live -v tests/live/test_e2e_insurance.py -k room` covers Stage 9 at room level.
- The room-level tests create `E2E …` agents that accumulate, because agents with sessions cannot be deleted.

**Live results so far**
- Stages 0–9 passed. Stage 7b failed on gemma and passed on gemini-3.5-flash.
- 9b (sketch) was skipped, 10 (avatar) and 11 (Gemini Live) were not run.
- 12 (cloud deploy): build not verified, and no `lkap-agent` is deployed.
- The browser runs covered only the typed path, because the Browser pane blocks mic and camera.

**Deploying the worker (§6, human steps)**
- `scripts/vendor_agent_deps.sh`
- Optionally `docker build -f agent/Dockerfile -t lkap-agent agent`.
- In `agent/`: `cp secrets.env.example secrets.env` and fill `LKAP_API_BASE_URL` (public HTTPS), `LKAP_SERVICE_TOKEN` and `LKAP_PACKS`. Do not add `LIVEKIT_*` or `LIVEKIT_AGENT_NAME`.
- `lk agent create --secrets-file secrets.env` (first time only), then `lk agent deploy`, `lk agent list`, `lk agent logs`.

**Known gaps (§7):**
- Unfinished stages 9b, 10 and 11.
- Stage 12 build + register is DoD-blocking and not met.
- End-to-end from the cloud worker needs a public api.
- Realtime typed turns (see §6 above).
- Reconnect after a network drop replays the same token and room.
- A harmless `StreamError` in the log at shutdown.
- The insurance notebook's initial stamp reads "Needs docs".
- Other vision models are unflagged until verified.

**Troubleshooting (§8):** eight items covering a frozen token source, a name drift, a token or base-URL mismatch, two workers, gemma vision, autoplay, empty knowledge-base answers, and cost.

---

## 3. LIVE_TEST_PLAN.md

**Principles:** prove one new thing per stage; LiveKit credentials alone carry the MVP; spend the cheapest tokens first (text mode before audio).

**Part A: integration sequence**
- A0 preconditions, A1 bring-up order, A2 seed agents (`smoke-generic`, `smoke-vision`, `smoke-kb`), A3 who fixes what, A4 the `test_e2e_generic.py` spec (7 steps).
- A1 extra checks: `/v1/providers` returns ≥17 providers, and the worker's registration line must carry an agent name (kill it otherwise).
- Defaults to keep: `user_away_timeout_s=15`, `max_tool_steps=3`.

**Part B: stages and what each verifies**

| Stage | Verifies |
|---|---|
| 0 | Inference LLM and the JSON workflow prompt, with no room (`test_inference_smoke.py`). |
| 1 | Dispatch and resolve: token → room → job → `GET /internal/v1/sessions/{id}/resolved` → session built. Row turns `active`. |
| 2 | Audio round trip: greeting via `session.say` (inworld), STT deepgram/nova-3, `inference.TurnDetector`, reply. `agent_state` cycles. |
| 3 | Typed chat: `lk.chat` → `platform_text_input_cb` → `on_user_turn_completed` → `generate_reply`. |
| 4 | Built-in tools, `UiChannel` patches, initial snapshot (D-W2-9a), generic panel, `tool_call_*` events. |
| 5 | End call → summary: `close_on_disconnect` → `ctx.shutdown` → `PUT summary`. Row `ended` ≤10 s with transcript, usage and `final_ui_state`; exactly one row per call. |
| 6 | Test mode on an unpublished agent via the console proxy. Public route shows 403; `?mode=test` connects. |
| 7a | Frame → `pin_frame` asset with `meta.source` camera, then screen, then camera. |
| 7b | The LLM sees the frame (per-turn injection plus `describe_current_frame`); one image per call; auto-degrade R5. |
| 8 | Knowledge base: ingestion, `/internal/v1/kb/search`, auto-inject, `search_knowledge`; `k=0` returns 422. |
| 9 | Insurance end to end: seed from pack, pack tools, background workflow, notebook, urgent `emergency_escalation` path, `escalation`/`workflow_run` events. |
| 9b | Sketch (Google key): `draw_incident_sketch` → confirm round trip. |
| 10 | Avatar: `avatar.start` → `wait_for_join` → `session.start` ordering, D-W2-7 identities, `useAgentRpc` avatar exclusion. |
| 11 | Gemini Live realtime: greeting without TTS, video input, NON_BLOCKING and silent tools, workflow LLM fallback to Inference. |
| 12 | Cloud deploy: Dockerfile builds, `lk agent create/deploy`, `lk agent list` shows `other-project-agent` and `lkap-agent`, Stages 1/3/4/5 rerun against the cloud worker. |

**Part C: definition of done**
- Blocking: stages 0–5, 6, 7a, 7b with the fallback model, 8, 9 without 9b, 12 build + register, and the offline gates including `test_e2e_generic` under `-m live`.
- Known gaps (not blocking): 9b, 10, 11, 7b on gemma (closed), 12 end-to-end, full reconnect.

**Part D: what the user supplies**
- LiveKit credentials and the `LKAP_*` secrets.
- Chrome with mic and camera permission.
- A one-time ~130 MB fastembed model download.
- Optional keys: `google-image-gen` (model `gemini-3.1-flash-image`), bey or tavus, `google-realtime`.
- For Stage 12: `lk cloud auth`, `agent/secrets.env`, and a public api.
- Keys go only through the console's credential form or `LKAP_BOOTSTRAP_CREDENTIALS_JSON`.

**Part E: cost rules:** order of spend, text before audio, short sessions, cheapest models, bounded vision, knowledge-base auto-inject only where needed, one worker at a time, watch usage.

**Part F: risks to the first live call, in check order:**
1. Unnamed worker, or two workers under one name.
2. Service-token or base-URL mismatch.
3. `participant_identity` mismatch.
4. Job does not end on hangup.
5. Gemma receiving vision input.
6. `TurnDetector` needs network at construction.
7. Browser autoplay.

---

## 4. UI_UX_SPEC.md

**Status:** "decided" (Fable 5.1). It supersedes ARCHITECTURE §12 for the web app and changes no API or protocol contract. **It gives no completion status for any WP**: each package has acceptance criteria but nothing is marked done.

### Design system

**Direction**
- The signature primitive is the **state meter**: 4 bars in the console, 5 on the session stage, CSS-only. States: idle, connecting, listening, thinking, speaking, failed, ended.
- One vocabulary everywhere: "Connecting · Listening · Thinking · Speaking · Reconnecting · Ended".
- One accent, a desaturated **signal green** (`--brand: oklch(0.55 0.14 152)` light, `0.74 0.15 152` dark). It is used only for live, focus, selection and *Start call*. Primary buttons are neutral ink.
- Neutrals are cool-tinted (hue 250).
- The console is light by default, with Light/Dark/System via `next-themes` (`storageKey="lkap-theme"`), mounted in `app/console/layout.tsx` only.
- The session surface is dark and fixed (`.dark` plus `data-surface="session"`).

**Tokens (all OKLCH)**
- Surfaces: `background, foreground, card, popover, muted, accent, secondary, primary, border, input, ring(=brand), destructive`.
- Brand: `brand`, `brand-foreground`, `brand-text`, `brand-soft`, `brand-line`.
- Semantic tones `info`, `success` (= brand), `warning`, `danger`, each with `-soft` and `-text` variants.
- `sidebar-*`, plus `stage` / `stage-foreground` for the session.
- Shadows: `--shadow-sm/md/lg`.
- Radius: xs 4, sm 6, md 8, lg 12, xl 16, 2xl 20; `--radius` = sm.
- Easing: `--ease-out`, `--ease-in-out`, `--ease-drawer`.
- Durations: `--dur-1` 120 ms, `--dur-2` 180 ms, `--dur-3` 240 ms, `--dur-4` 320 ms.
- Contrast targets are enforced by `scripts/check-contrast.mjs` (`pnpm check:contrast`).
- Hard-coded `emerald/sky/amber/red/blue-*` classes are banned.

**Type:** Geist, plus Geist Mono for data only. The notebook uses Caveat and Patrick Hand via `--font-hand` / `--font-hand-label`. Scale: display 28, h1 22, h2 17, h3 14, body 14 (console) / 16 (session), small 13, caption 12, micro 11, data 13 mono. Sentence case throughout.

**Layout:** 4 px spacing scale. Console content max 1200 px, forms 720 px, editor summary rail 280 px, sidebar 232 px (56 px collapsed). Cards do not nest (use `divide-y` inside). Focus is a 2 px brand ring. Hit targets ≥40 px on the session surface and mobile, ≥32 px on desktop.

**Motion:** ≤320 ms; animate transform, opacity, clip-path and filter only; press feedback `active:scale-[0.98]`; no animation on keyboard-driven navigation; honour `prefers-reduced-motion`.

**Icons:** Lucide only, via `Icon`, stroke 1.75. `@phosphor-icons/react` is removed.

**shadcn:** never run `shadcn init`; add components one at a time. WP-0 adds: sidebar, sheet, tooltip, dropdown-menu, popover, command, separator, progress, alert, label, checkbox, radio-group, scroll-area, collapsible, breadcrumb, kbd, input-group. `button.tsx` gains `variant="brand"` and `size="xl"`.

**Shared primitives in `web/src/components/shared/`**

| Primitive | Key props / role |
|---|---|
| `StateMeter` | `state`, size xs/sm/md/lg, bars 4\|5, `label` |
| `StatusChip` | tone neutral/info/success/warning/danger/live |
| `Icon` | Lucide wrapper |
| `EmptyState` | empty lists |
| `PageHeader` | page title with breadcrumbs and actions |
| `Section` | replaces editor cards |
| `Field` | "Required" shown as text in the hint |
| `DescriptionList` | term/detail pairs |
| `CopyButton` | copy with confirmation |
| `RelativeTime` | "4 min ago" |
| `VendorMark` | monogram, no logos |
| `CapabilityBadge` | vision, realtime, tools, silent-tools, voices, no-key, key-required, key-set |
| `Kbd` | keyboard hint |
| `ResponsiveTable` | table ≥768 px, card list below |

- Also in that folder: `agent-state.ts` (`AgentUiState`, `AGENT_STATE_LABEL`).
- Supporting helpers: `lib/format.ts` (`formatDateTime`, `formatTime`, `formatDuration`, `formatBytes`, `toMillis`, `pluralize`) and `lib/theme.ts`.

### Information architecture (every route and screen)

**Console:** a collapsible left sidebar, not a top nav. Below 768 px it becomes a 56 px top bar with a sheet.

| Screen | Route | Icon | Notes |
|---|---|---|---|
| Overview | `/console` | `layout-dashboard` | setup checklist (6 rows), recent sessions (8), live now, quick actions; no hero metrics |
| Agents | `/console/agents` | `bot` | list |
| New agent | `/console/agents/new` | | a page, not a modal; pack cards, then name |
| Agent editor | `/console/agents/[id]?section=providers\|instructions\|panel\|tools\|knowledge` | | default section is providers |
| Credentials | `/console/credentials` | `key-round` | |
| Knowledge | `/console/knowledge`, `/console/knowledge/[id]` | `book-open` | |
| Sessions | `/console/sessions`, `/console/sessions/[id]` | `history` | |
| Settings | `/console/settings` | `settings-2` | |
| Preview (unlisted) | `/console/preview/panels` | | noindex; scene query params |
| Console not-found | `app/console/not-found.tsx` | | |

**Outside the console**
- Home `/`: not redirected to the console.
- Root not-found: `app/not-found.tsx`.
- Session pages: `/s/[slug]` and `/s/[slug]?mode=test`. `&embed=1` is reserved for the P1 iframe drawer.
- Session phases: pre-call → in-call → ended.
- Unavailable page variants: `not_found`, `not_published`, `unreachable`.

**URL and navigation conventions**
- List filters live in the query string (`?q= ?status= ?pack= ?agent= ?range=`).
- Keyboard: ⌘K palette and `g`+key shortcuts are P1.

### Key UX rules

**Console vs session pages**
- The console is a studio for builders: light theme, 14 px body.
- The session page is a phone call for end users: dark, 16 px body, 40 px+ targets, safe-area aware, no builder jargon.
- The session bundle must not import from `components/console/**`.
- `/s/[slug]` First Load JS must stay ≤400 kB (currently 383 kB).
- Test mode shows a slim bar ("Test call · this agent is a draft" plus "Back to editor"), not a badge.

**Agent editor layout**
- A sticky header, then three columns: section nav 200 px · content max 720 px · summary rail 280 px.
- Header items: name with a pencil to edit, slug with copy, Draft/Live chip, unsaved indicator, Test call split button, Publish/Unpublish popover, Save (⌘S).
- Validation shows as dots on sections plus an in-section "Issues" list. The old banner is removed.
- The publish `Switch` is removed everywhere; publishing goes through a popover that runs `/validate`.

**Providers section**
- Mode cards: Cascaded / Realtime.
- Slot cards: STT → LLM → TTS.
- Each slot offers "LiveKit Inference" vs "Your own key". Deferred providers appear disabled as "Coming soon".
- Model combobox with a custom-id escape.

**Session layout grid**

| Viewport | Layout |
|---|---|
| Desktop `side` (generic panel) | grid `minmax(0,1fr) 400px`; stage above transcript on the left, panel on the right; control bar under the left column |
| Desktop `wide` (notebook) | grid `340px minmax(0,1fr)`; compact 200 px stage above transcript in the left rail, panel on the right; control bar under the rail |
| Tablet 768–1023 | single column (panel first for `wide`); transcript as a bottom sheet |
| Mobile <768 | `min-h-[100dvh]`, bottom padding `calc(88px + env(safe-area-inset-bottom))`, fixed control bar; `wide` uses a 72 px stage strip, `side` a 45vh stage; transcript as a 60vh bottom sheet |

**Session shell elements**
- Top strip (40 px) with the elapsed timer.
- Stage with self-view picture-in-picture (144 px desktop / 96 px mobile).
- Transcript uses the vendored `AgentChatTranscript`.
- Control bar is `AgentControlBar variant="livekit"`, restyled through a wrapper, never forked.

**Panel area**
- The panel gets its own column with a header (`PanelDefinition.title`).
- Proposed optional `PanelDefinition.handleRequest` so panels can receive `open_dialog` / `focus`.
- The generic panel moves to tokens.
- The notebook keeps its paper, ink, tape and stamp; its runtime Google Fonts `@import` and `.field` border-left stripe go.

**Other screens**
- Pre-call: two-column card, device check through `useMicCheck`, never ask for permission on page load, Start is `brand` `xl`.
- Audio priming uses an `AudioContext` created in the Start click, falling back to `startAudio()` with a full-well "Tap to hear" overlay.
- Session detail: tabs Timeline (unified, default) · Transcript · Panel at end of call · Raw events. Never show raw JSON outside "Details".

**Error channels:** field errors inline (never toasts); section errors as an `Alert`; transient action failures as a toast. Pages load with skeletons, not spinners.

### Work packages (§7)

All must pass `pnpm lint/typecheck/test/build`. File ownership is exclusive.

| WP | Title | Owner, wave | Scope |
|---|---|---|---|
| WP-0 | Foundation: tokens, theme, shared primitives | Opus, first, blocking | `globals.css` tokens, theme provider, shadcn additions, shared primitives, `format.ts`, api hooks (`useHealth`, `useTestCredential`, `useUpdateCredential`), `PANEL_META`/`CAPABILITY_META`, contrast script, remove phosphor |
| WP-1 | Console shell, IA, overview, settings | Sonnet, wave A | sidebar, mobile nav, breadcrumbs, theme menu, P1 command palette, route move (`/console` becomes Overview, new `/console/agents`), overview, settings; `nav.tsx` deleted |
| WP-2 | Agents list and new-agent flow | Sonnet, wave A | `AgentsTable` on `ResponsiveTable` with row menu, search and filters, no switches; `/console/agents/new` pack-card flow; `create-agent-dialog.tsx` deleted |
| WP-3 | Agent editor shell, summary rail, publish, test call | Opus, wave A | `editor-shell`, `section-nav`, `summary-rail`, `publish-popover`, `test-call-menu`, `validation-map.ts` (`useSectionIssues`), `unsaved-guard` |
| WP-4 | Providers, models, credentials | Opus, wave A | providers flow, slot editor, `model-combobox`, `registry-form`, `credential-sheet`, `/console/credentials` page |
| WP-5 | Instructions, panel, tools, knowledge sections | Sonnet, wave A | the four tabs, plus tool editors becoming sheets |
| WP-6 | Knowledge pages | Sonnet, wave A | `kb-list`, `kb-detail` with drop zone, `kb-search-panel`, `create-kb-dialog` |
| WP-7 | Sessions list and detail | Opus, wave A | filters, duration column, pagination of 25; `session-detail-view`, `session-timeline`, "Panel at end of call" |
| WP-8 | Session experience | Opus, wave A | `StageView` seam, `PreCallCard` with injected devices, `useMicCheck`, `EndOfCallCard`, `SessionUnavailable`, `classifyConnectError`, `session-shell` layouts, audio priming, test-mode bar, fonts in `(session)/layout.tsx` |
| WP-9 | Panels: generic + insurance notebook integration | Opus, wave A | token migration, notebook fonts and fields, `generic_ui_state.json` fixture |
| WP-10 | Preview route, capture script, visual QA harness | Sonnet, wave A, finishes after WP-8/9 | `/console/preview/panels` scenes, `web/scripts/ui-capture.mjs`, axe pass, optional `e2e/preview.spec.ts` |
| WP-11 | Home page, console not-found, a11y utilities | Sonnet, wave A | `/`, root and console not-found pages, favicon and og image |
| WP-12 | Integration, gates, after-screenshots review | Opus, last, sequential | merge order WP-0 → 1..11 → 12; grep checks for banned classes, phosphor and runtime fonts; `after/INDEX.md`; Fable punch list |

**API asks (§7.14, non-blocking)**
- `ValidationResult.issues[]` with path, message and severity.
- `GET /v1/sessions?limit=&offset=`.
- `AgentOut.session_count` and `AgentOut.last_session_at`.
- Docs only: CONTRACTS §11 adds `handleRequest`.

**Out of scope (§8)**
- Vendor logos.
- Auth, users and organisations.
- A packs page and a pack-settings UI.
- Tools as a top-level page.
- Session deletion.
- Analytics.
- A light theme for the session surface.
- End-of-call summary and in-call feedback.
- All P1 items: command palette, shortcuts, density setting, QR code, iframe drawer, agent duplication.
- Transcript export.
- Internationalisation.
- Forking `agents-ui`.
- Notebook redesign.
- Playwright coverage of a live call.