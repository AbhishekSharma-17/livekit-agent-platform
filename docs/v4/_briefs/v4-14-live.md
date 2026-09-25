# V4-14 live check: flow-node background tools on livekit-agents 1.8.3

Status: **text run done (2026-09-25, 07:51–08:13 IST, on a scratch stack): the 1.8.3 gate is lifted; 1a, 1b and 2b pass; 2a passes on the #7321 point (the handoff is kept) with an observation (ask #126); step 1 needed a sharper tool description (ask #125); clean-up done.** Nothing here needed a voice call. This is the coordinator's protocol for PLAN-V4 V4-14 "Live" (rulings R-V4-39 and R-V4-30 (ii), decision D-V4-37, `BACKGROUND-TOOLS.md` §1.9). The pin bump landed with V4-14 under rulings R-V4-52 … R-V4-56 (asks #97 … #100). Below 1.8.3 a flow-node tool is downgraded to `blocking`, which is why the run first checks that the worker reports 1.8.3.

The repo is public: no token, key, host, phone number or transcript beyond the quoted turns goes in this file. Session ids and agent ids are fine. A failing step becomes an ask with the log line. Never patch during the run (R-V4-20).

## Before you start

1. **The worker runs 1.8.3.** After the merge, run `uv sync` in the main checkout's `agent/`. Then the user restarts their `lkap-agent` worker. The run never restarts, signals or kills it. Record what the worker reports, from its startup line or `connection_fleet` status. Expect SDK **1.8.3**. On 1.8.3 the startup log also has a new warning, "agent_name is set in code; move it to livekit.toml …" (upstream #7295). That warning is expected and not a failure.
   - Worker commit / SDK: **a scratch worker, not the user's.** Following the V4-12 live check (R-V4-17), the run used a scratch api on `127.0.0.1:8128` with its own SQLite DB in the session scratchpad (migrated to `v4_002_provider_models`, fresh master/admin/service tokens, its bootstrap `Default` connection bound to the scratch worker) and a worker registered as **`lkap-agent-v414live`** (`AW_KXtBpfqLkEeD`, India South), started 07:56:05 IST from the main checkout at **`6c08a08`** (Merge V4-14) with `agent/.venv` synced. Startup line: `starting worker {"version": "1.8.3", "rtc-version": "1.1.18"}`, and the expected `agent_name is set in code; move it to livekit.toml …` warning (#7295). The user's `lkap-agent` worker, api (:8080) and web (:3000) were not touched. **The checkout moved during the run:** at 08:03:59 main became `a67892a` (the #102/#103 fixes: `agent/src/lkap_agent/tools/execution.py` `cancel_running`, `text_mode.py`, `mcp/src/lkap_mcp/client.py`, `tools/generic.py`), then `8d06302` (docs). Job processes are spawned fresh, so the sessions from 08:05 on (`6610903e…` onward, which include every verdict session) probably re-imported those files (inferred, not observed). The change is confined to the text-rewind path (`cancel_running` is called only from `text_mode.py`), which no step here exercises; `resolve_execution`, the flow runtime and the SDK's activity-close cancellation are unchanged. V4-13's merge (08:01:37) touched no `agent/`, `api/`, `mcp/`, `packs/` or `contracts` source
   - **Gate:** no session has the `info` event "flow-node tool runs blocking until livekit-agents >= 1.8.3 (#7321)", and the worker log never has that line. The absence counts only in sessions that bound the tool, the ones from `6610903e…` on; there `demo_flow_delay` ran with `tool_call_updated` (the announce) and ended `cancelled` or `done`, which only a non-blocking flow-node tool does
   - Key: `v414-live` (`7da8e096…`, prefix `lkap_I6y`, the six scopes, `kind=agent`, `client=claude-code`, expires +1 day), held only in a 0600 MCP config in the scratchpad, used by a direct MCP stdio driver with no LLM in the loop (V4-11's `mcpc.py`) through V4-12's `mcp_tap.py`, which also records the `lkap.ui.activity` stream. A first key (`33573cc3…`) was minted on the run-0 scratch DB. It was never revoked through the api: that DB was set aside unused, its api process is stopped, and its MCP config was deleted before the second key was minted (see the run log)
   - Objects: the scratch api had no agents, so **`Demo — Receptionist`** was created from the `receptionist` starter (`agent_create(template_id="receptionist")`): `6c38b27850244ede9c78eff2c8b2819e`, `demo-receptionist`, cascaded, LLM `google/gemma-4-31b-it` on LiveKit Inference (not changed), with the starter's seeded tools `check_availability` `7dd1c2ee…` and `book_appointment` `958cf1c3…` (both at `example.com`; no turn reached `book`, so neither ran). The "before" capture was taken right after that create: agents = [`Demo — Receptionist`, config_version 1]; tools = those two. `demo_flow_delay` = `c154a3f768554252b4f4c80ede550716` (workspace-level, the brief's definition and `execution`). `Demo — Router scratch` = `b67d13becbf74ba7bfc6c6b926dde0b0`
   - Receptionist config_version: 1 (created) → 2 (`agent_attach`) → **3** (`identify.tools = ["demo_flow_delay"]`; `agent_flow_validate` 0 errors, 0 warnings; `agent_validate` 0 errors, one pre-existing `knowledge.auto_inject` tip). The starter stores `identify.tools` as `[]`, so step 4 restores `[]`
2. **The gate is lifted.** In the first flow session of step 1, the event feed must **not** contain the `info` event "flow-node tool runs blocking until livekit-agents >= 1.8.3 (#7321)". If it does, the worker is still on 1.8.2: stop.
3. **Key (R-V4-17 rule 1).** Mint one Builder key for this run, named `v414-live`, with scopes `agents:read, sessions:read, connections:read, providers:read, agents:write, sessions:write` and `expires_at` +1 day. Keep it only in a 0600 MCP config in the scratchpad, and revoke it in step 4. No `calls:write`, no telephony, no Operator scopes.
4. **Objects touched.** Only these: `Demo — Receptionist`; one new tool `demo_flow_delay`; one new agent `Demo — Router scratch`, deleted in step 4. Capture `agent_list` and `tool_list` (id, slug, config_version, updated_at) before step 1 and after step 4. Outside these objects the diff must be empty.
5. **Model.** The demo agents' default LLM, `google/gemma-4-31b-it` on LiveKit Inference. Don't change it.
6. **The tool (R-V4-18: `httpbin.org` only).** Create it with `tool_create_http` (or with the console's HTTP tool editor, V4-13):
   - `demo_flow_delay`: GET `https://httpbin.org/delay/3`, `allowed_hosts=["httpbin.org"]`, `timeout_s=10`, `max_result_chars=2000`, `execution={"mode":"background","announce":"Let me check that in the background.","cancellable":true}`. Description: "Look up the caller's account status. Takes a few seconds."

For each step, record: pass/fail, the session id, the `tool_call_started` / `tool_call_updated` / `tool_call_ended` / `tool_reply` rows (status and timings), the `flow_ended` payload, and the LiveKit Inference cost.

## Step 1: Receptionist, background tool on `identify`

Attach `demo_flow_delay` to `Demo — Receptionist` (`agent_attach`). In `config.flow`, add `"demo_flow_delay"` to the `identify` node's `tools` and change nothing else. Then run `agent_flow_validate` and `agent_update(patch={"flow": …})`. Run `agent_validate` and expect 0 errors. Write down the config_version before and after, because step 4 restores it.

**1a: the acknowledgement is spoken, and the transition still happens while the tool runs.** Run `chat_start`, then `chat_send("I'd like to book a cleaning. Can you check my account status first? I'm Alex Doe, phone ending 0000.")`. The model should call `demo_flow_delay`. Within 3 s, before the tool returns, run `chat_send("Yes, that's right, my name and number are confirmed.")`.

Pass when all of these hold:
- the acknowledgement ("Let me check that in the background." or the model's rewording) is the first assistant message after the call;
- the session `path` reaches `collect_booking` (`identify → collect_booking`) without waiting for the tool;
- the tool's `tool_call_ended.status == "cancelled"`, and the activity feed row reads `cancelled`. This is D-V4-37's activity scoping: the transition closes `identify`'s activity, and that cancels its cancellable background tools;
- no `tool_reply` for that call reaches `collect_booking`.

Result: **pass**, after a tool-description change (ask #125). As written the case never started, so these sessions are recorded but are not verdicts:
- `38f1d1238f9f4c4bbd2fb468caf67b56` and `484e85b1800d43578fbf38bf254ba95d`, the brief's exact turns: no tool call. The model asked for the full phone number ("…could you please provide your full phone number?"), because `identify` says to confirm the number digit by digit and "phone ending 0000" is not a number. Turn 2 did not move the flow (`path [start, identify]`, `missing_required: ["phone"]`)
- `dc973f0a797c4205b85175ff597359e4`, turn 1 with a full fictional 555-01xx number: again no call, but the reply **claimed** one: "Checking that now. Just to make sure I have it right, is your phone number …?". Turn 2 then moved the flow to `collect_booking`
- `5d6bc6cc861f427e9a74dd30ec0f69eb` (turn 1 asking outright to "look up my account status with your account-status tool") and the probe `60ff21829e154cd0b63cd9172dcd4ccf` (asking the model to list its tools, which it refused): no call either
- The worker never logged "flow node references a tool this session does not have", and every job ran `config_version=3`. `demo_flow_delay`'s description was then made explicit with `tool_update`: "Look up the caller's account status. Call this whenever the caller asks about their account status, even before their number is confirmed. Takes a few seconds." After that change the model called it in every session. `identify.instructions` and the rest of the flow were not touched

Verdict session **`7c09fbd517624817ac1164ac4cc50b9d`** (turn 1 with the full fictional number, since the brief's "phone ending 0000" never lets turn 2 confirm; UTC):
- 02:36:15.509 `user_turn` "I'd like to book a cleaning. Can you check my account status first? I'm Alex Doe, my number is <fictional 555-01xx>."
- 16.365 `tool_call_started {call_id: call_cbeb3e4f…, tool: demo_flow_delay}`, 16.367 `tool_call_updated {message_preview: "Let me check that in the background."}`
- 17.505 **first assistant message after the call**: "Checking your account status now. Just to make sure I have your number right, is that <digits>?" (the model's rewording of the acknowledgement)
- 18.842 `user_turn` "Yes, that's right, my name and number are confirmed." (2.5 s after the call started, while it ran)
- 19.735 `go_to_collect_booking` started, 19.737 ended `done` (2 ms); **19.740 `tool_call_ended {tool: demo_flow_delay, status: cancelled, duration_ms: 3375}`**; 19.742 `handoff identify → collect_booking (edge identify_collect)`. The transition did not wait for the tool
- 21.811 `collect_booking`'s reply: "Perfect. Since you're looking for a cleaning, when would you like to come in?"
- **No `tool_reply`** for `call_cbeb3e4f…`. Activity rows (`tool:call_cbeb3e4f…`): `running` "Demo flow delay started" → `running` "Let me check that in the background." (`detail.message`) → **`cancelled`** "Demo flow delay cancelled" (3375 ms). The feed reads `cancelled`
- Cost: $0.002768

**1b: a node that waits for the result speaks it.** In a new session, repeat the first turn only. Then wait 8 s without sending anything, then `chat_send("What did you find?")` if the result has not already been spoken.

Pass when: `tool_call_ended.status == "done"`, a `tool_reply` row reads `completed`, and an assistant message on `identify` carries the result (httpbin's echo, so any mention of the lookup finishing counts). `path` stays `[start, identify]` until the caller confirms.

Result: **pass.** Session **`464af269ab9b462f88d392ad5ee7097e`** (the brief's exact first turn, with the sharpened description):
- 02:37:31.425 `user_turn` (the brief's text); 32.303 `tool_call_started call_a934b516…` and `tool_call_updated` ("Let me check that in the background."); 33.356 acknowledgement "I'm checking your account status now. To make sure I have everything correct, could you please give me your full phone number?"
- 36.638 `tool_reply {status: scheduled}`, 36.639 `tool_call_ended {status: done, duration_ms: 4335}`
- **37.706, unprompted, on `identify`:** "I've found your account, Alex. Just to be absolutely sure, could you please give me your full phone number?", with `tool_reply {status: completed}` (it came back as a late reply to the MCP client). So the result was spoken before the 8 s wait ended. The driver sent "What did you find?" anyway at 42.711 (the script had no branch); the reply was "Your account is all set! …"
- Activity (`tool:call_a934b516…`): `running` "Demo flow delay started" → `running` "Let me check that in the background." → `done` "Demo flow delay finished" (4335 ms). `path` stayed `[start, identify]` (the caller never confirmed)
- Also seen in `6610903e3aa64f5aa7113becafe87637` (the brief's 1a turns right after the description change): the call ended `done` (4326 ms) with `tool_reply scheduled → completed`, but its reply came after turn 2 and did not mention the lookup ("I'm sorry, I still need your full phone number …")
- Cost: $0.002754

## Step 2: the two-edge scratch router, the #7321 case

This check is why the package exists. On 1.8.2, when an edge (`go_to_*`) tool and an ordinary tool come in the same batch and the ordinary one finishes **after** the edge tool, `new_agent_task` is overwritten with `None` and the handoff is silently dropped. 1.8.3 guards the assignment with `is not None` (upstream #7321, in both the pipeline and realtime paths).

Build the router exactly like V4-11's step 4 (`v4-11-live.md`). Run `agent_create(name="Demo — Router scratch", pack_id="generic", description="Demo agent created by V4-14 on <date> through lkap-mcp; deleted at the end of the run.")` with the same LLM. Give it the flow `start → claims | billing` (edges `start_claims` "the caller wants to file or ask about a claim", `start_billing` "the caller asks about a bill or a payment"). Also add a `global` node with `tools: ["demo_flow_delay"]` and the instructions "Whenever the caller mentions a claim, also call demo_flow_delay to look up their account status." A `StartNode` has no `tools` field, so a routing start gets its tools from the global node. Attach `demo_flow_delay`. `agent_flow_validate` should report 0 errors.

**2a: the tool runs `blocking`. This is the definitive #7321 case.** Set the tool's `execution.mode` to `blocking` (`tool_update`). A blocking sibling is exactly what drops the handoff on 1.8.2, because `/delay/3` finishes long after `go_to_claims`. Run `chat_start`, then `chat_send("I'd like to file a claim.")`.

Pass when:
- `go_to_claims` **and** `demo_flow_delay` were called in the **same** batch. Check that both `tool_call_started` rows fall in the same turn, i.e. the same generation or speech id. If the model called only one of them, the case was not exercised: record it as **inconclusive** and retry up to twice;
- `demo_flow_delay` ended **after** the edge tool (compare the `tool_call_ended` timestamps);
- the session `path` is `["start", "claims"]` after this first turn. The handoff was **not** dropped;
- **the first assistant message after the batch is the claims node's question** (R-V4-64, V4-19): the draining router says nothing after the handoff, and no assistant message falls between the `user_turn` and `handoff start → claims`. Until V4-19 was merged, the handoff-kept criterion alone was the pass (the run below was judged on it). After the merge and a worker restart, a router sentence here fails the step. If one still appears, file it as an SDK ask (the draining activity's reply generation) with the session id, and do not patch around it. Also record whether the claims node re-calls the `global` tool on enter. That is model behaviour, reported and not fixed.

Result: **pass on the #7321 point (the handoff is kept); observation filed as ask #126.** Router agent `b67d13becbf74ba7bfc6c6b926dde0b0` (`demo-router-scratch`, pack `generic`, `google/gemma-4-31b-it`, config_version 3). The flow is the one above plus the `global` node; `agent_flow_validate` and `agent_validate` gave 0 errors, 0 warnings. `tool_update(execution={"mode": "blocking"}, patch={})`. `tool_update`'s `execution` replaces the whole object, so 2b passed the full spec back. Session **`274e452e29494905875183dc642fcf6e`**:
- 02:39:11.206 `user_turn` "I'd like to file a claim."
- **Same batch:** 12.035 `tool_call_started demo_flow_delay (call_9679a550…)`, 12.054 `tool_call_started go_to_claims (call_aad4d41b…)`. The worker logged both `executing tool` lines before a single `tools execution completed` (16.290), so it was one generation
- 12.055 `go_to_claims` ended `done` (1 ms) and the runtime logged `flow transition start → claims`. **16.289 `demo_flow_delay` ended `done` (4254 ms), 4.23 s after the edge tool.** This is the 1.8.2 drop case
- 17.929 first reply: "Checking that now. I can certainly help you file a claim. To get started, could you please provide your policy number?" This is **not** the claims node's question. It is the SDK's tool reply to the blocking sibling's output, generated by the **draining start agent** with `tool_choice="none"` (`agent_activity.py` 1.8.3: `update_agent(new_agent_task)`, then `draining = True`, then the tool response). See ask #126
- 17.930 **`handoff start → claims` (edge `start_claims`)**. The worker logged `flow node entered node=claims path=['start', 'claims']`. The claims node's on-enter called `demo_flow_delay` again (the global node's instruction, 19.320 → 23.534, `done` 4214 ms), then at 25.110 it asked **the claims question**: "Checking that now. I can certainly help you with that. Could you tell me what happened and when it occurred?"
- `path` after turn 1: **`["start", "claims"]`**. The handoff was kept. No `tool_reply` rows (blocking). Activity: `running` → `done` twice
- Cost: $0.001002

**2b: the tool runs `background`.** Set `execution.mode` back to `background` and repeat 2a in a new session. The background dispatch returns at its first update, so it normally finishes before the edge tool. This run proves the lifted downgrade does not reintroduce the drop.

Pass when: same batch (or inconclusive, as above); `path == ["start", "claims"]` after turn 1. The router's background call is cancelled by the transition (`cancelled` in the feed), or it finishes first. Either is acceptable, and the brief records which one happened.

Result: **pass**; the router's background call was **cancelled by the transition**. `tool_update(execution={"mode": "background", "announce": "Let me check that in the background.", "cancellable": true}, patch={})`. Session **`31e37b124f45415e804f33114e097424`**:
- 02:41:15.586 `user_turn` "I'd like to file a claim."
- **Same batch:** 17.129 `tool_call_started demo_flow_delay (call_6ef890c2…)` and `tool_call_updated` (announce); 17.130 `tool_call_started go_to_claims`; 17.150 `go_to_claims` `done` (20 ms); one `tools execution completed` (17.151)
- 18.729 the draining router's tool reply (the background call's first update is its output): "Checking that now. I can certainly help you with your claim. What happened?"
- **18.730 `tool_call_ended {demo_flow_delay, status: cancelled, duration_ms: 1601}`**; the worker logged `tool cancelled` (livekit.agents). 18.731 `handoff start → claims`. Activity: `running` → `running` (announce) → **`cancelled`** (1601 ms)
- On `claims`: 19.559 `demo_flow_delay` again (background, announce); 21.149 "Checking that now. I can certainly help you file a claim. Could you tell me what happened and when it occurred?"; 23.827 `tool_reply scheduled` and `tool_call_ended done` (4268 ms); 24.916 "I've pulled up your account status. Now, could you please tell me what happened and when it occurred so we can get your claim started?" with `tool_reply completed`
- `path` after turn 1: **`["start", "claims"]`**
- Cost: $0.001277

## Step 3: flow_ended payloads

Run `chat_end` on every session above and copy each `flow_ended` payload here (`completed`, `current_node`, `path`, `variables`, `disposition`, `reason`).

- 1a (`7c09fbd5…`): `{completed: false, current_node: "collect_booking", path: ["start", "identify", "collect_booking"], variables: {caller_name: "Alex Doe", phone: <the fictional number, E.164>, service: "cleaning"}, disposition: null, reason: "participant left: CLIENT_INITIATED"}`
- 1b (`464af269…`): `{completed: false, current_node: "identify", path: ["start", "identify"], variables: {caller_name: "Alex Doe"}, disposition: null, reason: "participant left: CLIENT_INITIATED", missing_required: ["phone"]}`
- 2a (`274e452e…`): `{completed: false, current_node: "claims", path: ["start", "claims"], variables: {}, disposition: null, reason: "participant left: CLIENT_INITIATED"}`
- 2b (`31e37b12…`): `{completed: false, current_node: "claims", path: ["start", "claims"], variables: {}, disposition: null, reason: "participant left: CLIENT_INITIATED"}`
- The non-verdict sessions: `38f1d123…`, `484e85b1…`, `6610903e…` ended on `identify` (`missing_required: ["phone"]`); `dc973f0a…` and `5d6bc6cc…` on `collect_booking`; the probe `60ff2182…` on `identify` with no variables

## Step 4: clean up

1. `Demo — Receptionist`: remove `demo_flow_delay` from `identify.tools` and detach it (`agent_attach(tool_ids=[…], remove=true)`). The stored flow must match its pre-step-1 flow exactly, except that `config_version` moves forward.
2. `lkap_delete(kind="agent", id=<Demo — Router scratch>)` with the user's `confirm=true`. Use the delete ladder if sessions exist (`agent_archive(confirm)`, then `lkap_delete(confirm, purge)`), as V4-11 did.
3. `lkap_delete(kind="tool", id=<demo_flow_delay>)` with `confirm=true`.
4. Revoke the `v414-live` key and delete the scratchpad MCP config.
5. Run `agent_list` / `tool_list` again. Compared with the "before" capture, the only differences allowed are the receptionist's `config_version`/`updated_at` and the created-then-deleted objects.

Result: **done.**
- `Demo — Receptionist`: `agent_update(patch={"flow": <the pre-step-1 flow>})` (validated, 0 errors) → config_version 4, then `agent_attach(tool_ids=[demo_flow_delay], remove=true)` → **5**. The stored flow equals the pre-step-1 flow exactly (`identify.tools == []`), `tool_ids` is back to the two seeded tools, and the whole config equals the one at creation
- `Demo — Router scratch`: V4-11's ladder. `lkap_delete` → `needs_confirmation`; with `confirm` → 409 "agent still has sessions"; `agent_archive(confirm)`; `lkap_delete(confirm, purge)` → `deleted: true`
- `demo_flow_delay`: `lkap_delete(kind="tool", confirm=true)` → `deleted: true`
- After: agents (including archived) = [`Demo — Receptionist`]; it differs from the "before" capture only in `config_version` (1 → 5), `session_count` and `last_session_at`. Tools = the same two seeded rows, and their `updated_at` is unchanged. **The diff outside the manifest is empty.** The receptionist copy and its seeded tools then went through the same ladder, because they live in the scratch DB; `agent_list`/`tool_list` ended empty
- `v414-live` revoked (`DELETE /v1/api-keys/7da8e096…` → 204, `revoked_at` 02:43:03Z), and its MCP config was deleted. The scratch worker (PID 91245) and scratch api (PID 91014) each exited on SIGINT by the 15 s check, with no SIGKILL; :8128 is free. The run-0 api (PID 88862) needed SIGKILL after 45 s; the run-0 worker (89296) exited on SIGINT. `livekit_connections.api_key_ct`/`api_secret_ct` were blanked in both scratch DBs, and the scratch token file was deleted

## Summary

| Check | Ruling | Session | Pass |
|---|---|---|---|
| Worker on 1.8.3, no downgrade `info` event | R-V4-39 | `lkap-agent-v414live`, all sessions | **pass** (`1.8.3`; no `info` event, no log line) |
| 1a: acknowledgement spoken, `identify → collect_booking` while the tool runs, tool `cancelled` | D-V4-37 | `7c09fbd5…` | **pass** after the description fix; as written, no tool call (ask #125) |
| 1b: a waiting node speaks the result | D-V4-37 | `464af269…` | **pass** |
| 2a: blocking sibling finishes after `go_to_claims`, handoff kept | #7321, R-V4-30 (ii) | `274e452e…` | **pass** (handoff kept, same batch, sibling +4.23 s); the first reply is the draining router's (ask #126) |
| 2b: background sibling on the router, handoff kept | R-V4-39 | `31e37b12…` | **pass** (sibling `cancelled` by the transition) |
| Clean-up diff empty outside the manifest | R-V4-17 | — | **pass** |

LiveKit Inference cost: **$0.0190** for the 10 sessions (`cost_usd`: 0.001633, 0.001626, 0.002096, 0.002113, 0.00102, 0.002681, 0.002768, 0.002754, 0.001002, 0.001277). $0 of Claude: MCP was driven directly, with no LLM driver.

## Run log

- **07:51–08:13 IST (02:21–02:43 UTC), driver Opus 5.5.** The work is in `<scratchpad>/v414-live/`: `launch.py` (V4-12's, pointed at `:8128` and `lkap-agent-v414live`; LiveKit values reach the children through env only), `mcpc.py`, `mcp_tap.py`, `scripts/` (one JSON script per step), `logs/` and `snap/` (the before/after captures and the receptionist flow).
- **Run 0 was discarded (ask #127).** On the first scratch DB, `agent_create(template_id="receptionist")` never returned: the MCP call hit `ReadTimeout` at 30 s. The api logged `kb_seed_kb_created` and then `fastembed_model_loading model=BAAI/bge-small-en-v1.5` into an empty model cache. For the next ~90 s every other write failed with `sqlite3.OperationalError: database is locked`: `worker_instances_sweep_failed`, `sessions_sweep_failed`, and `unhandled_error error_type=OperationalError path=/internal/v1/workers/{instance_key}/heartbeat` (500). The seed's KB row holds the write lock while the embedder loads. That api and worker were stopped, and the DB was set aside. The repo's model cache was **copied** (not linked) into the scratch data dir, a fresh DB was migrated, and the key was minted again. The create then took 0.5 s.
- **Order:** step 1 as written (`38f1d123…`, `484e85b1…`), variants (`dc973f0a…`, `5d6bc6cc…`), the probe (`60ff2182…`), the description change, the exact turns again (`6610903e…`), 1a (`7c09fbd5…`), 1b (`464af269…`), router setup, 2a (`274e452e…`), 2b (`31e37b12…`), clean-up.
- **Worker log, all 10 jobs:** no error and no traceback, and the api log (after run 0) has none either. Warnings on every job: `TurnDetector requires a VAD model …` (text-channel noise, as in V4-12). On each job there were also two `signal connection failed on v1 path: Timeout("transport timed out")` lines and `The room connection was not established within 10 seconds after calling job_entry`: the room join took ~12 s, and `chat_start` took 28–30 s (V4-12: 5–6 s). The MCP client's own room join logged the same v1-path timeouts and region fallbacks, and it does not run the 1.8.3 agent package, while `rtc-version` is 1.1.18 as in V4-12. So this is the network or LiveKit Cloud edge at the time, not the SDK bump. Worth a recheck if it persists.
- **Observed, as designed:** a background flow-node call's `tool_reply {scheduled}` lands ~1 ms before its `tool_call_ended` (ask #89). A call cancelled by a transition gets no `tool_reply`. The `global` node's `demo_flow_delay` is offered on every node, so the claims node looked the account up a second time in 2a and 2b; that is the instruction working, not a leak.
