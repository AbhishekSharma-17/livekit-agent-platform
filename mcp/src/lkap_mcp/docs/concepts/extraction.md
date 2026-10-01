# Live extraction and rules

An agent can **capture facts from the conversation while it talks** and
**react to them with rules**, with no code. Both are off for every existing
agent. Switch them on with `agent_update(patch={"extraction": {...},
"rules": [...]})` and check the result with `agent_validate`.

## Extraction (`ExtractionConfig`)

- `enabled`: off by default.
- `fields`: up to 30 `ExtractionField`s: a flow variable (`name`, `type`
  `string|number|boolean|enum|date|phone|email`, `description`, `options`,
  `required`) plus `label` (what the panel shows), `hint` (guidance for the
  extraction model), `sensitive` (never written into an event) and `show_in`
  (`details:<block_id>`, `details:<block_id>.<key>`, or
  `notebook:<block_id>.<section_id>` for a notebook's details or text section).
- `triggers`: one of each kind: `every_n_turns` with `n` (the default: every
  caller turn), `tool` with `tools` (after one of them returns), `node_exit`
  with `nodes` (when a flow step ends, empty means any step), `manual` (the
  agent gets the `extract_now` built-in).
- `min_turn_chars`: shorter caller turns ("yes", "okay") do not count.
- `still_needed`: `checklist` lists the required fields not captured yet on
  the panel's checklist (items `need_<field>`), ticking them as values arrive.

The extraction runs **in the background** on the agent's workflow model (the
`workflow_llm` slot, else its language model). One call of at most two
seconds, skipped when the conversation has not changed, never delaying the
reply. Values go into the session's **variables**, the same store
`{{ var.<name> }}` placeholders, `requires_vars`, result bindings and flows
use. An empty answer never erases a value. A flow step's own `extract` wins
for the names it lists.

## Rules (`Rule`)

Up to 50 rules, each `{id, label, when, then, once, enabled}`. `when` is a
short condition (at most 200 characters), never code:

- `var.x is set`, `var.x is empty`, `var.x is not set`
- `var.x == "text"`, `var.x != "text"`, `var.x == true`
- `var.n >= 5000` (also `<=`, `>`, `<`)
- `var.x matches /fire|smoke/i` (a pattern that repeats a repeated group is
  refused)
- `tool.lookup_policy.ok`, `tool.lookup_policy.failed`
- `not`, `and`, `or` and parentheses.

`then` lists up to 10 actions (`do`): `checklist.set_item`, `checklist.check`,
`status.set`, `details.set` (the value may use `{{ var.x }}`), `note.push`,
`var.set`, `escalate` (as `escalate_to_human`, with your reason),
`instruct` (a note for the model's next reply only) and `disposition.set`.

Rules run after each extraction, after each batch of tool results and after a
`var.set`. `once: true` (the default) fires a rule once per session.
`once: false` fires it each time its condition turns true.

## What is recorded

Each extraction run records an `extraction` session event (field names and
whether each is set, values only when the agent keeps sessions in full, and
never for a sensitive field). Each firing records a `rule_fired` event (the
rule id and its action kinds, never a value).

## Example

```json
{"extraction": {"enabled": true, "still_needed": "checklist",
  "fields": [{"name": "policy_number", "required": true,
              "show_in": "details:summary"},
             {"name": "hazard", "hint": "fire, smoke, gas or water"}]},
 "rules": [{"id": "danger", "when": "var.hazard matches /fire|smoke|gas/i",
            "then": [{"do": "status.set", "label": "Safety first",
                      "tone": "danger"}]}]}
```

## Related tools

`agent_update`, `agent_validate`, `agent_get`, `session_get`.

## Related schemas

`ExtractionConfig`, `ExtractionField`, `ExtractionEvent`, `Rule`,
`RuleFiredEvent`, `AgentConfig`.
