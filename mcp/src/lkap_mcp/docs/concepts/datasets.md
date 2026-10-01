# Datasets

A dataset is a read-only **lookup table**: a CSV or JSON file (at most
5 MiB, 50,000 rows and 64 columns) that an agent searches by one or more
**key columns**, such as a policy directory, a price list or a store list. Rows are
never changed from a call. To log something, use a sheet or CRM tool.

`dataset_list()` shows every table with its columns, key columns, row
count and import status.

## Creating one

`dataset_create(name, key_columns, text=...)` (or `file_path=...` in stdio
mode) uploads the file. `key_columns` names the columns a lookup matches on
(by header or column name) and how each is compared:

- `string`: case and extra spaces are ignored (`Ann Lee` = `ann  lee`).
- `phone`: only the digits count, and only the last ten, so
  `+91 98765 43210` and `098765 43210` are the same number.
- `email`: case is ignored.
- `number`: `1,200.50` = `1200.5`.

Headers become column names in lower case with `_` (`Policy Number` →
`policy_number`). A JSON file is a list of objects (or an object with a
`rows` list). The file is checked when it arrives (too many rows, an unknown
key column or an unreadable file is refused with a plain reason) and the
rows are then imported in the background. `wait=true` (default) returns once
the table is `ready`.

## Looking rows up

`dataset_lookup(dataset_id, keys={"phone": "+91 98765 43210"})` finds the
rows whose key columns match every value given (`match="exact"`, or
`"prefix"` for the start of the value), at most `max_rows` (≤ 20), with only
`return_columns` when given. The rows come back as `Untrusted` data. They are
someone's spreadsheet, not instructions.

## Giving an agent a lookup tool

`tool_create_dataset(name, description, dataset_id, key_columns=[...])`
creates a tool of kind `dataset`. Attach it with `agent_attach`. The model
sees one text argument per key column and gets back the found rows, fenced as
untrusted data. The tool can also:

- `pinned_arguments={"phone": "{{ ctx.caller_phone }}"}`: look the caller
  up by the number they call from, without the model asking.
- `requires_vars=[...]`: wait until variables are set.
- `bindings=[{"path": "/0/holder_name", "to": "details:card.holder"}]`: put
  the first found row's value on the panel before the model answers.

A lookup only ever reads tables of the agent's own workspace.
`agent_validate` reports a tool whose table was deleted, a key column the
table does not declare, or a table still importing.
`dataset_delete(dataset_id, confirm=true)` is refused while a tool still uses
the table.

## Related tools

`dataset_list`, `dataset_create`, `dataset_lookup`, `dataset_delete`,
`tool_create_dataset`, `agent_attach`, `agent_validate`.

## Related schemas

`DatasetOut`, `DatasetLookupIn`, `DatasetLookupOut`, `DatasetToolDefinition`.
