# scripts

dev.sh (run api + agent + web), export_contracts.sh. `dev.sh` is owned by W2-DEPLOY (see docs/IMPLEMENTATION_PLAN.md); `export_contracts.sh` by W0-SCAFFOLD.

`catalog_drift.py` compares the provider registry's model ids with the vendors' live lists. It is a thin wrapper around `uv run --project api python -m lkap_api.catalogs.drift`. Run it with `--only keyless --out <dir>`. It always exits 0. The weekly `.github/workflows/catalog-drift.yml` runs the same module and keeps one `Catalog drift` issue up to date (docs/v4/CUSTOM-MODELS.md D-V4-27).
