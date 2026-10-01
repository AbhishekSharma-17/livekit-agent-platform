# V5-25 live check: curated built-in tools P0

Status: **deferred** (not run by the implementing agent, it needs a worker and api restart and,
for three of the tools, vendor keys the user has not added yet). The coordinator runs the
no-key part after merge. The rest waits for the keys below.

Every vendor request shape (Tavily, Brave, Twilio, Telnyx, Cal.com API v2 with
`cal-api-version` `2024-09-04` for slots and `2024-08-13` for bookings) is pinned by `respx`
fixtures written from the vendors' public documentation. **none is verified against the live
service yet**. Record the first real answer of each here.

## Keys the user must add

| For | Key | Where |
|---|---|---|
| `web_search` | a **Tavily** API key (suggested, free tier, no card) **or** a **Brave Search** API key | Console → Keys → Add key → "Web search" → Tavily / Brave Search (registry `tavily-search` / `brave-search`, field `api_key`), or MCP `provider_key_create(provider_id="tavily-search", secrets={"api_key": ...})`. Then set the agent's `tools.web_search = {provider_id, credential_id}`. |
| `send_sms` | a **Twilio** account SID + auth token and a Twilio number that can send SMS, **or** a **Telnyx** API key and a Telnyx number with a messaging profile | Console → Keys → "Text messages (SMS)" → Twilio (`account_sid`, `auth_token`) / Telnyx (`api_key`). The sending number is not a secret. It goes in the agent's `tools.sms.fields.from_number` (E.164), e.g. `{"provider_id": "twilio-sms", "credential_id": ..., "fields": {"from_number": "+1…"}}`. For a web test also add `telephony.sms_targets` = `[{"label": "My phone", "to": "+…"}]` (your own mobile). |
| Cal.com templates | a **Cal.com API key** and the id of one event type | Console → Keys → Tool secrets, a key holding `CAL_API_KEY`, then `POST /v1/tool-templates/cal_com/instantiate {"credential_id": ..., "defaults": {"event_type_id": <id>}}` (or MCP `tool_create_from_template`), then attach the six tools to the agent. |
| `notify_team` (optional) | a Slack incoming-webhook URL (or any https webhook) | Console → Keys → Tool secrets, a key holding `TEAM_WEBHOOK_URL`, then `tools.notify_team = {"credential_id": ...}`. |

Until V5-28 ships the Tools-tab slots, set these through `agent_update` (MCP) or
`PUT /v1/agents/{id}`. No key is ever pasted into a chat, brief or log.

## Steps

1. After merge: restart the api and the worker (the worker registers new built-ins, the api
   serves `/v1/tool-templates` and resolves `builtin_providers`). No migration.
2. **No key** (text chat on the dev stack): ask "what is 15% of 1,240?" → a `calculate` call,
   answer 186. "spell my email back: jo.smith@example.com" → `spell_back` read-back ("J as in
   juliet…"). "what is 9am London time in Tokyo?" → `convert_time`. `agent_validate` shows no new
   issue for an agent with none of the new fields. The tools list gains only `calculate` and
   `spell_back`.
3. **Web search** (Tavily or Brave key): "what's the latest news about <topic>?" → `web_search`
   runs `auto` (inline when quick, otherwise announced). The reply is one or two sentences and
   reads out no web address. Record the vendor's answer shape and the latency.
4. **Read a page**: `tools.fetch_url_allowed_hosts = ["<a public docs site>"]`. Ask for a summary
   of a page on it → `fetch_url` runs in the background. Ask for a page on another site → refused
   with the allowed sites named.
5. **SMS** (Twilio or Telnyx key and number): web text chat with `sms_targets` set: "text my
   phone the reference ABC-123" → the agent names the saved contact, the message arrives, the
   session has one `sms_sent` event (label, last four digits, message id, no text). Ask again with
   the same text → it asks before sending twice. Ask to text a typed number → refused. On a
   phone call (when the LiveKit number is back) the default destination is the caller.
6. **Cal.com** (key and event type): "is there a free slot next Tuesday afternoon?" →
   `booking_check_availability` with the caller's time zone. Book one after reading the email
   back → `booking_create`, then move and cancel it. Record Cal.com's answers.
7. **Team webhook** (optional): `escalate_to_human` → the webhook receives "*Escalation*: …".
   `notify_team` with the same summary twice → posted once.

## Costs

`pricing.py` has no rows for Tavily, Brave, Twilio, Telnyx or Cal.com, so no session cost line is
produced for these calls. Each registry entry carries a plain-language `price_note` for the
console instead (Tavily: 1,000 free searches a month, then about $0.008 a search, Brave: about $5
per 1,000 with $5 free credit a month, Twilio/Telnyx: per message by country).

## Results

Not run yet.
