# LKAP — Integration sequence and live test ladder

Status: **binding for W2-AGENT-INTEGRATION, W3-E2E-INSURANCE and the MVP definition of done.** Companion to `DECISIONS-W2.md` (apply those first). Target project: `wss://your-project.livekit.cloud`; our dispatch name is `lkap-agent`; the project also hosts an unrelated `other-project-agent` agent that must never be touched, redeployed, or dispatched by us.

Three principles:

1. **Prove one new thing per stage.** Every stage below adds exactly one mechanism on top of the previous one, with a concrete pass/fail check. Do not skip ahead when a stage fails; the failure of a later stage is not diagnosable if an earlier one is red.
2. **LiveKit credentials alone carry the MVP.** Stages 0–9 need nothing but the LiveKit project keys already in `.claude/launch.json`. Vendor keys enter only at the optional stages (sketch, avatar, Gemini Live).
3. **Spend the cheapest token first.** Text-mode `session.run()` proves tools and the workflow LLM before any room, STT or TTS is involved; the browser is used to prove *transport*, not model behaviour.

---

## Part A — Integration sequence for W2-AGENT-INTEGRATION

### A0. Preconditions (nothing runs before these are green)

| Check | Command | Expect |
|---|---|---|
| Decisions applied | `DECISIONS-W2.md` D-W2-4, 5, 6, 7, 8, 9a, 9e, 9p, 10, 11, 12, 13 implemented in `agent/` (9f superseded by 11) | offline suites green |
| Contracts current | `cd contracts && uv sync && uv run pytest -x -q && uv run python -m lkap_contracts.export && cd .. && scripts/export_contracts.sh` | `web/src/contracts/lkap-contracts.d.ts` updated (after W2-CONTRACTS-FIX: contains `value?: unknown`) |
| Agent deps (mcp extra) | `cd agent && uv sync && uv run python -c "import livekit.agents.mcp"` | no ImportError |
| All offline gates | in each of `contracts/ api/ packs/ agent/`: `uv run ruff check . && uv run mypy src/ --strict && uv run pytest -x -q -m "not live"`; in `web/`: `pnpm lint && pnpm typecheck && pnpm test` | all green |
| Inference reachable | `cd agent && uv run pytest -x -v -m live tests/live/test_inference_smoke.py` (env: `LIVEKIT_URL/API_KEY/API_SECRET`) | 2 passed — this is **Stage 0** |
| No cloud `lkap-agent` yet | `lk agent list` (after `lk cloud auth`) | shows `other-project-agent` only. If a cloud `lkap-agent` already exists, **stop the local worker before any cloud test and vice versa** — two workers with one name split dispatch nondeterministically. |

### A1. Bring-up order (local worker against LiveKit Cloud)

Start in this order and confirm each before the next. Use the `.claude/launch.json` entries (`lkap-api`, `lkap-web`; `lkap-agent` is added by W2-DEPLOY — until it exists, run the agent from a terminal with the same exports). Secrets stay in the launch config; never write them to files.

1. **api** — `cd api && uv sync && LKAP_… uv run alembic upgrade head`, then launch `lkap-api` (port 8080). Check: `curl -s localhost:8080/v1/health` → `{"ok": true, "db": "ok", "packs": ["insurance_claim", "generic"], …}` and `curl -s localhost:8080/v1/providers -H "X-Admin-Token: $LKAP_ADMIN_TOKEN" | jq '.providers | length'` ≥ 17.
2. **agent worker (dev mode)** — from `agent/`: `LIVEKIT_URL=wss://your-project.livekit.cloud LIVEKIT_API_KEY=… LIVEKIT_API_SECRET=… LKAP_API_BASE_URL=http://127.0.0.1:8080 LKAP_SERVICE_TOKEN=<same value the api has> LKAP_PACKS=packs.insurance_claim,packs.generic uv run python -m lkap_agent.main dev` (`LIVEKIT_AGENT_NAME` is optional since D-W2-11; if set it must be `lkap-agent`). Check: `worker process prewarmed agent_name=lkap-agent` and the SDK's `registered worker` line with `agent_name=lkap-agent`; any job log without `accepting job agent_name=lkap-agent` is a stop (DECISIONS-W2 §D-W2-11). If the registration line has no agent name, **kill it immediately** (it would take automatic dispatch for every room in the project). Confirm exactly **one** `lkap_agent.main` process before every live run.
3. **web** — `cd web && pnpm install`, launch `lkap-web` (port 3000). Check: `/console` lists agents; `/console/agents` create dialog shows packs `generic` and `insurance_claim`.

Restart rule (DECISIONS-W2 §D-W2-13, supersedes the earlier "hot-reloads — verify" text): the worker caches nothing per agent (config is fetched per job), so config edits need no restart. `python -m lkap_agent.main dev` does **not** reload on file change — every code edit needs a restart: send **SIGINT**, wait ≥ 15 s (dev) / `drain_timeout` (start), then SIGKILL only if it is still alive; never SIGKILL first. In `dev` the first SIGINT closes the server immediately (observed: exit in 1–2 s with no active job); in `start` it drains (stays registered, finishes running calls) up to `drain_timeout`. Plain SIGTERM in `start` mode only drains — the old worker stays registered, which is how two workers once served `lkap-agent` at the same time.

### A2. Seed the first agents (api, admin token)

Create three agents in this order; the first is the only one needed for Stages 1–6.

```bash
A="-H X-Admin-Token:$LKAP_ADMIN_TOKEN -H Content-Type:application/json"
# 1. Smoke Generic: pack defaults → cascaded LiveKit Inference (deepgram/nova-3 → google/gemma-4-31b-it → inworld/inworld-tts-2), no vision, chat_input on
curl -s $A -X POST localhost:8080/v1/agents -d '{"name":"Smoke Generic","pack_id":"generic"}'
curl -s $A -X PUT  localhost:8080/v1/agents/<id> -d '{"published":true}'        # slug: smoke-generic
# 2. Smoke Vision: same + camera/screen share (Stage 7). Fetch AgentOut, set config.capabilities.camera=true, screen_share=true, PUT config back (config_version bumps).
# 3. Smoke KB (Stage 8): generic + one knowledge base attached, auto_inject=true, top_k=4.
```

Keep `voice.user_away_timeout_s` at 15 and `tools.max_tool_steps` at 3 (defaults) — both bound cost.

### A3. What to fix where (rule for defects found while climbing the ladder)

- Defect in a file W2-AGENT-INTEGRATION owns (`agent/src/lkap_agent/main.py`, `platform_agent.py`, `vision.py`, `config_client.py`, `ui/channel.py` per D-W2-7, tests/live) → fix, add a unit test, note in report.
- Defect in an in-flight package's file (W2-API-KB, W2-PACK-INSURANCE-TOOLS, W2-WEB-NOTEBOOK, W2-DEPLOY) → report to that owner with the failing stage id and log lines; do not edit.
- Defect in a finished Wave-1 file outside the list → "fix trivially with a note" is allowed for ≤ 10-line changes that do not alter a contract; anything larger becomes a ticket for W3-E2E-INSURANCE.
- Contract change needed → stop, write it into `DECISIONS-W2.md` as D-W2-10+ for the architect to confirm.

### A4. Live test file to deliver (`agent/tests/live/test_e2e_generic.py`, marker `live`)

Runs only when `LKAP_LIVE_API_BASE_URL`, `LKAP_LIVE_ADMIN_TOKEN` and the three `LIVEKIT_*` vars are set, and assumes the api **and** the local worker are up (A1). Steps, each with a timeout:

1. `POST {api}/v1/agents` (generic pack) → `PUT published=true` (or reuse an existing `smoke-generic` by slug; prefer create-and-delete for isolation — deletion fails with 409 while sessions exist, so leave the row and tag its name with the run id).
2. `POST /v1/agents/{slug}/connect` → `ConnectResponse`.
3. `rtc.Room().connect(serverUrl, participantToken)`; register `room.register_text_stream_handler("lkap.ui.state", …)` and `("lk.transcription", …)` **before** connecting.
4. Assert a `UiSnapshot` with `session_id == sessionId` arrives ≤ 20 s (D-W2-9a; `seq` is 1 for the generic pack, may be higher for a pack that patched before the platform snapshot).
5. Send typed chat: `await room.local_participant.send_text("Please add a note that says hello world.", topic="lk.chat")`; assert a `UiPatch` touching `/notes` arrives ≤ 30 s **and** an assistant `lk.transcription` segment arrives.
6. `GET /v1/sessions/{sessionId}/events` (admin) contains `session_started`, `user_turn`, `tool_call_started{tool:"push_note"}`, `tool_call_ended`.
7. `await room.disconnect()`; poll `GET /v1/sessions/{sessionId}` until `status == "ended"` ≤ 15 s (D-W2-9e); assert `transcript` has ≥ 2 turns and `usage` is non-empty; assert `GET /v1/sessions?agent_id=…` has exactly **one** row for this run (D-W2-2).

No audio is published by the test (no STT cost); TTS still speaks the greeting and one reply (unavoidable, ~2 short utterances).

---

## Part B — Staged live test ladder

Legend — **Needs**: `LK` = LiveKit project creds only; `G` = Google API key credential (free tier nearly exhausted; spend last); `AV` = bey or tavus API key. **Cost** is an order-of-magnitude per run. Each stage lists the *one* thing it proves, the manual check, the automated check (where one exists), and the first thing to look at when it fails.

### Stage 0 — Inference works with LiveKit creds, no room
- **Proves**: the default pipeline's LLM and the JSON workflow prompt.
- **How**: `uv run pytest -m live tests/live/test_inference_smoke.py`.
- **Pass**: both tests green. **Fail →** wrong `LIVEKIT_*` env, Inference disabled on the project, or model id churn (`google/gemma-4-31b-it`; try `google/gemini-3.5-flash`).
- Needs `LK`. Cost: ~2 short LLM calls.

### Stage 1 — Dispatch and resolve
- **Proves**: token → room → job → `GET /internal/v1/sessions/{id}/resolved` → session built.
- **How**: open `http://localhost:3000/s/smoke-generic`, click Start, allow the microphone.
- **Pass**: worker log shows `accepting job agent_name=lkap-agent` (D-W2-11: proves Cloud populates `JobRequest.agent_name` on explicit dispatch; if every job is rejected, stop and report) → `job accepted` → `session built mode=cascaded has_tts=True` → `session started`; api log shows `session_resolved`; `GET /v1/sessions` shows the row `active` with `started_at` set; the page reaches "connected" (banner). Nothing has to be audible yet.
- **Fail →** no `job accepted` at all: agent name mismatch (`LKAP_AGENT_NAME` in api vs `LIVEKIT_AGENT_NAME` in worker) or the worker is not registered; `could not resolve the agent config`: `LKAP_API_BASE_URL`/`LKAP_SERVICE_TOKEN` mismatch, or the api returned 409 (stale row — start a new call); `could not build the session`: provider kwargs (read the `ProviderBuildError` text — it names the provider and the rejected kwarg).
- Needs `LK`. Cost: none beyond the greeting.

### Stage 2 — Audio round trip on Inference
- **Proves**: greeting via `session.say` (inworld TTS), STT (deepgram/nova-3), EOT (`inference.TurnDetector`), reply.
- **How**: same call as Stage 1; listen for the greeting, say "Hello, can you hear me? Tell me one fact about Denver."
- **Pass**: greeting audible ≤ 3 s after connect; your sentence appears in the transcript (user turn) ≤ 3 s after you stop; the agent replies audibly and in the transcript; `agent_state` events cycle `listening → thinking → speaking → listening` in `GET /v1/sessions/{id}/events`.
- **Fail →** no greeting but transcript works: `StartAudioButton` (autoplay policy) — click "Enable sound"; no user transcript: microphone track not published (browser permission) or STT model id; reply text but no audio: TTS model/voice id (`Ashley`, `inworld/inworld-tts-2`).
- Needs `LK`. Cost: ~1 min of STT + 2–3 TTS utterances.

### Stage 3 — Typed chat
- **Proves**: `lk.chat` → `platform_text_input_cb` → `on_user_turn_completed` hook (KB inject, per-turn frame, pack hook) → `generate_reply` (DECISIONS-W2 §D-W2-9p).
- **How**: open the chat drawer, type "Answer in five words: what can you do?".
- **Pass**: assistant reply in the transcript; `user_turn` event with the typed text; a typed question on `smoke-kb` gets a KB-grounded answer (Stage 8 already does this typed).
- Automated: `test_e2e_generic.py` step 5 covers this. Needs `LK`. Cost: 1 LLM call + 1 TTS utterance.

### Stage 4 — Tool call and UI state in the panel
- **Proves**: built-in tools, `UiChannel` patches, initial snapshot (D-W2-9a), generic panel rendering, activity/events.
- **How**: type "Add a note that says 'hello world', then set the status to 'Reviewing' with an info tone." Then "What time is it?".
- **Pass**: the generic panel shows the note and the stamp; `useUiState().seq` advanced from 1 (dev tools: the `lkap.ui.state` streams); events contain `tool_call_started/ended` for `push_note`, `set_status`, `current_time`; no `error` events.
- **Fail →** panel empty but events show tools ran: `UiChannel.start()` not called before `session.start` (RPC/topic), or the browser's `sessionId` filter dropped the stream (compare `session_id` in the payload with `ConnectResponse.sessionId`); snapshot never arrives: D-W2-9a not applied.
- Automated: `test_e2e_generic.py` steps 4–6. Needs `LK`. Cost: 2–3 LLM calls.

### Stage 5 — End call, summary, no orphan row
- **Proves**: `close_on_disconnect` → `session close` → `ctx.shutdown` → shutdown callback → `PUT summary` (D-W2-9e), and D-W2-2.
- **How**: (a) click End call; (b) in a second call say "That's all, please end the call" (`end_call` tool).
- **Pass**: within 10 s the session row is `ended` with `transcript`, `usage.llm_*`/`tts_*`/`stt_*` counters and `final_ui_state`; `session_ended` event present; **exactly one** session row per call in `GET /v1/sessions?agent_id=…` (no `created` row left behind); the page shows "Call ended".
- **Fail →** row stays `active` for minutes: the job did not shut down (D-W2-9e missing) — the room's empty-timeout will eventually end it, which is not acceptable; two rows per hangup: D-W2-2(a) not applied.
- Needs `LK`. Cost: none extra.

### Stage 6 — Test mode on an unpublished agent (D-W2-1)
- **Proves**: `?mode=test` via the console proxy.
- **How**: unpublish `smoke-generic` in the console; open `/s/smoke-generic` (expect the "not published" card); click the console's **Test call** link (`/s/smoke-generic?mode=test`).
- **Pass**: public route → 403 card; test-mode route → pre-call card with the "Test mode" badge, call connects and Stage 2 behaviour repeats. Requires W2-TEST-CONNECT merged.
- Needs `LK`. Cost: one short call.

### Stage 7 — Camera and screen share (cascaded)
Split in two; 7a is DoD-blocking, 7b is blocking only with the fallback model.
- **7a — frame → pin (no LLM vision needed)**: use `smoke-vision`; enable the camera; type "Pin what you see with the caption 'test pin'."
  - **Pass**: `pin_frame` runs (events), a `lkap.ui.asset` byte stream arrives, the generic panel shows the image with the caption; `AssetRef.meta.source == "camera"`. Then start screen share (camera turns off automatically), repeat → `meta.source == "screen"` (D-W2-4). Stop screen share, re-enable camera, repeat → `"camera"`.
  - **Fail →** "No fresh camera or screen frame": `FrameBuffer.start()` not called, `participant_identity` mismatch, or the track is published but not subscribed (`track_subscribed` never fires — check `RoomOptions.participant_identity` equals the token identity).
- **7b — the LLM sees the frame**: hold up an object; say "What am I holding?" (per-turn injection) and then "Describe the current frame" (`describe_current_frame`).
  - **Pass**: the answer names the object; no `error` event; `injected frame` DEBUG log per user turn; a second image is *not* present in the LLM request of the next turn (R4 — assert in the unit test, observe via DEBUG log `images_in_ctx=1`).
  - **Fail →** LLM error mentioning images/modality: `google/gemma-4-31b-it` is text-only on Inference → set `pipeline.llm.model = "google/gemini-3.5-flash"` on `smoke-vision` and repeat; record the outcome in RUNBOOK. Auto-degrade (R5) must have fired: a `vision injection disabled` warning and normal text replies afterwards.
- Needs `LK`. Cost: 2–4 multimodal LLM calls (a few hundred tokens each), one JPEG per pin.

### Stage 8 — Knowledge base
- **Proves**: W2-API-KB ingestion + `/internal/v1/kb/search` + auto-inject + `search_knowledge`.
- **How**: in the console create KB "Policies", upload `packs/src/packs/insurance_claim/seeds/policy_lines.md`, wait for `ready`; attach to `smoke-kb`; ask (typed) "What does policy AUTO-11111's status say?" and "Search the knowledge base for flood coverage."
- **Pass**: first answer contains the doc's fact (lapsed) without a tool call (auto-inject: DEBUG `injected knowledge hits=…`); second triggers `search_knowledge` and quotes a source filename; `POST /v1/knowledge-bases/{id}/search` returns the chunk directly; `k=0` → 422 (D-W2-5).
- **Fail →** `kb search failed` warning in the worker: internal route missing/unauthorised (`X-Service-Token`); empty hits: ingestion status not `ready` or fastembed model download blocked (needs one-time network for ~130 MB).
- Needs `LK` (fastembed is local). Cost: 2 LLM calls.

### Stage 9 — Insurance pack end to end (W3-E2E-INSURANCE; needs W2-PACK-INSURANCE-TOOLS + W2-WEB-NOTEBOOK)
- **Proves**: seed-from-pack, pack tools, background workflow, notebook panel, urgency path.
- **How**: create from pack `insurance_claim` (Inference cascaded), publish, open `/s/<slug>`. Type: "Policy H0-44721, my basement flooded yesterday in Denver, nobody was hurt." Wait for the packet. Then a second call: "Policy AUTO-11111, I was rear-ended on I-25 and my passenger's neck hurts."
- **Pass (call 1)**: `lookup_policy` activity + "verified" note ≤ 5 s; `sync_claim_packet` activity `running → done`; stamp changes from blank to a route; still-needed list populated; adjuster packet dialog opens with markdown; the agent voices one short acknowledgement when the background tool starts (cascaded rule) and does not read lists aloud. **Pass (call 2)**: lapsed policy → status `danger` + note; injury → `emergency_escalation` route and an **urgent** `generate_reply` whose transcript contains "emergency"; `escalation`/`workflow_run` events present.
- **9b — sketch (optional, `G`)**: with a `google-image-gen` credential saved and selected: "Draw a sketch of the incident." → `draw_incident_sketch` job → polaroid "Does this look right?" → confirm button → `ui_action confirm_sketch` round-trip. Skip with a RUNBOOK note when no key or quota.
- **Fail →** workflow never finishes: `PromptJsonStructuredLLM` repair exhausted (log `structured extraction rejected`) — raise `timeout_s` or switch the workflow LLM slot to `google/gemini-3.5-flash`; panel blank: `ui_panel_id` not `insurance_notebook` or registry not updated.
- Needs `LK` (+`G` for 9b). Cost: per call ~4–6 LLM calls including 2 workflow extractions; one image generation for 9b.

### Stage 10 — Avatar (optional, `AV`)
- **Proves**: `avatar.start` → `wait_for_join` → `session.start` ordering; D-W2-7 identities.
- **How**: save a bey (or tavus) credential; set `pipeline.avatar` on `smoke-generic`; call.
- **Check first**: in dev tools, `room.remoteParticipants` has two entries; the one with `lk.publish_on_behalf` is the avatar. Confirm `useAgentRpc`'s target identity is the *other* one (send a `get_snapshot`; an RPC error "method not supported" means it hit the avatar → apply D-W2-7 step 3).
- **Pass**: video tile renders the avatar with lip-synced speech; `request_ui("toast", …)` from a pack reaches the browser (agent-side RPC targets `ui_identity`); Stage 4 checks still pass.
- Cost: vendor minutes; keep the call under 60 s.

### Stage 11 — Gemini Live realtime (optional, `G`; do once, ≤ 2 min)
- **Proves**: realtime path: `generate_reply` greeting (no TTS), `RoomOptions(video_input=True)` frames, `tool_behavior=NON_BLOCKING` + `cancel_tool_reply` silence for `silent_reply` tools, workflow LLM fallback to Inference (D-W2-6).
- **How**: save a `google-realtime` credential; switch the insurance agent to `mode="realtime"`, model `gemini-3.8-live`, voice `Kore`; run the flood scenario with the camera on.
- **Pass**: greeting spoken by Gemini; camera frames reach the model ("what am I holding?" works without `describe_current_frame`); `sync_claim_packet` returns silently (no spoken acknowledgement) and the later routine note is picked up on the next turn; urgent path still interrupts.
- **Fail →** 429/quota: stop, record "not verified: quota" in RUNBOOK; do not retry the same day.

### Stage 12 — Cloud deploy of the worker
- **Proves**: `agent/Dockerfile` builds; `lk agent create/deploy`; the deployed worker serves Stages 1–5 with the local worker **stopped**.
- **How**: stop the local worker → `cd agent && lk agent create --secrets-file secrets.env` (human-created: `LKAP_API_BASE_URL` reachable from the cloud, `LKAP_SERVICE_TOKEN`, `LKAP_PACKS`; `LIVEKIT_AGENT_NAME` is no longer needed — D-W2-11) → `lk agent deploy` → `lk agent list` shows `other-project-agent` **and** `lkap-agent` → `lk agent logs` (the cloud worker's first job must log `accepting job agent_name=lkap-agent`) → rerun Stages 1, 3, 4, 5 from the browser.
- Note: the cloud worker needs a public `LKAP_API_BASE_URL`; with the api on localhost this stage needs a tunnel or the compose deployment from W2-DEPLOY. If neither exists yet, prove "builds + registers" (`lk agent list`) and mark the end-to-end part as pending in RUNBOOK.
- Needs `LK` + `lk cloud auth`.

---

## Part C — Definition of done: blocking vs known gap

**Blocking (MVP is not done until green):**

| Stage | Why it blocks |
|---|---|
| 0–5 | The core loop: dispatch, audio both ways, chat, tools → panel, clean shutdown with a stored session and no orphan rows. |
| 6 | The console's own Test call button must work on a draft agent. |
| 7a | Camera → pin is the demo's signature feature and needs no model vision. |
| 7b **with the fallback model** | Cascaded vision must work on *some* LiveKit-billed model; gemma may be text-only. Record which model was used. |
| 8 | KB is a headline platform feature and works offline-first with fastembed. |
| 9 (without 9b) | Insurance parity minus the sketch: lookup, workflow, stamp, checklist, packet, urgent escalation. |
| 12 (build + register) | `agent/Dockerfile` builds and `lk agent list` shows `lkap-agent` next to an untouched `other-project-agent`. |
| Offline gates | ruff, mypy --strict, pytest offline in all packages; web lint/typecheck/vitest; contracts generated files up to date; `test_e2e_generic.py` green under `-m live`. |

**Known gaps (log in `docs/RUNBOOK.md` with the reason, do not block):**

| Item | Condition |
|---|---|
| 9b sketch | No image-gen credential or Google quota exhausted. |
| 10 avatar | No bey/tavus key. If a key exists and D-W2-7 step 3 is needed, it becomes a W3 fix, still non-blocking. |
| 11 Gemini Live | Verify once if quota allows; DoD item 3 in IMPLEMENTATION_PLAN is reworded to "verified once when a key with quota exists, result recorded in RUNBOOK". |
| 7b on `gemma-4-31b-it` | **Closed** (D-W2-10): gemma is text-only on Inference and ignores images *silently* ("CAM 42" red card described as "white"); `google/gemini-3.5-flash` verified; the worker now skips per-turn injection on known text-only models. |
| 12 end-to-end from the cloud worker | Needs a publicly reachable api; "builds + registers" is enough for the MVP. |
| Full reconnect after a network drop | Same token/room replay; if the job already closed the visitor starts a new call (D-W2-2). Not an MVP requirement. |

---

## Part D — What the user must supply

| Stage(s) | Needed | Already available? |
|---|---|---|
| 0–9, 12 | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`; `LKAP_MASTER_KEY`, `LKAP_ADMIN_TOKEN`, `LKAP_SERVICE_TOKEN` | Yes — in `.claude/launch.json` (`lkap-api`, `lkap-web`); W2-DEPLOY adds `lkap-agent` with the same values plus `LIVEKIT_AGENT_NAME=lkap-agent`. |
| 2, 7 | A browser (Chrome) with microphone and camera permission on `localhost:3000`; a second window or `getDisplayMedia` permission for screen share. | User action during the call. |
| 8 | One-time network access to download the fastembed model (~130 MB into `LKAP_DATA_DIR/models`). | Automatic on first ingest. |
| 9b | A Google API key saved as a `google-image-gen` credential (image model `gemini-3.1-flash-image`). | Optional — spend only if quota remains. |
| 10 | A Beyond Presence (`bey`) or Tavus API key saved as a credential. | Optional; not present today. |
| 11 | A Google API key with Live API quota saved as a `google-realtime` credential. | Optional; the current free-tier key is nearly exhausted — do not use it for anything before Stage 11. |
| 12 | `lk cloud auth` for the project; a human-created `agent/secrets.env`; a public api URL (or W2-DEPLOY's compose deployment). | Partly (CLI is installed). |

No vendor key is ever placed in env or files by Claude; keys go through the console's credential modal (or `LKAP_BOOTSTRAP_CREDENTIALS_JSON` in the launch config, human-edited).

---

## Part E — Keeping the token/quota cost low

1. **Order of spend**: Inference (billed to LiveKit) for everything; Google key only for 9b and 11, each attempted **once**, last.
2. **Text before audio**: `session.run(user_input=…)` tests (Stage 0 pattern, `stt=None, tts=None`, no room) prove tools, JSON extraction and pack workflow; use them for regression instead of browser calls. Add the insurance flow as a `-m live` text-mode test in W3 (`test_e2e_insurance.py`) before any browser run.
3. **Short sessions**: hang up as soon as the stage's check is met; `user_away_timeout_s=15` and `max_tool_steps=3` stay at defaults; never leave a tab open on `/s/…` (each open session keeps STT streaming).
4. **Cheapest models by default** (DECISIONS-W2 §D-W2-10): `google/gemma-4-31b-it` for text-only agents; the registry's first `supports_video` model (`google/gemini-3.5-flash`) for camera/screen-share agents — the insurance pack seeds it; keep a dedicated workflow LLM on gemma unless extraction fails twice.
5. **Vision is bounded** by D-W2-8 (one ≤ 512 px low-detail image per turn, one image per LLM call); turn `vision_inject_per_turn` off for agents that do not need it.
6. **KB auto-inject** only on `smoke-kb`/the insurance agent; `top_k=4`.
7. **One worker at a time** (local *or* cloud) so no job is served twice.
8. **Watch usage**: every ended session stores `usage` (`session_usage_updated`); the console's session detail shows it — check after each stage so a runaway loop (tool re-calls, interruption storms) is caught within one call.

---

## Part F — Risks that could sink the first live call (check in this order)

1. **Unnamed worker** — now prevented by construction (D-W2-11: `rtc_session(agent_name="lkap-agent", on_request=only_lkap_jobs)`); the remaining risks are a contradicting `LIVEKIT_AGENT_NAME(_OVERRIDE)` (the worker refuses to start) and **two workers** under one name (local + cloud, or a drained-but-registered old dev worker — see the restart rule). Verify the registration log line and one process before the first connect.
2. **Service-token / base-URL mismatch** → `could not resolve the agent config` → the fixed "configuration unavailable" line is spoken and the job ends. Check `LKAP_SERVICE_TOKEN` is byte-identical in `lkap-api` and the worker env.
3. **`RoomOptions(participant_identity=…)` vs the token identity** — the worker links RoomIO to `resolved.participant_identity`; the browser's identity comes from the same token, so a mismatch can only come from a client passing its own `participant_identity` to connect (the session page passes `null`). If audio never arrives at the agent, compare both values in the logs.
4. **Job does not end on hangup** (D-W2-9e) → sessions stay `active`, summaries arrive minutes late, and every test call costs an extra empty-timeout of worker time. Stage 5 is the gate.
5. **Cascaded vision on gemma** (D-W2-8 R8) → LLM errors on every camera turn without the auto-degrade. Stage 7b is isolated for exactly this reason.
6. **`inference.TurnDetector()` construction per session needs network** — a transient failure raises inside `_assemble` → "configuration invalid" path. Acceptable for MVP; if it recurs, construct the detector lazily with one retry (note for W3).
7. **Browser autoplay** — no greeting audio until "Enable sound" is clicked; not a platform bug, but it has fooled every first demo. The pre-call card's Start click satisfies the gesture requirement in Chrome; Safari may still need the button.
