# V4-21 live check: an HTTP tool's `silent_reply` on cascaded and realtime agents

Status: **protocol only, not run.** No session ids yet. This is the coordinator's protocol for PLAN-V4 V4-21 "Live" (rulings R-V4-71 and R-V4-68). V4-21 makes the worker read `HttpToolDefinition.silent_reply`. `_assemble` passes the names of the `http` rows with `silent_reply=true` to `PlatformAgent(silent_reply_tools=…)`, and the `function_tools_executed` handler cancels the reply for a batch made only of silent tools. Realtime models always honour this. The cascaded pipeline honours it from livekit-agents 1.8.3.

The repo is public: no token, key, host, phone number or transcript beyond the quoted turns goes in this file. Session ids and agent ids are fine. A failing step becomes an ask with the log line. Never patch during the run (R-V4-20).

**Scope limit (ask #170):** the wiring reaches plain (prompt) agents only. On a **flow** agent the entry `FlowNodeAgent` is built without the names, so this check uses non-flow agents. Don't run it on a flow agent until #170 is ruled.

## Before you start

1. **Worker restart.** After the merge, the user restarts their `lkap-agent` worker. The run never restarts, signals or kills it. Record the SDK version from the startup line (`starting worker {"version": …}`). Expect **1.8.3**. On a lower version, step 1's cascaded case is expected to keep the reply and to log `silent_reply is not honoured by this cascaded pipeline` once per session. Record that and stop.
2. **Key (R-V4-17 rule 1).** Mint one Builder key named `v421-live` with `agents:read, sessions:read, agents:write, sessions:write` and `expires_at` +1 day. Keep it only in a 0600 MCP config in the scratchpad. Revoke it in step 3.
3. **Objects touched.** One new tool, `demo_silent_status`, and one new scratch agent per mode, all deleted in step 3. Capture `agent_list` and `tool_list` before step 1 and after step 3. The diff must be empty.
4. **The tool (R-V4-18: `httpbin.org` only).** Create it **through the console's HTTP tool dialog** (V4-13). This checks that the console's `silent_reply` switch reaches the worker:
   - `demo_silent_status`: GET `https://httpbin.org/get`, `allowed_hosts=["httpbin.org"]`, `timeout_s=10`, `max_result_chars=2000`, **Silent reply on**, execution left at **blocking** (the validator refuses `silent_reply` with a background or automatic mode). Description: "Record the caller's status in the notebook. Call this whenever the caller says 'update my status'. Returns nothing to say."
   - Check with `tool_get` that `silent_reply` is `true`.

For each step, record: pass/fail, the session id, the `tool_call_started` / `tool_call_ended` rows, whether a `tool_reply` row or an assistant message follows the call, and the worker log lines for the session.

## Step 1: cascaded agent

Create `Demo — Silent cascaded` from the `blank` starter (cascaded, the default LLM, not changed). Attach `demo_silent_status`. Run `chat_start`, then `chat_send("Please update my status.")`.

Pass when all of these hold:
- `demo_silent_status` runs (`tool_call_ended.status == "done"`), and its activity row / block updates in the console;
- the model does **not** narrate the result: after the call, the next assistant message is either missing or the model's own continuation. It does not read back or summarise httpbin's JSON ("Your status shows origin …", "The request returned …");
- the worker log for the session has no `silent_reply is not honoured` line (1.8.3), and has the debug line `cancelled tool reply` with `tools=["demo_silent_status"]` if debug logging is on.

Then `chat_send("Thanks. What can you help me with?")`. The model answers normally (the tool didn't mute the session).

## Step 2: realtime agent

Repeat step 1 on `Demo — Silent realtime`: the `blank` starter switched to `pipeline.mode = "realtime"` with the workspace's realtime model. Use a voice call or the text channel, whichever the workspace's realtime model supports. The pass criteria are the same. Note for Gemini Live: the server generates tool replies itself (`auto_tool_reply_generation`) and honours `reply_required=False` only with `SILENT` scheduling, i.e. `tool_behavior=NON_BLOCKING`, not on Vertex (ask #150). If the realtime model still narrates, record the model id and the plugin's "Gemini will answer it anyway" log line. That is a known plugin limit, not a V4-21 failure.

## Step 3: clean-up

Delete both scratch agents and `demo_silent_status`, revoke `v421-live`, and capture `agent_list` / `tool_list` again. The diff against the "before" capture must be empty.

## Run log

Not run.
