# LKAP v2: live verification results (V2-20)

**Run:** 2026-09-23, 14:55 to 17:20 UTC, by V2-20. Every stage ran on the real LiveKit Cloud project `wss://your-project.livekit.cloud` (region India South), on a scratch stack isolated from the user's own processes. Repo HEAD was `5367a50`, with the working-tree fixes listed in §4.

## Summary

- **Passed:** v1 stages 0–9 (all regression stages); v2 stages L1, L2, L3, L6, L7, L8, L10, L12a, L12c.
- **Partial:** L5 and L9.
- **Not run:** L4, L11, L12b, L13, L14.
- **Registry:** `inference-vad` and `inference-turn-detector` are now `verification=verified`.
- **Bugs:** four fixed with tests (§4); five logged in `_asks.md` under "Open — left by V2-20".
- **No Google credential exists.** The brief assumed the copied DB held an encrypted Google credential. It doesn't: the `credentials` table has 0 rows in the live DB (checked read-only), in the scratch copy and in all three pre-v2 backups, and no vendor key is in the environment. So nothing here ran on a Google key.
  - Everything on `google/gemma-4-31b-it` and `google/gemini-3.5-flash` ran through **LiveKit Inference**, which LiveKit bills and which needs no Google key.
  - Gemini Live (L5 half-cascade, v1 stage 11) and 9b sketch could not run.

## 0. Setup and isolation

**Scratch dir:** `<scratchpad>/v220-live/`. The launcher (`launch.py`) reads the `lkap-api` entry of the user's `launch.json` in memory. It passes secrets to children through env only, never argv. It never prints them.

| Piece | Value |
|---|---|
| DB | `sqlite3 api/data/lkap.db ".backup <scratch>/data/lkap.db"` (migration head `v2_010_qa_status`); `kb/`, `lancedb/` and `models/` copied beside it |
| scratch api | `:8096` (`uvicorn lkap_api.main:app`), `LKAP_DATABASE_URL` → the copy, `PORT=8096` (see ask V2-20-2) |
| scratch web | `:3096`: a copy of `web/` with `node_modules` symlinked, running `next dev -p 3096` (never inside `web/`, never `pnpm build`) |
| receivers | webhook sink `:8097` (stdlib); widget host page `:8098` (`python -m http.server`) |
| workers | `lkap-v220-a` (hand-started, `dev` mode, default connection of the **copy**); `lkap-v220-fleet` (supervised pool on `cloud-a`, `start` mode) |
| supervisor | one, `subprocess` backend, `LKAP_SUPERVISOR_DRAIN_S=20`, metrics `:9196`, state/log dirs under the scratch dir |

- **Dispatch isolation.**
  - The copied default connection (`ff57601e…`) was bound to `lkap-agent`. **In the scratch copy only**, its `agent_name` was changed to `lkap-v220-a`. Every agent in the copy therefore dispatches to the scratch worker, never to the user's `lkap-agent`.
  - The copy's default connection stays `external`, so the supervisor never starts a worker for it.
- **Key fingerprints** (the first 4 chars plus a sha256 prefix):

  | Key | Fingerprint |
  |---|---|
  | `LIVEKIT_API_KEY` | `APId…sha256:b231e1ac3925` (the console shows `…X9ea`) |
  | `LIVEKIT_API_SECRET` | `ifA2…sha256:baa329978a6c` |
  | `LKAP_MASTER_KEY` | `9ifM…sha256:6145f4c6b3f6` (it decrypts the copied connection: `POST /v1/connections/ff57…/test` → `ok: true, 873.9 ms`) |
  | `LKAP_SERVICE_TOKEN` | `dev-…sha256:7d8c72577083` |
  | `LKAP_ADMIN_TOKEN` | `dev-…sha256:de72d648e29b` |
  | L7 webhook signing secret | `69d6…sha256:d2725eee7d8e` |
  | L1 API key | prefix `lkap_bZO` (revoked) |

- **Test users** exist in the scratch DB only: `v220-owner@example.test` (owner), `v220-viewer@example.test` (viewer) and `v220-builder3@example.test` (builder). They were created through `POST /v1/workspaces/{id}/invites` and `POST /v1/auth/accept-invite`. Their passwords are generated and kept in a mode-0600 scratch file. The live `owner@local` was not touched.
- **One isolation incident.** It is recorded here in full.
  - The first supervised replica called back to `http://127.0.0.1:8080`, the **user's** api, not the scratch api. `bundle.api_base_url()` derives the worker callback URL from `PORT`, which defaults to 8080, rather than from uvicorn's `--port`.
  - That replica made two `POST /internal/v1/workers/register` attempts (22:07:18 and 22:07:48 local) against the user's api. Both got **404** (unknown connection), so nothing was written to the user's DB.
  - The replica was registered on LiveKit as `lkap-v220-fleet`, never as `lkap-agent`, and served no job.
  - The supervisor and the replica were stopped within about 90 s. The scratch api was relaunched with `PORT=8096`.
  - It is logged as ask V2-20-2.

## 1. Stage table

| Stage | Status | Evidence (sessions are rows in the scratch DB) |
|---|---|---|
| **0** Inference smoke | passed | `launch.py agent-pytest -m live tests/live/test_inference_smoke.py` → `2 passed in 2.89s` |
| **1** Dispatch + resolve | passed | `test_e2e_generic.py` 1 passed (12.3 s). Worker: `accepting job agent_name=lkap-v220-a room=lkap-2147cc31` → `job accepted` → `session built mode=cascaded` → `session started`. Session `2147cc31…` |
| **2** Audio round trip | passed | A `say`-synthesised WAV was published as a 48 kHz mic track. STT user turns were `"Hello. Can you hear me?"` and `"Tell me one fact about Denver."`. Reply: "Denver is known as the 'Mile High City'…". Session `ea78980f…` ended, 8 `agent_state` events |
| **3** Typed chat | passed | `test_e2e_generic.py` step 5, plus typed turns in every scenario below |
| **4** Tools → panel | passed | `test_e2e_generic.py`: `push_note` patch and `lk.transcription` segment; `set_status` on `cloud-a` (session `044023c4…`) |
| **5** Hangup → summary | passed | (a) Every scripted hangup ended its row within 0–4.6 s of the poll. (b) Typed "That's all, please end the call now." → `end_call`, agent left, row `ended` 0.1 s later (session `633ceec0…`). One row per call |
| **6** Test mode on a draft | passed | API: an unprivileged connect to draft `v220-draft` → **403** `agent 'v220-draft' is not published`; a privileged connect → 200 and a reply (session `80c9bdcc…`). Console proxy: the L10 Playwright run signed in as the owner, opened `/s/v220-draft?mode=test` ("Test call · this agent is a draft"), pressed Start call, and saw a transcript (session `529f3853…`) |
| **7a** Camera/screen → pin | passed | `smoke-vision`, a synthetic "CAM 42" red card: `pin_frame` → asset `caption="test pin"`, `meta.source=camera`. A screen-share "SCR 7" card: `caption="screen pin"`, `meta.source=screen` (`final_ui_state`). Session `1aca59ea…` |
| **7b** LLM sees the frame | passed | `google/gemini-3.5-flash` (Inference): "The background is red, and the text shows 'CAM 42.'" via `describe_current_frame` |
| **8** Knowledge base | passed | Auto-inject: worker logs `injected knowledge hits=4` on every turn. `search_knowledge` called on request → "According to policy_lines.md, a lapsed policy requires underwriting and human review…" (session `5d06fbba…`). `POST /v1/knowledge-bases/{id}/search` with `k=0` → **422**. Note: gemma declined the "AUTO-11111 status" question, because the seed doc has no policy-specific line; the v1 answer was model inference |
| **9** Insurance end to end | passed | `test_e2e_insurance.py` 3 passed (64.7 s). Text flood: `sync_claim_packet` done 4356 ms. Text injury: urgent. Room: `lookup_policy`, `pin_evidence_photo`, one row, ended (session `d1a0482e…`) |
| **9b** Sketch | not run | no Google image credential |
| **11** Gemini Live | not run | no Google credential |
| **L1** Login, roles, API key, rate limit | passed | See §2 L1 |
| **L2** Second connection `cloud-a` | passed | Created (secret sent via API, see §2 L2); UI **Test connection** → OK with every capability chip; agent `v220-cloud-a` bound; call served by the pool, with `sessions.connection_id=027c7d6f…` (session `44ef5381…`) |
| **L3** Supervised pool (subprocess) | passed, after fix B2 | Pool 1/1 ready. **Rotate** (credentials_version 2 → 3): new replica started on the new desired hash, old replica drained and exited 0 five seconds later. UI **Restart** (restart_generation 1 → 2): same roll. The pool served calls afterwards (sessions `044023c4…`, `6398d822…`) |
| **L4** Catalogs | not run | The copied DB has **0 credentials** (no OpenAI, ElevenLabs, Tavus or Bey key) |
| **L5** Half-cascade, explicit slots | partial | Explicit `vad=inference-vad` and `turn_detection=inference-turn-detector`: the worker built both from the registry (`building provider provider_id=inference-vad`, `…inference-turn-detector`), and an audio round trip passed (session `31e831c6…`, EOU → first audio 1882 ms). Half-cascade on `google-realtime` with no credential → **422** `pipeline.realtime: provider 'google-realtime' requires a credential`. Gemini Live itself was **not run** (no Google key) |
| **L6** Registration + heartbeats | passed | Both pools registered (`worker registered with the api … installed_providers=28`). Heartbeats every 30 s (`POST /internal/v1/workers/…/heartbeat 204`). The Fleet card lists each instance with image `slim`, SDK `1.8.2`, status and last heartbeat, and 28 installed providers (`shots/l6-fleet-before-restart.png`). A killed replica shows `gone` |
| **L7** Webhook + QA | passed | `session.ended` delivered to `:8097`; `X-LKAP-Signature` verified (HMAC-SHA256 over `t.body`, `sig_ok True`, age 0.4 s); delivery `delivered`. QA (worker judge, `livekit-inference-llm:google/gemma-4-31b-it`): `status=done score=5 sentiment=positive`. Session `d4844dc7…`. Gap: no QA webhook event is ever emitted (ask V2-20-1) |
| **L8** Composite panel | passed | Agent `v220-panel`, 6 blocks. `request_form` → form schema `{full_name, phone_number}` requested; `table_append` → row `C-1`; `show_document` → `https://example.com/policy.pdf`; `search_knowledge` → `kb_citations` items (`policy_lines.md`, score 0.738); `pin_frame` → gallery `asset_ids` (1). Session `28587c10…` |
| **L9** Recording, cost, latency | partial | **Latency**: `llm_ttft`, `tts_ttfb` and `eou_to_first_audio` p50/p95 on every session. **Cost**: lines for STT/LLM/TTS (`audio_s_in 20.9`, `tokens_in 1162`, …), all `note="no price"`, because LiveKit Inference is unpriced in `pricing.py` by design; `cost_usd=null`. **Recording**: `recording/start` → **422** (`NoEgressStorageError`). Cloud Egress needs S3-compatible storage it can reach; local storage can't be an Egress target, and MinIO and Docker are unavailable |
| **L10** Viewer role + Playwright smoke | passed (UI gaps logged) | `e2e/login-console-smoke.spec.ts` (fixed, B4), run against `:3096` with the bypass off: **1 passed** (8.4 s), transcript `seen`. Viewer UI: Save disabled, and every write the UI attempted was refused server-side (`validate` → 403, no PUT reached the api). UI gaps: see ask V2-20-5 |
| **L11** Compose prod | not run | Docker isn't running |
| **L12a** Flow via the UI builder | passed | Playwright in the builder: Mode → **Use a flow**, edit the step's instructions, **Add node → End** (farewell and disposition `ui_flow_complete`), drag an edge `main → end` with a condition, **Save** → `config_version 2`, `mode=flow`. Call: `handoff start→main`, `go_to_end`, `handoff main→end`, `flow_ended completed=true`; row `disposition=ui_flow_complete`; the `session.ended` webhook carried the same disposition (session `ffb8e829…`) |
| **L12b** SIP + DTMF + transfer | not run | No SIP trunk; no calls made and no SIP resources created. The R-V2-25 origin check was verified without SIP: see L12c |
| **L12c** Text rewind + widget | passed | See §2 L12c |
| **L13** Avatars | not run | No Bey, Tavus, Simli or Anam keys yet |
| **L14** Self-hosted connection | not run | Docker isn't running |

## 2. Stage details

### L1: auth, roles, API keys, connect limit (`l1.py`)

**Login**

| Check | Result |
|---|---|
| owner login | `POST /v1/auth/login` → 204, `lkap_session` cookie set, `/v1/auth/me` role `owner` |
| viewer and builder login | 204, role `viewer` / `builder` |
| wrong password | **401** |
| owner logout, then `/me` | **401** |

**Role checks**

| Check | Result |
|---|---|
| viewer lists agents, connections, members | 200 |
| viewer creates an agent | **403** |
| viewer creates an API key | **403** |
| builder creates a connection | **403** |

**API key** (scopes `agents:read`)

| Check | Result |
|---|---|
| `GET /v1/agents` with the key | 200 |
| `POST /v1/agents` with the key | **403** |
| a bad key | **401** |
| revoke | 204, then **401** |

On the unfixed code, 4 of 12 immediate re-uses of a just-revoked key returned **200**. That is bug B1. After the fix it was 12/12 **401**.

**Connect rate limit**
- Seven unprivileged connects from one address to a published agent (no room joined): `[200, 200, 200, 200, 200, 200, 429]`.
- The 7th returns `rate_limited: "connects per minute from this address", retry_after_s 9.9`.

**Audit:** `/v1/audit` rows `api_key.create`, `api_key.revoke`, `POST /v1/agents` and `PUT /v1/agents/{agent_id}`, with the actor type (`user` or `system`/`break-glass`).

### L2: connection `cloud-a`

- `POST /v1/connections` created `cloud-a` (`027c7d6f…`): `slug=cloud-a`, `agent_name=lkap-v220-fleet`, `deployment_mode=supervised`, `replicas=1`, `worker_image=slim`.
  - The API key and secret were sent in the request body from env by a script, not typed into the UI form. Browser automation here never types credentials into fields. The rest of L2 used the UI.
- UI (`/console/connections/027c…`, `shots/l2-cloud-a-tested.png`): **Test connection** → status **OK**, Last checked "just now". Capabilities: Inference, SIP, Egress, Ingress and Cloud-hosted available; noise cancellation `krisp`; turn detector `hosted`.
- Agent `v220-cloud-a` was bound through `PUT /v1/agents/{id} {"connection_id": …}`.
- Call: the replica's log shows `accepting job agent_name=lkap-v220-fleet room=lkap-44ef5381`, the reply "I've added that note for you", and `push_note`. The row has `connection_id=027c7d6f…`.

### L3: pool, rolling restart, UI restart

**First run: failed, and exposed B2.**
- After the rotate, every new replica died at once with `OSError: [Errno 48] … bind on address ('::', 8081)`.
- The cause: the SDK's `start`-mode HTTP server listens on port 8081, and the old replica still held it.
- The supervisor crash-looped with backoff 5, 10, 20 and 40 s. The old replica kept serving until the supervisor was stopped.

**After the fix (supervisor log):**

```
16:44:51 replica_started  desired_hash=cb752a20e17d   (rotate: credentials_version 3)
16:44:56 replica_draining grace_s=20.0 (old)
16:44:57 replica_stopped  exit_code=0 pid=64259
16:47:01 replica_started  desired_hash=8cc59ae1a289   (UI "Restart", restart_generation 1)
16:47:06 replica_draining … 16:47:08 replica_stopped exit_code=0 pid=64411
17:14:32 replica_started  desired_hash=89f43d84da27   (API restart on the final code, restart_generation 2)
17:14:40 replica_stopped  exit_code=0 pid=65000
```

"launch.json worker stopped" was skipped by instruction. It was replaced by "pool serves a call", which passed three times.

### L12c: text channel, rewind and edit, widget, and the R-V2-25 probe

**Text session**
- `POST /v1/agents/smoke-generic/text-sessions` → a `channel=text` session (`7220180b…`). The agent published no tracks.
- `lkap.agent.action` RPC results:

  | Action | Reply |
  |---|---|
  | `inject_user_text` "Remember the code word MANGO…" | "OK" |
  | `inject_user_text` "…one word: APPLE." | "APPLE" |
  | `rewind {turn_index: 2}` | "APPLE" (regenerated) |
  | `inject_user_text {turn_index: 2}` "…BANANA." | "BANANA" |
  | "What was the code word?" | "MANGO" |

- The stored transcript keeps turns 1–2, drops the first APPLE reply, and appends BANANA.

**Widget**
- A third-party page on `:8098` embeds `widget.js` with `data-agent=v220-widget data-channel=text data-mode=inline`. The iframe `/s/v220-widget?embed=1&channel=text` greeted.
- Typed "Reply with exactly one word: WIDGET." → "WIDGET" (`shots/l12c-widget-1.png`).
- The CSP is `frame-ancestors 'self' http://localhost:8098`. An agent with no allowed origins gets `frame-ancestors 'self'`.
- A text session from `Origin: http://evil.example` → **403**.

**R-V2-25 origin probe (no SIP needed)**
- `RoomService.SendData` (reliable, topic `v220.origin.probe`) into the live room arrived in the Python rtc SDK with **`packet.participant is None` = True**.
- So the worker's `_on_data` origin check (`participant is not None` → ignore) holds for server-sent DTMF packets, and V2-21 doesn't need the HMAC fallback.

## 3. Registry

- **Flipped** to `verification=verified` (`contracts/src/lkap_contracts/providers.py::LIVE_VERIFIED_IDS`):
  - `inference-vad`
  - `inference-turn-detector`
- **Already verified from v1** and exercised again: `livekit-inference-stt`, `livekit-inference-llm` and `livekit-inference-tts` (all stages), and `fastembed-embedding` (stage 8, L8).
- **Not flipped** because no key existed: every vendor plugin, including Google, OpenAI, ElevenLabs, Tavus and Bey.
- After the flip, `scripts/export_contracts.sh --generate` was run; the only change was `contracts/generated/providers.json`, and the web copy is byte-identical. `gen_plugin_requirements.py --check` exits 0.

## 4. Bugs

**Fixed, each with a test:**

| # | Bug | Fix | Test |
|---|---|---|---|
| B1 | FastAPI's default `scope="request"` runs `get_db`'s commit **after the response is sent**. A client could get `204` for a revoke and still authenticate with the key. Live: 4/12 immediate re-uses succeeded. Every write had this read-after-write race, and a failed commit could surface after a 2xx | `DbDep`, `auth.deps._Db` and `AdminCtxDep` are now `scope="function"`. The audit write and the commit happen before `http.response.start` | `api/tests/test_commit_before_response.py`: raw-ASGI tests that check the DB at the instant the response starts. Both failed before the fix. Live re-check: 12/12 → 401 |
| B2 | Supervisor `subprocess` backend: every replica's SDK HTTP server binds 8081 in `start` mode. The rolling restart's replacement, or any `replicas>1`, crash-loops on `EADDRINUSE`, so rotation and restart never complete | Worker: `LKAP_WORKER_HTTP_PORT` → `AgentServer(port=…)` (`lkap_agent.main.worker_http_port`). Subprocess backend: always sets it to `0` (ephemeral). RUNBOOK §1 documents the variable | `agent/tests/unit/test_main.py::test_worker_http_port_*` (7 cases); `supervisor/tests/test_subprocess_backend.py` asserts the child env. Live: three clean rolls |
| B3 | `POST`/`PUT /v1/webhooks` accepted any event name. A subscription to `qa.completed` (the real name is `session.qa_completed`) saved and then never fired | `_check_events`: unknown names → 422 with `unknown` and `known` | `api/tests/test_webhooks.py::test_create_webhook_with_an_unknown_event_is_422` (2 cases), `…update…_is_422`. Live: 422 |
| B4 | `web/e2e/login-console-smoke.spec.ts` could never pass for real. It never pressed Start call (so no transcript), used selectors that don't exist, and its 30 s best-effort wait equalled the 30 s test timeout | Press Start call with a fake microphone, poll `[data-testid=session-transcript]` (best-effort, recorded as an annotation), `setTimeout(120 s)`, hang up | Live: 1 passed, transcript seen |

**Logged in `_asks.md` under "Open — left by V2-20"** (V2-20-1 … V2-20-5, with repro steps):
1. Four advertised webhook events are never emitted: `session.started`, `session.qa_completed`, `call.started` and `call.ended`. Only `session.ended` and `recording.ready` have emitters.
2. The worker callback URL comes from `PORT`, not the port the api actually listens on. This caused the isolation incident in §0.
3. A failed recording start (worker `recording` event `failed`, 422) leaves the session row's `recording.status` at `none`, so the console never shows why.
4. LiveKit Inference models have no price rows, so the default pipeline's `cost_usd` is always `null`.
5. The viewer UI still offers **New agent**, **New connection** and **Publish**, and the editor calls `validate` (403 twice per page load). The server refuses every write, so this is UI polish, not a hole.

## 5. Gates (after all fixes)

| Package | Result |
|---|---|
| contracts | ruff, format, `mypy --strict` clean; 2068 passed |
| api | ruff, format, `mypy --strict` clean (123 files); 850 passed, 1 skipped (87.7 s) |
| agent | ruff, format, `mypy --strict` clean (50 files); 935 passed, 9 skipped |
| supervisor | ruff, format, `mypy --strict` clean; 51 passed |
| web | `pnpm lint` 0 errors (5 pre-existing warnings), `pnpm typecheck` clean, `pnpm test` 76 files, 1165 tests passed |

`packs` and `testing` were unchanged and not re-run.

## 6. Cleanup

Every process V2-20 started was stopped with `launch.py stop <name>`: SIGINT, then at least 15 s, then SIGKILL only if still alive. None needed SIGKILL.

**Processes stopped**

| Process | How it stopped |
|---|---|
| `cloud-a` pool | `POST /v1/connections/027c…/fleet {"action":"stop"}`. The supervisor drained replica 75485 (`replica_stopped exit_code=0`), and the fleet showed 0 ready instances |
| supervisor 64255 | exited after SIGINT |
| worker `lkap-v220-a` 54540 | exited after SIGINT (`shutting down worker`). An external worker doesn't deregister; the api marks it `gone` after 90 s of silence |
| scratch api 74686, scratch web 70744 (`next dev -p 3096`), webhook receiver 66273, host page server 68769 | all exited after SIGINT |

**Afterwards**
- **Ports:** `lsof` shows :8096, :3096, :8097, :8098 and :9196 free.
- **Processes:** `ps` shows no `lkap_agent.main`, `lkap_supervisor`, uvicorn or next process of V2-20. The only ones left are the user's: worker 51801/51816, api 71145 (reloader) → 73809, and web 62881 → 39364.
- **Rooms:** `rooms.py` listed rooms against the 39 room names in the scratch `sessions` table. 0 rooms were listed in the project, so all had auto-closed and nothing needed deleting. No other room, agent or dispatch rule was touched.
- **`lk agent list`** (read-only, credentials from env) shows only `other-project-agent` (`CA_v5XKrAreaGbw`, ap-south). Nothing was deployed.
  - `lk agent list` shows Cloud-*deployed* agents, not connected self-hosted workers. So the proof that no `lkap-v220-*` worker remains is the dead PIDs above plus the drained fleet.
- **Telephony:** no SIP trunk, dispatch rule, number or call was created.

**The user's stack**

| Check | Result |
|---|---|
| Worker `lkap-agent` (51816) | Still running, never signalled |
| `:8080/v1/health` | `ok: true, db: ok` |
| `:3000` | 200 |
| `api/data/lkap.db`, read-only after the run | default connection `agent_name=lkap-agent`; 0 `cloud-a` rows, 0 `v220%` agents, 0 `v220%` users, 0 credentials, 0 webhook endpoints, 0 API keys, 0 worker rows from the isolation incident |

- **`--reload` note.** The user's api runs `uvicorn --reload`, so the source edits in §4 made its reloader restart the worker process (now PID 73809, parent 71145 unchanged). That is expected `--reload` behaviour, and health is ok afterwards.
- **Not restarted:** the user's `lkap-agent` worker has no hot reload, so it still runs the pre-fix agent code. B2 only matters to supervised pools, so nothing changes for it until the user restarts it.
