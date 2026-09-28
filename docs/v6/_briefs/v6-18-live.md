# V6-18 live check — tool kits and the generic catalogue

Prerequisites: the api restarted (the new routes and the kit catalogue load at import); the MCP
server restarted (`kit_list`, `kit_add`); the worker restarted only if it runs anything older than
V6-16/V6-17 (kits add ordinary tool rows, blocks, rules and extraction fields the worker already
understands — no worker code changed). No migration. The PLAN's live rules apply: `Demo — `
objects only, a Builder key minted for the run and revoked after, fictional names and
`example.com` addresses.

## 1. The catalogue

`kit_list()` → eight kits in order: `record_lookup`, `case_ticket`, `structured_intake`,
`verify_identity`, `payment_esign_link`, `notify_escalate`, `sheet_crm_log`, `booking`.
`kit_list(kit_id="record_lookup")` shows `record_lookup` / `record_results` / `record_found` and a
snippet with no link and no key name.

## 2. `record_lookup` on a lookup table (the card's first check)

1. Upload the `Demo — Policies` CSV of `v6-16-live.md` §1 (or reuse it).
2. `agent_create(name="Demo — Kits", pack_id="generic")`.
3. `kit_add(kit_id="record_lookup", agent_id=…, variant="dataset", dataset_id=…,
   key_columns=["policy_number"], block_prefix="policy", dry_run=true)` → `changes` lists the tool
   `policy_lookup`, the block `policy_results`, the rule `policy_found`, the snippet and the test case;
   `validation.ok` is true and nothing changed (`agent_get` shows the same `config_version`).
4. The same call without `dry_run` → one new version; `agent_validate` has no error. The same call
   again → every change `exists`, the version unchanged.
5. Test chat: "My policy number is PD-1001, what cover do I have?" → one `policy_lookup` call; the
   `Record` table on the panel fills without a model turn; the reply names "Comprehensive".
6. `agent_tests_run(id_or_slug=…)` → the kit's case runs with the fake rows (no lookup is made).

## 3. `notify_escalate`

1. `kit_add(kit_id="notify_escalate", agent_id=…)` (no key) → `notify_team` `skipped` with the note;
   live extraction turned on with `needs_person`; the rule `escalate_hand_over`.
2. Test chat: "I want to talk to a person right now." → the extraction sets `needs_person`, the rule
   fires (`rule_fired` in the session timeline), the status reads "Handing over", the hand-over block
   shows the request and the model says it is bringing in the team.
3. Optional, with a `Demo — ` webhook receiver: an `http-tool-secret` key holding
   `TEAM_WEBHOOK_URL`; `kit_add(..., block_prefix="esc2", secret_key_id=…)` sets Team notifications;
   the escalation posts one message.

## 4. `case_ticket` through Composio (only after the V5 Composio walk)

With the stored Composio key and a low-risk connected helpdesk app (Zendesk, Jira or Linear in a
sandbox): `kit_add(kit_id="case_ticket", agent_id=…, variant="composio", connection_id=…,
block_prefix="helpdesk", dry_run=true)` → the predicted tool name; then without `dry_run`. **Verify the
action slugs** (ask #143): if Composio refuses one as "not actions of …", pass the app's own slug in
`actions=[…]` and record it on the ask. A Builder key is refused (403); an admin key succeeds.

## 5. Sign-in servers and keys (spot checks)

- `kit_add(kit_id="case_ticket", variant="mcp_linear", …)` → an MCP server row with sign-in auth and
  the note to sign in; the Tools tab shows "Sign in".
- `kit_add(kit_id="record_lookup", variant="rest", settings={"base_url":
  "https://records.example.com/api/records"}, …)` → the note "Added without a key …"; the stored
  tool has no Authorization header and `allowed_hosts: ["records.example.com"]`.
- `settings={"base_url": "https://10.0.0.5/api"}` → 422 naming the private network.

## 6. Clean-up

Delete the `Demo — Kits` agent (its kit tools are owned by it and go with it), the dataset if made for
this run, any Composio tools picked (`lkap_delete(kind="tool", …, confirm=true)`); revoke the Builder
key.
