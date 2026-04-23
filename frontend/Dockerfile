# syntax=docker/dockerfile:1.9

# =========================================================================
# Stage 1: Dependencies
# Chainguard node-dev = Wolfi-based, rolls forward to latest patched Node.
# --ignore-scripts blocks arbitrary postinstall execution (supply-chain).
# =========================================================================
FROM cgr.dev/chainguard/node:latest-dev AS deps
USER root
WORKDIR /app

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund --ignore-scripts --prefer-offline

# =========================================================================
# Stage 2: Builder — compiles Next.js into the `standalone` server output.
# NODE_ENV is intentionally NOT set to `production` here: next build needs
# devDependencies (typescript, @types/*, tailwind) to compile. It is set on
# the runtime stage, which is what the Next.js server actually reads.
# =========================================================================
FROM cgr.dev/chainguard/node:latest-dev AS builder
USER root
WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=deps /app/node_modules ./node_modules
COPY . .

RUN npm run build && \
    rm -rf .next/cache

# =========================================================================
# Stage 3: Runtime — Chainguard Wolfi with ONLY the `nodejs` apk package.
#
# Why not `cgr.dev/chainguard/node`? That image bundles `npm`, and npm
# vendors its own node_modules (release-please → @xmldom/xmldom, @octokit/*,
# uuid). Those packages are unreachable at runtime (no shell, npm never
# invoked) but scanners still flag them.
#
# Why not `gcr.io/distroless/nodejs22-debian12`? Google's distroless lags
# Node patch releases (22.22.0 vs the current 22.22.2+ with fixes for
# CVE-2025-55130 and friends). Chainguard Wolfi rolls forward continuously.
#
# `apk add nodejs` pulls node only (npm is a separate `npm` package we do
# not install). Result: rolling-patched Node, no npm bundle, no xmldom CVEs.
# =========================================================================
FROM cgr.dev/chainguard/wolfi-base:latest AS runner
LABEL org.opencontainers.image.description="Frontend (Next.js) for VCS-Hybrid App" \
      org.opencontainers.image.source="https://github.com/Yawkata/nekvorepo"

USER root
# wolfi-base already ships the `nonroot` user/group (uid/gid 65532).
RUN apk add --no-cache nodejs && \
    mkdir -p /app /home/nonroot && \
    chown -R 65532:65532 /app /home/nonroot

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

COPY --from=builder --chown=65532:65532 /app/.next/standalone ./
COPY --from=builder --chown=65532:65532 /app/.next/static    ./.next/static
COPY --from=builder --chown=65532:65532 /app/public          ./public

USER nonroot
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["/usr/bin/node", "-e", "require('http').get('http://127.0.0.1:3000/', r => process.exit(r.statusCode < 500 ? 0 : 1)).on('error', () => process.exit(1))"]

ENTRYPOINT ["/usr/bin/node"]
CMD ["server.js"]
