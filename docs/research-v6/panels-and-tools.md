# LKAP audit — panels, capabilities, tool genericity (2026-09-28, read-only)

Summary of the audit agent's report (full detail was delivered in-session; key facts and file:line anchors kept).

## Bottom line
- Panels: 23 typed blocks, all with renderers. NOT built from research: whiteboard/annotation (B9), signature (B15), chart (B13), map (B14, deferred D-V5-28), timer (B16), embed/MCP Apps (B17, parked D-V5-27), code (B18), cart (B19), `layout` block (§5.2-2).
- Notebook: the "claim notebook" is a hard-coded insurance React panel (`web/src/panels/insurance_notebook/index.tsx:172-180`, registered `web/src/panels/registry.ts:141-145`; pack sets `ui_panel_id="insurance_notebook"`, `packs/src/packs/insurance_claim/manifest.py:63-65`). It parses an insurance-only `UiState.custom` schema (`insurance_notebook/state.ts:48-58`). "Handwritten" = CSS fonts (Caveat/Patrick Hand) on agent-written notes; no caller ink/drawing anywhere. The "sketch" is an AI image from pack tool `draw_incident_sketch` (`packs/.../tools.py:329-407`).
- Tools: generic mechanisms strong (declarative HTTP, MCP w/ OAuth + presets, Composio provider tools, 19 built-ins + 17 block tools, tool templates (HTTP-only, one Cal.com group), starter templates, flows). Insurance behaviour lives in 4 pack tools + a pack-only background extraction pipeline (`ClaimWorkflow`, `workflow.py:197-269`) + 708 lines of rules (`rules.py`).

## Gaps (platform)
1. HTTP tools can't see session/flow context (`declarative.py:68-101`: only args, secrets, schema defaults).
2. No result → panel/variable bindings without an LLM step.
3. No generic live structured extraction (only flow `extract` on node exit `flow.py:93` and post-call `QaConfig.fields`).
4. No declarative rules (required fields → checklist, if X then status/escalate).
5. Tool templates HTTP-only (`ToolTemplate.definition` is `HttpToolDefinition`, `tools.py:580-597`); no kits (tools+blocks+instructions).
6. No static dataset lookup source.
7. No flow `tool` node (node kinds `start|agent|end|global|transfer|qa`, `flow.py:17`).
8. No `set_checklist`/`check_item` built-in (only pack code via `UiChannel.set_checklist`), no `generate_image` built-in (slot `image_gen` is generic), caller block edits don't reach the model (non-card `block_action` → pack only, `platform_agent.py:1202-1226`).
9. No browser→agent text-stream topic (needed for ink strokes; RPC capped ~15 KiB).

## Proposed V6 packages (audit's suggestion)
- V6-01 Generic panel primitives (set_checklist/check_item, generate_image, caller-edit → fenced message, per-block notes) — S
- V6-02 `notebook` block contract + tools (sections text|checklist|details|ink; notebook_write/notebook_check; describe_panel case) — M
- V6-03 Ink canvas / whiteboard (subsumes B9 annotation; `lkap.ui.ink` text stream; PNG snapshot → session_assets kind `ink` → describe_asset; draw_on_canvas/read_canvas) — M–L
- V6-04 Console renderers: notebook, canvas (lazy freehand lib; check licence/bundle), `layout` block, "Notebook" preset — M
- V6-05 Rebase insurance pack onto generic parts (notebook preset, generate_image, choices; `LKAP_PACKS` default generic) — M
- V6-06 HTTP/provider tool context placeholders (`{{ ctx.* }}`, `{{ var.* }}`), result bindings, requires_vars, confirm_readback — M
- V6-07 Live structured extraction + declarative rules (generic ClaimWorkflow) — M
- V6-08 Tool template kits + generic catalogue (record lookup REST/dataset, case/ticket, structured intake, verify identity, payment/e-sign link, notify/escalate, sheet/CRM log) — M
- V6-09 Flow `tool` node — S–M
- V6-10 Next blocks: signature, chart, timer, code, cart — M
Order: V6-01 → V6-02 ∥ V6-06 → V6-03 → V6-04 → V6-05 ∥ V6-07 → V6-08 → V6-09 → V6-10. Parked: map, embed/MCP Apps, translation, campaigns.

## Capabilities status
Implemented: C1 languages, C5 NC config, C6/C25 tuning, C8 AMD, C11 warm transfer, C9 consent, C10 privacy, C12 listen-in+whisper, C14 SMS, C16 document extraction, C17 realtime video input, C20 memory, C21 simulations, C23 post-call fields, C24 guardrails.
Not implemented: C2 translation, C4 in-call sentiment, C7 backchanneling, C13 campaigns, C18 browser/computer use, C19 AgentTask sub-flows, C22 A/B tests, C3 console voice cloning.
