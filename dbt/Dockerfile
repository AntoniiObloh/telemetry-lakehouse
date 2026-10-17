# Build context is the repo root (pyproject.toml and uv.lock live there):
#   docker build -f dbt/Dockerfile -t dbt-runner:0.1.0 .
# Secrets come only from the environment at run time (never baked into the image):
#   docker run --rm -e DATABRICKS_HOST -e DATABRICKS_HTTP_PATH -e DBT_ENV_SECRET_DATABRICKS_TOKEN dbt-runner:0.1.0 build
# Connection check: `debug --connection` (plain `debug` also wants git, which the image
# does not need: dbt_utils is installed at build time).

FROM ghcr.io/astral-sh/uv:0.12.21 AS uv

# Stage 1: the locked dbt-databricks in a venv, plus the dbt packages from package-lock.yml.
FROM python:3.13.16-slim-trixie AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    DBT_SEND_ANONYMOUS_USAGE_STATS=false
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Only the "dbt" group (dbt-databricks), not pyspark, boto3 and the rest of the project.
RUN uv sync --locked --only-group dbt
WORKDIR /app/dbt
COPY dbt/dbt_project.yml dbt/packages.yml dbt/package-lock.yml ./
# dbt_utils is installed at build time, so a run needs no network beyond Databricks.
RUN /opt/venv/bin/dbt deps

# Stage 2: slim runtime with the venv and the dbt project, no uv, non-root.
FROM python:3.13.16-slim-trixie AS runtime
LABEL org.opencontainers.image.title="dbt-runner" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.description="dbt-databricks with the telemetry-lakehouse dbt project"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DBT_PROFILES_DIR=/app/dbt \
    DBT_SEND_ANONYMOUS_USAGE_STATS=false
WORKDIR /app/dbt
# The project stays root-owned and read-only; only target/ and logs/ are writable.
RUN groupadd --system --gid 10001 dbt \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin dbt \
    && mkdir target logs \
    && chown dbt:dbt target logs
COPY --from=builder /opt/venv /opt/venv
COPY dbt/ ./
COPY --from=builder /app/dbt/dbt_packages ./dbt_packages
USER 10001:10001
ENTRYPOINT ["dbt"]
CMD ["--help"]
