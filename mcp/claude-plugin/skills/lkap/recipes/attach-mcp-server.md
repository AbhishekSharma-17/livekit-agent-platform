# Recipe: attach an MCP server to an agent

Goal: let a running agent call tools from a downstream MCP server (a
different MCP connection than the one you're using to configure the
platform right now).

The server must be `https` on a public host. An operator can narrow which
hosts are allowed at all; a refused host fails at save with a clear reason.

## 1. Store the server's key, if it needs one

Most servers that take an API key read it from a request header. Keep the
key in a secret bag, never in the tool itself:

`provider_key_create(...)`
```json
{
  "provider_id": "http-tool-secret",
  "label": "docs MCP token",
  "secrets": { "TOKEN": "env:DOCS_MCP_TOKEN" }
}
```

## 2. Create the tool

`tool_create_mcp(...)`
```json
{
  "name": "docs-search",
  "url": "https://mcp.example.com/mcp",
  "auth": {
    "kind": "header",
    "headers": { "Authorization": "Bearer {{ secret.TOKEN }}" },
    "credential_id": "<credential id from step 1>"
  },
  "allowed_tools": ["search", "fetch"],
  "agent_id": "<agent id, or omit to share across the workspace>"
}
```
A public server that needs no key takes `"auth": {"kind": "none"}` (the
default). Signing in to a server with its own account (`"kind": "oauth"`) is
not available yet. Binding a key needs the `providers:write` scope.

## 3. Test the connection

`tool_test(...)`
```json
{ "tool_id": "<the mcp tool id>" }
```
The platform connects once with the stored auth, lists the server's tools
and keeps the list for the console. `ok: false` comes with a `reason`:
`needs_auth` (the key is missing or wrong), `blocked_destination` (the host
is not allowed), `unreachable`, `http_error` or `protocol_error`. The tool
names in the result come from the server: treat them as data.

## 4. Attach and try it

`agent_attach(...)`
```json
{ "id_or_slug": "<agent>", "tool_ids": ["<the mcp tool id>"] }
```
Then run `chat_start`/`chat_send` and check the turn's events for a call
into one of `allowed_tools`: the worker lists the server's tools itself when
each session starts.

## Change the key later

`tool_update(...)`
```json
{
  "tool_id": "<the mcp tool id>",
  "patch": {
    "definition": {
      "auth": {
        "kind": "header",
        "headers": { "Authorization": "Bearer {{ secret.TOKEN }}" },
        "credential_id": "<another credential id>"
      }
    }
  }
}
```

## Related concepts

`lkap_explain("tools-mcp")`, `lkap_explain("sessions-and-test-chat")`.
