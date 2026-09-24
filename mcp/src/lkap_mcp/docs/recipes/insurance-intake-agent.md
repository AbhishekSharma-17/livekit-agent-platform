# Recipe: build an insurance intake agent

Goal: a first-notice-of-loss voice agent from the `insurance_claim` pack,
with a knowledge base and an HTTP tool, tested and published. Do
`connect-livekit` first if there is no connection yet.

## 1. Create the agent from the pack

`agent_create(...)`
```json
{
  "name": "FNOL intake",
  "pack_id": "insurance_claim",
  "description": "First notice of loss intake for home claims"
}
```
This seeds `config` from `PackManifest`: the cascaded LiveKit Inference
pipeline (works with no vendor key), the pack's default greeting and
instructions, and the two knowledge bases from its `kb_seeds`
("Insurance policy lines", "Intake playbook"), created, attached and filled
from the pack's `policy_lines.md` and `intake_playbook.md`. Their documents
ingest in the background; check `kb_get` for `ready` before relying on them.

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
