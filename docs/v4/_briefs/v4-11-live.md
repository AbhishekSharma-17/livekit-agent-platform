# V4-11 live check: flow knowledge scope and lead-flow routing (R-V4-29, R-V4-30)

Status: **done — all five checks pass (2026-09-25, second run, 04:11–04:19 IST).** The first run (03:55–04:05) was blocked by B-12: every job crashed at start. The coordinator fixed that in `3d9b8c0` and restarted the worker, and the whole protocol was then run as written. The run log for both runs is at the end.

The repo is public: no token, key, host, phone number or transcript beyond the quoted turns goes in this file. Session ids and agent ids are fine.

## Before you start

1. **The worker runs the new code.** The fix lives in the worker (`agent/src/lkap_agent/flow/{runtime,edges}.py`), so the user restarts their `lkap-agent` worker once the merge is in their checkout. The run never restarts, signals or kills it. Record the version the worker reports (its startup log line, or `connection_fleet` status) here:
   - Worker version / commit: HEAD `3d9b8c0` ("register a plain shutdown function", 04:10:13 IST), which sits on `19a907d` (Merge V4-11) and `32750e6`. The worker is PID 30659, registered as `lkap-agent` at 04:10:29 IST: SDK 1.8.2, image `slim`, 33 installed providers. It logs no commit line, so the commit is inferred from the start time. `me` reports `workers.ready=1` (the B-7 fix)
2. **The api runs the new template.** The lead starter's edge move is api data (`templates/catalog/lead_qualification/template.json`); the api reloads the catalogue on restart. Step 3 does not depend on it (it writes the edges itself), but a fresh `agent_create(template_id="lead_qualification")` does.
3. **Key (R-V4-17 rule 1).** Mint one Builder key for this run (`agents:read, sessions:read, connections:read, providers:read, audit:read, agents:write, sessions:write`, `expires_at` +1 day), named `v411-live`, held only in a 0600 MCP config in the scratchpad. Revoke it at the end (step 7). No `calls:write`, no Operator scopes.
4. **Objects touched.** Only `Demo — Receptionist`, `Demo — Lead qualification` and one new `Demo — Router scratch` agent (deleted in step 7). Capture `agent_list` (id, slug, config_version, updated_at) before step 1 and after step 7; the diff outside these three must be empty.
5. **Model.** The demo agents' default LLM, `google/gemma-4-31b-it` on LiveKit Inference. Don't change it: the point is that the fix works on this model.

## Step 1: revert V4-06's knowledge workaround (R-V4-29)

V4-06 set each demo flow's **global node `kb_ids`** to the agent's knowledge bases, to get around B-2 (`v4-06-populate.md` §"Flow KB workaround"). Undo it on both agents so the fallback, not the patch, is what gets tested:

- `agent_get("Demo — Receptionist")` → in `config.flow`, set the `global` node's `kb_ids` to `[]`. Change nothing else. Check that no `agent` node lists `kb_ids` either (if one does, the flow is still narrowed and the check proves nothing — stop and record it). `agent_flow_validate`, then `agent_update(patch={"flow": …})`, then `agent_validate`: expect **no** "attached but no step of the flow can search it" warning.
- The same for `Demo — Lead qualification`.

Result:
- Receptionist config_version before → after: `6 → 7`. Only the global node's `kb_ids` changed (checked by diffing the stored flow before and after); every `agent` node already had `kb_ids: []`. `agent_flow_validate`: 0 errors, 0 warnings. `agent_validate`: 0 errors. Its one warning, `knowledge.auto_inject`, predates this change, and the "attached but no step of the flow can search it" warning is **absent**
- Lead qualification config_version before → after: `5 → 6` (global `kb_ids` only). `agent_flow_validate`: 0 errors. `agent_validate`: 0 errors, with the warnings `knowledge.auto_inject` and `qa.enabled`. The "no step can search it" warning is **absent**

## Step 2: knowledge reaches both flows again (R-V4-29)

For each agent: `chat_start`, one `chat_send`, then `chat_end`.

| Agent | Turn | Pass when |
|---|---|---|
| Demo — Receptionist | "What are your opening hours?" | the reply states hours from the seeded Practice info KB, and the session's `kb_citations` block is non-empty |
| Demo — Lead qualification | "How much does it cost?" | the reply states a price band from the Offer sheet KB, and `kb_citations` is non-empty |

Result (session id, pass/fail, the reply trimmed to about two sentences, `kb_citations` count, cost):
- Receptionist: `7e3c6c3d15654f6f93e00b4d41c8de03`, **pass**. Reply: "We're open Monday to Thursday from 8:00 am to 6:00 pm, Friday from 8:00 am to 3:00 pm, and Saturday from 9:00 am to 12:00 pm for check-ups and cleanings." These are the exact hours in the seeded `practice_info.md`: `POST /v1/knowledge-bases/93ec3804…/search` returns them at score 0.62. The agent's panel has **no `kb_citations` block**, so the citations count doesn't apply here (the caveat from the first run). The knowledge evidence is the worker's `injected knowledge hits=4 top_k=4` line for this session, now logged at info. Path `[start, identify]`. LiveKit Inference cost: $0.0011
- Lead qualification: `8a65708d8bb740f090adb330d6fd4160`, **pass**. Reply: "Our Starter plan begins at $15 per user per month, the Team plan starts at $25, and we offer custom pricing for Enterprise needs." That is the Offer sheet's price bands. `kb_citations`: **1** `block_update {block_id: offer, block_type: kb_citations, op: cite}`, and the worker logged `injected knowledge hits=2`. Path `[start]`: expected, since the flow does not move on a pricing question. Cost: $0.0010

## Step 3: the lead flow starts at `company` (R-V4-30 fix 3)

`Demo — Lead qualification`'s stored flow still has the old two-edge start (`start_company` "the caller agrees", `start_nurture` "the caller declines to answer questions"). Bring it in line with the template:

- Replace the flow's **edges** with the new template's (`start_company` "the caller agrees or starts answering"; `company_nurture` `company → nurture` "the caller declines to answer questions or is only researching", declared right after `company_needs`; `start_nurture` removed). Keep V4-06's node edits (the global node's extra instructions and its `demo_*` tools): the ruling says "replace the stored flow with the template's", and swapping the edges alone gives the same graph without losing those edits. If the coordinator prefers the literal reading, replace the whole `flow` with the template's and note it here.
- `agent_flow_validate`, `agent_update`, `agent_validate`.

Then `chat_start`:
1. `chat_send("Sounds good.")` → pass when the session `path` includes `company` and the reply asks for the company name or role (not a general answer from `start`).
2. `chat_send("The company is Contoso and I'm the IT manager.")` → pass when `needs` is entered and, after the extraction settles (a few seconds, or at `chat_end`), `variables.company == "Contoso"`.
3. `chat_end`; record the `flow_ended` payload (`path`, `variables`, `disposition`, `completed`).

Result:
- Session id: `0f3097fff9784969abb87cf5783e71b5`. Flow change: `edges` swapped for the template's 7 (`start_nurture` removed, `start_company` "the caller agrees or starts answering", `company_nurture` declared right after `company_needs`). The nodes, including V4-06's global instructions and `demo_*` tools, are unchanged. config_version `6 → 7`. Validate: 0 errors
- Turn 1 path / reply: `[start, company]`, **pass**. The `handoff start→company` (`edge_id=start_company`, `reason=start`) fired on "Sounds good.". Reply: "To get us started, could you tell me your company name and your role there?"
- Turn 2 path / reply: `[start, company, needs]`, **pass**. The model called `go_to_needs` (`edge_id=company_needs`, `reason=edge`). Reply: "Got it, thanks. What specifically are you looking to solve or improve with your energy monitoring and analytics?" Worker: `flow variables captured names=['company', 'role'] node=company`
- `flow_ended`: `{completed: false, current_node: "needs", path: ["start", "company", "needs"], variables: {company: "Contoso", role: "IT manager"}, disposition: null, reason: "participant left: CLIENT_INITIATED"}`. **`company == "Contoso"`: pass.** The session also shows 2 `kb_citations` cites
- Cost: $0.0018

## Step 4: a real routing start still routes (R-V4-30 fixes 1 and 2)

This proves the prompt and tool wording carry a router on their own, with no template help.

- `agent_create(name="Demo — Router scratch", pack_id="generic", description="Demo agent created by V4-11 on <date> through lkap-mcp; deleted at the end of the run.")`, same LLM as the demo agents.
- `agent_update(patch={"flow": …})` with this flow (validate first):

```json
{
  "nodes": [
    {"id": "start", "kind": "start", "greeting": "Hi, you've reached Acme Insurance. How can I help?"},
    {"id": "claims", "kind": "agent", "label": "Claims", "instructions": "Help the caller file a claim: ask what happened and when."},
    {"id": "billing", "kind": "agent", "label": "Billing", "instructions": "Help the caller with a bill: ask for the invoice number."}
  ],
  "edges": [
    {"id": "start_claims", "source": "start", "target": "claims", "condition": "the caller wants to file or ask about a claim"},
    {"id": "start_billing", "source": "start", "target": "billing", "condition": "the caller asks about a bill or a payment"}
  ]
}
```

- `chat_start`, `chat_send("I'd like to file a claim.")` → pass when the session `path` is `["start", "claims"]` after this **first** turn, and the reply asks what happened (the claims node's question), not a router answer.
- `chat_end`; record `flow_ended`.

Result:
- Agent id: `49b8eb3a8f264ff89d9bfe88f9ad068d` (`demo-router-scratch`, pack `generic`, unpublished). Its default LLM was already `google/gemma-4-31b-it`, so no change was needed. Flow validate: 0 errors, 0 warnings. `agent_validate`: 0 errors, 0 warnings
- Session id, path after turn 1, reply: `6188049913754b4c9b44025360520de6`, **`[start, claims]` after the first turn: pass**. The model called `go_to_claims` (`edge_id=start_claims`, `reason=edge`); unlike the single-edge `reason=start` hop in step 3, this is a real routing decision. Reply: "I can certainly help you with that. Could you tell me what happened and when it occurred?" That is the claims node's question
- `flow_ended`: `{completed: false, current_node: "claims", path: ["start", "claims"], variables: {}, disposition: null, reason: "participant left: CLIENT_INITIATED"}`. The deferred `StartNode.instructions` ask is **not** needed
- Cost: $0.0004

If this fails on `google/gemma-4-31b-it` while steps 3 and 5 pass, file the deferred ask from R-V4-30 in `docs/v4/_asks.md`: a `StartNode.instructions` field so an author can write the router's own prompt.

## Step 5: the receptionist still books (regression)

`Demo — Receptionist`: `chat_start`, then a booking request in two or three turns (for example "I'd like to book a cleaning." then a name and a day). Pass when the path goes `identify → collect_booking`.

Result:
- Session id, path: `771e573e3de048f3a4ce240e074abb40`: `start → identify → collect_booking → book → done`, **pass**. The turns were "I'd like to book a cleaning.", then "I'm Dana Lee, my number is 512-555-0100.", then "Yes, that's right. Next Tuesday at 9am please." Tools called: `go_to_collect_booking`, `request_form`, `go_to_book`, `current_time`, `check_availability`, `book_appointment`, `table_append`, `go_to_done`. `request_form` returned at once on the text channel with "No form was shown: this is a text chat…"; that is the B-5 fix working, with no timeout this time. The final reply was "You're booked. We'll text a reminder the day before. Goodbye!"
- `flow_ended`: `{completed: true, current_node: "done", path: ["start", "identify", "collect_booking", "book", "done"], variables: {caller_name: "Dana Lee", phone: "512-555-0100", service: "cleaning", preferred_time: "Next Tuesday at 9am"}, disposition: "booked", reason: "flow reached end node done", webhook_event: true}`. The summary was posted 1.5 s after the end node, so the B-8 fix works too
- Cost: $0.0065

## Step 6: what the console shows

In the user's console, open each demo agent's Flow section (read only; don't save):
- every step card shows **KB: all** (the flow lists none after step 1);
- a step's inspector shows "Inherits all N knowledge bases of this agent. …" under Knowledge bases.

Result: **pass** (headless Playwright on `:3000`, read only: nothing was clicked except a step card, and nothing was saved). Receptionist: 4 step cards show **KB: all**. Lead qualification: 5 step cards show **KB: all**. The inspector of Receptionist `identify` and of Lead `company` reads "Inherits all 2 knowledge bases of this agent. Pick some here or on the Global node to narrow." Screenshots: `<scratchpad>/v411/out/s6-{recep,lead}-{flow,inspector}.png`. Both agents stayed at config_version 7

## Step 7: clean up

1. `lkap_delete(kind="agent", id=<Demo — Router scratch>)` with the user's `confirm=true`; `agent_list` no longer shows it.
2. Revoke the `v411-live` key (`DELETE /v1/api-keys/{id}`; `GET /v1/api-keys` shows it revoked) and delete the scratchpad MCP config.
3. `agent_list` again: the diff against the "before" capture is only the two demo agents' `config_version`/`updated_at` (steps 1 and 3) and the deleted scratch agent.

Result: **done.** (1) The scratch agent went through the delete ladder: `lkap_delete` → `needs_confirmation`, then `confirm` → 409 "agent still has sessions", then `agent_archive(confirm)` → ok, then `lkap_delete(confirm, purge)` → `deleted: true`. `agent_list` shows it neither active nor archived. (2) The `v411-live-2` key (`47ce4e75…`, prefix `lkap_t2O`; the first run's `v411-live` was already revoked) was revoked: `DELETE` → 204, `revoked_at` 22:48:18Z. Its 0600 MCP config was deleted. (3) The before/after diff (`snap/run2-before.json` vs `snap/run2-after.json`) shows only Receptionist `config_version` 6→7 and Lead 5→7 with their `updated_at`, plus the new revoked key. The scratch agent was created and purged between the two snapshots. KBs, tools, provider keys, webhooks, connections and telephony are identical

## Summary

| Check | Ruling | Session | Pass |
|---|---|---|---|
| Receptionist KB facts with the patch reverted | R-V4-29 | `7e3c6c3d…` | **pass** (the injected-knowledge log line; the agent has no citations block) |
| Lead KB facts with the patch reverted | R-V4-29 | `8a65708d…` | **pass** (1 `kb_citations` cite) |
| Lead flow: "Sounds good." reaches `company`; `company == "Contoso"` | R-V4-30 (3) | `0f3097ff…` | **pass** |
| Two-edge scratch router reaches `claims` on turn 1 | R-V4-30 (1, 2) | `61880499…` | **pass** |
| Receptionist `identify → collect_booking` | regression | `771e573e…` | **pass** |
| Total cost | | | $0 of Claude (driven over MCP directly, with no LLM driver). LiveKit Inference for the 5 check chats: about $0.011, plus $0.0032 for the smoke chat |

## Run log, first run (2026-09-25, 03:55–04:05 IST): blocked by B-12

- **Driver:** Opus 5.5. It used no LLM driver: the plan was to call `lkap-mcp` directly over stdio with the Builder key, so the flow edits and chat turns would be exact. The work is in `<scratchpad>/v411/` (`mcpc.py`, `snap/`, `logs/`).
- **Pre-flight findings (they shape the re-run):**
  - **Draft vs. published.** `published` is a flag on the live config, and the api keeps no published snapshot. `text-sessions` resolves the current config, so a re-run doesn't need to publish after steps 1 and 3.
  - **Fallback trigger.** `FlowRuntime._flow_scopes_kbs = any(n.kb_ids for global/agent nodes)`. Both flows' agent nodes hold `kb_ids: []`, so clearing the global node's list is enough to turn on the fallback.
  - **`kb_citations` on the Receptionist.** Its panel has no `kb_citations` block, so step 2's receptionist line needs a different signal (see step 2's result line).
- **What happened.** Before any write, check 1 of the recheck (`v4-live-recheck.md`) opened a web call. The worker crashed the job before it connected. The user's own two console tests of Demo — Vision assistant, just before, crashed the same way. So did every job the worker accepted after its 03:52 restart: 3 of 3.

  ```text
  File "agent/src/lkap_agent/main.py", line 715, in run_session
      ctx.add_shutdown_callback(on_shutdown)
  File ".../livekit/agents/job.py", line 632, in add_shutdown_callback
      if callback.__code__.co_argcount >= min_args_num:
  AttributeError: '_OnceShutdown' object has no attribute '__code__'. Did you mean: '__call__'?
  ```

  The line runs for every channel (text, test, web, SIP), so `chat_start` would crash the same way. The driver therefore made **no config writes**. The reverts in steps 1 and 3 only make sense as the setup for the chats in steps 2–5; applied now, they would leave the user's demos changed with nothing proven.
- **Objects:** the before and after snapshots (`snap/v411-before.json`, `snap/v411-after.json`) are identical for agents, KBs, tools, webhooks and connections. The only differences are the revoked `v411-live` key, a provider key the user added from the console during the run, and the number's `lk_synced_at` from check 3. Details are in `v4-live-recheck.md` §4.
- **Re-run:** done; see the second run below.

## Run log, second run (2026-09-25, 04:11–04:19 IST): every check passes

- **Setup.** The coordinator fixed B-12 in `3d9b8c0` and restarted the worker (PID 30659, 04:10:29 IST). A new Builder key, `v411-live-2` (`47ce4e75…`, prefix `lkap_t2O`, the 7 Builder scopes, 1-day expiry), was minted into a 0600 MCP config. The driver called `lkap-mcp` over stdio (`<scratchpad>/v411/mcpc.py`; the scripts are in `scripts/`, the call logs in `logs/`, the sanitised worker log in `logs/worker-run2.log`).
- **Smoke first.** A text chat on Demo — Blank agent (`3b381f59…`) ran end to end: greeting, one turn, `chat_end`, `status=ended`. `me.health.workers.ready=1`. An earlier chat on the same agent (`ff5f6c38…`) was closed by the driver's MCP process exiting after a script error of its own (a wrong result path); that session **ended** normally.
  - Quality note, not a bug: the reply to "What are your opening hours on Sunday?" said it didn't have Sunday hours. `search_knowledge` returned `membership-plans.md` chunks for "opening hours Sunday", and V4-06 had answered this correctly from the House guide. The plumbing works; the retrieval ranking or the model's use of it varies.
- **Steps 1–7 ran as written**; the results are in the sections above.
- **Order:** smoke, then step 1 (both reverts), step 2, the step 3 edge swap and chat, step 4 create, flow and chat, step 5, step 6 in the browser, and step 7.
- **Observations, none of them bugs:**
  - A **single-edge start** advances on the first user turn with `reason=start` (receptionist `start→identify` even on a knowledge question; lead `start→company`). Step 3 therefore passes by the template's design. The routing proof is step 4, where the model chose `go_to_claims` (`reason=edge`).
  - The Lead flow still warns `flow node references a tool this session does not have … tool=demo_company_summary`: the tool is disabled (V4-06 B-4) but still listed on the global node.
  - In step 5 the model called `table_append(block_id="Bookings")`. The worker matched it to block `bookings` (`block_update … op: patch`) and logged `maximum number of function calls steps reached` once, after the end-node transition.
