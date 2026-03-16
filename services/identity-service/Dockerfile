# Stage 1: Build
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=0 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=services/identity-service/pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=services/identity-service/uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project --no-dev && \
    find .venv/lib/python3.13/site-packages/botocore/data -mindepth 1 -maxdepth 1 \
        -not -name 'cognito-idp' \
        -not -name 's3' \
        -not -name '*.json' \
        -exec rm -rf {} +

# Stage 2: Runtime
FROM python:3.13-slim

# Added 'curl' here to download the RDS CA bundle
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -m -s /bin/false appuser

WORKDIR /app

# 1. Prepare EFS mount point
RUN mkdir -p /mnt/efs/drafts && chown -R appuser:appgroup /mnt/efs/drafts

# 2. Setup RDS SSL Certificate for 'verify-full' mode
# We put it in /home/appuser/.postgresql/root.crt as expected by the Postgres driver
RUN mkdir -p /home/appuser/.postgresql/ && \
    curl -sS https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
    -o /home/appuser/.postgresql/root.crt && \
    chown -R appuser:appgroup /home/appuser/.postgresql/ && \
    chmod 0600 /home/appuser/.postgresql/root.crt

COPY --from=builder --chown=appuser:appgroup /app/.venv /app/.venv
COPY --chown=appuser:appgroup services/identity-service/app /app/app
COPY --chown=appuser:appgroup services/shared /app/shared

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app 

USER appuser
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]