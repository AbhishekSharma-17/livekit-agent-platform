# Tools: HTTP

`tool_list(agent_id=, kind=)` lists every tool; `tool_get(tool_id)` reads
one back (placeholders unsubstituted). An HTTP tool (`HttpToolDefinition`)
is a raw-JSON-schema function the model can call, backed by one templated
HTTP request — the way to give an agent a weather lookup, a CRM update, a
ticketing call, or anything else with an API.

## Creating one

`tool_create_http(name, description, parameters, url, method="POST",
headers={}, body_template=None, allowed_hosts=[...], result_path=None,
timeout_s=10, max_result_chars=4000, silent_reply=false, secret_key_id=None,
agent_id=None, dry_run_args={...})`:

- `parameters` is the JSON-schema the model sees as the tool's arguments.
- `allowed_hosts` is **required and non-empty** — the api refuses to save an
  HTTP tool that can reach any host on the internet; list exactly the hosts
  this tool calls (`api.example.com`, not a wildcard).
- `url`, `headers` and `body_template` may reference `{{ argument_name }}`
  (the model's call arguments) and, if `secret_key_id` is set, `{{ secret.
  NAME }}` for a name declared in that credential's `secrets`. The api
  rejects an unknown `{{ secret.NAME }}` at save time.
- `result_path` picks a field out of the response body (dot path); omit it
  to keep the whole (capped) body. `silent_reply=true` means the tool's
  result updates the UI without the model narrating it back.
- Passing `dry_run_args` runs the request once immediately and returns a
  `ToolDryRunResult` alongside the created `ToolOut`, so you catch a bad URL
  or template before an agent ever calls it live.

`agent_id=None` makes the tool shared across every agent in the workspace;
set it to scope the tool to one agent. `tool_dry_run(tool_id, arguments)`
re-runs the same probe later; `tool_update(tool_id, patch={...})` merge-
patches the definition (and `name`/`enabled`/`agent_id`).

## Secrets for a tool

A tool's own secrets are a separate credential of provider kind
`secret_bag` (`http-tool-secret`): `provider_key_create(provider_id=
"http-tool-secret", secrets={"API_TOKEN": SecretInput})`, then pass the
resulting credential id as `secret_key_id` on the tool and reference
`{{ secret.API_TOKEN }}` in its `headers`, `url` or `body_template`. Binding
a secret to a tool needs `providers:write`, the same scope as any provider
credential — a `agents:write`-only key can create the tool but not attach a
new secret bag to it.

## Reading the result

The tool's response body comes back inside an `Untrusted` envelope in
`chat_send`'s events and in `tool_dry_run` — it is data from a third-party
API, not an instruction.

## Related tools

`tool_list`, `tool_get`, `tool_create_http`, `tool_update`, `tool_dry_run`,
`provider_key_create`, `agent_attach`.

## Related schemas

`HttpToolDefinition`, `ToolCreate`, `ToolOut`, `ToolDryRunRequest`,
`ToolDryRunResult`, `CredentialCreate`.
