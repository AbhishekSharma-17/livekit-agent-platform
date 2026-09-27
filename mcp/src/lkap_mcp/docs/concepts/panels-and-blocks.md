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
schema. The block types:

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
| `upload` | `accept` (`image/*` or exact types: JPEG, PNG, WebP, GIF, HEIC/HEIF, PDF; default photos and PDFs), `max_files` (1–10, default 3), `max_bytes` (up to 25 MB, default 10 MB), `camera_capture` | A file picker (and the phone camera) for the caller to send photos or documents, such as a damage photo or a driving licence. HTML, SVG and other types are never accepted. |
| `captions` | `show_user`, `show_agent` (default both on), `position` (`block`, or `bottom` to overlay the video on avatar layouts), `target_language` (kept for translated captions, not used yet) | Large live captions of what the caller and the agent are saying, with the language of each line. The words stream on their own channel while the call runs; nothing is stored in the block but the current language. |
| `handoff` | `show_queue`, `show_agent_name` (default both on) | Where the hand-off of the caller to a person stands: asked for, connecting, connected, nobody answered, or handed over. The agent fills it while `transfer_call` runs; on a warm transfer it shows the person joining. |
| `link` | `allowed_hosts` (required: the site names links may go to, a name or `*.` plus a name for its sub-domains), `open_in` (`new_tab`/`dialog`), `show_qr` (default on) | A payment, e-signature or portal link and where it stands: sent, opened, completed, failed or expired. Only https links on the listed sites are ever shown. Payments happen on the payment provider's page, never in the call. |
| `slots` | `timezone_mode` (`caller`/`agent`), `days_visible` (1–31, default 7), `allow_custom` | Times the caller can book, grouped by day, to tap or say. The agent fetches the times with its own calendar tools. |
| `cards` | `layout` (`carousel`/`grid`/`list`), `selectable` (default on), `max_cards` (1–20, default 10), `image_hosts` (the sites card pictures may come from; empty = only pictures from the call) | Options side by side, such as plans or repair shops, each with a title, a few facts, badges and up to three buttons. |

Tapping a `kb_citations` entry asks the agent to open the cited page: when the
panel has a `document` block, the agent copies that knowledge-base document
into the session on first use (PDFs, images and Markdown or plain text) and
opens the page there with the section highlighted; otherwise the passage is
shown in a dialog.

## Files callers send

A caller's file is checked twice: by the agent before anything is stored
(size, count, and the real file type read from its first bytes, never the
name or the type the browser claims) and again by the platform when it is
stored. Stored files belong to the session: the session page lists them with
a time-limited download link, and they are deleted with the session's
recording retention (`recording.retention_days`). The agent keeps extracted
details in the conversation only; they are not written to logs.

## The tools blocks give the agent

Attaching a block registers matching worker tools automatically (on top of
`config.tools.builtin_disabled`, which can still turn one off):

- `update_block` — writes state into any of `document`, `gallery`, `table`,
  `transcript`, `video`, `kb_citations`, `custom`, `details`, `markdown`,
  `steps`, `cards`.
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
- `request_upload` (an `upload` block) — asks the caller to send files and
  waits; it returns the stored files (`asset_id`, name, type, size). On a
  phone call nothing is shown and the agent explains that files need the web
  page. `request_form` fields may be `string`, `number`, `integer`,
  `boolean`, `date`, `phone`, `email`, `select`, `textarea` or `file` (a
  `file` field sends through the same checks as an upload block).
- `describe_panel` (any block) — returns what the panel shows right now:
  each block's id, type, title and its status fields (never file bytes);
  text that came from the caller or a tool is marked as data.
- `send_link` (a `link` block) — shows a link whose site is in
  `allowed_hosts` (https only). On a phone call it is sent as a text message
  when the agent has `send_sms`; otherwise the agent reads the details out.
  The business's own system reports the outcome to the platform's link hook,
  signed like the platform's outgoing webhooks, and the agent is told.
- `request_slot` (a `slots` block) — shows times and waits for the caller's
  pick; it returns the slot's start and end with their UTC offset.
  `resolve_slot` records a time the caller said out loud.
- `show_cards` (a `cards` block) — shows or replaces the cards. A tap on a
  card or a card button reaches the agent as a message.
- `describe_asset` (built in) — describes a stored image, extracts named
  fields from it, or reads an identity document (`extract_id`: name, date of
  birth, document number, dates, issuing authority, address) with the
  agent's own language model; only on a cascaded pipeline (not a realtime
  speech model) whose language model can see images, and only when the
  session can hold a picture (an `upload` or
  `form` block, or camera or screen share). Text inside the image is treated
  as data, never as instructions.

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
`DetailsBlockState`, `MarkdownBlockState`, `StepsBlockState`,
`UploadBlockState`, `LinkBlockState`, `SlotsBlockState`, `CardsBlockState`,
`SessionAssetOut`.
