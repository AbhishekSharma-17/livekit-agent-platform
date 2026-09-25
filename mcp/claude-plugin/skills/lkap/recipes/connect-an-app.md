# Recipe: connect an app and pick actions for agents

Goal: give the workspace's agents actions in a third-party app (here a
calendar) through Composio. Needs a key with `providers:write`.

## 1. Store the Composio key (once per workspace)

`provider_key_create(...)`
```json
{
  "provider_id": "composio",
  "label": "Composio",
  "secrets": { "api_key": "env:COMPOSIO_KEY" }
}
```
The result includes a key test; `ok=false` means Composio rejected the key.

## 2. Find the app

`apps_list(...)`
```json
{ "query": "calendar" }
```
Note the app's `slug` and its `auth` list (`oauth_managed`, `api_key`, …).

## 3. Connect it

`apps_connect(...)`
```json
{ "toolkit": "googlecalendar", "method": "managed" }
```
The result's `redirect_url` is for the **user**: ask them to open it in their
browser and sign in (it expires in about ten minutes). Never open it
yourself. For an app that takes a key instead, use `"method": "api_key"` with
`"fields": { "api_key": "env:APP_KEY" }` — it connects at once.

## 4. Wait until it is active

`apps_connection_status(...)`
```json
{ "id": "<connection_id from step 3>" }
```
Repeat until `status` is `active`; `expired` or `failed` means start step 3
again.

## 5. Pick actions

`apps_actions(...)`
```json
{ "toolkit": "googlecalendar", "important": true }
```
Prefer `read` actions for a first agent; ask the user before any
`destructive` one.

`apps_add_tools(...)`
```json
{
  "connection_id": "<connection_id>",
  "actions": ["GOOGLECALENDAR_FIND_FREE_SLOTS"],
  "agent_id": "<agent id>"
}
```

## 6. Review

`apps_connections()` lists every connected app with its picked actions.

## Related concepts

`lkap_explain("apps")`, `lkap_explain("providers-and-keys")`.
