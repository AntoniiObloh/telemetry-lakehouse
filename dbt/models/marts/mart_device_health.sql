-- Health of every device as of computed_at (build time): freshness, gaps and
-- outliers over the last 24 hours, battery, and a 0-100 health_score built
-- from penalties (see _marts.yml).
WITH report_window AS (
    SELECT
        current_timestamp() AS computed_at,
        current_timestamp() - INTERVAL 24 HOURS AS window_start_at
),

devices AS (
    SELECT
        device_id,
        site
    FROM {{ ref('devices') }}
),

readings_until_now AS (
    SELECT
        telemetry.device_id,
        telemetry.event_at,
        telemetry.battery,
        telemetry.is_outlier
    FROM {{ ref('stg_telemetry') }} AS telemetry
    CROSS JOIN report_window
    WHERE telemetry.event_at <= report_window.computed_at
),

last_readings AS (
    SELECT
        device_id,
        battery,
        event_at AS last_seen_at
    FROM readings_until_now
    QUALIFY row_number() OVER (
        PARTITION BY device_id
        ORDER BY event_at DESC
    ) = 1
),

readings_in_window AS (
    SELECT
        readings_until_now.device_id,
        readings_until_now.event_at,
        readings_until_now.is_outlier
    FROM readings_until_now
    CROSS JOIN report_window
    WHERE readings_until_now.event_at >= report_window.window_start_at
),

window_counts AS (
    SELECT
        device_id,
        count(*) AS readings_24h,
        count_if(is_outlier) AS outliers_24h
    FROM readings_in_window
    GROUP BY device_id
),

gap_points AS (
    -- Readings plus both window edges, so silence at the start or the end
    -- of the window counts as a gap too.
    SELECT
        device_id,
        event_at
    FROM readings_in_window
    UNION ALL
    SELECT
        devices.device_id,
        report_window.window_start_at AS event_at
    FROM devices
    CROSS JOIN report_window
    UNION ALL
    SELECT
        devices.device_id,
        report_window.computed_at AS event_at
    FROM devices
    CROSS JOIN report_window
),

gaps AS (
    SELECT
        device_id,
        (
            unix_millis(event_at)
            - unix_millis(
                lag(event_at) OVER (
                    PARTITION BY device_id
                    ORDER BY event_at
                )
            )
        ) / 60000.0 AS gap_minutes
    FROM gap_points
),

longest_gaps AS (
    SELECT
        device_id,
        max(gap_minutes) AS longest_gap_minutes_24h
    FROM gaps
    GROUP BY device_id
),

device_metrics AS (
    SELECT
        devices.device_id,
        devices.site,
        report_window.computed_at,
        last_readings.last_seen_at,
        last_readings.battery,
        greatest(
            timestampdiff(
                MINUTE,
                last_readings.last_seen_at,
                report_window.computed_at
            ),
            0
        ) AS minutes_since_last_seen,
        round(
            longest_gaps.longest_gap_minutes_24h, 1
        ) AS longest_gap_minutes_24h,
        coalesce(window_counts.readings_24h, 0) AS readings_24h,
        coalesce(window_counts.outliers_24h, 0) AS outliers_24h
    FROM devices
    CROSS JOIN report_window
    LEFT JOIN last_readings
        ON devices.device_id = last_readings.device_id
    LEFT JOIN window_counts
        ON devices.device_id = window_counts.device_id
    LEFT JOIN longest_gaps
        ON devices.device_id = longest_gaps.device_id
),

health_penalties AS (
    SELECT
        device_id,
        -- Freshness: 1 point per minute of silence beyond 5 minutes, max 40.
        coalesce(
            least(greatest(minutes_since_last_seen - 5, 0), 40), 40
        ) AS freshness_penalty,
        -- Gaps: 1 point per 6 minutes of the longest gap (3 h = 30), max 30.
        least(longest_gap_minutes_24h / 6, 30) AS gap_penalty,
        -- Outliers: 1 point per outlier in the last 24 hours, max 15.
        least(outliers_24h, 15) AS outlier_penalty,
        -- Battery: 0.5 point per percent below 50, max 15; unknown = 15.
        coalesce(
            least(greatest((50 - battery) * 0.5, 0), 15), 15
        ) AS battery_penalty
    FROM device_metrics
)

SELECT
    device_metrics.device_id,
    device_metrics.site,
    device_metrics.computed_at,
    device_metrics.last_seen_at,
    device_metrics.minutes_since_last_seen,
    device_metrics.longest_gap_minutes_24h,
    device_metrics.readings_24h,
    device_metrics.outliers_24h,
    device_metrics.battery,
    -- Offline: silent for longer than offline_after_minutes, or never seen.
    coalesce(
        device_metrics.minutes_since_last_seen
        > {{ var('offline_after_minutes') }},
        TRUE
    ) AS is_offline,
    cast(
        round(
            greatest(
                100
                - health_penalties.freshness_penalty
                - health_penalties.gap_penalty
                - health_penalties.outlier_penalty
                - health_penalties.battery_penalty,
                0
            )
        ) AS INT
    ) AS health_score
FROM device_metrics
INNER JOIN health_penalties
    ON device_metrics.device_id = health_penalties.device_id
