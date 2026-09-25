# V4-12 live check: background and parallel tool calls (non-flow agents)

Status: **text half run (2026-09-25, 07:00–07:14 IST): steps 1 (text), 3, 4, 7 and 8 (text half) pass; step 2 fails as written (ask #101, a latency premise of the protocol, not an LKAP defect) and passes with a raised `auto_threshold_ms`; step 10 done.** The voice steps (9, 1-voice, 5, 6, 8's audible half) need a person speaking in the browser and have not been run. The code and its offline tests landed with V4-12; this is the protocol for the coordinator (`BACKGROUND-TOOLS.md` §9, PLAN-V4 V4-12 "Live", rulings R-V4-34 … R-V4-38). Flow-node background tools are V4-14's live check (they run blocking until the worker runs livekit-agents 1.8.3, R-V4-39). The tools were created with `tool_create_http`: V4-13's console editor had not landed, so **the console screenshots are pending V4-13**.

The repo is public: no token, key, host, phone number or transcript beyond the quoted turns goes in this file. Session ids and agent ids are fine. A failing step is an ask with the log line, never a patch during the run (R-V4-20).

## Before you start

1. **A scratch api and a fresh-named worker (R-V4-17).** Run a scratch api on its own port and its own DB under the session scratchpad, and a worker registered under a fresh agent name (not `lkap-agent`, which is the user's; never a second worker with that name). Stop the scratch worker with SIGINT, wait at least 15 s, then SIGKILL, and only the PIDs you started. Record the worker's SDK version from its startup line (expect `1.8.2`) and the commit it runs:
   - Worker commit / SDK: the worker process started at 07:00:58 IST on **`753d0c9`** (Merge V5-02 `request_block`, which sits on `4d9d59c` Merge V4-12). V5-02 touches only `agent/src/lkap_agent/ui/channel.py` and `contracts` `ui_protocol`. The checkout kept moving during the run. At 07:05:15, `d128e20` (V5-02 follow-ups) changed `agent/src/lkap_agent/main.py` (`NoopUiChannel` request methods), `packs/src/packs/base.py` and `contracts` exports. Each job runs in a newly spawned process ("no warmed process available"), so the jobs from step 3 on (07:05:31 onward) would have re-imported those files (inferred, not observed). The commits after it are docs only (`3fa08fd`, `78590b3`, `2d13825`). None of these commits touches a V4-12 file (`tools/execution.py`, `tools/declarative.py`, `text_mode.py`, `observability.py`, `platform_agent.py`, `session_builder.py`), `api/src` or `mcp/src`. Worker registered as **`lkap-agent-v412live`** (`AW_3iBy3kFaMhVr`, India South), `livekit.agents` **1.8.2** (rtc 1.1.18), image `slim`, 33 installed providers, `dev` mode, `LKAP_LOG_LEVEL=DEBUG`. Scratch api on `127.0.0.1:8127` with a fresh SQLite DB in the session scratchpad, migrated to `v4_002_provider_models`, fresh master/admin/service tokens, and its bootstrap `Default` connection bound to `lkap-agent-v412live` (so no job could reach the user's `lkap-agent`). The user's api (:8080), web (:3000) and `lkap-agent` worker were not touched
   - Key: `v412-live` (`df788332…`, prefix `lkap_eDx`, the six scopes above, `kind=agent`, `client=claude-code`, expires +1 day), held only in a 0600 MCP config in the scratchpad. The driver called `lkap-mcp` over stdio with no LLM in the loop (a copy of V4-11's `mcpc.py`). The MCP process ran through a thin wrapper (`mcp_tap.py`) that also records the agent's `lkap.ui.activity`/`lkap.ui.state` text streams from the chat room; that is where the activity rows below come from
   - Objects: agent `Demo — Background tools` = `6c5787db1d3f42698cd653599e9448b3` (`demo-background-tools`, pack `generic`, cascaded: `livekit-inference-stt deepgram/nova-3`, `livekit-inference-llm google/gemma-4-31b-it`, `livekit-inference-tts inworld/inworld-tts-2`, the pack defaults, checked rather than assumed). Tools: `demo_slow_echo` `3f4c4855…`, `demo_fast_echo` `4819e192…` (both with the brief's `execution` and dry-run 200), `demo_blocking_echo` `27cfe43a…` (step 8). `agent_validate`: 0 errors, 0 warnings. `tools.max_tool_steps` 3 → 4 (config_version 3). `agent_list` and `tool_list` before step 1: both empty (a fresh scratch DB)
   - **Tool descriptions** are not in the brief. The first attempt (session `a669098a…`) had both tools described as "Fetch the echo…", and the model asked "Would you like the quick echo or the slow one?" with no tool call. The descriptions were then made unambiguous: slow = "Fetch the echo. Call this whenever the user asks to fetch the echo (or asks for it again), unless they explicitly say 'quick echo' or 'blocking echo'."; fast = "…only when the user explicitly asks for the 'quick echo'."
2. **Key.** Mint one Builder key for this run (`agents:read, sessions:read, connections:read, providers:read, agents:write, sessions:write`, `expires_at` +1 day), named `v412-live`, held only in a 0600 MCP config in the scratchpad. Revoke it at the end (step 10). No `calls:write`, no telephony.
3. **Objects.** One new agent `Demo — Background tools` from the generic template, cascaded, `google/gemma-4-31b-it` on LiveKit Inference, and two tools, `demo_slow_echo` and `demo_fast_echo` (below). Nothing else is touched; capture `agent_list` and `tool_list` before step 1 and after step 10, and the diff outside these three objects must be empty.
4. **The tools (R-V4-18: `httpbin.org` only).** Create them through the console's HTTP tool editor (V4-13) when it has landed, so its screenshots go in this file; otherwise through `tool_create_http`:
   - `demo_slow_echo`: GET `https://httpbin.org/delay/4`, `allowed_hosts=["httpbin.org"]`, `timeout_s=10`, `max_result_chars=2000`, `execution={"mode":"auto","announce":"Fetching the echo now.","fillers":["Still fetching."],"filler_delay_s":3,"cancellable":true}`.
   - `demo_fast_echo`: the same at `https://httpbin.org/delay/0` with the same `execution`.
   - `demo_blocking_echo` (step 8 only): GET `https://httpbin.org/delay/2`, no `execution`.
   Attach them (`agent_attach`), then `agent_validate`: expect 0 errors. With `tools.execution_default` left at `blocking` there is no `max_tool_steps` warning; set `tools.max_tool_steps` to 4 anyway.

Record per step: pass/fail, the session id, the `tool_call_started` / `tool_call_updated` / `tool_call_ended` / `tool_reply` rows (status, timings), the activity rows the panel showed, and the LiveKit Inference cost.

## Step 9 first: interrupting the announcement (the design's premise)

Over voice in the browser (the console's Test call), ask "Fetch the echo." and talk over the acknowledgement ("Let me fetch that…") as soon as it starts.

Pass when: the acknowledgement is cut; `tool_call_ended.status == "done"` (not `cancelled`); the result is still spoken at the next idle (a `tool_reply` row `completed`). The offline counterpart is `agent/tests/unit/test_tool_execution.py::test_real_sdk_interrupting_the_turn_keeps_the_tool_running_and_the_result_lands` (text channel, scripted LLM); this step checks it on a real voice pipeline. If the result is lost, file an ask against D-V4-33 before V4-13 ships the console fields.

Result: **Needs the user (voice).** In the console's Test call on an agent set up as in "Before you start", say "Fetch the echo." and talk over the acknowledgement as soon as it starts; then read the session's `tool_call_ended` (expect `done`) and `tool_reply` (expect `completed`). Not run: this run had no one speaking. The text run's cancel paths (steps 4 and 7) are no evidence for this step.

## Step 1: HTTP `auto` on a slow GET

Text first: `chat_start`, `chat_send("Fetch the echo.")`, wait 10 s, read the events, `chat_end`.

Pass when: the first assistant message acknowledges within one turn; the activity block shows `running` with the announce text (`detail.message` set); a `tool_call_updated` event exists; a later assistant message carries the result; a `tool_reply` event reads `completed`.

Then over voice in the browser: the filler "Still fetching." is heard about 3 s after the acknowledgement. Compare EOU → first audio of the acknowledgement turn with a blocking run of the same tool (set `execution.mode` to `blocking` for one call, then back) from the session detail's latency columns.

Result (text): **pass.** Session `4ff580bc08c54fccaa8fa8d0988e9e81` (config_version 3, with the UI tap). An earlier identical run, `a91b6f2969f648e082fa23a1a4c96586`, also passed; its second turn is step 2. Timings are from the event `ts` (UTC):
- 01:35:02.523 `user_turn` "Fetch the echo."
- 03.108 `tool_call_started {call_id: call_1ae71d48…, tool: demo_slow_echo}`
- 03.811 `tool_call_updated {message_preview: "Fetching the echo now."}`: 703 ms after the start, which is the `auto_threshold_ms` of 700
- 04.421 first assistant message "Fetching that for you now." `chat_send` returned in 3.1 s, including its 1 s settle
- 08.503 `tool_reply {status: scheduled}`; 08.504 `tool_call_ended {status: done, duration_ms: 5395}`. The reply is recorded before `ended`, as ask #89 predicted
- 09.905 second assistant message "The echo has returned with your connection details and origin IP address. Is there anything else you'd like me to do with that information?"; `tool_reply {status: completed}`

Activity rows (`lkap.ui.activity`, id `tool:call_1ae71d48…`): `running` "Demo slow echo started", then `running` "Fetching the echo now." with `detail: {message: "Fetching the echo now."}`, then `done` "Demo slow echo finished" (`duration_ms` 5395). The same upserts arrived as `lkap.ui.state` patches on `/activity`. The worker logged `tool fillers skipped: this session has no voice (TTS)` once per call, the expected text-channel behaviour (D-V4-36). Cost: $0.0012 (and $0.0026 for `a91b6f29…`, which also ran step 2)
Result (voice, EOU → first audio auto vs blocking): **Needs the user (voice).** In a browser Test call, ask "Fetch the echo.": the filler "Still fetching." should be heard about 3 s after the acknowledgement. Then set `demo_slow_echo`'s `execution.mode` to `blocking` for one call, ask again, set it back, and compare EOU → first audio for the two acknowledgement turns in the session detail's latency columns.

## Step 2: the fast path

`chat_send("Fetch the quick echo.")` (the `/delay/0` tool).

Pass when: no `tool_call_updated`, one assistant message (the result inline, one generation).

Result: **fail as written (ask #101), pass with `auto_threshold_ms: 2500`.**
- As written, session `a91b6f29…` turn 2: `demo_fast_echo` took **1160 ms**. From this host, httpbin's `/delay/0` answers in 1.1–1.5 s (the dry run took 1122 ms; five `curl` samples took 1.15–1.50 s). That is above the 700 ms `auto_threshold_ms`, so the `auto` path did what it should for a slow call: `tool_call_updated` at +702 ms ("Fetching the echo now."), the acknowledgement "Getting that quick echo for you now.", `tool_call_ended {done, 1160}`, `tool_reply scheduled → completed`, then a second message "The quick echo is back, showing your connection details from httpbin.org. …". So the protocol's premise ("the fast path") does not hold for this endpoint from here; the runtime is not at fault
- Variant: session `7e72920bebd54a6f9a0d2351176d1dd3`, with the same `execution` plus `auto_threshold_ms: 2500` (set through `tool_update`). The events were `tool_call_started` (01:37:12.555), then `tool_call_ended {done, duration_ms: 2105}` (14.660): **no `tool_call_updated`, no `tool_reply`**, and **one** assistant message with the result inline, "Checking that now. I've fetched the quick echo for you." Activity: `running` "Demo fast echo started", then `done` (2105 ms), with no update row. Cost: $0.0011

## Step 3: a duplicate while running

During step 1's delay, `chat_send("Fetch it again.")`.

Pass when: the reply says it is on its way; no second `tool_call_started` for the same arguments (the SDK's `on_duplicate="reject"` with LKAP's template "That is already being looked up; tell the user it is on its way.").

Result: **pass.** Session `107eeeaa6ddc495eb5dd42f87ba72da3`:
- 01:35:34.783 "Fetch the echo." → 35.400 `tool_call_started call_edc700e7…` → 36.104 `tool_call_updated` → 37.910 "Fetching that now."
- 39.089 `user_turn` "Fetch it again." (sent right after the acknowledgement, while the GET ran). The model re-issued `demo_slow_echo`, and the worker logged `duplicate tool call rejected` (livekit.agents) at 40.184. The reply was **"It's on its way!"** (40.809)
- **One** `tool_call_started` in the session. The original call ended `done` (5278 ms) at 40.678, and its result was spoken at idle: `tool_reply scheduled` 40.812, then "The echo has returned with your request details from httpbin.org. …" and `tool_reply completed` 42.203
- Cost: $0.0020

## Step 4: cancel

During a new slow call, `chat_send("Never mind, stop that.")`.

Pass when: the model calls `lk_agents_cancel_task`, `tool_call_ended.status == "cancelled"`, the activity row reads `cancelled`.

Result: **pass.** Session `1575d5e5f9ae43289f112e68662e1bbc`:
- 01:36:19.238 "Fetch the echo." → 19.864 `tool_call_started call_62bfbfb8…` → 20.567 `tool_call_updated` → 21.228 "Fetching that for you now."
- 22.386 "Never mind, stop that." The model called `lk_agents_get_running_tasks` first (23.243, `done` in 2 ms; it returned the running `demo_slow_echo` call), then **`lk_agents_cancel_task(call_id=call_62bfbfb8…)`** (24.000). **`tool_call_ended {tool: demo_slow_echo, status: cancelled, duration_ms: 4141}`** at 24.006. The cancel tool returned "Task call_62bfbfb8… cancelled successfully." Reply: "Done. I've stopped that for you." No `tool_reply`, and no result message followed
- Activity: `running` (started) → `running` "Fetching the echo now." → **`cancelled`** "Demo slow echo cancelled" (4141 ms). The worker logged `tool cancelled` (livekit.agents)
- The model spent two tool steps here (list, then cancel); `max_tool_steps` = 4 covers them. Cost: $0.0024

## Step 5: hang up mid-tool

Over voice, ask for the slow echo and disconnect during the delay.

Pass when: `cancelled` events, no error in the worker log, the job ends within the reconnect grace.

Result: **Needs the user (voice).** In a browser Test call, ask for the slow echo and hang up during the 4 s delay. Then check for `tool_call_ended {status: cancelled}`, no error in the worker log, and a job end within `LKAP_RECONNECT_GRACE_S` (60 s). A **text-channel analogue** ran here as extra evidence: session `3b3cbab9bae349e499a77d5e39abfee3`, with `chat_end` during the delay. The worker logged `caller left, ending the job` (01:41:11.649), `tool cancelled`, and `tool_call_ended {cancelled, 3173 ms}` (11.652), then `process exiting` and `session summary posted` (13.173), with no error or traceback. The text channel ends at once on leave, so the reconnect grace was not exercised; the voice run still has to check it. Cost: $0.0011

## Step 6: realtime (Gemini Live)

Switch the agent to `realtime` (Google realtime slot; `tool_behavior=NON_BLOCKING`, `tool_response_scheduling=WHEN_IDLE`, the registry defaults) and repeat step 1 over voice.

Pass when: the announcement is voiced by the model; no filler (expected: no TTS, R-V4-38; the worker logs "tool fillers skipped" once); the deferred reply lands after idle. Record how many times the worker logs the `tool_choice` warning for the deferred reply (an observation, not a bug).

Result: **Needs the user (voice).** Switch the agent to `realtime` (the Google realtime slot, `NON_BLOCKING`/`WHEN_IDLE`) and ask "Fetch the echo." over voice. Then check that the model voices the announcement, that there is no filler, that the worker logs `tool fillers skipped` once, and that the deferred reply lands after idle; count the `tool_choice` warnings. Not run: this run had no one speaking.

## Step 7: the text channel

On the text channel: `chat_send("Fetch the echo.")` shows the two messages (acknowledgement, then result); in a second run, `chat_rewind` during the delay.

Pass when: the rewind ends the call `cancelled` (the worker's `cancel_running`), and no late result message appears.

Result: **pass** (the criteria), with an **observation filed as ask #102**.
- **Two messages:** step 1's session `4ff580bc…` shows the acknowledgement and then the result message (see step 1)
- **Rewind during the delay:** session `887aae7e43f542ae980f16a17c30144a`. After "Fetch the echo." (`tool_call_started call_dad9d583…` 01:38:24.823, acknowledgement 26.971), `chat_rewind(turn_index=1)` was sent at once. The worker logged `agent_action_received action=rewind` → `text_mode_rewind turn_index=1` (28.133) → `tool cancelled` (28.139), and **`tool_call_ended {status: cancelled, duration_ms: 3316}`** (28.140). Activity went `running` → `running` (update) → **`cancelled`**. **No `tool_reply` and no late result message** in the 12 s that followed, up to `chat_end`
- **But** the regenerated reply was **"It's on its way!"**, and it made no tool call. The user is told a result is coming, and none ever comes. This reproduced on a second run: session `c67ec25ab2154273980a4f05e1c51dca`, "It's on its way! I'll let you know as soon as I have it.", with the call again `cancelled` (01:39:52.128). The regenerated inference read **823** input tokens. The first inference for the same kept context (system prompt, greeting, "Fetch the echo.") read **794**. So a stale function-call pair for the cancelled call is still in the context the model sees (ask #102)
- **Edit variant:** session `f0f7cb61fb5848d5a0681bcb8dbb0722`, `chat_rewind(turn_index=1, replace_text="Thanks, that's all.")`. The worker logged `text_mode_inject_user_text turn_index=0` → `tool cancelled` → `tool_call_ended {cancelled, 3267 ms}` (01:38:04.274), with no late result. The model then answered "You're welcome! Have a great day." and called `end_call`; the session ended `end_call tool invoked`
- Cost: $0.0014 + $0.0014 + $0.0014

## Step 8: the thinking sound

Set `voice.thinking_sound="keyboard_typing"`, attach `demo_blocking_echo`, and over voice ask for it.

Pass when: the typing clip is audible during the 2 s wait and stops when the answer starts. On the text channel the worker starts no player.

Result: **audible half: Needs the user (voice)**; **text half: pass.**
- **Voice (not run):** in a browser Test call, ask for the blocking echo. The typing clip should be heard during the ≈2 s wait and stop when the answer starts
- **Text:** session `c6af8b40c87b4d76b09041b82dc7478b` (config_version 5, `voice.thinking_sound="keyboard_typing"`, `demo_blocking_echo` attached with no `execution`; `agent_validate` 0 errors, 0 warnings). "Fetch the blocking echo." gave `tool_call_started` → `tool_call_ended {done, duration_ms: 3202}` with no `tool_call_updated` and no `tool_reply`, then one reply, "I've fetched the blocking echo for you." The worker built the session with `has_tts=False`, and it logged **no `thinking sound started`** and started no `BackgroundAudioPlayer`: `start_thinking_sound` returns early on a text-only plan and does not log. Cost: $0.0011

## Step 10: clean up

Delete `Demo — Background tools` and the three `demo_*` tools, revoke `v412-live`, stop the scratch worker (SIGINT, wait, SIGKILL) and the scratch api. Diff `agent_list`/`tool_list` against the capture from "Before you start".

Result: **done.**
- `Demo — Background tools` went through V4-11's ladder: `lkap_delete` → `needs_confirmation`, then `confirm` → 409 "agent still has sessions", then `agent_archive(confirm)`, then `lkap_delete(confirm, purge)` → `DELETE …?purge=true` 204. The three `demo_*` tools were deleted (204 each)
- A mistake while scripting exposed a small MCP bug. `lkap_delete(kind="tool", id="")` sent `DELETE /v1/tools/`, got **307**, and reported `{"deleted": true}` (ask #103). The blocking tool was then deleted by its real id
- `agent_list` and `tool_list` after cleanup are both **empty**, the same as before. The diff outside the three objects is empty
- `v412-live` was revoked: `DELETE /v1/api-keys/df788332…` → 204, `revoked_at` 01:43:06Z. Its MCP config was deleted
- The scratch worker (PID 34783) and the scratch api (PID 34410) each got SIGINT and had exited by the 15 s check; no SIGKILL was needed. No process under the scratch dir remains, and :8127 is free. The user's `lkap-agent` worker, api (:8080) and web (:3000) were running throughout and untouched
- In the scratch DB, `livekit_connections.api_key_ct`/`api_secret_ct` were overwritten with empty blobs, and the scratch token file was deleted

## Summary (text half, 2026-09-25)

| Step | Session | Result |
|---|---|---|
| 9 interrupt the announcement | — | Needs the user (voice) |
| 1 text: `auto` on a slow GET | `4ff580bc…` (also `a91b6f29…`) | **pass** |
| 1 voice: filler, EOU → first audio | — | Needs the user (voice) |
| 2 fast path, as written | `a91b6f29…` turn 2 | **fail**: `/delay/0` took 1160 ms, over the 700 ms threshold (ask #101) |
| 2 fast path, `auto_threshold_ms: 2500` | `7e72920b…` | **pass** |
| 3 duplicate while running | `107eeeaa…` | **pass** (`duplicate tool call rejected`, one `tool_call_started`) |
| 4 cancel | `1575d5e5…` | **pass** (`lk_agents_cancel_task`, `cancelled`) |
| 5 hang up mid-tool | `3b3cbab9…` (text analogue) | text analogue passes; voice run needs the user |
| 6 realtime | — | Needs the user (voice) |
| 7 text channel: rewind | `887aae7e…`, `c67ec25a…`, `f0f7cb61…` | **pass** (cancelled, no late result); the regenerated reply claims "It's on its way!" (ask #102) |
| 8 thinking sound | `c6af8b40…` (text) | text half passes (no player); audible half needs the user |
| 10 clean up | — | **done** |
| LiveKit Inference cost | 11 sessions | **$0.0163** in all (`cost_usd` per session: 0.000653, 0.002572, 0.001247, 0.002015, 0.002369, 0.001093, 0.001387, 0.001363, 0.001379, 0.001145, 0.001082). $0 of Claude: MCP was driven directly, with no LLM driver |

## Run log

- **07:00–07:14 IST (01:30–01:44 UTC), driver Opus 5.5.** The work is in `<scratchpad>/v412-live/`: `launch.py` (scratch api and worker, secrets through env only), `mcpc.py` and `mcp_tap.py` (the MCP driver and the UI-stream tap), `scripts/` (one JSON script per step), `logs/` (call logs, worker and api logs, `ui-tap.jsonl`) and `snap/`.
- **Order:** setup; step 1 (first try `a669098a…`: the model asked which echo, see "Before you start"); step 1 and step 2 (`a91b6f29…`); step 1 again with the tap (`4ff580bc…`); step 3; step 4; the step 2 variant; step 7 (edit, rewind, rewind again); step 8 text; the step 5 text analogue; step 10.
- **Worker log, all 11 jobs:** no error and no traceback, and the api log has none either. The warnings repeat on every job and none comes from V4-12:
  - `TurnDetector requires a VAD model. Pass vad=inference.VAD() …` (livekit.agents), once per **text** session, although `session built` reports `has_turn_detector=False has_vad=False`. It is noise on the text channel and predates V4-12: V4-11's second-run worker log, from before V4-12, has it 11 times
  - `no warmed process available for job, waiting for one to be created` on most jobs, a fresh `dev` worker with its default idle-process pool; each job still started in about 1.5 s
  - One `event loop blocked for 105ms` (01:38:39, while nothing was running)
- **Observed, as designed:** `tool_reply {scheduled}` is recorded about 1 ms before `tool_call_ended` for a deferred result (ask #89). A cancelled call gets no `tool_reply`. On text, `tool fillers skipped: this session has no voice (TTS)` is logged once per call that has fillers.
- **For the voice steps** (9, 1-voice, 5, 6, 8-audible): the scratch agent and tools are deleted. Re-create them as in "Before you start", with the step-2 fix from ask #101 if it is ruled, on a stack whose worker runs `4d9d59c` or later.
