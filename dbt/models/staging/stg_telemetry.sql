{{
    config(
        materialized='incremental',
        incremental_strategy='merge',
        unique_key='event_id',
        skip_matched_step=true
    )
}}

{#-
    Clean events: one row per event_id, typed columns, outliers flagged (not removed).
    Incremental: re-reads the last stg_lookback_days of _ingested_at (late events arrive
    late, so they are new by arrival time) and merges on event_id. skip_matched_step makes
    the merge insert-only: the first copy of an event wins and re-read rows are left as is.
    firmware exists in bronze only after the schema drift has landed, so it is read only
    when the column is there.
-#}

{%- set bronze = source('bronze', 'telemetry_raw') -%}
{%- set has_firmware = false -%}
{%- if execute -%}
    {%- set bronze_columns = adapter.get_columns_in_relation(bronze)
        | map(attribute='name')
        | map('lower')
        | list -%}
    {%- set has_firmware = 'firmware' in bronze_columns -%}
{%- endif -%}

{#-
    Lower bound on _ingested_at. Only the bound is templated, so the SQL layout
    (and the lint result) is the same for a full load and an incremental run.
-#}
{%- if is_incremental() -%}
    {%- set ingested_since -%}
        (
            SELECT
                max(existing_rows.ingested_at)
                - INTERVAL {{ var('stg_lookback_days') }} DAYS
            FROM {{ this }} AS existing_rows
        )
    {%- endset -%}
{%- else -%}
    {%- set ingested_since = "TIMESTAMP '1970-01-01 00:00:00'" -%}
{%- endif %}

WITH typed_events AS (
    SELECT
        event_id,
        device_id,
        cast(ts AS BIGINT) AS ts_ms,
        cast(sent_at AS BIGINT) AS sent_at_ms,
        try_cast(temperature AS DOUBLE) AS temperature,
        try_cast(humidity AS DOUBLE) AS humidity,
        try_cast(battery AS INT) AS battery,
        {% if has_firmware -%}
            firmware,
        {%- else -%}
            cast(NULL AS STRING) AS firmware,
        {%- endif %}
        _ingested_at AS ingested_at,
        _source_file AS source_file
    FROM {{ bronze }}
    WHERE
        event_id IS NOT NULL
        AND _ingested_at >= {{ ingested_since }}
),

first_arrivals AS (
    -- A duplicate shares the event_id; keep the copy that arrived first.
    SELECT
        event_id,
        device_id,
        ts_ms,
        sent_at_ms,
        temperature,
        humidity,
        battery,
        firmware,
        ingested_at,
        source_file
    FROM typed_events
    QUALIFY row_number() OVER (
        PARTITION BY event_id
        ORDER BY ingested_at, source_file
    ) = 1
)

SELECT
    event_id,
    device_id,
    timestamp_millis(ts_ms) AS event_at,
    ts_ms,
    timestamp_millis(sent_at_ms) AS sent_at,
    temperature,
    humidity,
    battery,
    firmware,
    coalesce(
        temperature NOT BETWEEN {{ var('outlier_temperature_min') }}
        AND {{ var('outlier_temperature_max') }},
        FALSE
    ) AS is_outlier,
    ingested_at,
    source_file
FROM first_arrivals
