# 0001. Auto Loader, not COPY INTO, for bronze

Status: Accepted, 2026-10-17.

## Context

The generator writes a JSONL batch to `s3://<raw bucket>/raw/telemetry/date=.../` every
5 minutes. Bronze (`lakehouse.bronze.telemetry_raw`) must take every file exactly once, keep
the rows as they are (duplicates, late events, outliers), and survive schema drift: from
2026-10-08 the events carry a new field, `firmware`. The workspace is Databricks Free
Edition, serverless only.

## Decision

A serverless job (`databricks/`, bundle `ingest_bronze`) runs an Auto Loader stream
(`cloudFiles`, JSON) with `trigger(availableNow=True)`: it takes every new file, appends it
with `_ingested_at` and `_source_file`, and stops. Schema evolution is
`addNewColumns` plus `mergeSchema` on the write. Schema and checkpoint live under
`artifacts/` in the same bucket.

## Alternatives

- **COPY INTO** on a schedule. Also loads each file once (it remembers loaded files) and is
  plain SQL. Schema evolution needs `mergeSchema` on every statement and the column list
  managed by hand; the file tracking is per table and harder to inspect than a checkpoint.
- **A plain batch read of a date prefix** (`spark.read.json`). Simple, but re-reads files on
  every run, so exactly-once becomes our code (bookkeeping of loaded files).
- **A continuous stream.** Lowest latency, but a cluster running all the time; not possible
  on Free Edition and pointless for 5-minute batches.

## Consequences

- Exactly once comes from the checkpoint: a file is read once. A file that is overwritten
  later is **not** re-read (no `cloudFiles.allowOverwrites`), so regenerating a day with
  other parameters makes S3 and bronze disagree (see the e2e idempotency test).
- The first run after a new column stops the stream (`addNewColumns`); the job's retry
  (`max_retries`) picks up the new schema. Expected once per new column.
- Bronze is append-only. Removing bad rows is a manual `DELETE` on `_source_file`.
- The dev bundle pauses the schedule: in dev the job runs by hand, which is the most common
  reason for stale gold (runbook: "dbt source freshness").
- New external locations on Free Edition are blocked for hours after creation (serverless
  network policy); not an IAM problem.
