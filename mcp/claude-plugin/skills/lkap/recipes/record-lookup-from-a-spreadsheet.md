# Recipe: look records up from a spreadsheet

Goal: let an agent find a customer, order or policy in a spreadsheet the
user already has, show it on the panel and read back only what the caller
needs — no API, no key.

You need from the user: the spreadsheet saved as CSV (or JSON), and which
column holds the reference callers give (a customer number, an order
number, a phone number).

## 1. Upload the table

`dataset_create(...)`
```json
{
  "name": "Demo — Customers",
  "key_columns": { "Customer Number": "string", "Phone": "phone" },
  "file_path": "~/customers.csv"
}
```
It waits for the import; note the returned `id`. A `phone` key matches the
same number written with or without a country code.

## 2. Add the lookup kit

`kit_add(...)`
```json
{
  "kit_id": "record_lookup",
  "agent_id": "<agent id>",
  "variant": "dataset",
  "dataset_id": "<dataset id from step 1>",
  "key_columns": ["customer_number"],
  "block_prefix": "customer"
}
```
This adds `customer_lookup` (a lookup on the table), a `customer_results`
table block the found rows fill without a model turn, a status rule and the
instruction snippet. Leave `key_columns` out to match on every key column.
Add `"dry_run": true` first to see the list.

## 3. Check and try it

`agent_validate(id_or_slug)`, then `chat_start` and give a customer
number. The rows reach the model fenced as data, and a lookup never reads
another workspace's table. To find callers by the number they call from
instead, add the kit with `"key_columns": ["phone"]` (another
`block_prefix`) and pin the number on that tool:
`tool_update(tool_id=..., patch={"definition": {"pinned_arguments":
{"phone": "{{ ctx.caller_phone }}"}}})`.
