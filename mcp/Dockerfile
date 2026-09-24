# lkap-mcp — the remote streamable-HTTP MCP service (V3-06, docs/v3/AGENT-ACCESS.md §9).
#
# Build context MUST be the platform root (livekit_agent_platform/), like
# api/Dockerfile and supervisor/Dockerfile: `mcp/pyproject.toml` depends on
# `lkap-contracts` through a `[tool.uv.sources]` path dep (`../contracts`), and
# Docker COPY cannot reach outside its context.
#
#   docker build -f mcp/Dockerfile -t lkap-mcp .        # from livekit_agent_platform/
#
# `uv sync --no-editable` installs lkap-contracts and lkap-mcp as real
# site-packages copies (wheels built in the builder stage), which is what the
# agent image gets from its vendored wheels; the agent image vendors only
# because `lk agent deploy` forces its build context to agent/. The dev group
# (lkap-api, lkap-packs, lkap-testing: the test harness) is never installed.
# `livekit==1.1.18` (the rtc wheel the test chat joins rooms with) comes from
# PyPI as a manylinux wheel.
#
# Runtime: non-root `lkap`, `lkap-mcp --http` on 0.0.0.0:8090 inside the compose
# network only (prod publishes no port; Caddy proxies https://<api origin>/mcp
# here). The service holds no key and no platform token: it refuses to start
# with LKAP_API_KEY, LKAP_SERVICE_TOKEN, LKAP_ADMIN_TOKEN or LKAP_MASTER_KEY in
# its environment, and with LKAP_ENV=prod unless LKAP_MCP_PUBLIC_URL is https.
# `/healthz` is the only unauthenticated path.

# --- builder -----------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /build

# Path dep first (lkap-contracts), then mcp/ itself: dependency order keeps the
# layer cache warm across mcp-only edits.
COPY contracts/ contracts/
COPY mcp/ mcp/

# `--no-dev`: skip the test harness (its path deps are not in this context).
# The three `--no-install-package` flags say the same thing explicitly.
# `--frozen`: fail rather than drift from mcp/uv.lock.
WORKDIR /build/mcp
RUN uv sync --frozen --no-dev --no-editable \
    --no-install-package lkap-api \
    --no-install-package lkap-packs \
    --no-install-package lkap-testing

# The wheel must carry the docs and generated resources the tools serve, and
# the rtc wheel must load: fail the build here, not at the first session.
RUN /app/.venv/bin/python -c "import livekit.rtc, lkap_contracts, lkap_mcp.http; from lkap_mcp import content; assert content.read_doc('guide.md'), 'docs/guide.md missing from the wheel'; assert content.recipe('connect-livekit'), 'docs/recipes missing'; assert (content.GENERATED_DIR / 'providers.json').is_file(), 'generated/ missing'"

# --- runtime -------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

RUN groupadd --system lkap && useradd --system --gid lkap --create-home lkap

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LKAP_MCP_HTTP=1 \
    LKAP_MCP_HTTP_HOST=0.0.0.0 \
    LKAP_MCP_HTTP_PORT=8090

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv

USER lkap
EXPOSE 8090

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=4)"]

CMD ["lkap-mcp", "--http"]
