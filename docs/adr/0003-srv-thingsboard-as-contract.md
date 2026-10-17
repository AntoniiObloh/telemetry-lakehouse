# 0003. srv_thingsboard is the contract between dbt and the publisher

Status: Accepted, 2026-10-17.

## Context

The publisher sends gold values to ThingsBoard, and the dashboards read them by key
(`temp_avg_1h`, `health_score`, ...). If a dbt change renames a column or a key, nothing in
dbt fails: the publisher sends the wrong thing, or nothing, and a dashboard widget quietly
goes empty.

## Decision

One serving model, `srv_thingsboard`, is the only thing the publisher reads. It is in long
format, one value per row: `entity_type`, `entity_name`, `ts_ms`, `key`, `value`. Its yml
enforces a dbt **contract** (column names, types, not null), an `accepted_values` test on
`key` with the fixed set of 10 keys, and `unique_combination_of_columns` on
(entity_type, entity_name, ts_ms, key). Entity names match `thingsboard/provision.py`.

## Alternatives

- **The publisher reads the marts directly** (`fct_device_hourly`, `mart_device_health`).
  No extra model, but the publisher must know every mart's columns and the marts cannot
  change without breaking it.
- **Wide format** (one row per entity and ts, a column per key). Closer to what ThingsBoard
  receives, but every new key is a schema change for the publisher.
- **No contract, documentation only.** Breakage shows up on a dashboard, not in CI.

## Consequences

- A change to the columns or the key set fails `dbt build` (and dbt-ci on the PR) instead
  of breaking ThingsBoard later.
- A new key needs the model, the `accepted_values` list and the dashboard together; the
  publisher itself takes any key (the `ship-model` skill walks through it).
- Long format makes the publisher generic: it groups by entity and ts, whatever the keys.
- State keys are stamped with the build time (`computed_at`), hourly keys with the hour.
  That difference drives the publisher's lookback (ADR 0004) and is why reruns add new
  state points.
