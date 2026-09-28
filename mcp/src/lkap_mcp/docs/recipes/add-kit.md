# Recipe: add a tool kit

Goal: give an agent a whole job in one step — the tools, the panel blocks
they fill, a short instruction snippet, the details it captures and the
rules that drive the panel — from the kit catalogue.

You need from the user: which job (look a record up, open a case, take
down details, verify the caller, send a payment or signing link, hand over
to the team, log the call, book appointments) and, for a kit that calls
their own system, its address. No key is needed to add a kit.

## 1. Pick a kit

`kit_list()` lists the kits with their variants (where the tools come
from: the user's own system, a lookup table, a connected app, a server the
user signs in to, or nothing but the panel), the settings each needs and
what it adds. `kit_list(kit_id="case_ticket")` shows one in full, its
names spelled with the kit's default prefix.

## 2. Preview it

`kit_add(...)`
```json
{
  "kit_id": "case_ticket",
  "agent_id": "<agent id>",
  "settings": { "base_url": "https://helpdesk.example.com/api/cases" },
  "dry_run": true
}
```
`changes` lists every tool, block, instruction snippet, extraction field,
rule, flow step and test case it would add (or keep, when the agent already
has it); `tools` shows the tools as they would be stored and `validation`
the agent's check afterwards. Nothing changes.

## 3. Add it

Run the same `kit_add(...)` without `dry_run`. Everything lands in one new
configuration version; the snippet sits between `<!-- kit:case_ticket:case -->`
markers at the end of the instructions. Adding the kit again with the same
`block_prefix` adds nothing; another prefix adds a second copy (a `policy`
lookup next to an `order` lookup).

- **Keys.** `secret_key_id` is an `http-tool-secret` key holding the secret
  names the variant lists (binding it needs `providers:write`). Without it
  the HTTP tools carry no key header, and `next_steps` says so.
- **Connected apps.** A `composio` variant needs `connection_id` (connect
  the app first, recipe `connect-an-app`) and `providers:write`.
- **Flows.** On a flow agent, `flow_anchor` names the step the kit's steps
  hang off; an agent without a flow never becomes one.

## 4. Validate and try it

`agent_validate(id_or_slug)` should show no error. Each kit adds a test
case whose tools answer from the kit's fakes, so
`agent_tests_run(id_or_slug)` runs offline; then `chat_start` to talk to it.
