# Recipe: add booking tools (Cal.com)

Goal: let an agent check free times and book, move or cancel appointments
in the user's Cal.com account, using the ready-made Cal.com tool set.

You need from the user: a Cal.com API key (Cal.com → Settings → Developer →
API keys) and the id of the event type callers book (its number in
Cal.com's Event Types page).

## 1. See the templates

`tool_templates()` lists the six Cal.com tools (`cal_com.booking_check_availability`,
`cal_com.booking_create`, `cal_com.booking_list`, `cal_com.booking_get`,
`cal_com.booking_reschedule`, `cal_com.booking_cancel`), the secret name
they need (`CAL_API_KEY`) and the argument you fix (`event_type_id`).

## 2. Store the key

`provider_key_create(...)`
```json
{
  "provider_id": "http-tool-secret",
  "label": "Cal.com",
  "secrets": { "CAL_API_KEY": "env:CAL_API_KEY" },
  "test": false
}
```
Note the returned `id` as `secret_key_id` below.

## 3. Create the tools

`tool_create_from_template(...)`
```json
{
  "template_id": "cal_com",
  "secret_key_id": "<credential id from step 2>",
  "defaults": { "event_type_id": 123456 },
  "agent_id": "<agent id>"
}
```
Pass `names` (for example `["booking_check_availability", "booking_create"]`)
to add only some of them, or a single template id such as
`cal_com.booking_create`. Add `"plan": true` first to preview the request.

## 4. Attach and validate

`agent_attach(...)`
```json
{ "id_or_slug": "<agent>", "tool_ids": ["<the tool ids from step 3>"] }
```
Then `agent_validate(id_or_slug)`. The booking tools pass the caller's own
time zone to Cal.com; the agent reads it from the time note in its
instructions, and opening hours stay in the business time zone.

## 5. Try it

`chat_start` and ask for "a slot next Tuesday afternoon". The agent should
offer two or three times, read your name and email back (`spell_back`
helps) and only book after you say yes. Writes (book, move, cancel) wait for
Cal.com's answer; a look-up of free times answers inline when it is quick.
