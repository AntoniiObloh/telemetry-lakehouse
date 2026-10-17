"""End to end: running one day through generator -> bronze -> dbt -> publisher twice
leaves the same rows in Delta and the same points in ThingsBoard.

Why it holds, stage by stage (what the fingerprints below check):
- generator: the same seed and window give byte-identical batches under the same S3 keys,
  so the second upload overwrites every object with identical bytes;
- bronze: Auto Loader remembers processed files in its checkpoint and skips them (an
  overwritten file is not re-read without cloudFiles.allowOverwrites), so no row is added;
  _ingested_at is part of the digest, so a re-ingested file would show;
- stg_telemetry: incremental merge on event_id with skip_matched_step (insert only), so
  rows that exist are never rewritten;
- fct_device_hourly and the hourly rows of srv_thingsboard: pure functions of stg;
- publisher (a backfill of the day): ThingsBoard keeps one value per (entity, key, ts),
  so the same rows overwrite the same points.
Not covered, by design: rows stamped with the build time (mart_device_health.computed_at,
the srv_thingsboard state keys) and the Pipeline run metrics, which are new on every run.

Needs the dev stack, so it runs only with E2E=1 (from PowerShell, about 15 minutes):
  $env:E2E = "1"; $env:RAW_BUCKET = "<raw bucket>"; uv run pytest tests/e2e -s
plus AWS credentials that may write to RAW_BUCKET, the databricks CLI, the Databricks
variables of dbt/profiles.yml, a running ThingsBoard and thingsboard/.env.
E2E_DAY (default 2026-09-01) should be a day that only this test writes: regenerating a day
first written with another window changes its files, and Auto Loader would not re-read them.
"""

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DAY = date.fromisoformat(os.environ.get("E2E_DAY", "2026-09-01"))
DAY_START = datetime(DAY.year, DAY.month, DAY.day, tzinfo=UTC)
DAY_END = DAY_START + timedelta(days=1)
START_MS = int(DAY_START.timestamp() * 1000)
END_MS = int(DAY_END.timestamp() * 1000)
HOURLY_DEVICE_KEYS = "temp_avg_1h,temp_max_1h,humidity_avg_1h,readings_1h"
HOURLY_SITE_KEY = "site_temp_avg_1h"
SITES = ("Site A", "Site B")
STEP_TIMEOUT_S = 1800

pytestmark = pytest.mark.skipif(
    os.environ.get("E2E") != "1", reason="end-to-end: needs the dev stack, set E2E=1"
)


def child_env() -> dict[str, str]:
    """Environment for the steps: the PAT, never a databricks-cli OAuth profile."""
    env = dict(os.environ)
    # A profile in DATABRICKS_CONFIG_PROFILE wins over the token and may have expired.
    env.pop("DATABRICKS_CONFIG_PROFILE", None)
    env.setdefault("DATABRICKS_TOKEN", env.get("DBT_ENV_SECRET_DATABRICKS_TOKEN", ""))
    http_path = env.get("DATABRICKS_HTTP_PATH", "")
    if not http_path.startswith("/") and "/sql/" in http_path:  # Git Bash rewrite
        env["DATABRICKS_HTTP_PATH"] = http_path[http_path.index("/sql/") :]
    return env


def step(name: str, command: list[str], cwd: Path = ROOT) -> None:
    started = time.monotonic()
    result = subprocess.run(
        command,
        cwd=cwd,
        env=child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=STEP_TIMEOUT_S,
        check=False,
    )
    took = time.monotonic() - started
    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-20:])
        pytest.fail(f"{name} failed (exit {result.returncode}):\n{tail}")
    print(f"  {name}: ok in {took:.0f} s")


def run_pipeline(bucket: str) -> None:
    day, next_day = DAY.isoformat(), (DAY + timedelta(days=1)).isoformat()
    step(
        "generator",
        [sys.executable, "-m", "generator", "batch", "--sink", "s3", "--start", day, "--end", next_day],
    )  # fmt: skip
    databricks = shutil.which("databricks") or "databricks"
    step(
        "bronze",
        [databricks, "bundle", "run", "ingest_bronze", "-t", "dev", "--var", f"bucket={bucket}"],
        cwd=ROOT / "databricks",
    )  # fmt: skip
    dbt = shutil.which("dbt") or "dbt"
    step(
        "dbt",
        [dbt, "build", "--project-dir", "dbt", "--profiles-dir", "dbt", "--target", "dev"],
    )  # fmt: skip
    # A backfill of the day: old enough rows are outside the normal lookback, and the
    # watermark of the scheduled runs stays as it is.
    step(
        "publisher",
        [sys.executable, "-m", "publisher", "--from", day, "--to", day],
    )


def delta_fingerprints() -> dict[str, tuple]:
    """Row count, distinct events and an order-independent digest per layer, for the day."""
    from databricks.sql import connect

    env = child_env()
    next_batch = f"batch_{DAY_END:%Y%m%dT%H%M%SZ}"
    queries = {
        # Batches with sent_at in (day, day + 1]: date=<day>/ plus the midnight batch.
        "bronze": f"""
            SELECT count(*), count(DISTINCT event_id), sum(cast(xxhash64(
                event_id, ts, sent_at, temperature, humidity, battery,
                coalesce(firmware, ''), _source_file, _ingested_at) AS DECIMAL(38, 0)))
            FROM lakehouse.bronze.telemetry_raw
            WHERE _source_file LIKE '%date={DAY}/%' OR _source_file LIKE '%{next_batch}%'""",
        "stg_telemetry": f"""
            SELECT count(*), count(DISTINCT event_id), sum(cast(xxhash64(
                event_id, device_id, ts_ms, sent_at, temperature, humidity, battery,
                coalesce(firmware, ''), is_outlier, ingested_at, source_file) AS DECIMAL(38, 0)))
            FROM lakehouse.dbt_dev.stg_telemetry
            WHERE ts_ms >= {START_MS} AND ts_ms < {END_MS}""",
        "srv_thingsboard (hourly)": f"""
            SELECT count(*), count(DISTINCT entity_name, key), sum(cast(xxhash64(
                entity_type, entity_name, ts_ms, key, value) AS DECIMAL(38, 0)))
            FROM lakehouse.dbt_dev.srv_thingsboard
            WHERE ts_ms >= {START_MS} AND ts_ms < {END_MS} AND key LIKE '%\\_1h'""",
    }
    with (
        connect(
            server_hostname=env["DATABRICKS_HOST"].removeprefix("https://").rstrip("/"),
            http_path=env["DATABRICKS_HTTP_PATH"],
            access_token=env["DATABRICKS_TOKEN"],
        ) as connection,
        connection.cursor() as cursor,
    ):
        results = {}
        for layer, query in queries.items():
            cursor.execute(query)
            results[layer] = tuple(cursor.fetchone())
    return results


def thingsboard_fingerprint() -> tuple[int, str]:
    """Number of hourly points the day has in ThingsBoard and a digest of them."""
    sys.path.insert(0, str(ROOT / "thingsboard"))
    from provision import ENV_FILE, load_env
    from tb_rest_client.rest_client_ce import RestClientCE

    with (ROOT / "generator" / "devices.csv").open(encoding="utf-8", newline="") as f:
        devices = [row["device_id"] for row in csv.DictReader(f)]
    env = load_env(ENV_FILE)
    points = []
    with RestClientCE(base_url=env["TB_URL"]) as client:
        client.login(username=env["TB_USERNAME"], password=env["TB_PASSWORD"])
        targets = [
            (client.get_tenant_device(d).id, HOURLY_DEVICE_KEYS) for d in devices
        ]
        targets += [(client.get_tenant_asset(s).id, HOURLY_SITE_KEY) for s in SITES]
        for entity_id, keys in targets:
            series = client.get_timeseries(
                entity_id, keys, START_MS, END_MS, limit=100_000, agg="NONE"
            )
            for key, values in (series or {}).items():
                points += [
                    (entity_id.id, key, p["ts"], str(p["value"])) for p in values
                ]
    points.sort()
    digest = hashlib.sha256(json.dumps(points).encode("utf-8")).hexdigest()
    return len(points), digest


def fingerprint() -> dict[str, tuple]:
    return {**delta_fingerprints(), "thingsboard (hourly)": thingsboard_fingerprint()}


def test_rerunning_a_day_changes_nothing():
    bucket = os.environ.get("RAW_BUCKET")
    if not bucket:
        pytest.fail("RAW_BUCKET is not set")

    print(f"\nrun 1 for {DAY}")
    run_pipeline(bucket)
    first = fingerprint()
    print(f"run 2 for {DAY}")
    run_pipeline(bucket)
    second = fingerprint()

    for layer, values in first.items():
        print(f"  {layer}: {values[0]} rows, run 1 == run 2: {values == second[layer]}")
        assert values[0] > 0, f"{layer} has nothing for {DAY}: the check would be empty"
    assert second == first
