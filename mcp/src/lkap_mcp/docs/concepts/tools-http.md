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

## Redirects and the User-Agent

The tool client never follows redirects, by design: a `301` or `302` comes
back as the result, so check a dry run's `status_code` is `200`, not only
`ok` (which is true below 400). Point `url` at the
final address (for example `api.frankfurter.dev/v1/…`, not the old
`api.frankfurter.app`, which now redirects).

Every request carries a `User-Agent`. By default it is the platform's
(`LKAP_HTTP_TOOL_USER_AGENT` on the worker and on the api for dry runs; the
default is `LKAP/0.1` followed by the project's repository URL); a
`User-Agent` in the tool's own `headers` wins. Some public APIs refuse clients
without contact info: Wikimedia's REST API answers `403` ("Please respect our
robot policy") unless the User-Agent names a URL or an email. A descriptive
name alone is not enough there. Never invent a contact, and never put a
person's email in a tool without their consent.

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

## Background tools

A tool can keep the conversation going while it runs. `execution` (a
`ToolExecution`) on `tool_create_http` or `tool_update` sets how:

- `mode="blocking"` (the default): the agent waits for the result, as always.
- `mode="background"`: the agent says it is on it at once (the model's own
  words, steered by `announce`), keeps talking, and reports the result when
  it is next idle. A result never interrupts the caller or the agent.
- `mode="auto"`: the request runs inline for up to `auto_threshold_ms`
  (default 700); a fast answer comes back as usual, a slow one is announced
  and finishes in the background. The recommended setting for reads.

The safety rule is mechanical. Only **GET** tools follow the agent's
`tools.execution_default` (`agent_update(patch={"tools": {"execution_default":
"auto"}})`); any other method runs blocking unless its own `execution.mode`
says otherwise, and then asks before running a second copy
(`on_duplicate="confirm"`) and is not cancelled by a hangup or a "never
mind". `silent_reply` and a background mode on one tool is refused.
`end_call`, forms, telephony, panel writes and flow steps always block.

`fillers` are up to five lines spoken as written after `filler_delay_s` of
silence, one per `filler_interval_s`; they need a voice (a TTS), so a
realtime model without one and the text channel skip them. Every background
run gives up after `max_duration_s` (default 60) with an error the agent
voices. The session's activity feed and events show `tool_call_updated`
and `tool_reply`. On a flow node a background tool runs blocking until the
worker runs livekit-agents 1.8.3.

## Related tools

`tool_list`, `tool_get`, `tool_create_http`, `tool_update`, `tool_dry_run`,
`provider_key_create`, `agent_attach`, `agent_update`.

## Related schemas

`HttpToolDefinition`, `ToolExecution`, `ToolCreate`, `ToolOut`, `ToolDryRunRequest`,
`ToolDryRunResult`, `CredentialCreate`.
