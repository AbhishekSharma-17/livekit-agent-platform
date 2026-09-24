#!/usr/bin/env bash
# Fallback TS generation + copy step for lkap_contracts (docs/CONTRACTS.md §2, §12).
#
# `contracts/src/lkap_contracts/export.py` (owner: W0-CONTRACTS) is the
# primary generator: it writes `generated/providers.json` and
# `generated/schemas/*.schema.json` (pure Python/Pydantic, no Node needed),
# and itself tries `pnpm dlx json-schema-to-typescript` to produce
# `generated/ts/lkap-contracts.d.ts`. This script is the checked-in fallback
# for that last step (per docs/CONTRACTS.md §1), and always performs the
# final copy into `web/src/contracts/` — the only place the web app reads
# generated types from. TS types are generated, never hand-written.
#
# Usage:
#   scripts/export_contracts.sh              # just copy the existing .d.ts into web/
#   scripts/export_contracts.sh --generate    # also (re)run the Python export first
#   scripts/export_contracts.sh --force-ts    # rebuild the .d.ts from schemas/*.schema.json
#                                              # via `pnpm --dir web exec json2ts`, even if one
#                                              # already exists (the actual fallback path)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONTRACTS_DIR="${ROOT_DIR}/contracts"
WEB_DIR="${ROOT_DIR}/web"
SCHEMAS_DIR="${CONTRACTS_DIR}/generated/schemas"
TS_DIR="${CONTRACTS_DIR}/generated/ts"
GENERATED_TS="${TS_DIR}/lkap-contracts.d.ts"
WEB_CONTRACTS_DIR="${WEB_DIR}/src/contracts"

generate=false
force_ts=false
for arg in "$@"; do
  case "${arg}" in
    --generate) generate=true ;;
    --force-ts) force_ts=true ;;
    *)
      echo "export_contracts: unknown argument: ${arg}" >&2
      exit 2
      ;;
  esac
done

if [[ "${generate}" == true ]]; then
  echo "export_contracts: running python -m lkap_contracts.export ..." >&2
  (cd "${CONTRACTS_DIR}" && uv run python -m lkap_contracts.export)
fi

if [[ "${force_ts}" == true || ! -f "${GENERATED_TS}" ]]; then
  shopt -s nullglob
  schema_files=("${SCHEMAS_DIR}"/*.schema.json)
  shopt -u nullglob
  if [[ ${#schema_files[@]} -eq 0 ]]; then
    echo "export_contracts: no schemas in ${SCHEMAS_DIR}; run with --generate first" >&2
    exit 1
  fi

  echo "export_contracts: building lkap-contracts.d.ts from ${#schema_files[@]} schema(s) via json2ts (fallback path) ..." >&2
  mkdir -p "${TS_DIR}"
  tmp_file="${GENERATED_TS}.tmp"
  : > "${tmp_file}"
  for schema_file in "${schema_files[@]}"; do
    pnpm --dir "${WEB_DIR}" exec json2ts --input "${schema_file}" --cwd "${SCHEMAS_DIR}" >> "${tmp_file}"
    printf '\n' >> "${tmp_file}"
  done
  mv "${tmp_file}" "${GENERATED_TS}"
fi

if [[ ! -f "${GENERATED_TS}" ]]; then
  echo "export_contracts: ${GENERATED_TS} missing; nothing to copy" >&2
  exit 1
fi

mkdir -p "${WEB_CONTRACTS_DIR}"
cp "${GENERATED_TS}" "${WEB_CONTRACTS_DIR}/lkap-contracts.d.ts"
echo "export_contracts: copied generated/ts/lkap-contracts.d.ts -> web/src/contracts/" >&2

# V3-01 (docs/v3/_asks.md G2): the MCP server's generated resources, byte-identical
# to contracts/generated (providers.json, builtin_tools.json, schemas/). The
# schemas dir is replaced wholesale so a removed model does not linger.
MCP_GENERATED_DIR="${ROOT_DIR}/mcp/src/lkap_mcp/generated"
if [[ -d "${ROOT_DIR}/mcp" ]]; then
  mkdir -p "${MCP_GENERATED_DIR}"
  cp "${CONTRACTS_DIR}/generated/providers.json" "${MCP_GENERATED_DIR}/providers.json"
  cp "${CONTRACTS_DIR}/generated/builtin_tools.json" "${MCP_GENERATED_DIR}/builtin_tools.json"
  rm -rf "${MCP_GENERATED_DIR}/schemas"
  cp -R "${SCHEMAS_DIR}" "${MCP_GENERATED_DIR}/schemas"
  echo "export_contracts: copied providers.json, builtin_tools.json, schemas/ -> mcp/src/lkap_mcp/generated/" >&2
fi

# V3-08 (docs/v3/_asks.md G4): the Claude Code skill's recipes have one
# source, mcp/src/lkap_mcp/docs/recipes/ (V3-03), copied byte-identical into
# the packaged skill so `mcp/tests/test_docs_lint.py` can assert on the copy
# without a second place to keep the text in sync.
MCP_RECIPES_DIR="${ROOT_DIR}/mcp/src/lkap_mcp/docs/recipes"
SKILL_RECIPES_DIR="${ROOT_DIR}/mcp/claude-plugin/skills/lkap/recipes"
if [[ -d "${MCP_RECIPES_DIR}" ]]; then
  mkdir -p "${SKILL_RECIPES_DIR}"
  # Remove copies of recipes that no longer exist at the source, then copy
  # every current one, so a rename on the source side doesn't leave a stale
  # file behind in the plugin.
  find "${SKILL_RECIPES_DIR}" -maxdepth 1 -name '*.md' -delete
  cp "${MCP_RECIPES_DIR}"/*.md "${SKILL_RECIPES_DIR}/"
  echo "export_contracts: copied mcp/src/lkap_mcp/docs/recipes/*.md -> mcp/claude-plugin/skills/lkap/recipes/" >&2
fi
