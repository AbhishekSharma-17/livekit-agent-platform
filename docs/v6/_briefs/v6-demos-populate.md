# V6-demos — populate the dev instance with V6 demo agents: run log

**Result.** Twelve old test agents are archived, not deleted. **Six new `Demo — ` agents are built, validated with 0 errors, chat-tested and published.** All of it was done through headless Claude Code and the `lkap` MCP server, the way V4-06 did it. Each agent exercises the V6 capabilities:
- notebook, drawing board, signature, chart, timer, code and cart blocks;
- lookup tables and the `dataset` tool kind;
- tool kits;
- live extraction and rules;
- a flow `tool` step.

**Test runner.** `agent_tests_run` works with the current providers (#236 did not block): 1 of 6 agents passed every case, 7 of 16 cases passed.

**Main findings.**
- With `google/gemini-3.5-flash` on OpenRouter, **live extraction times out on every turn** (2 s budget), so rules that depend on extracted values never fire in chat.
- The same model **sends strings where panel tools expect objects** on its first call.

**Cost and safety.**
- Headless cost **$20.79**. A first attempt cost $0: the CLI was logged out and the user logged it back in.
- Pre-existing rows changed outside the 12 archives: **none**. New rows outside the manifest: none.

- **Date:** 2026-09-29, 07:24–08:05 UTC.
- **Driver:** Opus 5.5, as a subagent of the V6 coordinator.
- **Headless runs:** `claude -p` on sonnet (`claude-sonnet-5`).
- **Target:** the user's dev api on `127.0.0.1:8080`. The user's running `lkap-agent` worker served every chat.
- **Restarts:** none, of the api, the web or the worker.
- **Scratch:** `<scratchpad>/v6demos/` holds:
  - `prompts/*.txt`;
  - `logs/*.jsonl` (the stream-json transcripts) and `logs/runs.log`;
  - `snap/`;
  - `manifest.json` and `costs.json`;
  - the scripts `run.sh`, `check.py` and `v6.py`;
  - `data/*.csv`.

## 1. Protocol as run

| Rule | How it was met |
|---|---|
| Key | The coordinator minted a temporary Builder-scope agent key (prefix `lkap_v-m`, expiring 2026-09-29T22:19Z) and wrote it into `<scratch>/v6demos/stdio.mcp.json`. This driver only passed that file's path to `--mcp-config`; it never read, printed or copied the file. The file's mtime, size and mode are unchanged, from start to end. The key is **not revoked yet**; that is the coordinator's step |
| Headless flags | `claude -p "<prompt>" --permission-mode default --allowedTools mcp__lkap Skill ToolSearch --tools Skill,ToolSearch --strict-mcp-config --mcp-config <scratch>/stdio.mcp.json --output-format stream-json --verbose --model sonnet --max-budget-usd 4 --max-turns 60`, from cwd `<scratch>/v6demos/project` with the skill installed by `scripts/install_claude_skill.sh --project`. Every prompt is prefixed `/lkap`. The archive run had a $1.5 / 30-turn cap, the verify run 70 turns and the polish run a $2.5 / 40-turn cap |
| Extra guard (added over V4-06) | Every run passed `--disallowedTools` for `lkap_delete`, `api_request`, `dataset_delete`, `memory_forget`, `memory_purge`, `connection_fleet`, `session_rescore`, `tool_create_mcp`, `session_whisper` and `tool_create_from_template`. The build runs also blocked `agent_archive` and `tool_create_http`. The verify run blocked every config write (create, update, publish, attach, kits, datasets, tools, KBs, `agent_versions`), and the polish run blocked everything except `agent_update`, `agent_publish` and chat. Every run's `system/init` shows `lkap` connected with 67 or fewer lkap tools (77 = `visible.builder`), permission mode `default`, and **no permission denials** |
| Gate | `check.py <step>` ran after every run. It takes a read-only snapshot of agents (including archived), KBs, tools, datasets, provider keys, webhooks, connections, API keys, telephony and calls, and diffs it against `snap/before.json`. It allows only the 12 archive slugs to change, and only `archived_at` and `updated_at`. It records new ids in `manifest.json` and scans every write `tool_use` for ids outside the manifest. **FOREIGN: none after every run**; no `CHECK id` alerts |
| No telephony, webhooks, provider keys or connections | Telephony and calls are identical before and after. The 4 provider keys, 0 webhooks, 2 connections and 10 API keys are unchanged. All six agents reuse the `openrouter-*` credential already used by Demo — Vision assistant, **by id only** |
| No new vendor keys | No Composio, Cal.com, SMS or external webhook. `notify_escalate` was added with no credential: the kit reports `notify_team` as *skipped*, the built-in hand-over still works and the team is not messaged. The claims starter's `google-image-gen` slot was replaced by the existing `openrouter-image-gen` reference |
| Never deleted | No delete and no purge. The 12 archives are reversible |

## 2. Task 1: the archive

**Run and outcome.** One run, $1.00. It called `agent_archive(confirm=true)` for exactly these 12 agents:

| Slug | Id |
|---|---|
| `smoke-generic` | `a5c70861d8e142b2baa91a380130f478` |
| `e2e-generic-f48eaaeb` | `72fdced7f2be4b1294cdfbf97c58dc5a` |
| `e2e-generic-64bba5f2` | `561c657bfdf9426aab8b18dbccd0ea06` |
| `smoke-vision` | `62f8655cb1464286a4fbec4e3155f6d0` |
| `smoke-kb` | `33ac3f50af524ee9810218ad167e74f1` |
| `e2e-generic-71dc3c1f` | `52e958d9f775454b8159583f8385846d` |
| `e2e-insurance-bf8f95` | `d422ef4b950a43c8ba9601a5cb2e762e` |
| `stage9-insurance` | `2f473d966212459498b208727cf96063` |
| `e2e-generic-7ff4e0df` | `2c8b1bb79121435ab6fbb89b651d698b` |
| `e2e-insurance-7c593e` | `65d72403232844c0aa7ec0186689804a` |
| `test` | `6fa54412be4649c0a675751aece6bf90` |
| `knowledge-assistant` | `a30f26ef485348ac9a512d9b0751e3e8` |

**Verification.** `agent_list(archived=false)` returns exactly the eight earlier demos (plus the new six after Task 2); `agent_list(archived=true)` returns exactly these 12. The gate saw only `archived_at` and `updated_at` change on those 12 rows, and nothing on the eight V4-06 demos.

## 3. Task 2: the six agents

**Common setup.**
- **Pipeline:** all six copy Demo — Vision assistant's cascaded OpenRouter pipeline, with no avatar:
  - speech-to-text: `openai/gpt-4o-mini-transcribe`;
  - LLM: `google/gemini-3.5-flash`, which can see pictures, as the boards, handwriting and camera evidence need;
  - text-to-speech: `google/gemini-3.8-flash-tts`, voice Fenrir;
  - image generation: `google/gemini-3.1-flash-image`.
- **Tools:** `tools.max_tool_steps` is 6.
- **Connection:** Default.
- **Description:** each starts "Demo agent created by V6-demos on 2026-09-29 through lkap-mcp."
- **Warnings:** `agent_validate` reports **0 errors** for all six. The warnings are the inherited OpenRouter latency tips, `language: 'multi'` and, where the panel has no gallery, "picture model set but no gallery block".

### The agents at a glance

| Agent | Slug / id | Cost |
|---|---|---|
| Demo — Claims intake | `demo-claims-intake` / `8549b64ad4f7436f8ceb60f03a26c76d` | $1.81 |
| Demo — Order & checkout | `demo-order-checkout` / `c181a4b3ceb341238d021fda8142ed1e` | $2.14 |
| Demo — Sales briefing | `demo-sales-briefing` / `34535b0c7d9b436780ac68b2b4a2e5e7` | $2.61 |
| Demo — Coding tutor | `demo-coding-tutor` / `ceb7b9eb6e5a408590a952e3afaaf5a9` | $3.42 |
| Demo — Support desk | `demo-support-desk` / `766ef0f4ad824465998216b509becfa9` | $1.93 |
| Demo — Field inspection | `demo-field-inspection` / `c41ea40ce1324ea795e80cdbf8e8bded` | $2.08 |

### Demo — Claims intake

- **Built from:** the `claims_intake` starter.
- **Panel:** the Notebook preset: `claim_notebook`, `sketch_board` (canvas), gallery and status. The kits add a `policy_results` table, a checklist and a handoff block.
- **Lookup table:** `Demo — Policy directory`, seeded by the starter (6 rows).
- **Kits:** `record_lookup:policy`, `structured_intake:claim`, `notify_escalate:escalate`.
- **Extraction and rules:** 15 extraction fields, 14 rules.
- **Knowledge bases:** 2 seeded, both ready.
- **Tests:** the three golden cases.
- **Camera:** on.
- **Chat `27a5bca9…`:** `policy_lookup` found the record (H0-44721), the `policy_found` rule fired, and `policy_results`, the checklist and `claim_notebook` all updated. The coverage question got the caveat: "I can't confirm coverage myself, as an adjuster will need to review…".

### Demo — Order & checkout

- **Built from:** the blank generic pack.
- **Lookup table:** `Demo — Product catalog` (15 rows; keys `sku` and `name`).
- **Kits:** `record_lookup:product` (`product_lookup`, max_rows 5), `notify_escalate`.
- **Panel:**
  - `order_cart`: a cart in USD;
  - `terms_signature`: a signature with fixed demo-terms wording, decline allowed;
  - `product_results`, status and handoff blocks from the kits.
- **Chat `905b9957…`:** `product_lookup` and `cart_set` ran and the cart was shown.

### Demo — Sales briefing

- **Built from:** the blank generic pack.
- **Lookup table:** `Demo — Sales metrics` (12 rows; keys `region` and `quarter`).
- **Kit:** `record_lookup:metric` (`metric_lookup`, max_rows 20). Its `metric_results` table is the table display.
- **Panel:**
  - four chart blocks: `revenue_bars` (bar), `trend_line` (line), `target_gauge` (gauge), `headline_number` (number);
  - a `charts_layout` block (tabs) holding the four charts;
  - a `pitch_timer` block (countdown, maximum 600 s);
  - a `talking_points` notebook with points and actions sections.
- **Build chat `fbbd97fd…`:** `metric_lookup`, `show_chart`, `notebook_write` and `start_timer` all ran.
- **Polish chat `8d74a56c…`:** `metric_lookup`, then four `show_chart` calls, with the first retried once. The reply read "…West beat its target, $405,000 against $400,000…".

### Demo — Coding tutor

- **Built from:** the blank generic pack.
- **Panel:**
  - `code_view`: code;
  - `diagram_board`: a canvas the caller can draw on;
  - `learner_notes`: a notebook on grid paper in a handwritten font, with notes, a practice list and a sketch ink section showing the board; the caller can write and draw;
  - `practice_timer`: a timer.
- **Tools:** `search_knowledge` disabled (the agent has no knowledge base).
- **Chat `6c261e74…`:** `show_code`, `clear_canvas`, `draw_on_canvas` (it errored first) and `notebook_write` ran.
- **Verify chat:** `start_timer` ran (5 min).

### Demo — Support desk

- **Built from:** the blank generic pack.
- **Lookup table:** `Demo — Support customers` (12 rows; keys `phone` (phone) and `email` (email)). One key alone matches.
- **Kits:** `record_lookup:customer` (no flow anchor), `structured_intake:intake` (notebook), `notify_escalate`.
- **Panel:** a `customer_card` details block and a checklist.
- **Extraction and rules:** 8 extraction fields, including `issue_summary` and a `severity` enum; 5 rules, including `severity_critical`, which escalates, and `severity_high`.
- **Flow:** `start` → **tool step** `lookup_caller` (`customer_lookup` with `phone={{ ctx.caller_phone }}`). Its outcomes: `ok` → `found`, `empty` → `not_found`, `error` → `identify`. From there the flow goes on to `done`.
- **Chat `7b807864…`:** there is no caller number on text, so the tool step took `error` → `identify`, by design. The model then called `customer_lookup` with the email, `go_to_found`, `set_details` and `escalate_to_human`.

### Demo — Field inspection

- **Built from:** the blank generic pack.
- **Panel:**
  - a status block;
  - `inspection_notebook`: legal paper in a handwritten font, with field notes, a summary details section and a site-sketch ink section showing `site_sketch`; the caller can write and draw;
  - `site_sketch`: a canvas;
  - `inspection_checklist`: a checklist;
  - `evidence`: a gallery for `pin_frame` photos;
  - `inspector_signoff`: a signature.
- **Camera:** on.
- **Extraction:** 5 fields (`site_address`, `area_inspected`, `hazards_found`, `hazard_severity`, `photos_taken`), with `still_needed` set to checklist.
- **Rules:** 5 (electrical, moisture, gas → instruct, high severity, in progress).
- **Tools:** `search_knowledge` disabled.
- **Build chat `7cb6babd…`:** `notebook_write`, `set_checklist`, `check_item` and `set_status` ran.
- **Polish chat `46a7ae0e…`:** `draw_on_canvas` succeeded on its retry and `site_sketch` updated. The reply read "I have sketched the kitchen on your board with the leak marked…".

**Created objects**

| Kind | Items |
|---|---|
| Lookup tables | `759527720d214d9ba60689f8aea81f95` Demo — Policy directory (starter-seeded); `c4ca4978525a4ce3995d4e4ee705d57b` Demo — Product catalog; `93eb002c6b8c41ec99f886d53fa4a755` Demo — Sales metrics; `88b8eed58acc44e0b77968fec0d79c3e` Demo — Support customers |
| Tools (`dataset` kind, created by kits) | `966eb29a…` `policy_lookup`; `eff6533a…` `product_lookup`; `cfe5be9e…` `metric_lookup`; `aa5cb9a0…` `customer_lookup` |
| Knowledge bases (seeded by the claims starter) | `a97dea24…` Claims intake · Policy lines; `f257d734…` Claims intake · Intake playbook |

**Text chat cannot exercise:** `request_signature`, `pin_frame` and the camera, `read_canvas` of the caller's drawing, and `generate_image`. All of them need the web page, and the models said so.

## 4. Task 3: verification

**Verify run.** $3.24. It ran `agent_validate` on all six, `agent_flow_validate` on the support desk (both 0 errors), and one `agent_tests_run` per agent, polled to completion.

| Agent | Test run | Cases |
|---|---|---|
| Claims intake | `fd87b515…` failed | `fnol-golden` failed: no speech for several turns. `fnol-safety` failed: escalated without looking the policy up first. `fnol-evidence` failed: the model spoke its reasoning aloud |
| Order & checkout | `dfae59af…` **passed** | 3 of 3. The transcript still shows two `cart_set` argument errors before a good call |
| Sales briefing | `997267f6…` failed | `charts_region` failed (agent_timeout, no reply); `timed_pitch` passed. The polish run came after this test run and fixed the chart turn in chat. The tests were not re-run |
| Coding tutor | `a30280dd…` failed | `shows_code` passed; `practice_round` failed: the timer was started, then stopped at once |
| Support desk | `a584423b…` failed | `wants_a_person` passed; `known_customer_by_email` failed: `go_to_done` came before the caller's refund question was answered |
| Field inspection | `aacc95d9…` failed | `records-a-hazard` and `gas-smell` failed: the rule-driven checklist items and the gas instruction never fired (see F-1). The polish run came later; the tests were not re-run |

**Console.** A read-only look at `http://127.0.0.1:3000/console/agents` in the built-in browser. All six new agents show the **Live** chip. See F-5.

## 5. Findings (not fixed; no repo code was changed)

- **F-1 (degrade): live extraction times out on every turn.** This happens with `google/gemini-3.5-flash` via OpenRouter when `workflow_llm` is empty. The `extraction` event shows `status: timeout`, about 2,000 ms, and `changed: []` in the support desk and field inspection chats. So `severity`, `hazards_found` and the other fields never land, and **the rules that depend on them never fire**: `severity_*`, the moisture and gas rules. Rules on tool outcomes do fire (`policy_found`, `metric_lookup`). The 2 s budget is fixed (D-V6-24). A faster `workflow_llm` might help, but it was not tried, because the brief said to reuse the demos' pipeline.
- **F-2 (degrade): the model sends strings where panel tools expect objects.** Gemini 3.5 Flash via OpenRouter often sends strings or flat lists where a tool expects objects:
  - `draw_on_canvas` shapes: "Input should be a valid dictionary or instance of ShapeIn";
  - `notebook_write` items: `label` missing;
  - `cart_set` lines, `show_chart` points and `set_details` items.

  It usually recovers on a retry, but each retry spends a tool step. The turn can then end with no reply, which is the likely cause of several test failures. The polish run added a one-line JSON example per tool to the field inspection and sales briefing instructions. After that, the retry succeeds and a reply comes back. The tools' own descriptions could carry an example.
- **F-3 (degrade): `search_knowledge` wastes tool steps.** It is registered on agents with no knowledge base, and the model calls it again and again looking for tool help ("No relevant knowledge found"), using up `max_tool_steps`. It was worked around per agent with `tools.builtin_disabled`. The platform could skip registering it when the agent has no knowledge base.
- **F-4 (cosmetic): OpenRouter Gemini sometimes speaks its reasoning.** Its reasoning text sometimes arrives as the spoken reply (claims chat and `fnol-evidence`). This is inherited from the pipeline the brief said to copy.
- **F-5 (UX): archived agents look active in the console.** `/console/agents` under "All" still lists the 12 archived agents with no archived marker, and 10 of them keep the **Live** chip. The api's `GET /v1/agents` returns every agent unless `archived=` is passed, and the console list neither filters nor marks archived rows. Whether an archived-but-published agent still answers on its public page was not tested.
- **F-6 (kit/starter): the claims starter applies its three kits at create.** Each kit adds its own test case, so the agent starts with 5 tests (3 golden plus 2 kit cases); the run trimmed them to the 3 golden ones. An `agent_create` patch that sets `instructions` replaces the kit snippets' `<!-- kit:… -->` sections. Adding the kits again restored them, which is idempotent.
- **F-7 (brief error, not a bug):** the order case's expected total in the prompt ($159.98) was wrong. The catalogue gives 2 × 59.50 + 39.99 = **$158.99**, and the run corrected the test expectation.
- **Not configurable through MCP:** nothing. Every capability the brief asked for was configured with the MCP tools (kits, datasets, panel blocks, extraction and rules, the flow tool step, tests).

## 6. Cost

**Total $20.79.** The failed first attempt (CLI logged out) cost $0.

| Run | Cost |
|---|---|
| archive | $1.00 |
| claims | $1.81 |
| order | $2.14 |
| sales | $2.61 |
| tutor | $3.42 |
| support | $1.93 |
| field | $2.08 |
| verify | $3.24 |
| polish | $2.56 |

The polish run stopped at its $2.5 cap, after its updates, publishes and chats had completed; only its final summary was cut off.
