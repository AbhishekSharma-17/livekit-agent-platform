# V4-17 live check: OpenRouter's own charge beside the list-price cost

Status: **protocol only, not run.** No session ids yet. This is the protocol for PLAN-V4 V4-17 "Live" (`COSTS.md` §8 step 6 in full; rulings R-V4-48, R-V4-62). V4-17 adds the workspace opt-in `settings["cost"]["reconcile"]`, delivers it to the worker as `ResolvedAgentConfig.cost_reconcile`, has the worker post a `metrics {kind: "provider_requests"}` event with the per-request LLM/STT/TTS ids, and runs the api job `cost_reconcile`, which looks each OpenRouter `gen-…` id up at `GET /api/v1/generation?id=` and writes `session_costs.vendor_usd`/`vendor_ref` on the LLM input line and `sessions.reconciled_usd`.

The repo is public: no token, key, host, phone number or transcript goes in this file. Session ids, agent ids and dollar figures are fine. A failing step becomes an ask with the log line. Never patch during the run (R-V4-20).

## Before you start

1. **Worker restart.** After the merge the user restarts their `lkap-agent` worker (the worker code changed: `observability.py` and one `main.py` hunk). The run never restarts, signals or kills it. Record the SDK version from the startup line (expect 1.8.3). The api needs a restart too for the new job handler and routes; the user does that as well.
2. **Key (R-V4-17 rule 1).** Mint one key named `v417-live` with the `*` scope (`PUT /v1/workspaces/{id}` needs `admin`, and an API key reaches the workspace routes only with `*`) with `expires_at` +1 day, kept only in a 0600 MCP config in the scratchpad. Revoke it in step 5.
3. **Objects touched.** `Demo — Vision assistant` (existing, OpenRouter LLM) is used unchanged. The only write is the workspace setting, reverted in step 5. Capture `GET /v1/workspaces` (the `settings.cost` object, prices included) before step 1 and after step 5: the diff must be empty.
4. **OpenRouter credential.** The dev workspace's existing OpenRouter key (the one V4-15's live check used). Nothing new is stored.

For each step record: pass/fail, the session id, the figures with their units, and the api log lines `cost_reconcile_*` / `cost_reconciled` for the session (they carry counts and dollar figures only; confirm no key appears).

## Step 1: opt-in, and the worker sees it

1. `api_request(method="PUT", path="/v1/workspaces/default", body={"settings": {"cost": {"reconcile": ["openrouter"]}}})` → 200; the response's `settings.cost.prices` is unchanged from the capture (the merge keeps it).
2. `api_request(method="PUT", ..., body={"settings": {"cost": {"reconcile": ["deepgram"]}}})` → 422 naming the project id (ask #94). Nothing stored.

## Step 2: a session with ids

Start a text chat on `Demo — Vision assistant` (`chat_start`), send three turns, `chat_end`. In the worker log for that session expect, once each:
- `metrics_collected subscribed for cost reconciliation; the SDK's deprecation warning that follows is expected` (info), then the SDK's `metrics_collected is deprecated` warning;
- `provider request ids posted` with `ids` ≥ 3 and `dropped=0`.

`session_get` → the timeline has one `metrics` event with `kind="provider_requests"`; its `data.llm` rows carry `gen-…` ids and nothing else (`request_id`, `provider`, `model`).

## Step 3: the reconciliation (COSTS.md §8 step 6)

Within a minute of `chat_end`, `session_get`:
- The OpenRouter LLM lines show `price_source="live"` at OpenRouter's listed price (V4-15).
- `cost.reconciled_usd` is set, and the `tokens_in` line has `vendor_usd` with `vendor_ref="<n> generations"`, `n` = the number of `llm` ids. The `tokens_out` line has no vendor figure (OpenRouter's charge covers input and output together).
- **Pass:** `|reconciled_usd − (tokens_in.cost_usd + tokens_out.cost_usd)| ≤ 5 %` of the latter. Record both figures. A larger gap is an ask naming the difference (cached-token discount, a fallback model, BYOK, a price change since the sheet was cached), not a patch.
- If the api log shows `cost_reconcile_retry_scheduled`, record it: a generation OpenRouter had not recorded yet; the figure should appear ~30 s later. `vendor_ref` ending in ", k not found" after the retry means k ids never resolved: record k.
- The audit log (`activity`) has one `cost_reconciled` row for the session (`actor_type=system`).

## Step 4: no opt-in, no ids

`PUT` the setting back to `{"reconcile": []}`, run one more short text chat on the same agent: the worker log has **no** `metrics_collected` line and the timeline has no `provider_requests` event; `reconciled_usd` stays null.

## Step 5: clean-up

Restore `settings.cost` to the captured object (the `reconcile` key absent or `[]`, prices unchanged), revoke `v417-live`, diff the workspace capture (empty).

## Not in this check

- `openrouter-stt` / `openrouter-tts` lines are not reconciled (their request ids are not known to be OpenRouter generation ids; ask #191). The §8 step 6 unit check for those entries belongs to V4-15's brief.
- Deepgram (ask #94) and image-generation counting (ask #190).
