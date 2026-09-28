# Recipe: switch a prompt agent to a flow

Goal: replace free-form instructions with an explicit node graph for a
scripted intake, without losing the agent's tools or knowledge.

## 1. Draft the flow and validate it offline first

`agent_flow_validate(...)`
```json
{
  "id_or_slug": "<agent>",
  "flow": {
    "v": 1,
    "nodes": [
      { "id": "start", "kind": "start", "greeting": "Hi! Let's get your claim started." },
      { "id": "collect", "kind": "agent", "instructions": "Collect the policy number and a brief description of what happened.", "extract": ["policy_number"] },
      { "id": "wrap_up", "kind": "end", "farewell": "Thanks — an adjuster will follow up within one business day.", "disposition": "collected" }
    ],
    "edges": [
      { "id": "e1", "source": "start", "target": "collect" },
      { "id": "e2", "source": "collect", "target": "wrap_up", "condition": "the policy number and description have been collected" }
    ],
    "variables": [
      { "name": "policy_number", "type": "string", "required": true }
    ]
  }
}
```
Fix any structural `issues` (an unreachable node, a duplicate id, more than
one `start`) before saving — this call never writes anything.

## 2. Save it

`agent_update(...)`
```json
{ "id_or_slug": "<agent>", "patch": { "flow": "<the same FlowSpec object>" } }
```
The agent's mode becomes `"flow"` automatically; `agent_get` now returns
`config.flow` populated.

## 3. Optional: look the record up without a model turn

Once a lookup tool is attached (`add-http-tool`), a `tool` node calls it
as soon as the caller has given the number, stores what it found and
branches on the outcome. Add it between `collect` and `wrap_up`:
```json
{ "id": "lookup", "kind": "tool", "tool": "<your lookup tool's name>",
  "arguments": { "policy": "{{ var.policy_number }}" },
  "bindings": [ { "path": "/holder", "to": "var:holder" } ],
  "on": { "ok": "found", "empty": "not_found", "error": "lookup_failed" } }
```
Point edge `e2` at `lookup` instead of `wrap_up`, add three edges leaving
`lookup` — `found` (to `wrap_up`), `not_found` (back to `collect`) and
`lookup_failed` (to `wrap_up`); no condition needed — and declare `holder`
under `variables`. `agent_flow_validate` says which tool
names the step may use and warns when there is no `error` path.

## 4. Test, then decide

Run `test-and-publish`. To go back to a prompt agent later:
`agent_update(patch={"flow": null})`.

## Related concepts

`lkap_explain("flows")`.
