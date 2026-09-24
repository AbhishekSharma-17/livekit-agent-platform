# Block catalog lines

One line per block type, shown next to its generated config schema on the
`lkap://blocks` resource and `lkap_describe("block", type)`. See
`lkap_explain("panels-and-blocks")` for the full concept doc (config keys,
the tools each block gives the agent, and a worked example).

- `status` — a big status stamp the agent updates.
- `notes` — a running list of short notes.
- `checklist` — "still needed" items with done/not-done state.
- `activity` — a timeline of tool calls and events.
- `form` — a structured form the agent can request the user fill in.
- `document` — an embedded document viewer, pointed at a url.
- `gallery` — a grid of images/assets the agent pushes.
- `table` — a table the agent appends rows to.
- `transcript` — the live transcript, optionally interleaved with tool calls.
- `video` — a video tile (agent avatar, user camera/screen, or a track).
- `kb_citations` — knowledge-base hits cited during the conversation.
- `custom` — a pack-rendered block outside the built-in set.
