# Recordings and cost

## Recording

`config.recording` (`RecordingConfig{enabled, audio_only, storage_config_id,
retention_days}`) turns on LiveKit Egress for an agent's sessions. A
`storage_config_id` picks where recordings land (an operator-configured
storage backend); leaving it unset uses the workspace default, when one
exists. `session_get(session_id, include_recording_url=true)` returns a
freshly signed playback url when the recording is `ready` (`RecordingOut`);
the api's `GET /v1/sessions/{session_id}/recording` route is what the
console's play button calls, and 404s until it is ready.

## Cost

Every session accrues `CostLine`s (one per billed unit: speech-to-text
seconds, an LLM's input, cached and output tokens, a realtime model's audio and
text tokens priced separately, voice characters or tokens, avatar minutes, and
LiveKit's own call, participant, phone and recording minutes) rolled up into a
`SessionCost` on `session_get`. Each line carries its `price_source`
(`workspace`, `live` or `table`). A line with no known price says
`note: "no price"`, never `$0`. `activity()` lists configuration changes, not
cost. `session_list` rows carry `cost_usd` and `estimated_usd`;
`cost_summary(...)` is the workspace rollup (`AnalyticsSummary`: totals, by
day, by agent, `accuracy_pct`, `top_drivers`).

Test chats (`chat_start`/`chat_send`) spend real Inference or vendor credit
and count against the agent's `max_concurrent_sessions` exactly like a
browser session — the tools' `cost_hint` says so up front.

## Estimates

`cost_estimate(...)` answers "what will this cost per minute?" before a call:
an estimate at list prices, never a bill. It takes one of an agent, a starter
`template_id` or an unsaved `config`, and multiplies each price by a usage
model of named assumptions (call length, how much each side talks, replies per
minute, prompt size measured from the config, tool calls). Each assumption has
a low-high band, so the answer is a `MoneyRange` ("typically $a-$b"), plus a
line per part of the agent with plain labels, the list of unpriced parts, and
the source and date of every price. `workspace_averages=true` swaps the
defaults for the workspace's own session averages once it has ten ended
sessions. `pricing_quote(...)` shows the prices LKAP would use for one
provider and model, with that slot's own per-minute share.

Every session snapshots its estimate when it is created, at the config version
it pinned. After the call, `session_get` shows `estimated_usd` beside the
actual `total_usd`, the `variance_usd`, and `drivers`: one row per part with
the reason it differs ("more talk", "longer prompts", "price changed", ...).
A session created before estimates existed says "no estimate".

## Your prices

Some vendors price by plan, not per unit (ElevenLabs, Cartesia direct, Hume,
every avatar vendor), so the list-price table leaves them unpriced. An admin
enters the workspace's own per-unit price in USD with
`api_request(method="PUT", path="/v1/workspace/prices", body={...})`
(`WorkspacePricesIn`: the full list, at most 100 rows) and reads it back with
`GET /v1/workspace/prices`. A workspace price wins over OpenRouter's live
sheet, which wins over the table. The estimate-agent-cost recipe walks through all of it.

## Reconciliation

Actual cost is computed at list prices. For OpenRouter an admin can also have
LKAP read what OpenRouter itself charged: opt in with
`api_request(method="PUT", path="/v1/workspaces/{workspace}", body={"settings": {"cost": {"reconcile": ["openrouter"]}}})`
(the prices stored beside it are kept). From the next session on, the worker
reports the id of every LLM request (ids only, never the conversation), and
shortly after the call ends LKAP looks each one up with the workspace's
OpenRouter key. `session_get` then shows `reconciled_usd` (what OpenRouter
charged) and, on the LLM's input line, `vendor_usd` with `vendor_ref`
("12 generations"; the charge covers input and output together). A request
OpenRouter has not recorded yet is retried once 30 seconds later. No other
vendor is reconciled: OpenAI and Anthropic report cost only to admin keys and
only per day, and Deepgram's per-request charge needs a project id the
credential form does not ask for yet. Vendor invoices may still differ
(included minutes, volume tiers, taxes).

## Related tools

`cost_estimate`, `pricing_quote`, `cost_summary`, `session_get`,
`session_list`, `api_request`.

## Related schemas

`RecordingConfig`, `RecordingOut`, `SessionCost`, `CostLine`, `CostDriver`,
`CostEstimate`, `EstimateLine`, `MoneyRange`, `PriceQuote`, `WorkspacePrice`,
`AnalyticsSummary`, `AnalyticsBucket`, `AnalyticsDriver`.
