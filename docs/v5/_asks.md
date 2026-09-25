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
| 5 | V5-47 | `api` `tools` table, Composio adapter | The `tools.kind` check allows only `http`/`mcp`: the `provider` kind needs a migration; Composio marks `/api/v3.1/mcp/servers` deprecated in favour of Tool Router sessions — prefer `tool_router/session` or confirm; Tool Router allow/deny field shapes unverified | V5-18 | Open |
| 6 | coordinator (after V4-13 merges) | `web/src/components/console/tools/tool-row.tsx` and `tools-list.tsx`'s Kind column (`tool.kind === "http" ? "HTTP" : "MCP"`) | On `tool.kind === "provider"` (V5-47's `ProviderToolDefinition`), render an "App" `StatusChip` with the connected app's logo instead of the "HTTP"/"MCP" label — the logo comes from the toolkit behind `definition.connection_id`, not carried on the tool itself, so this needs a lookup (`GET /v1/tool-providers/composio/connections`) added alongside. V5-22 could not make this edit: `tool-row.tsx` was V4-13's file while V5-22 ran (docs/v5/COMPOSIO.md §6, D-V5-C13 says "the tools list shows an 'App' chip … this package edits `tool-row.tsx` only after V4-13 merges, else files an ask") | V5-22 | Open |

## Done

- #4 (V5-18 → V5-22): Keys page (`web/src/components/console/registry/credential-list.tsx`) now filters out `provider_id: "tool-provider-account"` rows client-side (row-level fix; the server-side refusal in ask #2 is still open) and gives the Composio row its own status chip (`useComposioStatus`, shared with Tools → Apps) plus Validate/Rotate/Disable/Remove key actions in place of the generic Test/Rotate/Rename/Delete menu. The Providers page (`web/src/components/console/providers/providers-catalog.tsx`) now has a `tool_provider` tab ("Connected apps"); `provider-row.tsx` renders that kind read-only with a "Manage in Tools → Apps" link rather than the generic enable/key-badge controls, because the generic `PUT /v1/providers/{id}/settings` toggle does not pause tools the way `POST /v1/tool-providers/composio/enable|disable` does (docs/v5/COMPOSIO.md §4's `set_enabled`) — wiring the generic Switch to a tool-provider kind would have let a builder disable Composio without pausing its tools.
