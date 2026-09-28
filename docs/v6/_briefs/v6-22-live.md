# V6-22 live check — the claims intake starter and the default flip

Prerequisites. **No migration.** The contracts changed additively (`StarterTemplate` gained
`extraction`, `rules`, `tests`, `dataset_seeds`, `kits`; regenerated). Restart the **api** (the catalogue
and the seed path load at import), the **worker** (its `LKAP_PACKS` default changed) and the **MCP server**
(its docs and the `agent_create` template list changed); rebuild nothing on the web beyond the usual dev
reload (the web's only change is the console label of the pack's notebook panel). The PLAN's live rules apply:
`Demo — ` objects only, a Builder key minted for the run and revoked after, fictional names and
`example.com` addresses.

**The dev stack must keep listing the legacy pack during this run:** set
`LKAP_PACKS=packs.insurance_claim,packs.generic` on the api **and** the worker (the same value; the
supervisor hands the api's value to its pools) before restarting. Without it, the existing insurance agents
lose the pack's tools and the gallery hides the legacy starter (RUNBOOK §9.8). The worker must be told
explicitly: its own default is now `packs.generic`.

Needs (PLAN §5): a vision-capable LLM on the agent (the starter seeds Inference `google/gemini-3.5-flash`)
for the evidence step; an image slot with a stored key (`google-image-gen`) for the sketch; for §5, an
OpenAI-compatible model with a stored key for the persona and the judges (ask #236).

## 1. The eight `Demo — ` agents still validate (ask #235)

With the dev stack's `LKAP_PACKS` listing both packs: `agent_list()`, then `agent_validate(id_or_slug=…)`
for each of the eight `Demo — ` agents → no new error. The insurance ones (`pack_id: insurance_claim`,
`ui_panel_id: insurance_notebook`) validate exactly as before; `agent_get` shows the same `config_version`.

## 2. The starter from the gallery

1. Console → Agents → New agent: the gallery shows **Claims intake** (after Vision assistant) with the
   chips and an optional Google key badge. With the legacy pack listed, "Insurance claim intake" is still
   at the end, its description saying it is legacy.
2. Create it as `Demo — Claims intake`. Check:
   - Panel: the wide Notebook layout — status, the claim notebook (Notes, Still needed, Summary, Sketch),
     the drawing board inside the Sketch section, Pictures, the Record table, the Still needed checklist,
     the hand-over block.
   - Tools tab: one tool, `policy_lookup` (a lookup table). Lookup tables: **Demo — Policy directory**,
     six rows, ready. Creating the starter a second time reuses the same table.
   - Knowledge: "Claims intake · Policy lines", "Claims intake · Intake playbook" reach `ready`.
   - Tests tab: `fnol-golden`, `fnol-safety`, `fnol-evidence`, and the two kit cases.
   - `agent_validate` → no error; the version history has one version, "created from template
     claims_intake".

## 3. A voice session through the FNOL story (publish, `/s/<slug>`, camera on)

| Step | Say / do | Expect |
|---|---|---|
| 1 | (listen) | The greeting asks whether you and everyone else are in a safe place. |
| 2 | "Yes, we're fine. A pipe burst under my kitchen sink last night and soaked the floor." | Within this turn or the next: the notebook's Summary shows the claim and "home_water_damage"; the checklist lists the four water-damage documents and the missing facts. |
| 3 | "I'm Maya Singh, my policy is H0-44721." | One `policy_lookup` call; the Record table fills; the agent reads back "Maya Singh" and the homeowners policy; the status reads "Record checked". |
| 4 | "Is this covered?" | The agent says it can't confirm coverage; no promise of payment. Ask again later: the caveat again. |
| 5 | Point the camera at a wet floor or any object; "Can you note that?" | The agent says what it sees and calls `pin_frame`; the photo appears in Pictures with its caption. |
| 6 | "Can you sketch the kitchen for the adjuster?" | `generate_image` (needs the image key); a pen sketch in Pictures; the agent asks whether it looks right. |
| 7 | "Also my neighbour slipped on the wet floor and hurt her wrist." | The status turns "Safety review" (danger), the hand-over block shows the escalation, the agent mentions emergency services and a person taking over; the session timeline shows `rule_fired safety_first` and an `escalation` event. |
| 8 | Give the address and a call-back number | A "Hand-off: …" note appears in the notebook's margin, the status reads "Ready for an adjuster", and the agent summarises in two sentences and writes the hand-off into Notes (`notebook_write`). |

Also by typing in a second call: "AUTO-11111, I was rear-ended" → the agent reads back a lapsed policy and
the status reads "Policy needs review".

## 4. The legacy agent still runs

Open the flagship's session page (`demo-insurance-claim-intake`): the pack's notebook renders exactly as before (fields, photos, sketch card, packet dialog). A call still runs the pack's tools (`lookup_policy`, `sync_claim_packet`, `pin_evidence_photo`, `draw_incident_sketch`).

## 5. The golden cases through the test runner (optional)

Give the claims agent a QA model and a workflow model with a stored OpenAI-compatible key, then
`agent_tests_run(id_or_slug=…, case_ids=["fnol-golden", "fnol-safety", "fnol-evidence"])` → the three
cases run against the worker; `fnol-golden` shows `policy_lookup` answering from its fixture row (no
lookup is made), `generate_image` and `notebook_write`; `fnol-evidence` in a text chat may report that no
camera view is available — that is an acceptable pass for its first expectation.

## 6. Clean-up

Delete the `Demo — Claims intake` agents and the `Demo — Policy directory` table when finished (a table
still used by a tool refuses to delete: delete the agents first); revoke the Builder key. Leave
`LKAP_PACKS` listing both packs on the dev stack while the insurance `Demo — ` agents exist.
