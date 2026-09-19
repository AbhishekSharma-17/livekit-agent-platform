#!/usr/bin/env bash
# Run api + agent + web for local development, each `uv`/`pnpm` in place
# (no Docker) — mirrors docs/CONTRACTS.md §12 "run (three terminals or
# scripts/dev.sh)" exactly, just in one script with cleanup.
#
# Secrets are never read or written by this script: export the required
# vars in your shell first (or `source` a human-created env file yourself
# before running this), same convention as `.claude/launch.json`. This
# script only fills in the non-secret dev defaults documented in
# docs/CONTRACTS.md §3 when they're unset.
#
# Usage: scripts/dev.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

log() { printf '[dev.sh] %s\n' "$1" >&2; }
die() { printf '[dev.sh] ERROR: %s\n' "$1" >&2; exit 1; }

# --- required secrets: must already be in the environment ------------------
required_vars=(LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET LKAP_MASTER_KEY)
missing=()
for var in "${required_vars[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    missing+=("${var}")
  fi
done
if [[ ${#missing[@]} -gt 0 ]]; then
  log "missing required env var(s): ${missing[*]}"
  log "LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET: from your LiveKit Cloud project"
  log "LKAP_MASTER_KEY: cd api && uv run python -m lkap_api.keys generate"
  die "export the missing var(s) and re-run (see docs/CONTRACTS.md §3, §12)"
fi

# --- non-secret dev defaults (docs/CONTRACTS.md §3) -------------------------
export LKAP_ADMIN_TOKEN="${LKAP_ADMIN_TOKEN:-dev-admin}"
export LKAP_SERVICE_TOKEN="${LKAP_SERVICE_TOKEN:-dev-service}"
export LKAP_AGENT_NAME="${LKAP_AGENT_NAME:-lkap-agent}"
export LIVEKIT_AGENT_NAME="${LIVEKIT_AGENT_NAME:-lkap-agent}"
export LKAP_API_BASE_URL="${LKAP_API_BASE_URL:-http://127.0.0.1:8080}"
export LKAP_PACKS="${LKAP_PACKS:-packs.insurance_claim,packs.generic}"
export LKAP_CORS_ORIGINS="${LKAP_CORS_ORIGINS:-http://localhost:3000}"
export LKAP_LOG_LEVEL="${LKAP_LOG_LEVEL:-INFO}"
export NEXT_PUBLIC_API_BASE_URL="${NEXT_PUBLIC_API_BASE_URL:-http://localhost:8080}"
export PORT_API="${PORT_API:-8080}"
export PORT_WEB="${PORT_WEB:-3000}"

pids=()

cleanup() {
  # DECISIONS-W2 D-W2-13: SIGINT first, wait up to 15s for a clean shutdown
  # (livekit-agents `dev` mode has no drain, but still deserves the same
  # grace as `start` mode's drain window), then SIGKILL only what's left.
  # Never SIGKILL first — a killed worker leaves an `active` session row
  # behind for the sweep (D-W2-2b) to clean up, not something to invite.
  log "shutting down (SIGINT, up to 15s grace)..."
  for pid in "${pids[@]:-}"; do
    if kill -0 "${pid}" 2>/dev/null; then
      kill -INT "${pid}" 2>/dev/null || true
    fi
  done
  for _ in $(seq 1 15); do
    any_alive=false
    for pid in "${pids[@]:-}"; do
      if kill -0 "${pid}" 2>/dev/null; then
        any_alive=true
      fi
    done
    if [[ "${any_alive}" == false ]]; then
      break
    fi
    sleep 1
  done
  for pid in "${pids[@]:-}"; do
    if kill -0 "${pid}" 2>/dev/null; then
      log "pid ${pid} still alive after grace period; sending SIGKILL"
      kill -KILL "${pid}" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# --- api: migrate then serve (does NOT auto-migrate itself) -----------------
log "api: uv sync + alembic upgrade head"
(cd "${ROOT_DIR}/api" && uv sync --quiet && uv run alembic upgrade head)

log "api: starting on :${PORT_API}"
(cd "${ROOT_DIR}/api" && exec uv run uvicorn lkap_api.main:app --reload --host 127.0.0.1 --port "${PORT_API}") &
pids+=("$!")

# --- agent: worker in `dev` mode --------------------------------------------
log "agent: starting (dispatch name ${LIVEKIT_AGENT_NAME})"
(cd "${ROOT_DIR}/agent" && exec uv run python -m lkap_agent.main dev) &
pids+=("$!")

# --- web: next dev -----------------------------------------------------------
log "web: starting on :${PORT_WEB}"
(cd "${ROOT_DIR}/web" && exec pnpm dev --port "${PORT_WEB}") &
pids+=("$!")

log "api :${PORT_API}  web :${PORT_WEB}  agent (worker, no port)"
log "press Ctrl+C to stop all three"

# Exit (and take the others down via the trap) as soon as any one process
# dies. Polls instead of `wait -n` — this targets the system /bin/bash on
# macOS (3.2), which predates `wait -n` (bash 4.3+).
while :; do
  for pid in "${pids[@]}"; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      wait "${pid}"
      exit_code=$?
      log "service pid ${pid} exited (code ${exit_code}); stopping the rest"
      exit "${exit_code}"
    fi
  done
  sleep 1
done
