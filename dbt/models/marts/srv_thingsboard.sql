-- Long format for the publisher: one value per (entity, timestamp, key),
-- sent to ThingsBoard as telemetry. Devices get health and hourly metrics;
-- site assets (named as in thingsboard/provision.py, e.g. "Site A") get the
-- average device health.
{%- set device_health_metrics = [
    'health_score',
    'minutes_since_last_seen',
    'longest_gap_minutes_24h',
    'readings_24h',
    'outliers_24h',
    'battery',
] -%}
{%- set device_hourly_metrics = [
    'readings_count',
    'outliers_count',
    'temperature_avg',
    'temperature_min',
    'temperature_max',
    'humidity_avg',
    'humidity_min',
    'humidity_max',
    'battery_min',
] %}

WITH device_health AS (
    SELECT
        device_id,
        site,
        computed_at,
        {{ device_health_metrics | join(',\n        ') }}
    FROM {{ ref('mart_device_health') }}
),

device_hourly AS (
    SELECT
        device_id,
        hour_at,
        {{ device_hourly_metrics | join(',\n        ') }}
    FROM {{ ref('fct_device_hourly') }}
),

device_health_values AS (
    {%- for metric in device_health_metrics %}
        SELECT
            'DEVICE' AS entity_type,
            '{{ metric }}' AS key,
            device_id AS entity_name,
            unix_millis(computed_at) AS ts_ms,
            cast({{ metric }} AS DOUBLE) AS value
        FROM device_health
        {%- if not loop.last %}
            UNION ALL
        {%- endif %}
    {%- endfor %}
),

device_hourly_values AS (
    {%- for metric in device_hourly_metrics %}
        SELECT
            'DEVICE' AS entity_type,
            '{{ metric }}' AS key,
            device_id AS entity_name,
            unix_millis(hour_at) AS ts_ms,
            cast({{ metric }} AS DOUBLE) AS value
        FROM device_hourly
        {%- if not loop.last %}
            UNION ALL
        {%- endif %}
    {%- endfor %}
),

site_health_values AS (
    SELECT
        'ASSET' AS entity_type,
        'health_score_avg' AS key,
        cast(round(avg(health_score), 1) AS DOUBLE) AS value,
        initcap(replace(site, '-', ' ')) AS entity_name,
        unix_millis(max(computed_at)) AS ts_ms
    FROM device_health
    GROUP BY site
),

all_values AS (
    SELECT
        entity_type,
        entity_name,
        ts_ms,
        key,
        value
    FROM device_health_values
    UNION ALL
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
    FROM site_health_values
)

SELECT
    entity_type,
    entity_name,
    ts_ms,
    key,
    value
FROM all_values
WHERE value IS NOT NULL
