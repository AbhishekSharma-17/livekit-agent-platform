#!/usr/bin/env bash
# LKAP v2 end-to-end smoke (PLAN-V2 V2-19): boot compose dev, log in, create a
# LiveKit connection from the environment, bind a generic agent to it and run a
# text-mode round trip through a supervised worker.
#
# Secrets come only from the environment (never from `.env*` files; export them
# yourself first, as with scripts/dev.sh):
#   LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET   the connection to create
#   LKAP_SMOKE_EMAIL + LKAP_SMOKE_PASSWORD              sign in with a real user, or
#   LKAP_ADMIN_TOKEN                                    the dev break-glass token instead
# deploy/api.env, deploy/web.env and deploy/supervisor.env must exist (copied
# from the *.env.example files, deploy/README.md §4) for the compose boot.
#
# Safety: the worker it starts registers under a fresh agent name
# (`lkap-smoke-<ts>`), never `lkap-agent`, so it cannot split dispatch with a
# worker you already run on the same LiveKit project.
#
# Usage:
#   scripts/smoke_v2.sh              full run (needs Docker): boot, write, round trip, tear down
#   scripts/smoke_v2.sh --keep       ... and leave the compose stack running
#   scripts/smoke_v2.sh --no-boot    use an api that is already up at $LKAP_SMOKE_API_URL
#   scripts/smoke_v2.sh --dry-run    READ-ONLY checks against a running api, no Docker,
#                                    no writes; prints the write steps it would take
# Env: LKAP_SMOKE_API_URL (default http://127.0.0.1:8080),
#      LKAP_SMOKE_TIMEOUT_S (default 300; waits for health and for the worker).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE=(docker compose -f "${ROOT_DIR}/deploy/docker-compose.dev.yml")
API="${LKAP_SMOKE_API_URL:-http://127.0.0.1:8080}"
TIMEOUT_S="${LKAP_SMOKE_TIMEOUT_S:-300}"
STAMP="$(date -u +%Y%m%d%H%M%S)"
WORK_DIR="$(mktemp -d)"
COOKIES="${WORK_DIR}/cookies.txt"

DRY_RUN=false
BOOT=true
KEEP=false
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=true; BOOT=false ;;
    --no-boot) BOOT=false ;;
    --keep) KEEP=true ;;
    -h | --help) sed -n '2,27p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) printf 'smoke_v2: unknown argument: %s\n' "${arg}" >&2; exit 2 ;;
  esac
done

log() { printf '[smoke_v2] %s\n' "$1" >&2; }
pass() { printf '[smoke_v2] PASS %s\n' "$1" >&2; }
die() { printf '[smoke_v2] FAIL %s\n' "$1" >&2; exit 1; }

BOOTED=false
cleanup() {
  rm -rf "${WORK_DIR}"
  if [[ "${BOOTED}" == true && "${KEEP}" == false ]]; then
    log "tearing down the compose stack (use --keep to leave it up)"
    "${COMPOSE[@]}" down >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# json <file> <python expression over `d`>: print one value from a JSON body.
json() {
  python3 - "$1" "$2" <<'PY'
import json, sys
with open(sys.argv[1]) as fh:
    d = json.load(fh)
value = eval(sys.argv[2], {"d": d})
print(json.dumps(value) if isinstance(value, (dict, list, bool)) or value is None else value)
PY
}

AUTH_ARGS=()
# call <METHOD> <path> [json-body] → writes the body to $WORK_DIR/body.json, echoes the status.
call() {
  local method="$1" path="$2" body="${3:-}"
  local args=(-sS -o "${WORK_DIR}/body.json" -w '%{http_code}' -X "${method}" -b "${COOKIES}" -c "${COOKIES}"
    -H 'Content-Type: application/json' ${AUTH_ARGS[@]+"${AUTH_ARGS[@]}"})
  if [[ "${DRY_RUN}" == true && "${method}" != GET ]]; then
    die "dry-run refused a ${method} ${path} (read-only mode)"
  fi
  # Bodies (passwords, api secrets) go through stdin, never argv: `ps` shows argv to every user (V2-21).
  if [[ -n "${body}" ]]; then
    printf '%s' "${body}" | curl "${args[@]}" --data-binary @- "${API}${path}"
  else
    curl "${args[@]}" "${API}${path}" </dev/null
  fi
}

expect() {  # expect <status> <METHOD> <path> [body]
  local want="$1"; shift
  local got
  got="$(call "$@")" || die "$2 $3: request failed"
  [[ "${got}" == "${want}" ]] || die "$2 $3 → HTTP ${got} (wanted ${want}): $(head -c 400 "${WORK_DIR}/body.json")"
}

wait_for_health() {
  local deadline=$((SECONDS + TIMEOUT_S))
  until curl -fsS -o "${WORK_DIR}/health.json" "${API}/v1/health" 2>/dev/null \
    && [[ "$(json "${WORK_DIR}/health.json" 'd["db"]')" == ok ]]; do
    (( SECONDS < deadline )) || die "GET /v1/health not ok with db=ok within ${TIMEOUT_S}s"
    sleep 3
  done
}

# ------------------------------------------------------------------ 0. prerequisites
command -v curl >/dev/null || die "curl is required"
command -v python3 >/dev/null || die "python3 is required"
if [[ "${DRY_RUN}" == false ]]; then
  for var in LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET; do
    [[ -n "${!var:-}" ]] || die "export ${var} first (the connection the smoke creates)"
  done
  command -v uv >/dev/null || die "uv is required (runs the text round trip in the agent venv)"
fi
if [[ -z "${LKAP_SMOKE_EMAIL:-}" && -z "${LKAP_ADMIN_TOKEN:-}" ]]; then
  die "export LKAP_SMOKE_EMAIL + LKAP_SMOKE_PASSWORD, or LKAP_ADMIN_TOKEN (dev break-glass)"
fi

# ------------------------------------------------------------------ 1. boot compose dev
if [[ "${BOOT}" == true ]]; then
  command -v docker >/dev/null || die "docker is required for the full run (or use --no-boot / --dry-run)"
  docker info >/dev/null 2>&1 || die "Docker is not running"
  for f in api web supervisor; do
    [[ -f "${ROOT_DIR}/deploy/${f}.env" ]] || die "deploy/${f}.env is missing (copy deploy/${f}.env.example)"
  done
  log "building the images and booting deploy/docker-compose.dev.yml"
  "${COMPOSE[@]}" up -d --build
  BOOTED=true
  "${COMPOSE[@]}" run --rm api alembic upgrade head
fi

# ------------------------------------------------------------------ 2. health
wait_for_health
[[ "$(json "${WORK_DIR}/health.json" '"generic" in d["packs"]')" == true ]] || die "the api has no generic pack"
pass "health: db ok, packs $(json "${WORK_DIR}/health.json" 'd["packs"]')"

# ------------------------------------------------------------------ 3. sign in
if [[ -n "${LKAP_SMOKE_EMAIL:-}" && "${DRY_RUN}" == false ]]; then
  [[ -n "${LKAP_SMOKE_PASSWORD:-}" ]] || die "LKAP_SMOKE_PASSWORD is required with LKAP_SMOKE_EMAIL"
  login_body="$(python3 -c 'import json,os; print(json.dumps({"email": os.environ["LKAP_SMOKE_EMAIL"], "password": os.environ["LKAP_SMOKE_PASSWORD"]}))')"
  expect 204 POST /v1/auth/login "${login_body}"
  pass "signed in as ${LKAP_SMOKE_EMAIL}"
else
  [[ -n "${LKAP_ADMIN_TOKEN:-}" ]] || die "--dry-run reads with LKAP_ADMIN_TOKEN (no login POST in read-only mode)"
  # The token goes in a 0600 header file, not argv (V2-21).
  (umask 077 && printf 'X-Admin-Token: %s\n' "${LKAP_ADMIN_TOKEN}" > "${WORK_DIR}/admin.headers")
  AUTH_ARGS=(-H "@${WORK_DIR}/admin.headers")
  log "using the break-glass admin token (dev only; the api refuses it unless LKAP_ALLOW_ADMIN_TOKEN is on)"
fi
expect 200 GET /v1/auth/me
pass "GET /v1/auth/me"

# ------------------------------------------------------------------ dry run: read-only checks
if [[ "${DRY_RUN}" == true ]]; then
  expect 200 GET /v1/packs
  pass "GET /v1/packs: $(json "${WORK_DIR}/body.json" '[p["manifest"]["id"] for p in d["items"]]')"
  expect 200 GET /v1/providers
  pass "GET /v1/providers: $(json "${WORK_DIR}/body.json" 'len(d["providers"])') registry entries"
  expect 200 GET /v1/connections
  pass "GET /v1/connections: $(json "${WORK_DIR}/body.json" 'len(d["items"])') connection(s), default: $(json "${WORK_DIR}/body.json" '[c["slug"] for c in d["items"] if c.get("is_default")]')"
  expect 200 GET /v1/agents
  pass "GET /v1/agents: $(json "${WORK_DIR}/body.json" 'len(d["items"])') agent(s)"
  expect 200 GET /v1/sessions
  pass "GET /v1/sessions: reachable"
  log "dry run done; a full run would now:"
  log "  POST /v1/connections {slug: smoke-${STAMP}, deployment_mode: supervised, agent_name: lkap-smoke-${STAMP}} from LIVEKIT_*"
  log "  POST /v1/connections/{id}/test, POST /v1/connections/{id}/fleet {action: start, replicas: 1}"
  log "  POST /v1/agents {pack_id: generic, connection_id}, PUT {published: true}"
  log "  POST /v1/agents/{id}/text-sessions, send 'lk.chat' and wait for the agent's 'lk.transcription'"
  log "  GET /v1/sessions/{id}: transcript has the exchange; then stop the pool"
  exit 0
fi

# ------------------------------------------------------------------ 4. connection from env
deployment_type=self_hosted
[[ "${LIVEKIT_URL}" == *".livekit.cloud"* ]] && deployment_type=cloud
connection_body="$(STAMP="${STAMP}" DT="${deployment_type}" python3 -c '
import json, os
stamp = os.environ["STAMP"]
print(json.dumps({
    "slug": "smoke-" + stamp,
    "name": "Smoke " + stamp,
    "deployment_type": os.environ["DT"],
    "url": os.environ["LIVEKIT_URL"],
    "api_key": os.environ["LIVEKIT_API_KEY"],
    "api_secret": os.environ["LIVEKIT_API_SECRET"],
    "agent_name": "lkap-smoke-" + stamp,
    "deployment_mode": "supervised",
    "replicas": 1,
}))')"
expect 201 POST /v1/connections "${connection_body}"
CONNECTION_ID="$(json "${WORK_DIR}/body.json" 'd["id"]')"
pass "created connection smoke-${STAMP} (${CONNECTION_ID})"
expect 200 POST "/v1/connections/${CONNECTION_ID}/test"
[[ "$(json "${WORK_DIR}/body.json" 'd["ok"]')" == true ]] || die "connection test failed: $(cat "${WORK_DIR}/body.json")"
pass "connection test ok"

stop_pool() { call POST "/v1/connections/${CONNECTION_ID}/fleet" '{"action":"stop"}' >/dev/null || true; }
trap 'stop_pool; cleanup' EXIT

# ------------------------------------------------------------------ 5. generic agent bound to it
agent_body="$(STAMP="${STAMP}" CID="${CONNECTION_ID}" python3 -c '
import json, os
print(json.dumps({"name": "Smoke " + os.environ["STAMP"], "pack_id": "generic", "connection_id": os.environ["CID"]}))')"
expect 201 POST /v1/agents "${agent_body}"
AGENT_ID="$(json "${WORK_DIR}/body.json" 'd["id"]')"
[[ "$(json "${WORK_DIR}/body.json" 'd["connection_id"]')" == "${CONNECTION_ID}" ]] || die "agent not bound to the connection"
expect 200 PUT "/v1/agents/${AGENT_ID}" '{"published": true}'
pass "generic agent ${AGENT_ID} bound and published"

# ------------------------------------------------------------------ 6. supervised worker
expect 200 POST "/v1/connections/${CONNECTION_ID}/fleet" '{"action":"start","replicas":1}'
deadline=$((SECONDS + TIMEOUT_S))
until expect 200 GET "/v1/connections/${CONNECTION_ID}/fleet" \
  && [[ "$(json "${WORK_DIR}/body.json" 'any(i["status"] == "ready" for i in d["instances"])')" == true ]]; do
  (( SECONDS < deadline )) || die "no ready worker for smoke-${STAMP} within ${TIMEOUT_S}s (supervisor logs: docker compose -f deploy/docker-compose.dev.yml logs supervisor)"
  sleep 5
done
pass "a supervised worker is ready"

# ------------------------------------------------------------------ 7. text-mode round trip
expect 200 POST "/v1/agents/${AGENT_ID}/text-sessions" '{}'
cp "${WORK_DIR}/body.json" "${WORK_DIR}/text-session.json"
SESSION_ID="$(json "${WORK_DIR}/text-session.json" 'd["sessionId"]')"
(cd "${ROOT_DIR}/agent" && uv run --quiet python - "${WORK_DIR}/text-session.json") <<'PY' || die "text round trip failed"
import asyncio, json, sys

from livekit import rtc


async def main() -> None:
    with open(sys.argv[1]) as fh:
        details = json.load(fh)
    room = rtc.Room()
    replies: asyncio.Queue[str] = asyncio.Queue()

    def on_transcription(reader: rtc.TextStreamReader, participant_identity: str) -> None:
        async def read() -> None:
            text = await reader.read_all()
            if text.strip():
                await replies.put(text)

        asyncio.ensure_future(read())

    room.register_text_stream_handler("lk.transcription", on_transcription)
    await room.connect(details["serverUrl"], details["participantToken"])
    try:
        for _ in range(60):  # wait for the agent to join
            if room.remote_participants:
                break
            await asyncio.sleep(1)
        else:
            raise SystemExit("the agent never joined the room")
        await asyncio.sleep(2)
        while not replies.empty():  # drop the greeting
            replies.get_nowait()
        await room.local_participant.send_text("Reply with the single word pong.", topic="lk.chat")
        reply = await asyncio.wait_for(replies.get(), timeout=90)
        print(f"[smoke_v2] agent replied: {reply[:120]!r}", file=sys.stderr)
    finally:
        await room.disconnect()


asyncio.run(main())
PY
pass "text round trip"

# ------------------------------------------------------------------ 8. the session row
deadline=$((SECONDS + 60))
until expect 200 GET "/v1/sessions/${SESSION_ID}" \
  && [[ "$(json "${WORK_DIR}/body.json" 'd["status"] in ("ended", "failed") and len(d.get("transcript") or []) >= 2')" == true ]]; do
  (( SECONDS < deadline )) || die "session ${SESSION_ID} did not end with a transcript within 60s"
  sleep 3
done
[[ "$(json "${WORK_DIR}/body.json" 'd["channel"]')" == text ]] || die "session channel is not text"
pass "session ${SESSION_ID}: channel text, $(json "${WORK_DIR}/body.json" 'd["status"]'), $(json "${WORK_DIR}/body.json" 'len(d["transcript"])') transcript turns"

log "smoke passed"
