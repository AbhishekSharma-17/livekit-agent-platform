# lkap-api — FastAPI admin/config API, credential vault, KB, sessions.
#
# Build context MUST be the platform root (livekit_agent_platform/), not
# api/, because `api/pyproject.toml` depends on `lkap-contracts` and
# `lkap-packs` via `[tool.uv.sources]` path deps (`../contracts`,
# `../packs`) — Docker COPY cannot reach outside its context, so the
# sibling packages have to be inside it. See deploy/docker-compose.yml
# (`build.context: ..`) and deploy/README.md.
#
#   docker build -f api/Dockerfile -t lkap-api .        # from livekit_agent_platform/
#
# docs/CONTRACTS.md §12: "Dockerfile = starter's `uv` multi-stage with
# python3.12-bookworm-slim"; docs/ARCHITECTURE.md §14: "api needs a
# persistent volume for LKAP_DATA_DIR", "uvicorn --workers 1" (LanceDB is a
# single-writer store).
#
# F-21 (docs/REVIEW-FINAL.md): the `ghcr.io/astral-sh/uv:0.12.15` pin below
# was unverifiable without Docker; confirmed live by V2-09 (2026-09-23) —
# that tag exists as a multi-arch image index on ghcr.io. Still unverified:
# an actual `docker build` of this file (no Docker daemon on this host either).
#
# In production, this process runs behind a reverse proxy (Caddy —
# deploy/docker-compose.prod.yml) that terminates TLS and forwards
# `X-Forwarded-For`/`-Proto`. The prod compose file's `command:` override adds
# `--proxy-headers --forwarded-allow-ips=<proxy-network-cidr>` so
# `Request.client.host` (used for per-IP rate limiting, `auth/deps.py`)
# reflects the real client instead of the proxy — not baked into this image's
# default CMD because the dev compose file talks to this container directly
# (no proxy in front) and a wrong `--forwarded-allow-ips` would let a
# non-proxy caller spoof its own IP. See deploy/docker-compose.prod.yml.

# --- builder -----------------------------------------------------------
FROM python:3.12-bookworm-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /build

# Path deps first (lkap-contracts, lkap-packs), then api/ itself — copying
# in dependency order keeps Docker's layer cache warm across api-only edits.
COPY contracts/ contracts/
COPY packs/ packs/
COPY api/ api/

# `--no-editable`: install lkap-contracts/lkap-packs as real site-packages,
# not editable path links back into /build (which the runtime stage won't
# have). `--no-dev`: skip pytest/ruff/mypy. `--frozen`: fail rather than
# silently drift from api/uv.lock.
WORKDIR /build/api
RUN uv sync --frozen --no-dev --no-editable

# --- runtime -------------------------------------------------------------
FROM python:3.12-bookworm-slim AS runtime

RUN groupadd --system lkap && useradd --system --gid lkap --create-home lkap \
    && mkdir -p /data && chown lkap:lkap /data

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LKAP_DATA_DIR=/data \
    LKAP_LOG_JSON=true \
    PORT=8080

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY api/alembic.ini /app/api/alembic.ini
COPY api/alembic/ /app/api/alembic/
COPY api/src/ /app/api/src/

WORKDIR /app/api
USER lkap

VOLUME ["/data"]
EXPOSE 8080

# The api does NOT auto-migrate (by design — see W1-API-CORE / CONTRACTS
# §12): run migrations as an explicit, separate step before first boot and
# after any image update that changes the schema:
#   docker compose -f deploy/docker-compose.yml run --rm api alembic upgrade head
# The image's default command only starts the server.
CMD ["sh", "-c", "exec uvicorn lkap_api.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
