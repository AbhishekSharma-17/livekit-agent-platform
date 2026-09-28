# Flows

A flow (`FlowSpec{nodes, edges, variables}`) replaces free-form prompting
with an explicit graph: each `agent` node is its own set of instructions,
tools and knowledge, and each outgoing edge becomes a handoff tool named
`go_to_{target_id}` that the model calls to move to the next node. Structural
rules are checked without the database (exactly one `start` node, node ids
match `^[a-z][a-z0-9_]{0,31}$`, no edge attaches to a `global` or `qa` node,
an `end` node has no outgoing edges, every node is reachable from `start`);
reference checks (do the `tools`/`kb_ids` names actually exist) need the
database and run in `agent_validate`/`agent_flow_validate`.

## Node kinds

- `start` — the entry point; `greeting` (`greeting_mode: "say"|"generate"`)
  plays before the first handoff.
- `agent` — one conversational step: `instructions`, `tools`, `kb_ids`,
  `extract` (variable names to pull from this step), `max_turns`, and
  optional per-node `providers` overrides (`llm`/`tts`).
- `global` — instructions, tools and knowledge merged into every `agent`
  node; at most one per flow, never an edge endpoint.
  A step searches the `global` node's `kb_ids` plus its own; when no node
  lists any `kb_ids`, every step searches all of the agent's
  `knowledge.kb_ids`, so leave them empty unless you mean to narrow a step.
- `end` — terminal: `farewell`, a `disposition` label, `webhook_event`.
- `transfer` — hands the call to a human or another number (`to`, `mode:
  "cold"|"warm"`); phone-shaped, so it interacts with the telephony policy
  (`lkap_explain("telephony")`).
- `qa` — a post-call scoring step (`rubric_prompt`); never part of the
  conversational graph itself, only referenced by the QA resolution rule
  (`lkap_explain("qa-and-evals")`).
- `tool` — calls one of the agent's attached tools the moment the flow
  reaches it, with no model turn, then branches on the outcome. Fields:
  `tool` (the tool row's `name`; for an MCP server also `mcp_tool`, the one
  server tool to call), `arguments` (literals, or text with
  `{{ var.<name> }}` / `{{ ctx.<name> }}`; exactly `{{ var.<name> }}` passes
  the value as it is), `bindings` (as a tool's own: `var:<name>`,
  `details:<block>.<key>`, `table:<block>`, `status`, `note`,
  `checklist:<item>`), `timeout_s` (≤ 30) and `on: {ok, error, empty}` —
  edge ids. `empty` means the tool succeeded but returned nothing, or no
  binding found a value; without an `empty` edge the `ok` edge is taken.
  Without an `error` edge a failure ends the call with a short apology and a
  `flow_error` session event. The result never reaches the model: later
  steps read what the bindings stored (`{{ holder }}` in instructions,
  fenced as tool data). A tool with `confirm_readback` cannot be a step (it
  must read values to the caller first); a missing variable is `error`.
  Every edge leaving a `tool` node must be named in `on`, its `condition`
  is ignored, and a loop of `tool` nodes with no `agent` node in it is
  refused. A `start` node whose only edge leads to a `tool` node greets,
  then runs it.

Both `tools` (a `global`/`agent` node's tool-name list) and a node's `kb_ids`
name built-in tools, block tools, pack tools or the `name` of a
workspace tool row (`config.tools.tool_ids`) — the same names
`tool_list`/`lkap_describe("builtin_tool", name)` show elsewhere.

## Building and validating

Send a full `FlowSpec` with `agent_flow_validate(id_or_slug, flow={...})`
first — it validates without saving, so you can iterate on the graph before
committing. Save with `agent_update(patch={"flow": {...}})`; the agent's
`mode` becomes `"flow"` automatically. `patch={"flow": null}` switches back
to a prompt agent.

## Variables and state

`FlowState{current_node, path, variables, disposition}` is the runtime
position, visible on `session_get`; `variables` (`VariableSpec{name, type,
description, options, required}`) declare what an `extract` step pulls out
of the conversation, referenced from instructions as `{{ name }}`.

## Related tools

`agent_flow_validate`, `agent_update`, `agent_get`, `session_get`.

## Related schemas

`FlowSpec`, `FlowEdge`, `FlowState`, `VariableSpec`, `StartNode`,
`AgentNode`, `EndNode`, `GlobalNode`, `TransferNode`, `QaNode`, `ToolNode`.
