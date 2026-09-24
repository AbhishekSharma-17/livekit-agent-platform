# Recipe: attach an MCP server to an agent

Goal: let a running agent call tools from a downstream MCP server (a
different MCP connection than the one you're using to configure the
platform right now).

## 1. Create the tool

`tool_create_mcp(...)`
```json
{
  "name": "docs-search",
  "url": "https://mcp.example.com/sse",
  "allowed_tools": ["search", "fetch"],
  "agent_id": "<agent id, or omit to share across the workspace>"
}
```

## 2. Add an auth header, if the server needs one

`provider_key_create(...)`
```json
{
  "provider_id": "http-tool-secret",
  "label": "docs MCP token",
  "secrets": { "TOKEN": "env:DOCS_MCP_TOKEN" }
}
```
`tool_update(...)`
```json
{
  "tool_id": "<the mcp tool id>",
  "patch": {
    "headers": { "Authorization": "Bearer {{ secret.TOKEN }}" },
    "credential_id": "<credential id from above>"
  }
}
```

## 3. Attach and test — there is no dry run for MCP servers

`agent_attach(...)`
```json
{ "id_or_slug": "<agent>", "tool_ids": ["<the mcp tool id>"] }
```
Then run `chat_start`/`chat_send` and check the turn's events for a call
into one of `allowed_tools` — that is the only way to confirm the worker
could reach the server, since `tool_create_mcp` does not probe the
connection at save time.

## Related concepts

`lkap_explain("tools-mcp")`, `lkap_explain("sessions-and-test-chat")`.
