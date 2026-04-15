# Stage 1: Build
FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl && rm -rf /var/lib/apt/lists/*

RUN curl -sS https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem -o /root.crt

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=services/repo-service/pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=services/repo-service/uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project --no-dev

# Stage 2: Runtime
FROM python:3.13-slim
LABEL org.opencontainers.image.description="Repo Service — draft management and EFS file storage"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -m -s /bin/false appuser && \
    mkdir -p /home/appuser/.postgresql/ && \
    chown -R appuser:appgroup /home/appuser/.postgresql/ && \
    # EFS mount point: bind-mounted locally, EFS CSI driver on EKS.
    # Owned by appuser so the service can create/delete draft directories.
    mkdir -p /mnt/efs/drafts && \
    chown -R appuser:appgroup /mnt/efs/drafts

COPY --from=builder --chown=appuser:appgroup /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appgroup /root.crt /home/appuser/.postgresql/root.crt
COPY --chown=appuser:appgroup services/repo-service/app /app/app
COPY --chown=appuser:appgroup services/shared /app/shared

RUN chmod 0600 /home/appuser/.postgresql/root.crt

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app"

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python3 -c 'import http.client; conn = http.client.HTTPConnection("localhost", 8000); conn.request("GET", "/health"); res = conn.getresponse(); exit(0 if res.status == 200 else 1)' || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
