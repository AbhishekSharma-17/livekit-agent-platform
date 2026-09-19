# LKAP — Runbook

How to run the LiveKit Agent Platform locally, create agents, test every feature by hand, deploy the worker, and what is known not to work yet. Written by W3-E2E-INSURANCE on 2026-09-19 from live runs against `wss://your-project.livekit.cloud`.

Binding references: `DECISIONS-W2.md` (wins everywhere) → `CONTRACTS.md` → `ARCHITECTURE.md` → `LIVE_TEST_PLAN.md` (the stage ladder this file reports on). `deploy/README.md` has the container and LiveKit Cloud details.

> **Shared project.** The LiveKit project also hosts an unrelated deployed agent, **`other-project-agent`**. Never dispatch to it, redeploy it, or pass its ids or secrets to any `lk agent` command. Our dispatch name is **`lkap-agent`**. The worker registers under that name in code and rejects every job not dispatched to it (D-W2-11).

---

## 1. Run locally

Three processes. The api listens on 8080, web on 3000, and the worker has no port. Secrets come from your shell or `.claude/launch.json` (`lkap-api`, `lkap-web`, `lkap-agent` entries), never from committed files.

| Variable | api | worker | web | Dev value |
|---|---|---|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | ✓ | ✓ | | project creds |
| `LKAP_MASTER_KEY` | ✓ | | | `cd api && uv run python -m lkap_api.keys generate` |
| `LKAP_ADMIN_TOKEN` | ✓ | | ✓ | `dev-admin` |
| `LKAP_SERVICE_TOKEN` | ✓ | ✓ (byte-identical) | | `dev-service` |
| `LKAP_AGENT_NAME` | ✓ (`lkap-agent`) | | | |
| `LKAP_API_BASE_URL` | | ✓ | | `http://127.0.0.1:8080` |
| `LKAP_PACKS` | | ✓ | | `packs.insurance_claim,packs.generic` |
| `LKAP_HTTP_TOOL_ALLOWED_HOSTS` | | optional | | comma list; empty = only the per-tool `allowed_hosts` applies |
| `LKAP_VISION_MAX_FRAME_AGE_S` | | optional | | default `8` |
| `LKAP_IDLE_HANGUP_S` | | optional | | default `120`; hangs up a session left `away` this long while listening/idle; `0`/unset to disable |
| `NEXT_PUBLIC_API_BASE_URL` | | | ✓ | `http://localhost:8080` |
| `LIVEKIT_AGENT_NAME` | | optional; if set it must be `lkap-agent` | | leave unset |

`LKAP_PACKS` must list the same packs for the api and the worker. A pack the worker cannot import degrades to the generic panel with no tools (worker log: `pack not available, falling back to the null pack`) even though the api still seeds/serves it normally.

One-time setup:

```bash
cd contracts && uv sync && uv run python -m lkap_contracts.export && cd .. && scripts/export_contracts.sh
cd api && uv sync && uv run alembic upgrade head && cd ..
cd agent && uv sync && cd ..
cd packs && uv sync && cd ..
cd web && pnpm install && cd ..
```

Start in this order and check each one before starting the next (LIVE_TEST_PLAN §A1):

```bash
# 1. api
cd api && uv run uvicorn lkap_api.main:app --host 127.0.0.1 --port 8080
curl -s localhost:8080/v1/health        # {"ok":true,"packs":["insurance_claim","generic"],"db":"ok",...}

# 2. worker (dev mode). No LIVEKIT_AGENT_NAME needed.
cd agent && uv run python -m lkap_agent.main dev
#   expect: registered worker {"agent_name": "lkap-agent", ...}
#   and, on the first job: accepting job agent_name=lkap-agent
#                          worker process prewarmed agent_name=lkap-agent

# 3. web
cd web && pnpm dev                      # http://localhost:3000/console
```

`scripts/dev.sh` starts all three with env from your shell.

**Before every live run, check that exactly one worker is running:** `ps aux | grep "lkap_agent.main" | grep -v grep` should show one process, and `lk agent list` should show no cloud `lkap-agent` while a local one is running. Two workers with one name split dispatch nondeterministically.

### 1.1 Restarting the worker (D-W2-13)

- Config edits (anything saved in the console) **never** need a restart, because config is fetched per job.
- Code edits **always** do. `python -m lkap_agent.main dev` has **no hot reload**; that exists only when the `lk` CLI drives the process, which the platform does not use.
- How to stop it: send **SIGINT**, wait **≥ 15 s** (dev) or `drain_timeout` (start mode, 3600 s by default), then **SIGKILL only if it is still alive**. Never SIGKILL first.
  ```bash
  kill -INT <pid>; for i in $(seq 1 15); do kill -0 <pid> 2>/dev/null || break; sleep 1; done; kill -0 <pid> 2>/dev/null && kill -KILL <pid>
  ```
- Observed on 2026-09-19 (livekit-agents 1.8.2), three restarts in `dev` mode with no active job: the process exited **1–2 s** after SIGINT, and the log showed `shutting down worker`. That matches D-W2-13 (dev → `aclose()` immediately). The start-mode drain was not exercised locally because Docker was not running.
- Plain SIGTERM in `start` mode only *drains*: the old worker stays registered while a new one starts. That is how two workers once served `lkap-agent` at the same time.
- `agent/Dockerfile` sets `STOPSIGNAL SIGINT`. If you ever run the worker under compose, the commented `agent` service in `deploy/docker-compose.yml` shows `stop_signal: SIGINT` and `stop_grace_period: 1h` (≥ `drain_timeout`).
- A killed worker leaves `active` session rows behind. The api's stale-session sweep (D-W2-2b) is the safety net, not a substitute for the grace period.

---

## 2. Create agents

Console path: `/console` → **New agent** → name + pack → **Create agent** → the editor opens → toggle **Draft → Published** (or use **Test call** for a draft; see D-W2-1).

API path (admin token):

```bash
A='-H X-Admin-Token:dev-admin -H Content-Type:application/json'
curl -s $A -X POST localhost:8080/v1/agents -d '{"name":"Claims intake","pack_id":"insurance_claim"}'
curl -s $A -X PUT  localhost:8080/v1/agents/<id> -d '{"published":true}'
```

What seeding from a pack gives you (CONTRACTS §8, D-W2-10):

| Pack | Pipeline | LLM | Capabilities | Seeded KBs |
|---|---|---|---|---|
| `generic` | cascaded Inference: `deepgram/nova-3` → LLM → `inworld/inworld-tts-2` (voice Ashley) | `google/gemma-4-31b-it` (text-only, cheapest) | chat | none |
| `insurance_claim` | same cascaded Inference stack, plus `google-image-gen` only when exactly one Google credential exists | **`google/gemini-3.5-flash`**, the registry's first `supports_video` model, because the camera is on | camera, chat | "Insurance policy lines", "Intake playbook" (ingested with fastembed; `ready` within seconds) |

Everything runs on LiveKit credentials alone. Vendor keys (Google image/realtime, bey/tavus avatars) go through the console's credential modal, never into env or files.

**Vision and the model gate (D-W2-10).** Per-turn camera/screen injection in cascaded mode depends on the registry:
- A model flagged `supports_video` (shown as "· vision" in the model picker) gets frames.
- A known text-only model (gemma) gets none. The session records one `info` event, "vision injection skipped: … is text-only", and `describe_current_frame` refuses.
- A free-text model id is tried, and the worker auto-disables injection after an LLM error (D-W2-8 R5).

Only `google/gemini-3.5-flash` (Inference) and the three Gemini Live models are flagged today. To add another model, verify it first: publish an agent on that model with camera on, show a coloured card with text, ask "what colour and what does it say", then set `supports_video=True` in `contracts/src/lkap_contracts/providers.py` and regenerate.

---

## 3. Test each feature by hand (real browser, mic and camera)

Use Chrome on `http://localhost:3000`, allow the microphone and camera, and hang up as soon as each check passes (cost). Every call must leave exactly **one** session row that turns `ended` within 10 s of hangup (`/console/sessions`).

**HTTP tools need `allowed_hosts` filled in (F-05).** An HTTP tool with an empty `allowed_hosts` list now fails both the console's dry run and every live call (fail closed); fill in the host(s) the tool calls before testing it.

| # | Feature | How | Pass |
|---|---|---|---|
| 1 | Dispatch + resolve | `/s/smoke-generic` → Start call | Worker: `accepting job agent_name=lkap-agent` → `job accepted` → `session built` → `session started`; page shows the agent listening. |
| 2 | Voice both ways | Listen for the greeting; say "Tell me one fact about Denver." | Greeting audible (click **Enable sound** if autoplay blocked it); your words and the reply in the transcript. |
| 3 | Typed chat | Chat button → type "Answer in five words: what can you do?" | Reply in the transcript; a `user_turn` event. Typed turns run the same hook as spoken ones (D-W2-9p): KB auto-inject, per-turn frame, pack hook. |
| 4 | Tools → panel | Type "Add a note that says hello world, then set the status to Reviewing." | Generic panel shows the note and the stamp; `tool_call_started/ended` events carry the tool name. |
| 5 | Hangup → summary | **End call**; in a second call say "That's all, please end the call." | Row `ended` ≤ 10 s with transcript, usage and final UI state; the page shows "Call ended". |
| 6 | Test call on a draft | Unpublish `smoke-generic`; console **Test call** (`/s/smoke-generic?mode=test`) | Public route shows "not published"; test mode shows the "Test mode" badge and connects. |
| 7a | Camera/screen → pin | `/s/smoke-vision`, camera on, type "Pin what you see with the caption 'test pin'." Then screen share and repeat. | Image with caption in the panel; `meta.source` is `camera`, then `screen`. |
| 7b | Model sees the frame | Hold up an object: "What am I holding?" | Correct answer on `google/gemini-3.5-flash`. On gemma: no image is sent and one `info` event appears (by design). |
| 8 | Knowledge base | `/s/smoke-kb`, typed: "What does policy AUTO-11111's status say?" / "Search the knowledge base for flood coverage." | First answer says lapsed without a tool call; second calls `search_knowledge(query)` and names `policy_lines.md`. |
| 9 | Insurance end to end | Create from pack `insurance_claim`, publish, `/s/<slug>`. Call 1, type or say: "Policy H0-44721, my basement flooded yesterday in Denver, nobody was hurt." Camera on: "Please pin what you see as evidence." Call 2: "Policy AUTO-11111, I was rear-ended on I-25 and my passenger's neck hurts." | Call 1: policy note ≤ 5 s, Claim writer running→done, stamp, still-needed list and %, **Read the adjuster packet** dialog with markdown, a polaroid evidence photo. Call 2: lapsed-policy note, stamp **Escalate to human** (danger), and an urgent spoken reply containing "emergency". |
| 9b | Sketch (optional) | Needs a `google-image-gen` credential: "Draw a sketch of the incident." | Polaroid "Does this look right?" → confirm round-trip. |

---

## 4. Automated tests

Offline gates, in each of `contracts/ api/ packs/ agent/`:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ --strict && uv run pytest -q -m "not live"
```

In `web/`: `pnpm lint && pnpm typecheck && pnpm test`.

Live tests (billed to the LiveKit project, a few cents per run):

```bash
cd agent
export LIVEKIT_URL=… LIVEKIT_API_KEY=… LIVEKIT_API_SECRET=…
uv run pytest -m live -v tests/live/test_inference_smoke.py                    # Stage 0, no room
uv run pytest -m live -v tests/live/test_e2e_insurance.py -k text_mode         # insurance text mode, no room/api
# with the api + ONE local worker running:
export LKAP_LIVE_API_BASE_URL=http://127.0.0.1:8080 LKAP_LIVE_ADMIN_TOKEN=dev-admin
uv run pytest -m live -v tests/live/test_e2e_generic.py                        # Stages 1,3,4,5
uv run pytest -m live -v tests/live/test_e2e_insurance.py -k room              # Stage 9 room level + camera pin
```

The room-level tests create a fresh agent per run (named with a run id). Rows cannot be deleted while sessions exist, so the console accumulates `E2E …` agents. That is expected.

---

## 5. Live results (LiveKit Cloud, LiveKit credentials only)

Stages 0–8 were run by W2-AGENT-INTEGRATION on 2026-09-18 with a scripted room participant (typed `lk.chat`, synthetic camera/screen tracks, a WAV file for audio). Stage 9 was run by W3-E2E-INSURANCE on 2026-09-19.

| Stage | Result | Model | Evidence |
|---|---|---|---|
| 0 Inference smoke | pass | gemma-4-31b-it | `test_inference_smoke.py` 2 passed |
| 1 Dispatch + resolve | pass (re-run after D-W2-11 with **no** `LIVEKIT_AGENT_NAME` set) | gemma | worker `registered worker {"agent_name": "lkap-agent"}`; `accepting job agent_name=lkap-agent room=lkap-d3716f77`; session `d3716f77…` ended 3.6 s after hangup |
| 2 Audio round trip | pass (WAV via probe) | gemma | "Denver is known as the 'Mile High City'…"; ended 1.1 s after hangup |
| 3 Typed chat | pass | gemma | typed turn → reply; `user_turn` event |
| 4 Tools → panel | pass | gemma | `push_note`/`set_status`/`current_time`; "It is currently 7:12 AM." |
| 5 Hangup → summary | pass | gemma | End call ended 5.2 s after hangup; `end_call` tool ended 0.1 s after hangup; one row per call |
| 6 Test mode (draft) | pass (through the console proxy) | gemma | proxy connect → call |
| 7a Camera/screen → pin | pass | gemma | assets `caption="test pin"` (camera) and `"screen pin"` (screen) |
| 7b LLM sees the frame | **gemma: fails silently**; **gemini-3.5-flash: pass** | both | Red "CAM 42" card: gemma answered "The background color is white." / "It says 'Hello World'"; flash answered "The main background is red." / "It says 'CAM 42'". Now enforced by the D-W2-10 gate. |
| 8 Knowledge base | pass | gemma | auto-inject answer "lapsed personal auto policy…"; `search_knowledge` → `policy_lines.md` |
| 9 text mode | **pass** (2/2, 13 s) | gemini-3.5-flash | Flood: tools `lookup_policy, sync_claim_packet`, route `needs_docs`, stamp "Needs docs", the first reply asks for the policy number. Injury: route `emergency_escalation`, stamp "Escalate to human" (danger), and a separate urgent reply: "Please contact emergency services immediately if anyone is in danger…" |
| 9 room level (scripted) | **pass** (53 s) | gemini-3.5-flash | agent `e2e-insurance-bf8f95`, session `ea5563db…`. Events: `lookup_policy`, `sync_claim_packet`, `describe_current_frame` (the model correctly said the synthetic frame is "only a solid, uniform red colour"), `pin_evidence_photo` → asset `kind=evidence meta.source=camera confirmed=false`. Worker `injected frame … images_in_ctx=1`. Final state: route `needs_docs`, 9 notes, packet 2736 chars, 1 row, ended. |
| 9 browser, call 1 | **pass** | gemini-3.5-flash | `/s/stage9-insurance` (created and published in the console), typed flood turn. Notebook: "H0-44721 / Homeowners (HO-3), active", "home water damage · high", still-needed 53% (11 open), "Claim writer finished 4567 ms", "Policy desk · Active". Adjuster packet dialog rendered. Session `bd7869e5…` ended 0.1 s after End call. |
| 9 browser, call 2 | **pass** | gemini-3.5-flash | typed injury turn. Stamp "Escalate to human", lapsed note, urgent reply "Please contact emergency services right away…". Session `8345f11a…`, route `emergency_escalation`; 2 calls → 2 rows. |
| 9 timings (scripted) | pass | gemini-3.5-flash | policy note patch 1.6 s after the typed turn (≤ 5 s); Claim writer running→done 4.8 s; `workflow_run {name: sync_claim_packet, duration_ms: 4717, status: done}` recorded |
| 9b Sketch | skipped | — | no `google-image-gen` credential; the Google free-tier key is nearly out of quota |
| 10 Avatar | not run | — | no bey/tavus key. D-W2-7 step 3 (`useAgentRpc` avatar exclusion) is unverified. |
| 11 Gemini Live | not run | — | Google quota |
| 12 Cloud deploy | **build not verified** | — | Docker was not running on the dev machine, so the image could not be built. `lk agent list` (read-only) shows only `other-project-agent`; no `lkap-agent` is deployed. The deploy itself is a human step (§6). |

The browser runs used the Claude Browser pane, which blocks microphone and camera. The browser legs therefore prove the typed path, panel rendering and hangup. Voice (Stage 2) and a real camera pin in the browser still need a hand check (§3 rows 2, 7a, 9). The Playwright suite planned under `web/e2e/**` was not written. The browser leg was driven at DOM level in the pane, with the api's session rows and events as evidence, because the pane blocks media and every browser run costs Inference.

---

## 6. Deploy the worker to LiveKit Cloud (human steps)

These commands create real resources in the shared project. Run them yourself. Stop the local worker first (§1.1).

```bash
cd livekit_agent_platform
scripts/vendor_agent_deps.sh                         # wheels for lkap-contracts / lkap-packs into agent/vendor/
docker build -f agent/Dockerfile -t lkap-agent agent # optional local check (needs Docker running)

cd agent
cp secrets.env.example secrets.env                   # fill in: LKAP_API_BASE_URL (PUBLIC https origin of the api),
                                                     #          LKAP_SERVICE_TOKEN, LKAP_PACKS
                                                     # NOT LIVEKIT_* and NOT LIVEKIT_AGENT_NAME (D-W2-11)
lk agent create --secrets-file secrets.env           # first time only; writes the ids into livekit.toml
lk agent deploy                                      # every later update (re-run vendor_agent_deps.sh first)
lk agent list                                        # must show lkap-agent AND the untouched other-project-agent
lk agent logs                                        # first job must log: accepting job agent_name=lkap-agent
```

End to end from the cloud worker needs a publicly reachable api (`LKAP_API_BASE_URL`), either the compose deployment behind HTTPS or a tunnel. Without one, the MVP bar is "builds + registers". Never pass `other-project-agent`'s ids or secrets to any `lk agent` command.

---

## 7. Known gaps and open items

| Item | Status / reason |
|---|---|
| 9b sketch | Skipped: no image-gen credential / Google quota. The tool degrades gracefully ("Sketching isn't available in this session"). |
| 10 avatar | No key. `useAgentRpc` avatar exclusion (D-W2-7 step 3) is unverified. |
| 11 Gemini Live | Google quota. The workflow LLM falls back to Inference gemma in realtime mode. |
| 12 build + register | **DoD-blocking (LIVE_TEST_PLAN Part C), not met yet.** Docker was not running locally, so the image was not built; the deploy is a human step (§6). |
| 12 end to end from the cloud worker | Needs a public api URL. |
| Idle hangup | `LKAP_IDLE_HANGUP_S` (default 120 s) ends a session that stays `away` that long while the agent is listening/idle (REVIEW-FINAL F-02). |
| Realtime typed turns | In realtime mode the model sees only the raw typed text, not per-turn `turn_ctx` edits (SDK behaviour, D-W2-9p known gap). |
| Full reconnect after a network drop | Replays the same token/room; if the job already closed, start a new call (D-W2-2). |
| Shutdown log noise | The insurance pack's final "Session ended … Final route" note is applied to the stored state, but sending it to the already-departed browser logs a `StreamError: internal error` warning. Harmless. |
| Initial stamp | The insurance notebook starts at "Needs docs" (the blank-intake route), not a blank stamp. This is pack behaviour. |
| Other vision models | `openai/gpt-4.1`, `gpt-4o-mini` and the vendor LLMs are not flagged `supports_video` until someone verifies them (§2). |

## 8. Troubleshooting (LIVE_TEST_PLAN Part F, updated)

1. **The page shows "This session has ended" immediately, and no session row is created.** The token source was frozen before its first connect. This happened under `pnpm dev` (React StrictMode re-runs effects) before the 2026-09-19 fix in `web/src/lib/livekit.ts`: `freeze()` is now a no-op until a connect has been attempted.
2. **No `accepting job` line / job rejected.** The dispatch name drifted. The api's `LKAP_AGENT_NAME` must be `lkap-agent`; the worker refuses to start if `LIVEKIT_AGENT_NAME(_OVERRIDE)` is set to anything else.
3. **"configuration unavailable" spoken.** `LKAP_SERVICE_TOKEN` or `LKAP_API_BASE_URL` mismatch between the api and the worker.
4. **Two replies / nondeterministic dispatch.** Two workers are running (local + cloud, or an old drained one). See §1.1.
5. **The camera turn is answered wrongly.** Check the model: gemma ignores images silently. The console warns, and the worker records "vision injection skipped".
6. **No greeting audio.** Autoplay policy; click **Enable sound**.
7. **KB answers are empty.** The KB is not `ready` yet, or the fastembed model download (~130 MB, first ingest only) is blocked.
8. **Costs.** Keep calls short; never leave `/s/…` open (STT streams while it is open); check `usage` on each session in the console.
