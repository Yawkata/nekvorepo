# syntax=docker/dockerfile:1.7

# =========================================================================
# Stage 1: Dependencies
# Isolates npm ci so it is only re-run when package*.json change.
# =========================================================================
FROM node:22-alpine AS deps
RUN apk add --no-cache libc6-compat
WORKDIR /app

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund

# =========================================================================
# Stage 2: Builder
# Compiles the Next.js app and produces the `standalone` server output.
# =========================================================================
FROM node:22-alpine AS builder
WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production

COPY --from=deps /app/node_modules ./node_modules
COPY . .

RUN npm run build

# =========================================================================
# Stage 3: Runtime
# Minimal distroless-style image containing only the standalone server,
# static assets, and public files. No package manager, no source, no dev deps.
# =========================================================================
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Non-root user with a fixed high UID/GID (matches backend services convention).
RUN addgroup -g 10001 -S nodejs && \
    adduser  -u 10001 -S nextjs -G nodejs

# Standalone output ships a self-contained server.js + the minimal node_modules
# required to run it. Static assets and /public must be copied alongside.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static    ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public          ./public

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD node -e "require('http').get('http://127.0.0.1:3000/', r => process.exit(r.statusCode < 500 ? 0 : 1)).on('error', () => process.exit(1))"

CMD ["node", "server.js"]
