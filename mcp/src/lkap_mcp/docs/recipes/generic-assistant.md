# Recipe: a plain voice assistant

Goal: the smallest possible agent, when there's no pack-specific need — a
good default when the user just says "build me a voice assistant".

## 1. Create it

`agent_create(...)`
```json
{
  "name": "Front desk assistant",
  "template_id": "blank",
  "description": "General-purpose voice assistant"
}
```
The `blank` starter is exactly what the `generic` pack seeds: no code tools
and no custom UI, the built-in composite panel (`status`, `notes`,
`checklist`, `activity` blocks), the cascaded LiveKit Inference pipeline,
and a plain instructions string you can immediately overwrite. For a
starter with more built in (knowledge, a booking flow, a phone menu), see
`start-from-template`.

## 2. Adjust instructions and greeting

`agent_update(...)`
```json
{
  "id_or_slug": "front-desk-assistant",
  "patch": {
    "instructions": "You are the front desk assistant for Example Co. Answer questions about office hours and directions; offer to take a message for anything else.",
    "voice": { "greeting": "Hi, you've reached Example Co. How can I help?" }
  }
}
```

## 3. Validate, test, publish

`agent_validate(...)`
```json
{ "id_or_slug": "front-desk-assistant" }
```
Then follow `test-and-publish`.

## Related concepts

`lkap_explain("agents")`, `lkap_explain("pipeline-modes")`.
