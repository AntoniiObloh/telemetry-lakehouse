# dbt models

Layers: `staging` and `intermediate` build into `silver`, `marts` into `gold`, snapshots
into `silver` (all into `dbt_dev` on target dev). One row per model; keep it current.

| Model | Layer | Grain | Key | Purpose |
|---|---|---|---|---|
| `devices` | seed | device | `device_id` | Device reference data; a copy of `generator/devices.csv` |
| `stg_telemetry` | staging (incremental) | event | `event_id` | Clean events from `bronze.telemetry_raw`: deduplicated, typed, `event_at` UTC, `is_outlier` |
| `int_device_firmware_latest` | intermediate (view) | device | `device_id` | Latest known firmware per device; null before the drift |
| `dim_device` | snapshot (SCD2) | device version | `device_id` + `dbt_valid_from` | Device attributes plus firmware history; current row has `dbt_valid_to` null |
| `fct_device_hourly` | marts | device x hour | `device_id`, `hour_at` | Hourly readings/outliers count and temperature/humidity stats without outliers |
| `mart_device_health` | marts | device | `device_id` | State at build time: last seen, gaps, outliers, battery, `is_offline`, `health_score` |
| `srv_thingsboard` | marts (contract) | entity x ts x key | `entity_type`, `entity_name`, `ts_ms`, `key` | Long format for the ThingsBoard publisher; fixed key set |
