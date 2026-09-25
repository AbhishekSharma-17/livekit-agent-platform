# V4-12 live check: background and parallel tool calls (non-flow agents)

Status: **not run.** The code and its offline tests landed with V4-12; this is the protocol for the coordinator (`BACKGROUND-TOOLS.md` §9, PLAN-V4 V4-12 "Live", rulings R-V4-34 … R-V4-38). Flow-node background tools are V4-14's live check (they run blocking until the worker runs livekit-agents 1.8.3, R-V4-39).

The repo is public: no token, key, host, phone number or transcript beyond the quoted turns goes in this file. Session ids and agent ids are fine. A failing step is an ask with the log line, never a patch during the run (R-V4-20).

## Before you start

1. **A scratch api and a fresh-named worker (R-V4-17).** Run a scratch api on its own port and its own DB under the session scratchpad, and a worker registered under a fresh agent name (not `lkap-agent`, which is the user's; never a second worker with that name). Stop the scratch worker with SIGINT, wait at least 15 s, then SIGKILL, and only the PIDs you started. Record the worker's SDK version from its startup line (expect `1.8.2`) and the commit it runs:
   - Worker commit / SDK: …
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

Result: …

## Step 1: HTTP `auto` on a slow GET

Text first: `chat_start`, `chat_send("Fetch the echo.")`, wait 10 s, read the events, `chat_end`.

Pass when: the first assistant message acknowledges within one turn; the activity block shows `running` with the announce text (`detail.message` set); a `tool_call_updated` event exists; a later assistant message carries the result; a `tool_reply` event reads `completed`.

Then over voice in the browser: the filler "Still fetching." is heard about 3 s after the acknowledgement. Compare EOU → first audio of the acknowledgement turn with a blocking run of the same tool (set `execution.mode` to `blocking` for one call, then back) from the session detail's latency columns.

Result (text): …
Result (voice, EOU → first audio auto vs blocking): …

## Step 2: the fast path

`chat_send("Fetch the quick echo.")` (the `/delay/0` tool).

Pass when: no `tool_call_updated`, one assistant message (the result inline, one generation).

Result: …

## Step 3: a duplicate while running

During step 1's delay, `chat_send("Fetch it again.")`.

Pass when: the reply says it is on its way; no second `tool_call_started` for the same arguments (the SDK's `on_duplicate="reject"` with LKAP's template "That is already being looked up; tell the user it is on its way.").

Result: …

## Step 4: cancel

During a new slow call, `chat_send("Never mind, stop that.")`.

Pass when: the model calls `lk_agents_cancel_task`, `tool_call_ended.status == "cancelled"`, the activity row reads `cancelled`.

Result: …

## Step 5: hang up mid-tool

Over voice, ask for the slow echo and disconnect during the delay.

Pass when: `cancelled` events, no error in the worker log, the job ends within the reconnect grace.

Result: …

## Step 6: realtime (Gemini Live)

Switch the agent to `realtime` (Google realtime slot; `tool_behavior=NON_BLOCKING`, `tool_response_scheduling=WHEN_IDLE`, the registry defaults) and repeat step 1 over voice.

Pass when: the announcement is voiced by the model; no filler (expected: no TTS, R-V4-38; the worker logs "tool fillers skipped" once); the deferred reply lands after idle. Record how many times the worker logs the `tool_choice` warning for the deferred reply (an observation, not a bug).

Result: …

## Step 7: the text channel

On the text channel: `chat_send("Fetch the echo.")` shows the two messages (acknowledgement, then result); in a second run, `chat_rewind` during the delay.

Pass when: the rewind ends the call `cancelled` (the worker's `cancel_running`), and no late result message appears.

Result: …

## Step 8: the thinking sound

Set `voice.thinking_sound="keyboard_typing"`, attach `demo_blocking_echo`, and over voice ask for it.

Pass when: the typing clip is audible during the 2 s wait and stops when the answer starts. On the text channel the worker starts no player.

Result: …

## Step 10: clean up

Delete `Demo — Background tools` and the three `demo_*` tools, revoke `v412-live`, stop the scratch worker (SIGINT, wait, SIGKILL) and the scratch api. Diff `agent_list`/`tool_list` against the capture from "Before you start".

Result: …

## Run log

…
