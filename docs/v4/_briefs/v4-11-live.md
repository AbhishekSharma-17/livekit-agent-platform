# V4-11 live check: flow knowledge scope and lead-flow routing (R-V4-29, R-V4-30)

Status: **open.** The code half (agent, api, MCP doc, console) is merged; nothing below has been run yet. The coordinator runs it on the user's dev stack after the merge, under R-V4-17's rules, and fills in the result lines.

The repo is public: no token, key, host, phone number or transcript beyond the quoted turns goes in this file. Session ids and agent ids are fine.

## Before you start

1. **The worker runs the new code.** The fix lives in the worker (`agent/src/lkap_agent/flow/{runtime,edges}.py`), so the user restarts their `lkap-agent` worker once the merge is in their checkout. The run never restarts, signals or kills it. Record the version the worker reports (its startup log line, or `connection_fleet` status) here:
   - Worker version / commit: `…`
2. **The api runs the new template.** The lead starter's edge move is api data (`templates/catalog/lead_qualification/template.json`); the api reloads the catalogue on restart. Step 3 does not depend on it (it writes the edges itself), but a fresh `agent_create(template_id="lead_qualification")` does.
3. **Key (R-V4-17 rule 1).** Mint one Builder key for this run (`agents:read, sessions:read, connections:read, providers:read, audit:read, agents:write, sessions:write`, `expires_at` +1 day), named `v411-live`, held only in a 0600 MCP config in the scratchpad. Revoke it at the end (step 7). No `calls:write`, no Operator scopes.
4. **Objects touched.** Only `Demo — Receptionist`, `Demo — Lead qualification` and one new `Demo — Router scratch` agent (deleted in step 7). Capture `agent_list` (id, slug, config_version, updated_at) before step 1 and after step 7; the diff outside these three must be empty.
5. **Model.** The demo agents' default LLM, `google/gemma-4-31b-it` on LiveKit Inference. Don't change it: the point is that the fix works on this model.

## Step 1: revert V4-06's knowledge workaround (R-V4-29)

V4-06 set each demo flow's **global node `kb_ids`** to the agent's knowledge bases, to get around B-2 (`v4-06-populate.md` §"Flow KB workaround"). Undo it on both agents so the fallback, not the patch, is what gets tested:

- `agent_get("Demo — Receptionist")` → in `config.flow`, set the `global` node's `kb_ids` to `[]`. Change nothing else. Check that no `agent` node lists `kb_ids` either (if one does, the flow is still narrowed and the check proves nothing — stop and record it). `agent_flow_validate`, then `agent_update(patch={"flow": …})`, then `agent_validate`: expect **no** "attached but no step of the flow can search it" warning.
- The same for `Demo — Lead qualification`.

Result:
- Receptionist config_version before → after: `…`; validate warnings: `…`
- Lead qualification config_version before → after: `…`; validate warnings: `…`

## Step 2: knowledge reaches both flows again (R-V4-29)

For each agent: `chat_start`, one `chat_send`, then `chat_end`.

| Agent | Turn | Pass when |
|---|---|---|
| Demo — Receptionist | "What are your opening hours?" | the reply states hours from the seeded Practice info KB, and the session's `kb_citations` block is non-empty |
| Demo — Lead qualification | "How much does it cost?" | the reply states a price band from the Offer sheet KB, and `kb_citations` is non-empty |

Result (session id, pass/fail, the reply trimmed to about two sentences, `kb_citations` count, cost):
- Receptionist: `…`
- Lead qualification: `…`

## Step 3: the lead flow starts at `company` (R-V4-30 fix 3)

`Demo — Lead qualification`'s stored flow still has the old two-edge start (`start_company` "the caller agrees", `start_nurture` "the caller declines to answer questions"). Bring it in line with the template:

- Replace the flow's **edges** with the new template's (`start_company` "the caller agrees or starts answering"; `company_nurture` `company → nurture` "the caller declines to answer questions or is only researching", declared right after `company_needs`; `start_nurture` removed). Keep V4-06's node edits (the global node's extra instructions and its `demo_*` tools): the ruling says "replace the stored flow with the template's", and swapping the edges alone gives the same graph without losing those edits. If the coordinator prefers the literal reading, replace the whole `flow` with the template's and note it here.
- `agent_flow_validate`, `agent_update`, `agent_validate`.

Then `chat_start`:
1. `chat_send("Sounds good.")` → pass when the session `path` includes `company` and the reply asks for the company name or role (not a general answer from `start`).
2. `chat_send("The company is Contoso and I'm the IT manager.")` → pass when `needs` is entered and, after the extraction settles (a few seconds, or at `chat_end`), `variables.company == "Contoso"`.
3. `chat_end`; record the `flow_ended` payload (`path`, `variables`, `disposition`, `completed`).

Result:
- Session id: `…`
- Turn 1 path / reply: `…`
- Turn 2 path / reply: `…`
- `flow_ended`: `…`
- Cost: `…`

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
- Agent id: `…`
- Session id, path after turn 1, reply: `…`
- `flow_ended`: `…`
- Cost: `…`

If this fails on `google/gemma-4-31b-it` while steps 3 and 5 pass, file the deferred ask from R-V4-30 in `docs/v4/_asks.md`: a `StartNode.instructions` field so an author can write the router's own prompt.

## Step 5: the receptionist still books (regression)

`Demo — Receptionist`: `chat_start`, then a booking request in two or three turns (for example "I'd like to book a cleaning." then a name and a day). Pass when the path goes `identify → collect_booking`.

Result:
- Session id, path: `…`
- `flow_ended`: `…`
- Cost: `…`

## Step 6: what the console shows

In the user's console, open each demo agent's Flow section (read only; don't save):
- every step card shows **KB: all** (the flow lists none after step 1);
- a step's inspector shows "Inherits all N knowledge bases of this agent. …" under Knowledge bases.

Result: `…`

## Step 7: clean up

1. `lkap_delete(kind="agent", id=<Demo — Router scratch>)` with the user's `confirm=true`; `agent_list` no longer shows it.
2. Revoke the `v411-live` key (`DELETE /v1/api-keys/{id}`; `GET /v1/api-keys` shows it revoked) and delete the scratchpad MCP config.
3. `agent_list` again: the diff against the "before" capture is only the two demo agents' `config_version`/`updated_at` (steps 1 and 3) and the deleted scratch agent.

Result: `…`

## Summary

| Check | Ruling | Session | Pass |
|---|---|---|---|
| Receptionist KB facts with the patch reverted | R-V4-29 | `…` | `…` |
| Lead KB facts with the patch reverted | R-V4-29 | `…` | `…` |
| Lead flow: "Sounds good." reaches `company`; `company == "Contoso"` | R-V4-30 (3) | `…` | `…` |
| Two-edge scratch router reaches `claims` on turn 1 | R-V4-30 (1, 2) | `…` | `…` |
| Receptionist `identify → collect_booking` | regression | `…` | `…` |
| Total cost | | | `…` |
