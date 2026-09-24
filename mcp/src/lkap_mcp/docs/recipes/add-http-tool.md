# Recipe: add an HTTP tool

Goal: give an existing agent a new API-backed tool, including a secret
header, and verify it before the agent ever calls it live.

## 1. Store the secret separately

`provider_key_create(...)`
```json
{
  "provider_id": "http-tool-secret",
  "label": "CRM API token",
  "secrets": { "API_TOKEN": "env:CRM_API_TOKEN" },
  "test": false
}
```
`http-tool-secret` credentials have no live test (`test=false` skips the
attempt); note the returned `id` as `secret_key_id` below.

## 2. Create the tool

`tool_create_http(...)`
```json
{
  "name": "lookup_customer",
  "description": "Look up a customer record by email",
  "parameters": {
    "type": "object",
    "properties": { "email": { "type": "string" } },
    "required": ["email"]
  },
  "url": "https://api.example.com/customers?email={{ email }}",
  "method": "GET",
  "headers": { "Authorization": "Bearer {{ secret.API_TOKEN }}" },
  "allowed_hosts": ["api.example.com"],
  "secret_key_id": "<credential id from step 1>",
  "dry_run_args": { "email": "test@example.com" },
  "agent_id": "<agent id or omit to share across the workspace>"
}
```

## 3. Verify

Check the returned `ToolDryRunResult.status`/`body`; call `tool_dry_run`
again any time you change the definition:

`tool_dry_run(...)`
```json
{ "tool_id": "<the tool id>", "arguments": { "email": "test@example.com" } }
```

## 4. Attach and validate

`agent_attach(...)`
```json
{ "id_or_slug": "<agent>", "tool_ids": ["<the tool id>"] }
```

## Related concepts

`lkap_explain("tools-http")`, `lkap_explain("providers-and-keys")`.
