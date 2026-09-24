# lkap-web — Next.js 15 console + session app.
#
# Build context is this directory (web/). docs/ARCHITECTURE.md §14:
# "web -> `next build` standalone image (node:24-alpine)".
#
#   cd web && docker build -t lkap-web \
#     --build-arg NEXT_PUBLIC_API_BASE_URL=https://api.example.com .
#
# NEXT_PUBLIC_API_BASE_URL is inlined into the JS bundle AND into the
# server-side console proxy route (src/app/api/console/[...path]/route.ts —
# Next replaces `process.env.NEXT_PUBLIC_*` at build time everywhere it's
# referenced, server code included) at `next build` time, so it MUST be set
# as a build arg, not a runtime env var. It has to be one URL reachable by
# both the end user's browser and this container: point it at the api's
# public HTTPS origin, not an internal/compose-only hostname. See
# deploy/README.md for the local docker-compose workaround.
#
# F-21 (docs/REVIEW-FINAL.md): `corepack prepare pnpm@9.15.9` was unverifiable
# without Docker; confirmed live by V2-09 (2026-09-23) against both the npm
# registry (the version exists) and `web/package.json`'s own
# `"packageManager": "pnpm@9.15.9"` (matches — corepack will not warn/fail on
# a mismatch). Still unverified: an actual `docker build` of this file (no
# Docker daemon on this host either).

# --- deps ----------------------------------------------------------------
FROM node:24-alpine AS deps
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# --- builder ---------------------------------------------------------------
FROM node:24-alpine AS builder
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8080
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
# The remote MCP url the Connect dialog offers (https://<api origin>/mcp); inlined
# at build time like the one above. Empty means no "Remote (HTTP)" option (R-V3-31).
ARG NEXT_PUBLIC_LKAP_MCP_PUBLIC_URL=
ENV NEXT_PUBLIC_LKAP_MCP_PUBLIC_URL=${NEXT_PUBLIC_LKAP_MCP_PUBLIC_URL}
ENV NEXT_TELEMETRY_DISABLED=1

RUN pnpm build

# --- runtime ---------------------------------------------------------------
FROM node:24-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000

RUN addgroup --system --gid 1001 lkap && adduser --system --uid 1001 lkap

# `output: "standalone"` (web/next.config.ts) traces the minimal server +
# dependency graph into .next/standalone — no node_modules copy needed.
COPY --from=builder --chown=lkap:lkap /app/.next/standalone ./
COPY --from=builder --chown=lkap:lkap /app/.next/static ./.next/static
COPY --from=builder --chown=lkap:lkap /app/public ./public

USER lkap
EXPOSE 3000

# LKAP_ADMIN_TOKEN is runtime-only (never sent to the browser bundle) and
# must be supplied at `docker run`/compose time, not baked in here.
CMD ["node", "server.js"]
