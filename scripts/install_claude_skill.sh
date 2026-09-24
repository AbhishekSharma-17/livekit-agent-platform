#!/usr/bin/env bash
# Install the lkap Claude Code skill on its own, for a user who already added
# the `lkap` MCP server with `claude mcp add` (the console's Connect dialog,
# docs/v3/AGENT-ACCESS.md §5) rather than loading the full plugin with
# `claude --plugin-dir`.
#
# Copies mcp/claude-plugin/skills/lkap/ (the skill body plus its recipes,
# both git-tracked and kept in sync with mcp/src/lkap_mcp/docs/recipes/ by
# scripts/export_contracts.sh) to where Claude Code looks for a user- or
# project-level skill:
#   default:    ~/.claude/skills/lkap      (every project, this user)
#   --project:  ./.claude/skills/lkap      (this project only, run from its root)
#
# The destination is recreated from scratch on every run, so re-running this
# script after a platform update refreshes the skill's recipes.
#
# Usage:
#   scripts/install_claude_skill.sh              install for the current user
#   scripts/install_claude_skill.sh --project     install for the current project only
#   scripts/install_claude_skill.sh --help
#
# Honors $HOME as set in the environment (this is how tests point the
# default install at a temporary home instead of the real one).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOURCE_DIR="${ROOT_DIR}/mcp/claude-plugin/skills/lkap"

scope="user"
for arg in "$@"; do
  case "${arg}" in
    --project)
      scope="project"
      ;;
    -h|--help)
      sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "install_claude_skill: unknown argument: ${arg}" >&2
      exit 2
      ;;
  esac
done

if [[ ! -d "${SOURCE_DIR}" ]]; then
  echo "install_claude_skill: ${SOURCE_DIR} not found; run from a full checkout" >&2
  exit 1
fi

if [[ ! -f "${SOURCE_DIR}/SKILL.md" ]]; then
  echo "install_claude_skill: ${SOURCE_DIR}/SKILL.md not found; the skill directory looks incomplete" >&2
  exit 1
fi

if [[ "${scope}" == "project" ]]; then
  dest_root="$(pwd)/.claude/skills"
else
  if [[ -z "${HOME:-}" ]]; then
    echo "install_claude_skill: \$HOME is not set" >&2
    exit 1
  fi
  dest_root="${HOME}/.claude/skills"
fi

dest_dir="${dest_root}/lkap"

mkdir -p "${dest_root}"
rm -rf "${dest_dir}"
cp -R "${SOURCE_DIR}" "${dest_dir}"

echo "install_claude_skill: installed the lkap skill -> ${dest_dir}" >&2
if [[ "${scope}" == "user" ]]; then
  echo "install_claude_skill: available in every Claude Code session for this user" >&2
else
  echo "install_claude_skill: available in Claude Code sessions started in this project" >&2
fi
