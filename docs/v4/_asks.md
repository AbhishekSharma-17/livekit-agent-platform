# v4 cross-package asks

One line per change a package needs in a file it does not own (`PLAN-V4.md`
"Exclusive ownership"). Format: **owner package** — file — the exact edit — who
asked — status. The coordinator's decisions here are binding.

Created by V4-01.

## Open — left by V4-01 (Starter templates: contracts, catalogue, api, MCP)

| # | Owner | File | Edit | Asked by | Status |
|---|---|---|---|---|---|
| 1 | coordinator (no owner this wave) | `mcp/src/lkap_mcp/docs/__init__.py` | `RECIPE_NAMES += "start-from-template"` (after `"connect-livekit"`). This loader keeps its own copy of `content.RECIPES`; without the name, `test_docs_lint.py`'s kebab-token check fails on every doc that backticks the new recipe, and `loader.recipe("start-from-template")` raises. | V4-01 | **Applied by V4-01** (one line, needed for the MCP gate). Coordinator: confirm, or revert together with the recipe. |
| 2 | V4-03 or whoever next owns the MCP prompts | `mcp/src/lkap_mcp/docs/prompts/build_agent.md` | Lines 11–12 still steer to `pack_id="insurance_claim"` / `pack_id="generic"`. Suggested: pick a starter from `lkap://templates` and call `agent_create(template_id=...)`; name the `start-from-template` recipe. Both old forms still work (R-V4-3), so this is wording, not a break. | V4-01 | Open |
| 3 | coordinator | `api/src/lkap_api/auth/roles.py` | Optional: `_Rule("/v1/templates", Requirement("viewer", "agents:read"), Requirement("builder", "agents:write"))` next to `/v1/packs`. V4-01 did **not** need it: `templates/router.py` declares `require("viewer", "agents:read")` itself (the pattern `fleet.py` and `connect.py` use), because without a rule the table fails closed to `admin` + `*` and builder/read-only keys would get 403 on `lkap://templates`. Add the rule only if the policy table should stay the single place to read route requirements. | V4-01 | Open (no action needed) |
| 4 | V4-02 (console) | `web/src/components/console/agents/create/**` | Not an edit request, a contract note: `StarterTemplate.voice` and `.knowledge` are serialised with **only the fields the starter sets** (a `field_serializer` in `lkap_contracts/templates.py`, so a dump re-validates without tripping the "no `voice.greeting` / `knowledge.kb_ids`" validator). In the `.d.ts` both are `VoiceConfig`/`KnowledgeConfig` with every field optional, so treat absent fields as "keeps the pack's value", never as the model default. | V4-01 | FYI |

## Open — left by V4-02 (Console: the New agent dialog and template gallery)

| # | Owner | File | Edit | Asked by | Status |
|---|---|---|---|---|---|
| 5 | coordinator (Fable ruling) | `docs/v4/PLAN-V4.md` V4-02 acceptance | The card expects a blank agent to land on `?section=providers&from=blank`, but `TEMPLATES.md` §6.4 lands on `<first next_steps[].section ?? "providers">`, and `blank`'s first next step is "Write the instructions" (§5.1), which gives `?section=instructions`. V4-02 follows §6.4 (no special case for blank) and its test asserts `instructions`. Rule on it: keep §6.4 and amend the card's example, or reorder `blank`'s next steps in the catalogue (V4-01's file). | V4-02 | Open |
| 6 | coordinator | `web/src/components/console/overview/{setup-checklist.tsx,probes.ts}` | Outside the V4-02 card, edited on the coordinator's instruction: "A LiveKit connection is tested" is ticked only by a connection whose `status == "ok"` (it was ticked by any saved connection, including an "Unverified" one), and the checklist's "New agent" action opens the dialog (needed for the card's "nothing links to `/console/agents/new`" grep). | V4-02 | Applied, please confirm |
| 7 | V4-02 follow-up / coordinator | `docs/v4/_briefs/v4-02-walk.md` | The card's live walk (create `knowledge_assistant` and `phone_agent` on the dev stack) was **not** run: this session was told the dev api must stay read-only. Screenshots with the create POST stubbed stand in for it. Run the walk on a scratch api when one is available. | V4-02 | Open |
