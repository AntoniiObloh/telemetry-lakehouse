-- Long format for the publisher: one value per (entity, timestamp, key), sent
-- to ThingsBoard as telemetry. Entity names match thingsboard/provision.py:
-- devices (sensor-01 ...), site assets (Site A, Site B) and the Fleet asset.
-- *_1h keys are hourly series (ts = start of the hour); the other keys
-- describe the state at computed_at (build time).
{%- set device_hourly_keys = [
    ('temp_avg_1h', 'temperature_avg'),
    ('temp_max_1h', 'temperature_max'),
    ('humidity_avg_1h', 'humidity_avg'),
    ('readings_1h', 'readings_count'),
] -%}
{%- set device_state_keys = [
    ('health_score', 'health_score'),
    ('battery_last', 'battery'),
] %}

WITH device_health AS (
    SELECT
        device_id,
        site,
        computed_at,
        health_score,
        battery,
        is_offline
    FROM {{ ref('mart_device_health') }}
),

device_hourly AS (
    SELECT
        device_id,
        hour_at,
        readings_count,
        outliers_count,
        temperature_avg,
        temperature_max,
        humidity_avg
    FROM {{ ref('fct_device_hourly') }}
),

device_sites AS (
    SELECT
        device_id,
        site
    FROM {{ ref('devices') }}
),

device_hourly_values AS (
    {%- for key, column in device_hourly_keys %}
        SELECT
            'DEVICE' AS entity_type,
            '{{ key }}' AS key,
            cast({{ column }} AS DOUBLE) AS value,
            device_id AS entity_name,
            unix_millis(hour_at) AS ts_ms
        FROM device_hourly
        {%- if not loop.last %}
            UNION ALL
        {%- endif %}
    {%- endfor %}
),

device_state_values AS (
    {%- for key, column in device_state_keys %}
        SELECT
            'DEVICE' AS entity_type,
            '{{ key }}' AS key,
            cast({{ column }} AS DOUBLE) AS value,
            device_id AS entity_name,
            unix_millis(computed_at) AS ts_ms
        FROM device_health
        {%- if not loop.last %}
            UNION ALL
        {%- endif %}
    {%- endfor %}
),

device_hourly_weights AS (
    -- Weight each device-hour by its non-outlier readings, so a device with a
    -- gap does not pull the site average as much as a complete one.
    SELECT
        device_hourly.hour_at,
        device_hourly.temperature_avg,
        device_sites.site,
        device_hourly.readings_count
        - device_hourly.outliers_count AS clean_readings
    FROM device_hourly
    INNER JOIN device_sites
        ON device_hourly.device_id = device_sites.device_id
),

site_hourly AS (
    SELECT
        site,
        hour_at,
        sum(temperature_avg * clean_readings)
        / nullif(
            sum(
                CASE WHEN temperature_avg IS NOT NULL THEN clean_readings END
            ),
            0
        ) AS temperature_avg
    FROM device_hourly_weights
    GROUP BY
        site,
        hour_at
),

site_hourly_values AS (
    SELECT
        'ASSET' AS entity_type,
        'site_temp_avg_1h' AS key,
        cast(round(temperature_avg, 2) AS DOUBLE) AS value,
        initcap(replace(site, '-', ' ')) AS entity_name,
        unix_millis(hour_at) AS ts_ms
    FROM site_hourly
),

site_state_values AS (
    SELECT
        'ASSET' AS entity_type,
        'site_health_avg' AS key,
        cast(round(avg(health_score), 1) AS DOUBLE) AS value,
        initcap(replace(site, '-', ' ')) AS entity_name,
        unix_millis(max(computed_at)) AS ts_ms
    FROM device_health
    GROUP BY site
),

fleet_state AS (
    SELECT
        max(computed_at) AS computed_at,
        round(avg(health_score), 1) AS health_avg,
        count_if(is_offline) AS devices_offline
    FROM device_health
),

fleet_state_values AS (
    SELECT
        'ASSET' AS entity_type,
        'Fleet' AS entity_name,
        'fleet_health_avg' AS key,
        cast(health_avg AS DOUBLE) AS value,
        unix_millis(computed_at) AS ts_ms
    FROM fleet_state
    UNION ALL
    SELECT
        'ASSET' AS entity_type,
        'Fleet' AS entity_name,
        'devices_offline' AS key,
        cast(devices_offline AS DOUBLE) AS value,
        unix_millis(computed_at) AS ts_ms
    FROM fleet_state
),

all_values AS (
    SELECT
        entity_type,
        entity_name,
        ts_ms,
        key,
        value
    FROM device_hourly_values
    UNION ALL
    SELECT
        entity_type,
        entity_name,
        ts_ms,
        key,
        value
    FROM device_state_values
    UNION ALL
    SELECT
        entity_type,
        entity_name,
        ts_ms,
        key,
        value
    FROM site_hourly_values
    UNION ALL
    SELECT
        entity_type,
        entity_name,
        ts_ms,
        key,
        value
    FROM site_state_values
    UNION ALL
    SELECT
        entity_type,
        entity_name,
        ts_ms,
        key,
        value
    FROM fleet_state_values
)

SELECT
    entity_type,
    entity_name,
    ts_ms,
    key,
    value
FROM all_values
WHERE value IS NOT NULL
