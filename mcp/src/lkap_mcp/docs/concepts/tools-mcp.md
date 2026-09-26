# Tools: MCP servers

An agent can attach its own downstream MCP server (`McpServerDefinition`) —
a streamable-HTTP MCP endpoint the *worker* connects to at session time, so
the agent's model can call whatever tools that server exposes. This is a
different MCP connection than the one you are using right now to configure
the platform: that one is you ↔ LKAP; this one is the running agent ↔ some
other MCP server, at conversation time.

## Creating one

`tool_create_mcp(name, url, headers={}, allowed_tools=None, secret_key_id=
None, timeout_s=5, agent_id=None)`. `allowed_tools` restricts which of the
downstream server's tools the model may see; omit it to expose all of them.
`secret_key_id` is the same `http-tool-secret` credential family as an HTTP
tool, referenced in `headers` as `{{ secret.NAME }}` for an auth header the
downstream server needs.

There is no probe route for an MCP server in v3 — unlike `tool_create_http`,
`tool_create_mcp` cannot dry-run the connection at save time. Verify it
works with a real `chat_start`/`chat_send` test after attaching: if the
worker cannot reach the server, the tool simply does not appear to the model
that session, and `session_events` (or the chat's own events) shows why.

## Connected services (servers that sign in)

Some MCP servers want a person to sign in with the vendor instead of an API
key. An admin connects such a server from the console (the tool's page:
Sign in, then the vendor's consent screen); the sign-in itself needs a
browser, so it cannot be done through this MCP server. Once connected:

- the platform keeps the vendor's long-lived grant and renews access by
  itself; the running agent only ever holds access that expires within
  minutes, and gets fresh access from the platform when it runs out;
- if the vendor withdraws the grant, or the server asks for permissions the
  sign-in did not include, the agent's next call to that server fails with a
  plain sentence ("This integration needs to be re-authorised by an admin"),
  the session records a `tool_needs_reauth` event, and the console shows the
  server as needing a new sign-in;
- disconnecting the server in the console, or deleting the tool, withdraws
  the grant at the vendor as well (when the vendor supports it) and forgets
  it here.

A server that is saved but not signed in is skipped at session time; the
session's events say so.

## Attaching

Same as an HTTP tool: `agent_attach(id_or_slug, tool_ids=[...])`, or
`agent_id` set at creation time to scope it to one agent immediately.

## Background tools

`tool_options` maps a downstream tool's name (one of `allowed_tools` when
that is set) to a `ToolExecution`. MCP tools never follow the agent's
`tools.execution_default`; opt each one in. A background MCP tool is
announced only through the server's own progress messages, so set
`report_progress=true` (validation warns otherwise), and its time limit is
the server's `timeout_s`. The rest is as for HTTP tools
(`lkap_explain("tools-http")`).

## Related tools

`tool_list`, `tool_get`, `tool_create_mcp`, `tool_update`, `agent_attach`,
`chat_start`, `chat_send`.

## Related schemas

`McpServerDefinition`, `ToolExecution`, `ToolCreate`, `ToolOut`.
