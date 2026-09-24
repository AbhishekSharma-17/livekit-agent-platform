# Recipe: a plain voice assistant

Goal: the smallest possible agent, when there's no pack-specific need — a
good default when the user just says "build me a voice assistant".

## 1. Create it

`agent_create(...)`
```json
{
  "name": "Front desk assistant",
  "pack_id": "generic",
  "description": "General-purpose voice assistant"
}
```
The `generic` pack has no code tools and no custom UI: it seeds the
built-in composite panel (`status`, `notes`, `checklist`, `activity`
blocks), the cascaded LiveKit Inference pipeline, and a plain instructions
string you can immediately overwrite.

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
