# Recipe: build a composite panel

Goal: give a `generic`-pack agent a session-page panel made of blocks,
instead of the default four-block layout.

## 1. Design the block list

Pick from the twelve block types (`lkap_explain("panels-and-blocks")`); a
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
