# lkap-supervisor — worker fleet reconciler (CONTRACTS-V2 §5, D-V2-2).
#
# Build context MUST be the platform root (livekit_agent_platform/), not
# supervisor/, because `supervisor/pyproject.toml` depends on `lkap-contracts`
# via `[tool.uv.sources]` (`../contracts`, a local path dep) — Docker COPY
# cannot reach outside its context, so the sibling package has to be inside
# it. Mirrors api/Dockerfile exactly for this reason.
#
#   docker build -f supervisor/Dockerfile -t lkap-supervisor .   # from livekit_agent_platform/
#
# CONTRACTS-V2 §5: "Entry: `python -m lkap_supervisor` (`--once` for tests)."
# The `docker` backend (`LKAP_SUPERVISOR_BACKEND=docker`) talks to the host's
# Docker Engine API over a mounted `/var/run/docker.sock` — that mount, and
# the group membership needed to use it, are a compose/deploy concern (see
# `deploy/docker-compose.prod.yml`), not baked into this image.

# --- builder -----------------------------------------------------------
FROM python:3.12-bookworm-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /build

# Path dep first (lkap-contracts), then supervisor/ itself — dependency
# order keeps Docker's layer cache warm across supervisor-only edits.
COPY contracts/ contracts/
COPY supervisor/ supervisor/

# `--no-editable`: install lkap-contracts as a real site-packages copy, not
# an editable path link back into /build (which the runtime stage won't
# have). `--no-dev`: skip pytest/ruff/mypy. `--frozen`: fail rather than
# silently drift from supervisor/uv.lock.
WORKDIR /build/supervisor
RUN uv sync --frozen --no-dev --no-editable

# --- runtime -------------------------------------------------------------
FROM python:3.12-bookworm-slim AS runtime

RUN groupadd --system lkap && useradd --system --gid lkap --create-home lkap

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LKAP_LOG_JSON=true

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY supervisor/src/ /app/supervisor/src/

WORKDIR /app/supervisor

# `lkap_supervisor` is already installed into /app/.venv/lib/.../site-packages
# by `uv sync --no-editable` above (like api/Dockerfile, which is the pattern
# this mirrors) — `python -m lkap_supervisor` resolves it from there via the
# `PATH="/app/.venv/bin:..."` set above, not from the `src/` copy. `src/` is
# copied for the same reason api/Dockerfile copies `api/src/`: an inspectable
# source tree alongside the installed wheel, not a runtime dependency.

# Runs as `lkap` (non-root) by default — the `subprocess` backend only needs
# to spawn `python -m lkap_agent.main start` in the mounted `agent/` tree
# (LKAP_SUPERVISOR_AGENT_DIR), which does not need root. The `docker` backend
# needs the container's *user* to be in the `docker` group that owns the
# mounted socket's GID on the host; deploy/docker-compose.prod.yml sets
# `group_add: ["${LKAP_DOCKER_GID}"]` for exactly that, so this stays
# non-root even for the docker backend (never add `lkap` to root/wheel to
# work around it).
USER lkap

# No exposed port for the reconcile loop itself; `:9105` (default) serves
# Prometheus metrics (CONTRACTS-V2 §5) when the process runs in loop mode
# (not `--once`).
EXPOSE 9105

# SIGINT is what `__main__.py` treats as "stop the loop and drain" for the
# subprocess backend's children (mirrors the agent's own D-W2-13 contract);
# give the container a grace period >= LKAP_SUPERVISOR_DRAIN_S before
# SIGKILL (compose: `stop_grace_period`).
STOPSIGNAL SIGINT
CMD ["python", "-m", "lkap_supervisor"]
