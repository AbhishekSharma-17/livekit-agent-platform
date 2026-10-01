# V6-16 live check · datasets and the `dataset` tool kind

Prerequisites: `v6_002_datasets` applied to the dev database after a backup
(`migration-rehearsal-v6.md`), the api restarted (routes, the `dataset_import` job) and the worker
restarted (the `dataset` tool kind). The PLAN's live rules apply: `Demo — ` objects only, a Builder key
minted for the run and revoked after, fictional names and `example.com` addresses.

## 1. Upload a `Demo — ` CSV

Save as `demo-policies.csv`:

```csv
Policy Number,Holder Name,Phone,Email,Cover,Renewal
PD-1001,Demo — Asha Rao,+91 98765 43210,asha@example.com,Comprehensive,2027-01-31
PD-1002,Demo — Ben Cole,(415) 555-0100,ben@example.com,Third party,2026-11-15
PD-1003,Demo — Cleo Diaz,+44 20 7946 0958,cleo@example.com,Comprehensive,2027-03-01
```

With the MCP server (Builder key): `dataset_create(name="Demo — Policies", key_columns={"Phone":
"phone", "Policy Number": "string", "Email": "email"}, file_path="demo-policies.csv")`. Expect
`status: ready`, `row_count: 3`, `policy_number`/`phone`/`email` marked as keys.

Also check a refusal: a file with 50,001 data rows is refused with "this file has more than 50,000
rows …" and nothing appears in `dataset_list()`.

## 2. A lookup from the console (or MCP)

`dataset_lookup(dataset_id=…, keys={"phone": "098765 43210"})` → one row, `PD-1001`, even though the
file wrote `+91 98765 43210`. `keys={"policy_number": "pd-10"}, match="prefix", max_rows=2` → two rows and
`truncated: true`. (The console's datasets page is V6-19. Until then use MCP or
`POST /v1/datasets/{id}/lookup`.)

`GET /v1/datasets/{id}/export` downloads the CSV. A cell starting with `=` comes back prefixed with `'`.

## 3. A text chat that looks a record up by phone number

1. `tool_create_dataset(name="lookup_policy", description="Find the caller's policy by their phone
   number and read back the holder, cover and renewal date.", dataset_id=…, key_columns=["phone"],
   return_columns=["policy_number", "holder_name", "cover", "renewal"])`, then `agent_attach(<a Demo
   agent>, tool_ids=[…])`, `agent_validate` (no issue).
2. `chat_start` on that agent, `chat_send("Hi, my number is 0 98765 43210, what cover do I have?")`.
   Expect a `lookup_policy` tool call and a reply naming "Comprehensive" and PD-1001. The session's tool
   result shows the rows inside `<untrusted source="dataset:lookup_policy">`.
3. `chat_send("And for 555 0199?")` → the model says no record was found.
4. Optional (phone): the same tool with `pinned_arguments={"phone": "{{ ctx.caller_phone }}"}` looks
   the caller up by the number they call from without asking (a phone call from a number in the file).

## 4. Clean-up

`dataset_delete(dataset_id, confirm=true)` is refused while `lookup_policy` exists. Delete the tool
(`lkap_delete(kind="tool", …, confirm=true)`), then the dataset. Revoke the Builder key.
