#!/usr/bin/env bash
# Build `lkap-contracts` and `lkap-packs` wheels and stage them at
# agent/vendor/*.whl.
#
# Why this exists: LiveKit Cloud's `lk agent create`/`lk agent deploy`
# builds its image from the directory containing `livekit.toml` (`agent/`)
# — that build context does NOT include the platform root, but
# `agent/pyproject.toml` depends on `lkap-contracts` and `lkap-packs` as
# *local path* deps (`../contracts`, `../packs` via `[tool.uv.sources]`),
# which only resolve when those directories are physically present next to
# `agent/`. Building real wheels here and vendoring them into `agent/`
# keeps the deploy unit (and `agent/Dockerfile`'s build context)
# self-contained: `agent/Dockerfile` installs everything else from
# `agent/uv.lock` as normal and installs these two wheels separately
# (`uv sync --no-install-package lkap-contracts --no-install-package
# lkap-packs`, confirmed to skip touching `../contracts`/`../packs`
# entirely, then `uv pip install vendor/*.whl`).
#
# Run this before:
#   - `docker build -f agent/Dockerfile agent` (or `docker compose build` if
#     the agent is ever added there)
#   - `cd agent && lk agent create --secrets-file secrets.env` / `lk agent deploy`
#
# `agent/vendor/` is build output, not source — see agent/vendor/.gitignore.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENDOR_DIR="${ROOT_DIR}/agent/vendor"

log() { printf '[vendor_agent_deps] %s\n' "$1" >&2; }

mkdir -p "${VENDOR_DIR}"
rm -f "${VENDOR_DIR}"/*.whl

log "building lkap-contracts wheel..."
(cd "${ROOT_DIR}/contracts" && uv build --wheel -o "${VENDOR_DIR}")

log "building lkap-packs wheel..."
(cd "${ROOT_DIR}/packs" && uv build --wheel -o "${VENDOR_DIR}")

log "staged: $(ls "${VENDOR_DIR}"/*.whl | xargs -n1 basename | tr '\n' ' ')"
