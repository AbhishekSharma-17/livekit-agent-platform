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

## Conventions

- Typed Python (mypy `--strict`) and Pydantic v2 models for all data.
- No side drawers in the web UI: use dialogs. An ESLint rule enforces this.
- No secrets in code, docs, tests or commit messages.

By contributing, you agree that your contributions are licensed under the
Apache License, Version 2.0.
