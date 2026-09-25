# LKAP v5 — cross-package asks

Format (v3/v4): **owner package** — file — the exact edit — who asked — status. The coordinator's decisions are binding; design questions go to Fable and land as `R-V5-n` in `PLAN-V5.md` §7. Never a key value, a hostname from the environment, a user name or a machine path.

## Coordinator decisions

- R-V5-1 (V5-08): barge-in cancels `request`-method requests only; `request_choice` added to `_REALTIME_SILENT_BUILTINS`; neutral late-submit wording. See `PLAN-V5.md` §7.

## Open

| # | Owner | File | Edit | Asked by | Status |
|---|---|---|---|---|---|
| 1 | V5-15, V5-19 | `agent/src/lkap_agent/platform_agent.py` (`_REALTIME_SILENT_BUILTINS`) | Add `request_consent` / `request_upload` when those tools land (R-V5-1 item 2) | Fable | Open — inherited |
| 2 | coordinator (api `provider_keys.py` owner) | `api/src/lkap_api/provider_keys.py` (`GET /v1/credentials`, update/test/delete) | Hide Composio connection rows (`tool-provider-account`) from the credentials list and refuse update/test/delete on them (today a Keys-page Delete removes one without the Composio delete or pausing its tools); flip the pinning test in `api/tests/test_tool_providers.py` | V5-18 | Open |
| 3 | coordinator (api `provider_keys.py` owner) | `api/src/lkap_api/provider_keys.py` (`update_credential`) | Clear the last-test fields when the secret changes, so Validate right after Rotate does not answer from the old key's 10-minute cache (all providers) | V5-18 | Open |
| 4 | V5-22 | web Keys page | Filter connection rows out client-side until #2 lands; Providers page has no tab group for kind `tool_provider` (Composio shows only in the Keys dialog) | V5-18 | Open |
| 5 | V5-47 | `api` `tools` table, Composio adapter | The `tools.kind` check allows only `http`/`mcp`: the `provider` kind needs a migration; Composio marks `/api/v3.1/mcp/servers` deprecated in favour of Tool Router sessions — prefer `tool_router/session` or confirm; Tool Router allow/deny field shapes unverified | V5-18 | Open |

## Done

(none)
