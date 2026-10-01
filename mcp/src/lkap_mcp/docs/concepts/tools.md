# Tools: built-ins and templates

Every agent gets a set of built-in tools the platform runs itself. HTTP
tools (`tools-http`) and MCP servers (`tools-mcp`) come on top. Built-ins are
switched off by name in `tools.builtin_disabled`
(`agent_update(patch={"tools": {"builtin_disabled": ["calculate"]}})`).

## Local built-ins (on by default)

- `calculate`: exact arithmetic for quotes, instalments and percentages
  (`1200 * 15%`, `(450 + 120) / 12`). Numbers, `+ - * / %`, parentheses and
  `round` only. Anything else is refused. Instant, never in the background.
- `spell_back`: the words to read an email, phone number, postcode,
  reference number or amount back to the caller ("B as in boy", digits in
  small groups, "one hundred dollars and fifty cents"). Instant.
- `current_time`, `convert_time`: the caller's and the business's time.
- `generate_image`: a picture (a sketch, a diagram) from the agent's picture
  model (`pipeline.image_gen`) into the panel's `gallery` block. Offered only
  when both are set (`panels-and-blocks`).

## Network built-ins (off until configured)

Each appears only when its setting is saved, and runs with its own
execution mode unless `tools.builtin_execution` sets one:

- `web_search` (`auto`): three short results from the web, at most 1,500
  characters, each naming its site but never its address. Set
  `tools.web_search` to a provider of kind `web_search` with its key:
  `tavily-search` (suggested: a free tier with no card) or `brave-search`.
- `fetch_url` (`background`): the main text of one page on a site listed in
  `tools.fetch_url_allowed_hosts` (site names only, `docs.example.com`).
  A private or local address is always refused, redirects are checked
  again, and `LKAP_HTTP_TOOL_ALLOWED_HOSTS`, when set, is a ceiling.
- `send_sms` (`background`): a text message. Set `tools.sms` to
  `twilio-sms` or `telnyx-sms` with its key and the sending number in
  `fields.from_number` (international format). On a phone call it texts the
  caller by default. Otherwise only the saved contacts in
  `telephony.sms_targets` (`SmsTarget`: a label and a number), named by
  label. The model can never type a number. The same message goes once per
  call unless the caller agrees to a repeat. Each message records an
  `sms_sent` session event without the text.
- `notify_team` (`background`): posts a short summary to the team's webhook
  (a Slack incoming webhook or any JSON webhook). `tools.notify_team`
  (`NotifyTeamConfig`) names an `http-tool-secret` key holding the webhook
  address under `secret_name` (default `TEAM_WEBHOOK_URL`). The
  conversation is included only with `include_transcript`. With
  `on_escalation` (default on) `escalate_to_human` posts too.

`send_sms` and `notify_team` change the world. A second call while one runs
asks first. A configured tool whose key is missing answers "not set up"
when called. `agent_validate` reports the missing key as an error first.

Keys are stored like any provider key:
`provider_key_create(provider_id="tavily-search", secrets={...})`, then
`agent_update(patch={"tools": {"web_search": {"provider_id":
"tavily-search", "credential_id": "<key id>"}}})`. No key test runs for
these services yet.

## Tool templates

Ready-made HTTP tools: `tool_templates()` lists them,
`tool_create_from_template(template_id=..., secret_key_id=...,
defaults={...})` creates them as ordinary HTTP tools, which you then attach
with `agent_attach`. The first set is Cal.com bookings (`cal_com`):
check availability, book, list, look up, move and cancel. It needs a
Cal.com API key stored as `CAL_API_KEY` in an `http-tool-secret` key and the
event type id as a default. The booking tools pass the caller's own time
zone to Cal.com (the model reads it from the time note in its
instructions). Routes: `GET /v1/tool-templates`,
`POST /v1/tool-templates/{template_id}/instantiate`. The recipe
`add-booking-tool` walks through it.

## Tool kits

A kit is a whole job in one step (V6-18): its tools, the panel blocks they
fill, a short instruction snippet, extraction fields, rules, optional flow
steps and a test case whose tools answer from fakes. `kit_list()` shows the
catalogue: `record_lookup`, `case_ticket`, `structured_intake`,
`verify_identity`, `payment_esign_link`, `notify_escalate`,
`sheet_crm_log` and `booking` (the Cal.com set above), each with variants
(its own system, a lookup table, a connected app, a server to sign in to, or
the panel only). `kit_add(kit_id=..., agent_id=..., settings={...},
dry_run=true)` previews every change. Without `dry_run` it adds them in one
configuration version, and again with the same `block_prefix` adds nothing.
No key is needed. Without `secret_key_id` the HTTP tools carry no key
header. Routes: `GET /v1/tool-kits`, `POST /v1/tool-kits/{kit_id}/instantiate`.
Recipes `add-kit` and `record-lookup-from-a-spreadsheet`.

## Related tools

`tool_templates`, `tool_create_from_template`, `kit_list`, `kit_add`, `agent_update`, `agent_attach`,
`agent_validate`, `provider_key_create`, `tool_list`.

## Related schemas

`ToolsConfig`, `NotifyTeamConfig`, `SmsTarget`, `TelephonyConfig`, `ToolExecution`, `ToolKit`,
`ToolKitInstantiate`, `ToolKitInstantiated`,
`ToolTemplate`, `ToolTemplateInstantiate`, `ToolTemplateInstantiated`.
