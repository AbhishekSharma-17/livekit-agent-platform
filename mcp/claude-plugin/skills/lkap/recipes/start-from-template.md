# Recipe: start from a starter template

Goal: create a working agent from one of the platform's starters — a
configuration preset layered on a pack — then follow the starter's own next
steps. The fastest route from "I want a receptionist" to a first test chat.

## 1. Pick a starter

Read the `lkap://templates` resource, or look one up by id:

`lkap_describe(...)`
```json
{ "kind": "template", "id": "receptionist" }
```
The catalogue: `blank` (a plain assistant), `knowledge_assistant` (answers
from documents and cites them), `receptionist` (a booking flow with a form,
a table and two HTTP tools), `vision_assistant` (camera and screen share),
`phone_agent` (keypad menu, messages, transfer, QA), `lead_qualification`
(a qualification flow whose variables reach your CRM through a webhook),
`survey_intake` (a voice questionnaire with an on-screen form) and
`insurance_claim` (the advanced example, a full code pack). Each entry's
`requires` says what the workspace needs: `provider_keys` (none, except an
optional Google key on `insurance_claim`), `telephony` or
`webhook_endpoint`. Everything except `insurance_claim` runs on LiveKit
Inference with no vendor key.

## 2. Create the agent

`agent_create(...)`
```json
{
  "name": "Front desk",
  "template_id": "receptionist",
  "description": "Books, moves and cancels appointments"
}
```
The starter seeds the whole config (instructions, greeting, pipeline, panel
blocks, flow, voice settings) and creates its knowledge bases and HTTP tools
with the agent. `template_id` wins over `pack_id`: the pack is the
starter's. Pass `patch` to adjust the seed in the same call. What the
connection cannot run is switched off rather than failing the create: DTMF
stays off until SIP is reachable, recording until Egress and a storage
config exist.

## 3. Follow its next steps

The result's `next_steps` are the starter's own checklist. For
`receptionist` that means pointing `check_availability` and
`book_appointment` at your booking system (they call `example.com`
placeholders until you do):

`tool_update(...)`
```json
{
  "tool_id": "<the check_availability tool_id from agent_get>",
  "patch": {
    "definition": {
      "url": "https://booking.example.com/availability?service={{ service }}&date={{ date }}",
      "allowed_hosts": ["booking.example.com"]
    }
  }
}
```
Then replace the sample knowledge (`kb_add_document`), and review the flow
with `agent_flow_validate`.

## 4. Validate, test, publish

`agent_validate(...)`
```json
{ "id_or_slug": "front-desk" }
```
Then follow `test-and-publish`.

## Related concepts

`lkap_explain("agents")`, `lkap_explain("flows")`,
`lkap_explain("tools-http")`.
