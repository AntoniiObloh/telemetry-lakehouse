# Build context is the repo root (pyproject.toml and uv.lock live there):
#   docker build -f publisher/Dockerfile -t publisher:0.1.0 .
# Settings and secrets come only from the environment at run time; the device tokens are
# mounted at /app/thingsboard/devices.tokens.json (see k8s/base/publisher.yaml).

FROM ghcr.io/astral-sh/uv:0.12.21 AS uv

# Stage 1: the locked "publisher" group (databricks-sql-connector, requests,
# tb-rest-client) in a venv.
FROM python:3.13.16-slim-trixie AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --only-group publisher

# Stage 2: slim runtime with the venv and the code, no uv, non-root.
FROM python:3.13.16-slim-trixie AS runtime
LABEL org.opencontainers.image.title="publisher" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.description="srv_thingsboard (gold) to ThingsBoard telemetry"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN groupadd --system --gid 10001 publisher \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin publisher
COPY --from=builder /opt/venv /opt/venv
COPY publisher/ ./publisher/
USER 10001:10001
ENTRYPOINT ["python", "-m", "publisher"]
