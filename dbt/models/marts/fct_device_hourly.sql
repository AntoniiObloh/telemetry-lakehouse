-- One row per device and hour; outliers are counted but left out of the
-- statistics. A full normal hour has 120 readings (one every 30 s); a gap
-- shows as fewer readings or no row at all.
WITH readings AS (
    SELECT
        device_id,
        temperature,
        humidity,
        battery,
        is_outlier,
        date_trunc('hour', event_at) AS hour_at
    FROM {{ ref('stg_telemetry') }}
),

hourly_stats AS (
    SELECT
        device_id,
        hour_at,
        count(*) AS readings_count,
        count_if(is_outlier) AS outliers_count,
        round(
            avg(CASE WHEN NOT is_outlier THEN temperature END), 2
        ) AS temperature_avg,
        min(CASE WHEN NOT is_outlier THEN temperature END) AS temperature_min,
        max(CASE WHEN NOT is_outlier THEN temperature END) AS temperature_max,
        round(
            avg(CASE WHEN NOT is_outlier THEN humidity END), 2
        ) AS humidity_avg,
        min(CASE WHEN NOT is_outlier THEN humidity END) AS humidity_min,
        max(CASE WHEN NOT is_outlier THEN humidity END) AS humidity_max,
        min(battery) AS battery_min
    FROM readings
    GROUP BY
        device_id,
        hour_at
)

SELECT
    device_id,
    hour_at,
    readings_count,
    outliers_count,
    temperature_avg,
    temperature_min,
    temperature_max,
    humidity_avg,
    humidity_min,
    humidity_max,
    battery_min
FROM hourly_stats
