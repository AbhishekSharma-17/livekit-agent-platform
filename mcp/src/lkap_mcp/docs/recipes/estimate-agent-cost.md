# Recipe: estimate an agent's cost, then compare with a real call

Goal: know what an agent costs per minute before publishing it, see which part
of the pipeline drives the figure, and check the estimate against a test call.
Every figure here is an estimate at list prices, not a bill.

## 1. Estimate the saved config

`cost_estimate(...)`
```json
{ "agent_id_or_slug": "<agent>", "session_minutes": 5 }
```
Read `per_minute_usd` (`low`, `mid`, `high`) and `per_session_usd`. `lines`
lists each part with a plain label ("Caller's speech → text", "Agent's
thinking", "Agent's voice", "Call minutes", ...), its quantity per minute and
the price with `quote.source` and `quote.as_of`. `unpriced` names every part
with no published price; `caveats` repeats the tier notes.

## 2. Try a change before saving it

Estimate a starter or a draft instead of the saved agent:

`cost_estimate(...)`
```json
{ "template_id": "receptionist", "channel": "phone", "assumptions": { "agent_talk_ratio": 0.6 } }
```
`channel` is `web`, `phone` (adds phone minutes) or `text` (drops every audio
part). Assumption keys you can override: `session_minutes`,
`caller_talk_ratio`, `agent_talk_ratio`, `agent_turns_per_min`,
`output_tokens_per_turn`, `tool_calls_per_session`, `prompt_tokens`, and more
listed in the tool's description. `"workspace_averages": true` uses the
workspace's own session averages once it has ten ended sessions.

To compare two models for one slot, quote each:

`pricing_quote(...)`
```json
{ "provider_id": "livekit-inference-tts", "model": "deepgram/aura-2" }
```

## 3. Price a plan-based vendor

An avatar or a plan-priced voice shows up in `unpriced`. An admin can enter
the workspace's own rate (USD per unit):

`api_request(...)`
```json
{
  "method": "PUT",
  "path": "/v1/workspace/prices",
  "body": { "prices": [ { "provider_id": "bey-avatar", "unit": "minutes", "usd_per_unit": 0.1, "note": "Starter plan", "as_of": "" } ] }
}
```
The list replaces the stored one. Estimate again: the avatar line now says
`quote.source: "workspace"`.

## 4. Run a test call and compare

Publish only after `agent_validate(...)` is clean, then run a short test chat
with `chat_start(...)` and `chat_send(...)`, end it with `chat_end(...)`, and
read the session:

`session_get(...)`
```json
{ "session_id": "<session>", "include_transcript": false }
```
`cost.total_usd` is the actual cost at list prices; `cost.estimated_usd` is
the estimate snapshotted when the session was created, at the config version
it ran; `cost.variance_usd` is the difference; `cost.drivers` explains it part
by part with a reason. A test chat has call minutes on both sides and no audio
parts on either.

## 5. Watch it over time

`cost_summary(...)`
```json
{ "range": "7d" }
```
`estimated_usd` and `accuracy_pct` sit beside the actual `cost_usd`, per day
and per agent, and `top_drivers` shows where the money went.
