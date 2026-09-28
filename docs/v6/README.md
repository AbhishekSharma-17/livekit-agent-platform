# LKAP v6 — index

v6 answers the user's four asks of 2026-09-28 with the six research documents in `docs/research-v6/`: speech that streams (OpenRouter stays the LLM family), LiveKit Inference from a self-hosted server (researched; the spike and the credentials are deferred), a generic notebook, drawing board and more panel blocks, and tools for any use case (context placeholders, result bindings, live extraction and rules, lookup tables, a flow tool step, tool kits), with the insurance experience rebuilt as a starter from those parts. Local only; no production deployment yet. Every package except the LiveKit Inference chain (V6-04, V6-05, V6-09) is merged (2026-09-29) and V6 is signed off (R-V6-5: supervised local use, not production); the dev-stack live checks, all still deferred, are listed in `PLAN-V6.md` §5.1, and what carries to the next plan is in `_asks.md` "Open at close".

| Document | What it is |
|---|---|
| [`PLAN-V6.md`](PLAN-V6.md) | The master plan: ground rules and the migration ledger (§0), every decision answering the research's open questions with a default (§1), the package table and waves (§2), one card per package with scope, exclusive files, acceptance and live check (§3), the security checkpoint (§4), what the user must provide and the deferred live checks (§5), the status table (§6), rulings and the user's decisions (§7) |
| [`ARCHITECTURE-V6.md`](ARCHITECTURE-V6.md) | What v6 changed, area by area, as built, and the index of every v6 decision (D-V6-n), ruling (R-V6-n) and user decision (U-V6-n) (V6-25) |
| [`SECURITY-REVIEW-V6.md`](SECURITY-REVIEW-V6.md) | The security review (V6-20): findings, the authorization sweep, the §4 checkpoint row by row, the dependency audit, residual risks, Fable's sign-off (R-V6-2), the V6-21 fix notes; the re-review of V6-18 … V6-28 (§10, R-V6-4, fixed by V6-29); the final sign-off (§11, R-V6-5) |
| [`_asks.md`](_asks.md) | Cross-package asks (the v3–v5 format), each with its owner and status |
| `_briefs/` | Live-check briefs (V6-16, V6-18, V6-22, V6-24) and the migration rehearsal (`v6_002_datasets`) |

Research inputs: `../research-v6/openrouter-audio.md`, `speech-vendors.md`, `tts-vendors.md`, `gateways-audio.md`, `livekit-inference.md`, `panels-and-tools.md`. Operating notes: `../RUNBOOK.md` §3 (connections and their workers), §9.8 (the default pack list and rule patterns), §9.9 (v6 at a glance). Shapes: `../CONTRACTS.md`. The insurance pack's behaviours in the new starter: `../INSURANCE_PACK_MAPPING.md` §5. Process and safety rules: `../v2/HANDOFF.md` and `../v5/PLAN-V5.md` §0.1.
