# LKAP v2 — Architecture, Contracts and Plan

Status: **complete** (2026-09-19). Written incrementally; each file is self-contained. Author: Fable 5.1 (architect). Implementers: Opus 5 / Sonnet 5 agents per `PLAN-V2.md`.

v2 turns LKAP from "a platform with one reference use case" into a general voice + video agent platform: UI-configured providers (cascaded / realtime / half-cascade), a schema-driven panel catalog, all LiveKit avatar and provider plugins, UI-managed LiveKit connections (Cloud projects and self-hosted servers) with per-connection worker fleets, a flow builder, LiveKit-native telephony, evals, observability, distribution surfaces, and production hardening.

## Files

| File | What it is | Read when |
|---|---|---|
| [`ARCHITECTURE-V2.md`](ARCHITECTURE-V2.md) | Every v2 decision with justification, mermaid diagrams (topology, session start, provider/avatar factory, panel protocol, flow runtime, SIP), and the deferred list | Before implementing anything |
| [`CONTRACTS-V2.md`](CONTRACTS-V2.md) | New and changed contracts: entities + DB schema + migrations from v1, API endpoints + Pydantic models, panel protocol v2, flow/node spec schema, registry schema changes, worker supervisor interface, env vars | While implementing a work package |
| [`UI_UX_SPEC-V2-AMENDMENTS.md`](UI_UX_SPEC-V2-AMENDMENTS.md) | Deltas against `../UI_UX_SPEC.md`: new IA, which on-hold WP-1…WP-12 proceed unchanged vs change, new screens' key UX | Web work packages |
| [`DOGRAH-PARITY.md`](DOGRAH-PARITY.md) | Feature-by-feature table: Dograh vs LKAP v1 vs v2 Phase 1/2/3, and where we deliberately differ or beat it | Product scoping, positioning |
| [`PLAN-V2.md`](PLAN-V2.md) | Phases 1–3; Phase 1 work packages in waves with owner, exclusive file ownership, dependencies, acceptance criteria, verification commands and live-verification steps | Orchestration and implementation |

## Precedence

1. `PLAN-V2.md` acceptance criteria decide "done".
2. `CONTRACTS-V2.md` wins over `ARCHITECTURE-V2.md` where they differ on names/shapes.
3. `ARCHITECTURE-V2.md` wins over the v1 docs (`../ARCHITECTURE.md`, `../CONTRACTS.md`, `../DECISIONS-W2.md`) where they differ; anything v1 says that v2 does not contradict still holds.
4. `../research-v2/*` are inputs, not specs — if a research claim is marked UNVERIFIED, the plan treats it as an open item, never as a fact.

## Inputs

- v1 docs: `../ARCHITECTURE.md`, `../CONTRACTS.md`, `../DECISIONS-W2.md`, `../REVIEW-FINAL.md`, `../RUNBOOK.md`, `../UI_UX_SPEC.md`, `../INSURANCE_PACK_MAPPING.md`, `../LIVE_TEST_PLAN.md`.
- Research: `../research-v2/dograh.md`, `../research-v2/livekit-plugins-catalog.md`, `../research-v2/livekit-multi-deployment.md`.

## Working notes

`_briefs/` holds the distilled inputs used while designing (v1 architecture/contracts, review/runbook/UI spec, code shape, the decision sketch). They are inputs, not specs. `_asks.md` (created by V2-00) is the cross-package change-request log during Phase 1.

## Open questions for the user (block or materially change Phase 1)

1. **Git remote / CI host.** The tree reports no git repository at the platform root and has no CI config. V2-09 assumes GitHub Actions; confirm the repo/remote (or name the CI host) before wave 1.
2. **Avatar keys.** Stage L13 needs Beyond Presence (free tier: 40 min/month) and Tavus keys; Simli/Anam optional. Without them the "verified" avatar set stays empty and D-V2-11 ships as "available" only.
3. **SIP trunk + number** (Twilio or Telnyx, SIP-capable) — only if telephony live verification (L12b) is expected inside Phase 1.
4. **A second LiveKit target** for L14: either a second Cloud project's credentials or permission to run a local `livekit-server` in Docker on the dev host (the plan assumes the latter).
5. **Public HTTPS for the api** (`LKAP_PUBLIC_BASE_URL`): required for LiveKit webhooks (Egress completion, SIP events) and for any Cloud-hosted worker. Without it, recordings finalize by polling and `cloud_hosted` mode is documentation-only.
6. **Docker on the dev host** for L11/L14 and the `full` image build (the full image is several GB; a nightly CI build is planned instead of building it on every PR).
