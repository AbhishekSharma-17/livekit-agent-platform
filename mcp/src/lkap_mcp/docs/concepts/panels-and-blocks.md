# Panels and blocks

`config.panel` (`PanelLayout{panel_id, layout, blocks}`) decides what the
session page shows next to (or below, per `layout: "side"|"wide"`) the
call controls. `panel_id="composite"` is the no-code panel built from a list
of `BlockSpec{id, type, config}`; a pack may instead set its own
`ui_panel_id` (the `insurance_claim` pack ships a custom "insurance_notebook"
React panel and an empty `blocks` list — its state comes entirely from pack
code, not the block system).

## Block catalog

Every `BlockSpec.config` is validated against its type's own strict schema
(unknown keys are rejected) — `lkap_describe("block", type)` returns that
schema. The twelve block types:

| Type | Config | What it shows |
|---|---|---|
| `status` | none | A big status stamp the agent updates. |
| `notes` | none | A running list of short notes. |
| `checklist` | none | "Still needed" items with done/not-done state. |
| `activity` | none | A timeline of tool calls and events. |
| `form` | none | A structured form the agent can request the user fill in. |
| `gallery` | none | A grid of images/assets the agent pushes. |
| `kb_citations` | none | Knowledge-base hits cited during the conversation. |
| `table` | `columns: [{key, label, type}]` | A table the agent appends rows to. |
| `document` | `url` (https only), `page` | An embedded document viewer. |
| `transcript` | `show_tools` | The live transcript, optionally interleaved with tool calls. |
| `video` | `source` (`agent_avatar`/`user_camera`/`user_screen`/`track:<sid>`), `muted` | A video tile. |
| `custom` | `kind` + any pack-declared JSON (all public) | A pack-rendered block outside the built-in set. |

## The tools blocks give the agent

Attaching a block registers matching worker tools automatically (on top of
`config.tools.builtin_disabled`, which can still turn one off):

- `update_block` — writes state into any of `document`, `gallery`, `table`,
  `transcript`, `video`, `kb_citations`, `custom`.
  `show_document` — points a `document` block at a url. `table_append` —
  appends one row to a `table` block. `request_form` — asks the user to
  fill in a `form` block and returns their answers.

## Building a composite panel

`agent_update(patch={"panel": {"panel_id": "composite", "layout": "side",
"blocks": [{"id": "checklist_1", "type": "checklist", "config": {}},
{"id": "table_1", "type": "table", "config": {"columns": [{"key": "item",
"label": "Item"}]}}]}})`. Always `agent_validate` afterward — a bad
`config` key for a block type is reported at
`panel.blocks[i].config.<key>`.

## Related tools

`agent_update`, `agent_validate`, `lkap_describe`.

## Related schemas

`PanelLayout`, `BlockSpec`, `FormBlockState`, `DocumentBlockState`,
`GalleryBlockState`, `TableBlockState`, `TranscriptBlockState`,
`VideoBlockState`, `KbCitationsBlockState`.
