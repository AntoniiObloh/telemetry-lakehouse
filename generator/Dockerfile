# Build context is the repo root (pyproject.toml and uv.lock live there):
#   docker build -f generator/Dockerfile -t telemetry-lakehouse-generator .

FROM ghcr.io/astral-sh/uv:0.12.21 AS uv

# Stage 1: resolve and install the locked runtime deps into a venv.
FROM python:3.13.16-slim-trixie AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Only the "generator" group (boto3, paho-mqtt), not dbt and the rest of the project.
RUN uv sync --locked --only-group generator

# Stage 2: slim runtime with the venv and the code, no uv, no build tools, non-root.
FROM python:3.13.16-slim-trixie AS runtime
LABEL org.opencontainers.image.title="telemetry-lakehouse generator" \
      org.opencontainers.image.description="Synthetic IoT telemetry: JSONL batches with data defects"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN groupadd --system --gid 10001 generator \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin generator \
    && mkdir /app/data \
    && chown generator:generator /app/data
COPY --from=builder /opt/venv /opt/venv
COPY generator/ ./generator/
USER 10001:10001
ENTRYPOINT ["python", "-m", "generator"]
CMD ["--help"]
