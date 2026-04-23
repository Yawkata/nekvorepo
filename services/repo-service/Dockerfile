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
    PYTHONDONTWRITEBYTECODE=1

RUN wget -qO /root.crt https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem && \
    chmod 0600 /root.crt

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=services/repo-service/pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=services/repo-service/uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project --no-dev --compile-bytecode

# Repo-service uses S3 (drafts), SQS (cache invalidation), SES (notifications).
RUN set -eux; \
    SP=$(ls -d /app/.venv/lib/python*/site-packages); \
    find "$SP/botocore/data" -mindepth 1 -maxdepth 1 \
        -not -name 's3' \
        -not -name 'ses' \
        -not -name 'sqs' \
        -not -name 'sns' \
        -not -name 'sts' \
        -not -name '*.json' \
        -exec rm -rf {} + ; \
    find "$SP" -depth -type d \( -name 'tests' -o -name 'test' -o -name 'examples' \) -exec rm -rf {} + 2>/dev/null || true ; \
    find "$SP" -type f \( -name '*.pyi' -o -name '*.c' -o -name '*.h' -o -name '*.pxd' -o -name '*.pyx' \) -delete ; \
    find "$SP" -type f -name '*.so' -exec strip --strip-unneeded {} + 2>/dev/null || true

# Pre-create EFS mount point and postgres cert dir with nonroot ownership.
RUN mkdir -p /stage/home/nonroot/.postgresql /stage/mnt/efs/drafts && \
    cp /root.crt /stage/home/nonroot/.postgresql/root.crt && \
    chmod 0600 /stage/home/nonroot/.postgresql/root.crt && \
    chown -R 65532:65532 /stage

# =========================================================================
# Stage 2: Runtime
# =========================================================================
FROM cgr.dev/chainguard/python:latest AS runtime
LABEL org.opencontainers.image.description="Repo Service — draft management and EFS file storage" \
      org.opencontainers.image.source="https://github.com/Yawkata/nekvorepo"

WORKDIR /app

COPY --from=builder --chown=nonroot:nonroot /app/.venv  /app/.venv
COPY --from=builder --chown=nonroot:nonroot /stage/home /home
COPY --from=builder --chown=nonroot:nonroot /stage/mnt  /mnt
COPY --chown=nonroot:nonroot services/repo-service/app /app/app
COPY --chown=nonroot:nonroot services/shared          /app/shared

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="/app" \
    HOME=/home/nonroot

USER nonroot
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import http.client; c=http.client.HTTPConnection('127.0.0.1',8000,timeout=2); c.request('GET','/health'); exit(0 if c.getresponse().status==200 else 1)"]

ENTRYPOINT []
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
