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
schema. The seventeen block types:

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
| `choices` | `multi`, `layout` (`buttons`/`list`/`chips`), `max_options` (2–20, default 8) | Options the caller taps or answers out loud (yes/no, "which policy?", a quick poll). |
| `details` | `columns` (1 or 2), `fields: [{key, label, type}]` (the starting rows) | A key-value card of facts collected so far; `type` is `string`, `number`, `date`, `money`, `phone`, `email` or `badge`. |
| `markdown` | `max_chars` (200–50000, default 8000), `allow_links` | Longer text on screen: a recap, instructions, a quoted clause. Never raw HTML. |
| `steps` | `steps: [{id, label}]`, `source` (`manual`/`flow`), `show_notes` | A progress timeline. With `source: "flow"` it follows the agent's flow by itself (step ids are flow node ids). |
| `consent` | `kind` (`recording`/`ai_disclosure`/`terms`/`custom`), `text` (empty = the workspace's wording for `recording` and `ai_disclosure`), `required`, `decline_action` (`continue`/`end_call`), `show_banner` | A question the caller accepts or declines, such as agreeing to be recorded, plus the "you're talking to an AI assistant" banner. The text is public by design. |

Tapping a `kb_citations` entry asks the agent to open the cited page: when the
session holds that document and the panel has a `document` block, the page
opens there with the section highlighted.

## The tools blocks give the agent

Attaching a block registers matching worker tools automatically (on top of
`config.tools.builtin_disabled`, which can still turn one off):

- `update_block` — writes state into any of `document`, `gallery`, `table`,
  `transcript`, `video`, `kb_citations`, `custom`, `details`, `markdown`,
  `steps`.
  `show_document` — points a `document` block at a url. `table_append` —
  appends one row to a `table` block. `request_form` — asks the user to
  fill in a `form` block and returns their answers.
- `request_choice` (a `choices` block) — shows a question with options and
  waits for the caller's tap; it returns `{selected}`. If the caller starts
  speaking while the options are up, the request is withdrawn (a pending form
  is not). `resolve_choice` records an option the caller said out loud. On a
  phone call nothing is shown and the agent asks out loud.
- `set_details` (a `details` block) — adds or updates rows by `key`, quietly.
- `show_text` (a `markdown` block) — replaces the text; the agent speaks a
  one-line summary. Raw HTML and text over `max_chars` are refused.
- `set_steps` (a `steps` block with `source: "manual"`) — marks steps
  `pending`, `active`, `done`, `skipped` or `failed`. A `source: "flow"`
  block has no tool.
- `request_consent` (a `consent` block) — shows the wording and waits for
  Accept or Decline; `record_consent` records a yes or no the caller said
  out loud (it is also registered without a block when the agent asks for
  consent before recording). Every answer is stored as a `consent` session
  event with the SHA-256 of the exact wording. A declined required consent
  with `decline_action: "end_call"` ends the call after a goodbye.

`agent_validate` warns when a `choices` block sits on an agent set up for
phone calls (keypad input or transfer destinations: phone callers see no
screen), and when a `source: "flow"` steps block has no flow to follow or
names a step the flow does not have. A `terms` or `custom` consent block
without its own `text` is an error.

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
`VideoBlockState`, `KbCitationsBlockState`, `ChoicesBlockState`,
`DetailsBlockState`, `MarkdownBlockState`, `StepsBlockState`.
