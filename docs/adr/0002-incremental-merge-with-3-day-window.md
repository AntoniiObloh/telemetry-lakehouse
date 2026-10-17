# 0002. stg_telemetry: insert-only merge on event_id over the last 3 days of arrivals

Status: Accepted, 2026-10-17.

## Context

Bronze holds every arrival: about 2% duplicates (the same `event_id` in the same or the next
batch), 1% late events (arriving 1-48 h after `ts`), and it grows by about 2,900 rows an
hour. Silver must have each event once, from the first arrival, and dbt runs hourly on a
serverless warehouse that we pay for by the minute.

## Decision

`stg_telemetry` is an incremental model: `incremental_strategy='merge'`,
`unique_key='event_id'`, `skip_matched_step=true` (insert only). Each run reads the bronze
rows with `_ingested_at >= max(ingested_at) - stg_lookback_days` (3) and keeps the first
arrival per `event_id` (`QUALIFY row_number() ... ORDER BY ingested_at, source_file = 1`).

## Alternatives

- **Full rebuild every run.** Always correct and simplest, but rereads all of bronze
  every hour; cost grows with history.
- **Append only, deduplicate downstream.** Cheap writes, but every consumer must
  deduplicate, and the duplicates stay in silver.
- **Merge with update** (no `skip_matched_step`). A later duplicate would overwrite the
  first arrival; the row would change between runs for no reason.
- **Window on event time (`ts`) instead of arrival time.** Late events (up to 48 h) would
  fall outside a short window and be lost, or the window would need to be wider than the
  lateness.

## Consequences

- The window is on arrival time, so late events are never missed however late they are. The
  3 days bound only the bronze reread; they also tolerate dbt being down for up to 3 days.
  After a longer outage, run `dbt build -s stg_telemetry+ --full-refresh`.
- Insert only makes reruns idempotent: existing rows are never rewritten (the e2e test checks
  it, `ingested_at` included).
- A correction to an existing event (same `event_id`, other values) is ignored. Fixing data
  means a full refresh or a manual `MERGE`.
- The model reads `{{ this }}` to find the window, so its SQL differs between the first
  build and later ones; sqlfluff had to lint both renderings.
