# lkap-agent — LiveKit worker (dispatch name "lkap-agent").
#
# Build context is THIS directory (agent/) — deliberately, because that is
# what `lk agent create`/`lk agent deploy` actually build from (the
# directory containing livekit.toml; verified: `lk agent dockerfile` and
# `lk agent create` take a single `[working-dir]`, no separate context
# flag). That means `../contracts` and `../packs` — which
# `agent/pyproject.toml` depends on as LOCAL PATH deps via
# `[tool.uv.sources]` — are NOT visible to this build.
#
# Fix: run `scripts/vendor_agent_deps.sh` FIRST (from the platform root).
# It builds real wheels for `lkap-contracts`/`lkap-packs` into
# `agent/vendor/*.whl`. This Dockerfile installs every other dependency
# normally from `agent/uv.lock`, explicitly skipping those two path-based
# entries (`--no-install-package`, verified to skip touching `../contracts`
# / `../packs` at all — see scripts/vendor_agent_deps.sh), then installs the
# two vendored wheels directly. Local build:
#
#   cd livekit_agent_platform
#   scripts/vendor_agent_deps.sh
#   docker build -f agent/Dockerfile -t lkap-agent agent
#
# Same two steps before `lk agent create --secrets-file secrets.env` / `lk
# agent deploy` from `agent/` — see deploy/README.md §2.
#
# --- image flavors (CONTRACTS-V2 §7, D-V2-8) --------------------------------
#
# `LKAP_IMAGE_FLAVOR` (`slim` default, or `full`) selects
# `requirements/{flavor}.txt` — the provider-plugin packages CONTRACTS-V2 §7
# assigns to that flavor, generated from the registry by
# `scripts/gen_plugin_requirements.py` (run it after any registry change; CI
# checks the two are in sync via `--check`). They are layered on top of
# `uv sync --frozen`'s own v1 baseline via a plain `uv pip install`, one
# package at a time so a single failing wheel (e.g. an out-of-tree native
# package) is reported without aborting before every other package is tried
# — CONTRACTS-V2 §7 / PLAN-V2 §8 risk table: "the build gate is the import
# check; failures ... must either be fixed or the entry flipped to deferred".
# `requirements/check_installed_providers.py` is a build-context-local mirror
# of `scripts/check_installed_providers.py` (this directory has no visibility
# outside itself — see the build-context note above); it imports every
# selected provider's `python_class` for real and writes
# `/app/installed_providers.json`, which the worker reads at start and sends
# in `POST /internal/v1/workers/register` (CONTRACTS-V2 §5).
#
#   docker build --build-arg LKAP_IMAGE_FLAVOR=slim -f agent/Dockerfile -t lkap-agent:slim agent
#   docker build --build-arg LKAP_IMAGE_FLAVOR=full -f agent/Dockerfile -t lkap-agent:full agent

# --- builder ---------------------------------------------------------------
FROM python:3.12-bookworm-slim AS builder

ARG LKAP_IMAGE_FLAVOR=slim

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_NO_SYNC=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src/ src/
COPY vendor/ vendor/
COPY requirements/ requirements/
RUN test -n "$(ls -A vendor/*.whl 2>/dev/null)" || \
    { echo "agent/vendor/*.whl missing — run scripts/vendor_agent_deps.sh first" >&2; exit 1; }
RUN test -f "requirements/${LKAP_IMAGE_FLAVOR}.txt" || \
    { echo "agent/requirements/${LKAP_IMAGE_FLAVOR}.txt missing — run scripts/gen_plugin_requirements.py first" >&2; exit 1; }

# `--no-install-package`: lkap-contracts/lkap-packs are path deps
# (`../contracts`, `../packs`) this context doesn't have; skip them here and
# install the vendored wheels next. `--no-editable`: everything else
# installs as a real site-packages copy, not a link back into /app (which
# the runtime stage keeps, but keep both stages' layouts identical).
# `--frozen`: fail rather than silently drift from agent/uv.lock.
RUN uv sync --frozen --no-editable \
    --no-install-package lkap-contracts \
    --no-install-package lkap-packs
RUN uv pip install --python /app/.venv/bin/python --no-deps vendor/*.whl

# `--no-deps` above trusts that lkap-contracts/lkap-packs' own runtime deps
# are already satisfied by agent/uv.lock (true today: pydantic,
# livekit-agents). This smoke-checks that assumption on every build instead
# of letting it fail silently — if packs/contracts ever gain a dependency
# agent/ doesn't also declare, this line breaks the build here, not at
# start-up in prod. `--no-sync` is required: plain `uv run` re-validates the
# venv against the lockfile and fails trying to reach `../contracts`/
# `../packs`, which this build context doesn't have (verified empirically).
RUN uv run --no-sync python -c "import lkap_agent, lkap_contracts, packs"

# Install this flavor's provider-plugin packages one at a time (not a single
# `uv pip install -r`, which resolves-then-aborts on the first bad
# requirement and would hide every other package's status): every attempt is
# logged, and any failure fails the build only after every package has been
# tried, per CONTRACTS-V2 §7's "any failure is listed in the build log".
RUN set -eu; \
    fail=0; \
    while IFS= read -r requirement; do \
      case "${requirement}" in ''|'#'*) continue ;; esac; \
      echo "install: ${requirement}"; \
      if ! uv pip install --python /app/.venv/bin/python "${requirement}"; then \
        echo "FAILED: ${requirement}" >&2; \
        fail=1; \
      fi; \
    done < "requirements/${LKAP_IMAGE_FLAVOR}.txt"; \
    exit "${fail}"

# The build-time import gate (D-V2-8): import every selected provider's
# `python_class` for real and write the manifest the worker reports at start.
# uv/pip resolving a package above is not the acceptance criterion; this is.
RUN uv run --no-sync python requirements/check_installed_providers.py \
    --flavor "${LKAP_IMAGE_FLAVOR}" --out /app/installed_providers.json

# Silero VAD weights (and any other plugin download_files step) are written
# straight into the installed packages under .venv/ — running this after
# `uv sync` *and* the flavor's extra installs (turn-detector's model is a
# `download_files` step too) populates site-packages, so the files exist in
# the layer this stage hands to the runtime stage below.
RUN uv run --module livekit.agents download-files

# --- runtime -----------------------------------------------------------------
FROM python:3.12-bookworm-slim AS runtime

ARG LKAP_IMAGE_FLAVOR=slim

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /uvx /usr/local/bin/

RUN groupadd --system lkap && useradd --system --gid lkap --create-home lkap

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_NO_SYNC=1 \
    LKAP_LOG_JSON=true \
    LKAP_IMAGE_FLAVOR=${LKAP_IMAGE_FLAVOR}

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/installed_providers.json /app/installed_providers.json
COPY pyproject.toml uv.lock ./
COPY src/ src/
COPY livekit.toml ./

# LKAP_IMAGE_FLAVOR is also baked in as a runtime env var (not just a build
# ARG) so the worker process can log/report which flavor it is without
# re-deriving it from installed_providers.json.
USER lkap

# No exposed port — this is a worker, not a server (docs/CONTRACTS.md §12).
#
# Required at run time (LiveKit Cloud injects LIVEKIT_URL/API_KEY/API_SECRET;
# everything else comes from the secrets set at `lk agent create` /
# `update-secrets` — LKAP_API_BASE_URL, LKAP_SERVICE_TOKEN, LKAP_PACKS):
#   LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
#   LKAP_API_BASE_URL, LKAP_SERVICE_TOKEN, LKAP_PACKS
# The dispatch name is fixed in code (DECISIONS-W2 §D-W2-11):
# `rtc_session(agent_name="lkap-agent", on_request=only_lkap_jobs)`.
# LIVEKIT_AGENT_NAME / LIVEKIT_AGENT_NAME_OVERRIDE are optional; if either is
# set it must equal "lkap-agent" or the worker refuses to start, and every job
# not dispatched to "lkap-agent" is rejected — so this worker can never take
# automatic dispatch for the shared project (e.g. other-project-agent's rooms).
# `UV_NO_SYNC=1`: `uv run` uses the venv baked in above as-is, no network
# resolve at container start (docs/CONTRACTS.md §3 D-W2-6: Inference
# credentials are read from LIVEKIT_API_KEY/SECRET in the process env by the
# SDK itself, not passed as constructor kwargs — nothing extra needed here).
# DECISIONS-W2 §D-W2-13: the first SIGINT/SIGTERM starts a drain in `start`
# mode (stay registered, finish running calls, accept none) for up to the
# SDK's drain_timeout (3600 s in livekit-agents 1.8.2); a second signal
# os._exit(1)s. SIGINT is the signal the SDK's CLI is written around; any
# orchestrator must give the container a grace period >= drain_timeout
# before SIGKILL (compose: `stop_grace_period`, see deploy/docker-compose.yml).
STOPSIGNAL SIGINT
CMD ["uv", "run", "python", "-m", "lkap_agent.main", "start"]
