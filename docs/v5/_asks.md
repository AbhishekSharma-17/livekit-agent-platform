# LKAP v5 — cross-package asks

Format (v3/v4): **owner package** — file — the exact edit — who asked — status. The coordinator's decisions are binding; design questions go to Fable and land as `R-V5-n` in `PLAN-V5.md` §7. Never a key value, a hostname from the environment, a user name or a machine path.

## Coordinator decisions

- R-V5-1 (V5-08): barge-in cancels `request`-method requests only; `request_choice` added to `_REALTIME_SILENT_BUILTINS`; neutral late-submit wording. See `PLAN-V5.md` §7.

## Open

| # | Owner | File | Edit | Asked by | Status |
|---|---|---|---|---|---|
| 1 | V5-15, V5-19 | `agent/src/lkap_agent/platform_agent.py` (`_REALTIME_SILENT_BUILTINS`) | Add `request_consent` / `request_upload` when those tools land (R-V5-1 item 2) | Fable | Open — inherited |
| 2 | V5-03 | `web/src/panels/registry.ts` | `PanelAction` (and `PanelFormSubmitAction`, or a new `PanelBlockSubmitAction`) has no `"block_submit"` member, though `AgentAction.action` has carried it since V5-02 and `agentActionFor` already forwards any action it doesn't special-case unchanged. `web/src/panels/composite/use-block-request.ts` sends it today via `perform({action: "block_submit", ...} as unknown as PanelAction)` (documented at the cast) — correct at runtime, but the type gap should close. Add `PanelBlockSubmitAction {action: "block_submit"; payload: {block_id: string; values: Record<string, unknown>} | {block_id: string; cancelled: true}}` to the `PanelAction` union and drop the cast in `use-block-request.ts`. | V5-03 | Open |
| 3 | V5-03 | `web/src/panels/composite/index.tsx` (docblock) and `web/src/panels/README.md` | Both still describe the composite panel's `lkap.ui.request` handling as `form`, `show_block`, `focus`, `navigate` only; V5-03 added `request` alongside `form` in `composite/requests.ts` (one handler, two method names) but neither doc comment was touched (not exclusive files of this card). Update the method lists to include `request`. | V5-03 | Open |

## Done

(none)
