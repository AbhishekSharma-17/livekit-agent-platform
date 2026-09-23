# Phase 1 cross-package asks

One line per change a package needs in a file it does not own (`PLAN-V2.md` §
"Exclusive ownership"). Format: **owner package** — file — the exact edit — who asked.

Created by V2-00.

## Open — left by V2-00 (Contracts v2)

These are the only places where the v2 contracts break a v1 consumer. V2-00 owns
`contracts/**`, the two pack manifests and the regenerated
`web/src/contracts/lkap-contracts.d.ts` only; the coordinator granted it the
api/agent/packs files (asks 1–4, now closed) and holds the two `web/` ones back
while WP-2 is in flight.

| # | Owner | File | Edit | Why |
|---|---|---|---|---|
| 5 | WP-3 / V2-13 (web agent editor) | `web/src/components/console/agents/defaults.ts` | `DEFAULT_VOICE += first_speaker: "agent"`, `DEFAULT_CAPABILITIES += dtmf: false` | `VoiceConfig.first_speaker` and `CapabilitiesConfig.dtmf` (CONTRACTS-V2 §4.3); both are `Required<…>` in that file |
| 6 | WP-3 / V2-13 (web agent editor) | `web/src/components/console/lib/schemas.ts` | Add `"half_cascade"` to the zod `mode` enum of `agentEditorFormSchema` | `PipelineMode` gained `half_cascade`; `agent-editor.tsx:37` assigns `config.pipeline.mode` into the form type |

Asks 1–4 were applied by V2-00 after the coordinator granted the files
(2026-09-20); `agent`, `api` and `packs` are back at their baseline counts
(331 / 230 / 189 + 5 xfail). Asks 5–6 stay open: WP-2 is editing `web/` and the
coordinator applies them once it lands. Until then `pnpm typecheck` reports
exactly those three errors.

These six were the *complete* list of downstream breaks V2-00 caused. Other web
`tsc`/vitest failures seen the same day (`pre-call-card`, `agents-table`,
`end-of-call-card`, `create-agent-dialog`, a transient `tests/zzrepro.test.tsx`)
come from the parallel UI packages, not from contracts.

## Open — left by V2-01 (DB schema v2)

V2-01 needed nothing in a file it does not own, so these are **hand-offs**, not
edits it wants made: decisions it had to take in its own files that wave-1
packages must build on, plus one gap in a file V2-00 owns.

| # | Owner | File / subject | What V2-01 did, and what the owner must do |
|---|---|---|---|
| 11 | V2-00 / coordinator | `contracts/**` `HealthResponse` + `api/src/lkap_api/routers/health.py` | CONTRACTS-V2 §1.3 says `/v1/health` reports `agents_unbound: n`, but `HealthResponse` has no such field and neither file is V2-01's. **Not implemented.** `health.py` also has no owner in any card — the coordinator should assign it (V2-02 is the natural home, it already touches every admin route). The V2-01 live acceptance only needed `db: ok`, which passes. **Whoever takes it should also make the check compare `alembic_version` to the script head**: today it only does `select(Agent.id).limit(1)`, so a half-migrated or un-migrated schema still reports `db: "ok"` while the v2 columns are missing (see #17). |
| 12 | V2-02 | `api/src/lkap_api/auth/passwords.py` (new) | `bootstrap.ensure_owner` imports `lkap_api.auth.passwords.hash_password` lazily via `importlib` and stores `password_hash=NULL` when it is absent, so V2-01 did not pick an argon2 library on V2-02's behalf. Once that module exists, bootstrap hashes `LKAP_BOOTSTRAP_OWNER_PASSWORD` (or a `secrets.token_urlsafe(24)` logged once at WARNING). **V2-02 must also set the password on the *existing* owner row**, which today has a NULL hash and cannot sign in. Note `api/src/lkap_api/auth.py` is still a v1 *module*; turning it into the `auth/` package is V2-02's to resolve. |
| 13 | V2-02 | `api/tests/conftest.py` — `tenant_guard` fixture | The "no unscoped tenant query" guard (`lkap_api.db.guard`) ships **opt-in**, not autouse: every v1 router still queries unscoped, so autouse would fail ~200 tests today. V2-02 flips `tenant_guard` to `autouse=True` once `WorkspaceContext` is on the admin routers. `audit_log` and `workspace_members` are deliberately outside `TENANT_TABLES` (see the test for why). |
| 14 | V2-02 | `workspace_id` defaults | Every tenant model column has a **Python-side default** of `DEFAULT_WORKSPACE_ID` (and a matching `server_default` in `v2_003`). That is what keeps the v1 routers inserting successfully after the tenancy migration (zero downtime); V2-02 replaces the default with the value from `WorkspaceContext`. |
| 15 | V2-03 / V2-08 / V2-17 | `*_ct` column encoding | Every encrypted column (`livekit_connections.api_key_ct`/`api_secret_ct`, `storage_configs.access_key_ct`/`secret_key_ct`, `webhook_endpoints.secret_ct`, `sip_trunks.auth_password_ct`) holds **`Vault` ciphertext over a one-key secret bag**, exactly like v1's `credentials.ciphertext`: `vault.encrypt({"api_key": k})`. Read them back with `Vault.decrypt(...)["api_key"]`. `python -m lkap_api.keys rotate` relies on this uniformity. |
| 16 | V2-03 | `fleet_desired` | The table exists and is empty. Bootstrap does **not** write it — V2-03 owns the `fleet_desired` writer, so a connection created by bootstrap has no desired-state row until V2-03 lands. |
| 17 | **Coordinator** (decision), V2-11 / WP-3 | Migrating the developer's live `api/data/lkap.db` | **This is a decision, not an instruction — and it cannot be deferred indefinitely.** Facts: (a) the v2 `Agent` model selects `workspace_id`, `connection_id`, `mode`, `limits`, `allowed_origins`, so as soon as the dev api restarts on V2-01 code against the un-migrated file, every agent route 500s with `no such column` — while `/v1/health` still answers `db: "ok"`, because it only selects `agents.id` (hence the addition asked for in #11). The process on :8080 is still running pre-V2-01 code and is unaffected **until it restarts**. (b) `upgrade head` moves 7 of the 10 live agents from `ui_panel_id="generic"` to `"composite"` with the four default blocks; `downgrade 4135323c6ecc` restores `generic`. Both verified on a copy. **Decision needed:** either accept that those 7 dev agents' panels do not render until V2-11, or file a one-line web ask to treat `composite` as `generic` in the interim. Sequence when migrating (no need to stop anything first): `sqlite3 api/data/lkap.db ".backup <path outside api/data>"`, then `cd api && uv run alembic upgrade head`, then restart the api. Take the backup before **every** alembic run on that file: SQLite DDL is non-transactional, so a mid-chain failure leaves `alembic_version` out of step with the schema. |
| 18 | V2-09 | `.github/workflows/api-postgres.yml` | V2-01 shipped it as the stub its card allows. V2-09 folds the Postgres leg into its own `python.yml` matrix and should then delete this file. It is also the **only** place the v2 chain is exercised against a real server (no Docker on the dev host — see the V2-01 report). |

## Open — planning issues found by V2-00

| # | Owner | Issue |
|---|---|---|
| 8 | V2-05 | `GEMINI_LIVE_VOICES` is still the v1 8-name list. CONTRACTS-V2 §4.1 says 30 names; the PLAN gives the item to V2-05, so V2-00 left it alone. |

## Closed

| # | Resolution |
|---|---|
| 7 | Resolved by ruling **R-V2-1** (PLAN-V2 §8): the alias is `available and worker_image == "slim"`; `verification` gates nothing. V2-00 now sets `verification` honestly (the four live-verified providers) and `worker_image="slim"` on all 18 v1 entries, so V2-05's flip is a no-op for `status` and no consumer changes in waves 0-1. |
| 9 | Resolved by ruling **R-V2-2**: V2-00's "owns and updates" wording is withdrawn. `api/config_service.py` and `api/packs.py` → V2-03; `web/src/components/console/registry/**` and `CAPABILITY_META` → V2-13. V2-00 touched none of them. |
| 10 | Ruling **R-V2-3b** applied by V2-00: `UiRequest` payload keys documented in `ui_protocol.py` (`open_dialog {dialog, params?}`, `focus {target}`, `request_video_source {source}`, `toast {message, tone?}`), and `agent/tests/unit/test_ui_channel.py` now calls `request_ui("open_dialog", {"dialog": "packet"})`. No other `{"id": ...}` call site exists under `agent/` or `packs/`. |

| # | Applied by | File | Edit | Why |
|---|---|---|---|---|
| 1 | V2-00 (granted by the coordinator, 2026-09-20) | `agent/src/lkap_agent/providers/factory.py` | Add `"vad"`, `"turn_detection"`, `"noise_cancellation"` to `SLOT_KINDS` (kind mapping only; construction stays V2-07's) | `ProviderSlot` gained the three slots (CONTRACTS-V2 §4.3); the tripwire `agent/tests/unit/test_factory.py::test_slot_kinds_covers_every_resolved_slot` fires as designed |
| 2 | V2-00 (granted by the coordinator, 2026-09-20) | `api/tests/test_agents.py::test_validate_endpoint_reports_warnings` | Expect `{"ok": True, "errors": [], "warnings": [], "issues": []}` | `ValidationResult` gained `issues[]` (CONTRACTS-V2 §"Conventions", UI_UX_SPEC §7.14) |
| 3 | V2-00 (granted by the coordinator, 2026-09-20) | `api/tests/test_credentials.py::test_credential_test_reports_not_implemented_providers` | Replaced the exact-JSON match with a subset check on `ok`/`message`, so later additive fields cannot break it again | `CredentialTestResult` gained `checked_at`/`catalog_preview` (CONTRACTS-V2 §3.4) |
| 4 | V2-00 (granted by the coordinator, 2026-09-20) | `packs/tests/insurance_claim/fixtures/insurance_ui_state_lkap_{blank,auto,flood}.json` | Regenerate: `UiState` now dumps `"v": 2` and `"blocks": {}` | `UiState` v2 (CONTRACTS-V2 §4.4) |
