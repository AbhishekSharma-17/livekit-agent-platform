# Contributing

Thanks for your interest. Issues and pull requests are welcome.

## Setup

- Python 3.12 with [uv](https://docs.astral.sh/uv/); Node 20+ with pnpm.
- Each Python package (`contracts`, `api`, `agent`, `packs`, `supervisor`,
  `testing`, `mcp`) is its own uv project: `cd <package> && uv sync`.
- Web: `cd web && pnpm install`.
- See `README.md` and `docs/RUNBOOK.md` for running the stack locally.

## Before you open a pull request

Run the gate for every package you touched.

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src/ --strict && uv run pytest -q -m "not live"
```

In `web/`, run `pnpm lint && pnpm typecheck && pnpm test`.

If you change `contracts/`, regenerate with `scripts/export_contracts.sh` and
commit the generated files.

## Adding a model to the registry

The registry's model lists (`contracts/src/lkap_contracts/providers.py`) are
suggestions, not an allowlist: any id a vendor accepts already works as a
custom id. A model is promoted to a registry `ModelSpec` only when all three
hold:

1. **It is named.** The weekly `Catalog drift` issue lists it under
   `upstream_new`, or a user asks for it.
2. **It passed "Test model" on a dev stack.** Run
   `POST /v1/providers/{id}/test-model` (or the console's Test button) with
   the exact id, and put the provider, the id, the latency, the cost and the
   detected capabilities in the pull request.
3. **The pull request edits `providers.py` only.** Add the id to `models`.
   Changing `default_model` needs a second piece of evidence: a text-chat
   session on a dev stack that used the new default.

Two more rules:

- `supports_video` becomes `True` only after a passing `vision` probe or a
  recorded session that used vision. Vendor metadata alone is not enough.
- A `default_model` is never a `:free`, `auto` or preview id.

This process never touches `VERIFIED_IDS`; that list records live
provider-level calls.

To check drift locally, run
`scripts/catalog_drift.py --only keyless --out <dir>`. The keyless lists are OpenRouter, Deepgram and Rime. Keyed lists read
their key from the environment variable the module names (`OPENAI_API_KEY`
and so on). The script writes `catalog-drift.md` and `catalog-drift.json`.
When a registry id stops appearing in the vendor's list, it moves to
`registry_not_upstream`. Agents that use it also get a validation warning,
never an error.

## Conventions

- Typed Python (mypy `--strict`) and Pydantic v2 models for all data.
- No side drawers in the web UI: use dialogs. An ESLint rule enforces this.
- No secrets in code, docs, tests or commit messages.

By contributing, you agree that your contributions are licensed under the
Apache License, Version 2.0.
