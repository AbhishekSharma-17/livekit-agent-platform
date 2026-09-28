# Recipe: build an insurance intake agent

Goal: a first-notice-of-loss voice agent from the `claims_intake` starter
(the generic pack, blocks and tool kits), with a knowledge base and an HTTP
tool, tested and published. Do
`connect-livekit` first if there is no connection yet.

## 1. Create the agent from the starter

`agent_create(...)`
```json
{
  "name": "FNOL intake",
  "template_id": "claims_intake",
  "description": "First notice of loss intake for home claims"
}
```
The `claims_intake` starter needs no code pack. It seeds the cascaded
LiveKit Inference pipeline (works with no vendor key; the model can see the
camera), a greeting that asks about safety first, the Notebook panel (a claim
notebook, a drawing board, pictures), live extraction of the claim into the
notebook's summary, rules that list the documents each kind of claim needs,
never confirm coverage and hand safety concerns to a person, and three
test conversations (`agent_tests_run`). It also adds three tool kits: the
policy lookup (`policy_lookup` on the `Demo — Policy directory` lookup table,
created once per workspace), the details intake and the hand-over; and two
knowledge bases ("Claims intake · Policy lines", "Claims intake · Intake
playbook"). Check `kb_get` for `ready` before relying on them. The sketch
needs a Google key (`google-image-gen`); it is optional — without one the
`image_gen` slot is left empty and everything else works. Add it later with
`provider_key_create`.

The older `insurance_claim` code pack is kept for agents made from it; it
only appears when the deployment lists it in `LKAP_PACKS`.

## 2. Add knowledge

`kb_create(...)`
```json
{ "name": "Regional flood rider notes" }
```
`kb_add_document(...)`
```json
{
  "kb_id": "<the kb_id just created>",
  "text": "Flood damage is covered under the optional flood rider (policy suffix -FR) up to $50,000 per occurrence. Standard HO-4 and HO-6 policies exclude flood damage entirely.",
  "filename": "flood-rider.md",
  "wait": true
}
```

## 3. Add an HTTP tool

`tool_create_http(...)`
```json
{
  "name": "lookup_weather",
  "description": "Look up recent weather for a city, to corroborate a storm or flood claim",
  "parameters": {
    "type": "object",
    "properties": { "city": { "type": "string" } },
    "required": ["city"]
  },
  "url": "https://api.example.com/weather?city={{ city }}",
  "method": "GET",
  "allowed_hosts": ["api.example.com"],
  "dry_run_args": { "city": "Austin" }
}
```
Check the returned `ToolDryRunResult` before moving on — a non-2xx status or
an empty body usually means the `url` or `allowed_hosts` needs a fix.

## 4. Attach and validate

`agent_attach(...)`
```json
{
  "id_or_slug": "fnol-intake",
  "kb_ids": ["<flood rider kb_id>"],
  "tool_ids": ["<lookup_weather tool_id>"]
}
```
`agent_validate(...)`
```json
{ "id_or_slug": "fnol-intake" }
```
Fix any `issues` before continuing — most commonly a missing pipeline slot
or an unknown tool/kb id from a typo.

## 5. Test it

Follow `test-and-publish` next: `chat_start`, a couple of `chat_send` turns
describing a claim, then `chat_end`.

## 6. Publish

`agent_publish(...)`
```json
{ "id_or_slug": "fnol-intake", "published": true }
```
The response includes the session url to share.

## Related concepts

`lkap_explain("agents")`, `lkap_explain("knowledge")`,
`lkap_explain("tools-http")`.
