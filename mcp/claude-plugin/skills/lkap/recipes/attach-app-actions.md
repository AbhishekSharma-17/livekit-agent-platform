# Recipe: give an agent a connected app's actions

Goal: let an agent use actions of an app the workspace has already connected
through Composio (see the `connect-an-app` recipe first). Needs a key with
`providers:write` and `agents:write`.

## 1. Find the connection and its actions

`apps_connections()` — note the `id` of an `active` connection.

`apps_actions(...)`
```json
{ "toolkit": "googlecalendar", "important": true }
```
Start with `read` actions. A `destructive` action (deletes, removes, refunds,
moves money) needs the user's explicit go-ahead.

## 2. Add the actions as tools and attach them

`apps_add_tools(...)`
```json
{
  "connection_id": "<connection id>",
  "actions": ["GOOGLECALENDAR_FIND_FREE_SLOTS"],
  "agent_id": "<agent id>"
}
```
Each action becomes one tool named `<app>_<action>` (e.g.
`googlecalendar_find_free_slots`) with its inputs pinned, and is attached to
the agent as a new config version; the agent's `tools.apps.mode` becomes
`actions`. Reads run while the conversation continues ("Let me look that
up"); actions that change something wait for their result. Picking the same
action again reuses its tool.

## 3. Check and try it

`agent_validate(...)`
```json
{ "id_or_slug": "<agent id>" }
```
Then `chat_start` and `chat_send` a message that needs the action. If the
app's sign-in has expired the agent says the app needs to be reconnected by
an admin — reconnect it in the console's Tools, Apps.

## Optional: an app server or a tool finder

`agent_apps_mode(...)`
```json
{ "id_or_slug": "<agent id>", "mode": "router", "allowed_toolkits": ["googlecalendar"] }
```
- `server` offers the picked actions of the allowed apps through one managed
  app server.
- `router` lets the agent search for actions and run them during the
  conversation (Composio's tool finder). Replies are slower; connecting new
  apps from a conversation stays off unless `router.manage_connections` is
  set, and never helps a phone caller.
- `off` removes the server or finder; attached action tools stay.

Saving provisions the server or finder and attaches it as a managed MCP
server (shown read-only in the agent's tools); changing the settings
replaces it, and `off` or deleting the agent removes it. `denied_actions`
lists actions it must never run.

## Related concepts

`lkap_explain("apps")`, `lkap_explain("tools-mcp")`.
