# V4-06 — populate and validate: run log

**Result: 8 of 8 demo agents are built, tested in chat and published** in the user's dev workspace, one per starter. Every agent has at least one `ready` knowledge base, at least one enabled HTTP tool whose dry run returned 200, and zero `agent_validate` errors.

**Avatars.** Beyond Presence renders live on Demo — Vision assistant. **The Simli session fails at start** on Demo — Survey / intake form; see B-1.

**Findings.** Eleven bugs are logged (one blocker, five degrades, five cosmetic), and none were fixed (R-V4-20).

**Cost and safety.** The run cost **$12.53**, against a $30 cap. The before/after diff outside the manifest is empty, and both keys are revoked.

- **Date:** 2026-09-24, from 20:36 to 21:28 UTC (2026-09-25 local time; the demo descriptions keep the card's "2026-09-25" label).
- **Repo:** HEAD `b712faa` at the start. Another session committed V4-04 (`9561de8`) during the run.
- **Driver:** Opus 5.5, following the card's protocol (`PLAN-V4.md` V4-06, R-V4-17…20).
- **Target:** the user's real dev api on `:8080` and its DB, at migration `v4_001_livekit_numbers`. The user's `lkap-agent` worker served every chat.
- **Worker:** PID 27884, image `slim`, SDK 1.8.2, 33 installed providers, including `bey-avatar` and `simli-avatar`.
- **Scratch files:** `<scratchpad>/v406/`. That directory holds:
  - the prompts, in `prompts/*.txt`;
  - the transcripts, in `transcripts/*.jsonl`;
  - `manifest.json`;
  - the snapshots, in `snap/`;
  - the gate script, `check.py`;
  - `final-agents.json`.
- **Screenshots:** `<scratchpad>/ui-audit/v4-06/`. This is where the coordinator's brief asked for them; the card had suggested `docs/v4/_briefs/v4-06-populate/`, and nothing was added to the repo there.

## 1. Protocol as run

| Rule | How it was met |
|---|---|
| Key (R-V4-17 #1) | `POST /v1/api-keys` with the dev admin token (header `X-Admin-Token`). Body: `name=v406-populate`, `kind=agent`, `client=claude-code`, the Builder scopes (`agents:read/write`, `sessions:read/write`, `connections:read`, `providers:read`, `audit:read`), expiry +1 day. Result: id `9e1802db…`, prefix `lkap_mR-`. The key lived only in `<scratchpad>/v406/stdio.mcp.json` (0600). It was **revoked** at the end (`DELETE` returned 204, and `GET /v1/api-keys` then showed `revoked_at`), and the file was deleted |
| Headless flags (#2) | `claude -p "<prompt>" --permission-mode default --allowedTools mcp__lkap Skill ToolSearch --tools Skill,ToolSearch --strict-mcp-config --mcp-config <scratch>/stdio.mcp.json --output-format stream-json --verbose --model sonnet --max-budget-usd 3 --max-turns 40`, from cwd `<scratch>/v406/project`, with the skill installed via `scripts/install_claude_skill.sh --project`. Every prompt was prefixed `/lkap`. **Deviation:** `--model sonnet` was passed explicitly (the coordinator asked for sonnet; the card says "no override"). Retry and test-only runs used a 1.5 / 20 cap |
| Isolation evidence | Every run's `system/init` shows `model=claude-sonnet-5`, `permissionMode=default`, `mcp_servers=[{lkap, connected}]` and 48 `mcp__lkap__*` tools. That tool set is exactly `tools.snap.json`'s `visible.builder`: no `call_place` or `call_control`, and no connection, provider-key or webhook writes. There were **no permission denials** in any run. The probe run: session `bedd8a58-…`, $0.24 |
| No telephony (#3) | Trunks, numbers and dispatch rules were `[]` before and after, and `calls` was `{items: [], total: 0}` before and after, so both are **identical**. No prompt mentioned a number, trunk, rule or transfer target |
| User's objects untouched (#4) | `check.py` ran after every run. It snapshots agents (id, slug, config_version, published, updated_at, archived_at), KBs, tools, provider keys, webhooks, connections, telephony and calls. It then diffs pre-existing rows against `snap/before.json` and scans every write `tool_use` for ids outside the manifest. **Foreign changes in every run: none.** See §6 |
| No webhooks, provider keys or connections (#5) | None were created. The avatars reference the user's existing credentials by id only: Bey `2c997961…` (fingerprint `…SZaM`) and Simli `1b7eb680…` (fingerprint `…so27`) |
| No restarts or env (#6) | The worker, api and web kept the same PIDs, running continuously (27884, 35129 and 35228). No `.env*` was read, and no `credentials*` path was touched |
| Delete (R-V3-21) | The only `lkap_delete` and `agent_archive` calls were on the throwaway agent `b263b68d…` (§4) |

**Simli face id.** `provider_catalog("simli-avatar", "avatars")` returned `items: []` (`source=vendor`). The run therefore used Simli's documented preset face **"Tina", `cace3ef7-a4c4-425d-a8cf-a5358eb0c427`**, the first entry of https://docs.simli.com/api-reference/preset-faces. LiveKit's Simli page only shows `face_id="..."` and points at Simli's library.

**Simli path.** `simli-avatar` is installed on the worker, so the card's Bey fallback was **not** used. Simli is configured on the survey agent as the user chose. It fails at runtime (B-1), which is a worker bug, not a missing plugin.

## 2. Per-agent results

| Agent | Slug | Starter | KBs (all docs `ready`) | HTTP tools (dry run) | Avatar | Chat session | KB signal | Published |
|---|---|---|---|---|---|---|---|---|
| Demo — Blank agent | `demo-blank-agent` | blank | · House guide, · Membership plans | `demo_public_holidays` **200** (used); `demo_currency_rates` 301 ×2 → **disabled** (B-3) | — | `3356c6d0…`: ended, 3 user turns, usage, tools `current_time`, `demo_public_holidays` | reply "open 10:00–4:00 on Sundays" (the hours are only in the House guide; auto-inject) | yes |
| Demo — Knowledge assistant | `demo-knowledge-assistant` | knowledge_assistant | seeded Product FAQ and Support playbook; · Product FAQ (25 Q&A); · NWS API notes (**URL import** of weather.gov/documentation/services-web-api, `text/html`, 72 chunks) | `demo_nws_point_lookup` **200** (used); `demo_wikipedia_summary` 403 ×2 → **disabled** (B-4) | — | `0498f2b1…` (3 turns, `escalate_to_human`, `set_status`) and `62970fdb…` (2 turns, `demo_nws_point_lookup`) | "According to the product FAQ…" plus a `kb_citations` block_update (`sources`) | yes |
| Demo — Receptionist | `demo-receptionist` | receptionist (flow) | seeded Practice info; · Practice info (extended) | seeded `check_availability` → httpbin `/anything` **200**; `book_appointment` → httpbin `/post` **200** (both repointed with `tool_update`); `demo_public_holidays` **200** (shared with Blank) | — | `f009894f…`: the full booking flow (3 user turns); `8858a221…`: July 4th → `search_knowledge` and `demo_public_holidays` (1 turn); `b1d4a56e…`: the KB after the fix (2 turns) | none before the fix (B-2); after it, "garage entrance on 12th Street … first 90 minutes" | yes |
| Demo — Vision assistant | `demo-vision-assistant` | vision_assistant | · What to look for | `demo_zip_lookup` **200** (used) | **bey-avatar**, participant "Demo avatar", LLM `google/gemini-3.5-flash` | `e8dccdda…`: ended, 2 turns, `demo_zip_lookup` | reply lists error messages, status codes and form fields from the checklist (weak) | yes |
| Demo — Phone agent | `demo-phone-agent` | phone_agent | seeded FAQ; · FAQ (extended) | `demo_weather_alerts` **200** (used; User-Agent set) | — | `c628e26c…`: ended, 3 turns, `demo_weather_alerts` | "open until 6:00 PM CT weekdays, 1:00 PM Saturdays, closed Sundays" (from the FAQ) | yes |
| Demo — Lead qualification | `demo-lead-qualification` | lead_qualification (flow) | seeded Offer sheet; · Customer stories | `demo_lead_zip_lookup` **200** (used); `demo_company_summary` 403 → **disabled** (B-4) | — | `cac1895d…` (3 turns, `demo_lead_zip_lookup`, `search_knowledge` → none) and `9ccc8cd2…` (3 turns, after the fix) | none before the fix (B-2); after it, "Team plan, from 25 dollars per user per month" | yes |
| Demo — Survey / intake form | `demo-survey-intake-form` | survey_intake | · Survey guidelines | `demo_country_info` (date.nager.at `CountryInfo`) **200** (used); `demo_country_facts` (restcountries) 301 → **disabled** (B-3) | **simli-avatar**, face Tina, participant "Demo avatar" (fails live, B-1) | `ecd2baf7…`: ended, 4 turns, `request_form`, `table_append`, `demo_country_info`, `end_call` | not observed (a guideline doc; no search, no citation block) | yes |
| Demo — Insurance claim intake | `demo-insurance-claim-intake` | insurance_claim (pack) | pack KBs `Insurance policy lines` and `Intake playbook` (the user's pre-existing rows, reused by the seeder); · Policy notes | `demo_weather_alerts` **200** (shared with Phone; used) | — | `cc096053…`: ended, 3 turns, `lookup_policy`, `demo_weather_alerts`, `search_knowledge` (hit on `policy-notes.md`), workflow `sync_claim_packet` | a `search_knowledge` hit | yes |

`agent_validate` (re-run by the driver at the end, `POST /v1/agents/{id}/validate`) reports **0 errors for all eight**. The standing warnings are `knowledge.auto_inject` (all eight) and `qa.enabled` (lead qualification).

**How the demos differ from the card**
- **Substitute tools.** They keep each agent working while its card tool is disabled:
  - `demo_nws_point_lookup` on the Knowledge assistant, which fits its NWS notes KB;
  - `demo_lead_zip_lookup` on Lead qualification;
  - `demo_country_info` on Survey.

  All three are in the R-V4-18 allowlist.
- **Flow KB workaround.** On Receptionist and Lead qualification, a follow-up run set the flow's **global node `kb_ids`** to the agent's KBs. This is a config change, not code, and without it the flow agents can't reach their knowledge (B-2).
- **Shared tools.** Tools are workspace-level (`agent_id: null`), so a same-name tool was reused rather than duplicated:
  - `demo_public_holidays` is shared by Blank and Receptionist; the receptionist run also rewrote its description;
  - `demo_weather_alerts` is shared by Phone and Insurance.
- **KB signal.** The worker logs `injected knowledge` at DEBUG, and the user's worker runs at INFO, so that line never appears (B-11). The KB signal above therefore comes from reply content, `search_knowledge` events or `kb_citations` block updates.

## 3. Chat excerpts (trimmed; replies are untrusted model output)

- **Blank:**
  - "What are your opening hours on Sunday?" → "We are open from 10:00 AM to 4:00 PM on Sundays."
  - "How much is 50 dollars in euros?" → "I'm sorry, I don't have a tool to perform currency conversions…" (the tool is disabled, B-3).
  - "Are you open on Labor Day this year?" → "…closed on all US federal holidays, so we will be closed on Labor Day." (`demo_public_holidays`).
- **Knowledge assistant:**
  - "What's your refund policy?" → "According to the product FAQ, paid plans can be refunded in full within 30 days of the first payment…"
  - The Wikipedia request → declined (the tool is disabled, B-4).
  - "I need to talk to a person." → "I'll put you through to a colleague…" (`escalate_to_human`, plus an `escalation` event).
  - Follow-up: "Which NWS forecast office covers downtown Austin (30.2672, -97.7431)?" → answered via `demo_nws_point_lookup`.
- **Receptionist, the booking flow:**
  1. "I'd like to book a cleaning next Tuesday morning; I'm Dana Lee, 512-555-0100." → "…is your phone number 5-1-2, 5-5-5, 0-1-0-0?"
  2. "Yes… Cleaning, next Tuesday at 9am" → **`state=timeout`**, no reply (`request_form` was waiting for an on-screen submit, B-5).
  3. "Please go ahead and book it…" → `check_availability`, `book_appointment`, `table_append`, then "You're booked. We'll text a reminder the day before. Goodbye!" (flow end `done`).
  4. New chat: "Is the practice open on July 4th?" → "We are closed on July 4th for Independence Day."
  5. After the fix: "Where do I park?" → "…garage… entrance on 12th Street. We validate parking for the first 90 minutes."
- **Vision:**
  - "What can you do if I share my screen?" → "…read error messages, check status codes, walk through form fields…"
  - "Where is ZIP 78701?" → "ZIP code 78701 is located in Austin, Texas."
- **Phone:**
  - "What time do you close today?" → "…open until 6:00 PM Central Time on weekdays, and until 1:00 PM on Saturdays. We are closed on Sundays."
  - "Any weather alerts for Texas?" → "…an Air Quality Alert for the Houston, Galveston, and Brazoria areas."
  - The message for the manager → "…your name is Sam, and your number is 5 1 2, 5 5 5, 0 1 9 9…"
- **Lead qualification:**
  - "Our office ZIP is 98052…" → "I've noted you're with Contoso in Redmond…"
  - "How much does it cost?" → before the fix: "I don't have the specific pricing bands…" (`search_knowledge` returned "No relevant knowledge found.", B-2). After the fix: "…Team plan, which starts at 25 dollars per user per month."
  - The flow stayed on `start` in both sessions (B-6).
- **Survey:** the name, then "Four out of five… yes… onboarding was slow", then "Canada" → `demo_country_info`, `table_append` and `set_status completed`, then "Thank you for your time, Alex! … Goodbye!" (`end_call`).
- **Insurance:**
  - "My basement flooded… policy HO-4471-2210" → `lookup_policy` returned `found: false` (B-9), and the agent continued the intake.
  - "Were there flood alerts for Texas?" → "…an air quality alert in the Houston area, [but no] active flood alerts…"
  - "Is groundwater flooding covered?" → "…excluded… covered if you have an optional flood rider" (a `search_knowledge` hit on `policy-notes.md`).

## 4. Create/delete test (scratch run, $0.51)

`Demo — Scratch (delete me)` was created from `blank` (id `b263b68d02a74b3ab1b824416d9be73e`, slug `demo-scratch-delete-me`) and had one chat. The delete ladder then ran:

| Step | Result |
|---|---|
| `lkap_delete` | `needs_confirmation` |
| `lkap_delete` with `confirm` | `conflict` **409**: "agent still has sessions; delete them first, or archive and purge it" |
| `agent_archive` with `confirm` | ok |
| `lkap_delete` with `confirm` and `purge` | `deleted: true` |
| `agent_list` | shows it neither active nor archived |

This was the only delete in the run.

## 5. Browser check (Playwright Chromium 1243, `--use-fake-ui-for-media-stream --use-fake-device-for-media-stream`)

| Step | Result | Screenshot |
|---|---|---|
| (a) `/console/agents` | All 8 `Demo — ` agents are listed with the **Live** chip, which is the console's published label | `a-agents-list.png` |
| (b) Editor → Providers | Vision shows Avatar **Beyond Presence**. Survey shows Avatar **Simli**, key `…so27`, with participant "Demo avatar" (only the key's fingerprint is on screen) | `b-editor-vision-providers.png`, `b-editor-survey-providers.png` |
| (c) `/s/demo-knowledge-assistant` | The public pre-call card loads, with Start call and the description | `c-public-knowledge-assistant.png` |
| (d) Live **Bey**, `/s/demo-vision-assistant` | **Passed.** A remote avatar `<video>` went live at **1536×1024, 16 s after Start call**, speaking the greeting. The driver clicked END CALL at 24 s. Session `95d53626…`: web, **ended**, 21:22:57–21:23:15 (**18 s** of Bey time) | `d-bey-vision-precall.png`, `d-bey-vision-live.png`, `d-bey-vision-ended.png` |
| (d) Live **Simli**, `/s/demo-survey-intake-form` | **Failed (B-1).** No video within 60 s. The page showed "couldn't join the call — Agent joined the room but did not complete initializing". Session `c53d98aa…`: web, **failed**, 31 s. No Simli stream was created, so no Simli minutes were used | `d-simli-survey-live.png`, `d-simli-survey-ended.png` |
| (e) Connect dialog leg (closes V3-07-8) | Settings → AI agents → Connect an AI agent. Choices: name `v406-dialog-leg`, Local (stdio), **Builder**, `calls:write` left unchecked, warning acknowledged → Create key. The key step ("Your agent key") rendered with its snippet blocks; **no screenshot of that step, and the key was never printed**. Escape, then revoked from the keys table. The row reads "v406-dialog-leg · lkap_Tb8… · Builder scopes · Never used · **Revoked**". **Snippet rendered; key revoked.** Expiry was **7 days**: the dialog has no 1-day option (B-10) | `e1-connect-dialog-filled.png` (before create), `e2-keys-table-revoked.png` |

Every screenshot was checked. None shows a key, token or `.env` value, only prefixes (`lkap_Tb8…`, `lkap_mR-…`) and last-4 fingerprints. The run used about 18 s of Beyond Presence and 0 s of Simli.

## 6. Manifest and before/after diff

**Before** (`snap/before.json`):
- 12 agents;
- 3 KBs (`Intake playbook`, `Insurance policy lines`, `Policies`);
- 0 tools;
- 2 provider keys (`simli-avatar`, `bey-avatar`);
- 0 webhooks;
- 1 connection (`Default`, `lkap-agent`, status `unverified`);
- 2 API keys (`AI agent key`, active; `Test1`, revoked);
- no telephony objects and no calls.

**After** (`snap/final.json`):
- 20 agents, 18 KBs, 12 tools;
- provider keys, webhooks, connections, telephony and calls **identical** to before;
- 4 API keys: the two new ones are both **revoked**, and the two pre-existing ones are unchanged.

**Pre-existing rows changed: none** (agents, KBs, tools and provider keys compared field by field, `updated_at` included). **New rows outside the manifest: none.**

**Agents (8)**

| id | name | slug |
|---|---|---|
| `513dcaf34a6a42729d035fb9662f2675` | Demo — Blank agent | demo-blank-agent |
| `6969e8ce33c94bd2b4216cb31f1b0de6` | Demo — Knowledge assistant | demo-knowledge-assistant |
| `d52591f4860f4561bab3ed3e66be3370` | Demo — Receptionist | demo-receptionist |
| `7ccd189876a74adca561cf88e2bf7e64` | Demo — Vision assistant | demo-vision-assistant |
| `174fecb8788a441992c25fcd53086a46` | Demo — Phone agent | demo-phone-agent |
| `ae20189be9a54da79e41c656dc3c43a4` | Demo — Lead qualification | demo-lead-qualification |
| `ea0f9f2a68894da186315189227bfba9` | Demo — Survey / intake form | demo-survey-intake-form |
| `64c4bd62aca64826a42c1b794ed7a1bb` | Demo — Insurance claim intake | demo-insurance-claim-intake |

The throwaway agent `b263b68d02a74b3ab1b824416d9be73e` was created and deleted in the scratch run.

**Knowledge bases (15).** Items marked "seeded" were created by `agent_create` for that agent.

| id | name |
|---|---|
| `dbb544e4…` | Demo — Blank agent · House guide |
| `92f1c2a7…` | Demo — Blank agent · Membership plans |
| `acc75353…` | Knowledge assistant · Product FAQ (seeded) |
| `a31670dd…` | Knowledge assistant · Support playbook (seeded) |
| `f0f9f838…` | Demo — Knowledge assistant · Product FAQ |
| `51f481a0…` | Demo — Knowledge assistant · NWS API notes |
| `93ec3804…` | Receptionist · Practice info (seeded) |
| `3563c42c…` | Demo — Receptionist · Practice info (extended) |
| `d665847a…` | Demo — Vision assistant · What to look for |
| `61d405f8…` | Phone agent · FAQ (seeded) |
| `211aabd5…` | Demo — Phone agent · FAQ (extended) |
| `70c1638f…` | Lead qualification · Offer sheet (seeded) |
| `a9194613…` | Demo — Lead qualification · Customer stories |
| `fa1b5511…` | Demo — Survey / intake form · Survey guidelines |
| `74319436…` | Demo — Insurance claim intake · Policy notes |

**Tools (12)**

| id | name | host | enabled |
|---|---|---|---|
| `48dc31bc…` | demo_public_holidays | date.nager.at | yes |
| `fe2d9d32…` | demo_currency_rates | api.frankfurter.app | **no** |
| `917fd2c8…` | demo_wikipedia_summary | en.wikipedia.org | **no** |
| `f331e115…` | demo_nws_point_lookup | api.weather.gov | yes |
| `8922e6b7…` | check_availability (seeded, repointed) | httpbin.org | yes |
| `e2c4a0ad…` | book_appointment (seeded, repointed) | httpbin.org | yes |
| `cc029a0d…` | demo_zip_lookup | api.zippopotam.us | yes |
| `38aaf371…` | demo_weather_alerts | api.weather.gov | yes |
| `4de432e3…` | demo_company_summary | en.wikipedia.org | **no** |
| `a271af2c…` | demo_lead_zip_lookup | api.zippopotam.us | yes |
| `ee50d1ba…` | demo_country_facts | restcountries.com | **no** |
| `cbf899e9…` | demo_country_info | date.nager.at | yes |

Every tool has `allowed_hosts` set to exactly its one host, `timeout_s` 8 and `max_result_chars` 3000. This was checked at the end with `GET /v1/tools/{id}` for all 12; the repointed seeds kept `["httpbin.org"]`. Every host is in the R-V4-18 allowlist. The full ids are in `<scratchpad>/v406/manifest.json`.

**API keys (2, both revoked)**
- `v406-populate`: `9e1802db…`, prefix `lkap_mR-`.
- `v406-dialog-leg`: prefix `lkap_Tb8`.

**References to pre-existing rows, recorded rather than treated as foreign writes**
- `agent_create(template_id="insurance_claim")` seeded the new agent with the user's existing pack KBs `Insurance policy lines` (`57765f2c…`) and `Intake playbook` (`38a3079c…`). The seeder reuses them by name.
- The run's `agent_attach` re-passed those two ids, which writes the new agent's config only. Both KB rows are byte-identical before and after (`updated_at` 2026-09-18).
- `agent_create` also passed no `connection_id`, so the default connection was used; it was only read.

## 7. Bugs found (none fixed; mirrored in `_asks.md` #26–#36)

| # | Where | Exact call / repro | Expected | Actual | Severity | Blocked |
|---|---|---|---|---|---|---|
| B-1 | agent: `providers/special_cases.py::unwrap_nested_fields` / the api's resolved kwargs for nested secrets | Agent with `pipeline.avatar = {provider_id: "simli-avatar", credential_id: <simli key>, fields: {"simli_config.face_id": "cace3ef7-…"}}`; open `/s/demo-survey-intake-form` and Start call | The Simli avatar joins and publishes video | Worker: `failed to create simli session token server returned N/A and detail 'dict' object has no attribute 'create_json'` [livekit.plugins.simli], then `could not start the session`; session `c53d98aa…` `status=failed`, "start failed". `SimliConfig` reaches the plugin as a plain `dict`. **Diagnosis, from a code read (no runtime values inspected):** the api's `config_service._assign_nested` (line 251, called at 959–964) turns the dotted fields into **one non-dotted key**, `{"simli_config": {"face_id": …, "api_key": …}}`. The worker's `unwrap_nested_fields` groups only *dotted* keys, and wraps only `nested_model` fields whose *name has no dot*. Every Simli field name is dotted (`simli_config.*`), so `plain_name_to_model` is empty and the pre-nested dict passes through unwrapped. **Fix:** in `unwrap_nested_fields`, also build `nested_model(**value)` when a plain key equals a dotted outer name and its value is a dict (this affects Anam too, since it uses the same shape), plus a factory test fed with the api's real nested shape. Also, a failing avatar fails the whole call; there is no voice-only fallback | **blocks** the Simli avatar | step 10(d), Simli |
| B-2 | agent: `flow/runtime.py::kb_ids_for` (line 356) and `flow/state.py::ScopedKbClient`; api templates `receptionist`, `lead_qualification` | Create either flow starter, then `chat_send("How much does it cost?")` (lead) or `"…opening hours July 4th"` (receptionist) | `search_knowledge` and auto-inject reach the agent's `knowledge.kb_ids` (the seeded KBs) | `search_knowledge` → "No relevant knowledge found." in 1–2 ms (no lookup made), and replies lack KB facts. In flow mode the scope is `global.kb_ids + node.kb_ids`, and both starters leave `global.kb_ids` empty. The same api search (`POST /v1/knowledge-bases/70c1638f…/search`) returns the offer sheet at score 0.64. Setting the global node's `kb_ids` fixes it (verified in sessions `b1d4a56e…` and `9ccc8cd2…`). Fix: seed `global.kb_ids` with the seeded KB ids, or fall back to `knowledge.kb_ids` when a flow declares none | **degrades** (both flow starters lose their KB) | worked around in config |
| B-3 | coordinator: R-V4-18 allowlist | `tool_create_http(url="https://api.frankfurter.app/latest?from={{from}}&to={{to}}", dry_run_args={from: USD, to: EUR})`; `…restcountries.com/v3.1/name/{{country}}?fields=name,capital,region` with `{country: Canada}` | 200 | **301** twice each. Frankfurter now redirects to `api.frankfurter.dev/v1/…`; REST Countries v3.1 redirects to `files-03.restcountries.com/…/legacy.json`. The tool client never follows redirects, by design (`declarative.py:141`, `net_guard`), so both tools were disabled. Update the allowlist: `api.frankfurter.dev` (same project, MIT). REST Countries has no drop-in; `date.nager.at/api/v3/CountryInfo/{code}` worked | degrades | two card tools |
| B-4 | coordinator (R-V4-18) and the MCP `tools-http` concept doc | `tool_create_http(url="https://en.wikipedia.org/api/rest_v1/page/summary/{{title}}", headers={"User-Agent": "LKAP-demo/1.0 (LKAP V4-06 demo agent)"}, dry_run_args={title: "Weather_forecasting"})` | 200 | **403**: "Please respect our robot policy https://w.wiki/4wJS". Reproduced with plain `httpx`: a descriptive User-Agent **without contact info** is refused, while curl and urllib pass with the same header, so it is client fingerprinting. A User-Agent with a contact URL or email returns 200. The run did not invent a contact or send the user's email to a third party, so `demo_wikipedia_summary` and `demo_company_summary` stay **disabled**. The user can set a real contact in their headers and re-enable them | degrades | two card tools |
| B-5 | agent (`request_form` on the text channel) / mcp `chat_send` | Receptionist flow node `collect_booking` or the survey prompt calls `request_form` during `chat_send` | Text chat gets a reply (a verbal fallback, or the form auto-resolves) | The turn blocks until the form times out. Receptionist turn 2: `state=timeout`, no reply (about 85 s). Survey turn 3 stalled about 65 s, and the worker logged `speech not done in time after interruption, cancelling the speech arbitrarily`. Text chat has no way to submit a form | degrades | — |
| B-6 | api template `lead_qualification` / agent flow | `chat_start` on Demo — Lead qualification; turns "Sounds good." → "The company is Contoso and I'm the IT manager…" → pricing | `start→company→needs…`, variables extracted | `flow_ended {path: ["start"], variables: {}, missing_required: ["company"]}` in both sessions, `cac1895d…` and `9ccc8cd2…`. The default LLM (`google/gemma-4-31b-it`) never took the edge "the caller agrees". Receptionist edges worked with the same model. Cause not isolated (log only) | degrades | — |
| B-7 | mcp `me` (`discovery.py`) | `me()` with a ready worker (fleet `status=ready`, 33 providers) on a connection whose `status="unverified"` | Reports whether a worker is reachable (SKILL.md says it does) | `health.connections = {n: 1, ok: 0}`, which the model reported as "no worker currently reachable". Chats then worked. The field counts tested connections, not workers | cosmetic / misleading | — |
| B-8 | agent: text channel `end_call` | Survey session `ecd2baf7…`: the agent calls `end_call`; the MCP runs `chat_end` | Session summary posted promptly | `caller left, ending the job` at 21:10:39. The summary was posted at **21:11:25** (51 s after `end_call`) after `failed to send session event … room session transport is closed`. `session_get` meanwhile shows `status=active`, 0 turns | cosmetic | — |
| B-9 | api `templates/catalog/insurance_claim/template.json:26` / `packs/insurance_claim/policy_directory.py` | Sample prompt "My policy number is HO-4471-2210." | `lookup_policy` finds it | `found: false`. `POLICY_RECORDS` holds `H044721`, `AUTO90210`, `RNT3008` and `TRV7711` | cosmetic | — |
| B-10 | web `settings/snippets.ts` `AGENT_KEY_EXPIRY_OPTIONS` / card | The Connect dialog's expiry select | A 1-day option (card step 10(e)) | The options are 7, 30, 90 and 365 days; the run used 7 and revoked at once | cosmetic (card or UI) | — |
| B-11 | agent `platform_agent.py:440` | Acceptance (1)'s KB signal "the worker's `injected knowledge` line" | Observable on the user's worker | It is logged at `debug`, and the worker runs at INFO: 0 lines in the whole run | cosmetic | — |

**Other notes** (not bugs):
- `provider_catalog("simli-avatar", "avatars")` lists no faces (`source=vendor`), so the console picker can't offer Simli's documented presets. The user already knew this.
- A disabled tool that the instructions still name makes the LLM try it: the worker logged `unknown AI function demo_wikipedia_summary`.
- The flow warns `flow node references a tool this session does not have` for the disabled `demo_company_summary`.
- The live session panel's Status block reads "Not started" while the Bey call is live (`d-bey-vision-live.png`).

## 7a. Acceptance (card items 1–8)

| # | Item | Result | Evidence |
|---|---|---|---|
| 1 | 8 published agents, each with ≥1 KB holding a `ready` doc, ≥1 HTTP tool with a 200 creation dry run, 0 validation errors, and one ended text session with ≥2 user turns, `usage`, a `demo_`/repointed tool event and a KB signal | **Pass for 6; partial for 2** | §2 and `final-agents.json`. **Receptionist:** met across sessions but not in one. `f009894f…` has 3 user turns plus the repointed `check_availability`/`book_appointment` but no KB hit (B-2); `b1d4a56e…` has the KB signal but no tool call; `8858a221…` has `demo_public_holidays` but 1 turn. **Survey:** no KB signal was observed in `ecd2baf7…` (the tools and the 4 turns pass). Every other agent meets it in a single session |
| 2 | Vision has `bey-avatar`, Survey has `simli-avatar`, both with a resolved credential id and 0 validation errors | **Pass** in config. Simli then fails live (B-1) | §2; `validate` returns 0 errors |
| 3 | The throwaway agent is gone | **Pass** | §4 |
| 4 | The key is revoked and its config file deleted | **Pass** | §9 |
| 5 | Telephony and calls identical before and after; no diff outside the manifest | **Pass** | §6 |
| 6 | Step-10 screenshots exist with no secret; the dialog leg records "snippet rendered; key revoked" | **Pass**, except the Simli tile (B-1) | §5 |
| 7 | A run log per template (session, turns, cost, tool sequence), the bug table, the cost and the transcripts' location | **Pass** | §2, §7, §8 |
| 8 | No diff in `api mcp web agent contracts` | **Pass** | `git status --short` shows only the two V4-06 docs |

## 8. Cost (sum of `total_cost_usd` per stream)

| Run | Turns | $ | Claude session |
|---|---|---|---|
| probe (`me` only) | 3 | 0.24 | `bedd8a58-27a0-4457-8a86-b523639e6ae9` |
| blank | 25 | 1.04 | `d63ef989-fbf7-4a1e-9a3a-3a6ace56b67c` |
| knowledge_assistant | 25 | 0.97 | `0892804a-9824-470b-9c38-36adc4fcfac3` |
| knowledge_assistant_2 (substitute tool) | 18 | 0.74 | `3167838a-d2f8-43bf-8f45-2592103e220b` |
| receptionist | 39 | 1.81 | `72212fde-0a69-4cfa-8367-85ee227d4ab5` |
| receptionist_2 (flow KB) | 15 | 0.71 | `6e0afb7d-dafb-4baa-b25f-110d51ebf54e` |
| vision_assistant | 23 | 0.95 | `44136fcd-c51e-406f-8172-cfdd02d25da3` |
| phone_agent | 20 | 0.94 | `1d9a7bd5-80db-49ca-981f-e90c6333f6da` |
| lead_qualification | 28 | 1.56 | `1176d02e-6aec-431a-8eda-2db469824c37` |
| lead_qualification_2 (flow KB) | 17 | 0.79 | `2e9126c4-5daf-4fc4-8eda-3e5fb5654336` |
| survey_intake | 33 | 1.25 | `2c323be1-c9e9-4232-9c2e-2285bc2d7077` |
| insurance_claim | 22 | 1.02 | `d3a19337-11d5-45f4-bcbf-2ba85619e67b` |
| scratch (create/delete) | 15 | 0.51 | `f64d8bde-c928-4e67-bd66-38763a9724a2` |
| **Total** | | **$12.53** | cap $30 |

**MCP tool sequence per run** (counts, from `check.py`):
- **blank:** guide, me, agent_list, kb_list, tool_list, agent_create, agent_update, kb_create ×2, kb_add_document ×2, tool_create_http ×2, tool_dry_run, tool_update, agent_attach, agent_validate, chat_start, chat_send ×3, chat_end, agent_publish.
- **knowledge_assistant:** the same shape, plus a URL import; tool_update disables Wikipedia.
- **knowledge_assistant_2:** agent_get, tool_create_http, agent_attach, agent_update, agent_validate, chat ×2, publish.
- **receptionist:** agent_flow_validate ×2, agent_update ×2, tool_update ×3, tool_dry_run ×3, agent_attach ×2, chat_start ×2, chat_send ×4, session_events ×2, chat_end ×2, publish.
- **vision:** plus lkap_explain and provider_key_list, agent_update ×2.
- **phone:** the standard shape.
- **lead:** agent_flow_validate ×2, tool_create_http ×2, tool_update.
- **survey:** provider_key_list, lkap_explain, provider_catalog, agent_update ×5, tool_create_http ×2, chat_send ×4.
- **insurance:** tool_dry_run on the shared tool, no tool_create.
- **\*_2 follow-ups:** agent_get, flow validate, update, validate, chat, publish.
- **scratch:** agent_create, chat, lkap_delete ×3, agent_archive, agent_list ×2.

The LiveKit Inference usage of the test chats bills to the user's LiveKit project. Avatar usage was about 18 s of Bey and 0 s of Simli.

## 9. Cleanup

- **Keys:**
  - `v406-populate` was revoked with `DELETE` → 204; `GET /v1/api-keys` now shows `revoked_at`.
  - `<scratchpad>/v406/stdio.mcp.json` was deleted.
  - The dialog-leg key `v406-dialog-leg` was revoked in the UI.
- **The user's processes:** worker 27884, api 35129 and web 35228 kept the same PIDs, running continuously, and were never signalled; `/v1/health` is ok.
- **Git:** `git status --short` was clean before these two docs were written. The only repo changes from V4-06 are `docs/v4/_briefs/v4-06-populate.md` and the "Open — left by V4-06" section of `docs/v4/_asks.md`. HEAD moved `b712faa` → `9561de8` because V4-04 committed in parallel.
- **User-side mitigation, not done under R-V4-20:** until B-1 is fixed, `/s/demo-survey-intake-form` fails for every visitor. Remove the avatar in the Survey editor, or switch it to Beyond Presence, to keep that demo usable.
- **A judgment to confirm (coordinator owns the rule):** on Insurance, `agent_attach` re-passed the two pre-existing pack KB ids that the seeder itself had put in the config (§6). The card's literal rule says a foreign id in a write stops the run. The run treated it as a reference, since the KB rows are byte-identical, and continued.
- **What stays in the user's DB, as the user asked:**
  - the 8 `Demo — ` agents;
  - their KBs and tools;
  - their test and live sessions, including the one failed Simli web session `c53d98aa…`.

## Appendix: prompts (verbatim; each template prompt ended with the shared rules block)

Files: `<scratchpad>/v406/prompts/`. The shared rules block (`_rules.txt`) was appended to every template, follow-up and `*_2` prompt; the scratch and probe prompts stand alone.

### Shared rules block (`_rules.txt`)

```text
RULES FOR THIS RUN (follow exactly):
1. Scope. Work only on the agent named above, on knowledge bases named "Demo — …", on tools named "demo_…", and on the knowledge bases and tools that agent_create itself seeds for this agent. Never modify, attach, publish, archive or delete any other agent, knowledge base, tool, connection, key or webhook.
2. Idempotent. Call agent_list first; if an agent with exactly this name already exists, reuse it (agent_get) instead of creating another. Likewise reuse an existing knowledge base (kb_list) or tool (tool_list) with the exact name.
3. Forbidden tools and actions: api_request, lkap_delete, agent_archive, agent_versions restore, tool_create_mcp, session_rescore. Create no webhook, provider key or connection. No telephony at all: never place a call and never ask for a number, trunk, dispatch rule or transfer target.
4. Use the default connection (omit connection_id unless a tool requires it). The description of the agent must start with: "Demo agent created by V4-06 on 2026-09-25 through lkap-mcp."
5. Knowledge documents: write the text yourself, realistic and at least 400 words per document; add each with kb_add_document(wait=true) and confirm its status is ready.
6. HTTP tools: allowed_hosts is exactly the one host in the URL, timeout_s 8, max_result_chars 3000, give each parameter a clear description, always pass dry_run_args, and confirm the dry run returned HTTP 200. If a dry run does not return 200, try it once more with tool_dry_run; if it still fails, set that tool enabled=false with tool_update (do not delete it), report the status code, and continue.
7. Validate with agent_validate (and agent_flow_validate if the agent has a flow) and report errors and warnings. Test with chat_start, the chat_send turns listed, then chat_end. Publish with agent_publish only if validation has zero errors.
8. If a step fails, retry it once, then skip it and report the exact tool, arguments and error. Be efficient: no unnecessary reads.
9. Final answer: each step's outcome, each chat_send reply quoted (trimmed to about two sentences), then one JSON block {"agent_id", "slug", "kb_ids", "tool_ids", "chat_session_id", "validate_errors", "validate_warnings", "published", "problems": [...]}.
```

### `probe.txt`

```text
/lkap Call the lkap `me` tool once and report the workspace, the key's scopes, and whether a worker is reachable. Do nothing else.
```

### `blank.txt` (+ shared rules)

```text
/lkap Build a demo agent named "Demo — Blank agent" from the starter template_id "blank".
- Instructions (agent_update): a friendly front-desk concierge for a fictional co-working space "Northwind Commons" in Austin. It answers questions about opening hours, rooms, booking, access, printing, guests and membership plans, uses search_knowledge for facts, never invents prices, keeps answers to two or three short sentences, and uses the currency and holiday tools when asked. Also set the greeting to "Hi, welcome to Northwind Commons! Ask me about hours, rooms or memberships."
- Knowledge base "Demo — Blank agent · House guide": one document covering opening hours (weekdays 7am–9pm, Saturday 9am–6pm, Sunday 10am–4pm, closed on US federal holidays), the rooms (four meeting rooms with names and capacities, phone booths, a quiet floor), how to book, key-card access, printing, guests and the kitchen.
- Knowledge base "Demo — Blank agent · Membership plans": one document with three plans (Hot Desk $199/month, Dedicated Desk $349/month, Private Office from $899/month), what each includes, day passes, and the cancellation policy (30 days' notice).
- HTTP tool "demo_currency_rates": GET https://api.frankfurter.app/latest?from={{from}}&to={{to}} (parameters from and to, ISO currency codes), result_path "rates", dry_run_args {"from": "USD", "to": "EUR"}.
- HTTP tool "demo_public_holidays": GET https://date.nager.at/api/v3/PublicHolidays/{{year}}/US (parameter year, e.g. 2026), description "US public holidays for a year (the space is closed on federal holidays)", dry_run_args {"year": "2026"}.
- Attach both knowledge bases and both tools with agent_attach. Keep the default panel.
- Chat: (1) "What are your opening hours on Sunday?" (2) "How much is 50 dollars in euros?" (3) "Are you open on Labor Day this year?"
- Publish.
```

### `knowledge_assistant.txt` (+ shared rules)

```text
/lkap Build a demo agent named "Demo — Knowledge assistant" from the starter template_id "knowledge_assistant".
- Instructions: keep the template's instructions (agent_get first) and add one paragraph: the product is "Acme Meter", a smart energy meter with a companion app; when the caller asks about a general topic that isn't in the documentation, it may use demo_wikipedia_summary and must say the answer comes from Wikipedia.
- Knowledge base "Demo — Knowledge assistant · Product FAQ": one document with 25 new question-and-answer pairs about Acme Meter (plans, installation, app pairing, data export, API access on the Pro plan, battery, warranty, the 30-day refund policy, support hours Mon–Fri 8am–6pm CT, privacy). Keep the template's seeded knowledge bases attached (the seeded Support playbook stays as it is).
- Knowledge base "Demo — Knowledge assistant · NWS API notes": add one document by URL import: kb_add_document(url="https://www.weather.gov/documentation/services-web-api", wait=true). If the URL import fails, report the exact error, then add a short text summary of that page instead (at least 400 words about the National Weather Service API: endpoints /points, /gridpoints, /alerts, the User-Agent requirement, rate limits, GeoJSON output).
- HTTP tool "demo_wikipedia_summary": GET https://en.wikipedia.org/api/rest_v1/page/summary/{{title}} (parameter title: the Wikipedia page title with underscores instead of spaces, e.g. Weather_forecasting), headers {"User-Agent": "LKAP-demo/1.0 (LKAP V4-06 demo agent)"}, result_path "extract", dry_run_args {"title": "Weather_forecasting"}.
- Attach the new knowledge bases and the tool (keep the seeded knowledge bases attached). Keep the kb_citations panel block.
- Chat: (1) "What's your refund policy?" (2) "Give me a two-line summary of the Wikipedia page for 'Weather forecasting'." (3) "I need to talk to a person."
- Publish.
```

### `knowledge_assistant_2.txt` (+ shared rules)

```text
/lkap Follow-up on the existing agent "Demo — Knowledge assistant" (find it with agent_list; do not create a new agent). Its demo_wikipedia_summary tool is disabled (Wikipedia answers 403 to the platform's HTTP client); leave that tool as it is.
- Create HTTP tool "demo_nws_point_lookup": GET https://api.weather.gov/points/{{lat}},{{lon}} (parameters lat and lon: decimal latitude and longitude of a US location), description "National Weather Service point metadata for a US location: the forecast office, grid, nearest city and the forecast URL", headers {"User-Agent": "LKAP-demo/1.0 (LKAP V4-06 demo agent)", "Accept": "application/geo+json"}, result_path "properties", dry_run_args {"lat": "30.2672", "lon": "-97.7431"}.
- Attach it to the agent with agent_attach, keeping every knowledge base and tool already attached. Append to the instructions (agent_get first, keep the rest unchanged): "When a caller asks which National Weather Service office or forecast grid covers a US location, call demo_nws_point_lookup with its latitude and longitude, and explain the result using the NWS API notes."
- agent_validate, then a new chat: (1) "Which National Weather Service forecast office covers downtown Austin, at 30.2672, -97.7431?" (2) "What's your refund policy?" Then chat_end, and agent_publish again.
```

### `receptionist.txt` (+ shared rules)

```text
/lkap Build a demo agent named "Demo — Receptionist" from the starter template_id "receptionist" (a flow agent).
- Instructions: agent_get first. Keep the flow's global node text and append one sentence to the global node's instructions: "The practice is Acme Dental in Austin, Texas." Validate the changed flow with agent_flow_validate before saving it with agent_update.
- Knowledge: the template seeds a "Receptionist · Practice info" knowledge base for this agent. Create "Demo — Receptionist · Practice info (extended)" with one document (at least 400 words) that repeats the practice basics (hours Mon–Fri 8am–5pm, Saturday 9am–1pm, address 1200 Congress Ave, Austin) and adds parking (garage entrance on 12th Street, first 90 minutes validated), accepted insurance (Delta Dental, Cigna, Aetna, MetLife, Guardian; no Medicaid), new-patient paperwork, and the 24-hour cancellation policy. Attach it alongside the seeded one.
- The template seeds two placeholder HTTP tools for this agent (check_availability and book_appointment, pointing at example.com). Find them with tool_list, read each with tool_get to learn the definition shape, then repoint them with tool_update:
  * check_availability → GET https://httpbin.org/anything?service={{service}}&date={{date}}, allowed_hosts ["httpbin.org"], result_path "args", timeout_s 8, max_result_chars 3000.
  * book_appointment → POST https://httpbin.org/post (keep its JSON body_template with name, phone, service, time), allowed_hosts ["httpbin.org"], result_path "json", timeout_s 8, max_result_chars 3000.
  Then run tool_dry_run on each (check_availability {"service": "cleaning", "date": "2026-09-29"}; book_appointment {"name": "Dana Lee", "phone": "512-555-0100", "service": "cleaning", "time": "2026-09-29T09:00"}) and confirm HTTP 200.
- HTTP tool "demo_public_holidays": GET https://date.nager.at/api/v3/PublicHolidays/{{year}}/US (parameter year), description "US public holidays for a year; the practice is closed on federal holidays", dry_run_args {"year": "2026"}. Attach it, and add "demo_public_holidays" to the global node's tools list in the flow so the flow can use it (validate the flow again).
- Keep the table panel block.
- Chat: (1) "I'd like to book a cleaning next Tuesday morning; I'm Dana Lee, 512-555-0100." (2) Answer whatever it asks next so the booking can go through, e.g. "Yes, that's right. Cleaning, next Tuesday at 9am please." (3) "Is the practice open on July 4th?"
- Publish.
```

### `receptionist_2.txt` (+ shared rules)

```text
/lkap Follow-up on the existing flow agent "Demo — Receptionist" (find it with agent_list; do not create anything). Known platform behaviour: in flow mode search_knowledge only searches the knowledge bases listed on the flow's global node (kb_ids) plus the current node's kb_ids, and this agent's global node lists none, so every search returns "No relevant knowledge found".
- agent_get, then set the flow's global node "kb_ids" to exactly the agent's knowledge.kb_ids (the seeded Practice info plus "Demo — Receptionist · Practice info (extended)"). Change nothing else in the flow. agent_flow_validate, then agent_update with the new flow, then agent_validate.
- New chat: (1) "What are your opening hours on Saturday, and do you take Delta Dental?" (2) "Where do I park?" Then chat_end and agent_publish again.
- Report whether search_knowledge now returns passages (quote the replies).
```

### `vision_assistant.txt` (+ shared rules)

```text
/lkap Build a demo agent named "Demo — Vision assistant" from the starter template_id "vision_assistant", with a Beyond Presence avatar.
- Instructions: keep the template's (agent_get first) and add: "When the caller shows a document, read its title first. When asked where a US ZIP code is, use demo_zip_lookup."
- Knowledge base "Demo — Vision assistant · What to look for": one document (at least 400 words): a checklist for reading screens and camera views — error dialogs (title, code, the button text), serial and model numbers on labels, form fields and which are empty, dates and amounts, warning lights, and what to never guess.
- HTTP tool "demo_zip_lookup": GET https://api.zippopotam.us/us/{{zip}} (parameter zip: a 5-digit US ZIP code), result_path "places", dry_run_args {"zip": "78701"}.
- Attach the knowledge base and the tool. Keep the gallery panel block.
- Avatar: call lkap_explain("pipeline-modes") and agent_get to see where the avatar slot and avatar_options live in the config. Find the user's existing Beyond Presence key with provider_key_list (provider_id "bey-avatar"; its id is 2c997961d82a46e188063cfb1f994d39) — reference it by id only; never create a key. Set the pipeline's avatar slot to provider_id "bey-avatar" with that credential_id (use its stock avatar: no avatar_id field), and set avatar_options.participant_name to "Demo avatar", with agent_update, at the paths the config actually uses. Then agent_validate and make sure the avatar slot has zero errors.
- Chat (text chat drops the avatar, that's expected): (1) "What can you do if I share my screen?" (2) "Where is ZIP 78701?"
- Publish.
```

### `phone_agent.txt` (+ shared rules)

```text
/lkap Build a demo agent named "Demo — Phone agent" from the starter template_id "phone_agent".
- Instructions: keep the template's, including the keypad menu (agent_get first), and add: "The company is Acme Support in Austin, Texas. If the caller asks about severe weather or weather alerts, use demo_weather_alerts with the two-letter state code." Do not add any transfer target, number or dispatch rule.
- Knowledge: the template seeds a "Phone agent · FAQ" knowledge base. Create "Demo — Phone agent · FAQ (extended)" with one document of 20 question-and-answer pairs (at least 400 words): opening hours (Mon–Fri 8am–6pm CT, Saturday 9am–1pm, closed Sunday), address, returns, order status, shipping times, warranty, how messages are handled (callback within one business day), holidays, accessibility, languages. Attach it alongside the seeded one.
- HTTP tool "demo_weather_alerts": GET https://api.weather.gov/alerts/active?area={{state}} (parameter state: two-letter US state code), headers {"User-Agent": "LKAP-demo/1.0 (LKAP V4-06 demo agent)", "Accept": "application/geo+json"}, result_path "features", dry_run_args {"state": "TX"}.
- Attach the tool. Keep the transcript panel block.
- Chat: (1) "What time do you close today?" (2) "Are there any weather alerts for Texas right now?" (3) "I want to leave a message for the manager: it's Sam, 512-555-0199, please call me back about my order."
- Publish.
```

### `lead_qualification.txt` (+ shared rules)

```text
/lkap Build a demo agent named "Demo — Lead qualification" from the starter template_id "lead_qualification" (a flow agent).
- Flow: agent_get first. Keep the flow; make the global node name the product "Acme Meter" explicitly (append: "The product is Acme Meter, a smart energy meter and analytics platform. When the caller names their company, you may call demo_company_summary to learn what it does."), and add "demo_company_summary" to the global node's tools. Validate with agent_flow_validate, then save with agent_update. Do not create a webhook (that next step stays for the user).
- Knowledge: the template seeds a "Lead qualification · Offer sheet" knowledge base. Create "Demo — Lead qualification · Customer stories" with one document (at least 400 words) of three customer stories (a 12-site retail chain, a 40-person manufacturing plant, a university campus): problem, rollout, results with numbers, and the plan they chose. Attach it alongside the seeded offer sheet.
- HTTP tool "demo_company_summary": GET https://en.wikipedia.org/api/rest_v1/page/summary/{{company}} (parameter company: the company's Wikipedia page title with underscores instead of spaces), headers {"User-Agent": "LKAP-demo/1.0 (LKAP V4-06 demo agent)"}, result_path "extract", dry_run_args {"company": "Contoso"}.
- Note: Wikipedia may answer 403 to the platform's HTTP client; if demo_company_summary ends disabled, keep it attached anyway (the user will fix its User-Agent later) and remove it from the global node's instructions.
- HTTP tool "demo_lead_zip_lookup": GET https://api.zippopotam.us/us/{{zip}} (parameter zip: the lead's 5-digit US office ZIP code), description "City and state for a US ZIP code, to route the lead to the right regional specialist", result_path "places", dry_run_args {"zip": "98052"}. Add it to the global node's tools and append to the global node's instructions: "If the caller gives an office ZIP code, call demo_lead_zip_lookup and mention the city so the right regional specialist follows up." Validate the flow again.
- Attach both tools.
- Chat: (1) "We're a 20-person team at Contoso looking at this for Q4. I'm the IT manager." (2) "Our office ZIP is 98052. What does Contoso do, briefly?" (3) "How much does it cost?"
- Publish.
```

### `lead_qualification_2.txt` (+ shared rules)

```text
/lkap Follow-up on the existing flow agent "Demo — Lead qualification" (find it with agent_list; do not create anything). Known platform behaviour: in flow mode search_knowledge only searches the knowledge bases listed on the flow's global node (kb_ids) plus the current node's kb_ids, and this agent's global node lists none, so every search returns "No relevant knowledge found".
- agent_get, then set the flow's global node "kb_ids" to exactly the agent's knowledge.kb_ids (the seeded Offer sheet plus "Demo — Lead qualification · Customer stories"). Change nothing else in the flow. agent_flow_validate, then agent_update with the new flow, then agent_validate.
- New chat that follows the flow from the start: (1) "Sounds good." (2) "The company is Contoso and I'm the IT manager. Our office ZIP is 98052." (3) "How much does it cost for a team of 20?" Then chat_end and agent_publish again.
- Then session_get on that chat's session and report the flow path and the extracted variables if the session shows them, and whether search_knowledge returned passages (quote the replies).
```

### `survey_intake.txt` (+ shared rules)

```text
/lkap Build a demo agent named "Demo — Survey / intake form" from the starter template_id "survey_intake", with a Simli avatar.
- Instructions: keep the template's five questions (agent_get first) and add a sixth question, asked last: "Which country are you calling from?" After the answer, call demo_country_facts with that country and thank them with one fact from it (the capital or region). Include the country in the confirmed form and the table row.
- Knowledge base "Demo — Survey / intake form · Survey guidelines": one document (at least 400 words) on how to ask the six questions: neutral wording, one question at a time, accepting partial answers, never leading the respondent, how to handle "I don't know", reading answers back, privacy.
- HTTP tool "demo_country_facts": GET https://restcountries.com/v3.1/name/{{country}}?fields=name,capital,region (parameter country: an English country name), dry_run_args {"country": "Canada"}.
- If demo_country_facts ends up disabled because its dry run failed, also create HTTP tool "demo_country_info": GET https://date.nager.at/api/v3/CountryInfo/{{country_code}} (parameter country_code: the ISO 3166-1 alpha-2 code of the country, e.g. CA for Canada), description "Country facts: common and official name, region and neighbouring countries", dry_run_args {"country_code": "CA"}, and change the instructions to use demo_country_info (with the country's two-letter code) instead, thanking the respondent with one fact such as the official name or region.
- Attach the knowledge base and the working tool(s). Keep the form and table panel blocks.
- Avatar: call lkap_explain("pipeline-modes") and agent_get to see where the avatar slot and avatar_options live in the config. Find the user's existing Simli key with provider_key_list (provider_id "simli-avatar"; its id is 1b7eb6800d6b4a4890d6d4d661c70fd0) — reference it by id only; never create a key. You may call provider_catalog("simli-avatar", "avatars") once; if it lists no faces, use Simli's documented preset face "Tina", face id cace3ef7-a4c4-425d-a8cf-a5358eb0c427. Set the pipeline's avatar slot to provider_id "simli-avatar", that credential_id, and fields {"simli_config.face_id": "<face id>"}; set avatar_options.participant_name to "Demo avatar"; use agent_update at the paths the config actually uses. Then agent_validate and make sure the avatar slot has zero errors.
- Chat (text chat drops the avatar, that's expected): (1) "Sure, let's go — I'm Alex." (2) "Four out of five, and yes, I'd recommend it. Onboarding was slow; nothing else to add." (3) "I'm calling from Canada." (If it asks you to confirm the form, reply "Yes, that's all correct.")
- Publish.
```

### `insurance_claim.txt` (+ shared rules)

```text
/lkap Build a demo agent named "Demo — Insurance claim intake" from the starter template_id "insurance_claim" (the advanced example pack).
- Instructions: keep the pack's (agent_get first) and append: "When the caller mentions a storm or flooding, you may use demo_weather_alerts with the two-letter state code to check active alerts."
- Knowledge: keep the pack's two seeded knowledge bases. Create "Demo — Insurance claim intake · Policy notes" with one document (at least 400 words) that expands these lines into a page: Policy HO-3 covers sudden water damage from burst pipes up to $25,000; flood damage from rising groundwater or surface water is covered only with the optional flood rider (policy suffix -FR), with a $2,500 deductible and a $50,000 limit; every claim must be reported within 30 days of the loss; mitigation duties (stop the water, photos before cleanup, keep receipts); what adjusters need. Attach it alongside the seeded ones.
- HTTP tool "demo_weather_alerts" (reuse it with tool_list if it already exists, otherwise create it): GET https://api.weather.gov/alerts/active?area={{state}} (parameter state: two-letter US state code), headers {"User-Agent": "LKAP-demo/1.0 (LKAP V4-06 demo agent)", "Accept": "application/geo+json"}, allowed_hosts ["api.weather.gov"], result_path "features", dry_run_args {"state": "TX"}. Attach it.
- Keep the pack's notebook panel.
- Chat: (1) "My basement flooded last night after the storm in Austin; my policy is HO-4471-2210." (2) "Were there flood alerts for Texas?" (3) "Is groundwater flooding covered on my policy?"
- Publish.
```

### `scratch.txt`

```text
/lkap Create-and-delete test. Create an agent named "Demo — Scratch (delete me)" from template_id "blank" with the description "Demo agent created by V4-06 on 2026-09-25 through lkap-mcp. Throwaway for the delete test." (reuse it if it already exists). Run chat_start, one chat_send "Hello, just testing — reply with one sentence.", then chat_end.
Then delete it, and only it, following the platform's delete ladder. I, the user, give my explicit go-ahead to delete exactly this one agent ("Demo — Scratch (delete me)") and to archive it if needed; nothing else may be deleted or archived:
1. lkap_delete(kind="agent", id=<its id>) without confirm → expect needs_confirmation.
2. lkap_delete with confirm=true → expect a 409 conflict because it has a session.
3. agent_archive(<its id>, confirm=true).
4. lkap_delete with confirm=true and purge=true → expect deleted.
5. agent_list → confirm it is gone.
Do not call api_request; create no webhook, key or connection; no telephony. Report each step's exact result (code and message).
```
