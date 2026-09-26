# Tools: built-ins and templates

Every agent gets a set of built-in tools the platform runs itself; HTTP
tools (`tools-http`) and MCP servers (`tools-mcp`) come on top. Built-ins are
switched off by name in `tools.builtin_disabled`
(`agent_update(patch={"tools": {"builtin_disabled": ["calculate"]}})`).

## Local built-ins (on by default)

- `calculate`: exact arithmetic for quotes, instalments and percentages
  (`1200 * 15%`, `(450 + 120) / 12`). Numbers, `+ - * / %`, parentheses and
  `round` only; anything else is refused. Instant, never in the background.
- `spell_back`: the words to read an email, phone number, postcode,
  reference number or amount back to the caller ("B as in boy", digits in
  small groups, "one hundred dollars and fifty cents"). Instant.
- `current_time`, `convert_time`: the caller's and the business's time.

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
  caller by default; otherwise only the saved contacts in
  `telephony.sms_targets` (`SmsTarget`: a label and a number), named by
  label. The model can never type a number. The same message goes once per
  call unless the caller agrees to a repeat; each message records an
  `sms_sent` session event without the text.
- `notify_team` (`background`): posts a short summary to the team's webhook
  (a Slack incoming webhook or any JSON webhook). `tools.notify_team`
  (`NotifyTeamConfig`) names an `http-tool-secret` key holding the webhook
  address under `secret_name` (default `TEAM_WEBHOOK_URL`). The
  conversation is included only with `include_transcript`; with
  `on_escalation` (default on) `escalate_to_human` posts too.

`send_sms` and `notify_team` change the world: a second call while one runs
asks first. A configured tool whose key is missing answers "not set up"
when called; `agent_validate` reports the missing key as an error first.

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

## Related tools

`tool_templates`, `tool_create_from_template`, `agent_update`, `agent_attach`,
`agent_validate`, `provider_key_create`, `tool_list`.

## Related schemas

`ToolsConfig`, `NotifyTeamConfig`, `SmsTarget`, `TelephonyConfig`, `ToolExecution`,
`ToolTemplate`, `ToolTemplateInstantiate`, `ToolTemplateInstantiated`.
