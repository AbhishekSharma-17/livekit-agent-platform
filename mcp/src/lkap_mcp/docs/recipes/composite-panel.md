# Recipe: build a composite panel

Goal: give a `generic`-pack agent a session-page panel made of blocks,
instead of the default four-block layout.

## 1. Design the block list

Pick from the block types (`lkap_explain("panels-and-blocks")`); a
support-triage agent might want a checklist, a table it appends to, and the
transcript with tool calls visible:

`agent_update(...)`
```json
{
  "id_or_slug": "<agent>",
  "patch": {
    "panel": {
      "panel_id": "composite",
      "layout": "side",
      "blocks": [
        { "id": "checklist_main", "type": "checklist", "config": {} },
        {
          "id": "tickets_table",
          "type": "table",
          "config": { "columns": [
            { "key": "id", "label": "Ticket" },
            { "key": "priority", "label": "Priority" }
          ] }
        },
        { "id": "transcript_main", "type": "transcript", "config": { "show_tools": true } }
      ]
    }
  }
}
```

An intake agent that asks quick questions, keeps a summary card and shows
where the caller is might add the newer blocks instead:

```json
[
  { "id": "quick_answers", "type": "choices", "config": { "layout": "buttons", "max_options": 4 } },
  {
    "id": "claim_details",
    "type": "details",
    "config": { "columns": 2, "fields": [
      { "key": "claim_no", "label": "Claim number" },
      { "key": "date_of_loss", "label": "Date of loss", "type": "date" }
    ] }
  },
  { "id": "recap", "type": "markdown", "config": { "max_chars": 4000 } },
  { "id": "progress", "type": "steps", "config": { "source": "flow" } }
]
```

The agent gets `request_choice` / `resolve_choice`, `set_details` and
`show_text` for them; the `steps` block follows the agent's flow on its own
(give it `"source": "manual"` and it gets `set_steps` instead).

To keep the card and the recap in one place, add a `layout` block that shows
them as tabs (the two blocks stay in the list; the layout only claims them):
`{"id": "claim_tabs", "type": "layout", "config": {"kind": "tabs", "children":
[{"block_id": "claim_details", "label": "Claim"}, {"block_id": "recap", "label":
"Recap"}]}}`.

### Or start from the Notebook

A note-taking agent can take the ready-made Notebook panel instead (a wide
panel: a status stamp, a notebook with Notes, Still needed, Summary and Sketch
sections that the caller may write in too, and a gallery). The agent gets
`notebook_write` and `notebook_check`:

`agent_update(...)`
```json
{ "id_or_slug": "<agent>", "panel_preset": "notebook" }
```

## 2. Validate

`agent_validate(...)`
```json
{ "id_or_slug": "<agent>" }
```
A bad key for a block type comes back as an issue at
`panel.blocks[i].config.<key>` — every block schema forbids unknown keys, so
a typo is caught here rather than silently ignored.

## 3. See it live

Test with `chat_start`/`chat_send`, or open the (unpublished) session url
directly — a draft agent's panel still renders for anyone who can reach the
console.

## Related concepts

`lkap_explain("panels-and-blocks")`.
