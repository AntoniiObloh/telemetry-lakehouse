# 0004. ThingsBoard is the serving layer

Status: Accepted, 2026-10-17.

## Context

Users need a live view of five sensors and their sites: a map, current values, hourly
history, health, and later alarms. The data lives in Databricks (gold), which is not built
for many small, frequent reads or for live device data. The devices themselves also need
somewhere to send live telemetry.

## Decision

ThingsBoard CE (docker compose, its own Postgres) serves the data. Devices, sites (Site A,
Site B), Fleet and Pipeline are ThingsBoard entities created by `provision.py`. Live
telemetry goes straight from the devices over MQTT (`generator-live`); computed values come
from gold through the publisher (ADR 0003). The publisher keeps its progress in a
watermark, the server attribute `publisher_watermark_ms` of the Pipeline asset.
Dashboards are kept as code in `thingsboard/dashboards/*.json` (`make tb-export`,
`make tb-import`).

## Alternatives

- **A BI tool on Databricks** (Power BI, Superset, Grafana with a Databricks source). No
  copy of the data and no publisher, but no device model, no MQTT, no alarms on devices, and
  every dashboard view queries the warehouse (cost, cold starts on serverless).
- **A custom web app.** Full control, but the device registry, auth, dashboards and alarms
  would all be ours to build.
- **ThingsBoard reading Databricks directly.** Not supported without a custom integration.

## Consequences

- Live and computed data meet in one place: the device state can show live `temperature`
  next to `temp_avg_1h` from gold.
- Gold is copied into ThingsBoard's Postgres. ThingsBoard keeps one value per
  (entity, key, ts), so republishing overwrites instead of duplicating: the publisher is
  idempotent and backfills are safe (`--from/--to`).
- The publisher needs a lookback (48 h before the watermark) because hourly values change
  while the hour fills up and when late events arrive.
- ThingsBoard becomes a stateful dependency: re-provisioning changes device tokens
  (`tb-device-tokens` must be recreated) and its data is lost with its volume (never
  `docker compose down -v`).
- Alarm rules are not configured yet (runbook, "ThingsBoard alarms").
