# V4-14 live check: flow-node background tools on livekit-agents 1.8.3

Status: **not run.** This is the coordinator's protocol for PLAN-V4 V4-14 "Live" (rulings R-V4-39 and R-V4-30 (ii), decision D-V4-37, `BACKGROUND-TOOLS.md` §1.9). **It is also blocked.** The pin bump has not landed, because the asks from V4-14 (`docs/v4/_asks.md` #95 … #98) are still open. The run needs a worker on 1.8.3, and until the bump lands a flow-node tool is still downgraded to `blocking`, which would make every step here pass or fail for the wrong reason.

The repo is public: no token, key, host, phone number or transcript beyond the quoted turns goes in this file. Session ids and agent ids are fine. A failing step becomes an ask with the log line. Never patch during the run (R-V4-20).

## Before you start

1. **The worker runs 1.8.3.** After the merge, run `uv sync` in the main checkout's `agent/`. Then the user restarts their `lkap-agent` worker. The run never restarts, signals or kills it. Record what the worker reports, from its startup line or `connection_fleet` status. Expect SDK **1.8.3**. On 1.8.3 the startup log also has a new warning, "agent_name is set in code; move it to livekit.toml …" (upstream #7295). That warning is expected and not a failure.
   - Worker commit / SDK: …
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

Result: …

**1b: a node that waits for the result speaks it.** In a new session, repeat the first turn only. Then wait 8 s without sending anything, then `chat_send("What did you find?")` if the result has not already been spoken.

Pass when: `tool_call_ended.status == "done"`, a `tool_reply` row reads `completed`, and an assistant message on `identify` carries the result (httpbin's echo, so any mention of the lookup finishing counts). `path` stays `[start, identify]` until the caller confirms.

Result: …

## Step 2: the two-edge scratch router, the #7321 case

This check is why the package exists. On 1.8.2, when an edge (`go_to_*`) tool and an ordinary tool come in the same batch and the ordinary one finishes **after** the edge tool, `new_agent_task` is overwritten with `None` and the handoff is silently dropped. 1.8.3 guards the assignment with `is not None` (upstream #7321, in both the pipeline and realtime paths).

Build the router exactly like V4-11's step 4 (`v4-11-live.md`). Run `agent_create(name="Demo — Router scratch", pack_id="generic", description="Demo agent created by V4-14 on <date> through lkap-mcp; deleted at the end of the run.")` with the same LLM. Give it the flow `start → claims | billing` (edges `start_claims` "the caller wants to file or ask about a claim", `start_billing` "the caller asks about a bill or a payment"). Also add a `global` node with `tools: ["demo_flow_delay"]` and the instructions "Whenever the caller mentions a claim, also call demo_flow_delay to look up their account status." A `StartNode` has no `tools` field, so a routing start gets its tools from the global node. Attach `demo_flow_delay`. `agent_flow_validate` should report 0 errors.

**2a: the tool runs `blocking`. This is the definitive #7321 case.** Set the tool's `execution.mode` to `blocking` (`tool_update`). A blocking sibling is exactly what drops the handoff on 1.8.2, because `/delay/3` finishes long after `go_to_claims`. Run `chat_start`, then `chat_send("I'd like to file a claim.")`.

Pass when:
- `go_to_claims` **and** `demo_flow_delay` were called in the **same** batch. Check that both `tool_call_started` rows fall in the same turn, i.e. the same generation or speech id. If the model called only one of them, the case was not exercised: record it as **inconclusive** and retry up to twice;
- `demo_flow_delay` ended **after** the edge tool (compare the `tool_call_ended` timestamps);
- the session `path` is `["start", "claims"]` after this first turn, and the reply is the claims node's question. The handoff was **not** dropped.

Result: …

**2b: the tool runs `background`.** Set `execution.mode` back to `background` and repeat 2a in a new session. The background dispatch returns at its first update, so it normally finishes before the edge tool. This run proves the lifted downgrade does not reintroduce the drop.

Pass when: same batch (or inconclusive, as above); `path == ["start", "claims"]` after turn 1. The router's background call is cancelled by the transition (`cancelled` in the feed), or it finishes first. Either is acceptable, and the brief records which one happened.

Result: …

## Step 3: flow_ended payloads

Run `chat_end` on every session above and copy each `flow_ended` payload here (`completed`, `current_node`, `path`, `variables`, `disposition`, `reason`).

- 1a: …
- 1b: …
- 2a: …
- 2b: …

## Step 4: clean up

1. `Demo — Receptionist`: remove `demo_flow_delay` from `identify.tools` and detach it (`agent_attach(tool_ids=[…], remove=true)`). The stored flow must match its pre-step-1 flow exactly, except that `config_version` moves forward.
2. `lkap_delete(kind="agent", id=<Demo — Router scratch>)` with the user's `confirm=true`. Use the delete ladder if sessions exist (`agent_archive(confirm)`, then `lkap_delete(confirm, purge)`), as V4-11 did.
3. `lkap_delete(kind="tool", id=<demo_flow_delay>)` with `confirm=true`.
4. Revoke the `v414-live` key and delete the scratchpad MCP config.
5. Run `agent_list` / `tool_list` again. Compared with the "before" capture, the only differences allowed are the receptionist's `config_version`/`updated_at` and the created-then-deleted objects.

Result: …

## Summary

| Check | Ruling | Session | Pass |
|---|---|---|---|
| Worker on 1.8.3, no downgrade `info` event | R-V4-39 | | |
| 1a: acknowledgement spoken, `identify → collect_booking` while the tool runs, tool `cancelled` | D-V4-37 | | |
| 1b: a waiting node speaks the result | D-V4-37 | | |
| 2a: blocking sibling finishes after `go_to_claims`, handoff kept | #7321, R-V4-30 (ii) | | |
| 2b: background sibling on the router, handoff kept | R-V4-39 | | |
| Clean-up diff empty outside the manifest | R-V4-17 | | |
