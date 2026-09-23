#!/usr/bin/env bash
# Back up the Postgres database and LKAP_DATA_DIR (local storage / LanceDB /
# SQLite fallback) to S3 (PLAN-V2 §V2-09).
#
# Intended to run on a schedule against the prod compose stack (host cron or
# a scheduled task runner — Phase 1 ships the script, not a scheduler):
#   0 3 * * * cd /opt/lkap && LKAP_BACKUP_S3_BUCKET=lkap-backups scripts/backup.sh >> /var/log/lkap-backup.log 2>&1
#
# Reads secrets only from the environment (never from `.env*` — this repo's
# convention throughout, docs/v2/CONTRACTS-V2.md §6). Export the required
# vars in the shell that runs this script (e.g. from deploy/prod.env /
# deploy/api.env via `set -a; source deploy/prod.env; set +a`, done by the
# operator, never by this script or by Claude).
#
# Required:
#   LKAP_BACKUP_S3_BUCKET   — destination bucket (or bucket/prefix)
# One of, for Postgres (skip the pg_dump leg entirely if neither is set —
# e.g. a dev/sqlite-only deployment has no Postgres to dump):
#   LKAP_DATABASE_URL       — postgresql[+asyncpg]://user:pass@host:port/db
#   PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE — libpq's own env vars
# For the LKAP_DATA_DIR tar leg:
#   LKAP_DATA_DIR           — default /data (matches the api/agent images' own default)
# For the S3 upload (either works; explicit vars win):
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION, or
#   LKAP_STORAGE_ACCESS_KEY / LKAP_STORAGE_SECRET_KEY / LKAP_STORAGE_REGION
#   LKAP_BACKUP_S3_ENDPOINT_URL — set this to MinIO's URL (e.g.
#     http://minio:9000, or https://<public-minio-host> from outside compose)
#     when backing up to the same MinIO the platform uses for storage,
#     instead of real AWS S3.
#
# Tooling: needs `pg_dump` (skipped with a warning if Postgres env is unset
# or the binary is missing — never a hard failure for a sqlite-only
# deployment) and the `aws` CLI (hard failure if missing; this script does
# not vendor an S3 client). Neither is installed by any Dockerfile this
# package owns — run this on the host, or in a small sidecar image that has
# both, not inside the `api`/`postgres` containers as shipped.
#
# Restore is deliberately NOT automated here (a restore is a decide-then-act
# human operation, docs/RUNBOOK.md v2 territory — V2-19): this script only
# produces the two artifacts a restore needs, named with a timestamp, and
# prints the exact `aws s3 cp` / `pg_restore` / `tar` commands to reverse it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

log() { printf '[backup] %s\n' "$1" >&2; }
die() { printf '[backup] ERROR: %s\n' "$1" >&2; exit 1; }

: "${LKAP_BACKUP_S3_BUCKET:?set LKAP_BACKUP_S3_BUCKET (destination bucket, optionally bucket/prefix)}"
LKAP_DATA_DIR="${LKAP_DATA_DIR:-/data}"

command -v aws >/dev/null 2>&1 || die "aws CLI not found on PATH — install it or run this on a host that has it"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

AWS_ARGS=()
if [[ -n "${LKAP_BACKUP_S3_ENDPOINT_URL:-}" ]]; then
  AWS_ARGS+=(--endpoint-url "${LKAP_BACKUP_S3_ENDPOINT_URL}")
fi
# Fall back to the platform's own storage credentials when the standard AWS
# ones are not set (dev/MinIO convenience — see the header comment).
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-${LKAP_STORAGE_ACCESS_KEY:-}}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-${LKAP_STORAGE_SECRET_KEY:-}}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-${LKAP_STORAGE_REGION:-us-east-1}}"

s3_url() { printf 's3://%s/%s' "${LKAP_BACKUP_S3_BUCKET%/}" "$1"; }

# --- Postgres (pg_dump), skipped entirely if not configured -----------------
have_postgres=false
if [[ -n "${LKAP_DATABASE_URL:-}" ]]; then
  have_postgres=true
elif [[ -n "${PGHOST:-}${PGDATABASE:-}" ]]; then
  have_postgres=true
fi

if [[ "${have_postgres}" == true ]]; then
  if ! command -v pg_dump >/dev/null 2>&1; then
    log "WARNING: pg_dump not found on PATH; skipping the Postgres leg"
  else
    dump_file="${WORK_DIR}/postgres-${TIMESTAMP}.dump"
    log "pg_dump -> ${dump_file}"
    if [[ -n "${LKAP_DATABASE_URL:-}" ]]; then
      # pg_dump wants a plain postgresql:// DSN, not SQLAlchemy's +asyncpg driver suffix.
      dsn="${LKAP_DATABASE_URL/postgresql+asyncpg:/postgresql:}"
      pg_dump --format=custom --file="${dump_file}" --dbname="${dsn}"
    else
      pg_dump --format=custom --file="${dump_file}"
    fi
    dest="$(s3_url "postgres/postgres-${TIMESTAMP}.dump")"
    log "uploading -> ${dest}"
    aws s3 cp "${AWS_ARGS[@]}" "${dump_file}" "${dest}"
    log "restore with: pg_restore --clean --if-exists --dbname=<target-dsn> ${dump_file##*/} (after: aws s3 cp ${dest} .)"
  fi
else
  log "LKAP_DATABASE_URL/PGHOST/PGDATABASE unset; skipping the Postgres leg (sqlite-only deployment)"
fi

# --- LKAP_DATA_DIR (local storage / LanceDB / sqlite fallback) --------------
if [[ ! -d "${LKAP_DATA_DIR}" ]]; then
  log "WARNING: LKAP_DATA_DIR (${LKAP_DATA_DIR}) does not exist; skipping the data-dir leg"
else
  tar_file="${WORK_DIR}/data-${TIMESTAMP}.tar.gz"
  log "tar ${LKAP_DATA_DIR} -> ${tar_file}"
  tar -czf "${tar_file}" -C "$(dirname "${LKAP_DATA_DIR}")" "$(basename "${LKAP_DATA_DIR}")"
  dest="$(s3_url "data/data-${TIMESTAMP}.tar.gz")"
  log "uploading -> ${dest}"
  aws s3 cp "${AWS_ARGS[@]}" "${tar_file}" "${dest}"
  log "restore with: aws s3 cp ${dest} . && tar -xzf data-${TIMESTAMP}.tar.gz -C $(dirname "${LKAP_DATA_DIR}")"
fi

log "done (${TIMESTAMP})"
