# LKAP v5 — cross-package asks

Format (v3/v4): **owner package** — file — the exact edit — who asked — status. The coordinator's decisions are binding; design questions go to Fable and land as `R-V5-n` in `PLAN-V5.md` §7. Never a key value, a hostname from the environment, a user name or a machine path.

## Coordinator decisions

- R-V5-1 (V5-08): barge-in cancels `request`-method requests only; `request_choice` added to `_REALTIME_SILENT_BUILTINS`; neutral late-submit wording. See `PLAN-V5.md` §7.

## Open

| # | Owner | File | Edit | Asked by | Status |
|---|---|---|---|---|---|
| 1 | V5-15, V5-19 | `agent/src/lkap_agent/platform_agent.py` (`_REALTIME_SILENT_BUILTINS`) | Add `request_consent` / `request_upload` when those tools land (R-V5-1 item 2) | Fable | Open — inherited |

## Done

(none)
