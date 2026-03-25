# Stage 1: Build
FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY services/database-migrations/pyproject.toml services/database-migrations/uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project && \
    find /opt/venv/lib/python3.13/site-packages/botocore/data -mindepth 1 -maxdepth 1 \
        -not -name 's3' -not -name '*.json' -exec rm -rf {} + 2>/dev/null || true

# Stage 2: Runtime
FROM python:3.13-slim
LABEL org.opencontainers.image.description="Database Migrations for VCS-Hybrid App"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 10001 migratorgroup && \
    useradd -u 10001 -g migratorgroup -m -s /bin/false migratoruser

COPY --from=builder --chown=migratoruser:migratorgroup /opt/venv /opt/venv
COPY --chown=migratoruser:migratorgroup services/shared /app/shared
COPY --chown=migratoruser:migratorgroup services/database-migrations/migrations /app/migrations
COPY --chown=migratoruser:migratorgroup services/database-migrations/alembic.ini /app/alembic.ini

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app" \
    PYTHONUNBUFFERED=1

USER migratoruser

# No HEALTHCHECK — this is a one-shot job that runs alembic upgrade and exits 0.
# A polling health check on an intentionally exited container produces false
# "unhealthy" noise. docker-compose uses service_completed_successfully instead.

CMD ["alembic", "upgrade", "head"]
