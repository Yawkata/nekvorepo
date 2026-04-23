# syntax=docker/dockerfile:1.9

# =========================================================================
# Stage 1: Build
# =========================================================================
FROM cgr.dev/chainguard/python:latest-dev AS builder
USER root
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONDONTWRITEBYTECODE=1

COPY services/database-migrations/pyproject.toml services/database-migrations/uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --compile-bytecode

# Slim the venv. psycopg[binary] is already in pyproject.
RUN set -eux; \
    SP=$(ls -d /opt/venv/lib/python*/site-packages); \
    find "$SP" -depth -type d \( -name 'tests' -o -name 'test' -o -name 'examples' \) -exec rm -rf {} + 2>/dev/null || true ; \
    find "$SP" -type f \( -name '*.pyi' -o -name '*.c' -o -name '*.h' -o -name '*.pxd' -o -name '*.pyx' \) -delete ; \
    find "$SP" -type f -name '*.so' -exec strip --strip-unneeded {} + 2>/dev/null || true

# =========================================================================
# Stage 2: Runtime — Chainguard Python distroless, nonroot (uid 65532).
# =========================================================================
FROM cgr.dev/chainguard/python:latest AS runtime
LABEL org.opencontainers.image.description="Database Migrations for VCS-Hybrid App" \
      org.opencontainers.image.source="https://github.com/Yawkata/nekvorepo"

WORKDIR /app

COPY --from=builder --chown=nonroot:nonroot /opt/venv /opt/venv
COPY --chown=nonroot:nonroot services/shared                         /app/shared
COPY --chown=nonroot:nonroot services/database-migrations/migrations /app/migrations
COPY --chown=nonroot:nonroot services/database-migrations/alembic.ini /app/alembic.ini
COPY --chown=nonroot:nonroot services/database-migrations/init_roles.py /app/init_roles.py
COPY --chown=nonroot:nonroot services/database-migrations/entrypoint.py /app/entrypoint.py

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER nonroot

# No HEALTHCHECK — one-shot migrator. docker-compose gates via service_completed_successfully.
ENTRYPOINT []
CMD ["/opt/venv/bin/python", "/app/entrypoint.py"]
